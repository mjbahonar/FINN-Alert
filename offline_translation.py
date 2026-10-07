"""CPU-only inference for the Argos Norwegian Bokmal -> English model."""
from pathlib import Path
import re


class OfflineTranslator:
    def __init__(self, model_path):
        # Import inference dependencies only; never load Argos/Stanza/PyTorch.
        import ctranslate2
        import sentencepiece

        path = Path(model_path)
        self.tokenizer = sentencepiece.SentencePieceProcessor(
            model_file=str(path / "sentencepiece.model"))
        self.model = ctranslate2.Translator(
            str(path / "model"), device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=1)

    def translate(self, text):
        paragraphs = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                paragraphs.append("")
                continue
            translated = []
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                tokens = self.tokenizer.encode(sentence, out_type=str)
                # Bound memory and decoder length without truncating source text.
                for offset in range(0, len(tokens), 200):
                    result = self.model.translate_batch(
                        [tokens[offset:offset + 200]], beam_size=4,
                        replace_unknowns=True, max_decoding_length=1024)[0]
                    if not result.hypotheses or not result.hypotheses[0]:
                        raise ValueError("Offline translation returned no text")
                    if len(result.hypotheses[0]) >= 1024:
                        raise ValueError("Offline translation reached decoder limit")
                    translated.append(self.tokenizer.decode(result.hypotheses[0])
                                      .replace("▁", " ").replace("_", " ").strip())
            paragraphs.append(" ".join(translated))
        return "\n".join(paragraphs)
