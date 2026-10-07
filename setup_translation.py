"""Download only the Argos nb -> en inference files; no Argos runtime needed."""
import argparse
import json
from pathlib import Path, PurePosixPath
import tempfile
from urllib.request import Request, urlopen
import zipfile
import shutil

MODEL_URL = "https://argos-net.com/v1/translate-nb_en-1_9.argosmodel"


def install(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as archive:
        with urlopen(Request(MODEL_URL, headers={"User-Agent": "ArgosTranslate"}), timeout=120) as response:
            shutil.copyfileobj(response, archive)
        archive.seek(0)
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                parts = PurePosixPath(member.filename).parts
                if member.is_dir() or len(parts) < 2:
                    continue
                relative = PurePosixPath(*parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe model archive path")
                if relative.parts[0] != "model" and str(relative) not in ("sentencepiece.model", "metadata.json", "README.md"):
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    metadata = json.loads((destination / "metadata.json").read_text())
    if metadata.get("from_code") != "nb" or metadata.get("to_code") != "en":
        raise ValueError("Unexpected model language pair")
    if not (destination / "model/model.bin").is_file() or not (destination / "sentencepiece.model").is_file():
        raise ValueError("Incomplete translation model")
    print("Installed Norwegian -> English model:", destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("models/nb-en"))
    install(parser.parse_args().destination)
