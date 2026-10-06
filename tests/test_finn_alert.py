import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from finn_alert import Bot, ServiceError, STOP, chunks, database, parse_detail, parse_search

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
        message = self.bot.send.call_args.args[0]
        self.assertIn('Translation failed. Original text follows.', message)
        self.assertIn('Original description', message)
        self.assertIn(ITEM, message)
        self.assertEqual(self.db.execute('SELECT status FROM ads').fetchone()[0], 'sent')

    def test_successful_translation_uses_english(self):
        self.bot.translate = Mock(return_value='English title and description')
        self.assertEqual(self.bot.listing_messages(ITEM, 'Original', 'Description'),
                         [f'FINN | English\n{ITEM}\n\nEnglish title and description'])

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


if __name__ == "__main__":
    unittest.main()
