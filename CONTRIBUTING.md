# Contributing to CurlPyPro

Thanks for taking the time to contribute! Bug reports, feature ideas, docs fixes
and pull requests are all welcome.

## Ways to help

- **Report a bug**: open an issue with the *Bug report* template. Include your
  OS, how you run CurlPyPro (installer, `.exe`, or from source + Python version),
  and steps to reproduce.
- **Suggest a feature**: use the *Feature request* template and describe the
  problem you're trying to solve, not only the solution.
- **Send a pull request**: see below. For anything larger than a small fix,
  open an issue first so we can agree on the approach.

## Development setup

```bash
git clone https://github.com/lewisMachilika/CurlPyPro.git
cd CurlPyPro
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python curlpypro.py
```

The app stores its data in `~/.curlpypro/curlpypro.db` (SQLite). To test with a
clean slate, rename that folder temporarily rather than deleting your history.

## Pull request checklist

- Branch from the default branch and keep each PR focused on one change.
- Match the existing code style (PEP 8, 4-space indents, descriptive names).
- Long-running work (HTTP calls, token fetches, stress tests) must run in a
  `QThread` worker. Never block the UI thread.
- Don't break stored data: if you change what goes into the SQLite database,
  add a migration path for existing users.
- Run `python -m pytest` and `ruff check curlpypro.py tests`; add tests for
  new behaviour (GUI tests use `pytest-qt` and the local server in `tests/server.py`).
- Test the flows you touched by hand on at least one OS, and say which in the PR.
- Update `README.md` and add a line under **Unreleased** in `CHANGELOG.md` for
  user-visible changes.

## Commit messages

Use short, imperative subjects, e.g. `Add GraphQL body mode` or
`Fix proxy detection on Windows PAC setups`.

## Code of conduct

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
