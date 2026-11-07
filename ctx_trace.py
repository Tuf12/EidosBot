"""CtxWindowExt tracer — generated install, contract v0.1."""
import datetime
import json
import os
import traceback
CONTRACT_VERSION = "0.1"
TRACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ctx-tracker")
TRACE_FILE = os.path.join(TRACE_DIR, "trace.jsonl")
MAX_BODY_CHARS = 20000
KINDS = ("system_prompt", "tools", "history", "user_message", "rag", "env", "other")
_pending = []
_seq = 0
def _redact(text):
    if not isinstance(text, str):
        text = str(text)
    secret = os.environ.get("MODEL_API_KEY", "")
    if secret and secret in text:
        text = text.replace(secret, "[REDACTED]")
    kept = []
    for line in text.split("\n"):
        if line.strip().lower().startswith("authorization:"):
            kept.append("Authorization: [REDACTED]")
        else:
            kept.append(line)
    return "\n".join(kept)
def _estimate_tokens(chars):
    return (chars + 3) // 4 if chars > 0 else 0
def _caller_source(depth=3):
    try:
        frame = traceback.extract_stack()[-depth]
        return {"file": os.path.basename(frame.filename), "lines": [frame.lineno, frame.lineno]}
    except Exception:
        return {"file": "(unknown)", "lines": [1, 1]}
def _caller_stack(limit=4):
    try:
        frames = traceback.extract_stack()[:-2]
        out = []
        for f in frames[-limit:]:
            out.append("%s (%s:%d)" % (f.name, os.path.basename(f.filename), f.lineno))
        return out
    except Exception:
        return []
def wrap(messages, section="other", label=None):
    global _seq
    kind = section if section in KINDS else "other"
    _seq += 1
    raw = messages if isinstance(messages, str) else json.dumps(messages, default=str)
    body = _redact(raw)
    truncated = len(body) > MAX_BODY_CHARS
    if truncated:
        body = body[:MAX_BODY_CHARS]
    _pending.append({"id": "sec-%d" % _seq, "label": label or (kind + ":messages"), "kind": kind, "chars": len(body), "tokens_est": _estimate_tokens(len(body)), "source": _caller_source(), "stack": _caller_stack(), "body": body, "truncated": truncated})
    return messages
def flush(model="user-model (any)", project=None):
    global _pending, _seq
    if not _pending:
        return None
    sections = _pending
    _pending = []
    _seq = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    total_chars = sum(s["chars"] for s in sections)
    run = {"run_id": "%s_%s_%d" % (now, project or "app", os.getpid()), "ts": now, "project": project or os.path.basename(os.getcwd()), "tracer": {"lang": "python", "file": os.path.basename(__file__), "version": CONTRACT_VERSION}, "model": {"name": model, "endpoint": "not-recorded"}, "sections": sections, "totals": {"chars": total_chars, "tokens_est": _estimate_tokens(total_chars)}}
    os.makedirs(TRACE_DIR, exist_ok=True)
    fh = open(TRACE_FILE, "a", encoding="utf-8")
    fh.write(json.dumps(run) + "\n")
    fh.close()
    return run["run_id"]
