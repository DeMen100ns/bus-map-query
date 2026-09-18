"""Shared deterministic, atomic output helpers."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protect_outputs(outputs, inputs=()):
    outputs = [Path(p).resolve() for p in outputs]
    inputs = [Path(p).resolve() for p in inputs]
    raw = (ROOT / "data/raw").resolve()
    if len(set(outputs)) != len(outputs):
        raise ValueError("Output paths must be distinct")
    for output in outputs:
        if output == raw or raw in output.parents:
            raise ValueError("Refusing to overwrite raw data")
        for other in inputs + outputs:
            if output == other and other in inputs:
                raise ValueError("Output must not overwrite an input")
        # resolve catches symlinks; samefile also catches existing hard links.
        for other in inputs:
            if output.exists() and other.exists() and output.samefile(other):
                raise ValueError("Output aliases an input")
    for i, output in enumerate(outputs):
        for other in outputs[:i]:
            if output.exists() and other.exists() and output.samefile(other):
                raise ValueError("Output paths alias each other")


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
