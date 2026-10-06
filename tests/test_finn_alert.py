import json
import sqlite3
import unittest
from unittest.mock import Mock

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


if __name__ == "__main__":
    unittest.main()
