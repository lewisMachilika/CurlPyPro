# Build CurlPyPro into a single-file Windows executable.
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
& $py -m PyInstaller curlpypro.spec --noconfirm

$isccCandidates = @(
    @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
)

if ($isccCandidates.Count -gt 0) {
    Write-Host "==> Generating installer wizard images" -ForegroundColor Cyan
    & $py scripts\make_wizard_images.py

    Write-Host "==> Building Windows installer" -ForegroundColor Cyan
    & $isccCandidates[0] "installer\windows\CurlPyPro.iss"
    Write-Host "`nDone. Installer: dist\installer\CurlPyPro-Setup.exe" -ForegroundColor Green
} else {
    Write-Warning "Inno Setup 6 was not found; skipping the searchable Windows installer."
    Write-Host "Install Inno Setup 6, then run this script again to create it." -ForegroundColor Yellow
}

Write-Host "Portable executable: dist\CurlPyPro.exe" -ForegroundColor Green
