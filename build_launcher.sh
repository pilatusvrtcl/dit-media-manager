#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv-build" ]]; then
  python3 -m venv .venv-build
fi

source .venv-build/bin/activate

APP_VERSION="$(python - <<'PY'
from app.version import __version__
print(__version__)
PY
)"

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

PYI_CMD=(
  python -m PyInstaller --noconfirm --clean --windowed
  --name "DIT Media Launcher"
  --add-data "settings.json:."
)

if [[ -f "assets/launcher_icon.icns" ]]; then
  PYI_CMD+=(--icon "assets/launcher_icon.icns")
fi

PYI_CMD+=(app/launcher.py)
"${PYI_CMD[@]}"

APP_PLIST="dist/DIT Media Launcher.app/Contents/Info.plist"
if [[ -f "$APP_PLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$APP_PLIST" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$APP_PLIST"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$APP_PLIST" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$APP_PLIST"
fi

echo "Build complete: dist/DIT Media Launcher.app"
