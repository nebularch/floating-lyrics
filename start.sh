
#!/usr/bin/env bash
set -e

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$APP_DIR/.venv/bin/python"
SCRIPT="$APP_DIR/floating-lyrics.py"

if [[ ! -x "$PYTHON" ]]; then
    printf 'FloatingLyrics is not set up yet. Run ./setup.sh first.\n' >&2
    exit 1
fi

exec "$PYTHON" "$SCRIPT" "$@"
