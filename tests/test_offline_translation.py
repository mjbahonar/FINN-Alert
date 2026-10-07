import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from finn_alert import load_config, Bot, ServiceError
from offline_translation import OfflineTranslator


class OfflineTests(unittest.TestCase):
    def test_long_input_is_not_truncated_and_paragraphs_survive(self):
        translator = OfflineTranslator.__new__(OfflineTranslator)
        translator.tokenizer = Mock()
        translator.tokenizer.encode.side_effect = lambda text, **kw: list(text)
        translator.tokenizer.decode.side_effect = lambda tokens: "".join(tokens)
        translator.model = Mock()
        translator.model.translate_batch.side_effect = lambda batch, **kw: [Mock(hypotheses=[batch[0]])]
        text = "x" * 450 + "\n\nDoor"
        output = translator.translate(text)
        self.assertEqual(output.replace(" ", ""), text)
        sizes = [len(call.args[0][0]) for call in translator.model.translate_batch.call_args_list]
        self.assertEqual(sizes, [200, 200, 50, 4])

    def test_offline_failure_is_safe_and_uses_original_code_block(self):
        bot = Bot({"translation": {"provider": "argos", "model_path": "missing"}}, None)
        bot.offline_translator = Mock()
        bot.offline_translator.translate.side_effect = RuntimeError("private-path")
        with self.assertRaisesRegex(ServiceError, "Offline translation unavailable"):
            bot.translate("Hei")
        message = bot.listing_messages("https://www.finn.no/recommerce/forsale/item/123", "Hei", "Verden")[0]
        self.assertIn("<pre>Hei\n\nVerden</pre>", message["text"])
        self.assertNotIn("private-path", message["text"])

    def test_env_and_model_paths_follow_custom_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = json.loads(Path("config.example.json").read_text())
            (root / "config.json").write_text(json.dumps(config))
            (root / ".env").write_text("TELEGRAM_BOT_TOKEN=123:fake\nTELEGRAM_CHAT_ID=-123\n")
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_config(root / "config.json")
                self.assertEqual(loaded["telegram"]["chat_id"], "-123")
                self.assertEqual(loaded["translation"]["model_path"], str(root / "models/nb-en"))
                with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "-456"}):
                    self.assertEqual(load_config(root / "config.json")["telegram"]["chat_id"], "-456")
