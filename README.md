# CurlPyPro

A lightweight Postman-style API client built with **PyQt6** (GUI) and **requests** (HTTP).

Features:

- Request builder (method, URL)
- Query-parameter editor with enable/disable rows and URL extraction
- Headers editor (simple `Key: Value` lines)
- Body editor (raw text / JSON)
- Auth helpers for bearer tokens, basic auth, and API keys
- Advanced transport controls for redirects, SSL verification, timeouts, and retries
- Environment variables with `{{VAR}}` substitution (URL, params, headers, auth, body)
- Send button with response viewer (status, headers, pretty JSON body, image preview, response insights)
- History + saving/loading requests (collections stored locally)
- Code-snippet generation (curl, python requests, PowerShell, Java, axios)
- File/image attachments via `multipart/form-data`
- Stress testing with concurrency, status/latency summaries, and optional pre-request scripts
- Raw response saving, base64 extraction, and HAR export

On first run the app creates `~/.curlpro/` storing history, envs, and collections.

## Run from source

1. Create and activate a virtual environment:
   - macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`
   - Windows: `python -m venv .venv && .venv\Scripts\activate`
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python curlpro.py`

## Build a standalone app

CurlPyPro packages into a single self-contained executable per OS using
[PyInstaller](https://pyinstaller.org). **Each OS must be built on that OS** —
PyInstaller cannot cross-compile.

### Local build

- **Windows:** `./scripts/build.ps1` → produces `dist/CurlPyPro.exe`
- **macOS:** `./scripts/build.sh` → produces `dist/CurlPyPro.app` (and `dist/CurlPyPro`)
- **Linux:** `./scripts/build.sh` → produces `dist/CurlPyPro`

The scripts install build deps (`requirements-dev.txt`), clean previous output,
and run PyInstaller against `curlpro.spec`.

You can also build manually:

```
pip install -r requirements-dev.txt
pyinstaller curlpro.spec --noconfirm
```

### Automated multi-OS builds (CI)

`.github/workflows/build.yml` builds Windows, macOS, and Linux executables on
GitHub Actions:

- **Every push / PR:** binaries are uploaded as workflow **artifacts** (download
  from the run's Summary page).
- **Tagged release** (push a tag like `v1.0.0`): all three binaries are attached
  to the GitHub Release automatically.

```
git tag v1.0.0
git push origin v1.0.0
```

### App icon (optional)

Drop an icon and it's picked up automatically by `curlpro.spec`:

- Windows: `assets/icon.ico`
- macOS: `assets/icon.icns`

## Notes

- The single-file executable is self-contained — no Python install needed to run it.
- First launch is slightly slower because the bundle unpacks to a temp dir.
- On macOS/Windows, unsigned binaries may trigger a Gatekeeper/SmartScreen
  warning; code signing is out of scope for this setup.
