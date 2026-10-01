"""Sherpa-ONNX Parakeet STT model catalog, paths, and download."""

from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "parakeet-tdt-0.6b-v2-int8"

_LEGACY_ALIASES = {
    "nvidia/parakeet-tdt-0.6b-v2": DEFAULT_MODEL,
}

_CATALOG = {
    DEFAULT_MODEL: {
        "dir_name": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2"
        ),
        "encoder": "encoder.int8.onnx",
        "decoder": "decoder.int8.onnx",
        "joiner": "joiner.int8.onnx",
        "tokens": "tokens.txt",
    },
}


def voice_models_dir() -> Path:
    home = os.environ.get("OPTIMALX_HOME", os.path.expanduser("~/OptimalX"))
    return Path(home) / "voice" / "models"


def normalize_model_id(model_name: str | None) -> str:
    name = (model_name or DEFAULT_MODEL).strip()
    return _LEGACY_ALIASES.get(name, name)


def model_dir(model_name: str | None) -> Path:
    model_id = normalize_model_id(model_name)
    entry = _CATALOG.get(model_id)
    if not entry:
        raise ValueError(f"Unknown STT model: {model_name}")
    return voice_models_dir() / entry["dir_name"]


def model_paths(model_name: str | None) -> dict[str, Path]:
    model_id = normalize_model_id(model_name)
    entry = _CATALOG.get(model_id)
    if not entry:
        raise ValueError(f"Unknown STT model: {model_name}")
    root = voice_models_dir() / entry["dir_name"]
    return {
        "model_id": model_id,
        "root": root,
        "encoder": root / entry["encoder"],
        "decoder": root / entry["decoder"],
        "joiner": root / entry["joiner"],
        "tokens": root / entry["tokens"],
    }


def model_ready(model_name: str | None) -> bool:
    try:
        paths = model_paths(model_name)
    except ValueError:
        return False
    return all(p.is_file() for p in (paths["encoder"], paths["decoder"], paths["joiner"], paths["tokens"]))


def ensure_model(model_name: str | None) -> dict[str, Path]:
    paths = model_paths(model_name)
    if model_ready(paths["model_id"]):
        return paths
    model_id = paths["model_id"]
    entry = _CATALOG[model_id]
    voice_models_dir().mkdir(parents=True, exist_ok=True)
    archive = voice_models_dir() / f"{entry['dir_name']}.tar.bz2"
    if not archive.is_file():
        print(f"Downloading STT model {model_id}…", flush=True)
        urllib.request.urlretrieve(entry["url"], archive)
    print(f"Extracting STT model {model_id}…", flush=True)
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(path=voice_models_dir())
    if not model_ready(model_id):
        raise RuntimeError(f"STT model files missing after extract: {paths['root']}")
    try:
        archive.unlink()
    except OSError:
        pass
    return model_paths(model_id)
