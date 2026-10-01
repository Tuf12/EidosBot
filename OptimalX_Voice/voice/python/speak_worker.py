#!/usr/bin/env python3
"""Long-lived Kokoro TTS worker — one model load, many utterances."""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from device_resolve import cuda_mem_info, resolve_device
from speak import KokoroRuntime, synthesize, synthesize_stream


def stdin_pending() -> bool:
    """True when another command is waiting (cancel / next speak)."""
    try:
        import select

        return bool(select.select([sys.stdin], [], [], 0)[0])
    except Exception:
        return False


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="")
    parser.add_argument("--warm-voice", default="bm_george")
    args = parser.parse_args()
    default_device = resolve_device(args.device or None)

    runtime = KokoroRuntime(default_device)
    try:
        runtime.warm(args.warm_voice)
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "trace": traceback.format_exc()})
        return 1
    try:
        for _chunk in runtime.synthesize_stream("Ready.", args.warm_voice, 1.0):
            pass
    except Exception:
        # Model is already loaded; first-inference warmup is best-effort.
        pass

    free, total = cuda_mem_info()
    emit(
        {
            "ok": True,
            "ready": True,
            "event": "loaded",
            "engine": "kokoro",
            "device": runtime.device_name,
            "cudaFreeMb": round(free / (1024 * 1024), 1) if free else 0,
            "cudaTotalMb": round(total / (1024 * 1024), 1) if total else 0,
        }
    )

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
            if cmd == "cancel":
                emit(
                    {
                        "ok": True,
                        "event": "speak_done",
                        "cancelled": True,
                        "streamId": str(msg.get("streamId") or "").strip(),
                    }
                )
                continue
            if cmd in ("ping", "status"):
                free, total = cuda_mem_info()
                emit(
                    {
                        "ok": True,
                        "ready": True,
                        "engine": "kokoro",
                        "device": runtime.device_name,
                        "cudaFreeMb": round(free / (1024 * 1024), 1) if free else 0,
                        "cudaTotalMb": round(total / (1024 * 1024), 1) if total else 0,
                    }
                )
                continue
            if cmd == "speak":
                text = str(msg.get("text") or "").strip()
                out_path = str(msg.get("outPath") or "").strip()
                if not text:
                    emit({"ok": False, "error": "missing text"})
                    continue
                if not out_path:
                    emit({"ok": False, "error": "missing outPath"})
                    continue
                device = msg.get("device") or runtime.device_name
                result = synthesize(
                    text=text,
                    voice=str(msg.get("voice") or "bm_george"),
                    out_path=out_path,
                    device=device if device in ("cpu", "cuda") else None,
                    speed=float(msg.get("speed") or 1.0),
                    runtime=runtime,
                )
                emit(result)
                continue
            if cmd == "speak_stream":
                text = str(msg.get("text") or "").strip()
                stream_id = str(msg.get("streamId") or "").strip()
                if not text:
                    emit(
                        {
                            "event": "speak_done",
                            "streamId": stream_id,
                            "ok": False,
                            "error": "missing text",
                        }
                    )
                    continue
                device = msg.get("device") or runtime.device_name
                voice = str(msg.get("voice") or "bm_george")
                speed = float(msg.get("speed") or 1.0)
                raw_pieces = msg.get("pieces")
                pieces = (
                    [str(part).strip() for part in raw_pieces if str(part).strip()]
                    if isinstance(raw_pieces, list)
                    else None
                )
                chunk_count = 0
                try:
                    cancelled = False
                    for chunk in synthesize_stream(
                        text=text,
                        voice=voice,
                        device=device if device in ("cpu", "cuda") else None,
                        speed=speed,
                        runtime=runtime,
                        pieces=pieces,
                    ):
                        chunk_count += 1
                        emit(
                            {
                                "event": "chunk",
                                "streamId": stream_id,
                                "ok": True,
                                **chunk,
                            }
                        )
                        if stdin_pending():
                            cancelled = True
                            break
                    emit(
                        {
                            "event": "speak_done",
                            "streamId": stream_id,
                            "ok": True,
                            "cancelled": cancelled,
                            "chunkCount": chunk_count,
                            "voice": voice,
                            "device": runtime.device_name,
                            "chunked": True,
                        }
                    )
                except Exception as exc:
                    emit(
                        {
                            "event": "speak_done",
                            "streamId": stream_id,
                            "ok": False,
                            "error": str(exc),
                            "trace": traceback.format_exc(),
                            "chunkCount": chunk_count,
                        }
                    )
                continue
            emit({"ok": False, "error": f"unknown cmd: {cmd}"})
        except Exception as exc:
            emit({"ok": False, "error": str(exc), "trace": traceback.format_exc()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
