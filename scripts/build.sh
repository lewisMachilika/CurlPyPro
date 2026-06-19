#!/usr/bin/env bash
# Build CurlPro into a single-file executable on macOS or Linux.
# Usage (from project root):  ./scripts/build.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

# Use the project venv's python if present, otherwise python3 on PATH.
py="$root/.venv/bin/python"
[ -x "$py" ] || py="python3"

echo "==> Installing build dependencies"
"$py" -m pip install -r requirements-dev.txt

echo "==> Cleaning previous build"
rm -rf build dist

echo "==> Building with PyInstaller"
"$py" -m PyInstaller curlpro.spec --noconfirm

echo ""
if [ "$(uname)" = "Darwin" ]; then
  echo "Done. App bundle: dist/CurlPro.app  (single executable: dist/CurlPro)"
else
  echo "Done. Executable: dist/CurlPro"
fi
