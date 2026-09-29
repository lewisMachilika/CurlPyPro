<p align="center">
  <img src="assets/icon-master.png" alt="CurlPyPro logo" width="120">
</p>

<h1 align="center">CurlPyPro</h1>

<p align="center">
  A fast, lightweight, offline-first API client for the desktop, built with Python and PyQt6.
  <br>
  No account, no cloud sync, no telemetry. Your requests stay on your machine.
</p>

<p align="center">
  <a href="https://github.com/lewisMachilika/CurlPyPro/actions/workflows/build.yml"><img alt="Build" src="https://github.com/lewisMachilika/CurlPyPro/actions/workflows/build.yml/badge.svg"></a>
  <a href="https://github.com/lewisMachilika/CurlPyPro/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/lewisMachilika/CurlPyPro?sort=semver"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue.svg">
  <img alt="Platforms" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg">
</p>

<!-- Add a screenshot: save it as docs/screenshot.png and uncomment the line below. -->
<!-- <p align="center"><img src="docs/screenshot.png" alt="CurlPyPro screenshot" width="900"></p> -->

---

## Why CurlPyPro?

Postman-style tools keep getting heavier and increasingly want you signed in.
CurlPyPro is a single native app that opens instantly, works fully offline, and
keeps everything in one local SQLite file you control.

## Features

### Build requests

- Any HTTP method, multiple request tabs
- Query-parameter table with enable/disable per row (auto-extracted from the URL)
- Headers, raw/JSON body, and `multipart/form-data` file and image uploads
- Paste a `curl` command into the URL bar to import it

### Auth

- Bearer token, Basic auth, API key (header or query)
- OAuth 2.0 token fetching

### Environments

- `{{VAR}}` substitution in URL, params, headers, auth and body
- Switch between environments (dev / staging / prod) in one click

### Inspect responses

- Status, timing, size, and headers
- Pretty-printed JSON, HTML and XML; image and HTML previews
- Save raw responses, extract base64 payloads, export HAR files

### Transport control

- Redirects, SSL verification, timeouts, and automatic retries
- Proxy support, including the Windows system proxy and PAC setups
- Cookie jar viewer and editor

### Productivity

- History and saved collections
- Code snippets for curl, Python `requests`, PowerShell, Java, and axios
- Stress testing with concurrency, status breakdowns and latency summaries
- Light and dark themes

## Install

### Download (recommended)

Grab the latest build for your OS from the
[Releases page](https://github.com/lewisMachilika/CurlPyPro/releases/latest):

| OS      | File                                                          |
|---------|---------------------------------------------------------------|
| Windows | `CurlPyPro-Setup.exe` (installer) or `CurlPyPro.exe` (portable) |
| macOS   | `CurlPyPro` (see note on Gatekeeper below)                    |
| Linux   | `CurlPyPro`, then `chmod +x CurlPyPro`                        |

> Binaries are not code-signed yet, so Windows SmartScreen or macOS Gatekeeper
> may warn on first launch. On Windows choose **More info → Run anyway**; on
> macOS right-click the app and choose **Open**.

### Run from source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/lewisMachilika/CurlPyPro.git
cd CurlPyPro
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python curlpypro.py
```

## Keyboard shortcuts

| Shortcut       | Action                |
|----------------|-----------------------|
| `Ctrl+T`       | New request tab       |
| `Ctrl+W`       | Close current tab     |
| `Delete`       | Delete selected history entry |
| `Ctrl+Shift+C` | Copy current snippet (in the snippet dialog) |

## Where is my data?

Everything lives in `~/.curlpypro/curlpypro.db` (SQLite): history, collections,
environments, cookies and settings. Back up or copy that file to move your
workspace to another machine.

> Tokens and passwords are stored **unencrypted** in that file. See
> [SECURITY.md](SECURITY.md).

## Build a standalone app

CurlPyPro packages into a single self-contained executable with
[PyInstaller](https://pyinstaller.org). Each OS must be built on that OS;
PyInstaller cannot cross-compile.

- **Windows:** `./scripts/build.ps1` produces `dist/CurlPyPro.exe`, plus
  `dist/installer/CurlPyPro-Setup.exe` when [Inno Setup 6](https://jrsoftware.org/isinfo.php)
  is installed. The installer adds a Start Menu entry and an optional desktop shortcut.
- **macOS:** `./scripts/build.sh` produces `dist/CurlPyPro.app`.
- **Linux:** `./scripts/build.sh` produces `dist/CurlPyPro`.

Or manually:

```bash
pip install -r requirements-dev.txt
pyinstaller curlpypro.spec --noconfirm
```

### Releases via CI

[`.github/workflows/build.yml`](.github/workflows/build.yml) builds Windows,
macOS and Linux binaries on every push and pull request. Pushing a version tag
attaches all three to a GitHub Release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Roadmap

Ideas being considered. Upvote or discuss them in
[Issues](https://github.com/lewisMachilika/CurlPyPro/issues):

- [ ] Response tests and assertions (status, JSON path, headers) with a collection runner
- [ ] Import Postman collections and OpenAPI/Swagger specs
- [ ] GraphQL body mode with schema introspection
- [ ] WebSocket and Server-Sent Events support
- [ ] Response diff between two runs
- [ ] Chain requests: capture a value from one response into an environment variable
- [ ] Encrypted storage for secrets (OS keychain)
- [ ] Command-line runner for CI pipelines

## Contributing

Contributions are welcome! Read [CONTRIBUTING.md](CONTRIBUTING.md) to get
started, and please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Found a security issue? See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © Lewis Machilika and CurlPyPro contributors
