import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from finn_alert import Bot, ConfigWatcher, priority_reasons, load_config, require_telegram, rich_messages, wait_for_next_poll

ITEM = "https://www.finn.no/recommerce/forsale/item/123"
BASE = 'search_url = "https://www.finn.no/recommerce/forsale/search?trade_type=2"\n' \
       'poll_interval_seconds = 300\nmax_pages = 1\nrequest_delay_seconds = 1\ninitial_mode = "send"\n' \
       '[translation]\nprovider = "argos"\n'
ENV = "TELEGRAM_BOT_TOKEN=123:fake\nTELEGRAM_CHAT_ID=-1\n"


def write_config(root, extra="", env=ENV):
    (root / "config.toml").write_text(BASE + extra, encoding="utf-8")
    (root / ".env").write_text(env, encoding="utf-8")
    return root / "config.toml"


class PriorityTests(unittest.TestCase):
    def load(self, extra="", env=ENV, process_env=None):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, process_env or {}, clear=True):
            return load_config(write_config(Path(folder), extra, env))

    def test_keywords_and_postal_codes_are_either_or(self):
        priority = self.load('[priority]\nkeywords = ["Sykkel", "ØL"]\npostal_codes = ["5003"]\n')["priority"]
        self.assertTrue(priority_reasons(priority, "Flott terrengSYKKEL", "0150 Oslo"))
        self.assertTrue(priority_reasons(priority, "Gratis øl", ""))
        self.assertTrue(priority_reasons(priority, "Sofa", "5003 Bergen"))
        self.assertFalse(priority_reasons(priority, "Sofa", "5004 Bergen"))
        self.assertFalse(priority_reasons(priority, "Sofa", "Storgata 5003 Bergen"))
        self.assertFalse(priority_reasons(dict(priority, enabled=False), "sykkel", "5003"))
        self.assertEqual(priority_reasons(priority, "Sykkel og øl", "5003 Bergen"),
                         ["keyword \u201csykkel\u201d", "keyword \u201cøl\u201d", "postal code 5003"])

    def test_env_lists_are_added_to_config_lists(self):
        priority = self.load('[priority]\nkeywords = ["sykkel"]\n',
                             ENV + "PRIORITY_KEYWORDS=gratis, Sofa\nPRIORITY_POSTAL_CODES=0150,5003\n")["priority"]
        self.assertEqual(priority["keywords"], ("sykkel", "gratis", "sofa"))
        self.assertEqual(priority["postal_codes"], frozenset({"0150", "5003"}))

    def test_invalid_priority_settings_are_rejected(self):
        for extra in ('[priority]\npostal_codes = ["15"]\n', '[priority]\nkeywords = "sykkel"\n',
                      '[priority]\nsend_to_group = "yes"\n', '[telegram]\nsilent_normal = 1\n'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.load(extra)
        config = self.load('[priority]\nsend_to_group = true\n')
        with self.assertRaisesRegex(ValueError, "PRIORITY_CHAT_ID"):
            require_telegram(config)
        require_telegram(self.load('[priority]\nsend_to_group = true\n', ENV + "TELEGRAM_PRIORITY_CHAT_ID=-2\n"))

    def test_listing_messages_are_tagged_and_routed(self):
        config = self.load('[priority]\nenabled = true\nsend_to_group = true\nkeywords = ["sykkel"]\n',
                           ENV + "TELEGRAM_PRIORITY_CHAT_ID=-2\n")
        bot = Bot(config, None)
        bot.translate = Mock(return_value="bike")
        messages = bot.listing_messages(ITEM, "Sykkel", "Pen")
        self.assertTrue(all(message["_route"] == "priority" for message in messages))
        self.assertIn("PRIORITY LISTING", messages[0]["text"])
        self.assertIn("<blockquote>\U0001F3AF Reason: keyword \u201csykkel\u201d</blockquote>", messages[0]["text"])
        plain = bot.listing_messages(ITEM, "Sofa", "Pen")
        self.assertNotIn("PRIORITY", plain[0]["text"])
        self.assertFalse(any("_route" in message for message in plain))
        response = Mock()
        response.json.return_value = {"ok": True}
        with patch("finn_alert.request", return_value=response) as call:
            bot.send(messages[0])
            sent = call.call_args.kwargs["json"]
            self.assertEqual((sent["chat_id"], sent["disable_notification"]), ("-2", False))
            self.assertNotIn("_route", sent)
            self.assertIn("_route", messages[0])
            bot.send(plain[0])
            sent = call.call_args.kwargs["json"]
            self.assertEqual((sent["chat_id"], sent["disable_notification"]), ("-1", True))
            bot.send("Fetch error alert")
            self.assertNotIn("disable_notification", call.call_args.kwargs["json"])
            bot.c["priority"]["send_to_group"] = False
            bot.send(messages[0])
            sent = call.call_args.kwargs["json"]
            self.assertEqual((sent["chat_id"], sent["disable_notification"]), ("-1", False))
            bot.c["telegram"]["silent_normal"] = False
            bot.send(plain[0])
            self.assertFalse(call.call_args.kwargs["json"]["disable_notification"])

    def test_hot_reload_applies_valid_edits_and_ignores_bad_ones(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = write_config(Path(folder), '[priority]\nkeywords = ["sykkel"]\n')
            watcher = ConfigWatcher(path)
            current = load_config(path)
            self.assertIs(watcher.reload(current), current)
            self.assertFalse(watcher.changed())
            path.write_text(BASE.replace("300", "60") + '[priority]\nkeywords = ["sofa"]\n', encoding="utf-8")
            self.assertTrue(watcher.changed())
            reloaded = watcher.reload(current)
            self.assertEqual((reloaded["poll_interval_seconds"], reloaded["priority"]["keywords"]), (60, ("sofa",)))
            path.write_text("this is = not [valid", encoding="utf-8")
            with self.assertLogs("finn-alert", "ERROR"):
                self.assertIs(watcher.reload(reloaded), reloaded)
            self.assertFalse(watcher.changed())
            (path.parent / ".env").write_text(ENV + "PRIORITY_KEYWORDS=tv\n", encoding="utf-8")
            path.write_text(BASE + '[priority]\nkeywords = ["sofa"]\n', encoding="utf-8")
            self.assertEqual(watcher.reload(reloaded)["priority"]["keywords"], ("sofa", "tv"))

    def test_wait_wakes_early_on_change(self):
        watcher = Mock()
        watcher.changed.return_value = True
        with patch("finn_alert.STOP.wait") as wait:
            wait_for_next_poll(300, watcher)
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
