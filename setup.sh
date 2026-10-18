
#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

printf '\nFloatingLyrics — setup\n'
printf '%s\n\n' '----------------------'

if [[ ! -f /etc/arch-release ]] || ! command -v pacman >/dev/null 2>&1; then
    printf 'Error: this setup script currently supports Arch Linux.\n' >&2
    exit 1
fi

printf '[1/4] Installing system dependencies...\n'
sudo pacman -S --needed python python-gobject gtk3 playerctl

printf '\n[2/4] Preparing Python environment...\n'
python -m venv --system-site-packages "$VENV_DIR"

printf '\n[3/4] Checking GTK bindings...\n'
"$VENV_DIR/bin/python" -c '
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
print("GTK 3 bindings: OK")
'

printf '\n[4/4] Checking media integration...\n'
if command -v playerctl >/dev/null 2>&1; then
    playerctl --version
else
    printf 'Error: playerctl was not found after installation.\n' >&2
    exit 1
fi

if [[ ! -f "$APP_DIR/floating-lyrics.py" ]]; then
    printf 'Error: floating-lyrics.py is missing from %s\n' "$APP_DIR" >&2
    exit 1
fi

printf '\nChecking Python syntax...\n'
"$VENV_DIR/bin/python" -m py_compile "$APP_DIR/floating-lyrics.py"

printf '\nSetup complete.\n'
printf 'Start FloatingLyrics with: ./start.sh\n\n'
