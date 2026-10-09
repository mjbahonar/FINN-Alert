import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from finn_alert import Bot, ServiceError, HttpError, STOP, chunks, database, parse_detail, parse_search, parse_metadata, rich_messages, request

URL = "https://www.finn.no/recommerce/forsale/search?trade_type=2"
ITEM = "https://www.finn.no/recommerce/forsale/item/123"


class Tests(unittest.TestCase):
    def setUp(self):
        STOP.clear()
        self.db = database(":memory:")
        self.config = {"search_url": URL, "initial_mode": "send", "request_delay_seconds": 0}
        self.bot = Bot(self.config, self.db)

    def tearDown(self):
        self.db.close()
        STOP.clear()

    def test_google_maps_uses_postal_area_and_country(self):
        from urllib.parse import urlsplit, parse_qs
        message = rich_messages(ITEM, 'Description', 'FINN', {'address': '0250 Oslo & sentrum'})[0]
        buttons = [button for row in message['reply_markup']['inline_keyboard'] for button in row]
        link = next(button['url'] for button in buttons if button['text'] == 'Google Maps')
        self.assertEqual(parse_qs(urlsplit(link).query), {'api': ['1'], 'query': ['0250 Oslo & sentrum, Norway']})
        self.assertIn('Google Maps</a>', message['text'])
        missing = rich_messages(ITEM, 'Description', 'FINN', {})[0]
        self.assertNotIn('Google Maps', missing['text'])

    def test_missing_listing_does_not_block_or_retry(self):
        for status in (404, 410):
            with self.subTest(status=status):
                self.db.execute('DELETE FROM ads')
                self.bot.enqueue({'124': ITEM + '4', '123': ITEM})
                self.bot.fetch = Mock(side_effect=HttpError(status))
                self.bot.report_fetch_error = Mock()
                self.bot.send = Mock()
                self.db.execute('UPDATE ads SET messages=? WHERE id=?', (json.dumps(['next listing']), '124'))
                self.db.commit()
                with patch.object(STOP, 'wait'):
                    self.assertTrue(self.bot.deliver())
                    self.assertTrue(self.bot.deliver())
                self.assertEqual(self.db.execute('SELECT id,status FROM ads ORDER BY id').fetchall(), [('123', 'unavailable'), ('124', 'sent')])
                self.bot.fetch.assert_called_once_with(ITEM)
                self.bot.send.assert_called_once_with('next listing')
                self.bot.report_fetch_error.assert_not_called()

    def test_http_status_preserved_and_temporary_failure_retained(self):
        session = Mock()
        session.request.return_value.status_code = 404
        with self.assertRaises(HttpError) as caught:
            request(session, 'GET', URL)
        self.assertEqual(caught.exception.status_code, 404)
        self.bot.enqueue({'123': ITEM})
        self.bot.fetch = Mock(side_effect=HttpError(429))
        self.bot.report_fetch_error = Mock()
        self.assertFalse(self.bot.deliver())
        self.assertEqual(self.db.execute('SELECT status FROM ads').fetchone()[0], 'pending')
        self.bot.report_fetch_error.assert_called_once()

    def test_links_unique_and_pagination(self):
        items, next_url = parse_search(f'<a href="{ITEM}"></a><a href="{ITEM}"></a><a rel="next" href="?page=2"></a>', URL)
        self.assertEqual(items, {"123": ITEM})
        self.assertTrue(next_url.endswith("?page=2"))

    def test_challenge_not_baseline(self):
        with self.assertRaises(ServiceError):
            parse_search("<h1>Access denied</h1>", URL)
        self.assertEqual(self.db.execute("SELECT count(*) FROM meta").fetchone()[0], 0)

    def test_extract_only_description(self):
        title, desc = parse_detail('<h1 data-testid="object-title">Sofa</h1><section data-testid="description"><div class="whitespace-pre-wrap"><p>Hei</p><p>Verden</p></div><button>Show more</button></section>')
        self.assertEqual((title, desc), ("Sofa", "Hei\nVerden"))

    def test_initial_skip_then_new_only(self):
        self.config["initial_mode"] = "skip"
        self.bot.enqueue({"123": ITEM})
        self.bot.enqueue({"123": ITEM, "124": ITEM + "4"})
        self.assertEqual(self.db.execute("SELECT id,status FROM ads ORDER BY id").fetchall(), [("123", "skipped"), ("124", "pending")])

    def test_shadow_dom_template_text(self):
        title, desc = parse_detail('<template shadowrootmode="open"><h1 data-testid="object-title">Sofa</h1><section data-testid="description"><div class="whitespace-pre-wrap"><p>Full description</p></div></section></template>')
        self.assertEqual((title, desc), ("Sofa", "Full description"))

    def test_resume_and_dedup(self):
        self.bot.enqueue({"123": ITEM})
        self.db.execute("UPDATE ads SET messages=?,next_part=1", (json.dumps(["first", "second"]),))
        self.db.commit()
        self.bot.send = Mock(side_effect=ServiceError("timeout"))
        self.assertFalse(self.bot.deliver())
        self.assertEqual(self.db.execute("SELECT next_part,status FROM ads").fetchone(), (1, "pending"))
        self.bot.send = Mock(side_effect=lambda _: STOP.set())
        self.assertTrue(self.bot.deliver())
        STOP.clear()
        self.bot.enqueue({"123": ITEM})
        self.bot.deliver()
        self.bot.send.assert_called_once_with("second")
        self.assertEqual(self.db.execute("SELECT status FROM ads").fetchone()[0], "sent")

    def test_translation_failure_sends_original_and_marks_sent(self):
        self.bot.enqueue({"123": ITEM})
        self.bot.fetch = Mock(return_value='<h1 data-testid="object-title">Sofa</h1><section data-testid="description"><div class="whitespace-pre-wrap">Original description</div></section>')
        self.bot.translate = Mock(side_effect=ServiceError('HTTP 429'))
        self.bot.send = Mock(side_effect=lambda _: STOP.set())
        self.assertTrue(self.bot.deliver())
        message = self.bot.send.call_args.args[0]['text']
        self.assertIn('Translation failed. Original text follows.', message)
        self.assertIn('Original description', message)
        self.assertIn(ITEM, message)
        self.assertEqual(self.db.execute('SELECT status FROM ads').fetchone()[0], 'sent')

    def test_successful_translation_uses_english(self):
        self.bot.translate = Mock(return_value='English title and description')
        message = self.bot.listing_messages(ITEM, 'Original', 'Description')[0]
        self.assertIn('FINN | English', message['text'])
        self.assertIn('Original text:', message['text'])
        self.assertIn('Translated to English:', message['text'])
        self.assertIn('<pre>Original\n\nDescription</pre>', message['text'])
        self.assertIn('<pre>English title and description</pre>', message['text'])
        self.assertEqual(message['parse_mode'], 'HTML')

    def test_bilingual_long_text_is_complete_and_fits_telegram(self):
        from bs4 import BeautifulSoup
        original = "😀<&> " * 1000
        translated = "English<&> " * 1500
        self.bot.translate = Mock(return_value=translated)
        messages = self.bot.listing_messages(ITEM, "Title", original)
        originals, translations = [], []
        for message in messages:
            soup = BeautifulSoup(message['text'], 'html.parser')
            self.assertLess(len(soup.get_text().encode('utf-16-le')) // 2, 4096)
            for label in soup.select('b'):
                if label.get_text() == 'Original text:':
                    originals.append(label.find_next('pre').get_text())
                if label.get_text() == 'Translated to English:':
                    translations.append(label.find_next('pre').get_text())
        self.assertEqual(''.join(originals), 'Title\n\n' + original)
        self.assertEqual(''.join(translations), translated)

    def test_fallback_is_cached_when_telegram_fails(self):
        self.bot.enqueue({'123': ITEM})
        self.bot.fetch = Mock(return_value='<h1 data-testid="object-title">Sofa</h1><section data-testid="description"><div class="whitespace-pre-wrap">Description</div></section>')
        self.bot.translate = Mock(side_effect=ServiceError('HTTP 429'))
        self.bot.send = Mock(side_effect=ServiceError('Telegram unavailable'))
        self.assertFalse(self.bot.deliver())
        self.bot.send = Mock(side_effect=lambda _: STOP.set())
        self.assertTrue(self.bot.deliver())
        self.bot.translate.assert_called_once()
        self.bot.fetch.assert_called_once()

    def test_long_emoji_chunks_fit_telegram(self):
        text = "😀 " * 10000
        parts = list(chunks(text, 1700))
        self.assertEqual("".join(parts), text)
        for part in parts:
            message = f"FINN | English\n{ITEM}\n\n{part}"
            self.assertLess(len(message.encode("utf-16-le")) // 2, 4096)

    def test_search_failure_alert_and_restart_cooldown(self):
        self.config['max_pages'] = 1
        self.bot.fetch = Mock(side_effect=ServiceError('Remote service returned HTTP 429'))
        self.bot.send = Mock()
        with patch('finn_alert.time.time', return_value=1000):
            with self.assertRaises(ServiceError):
                self.bot.discover()
        self.assertIn('Stage: Search page', self.bot.send.call_args.args[0])
        self.assertIn('HTTP 429', self.bot.send.call_args.args[0])
        restarted = Bot(self.config, self.db)
        restarted.send = Mock()
        with patch('finn_alert.time.time', return_value=1100):
            restarted.report_fetch_error('Search page', URL, ServiceError('Remote service returned HTTP 429'))
        restarted.send.assert_not_called()
        with patch('finn_alert.time.time', return_value=2800):
            restarted.report_fetch_error('Search page', URL, ServiceError('Remote service returned HTTP 429'))
        restarted.send.assert_called_once()

    def test_listing_fetch_and_parse_errors_notify(self):
        self.bot.send = Mock()
        self.bot.fetch = Mock(side_effect=ServiceError('Network request failed or timed out'))
        with self.assertRaises(ServiceError):
            self.bot.listing_detail(ITEM)
        self.bot.fetch = Mock(return_value='<h1>Removed</h1>')
        with self.assertRaises(ServiceError):
            self.bot.listing_detail(ITEM)
        self.assertEqual(self.bot.send.call_count, 2)
        self.assertIn('Stage: Listing details', self.bot.send.call_args.args[0])

    def test_failed_alert_delivery_can_retry_without_recursion(self):
        self.bot.send = Mock(side_effect=ServiceError('Telegram unavailable'))
        self.bot.report_fetch_error('Search page', URL, ServiceError('HTTP 403'))
        self.bot.send.assert_called_once()
        self.assertEqual(self.db.execute('SELECT count(*) FROM error_alerts').fetchone()[0], 0)
        self.bot.send = Mock()
        self.bot.report_fetch_error('Search page', URL, ServiceError('HTTP 403'))
        self.bot.send.assert_called_once()

    def test_alert_redacts_secrets_and_preview_does_not_send(self):
        self.config['telegram'] = {'bot_token': 'secret-token'}
        self.config['translation'] = {'google_api_key': 'secret-key'}
        self.bot.send = Mock()
        self.bot.report_fetch_error('Search page', URL + '&private=value', ServiceError('secret-token secret-key https://example.com/private'))
        message = self.bot.send.call_args.args[0]
        for secret in ('secret-token', 'secret-key', 'private=value', 'example.com'):
            self.assertNotIn(secret, message)
        preview = Bot(self.config, None)
        preview.send = Mock()
        preview.report_fetch_error('Search page', URL, ServiceError('HTTP 429'))
        preview.send.assert_not_called()

    def test_metadata_preserves_updated_vs_published(self):
        source = '<template><section data-testid="object-info">Sist endret: 4.10.2026 kl. 14:56</section><a data-testid="map-link" href="/map?postalCode=5056"><span data-testid="object-address">5056 Bergen</span></a></template>'
        metadata = parse_metadata(source)
        self.assertEqual(metadata['address'], '5056 Bergen')
        self.assertIn('2026-10-04 14:56', metadata['updated'])
        self.assertNotIn('published', metadata)
        self.assertEqual(metadata['map_url'], 'https://www.finn.no/map?postalCode=5056')

    def test_metadata_missing_or_invalid(self):
        self.assertEqual(parse_metadata('<p>No metadata</p>'), {'address': ''})
        self.assertNotIn('updated', parse_metadata('<section data-testid="object-info">Sist endret: 99.10.2026 kl. 14:56</section>'))
        self.assertNotIn('map_url', parse_metadata('<a data-testid="map-link" href="javascript:alert(1)">Map</a>'))

    def test_rich_text_escaping_and_copy_limits(self):
        body = '<script>& " ' + '\U0001f600' * 3000
        messages = rich_messages(ITEM, body, 'FINN | English', {'address': '5056 Bergen', 'map_url': 'https://www.finn.no/map?a=1&b=2'})
        from bs4 import BeautifulSoup
        reconstructed = ''
        for message in messages:
            soup = BeautifulSoup(message['text'], 'html.parser')
            reconstructed += soup.pre.get_text()
            self.assertFalse(soup.select('script'))
            self.assertLess(len(soup.get_text().encode('utf-16-le')) // 2, 4096)
            for row in message['reply_markup']['inline_keyboard']:
                for button in row:
                    if 'copy_text' in button:
                        self.assertLessEqual(len(button['copy_text']['text']), 256)
        self.assertEqual(reconstructed, body)

    def test_send_supports_legacy_and_rich_queue_entries(self):
        self.config['telegram'] = {'bot_token': '123:fake', 'chat_id': '-123'}
        response = Mock()
        response.json.return_value = {'ok': True}
        with patch('finn_alert.request', return_value=response) as call:
            self.bot.send('Legacy queued text')
            self.assertEqual(call.call_args.kwargs['json']['text'], 'Legacy queued text')
            message = rich_messages(ITEM, 'Short text', 'FINN', {})[0]
            self.bot.send(message)
            self.assertEqual(call.call_args.kwargs['json']['parse_mode'], 'HTML')
            self.assertNotIn('chat_id', message)


if __name__ == "__main__":
    unittest.main()
