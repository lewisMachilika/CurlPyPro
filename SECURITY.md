# Security Policy

## Supported versions

Only the latest release of CurlPyPro receives security fixes.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private reporting:
[Report a vulnerability](https://github.com/lewisMachilika/CurlPyPro/security/advisories/new),
or email **lmachilika@gmail.com** with the subject `CurlPyPro security`.

Include a description, steps to reproduce, and the impact you expect. You should
get an acknowledgement within 7 days.

## Scope

CurlPyPro stores request history, environments, cookies and OAuth tokens
**unencrypted** in `~/.curlpypro/curlpypro.db` on your machine. Treat that file
like any other file that contains secrets. Secrets leaking unexpectedly into
exported files, code snippets, HAR exports, or logs are in scope.
