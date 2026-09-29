# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Open-source project files: license, contributing guide, code of conduct,
  security policy, issue and pull request templates.

### Fixed
- Requests now honour the Windows system proxy, including PAC setups.

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
