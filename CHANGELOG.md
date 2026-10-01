# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Binary body** type that sends a file's raw bytes (with the file's MIME type
  unless you set `Content-Type`); included in generated code snippets.
- **Dynamic variables** (`{{$guid}}`, `{{$timestamp}}`, `{{$isoTimestamp}}`,
  `{{$randomInt}}`, …) and variable names containing `.` or `-`, as in Postman.
- Undefined `{{VARS}}` are listed in the status bar when a request is sent.
- **Export collections to Postman v2.1**, keeping folders, auth, bodies and
  assertions Postman can express. Postman import now also converts response-time,
  header and body-text tests and `file` bodies.
- **Save in place:** a request opened from a collection remembers where it came
  from; `Ctrl+S` updates it. The save dialog now asks for a request name.
- Collection search box (`Ctrl+P`); rename, duplicate and move requests.
- **Response filter** by text or JSON path, a Copy button, and a color-coded status line.
- Menu bar (File, Request, Tools, View, Help) with shortcuts; `Ctrl+Enter`/`F5`
  send, `Ctrl+L`, `Ctrl+D`, `Ctrl+Tab`, `Ctrl+F`, `Ctrl+E`, `Ctrl+R` and more (`F1` lists them).
- Working **dark theme** and text-size controls under View.
- Automated test suite (pytest + pytest-qt, 66 tests) and a lint + test CI workflow.

### Changed
- The HTML preview engine is created only when an HTML response arrives, so
  new tabs open faster and use less memory.
- The stress test builds requests with the same code as Send, so OAuth 2.0 and
  binary bodies work there too.
- Double-clicking a collection request opens it in the current tab if that tab
  is empty, otherwise in a new tab, instead of overwriting your work.
- Generated curl commands use `curl` (not `curl.exe`) outside Windows; snippets
  only include attachments when the body type is Form Data.

### Fixed
- The Send button advertised `Ctrl+Enter / F5`, but neither shortcut existed.
- The **Binary** body type sent the text box contents instead of a file.
- The dark theme only recolored the window background, and there was no way to
  switch themes or change the text size from the app.
- The app aborted on Linux when run as root (e.g. in Docker), because the
  HTML preview's Chromium sandbox refuses to start.
- Binary responses no longer interrupt with a "Save it now?" dialog (unless
  the request came from a curl command with `--output`).
- The SSE console dropped the last event when the stream ended without a blank line.
- `curlpypro --version` reported 1.0.0 in the 1.1.0 release.
- Saved snippets, requests and exports are written as UTF-8 on every OS.
- "Extract & Save Base64" showed as "Extract _Save Base64".

## [1.1.0]

### Added
- **Tests tab:** assertions on status code, response time, headers, body,
  body size and JSON paths, with quick-add presets and a results tab on the response.
- **Capture tab:** save values from a response (JSON path, header, status,
  regex) into the active environment to chain requests.
- **Collection runner:** run a collection with iterations, delay and
  stop-on-failure; export results as JSON or JUnit XML. Collections gained a
  right-click menu (run, rename, delete, open in new tab).
- **Command-line runner:** `curlpypro run` runs a saved collection or a
  CurlPyPro/Postman/OpenAPI file with environments, `--var` overrides and
  JUnit/JSON reports; exit codes 0 (pass), 1 (failures), 2 (usage error).
  Also `curlpypro --version`.
- **Import** Postman v2.0/v2.1 collections and environments, and OpenAPI 3 /
  Swagger 2 specs in JSON or YAML.
- **GraphQL** body mode with a variables editor and an introspection-based
  schema browser that generates queries.
- **WebSocket / SSE console** (Options menu).
- **Response diff** against the previous response or any history entry.
- **Secret environment variables** stored in the OS keychain.
- Open-source project files: license, contributing guide, code of conduct,
  security policy, issue and pull request templates.

### Changed
- New optional dependencies: PyYAML, websocket-client and keyring. Each
  feature is disabled gracefully when its package is missing.
- `{{VARS}}` are now resolved in OAuth 2.0 settings.

### Fixed
- Requests now honour the Windows system proxy, including PAC setups.
- Cancelling the Environment Manager no longer keeps unsaved edits, and
  switching environments inside it no longer discards them.

## [1.0.0]

### Added
- Request builder with query params, headers, body, and auth helpers
  (Bearer, Basic, API key, OAuth 2.0).
- Environments with `{{VAR}}` substitution.
- History and local collections stored in SQLite.
- Import from cURL; code snippets for curl, Python, PowerShell, Java and axios.
- Multipart file uploads; image, HTML, JSON and XML response previews.
- Stress testing with concurrency and latency summaries.
- HAR export, raw response saving, base64 extraction.
- Standalone builds for Windows, macOS and Linux, plus a Windows installer.
