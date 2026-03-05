#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv-build" ]]; then
  python3 -m venv .venv-build
fi

source .venv-build/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python - <<'PY'
import sys

try:
  import tkinter  # noqa: F401
except Exception as exc:
  print("Tkinter is unavailable in this Python environment.")
  print("Use a Python build with Tk support (python.org installer is recommended).")
  print(f"Details: {exc}")
  sys.exit(1)
PY

ICON_ARGS=()
if [[ -f "assets/launcher_icon.icns" ]]; then
  ICON_ARGS+=(--icon "assets/launcher_icon.icns")
fi

python -m PyInstaller --noconfirm --clean --windowed \
  --name "DIT Media Launcher" \
  --add-data "settings.json:." \
  "${ICON_ARGS[@]}" \
  app/launcher.py

echo "Build complete: dist/DIT Media Launcher.app"
