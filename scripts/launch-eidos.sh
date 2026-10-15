#!/usr/bin/env bash
# Start the EidosBot desktop HUD from a menu or desktop icon.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

PYTHON="$ROOT/venv/bin/python3"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/eidosbot"
LOG="$LOG_DIR/launch.log"
mkdir -p "$LOG_DIR"

fail() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="EidosBot" --text="$*"$'\n\n'"Details: $LOG" || true
  fi
  exit 1
}

if [[ ! -x "$PYTHON" ]]; then
  fail "EidosBot could not find its Python environment at $PYTHON"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') launching from $ROOT" >>"$LOG"
exec "$PYTHON" -m eidos.main --gui "$@" >>"$LOG" 2>&1
