# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Tests tab:** assertions on status code, response time, headers, body,
  body size and JSON paths, with quick-add presets and a results tab on the response.
- **Capture tab:** save values from a response (JSON path, header, status,
  regex) into the active environment to chain requests.
- **Collection runner:** run a collection with iterations, delay and
  stop-on-failure; export results as JSON or JUnit XML. Collections gained a
  right-click menu (run, rename, delete, open in new tab).
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
