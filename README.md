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
- Headers, raw/JSON body, `multipart/form-data` file and image uploads, and
  **binary** bodies sent straight from a file
- **GraphQL** body mode with a variables editor and a schema browser that
  builds queries from introspection
- Paste a `curl` command into the URL bar to import it

### Auth

- Bearer token, Basic auth, API key (header or query)
- OAuth 2.0 (client credentials, password, authorization code)

### Environments

- `{{VAR}}` substitution in URL, params, headers, auth and body; names may use
  dots and dashes like Postman's (`{{api.key}}`, `{{base-url}}`)
- **Dynamic variables** with a fresh value on every send: `{{$guid}}`,
  `{{$timestamp}}`, `{{$isoTimestamp}}`, `{{$randomInt}}`, `{{$randomEmail}}` and more
- Variables the active environment doesn't define are flagged in the status bar when you send
- Switch between environments (dev / staging / prod) in one click
- **Request chaining:** capture a value from a response (JSON path, header,
  status or regex) into a variable that later requests use, e.g. log in once
  and reuse `{{TOKEN}}` everywhere
- **Secrets in the OS keychain:** mark a variable as secret and its value is
  stored in Windows Credential Manager, macOS Keychain or Secret Service
  instead of the local database

### Inspect responses

- Status (color-coded), timing, size, and headers
- **Filter the response** by text or by JSON path (`$.data.items[0]`), and copy it in one click
- Pretty-printed JSON, HTML and XML; image and HTML previews
- **Diff** each response against the previous one (or any history entry),
  ignoring JSON key order
- Save raw responses, extract base64 payloads, export HAR files

### Transport control

- Redirects, SSL verification, timeouts, and automatic retries
- Proxy support, including the Windows system proxy and PAC setups
- Cookie jar viewer and editor

### Test and automate

- **Assertions** on status, response time, headers, body size, body text and
  JSON paths (`$.data.items[0].id`, `$.items.length`), with quick-add presets
- **Collection runner:** run a whole collection in order with iterations,
  delays and stop-on-failure; export results as JSON or **JUnit XML** for CI
- **Command-line runner** (`curlpypro run`) for CI pipelines, with exit codes
  and JUnit/JSON reports
- Post-response Python scripts that can read the response and update variables

### Import

- **Postman** collections (v2.0 / v2.1) and environments, including folders,
  auth inheritance, path variables, binary bodies and common tests (status,
  response time, header present, body contains)
- **OpenAPI 3** and **Swagger 2** specs in JSON or YAML: every operation becomes
  a request with example bodies, query params and auth, plus a `{{baseUrl}}`
  environment

### Export

- CurlPyPro's own JSON format, or **Postman v2.1** (**File → Export Collection
  (Postman v2.1)** or right-click a collection). Folders, auth, bodies and the
  assertions Postman can express come along.

### Realtime

- **WebSocket** console: connect, send and receive messages with pretty-printed JSON
- **Server-Sent Events** viewer that parses event names, ids and data

### Productivity

- History and saved collections; search requests across collections (`Ctrl+P`)
- Open a saved request, edit it and press `Ctrl+S` to update it in place;
  rename, duplicate and reorder requests from the right-click menu
- Menu bar with every action and its shortcut; `F1` lists all shortcuts
- Code snippets for curl, Python `requests`, PowerShell, Java, and axios
- Stress testing with concurrency, status breakdowns and latency summaries
- Light and dark themes (**View → Dark Theme**) and adjustable text size

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

## Run collections from the command line

`curlpypro run` runs a collection's requests, captures and assertions without
opening the window. It exits with **0** when everything passed, **1** when a
request or assertion failed, and **2** on a usage error, so it works as a CI step.

```bash
# A collection saved in the app, with a saved environment
python curlpypro.py run "My API" --env staging

# A file: CurlPyPro export, Postman collection or OpenAPI spec (JSON/YAML)
python curlpypro.py run --file shop.postman_collection.json --var baseUrl=http://localhost:8080

# CI: environment from a file, secrets from variables, reports for the CI UI
python curlpypro.py run --file api-tests.json --env-file ci.env.json \
    --var TOKEN="$API_TOKEN" --junit results.xml --json results.json
```

Sample output:

```text
CurlPyPro 1.0.0: running "Chain" (2 requests, 1 iteration)

  PASS  POST    Login  200  3 ms
  FAIL  GET     Me  200  2 ms
          ✗ JSON path $.user equals bob  (actual: ann)

Requests: 2 (1 failed, 0 errors)  Tests: 2/3 passed  Time: 0.1 s  FAILED
```

Useful options: `-n/--iterations`, `--delay MS`, `--timeout SECONDS`, `--bail`
(stop at the first failure), `-k/--insecure`, `--no-scripts`, `-v/--verbose`.
Run `python curlpypro.py run --help` for the full list.

Variables are applied in this order, later ones winning: variables stored in
the collection file (Postman collection variables, OpenAPI `baseUrl`), then
`--env`, then `--env-file`, then each `--var`. Captured values are passed
between requests during the run but are not saved back.

### In GitHub Actions

Export a collection from the app (**Export** under Collections) into your repo,
then:

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
- name: Install CurlPyPro
  run: |
    sudo apt-get update && sudo apt-get install -y libgl1 libegl1 libxkbcommon0 libdbus-1-3
    git clone --depth 1 https://github.com/lewisMachilika/CurlPyPro.git /tmp/curlpypro
    pip install -r /tmp/curlpypro/requirements.txt
- name: API tests
  run: python /tmp/curlpypro/curlpypro.py run --file tests/api.json --var baseUrl=http://localhost:8080 --junit api-results.xml
```

> Use the Python source in CI. The packaged Windows `.exe` is a windowed app:
> it can print to a console you launch it from, but shells don't wait for it or
> report its exit code.

## Keyboard shortcuts

| Shortcut                    | Action                                   |
|-----------------------------|------------------------------------------|
| `Ctrl+Enter` / `F5`         | Send the request                         |
| `Ctrl+S`                    | Save (updates the collection request the tab came from) |
| `Ctrl+Shift+S`              | Save to a collection as a new request    |
| `Ctrl+T` / `Ctrl+W`         | New tab / close tab                      |
| `Ctrl+D`                    | Duplicate tab                            |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Next / previous tab                    |
| `Ctrl+L`                    | Focus the URL bar                        |
| `Ctrl+F`                    | Filter the response (text or `$.json.path`) |
| `Ctrl+P`                    | Search collections                       |
| `Ctrl+Shift+C`              | Copy the request as cURL                 |
| `Ctrl+E`                    | Manage environments                      |
| `Ctrl+R`                    | Run the selected collection              |
| `Ctrl+O`                    | Import a collection or API spec          |
| `Ctrl+=` / `Ctrl+-` / `Ctrl+0` | Larger / smaller / default text       |
| `Delete`                    | Delete selected history entry            |
| `F1`                        | Show all shortcuts                       |

In the WebSocket console, `Ctrl+Enter` sends the message.

## Where is my data?

Everything lives in `~/.curlpypro/curlpypro.db` (SQLite): history, collections,
environments, cookies and settings. Back up or copy that file to move your
workspace to another machine.

Environment variables marked **Secret** are the exception: their values live in
your OS keychain under the service name `CurlPyPro`, so they don't travel with
the database file. Everything else, including auth fields typed directly into a
request and stored history, is saved **unencrypted**. See [SECURITY.md](SECURITY.md).

## Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest          # core, importers, CLI runner and the GUI (headless)
ruff check curlpypro.py tests
```

The GUI tests drive the real window with `pytest-qt` against a local test
server, so they need no network access. On a Linux machine without a display,
run them with `QT_QPA_PLATFORM=offscreen`.

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

- [x] Response tests and assertions with a collection runner
- [x] Import Postman collections and OpenAPI/Swagger specs
- [x] GraphQL body mode with schema introspection
- [x] WebSocket and Server-Sent Events support
- [x] Response diff between two runs
- [x] Chain requests by capturing response values into variables
- [x] Secrets in the OS keychain
- [x] Command-line runner for CI pipelines
- [x] Export collections back to Postman format
- [x] Automated test suite and linting in CI

## Contributing

Contributions are welcome! Read [CONTRIBUTING.md](CONTRIBUTING.md) to get
started, and please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Found a security issue? See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © Lewis Machilika and CurlPyPro contributors
