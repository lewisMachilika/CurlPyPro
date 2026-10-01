import os
import sys
import tempfile
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

# CurlPyPro keeps its data in ~/.curlpypro and resolves that path on import, so
# point HOME at a throwaway directory before the module is loaded.
_HOME = tempfile.mkdtemp(prefix="curlpypro-test-home-")
os.environ["HOME"] = _HOME
os.environ["USERPROFILE"] = _HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
# Stop keyring from talking to a real OS keychain during tests.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import curlpypro  # noqa: E402
from server import start_server  # noqa: E402


@pytest.fixture(scope="session")
def server():
    srv, base = start_server()
    yield base
    srv.shutdown()


@pytest.fixture
def fresh_db():
    """Start each test that touches storage with an empty database."""
    if curlpypro.DB_FILE.exists():
        curlpypro.DB_FILE.unlink()
    curlpypro.init_db()
    yield curlpypro.DB_FILE


@pytest.fixture
def popups(monkeypatch):
    """Record message boxes instead of blocking on them."""
    shown = []

    def record(kind, answer=QMessageBox.StandardButton.Ok):
        def fn(parent, title, text, *a, **k):
            shown.append((kind, title, text))
            return answer
        return fn

    monkeypatch.setattr(QMessageBox, "information", record("info"))
    monkeypatch.setattr(QMessageBox, "warning", record("warning"))
    monkeypatch.setattr(QMessageBox, "critical", record("critical"))
    monkeypatch.setattr(QMessageBox, "question", record("question", QMessageBox.StandardButton.Yes))
    return shown


@pytest.fixture
def win(qtbot, fresh_db, popups):
    w = curlpypro.CurlPyProMainWindow()
    qtbot.addWidget(w)
    yield w
    for i in range(w.tabs.count()):
        for store in w.tabs.widget(i)._request_store.values():
            store["thread"].wait(5000)
