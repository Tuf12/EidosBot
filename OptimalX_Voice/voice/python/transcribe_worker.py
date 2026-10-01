#!/usr/bin/env python3
"""Long-lived Parakeet worker — one model load, many transcriptions.

Newline-delimited JSON on stdin; one JSON response per line on stdout.
Sherpa-onnx logs go to stderr only.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from device_resolve import resolve_stt_provider
from stt_model import DEFAULT_MODEL
from transcribe import load_model, status, transcribe


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="")
    args = parser.parse_args()
    provider = resolve_stt_provider(args.device or None)

    try:
        load_model(args.model, provider)
        emit(
            {
                "ok": True,
                "ready": True,
                "event": "loaded",
                "model": args.model,
                "provider": provider,
                "device": provider,
            }
        )
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "trace": traceback.format_exc()})
        return 1

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            emit({"ok": False, "error": f"invalid json: {exc}"})
            continue
        cmd = (msg.get("cmd") or "").strip()
        try:
            if cmd == "quit":
                emit({"ok": True, "event": "bye"})
                return 0
            if cmd == "ping" or cmd == "status":
                emit(status(args.model))
                continue
            if cmd == "transcribe":
                wav = msg.get("wavPath") or ""
                emit(transcribe(wav, args.model, msg.get("device") or provider))
                continue
            emit({"ok": False, "error": f"unknown cmd: {cmd}"})
        except Exception as exc:
            emit({"ok": False, "error": str(exc), "trace": traceback.format_exc()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
