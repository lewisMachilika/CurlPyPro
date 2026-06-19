# Build CurlPro into a single-file Windows executable.
# Usage (from project root):  ./scripts/build.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Use the project venv's python if present, otherwise whatever python is on PATH.
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "==> Installing build dependencies" -ForegroundColor Cyan
& $py -m pip install -r requirements-dev.txt

Write-Host "==> Cleaning previous build" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Building with PyInstaller" -ForegroundColor Cyan
& $py -m PyInstaller curlpro.spec --noconfirm

Write-Host "`nDone. Executable: dist\CurlPro.exe" -ForegroundColor Green
