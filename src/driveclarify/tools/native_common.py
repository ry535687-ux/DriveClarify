"""Native worker paths are read from the selected plan, never from host constants."""
import datetime
import hashlib
import json
import os
from pathlib import Path

plan = json.loads(Path(os.environ["DRIVECLARIFY_PLAN"]).read_text())
paths = {key: Path(value) for key, value in plan["paths"].items()}
OUT = Path(plan["output"])
ROOT = OUT / "runtime/python"
SIM = paths["simlingo_root"]
PYTHON = str(paths["native_python"])
CHECKPOINT = OUT / "model/checkpoints/a1_selected.ckpt/pytorch_model.pt"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def log(command):
    with (OUT / "commands.log").open("a") as stream:
        stream.write(now() + " " + command + "\n")

