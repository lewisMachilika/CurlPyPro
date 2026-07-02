#!/usr/bin/env python3
"""
curlpro.py - CurlPro with code-snippet generation (curl, python requests, powershell, axios)
and support for attaching images/files in multipart/form-data.

Requirements:
    pip install PyQt6 requests
Run:
    python curlpro.py
"""
import sys
import os
import json
import time
import re
import sqlite3
import mimetypes
import datetime as _dt
from pathlib import Path
import shlex
import base64
from urllib.parse import unquote, urlparse, urlencode, parse_qsl, urlunparse
import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util import Retry
except Exception:  # pragma: no cover - depends on requests' vendored stack
    Retry = None
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox, QListWidget,
    QSplitter, QMessageBox, QFileDialog, QTabWidget, QListWidgetItem,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QFrame, QScrollArea,
    QGroupBox, QDialog, QDialogButtonBox, QProgressBar, QStatusBar,
    QToolButton, QMenu, QPlainTextEdit, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QDoubleSpinBox,
    QCheckBox, QWidgetAction
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QClipboard, QColor, QShortcut, QKeySequence, QFont, QPixmap

# ---------------------------
# Storage (SQLite, single file in the data dir)
# ---------------------------
DATA_DIR = Path.home() / ".curlpro"
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "curlpro.db"

def _connect():
    return sqlite3.connect(str(DB_FILE))

def init_db():
    """Create tables if needed and migrate any legacy JSON files once."""
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "  key TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS history ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  timestamp REAL,"
                "  data TEXT NOT NULL"
                ")"
            )
    finally:
        conn.close()
    _migrate_legacy_json()


def _migrate_legacy_json():
    """Import data written by the old JSON-file storage, once."""
    docs = {
        "settings": DATA_DIR / "settings.json",
        "envs": DATA_DIR / "envs.json",
        "collections": DATA_DIR / "collections.json",
    }
    conn = _connect()
    try:
        with conn:
            have_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if have_docs == 0:
                for key, path in docs.items():
                    if path.exists():
                        try:
                            value = json.loads(path.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO documents(key, value) VALUES(?, ?)",
                            (key, json.dumps(value)),
                        )

            have_hist = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            hist_path = DATA_DIR / "history.json"
            if have_hist == 0 and hist_path.exists():
                try:
                    entries = json.loads(hist_path.read_text(encoding="utf-8"))
                except Exception:
                    entries = []
                if isinstance(entries, list):
                    for entry in entries:
                        conn.execute(
                            "INSERT INTO history(timestamp, data) VALUES(?, ?)",
                            (entry.get("timestamp"), json.dumps(entry)),
                        )
    finally:
        conn.close()


def load_document(key, default):
    """Load a wholesale JSON document (settings / envs / collections)."""
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT value FROM documents WHERE key=?", (key,)
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            return json.loads(row[0])
    except Exception:
        pass
    return default


def save_document(key, data):
    """Atomically upsert a wholesale JSON document."""
    try:
        conn = _connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO documents(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(data)),
                )
        finally:
            conn.close()
    except Exception:
        pass


def load_history():
    """Return history entries oldest-first, each tagged with its row id."""
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, data FROM history ORDER BY id ASC"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    result = []
    for rid, data in rows:
        try:
            entry = json.loads(data)
        except Exception:
            continue
        entry["_id"] = rid
        result.append(entry)
    return result


def add_history(entry):
    """Insert one history entry; return its new row id (or None on failure)."""
    try:
        conn = _connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO history(timestamp, data) VALUES(?, ?)",
                    (entry.get("timestamp"), json.dumps(entry)),
                )
                return cur.lastrowid
        finally:
            conn.close()
    except Exception:
        return None


def delete_history(entry):
    """Delete a single history entry by its stored row id."""
    rid = entry.get("_id")
    if rid is None:
        return
    try:
        conn = _connect()
        try:
            with conn:
                conn.execute("DELETE FROM history WHERE id=?", (rid,))
        finally:
            conn.close()
    except Exception:
        pass


def clear_history_db():
    """Remove all history entries."""
    try:
        conn = _connect()
        try:
            with conn:
                conn.execute("DELETE FROM history")
        finally:
            conn.close()
    except Exception:
        pass


placeholder_pattern = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def apply_env(text: str, env: dict):
    if not text:
        return text

    def repl(m):
        key = m.group(1)
        return str(env.get(key, m.group(0)))

    return placeholder_pattern.sub(repl, text)


def parse_status_code_list(text: str):
    """Parse comma/space separated retry status codes into a sorted list."""
    codes = set()
    for part in re.split(r"[\s,]+", text or ""):
        if not part:
            continue
        try:
            code = int(part)
        except ValueError:
            continue
        if 100 <= code <= 599:
            codes.add(code)
    return sorted(codes)


def add_params_to_url(url: str, params) -> str:
    """Append resolved query params to a URL while preserving any existing query."""
    if not params:
        return url
    parsed = urlparse(url)
    existing = parse_qsl(parsed.query, keep_blank_values=True)
    if isinstance(params, dict):
        param_items = list(params.items())
    else:
        param_items = list(params)
    merged = existing + [(str(k), str(v)) for k, v in param_items]
    return urlunparse(parsed._replace(query=urlencode(merged, doseq=True)))


def strip_query_from_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query=""))


def make_retry_session(retry_total=0, retry_backoff=0.0, retry_statuses=None, max_redirects=30):
    """Return a requests session configured with optional urllib3 retries."""
    session = requests.Session()
    try:
        session.max_redirects = max(1, int(max_redirects or 30))
    except Exception:
        session.max_redirects = 30

    retry_total = int(retry_total or 0)
    if retry_total > 0 and Retry is not None:
        retry_kwargs = {
            "total": retry_total,
            "connect": retry_total,
            "read": retry_total,
            "status": retry_total,
            "backoff_factor": float(retry_backoff or 0),
            "status_forcelist": retry_statuses or [429, 500, 502, 503, 504],
            "raise_on_status": False,
            "respect_retry_after_header": True,
        }
        try:
            retry = Retry(allowed_methods=None, **retry_kwargs)
        except TypeError:
            retry = Retry(method_whitelist=None, **retry_kwargs)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    return session


# ---------------------------
# curl command parsing
# ---------------------------
# Long/short curl flags that take NO argument — safe to skip while tokenising.
_CURL_NOARG_FLAGS = {
    "-i", "--include", "-s", "--silent", "-S", "--show-error", "-k", "--insecure",
    "-L", "--location", "-v", "--verbose", "-f", "--fail", "--compressed",
    "-#", "--progress-bar", "-g", "--globoff", "-N", "--no-buffer", "-#",
    "-O", "--remote-name", "-j", "--junk-session-cookies", "--raw",
}


def looks_like_curl(text: str) -> bool:
    """True if `text` looks like a pasted curl command (vs. a plain URL)."""
    s = (text or "").strip().lower()
    return s.startswith("curl ") or s.startswith("curl\n") or s == "curl" or s.startswith("curl\t")


def parse_curl_command(text: str) -> dict:
    """Parse a curl command line into request parts.

    Returns a dict: method, url, headers (dict), data (str or None),
    is_form (bool), form (list of "k=v" / "k=@file" strings),
    user (str "u:p" or None).  Best-effort: unknown flags are skipped.
    """
    result = {
        "method": None,
        "url": None,
        "headers": {},
        "data_parts": [],
        "is_form": False,
        "form": [],
        "user": None,
        "get_with_data": False,
    }

    text = (text or "").strip()
    if looks_like_curl(text):
        # drop the leading "curl" word
        text = text[4:]
    # Collapse shell line-continuations (backslash + newline) into spaces.
    text = re.sub(r"\\\r?\n", " ", text)
    # Some single-line widgets flatten newlines before paste handlers see them,
    # leaving continuations as "\ --url" / "\ -H" tokens.
    text = re.sub(r"\\\s+(?=(?:-{1,2}[A-Za-z]|https?://))", " ", text)

    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — retry without posix escaping.
        try:
            tokens = shlex.split(text, posix=False)
        except ValueError:
            tokens = text.split()

    i, n = 0, len(tokens)

    def take():
        nonlocal i
        i += 1
        return tokens[i] if i < n else ""

    while i < n:
        tok = tokens[i]
        if tok in ("-X", "--request"):
            result["method"] = take().upper()
        elif tok == "--url":
            result["url"] = take()
        elif tok in ("-H", "--header"):
            h = take()
            if ":" in h:
                k, v = h.split(":", 1)
                if k.strip():
                    result["headers"][k.strip()] = v.strip()
        elif tok in ("-A", "--user-agent"):
            result["headers"]["User-Agent"] = take()
        elif tok in ("-e", "--referer"):
            result["headers"]["Referer"] = take()
        elif tok in ("-b", "--cookie"):
            result["headers"]["Cookie"] = take()
        elif tok in ("-u", "--user"):
            result["user"] = take()
        elif tok in ("-d", "--data", "--data-raw", "--data-ascii",
                     "--data-binary", "--data-urlencode"):
            result["data_parts"].append(take())
        elif tok in ("-F", "--form", "--form-string"):
            result["is_form"] = True
            result["form"].append(take())
        elif tok in ("-G", "--get"):
            result["get_with_data"] = True
        elif tok in _CURL_NOARG_FLAGS:
            pass
        elif tok.startswith("-"):
            # Unknown flag — skip it; don't guess whether it takes a value.
            pass
        else:
            # First bare token is the URL.
            if result["url"] is None:
                result["url"] = tok
        i += 1  # advance past this flag (take() already consumed any argument)

    if result["data_parts"]:
        result["data"] = "&".join(result["data_parts"])
    else:
        result["data"] = None
    return result


# ---------------------------
# Network thread
# ---------------------------
class RequestThread(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(
        self, method, url, headers, json_body, data, files=None, timeout=30,
        params=None, auth=None, allow_redirects=True, verify_ssl=True,
        max_redirects=30, retry_total=0, retry_backoff=0.0,
        retry_statuses=None,
    ):
        super().__init__()
        self.method = method
        self.url = url
        self.headers = headers
        self.json_body = json_body
        self.data = data
        self.files = files or None  # list of (field, (filename, fileobj, mime))
        self.timeout = timeout
        self.params = params or None  # dict of query params (used by API-key auth)
        self.auth = auth              # (username, password) tuple for Basic auth, or None
        self.allow_redirects = allow_redirects
        self.verify_ssl = verify_ssl
        self.max_redirects = max_redirects
        self.retry_total = retry_total
        self.retry_backoff = retry_backoff
        self.retry_statuses = retry_statuses or []

    # def run(self):
    #     try:
    #         t0 = time.time()
    #         resp = requests.request(
    #             self.method, self.url,
    #             headers=self.headers,
    #             json=self.json_body,
    #             data=self.data,
    #             files=self.files,          # multipart support
    #             timeout=self.timeout
    #         )
    #         elapsed = time.time() - t0
    #         self.finished.emit({
    #             'response': resp,
    #             'elapsed': elapsed
    #         })
    #     except Exception as e:
    #         self.error.emit(str(e))

    def run(self):
        session = None
        try:
            session = make_retry_session(
                self.retry_total, self.retry_backoff,
                self.retry_statuses, self.max_redirects
            )
            t0 = time.time()
            resp = session.request(
                self.method, self.url,
                headers=self.headers,
                json=self.json_body,
                data=self.data,
                files=self.files,          # multipart support
                params=self.params,        # query-param auth support
                auth=self.auth,            # HTTP Basic auth support
                allow_redirects=self.allow_redirects,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            elapsed = time.time() - t0
            self.finished.emit({
                'response': resp,
                'elapsed': elapsed
            })
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            # Ensure any file objects passed in `files` are closed
            try:
                if self.files:
                    for item in self.files:
                        # item is ("field", (filename, fileobj, mime)) OR ("field", fileobj)
                        val = item[1]
                        fileobj = None
                        if hasattr(val, "__iter__") and len(val) >= 2 and hasattr(val[1], "close"):
                            fileobj = val[1]
                        elif hasattr(val, "close"):
                            fileobj = val
                        if fileobj:
                            try:
                                fileobj.close()
                            except Exception:
                                pass
            except Exception:
                pass
# ---------------------------
# Stress test worker & dialog
# ---------------------------
_STRESS_SCRIPT_TEMPLATE = """\
# Pre-request script  —  runs before every stress-test request.
# Available globals: requests, json, random, uuid, time, threading
# (also Faker / faker if the 'faker' package is installed)
#
# Define either or both of these functions:
#
#   setup(ctx)           — called ONCE before the test starts.
#                          Use it to fetch tokens, open DB connections, etc.
#                          ctx is a shared dict available to every call.
#                          ctx['_lock'] is a threading.Lock() you can use freely.
#
#   before_request(ctx)  — called before EACH request (from worker threads).
#                          Return a dict with any subset of:
#                            headers   – merged on top of the configured headers
#                            json_body – replaces the configured JSON body
#                            data      – replaces form/raw data
#                            params    – replaces query params
#
# ── Example 1: Bearer token fetched once, auto-refreshed on expiry ──────────
# def setup(ctx):
#     _fetch_token(ctx)
#
# def _fetch_token(ctx):
#     r = requests.post('https://auth.example.com/token',
#                       json={'username': 'admin', 'password': 'secret'})
#     ctx['token']   = r.json()['access_token']
#     ctx['expires'] = time.time() + 3500   # refresh 100 s before 1 h expiry
#
# def before_request(ctx):
#     with ctx['_lock']:
#         if time.time() >= ctx.get('expires', 0):
#             _fetch_token(ctx)
#         token = ctx['token']
#     return {'headers': {'Authorization': f'Bearer {token}'}}
#
# ── Example 2: unique fake email in every request body ──────────────────────
# def before_request(ctx):
#     email = f'user_{uuid.uuid4().hex[:10]}@stress.example.com'
#     return {'json_body': {'email': email, 'name': 'Test User'}}
#
# ── Example 3: combine both ─────────────────────────────────────────────────
# def setup(ctx):
#     _fetch_token(ctx)
#
# def _fetch_token(ctx):
#     r = requests.post('https://auth.example.com/token',
#                       json={'username': 'admin', 'password': 'secret'})
#     ctx['token']   = r.json()['access_token']
#     ctx['expires'] = time.time() + 3500
#
# def before_request(ctx):
#     with ctx['_lock']:
#         if time.time() >= ctx.get('expires', 0):
#             _fetch_token(ctx)
#         token = ctx['token']
#     email = f'user_{uuid.uuid4().hex[:10]}@stress.example.com'
#     return {
#         'headers':   {'Authorization': f'Bearer {token}'},
#         'json_body': {'email': email},
#     }
"""


class StressTestWorker(QThread):
    progress = pyqtSignal(int, int)   # completed, total
    result_ready = pyqtSignal(dict)
    all_done = pyqtSignal(dict)
    script_error = pyqtSignal(str)

    def __init__(self, config, total, concurrency, script_fns=None):
        super().__init__()
        self.config = config
        self.total = total
        self.concurrency = concurrency
        self._stop = False
        self._script_fns = script_fns or {}
        self._ctx = {}

    def stop(self):
        self._stop = True

    def _do_request(self, overrides=None):
        if self._stop:
            return {'status': 0, 'elapsed': 0, 'error': 'stopped'}
        t0 = time.time()
        cfg = self.config
        headers = dict(cfg.get('headers', {}))
        json_body = cfg.get('json_body')
        data = cfg.get('data')
        params = cfg.get('params')
        if overrides:
            if 'headers' in overrides:
                headers.update(overrides['headers'])
            if 'json_body' in overrides:
                json_body = overrides['json_body']
            if 'data' in overrides:
                data = overrides['data']
            if 'params' in overrides:
                params = overrides['params']
        session = None
        try:
            session = make_retry_session(
                cfg.get('retry_total', 0),
                cfg.get('retry_backoff', 0.0),
                cfg.get('retry_statuses') or [],
                cfg.get('max_redirects', 30),
            )
            resp = session.request(
                cfg['method'], cfg['url'],
                headers=headers,
                json=json_body,
                data=data,
                params=params,
                auth=cfg.get('auth'),
                allow_redirects=cfg.get('allow_redirects', True),
                verify=cfg.get('verify_ssl', True),
                timeout=cfg.get('timeout', 30),
            )
            return {'status': resp.status_code, 'elapsed': (time.time() - t0) * 1000, 'error': None}
        except Exception as e:
            return {'status': 0, 'elapsed': (time.time() - t0) * 1000, 'error': str(e)}
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    def _call_before_request(self):
        fn = self._script_fns.get('before_request')
        if not fn:
            return None
        try:
            return fn(self._ctx) or {}
        except Exception as e:
            return {'_script_error': str(e)}

    def run(self):
        import concurrent.futures as cf
        import threading as _threading

        self._ctx = {'_lock': _threading.Lock()}

        setup_fn = self._script_fns.get('setup')
        if setup_fn:
            try:
                setup_fn(self._ctx)
            except Exception as e:
                self.script_error.emit(f"setup() raised: {e}")
                self.all_done.emit({'total': 0, 'requested': self.total,
                                    'success': 0, 'errors': 0, 'wall_ms': 0, 'rps': 0})
                return

        results = []
        t_wall = time.time()

        def task():
            overrides = self._call_before_request()
            return self._do_request(overrides)

        with cf.ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futs = [ex.submit(task) for _ in range(self.total)]
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r.get('error') != 'stopped':
                    results.append(r)
                    self.result_ready.emit(dict(r))
                self.progress.emit(len(results), self.total)
                if self._stop:
                    break

        wall_ms = (time.time() - t_wall) * 1000
        if results:
            times = [r['elapsed'] for r in results]
            st = sorted(times)
            n = len(st)
            statuses = {}
            for r in results:
                statuses[r['status']] = statuses.get(r['status'], 0) + 1
            summary = {
                'total': n,
                'requested': self.total,
                'success': sum(1 for r in results if r['error'] is None and 200 <= r['status'] < 400),
                'errors': sum(1 for r in results if r['error'] is not None),
                'min_ms': st[0],
                'max_ms': st[-1],
                'avg_ms': sum(times) / n,
                'p50_ms': st[n // 2],
                'p95_ms': st[max(0, int(n * 0.95) - 1)],
                'statuses': statuses,
                'wall_ms': wall_ms,
                'rps': n / (wall_ms / 1000) if wall_ms > 0 else 0,
            }
        else:
            summary = {'total': 0, 'requested': self.total, 'success': 0, 'errors': 0,
                       'wall_ms': wall_ms, 'rps': 0}
        self.all_done.emit(summary)


class StressTestDialog(QDialog):
    def __init__(self, request_config, parent=None):
        super().__init__(parent)
        self.request_config = request_config
        self.worker = None
        self._results = []
        self.setWindowTitle("Stress Test")
        self.resize(760, 700)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Config group
        config_group = QGroupBox("Configuration")
        cg = QVBoxLayout(config_group)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target:"))
        tl = QLabel(f"  {self.request_config['method']}  {self.request_config['url']}")
        tl.setWordWrap(True)
        font = tl.font()
        font.setBold(True)
        tl.setFont(font)
        target_row.addWidget(tl, 1)
        cg.addLayout(target_row)

        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("Total requests:"))
        self.total_spin = QSpinBox()
        self.total_spin.setRange(1, 10000000)
        self.total_spin.setValue(100)
        self.total_spin.setFixedWidth(100)
        params_row.addWidget(self.total_spin)
        params_row.addSpacing(24)
        params_row.addWidget(QLabel("Concurrency:"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 10000)
        self.concurrency_spin.setValue(10)
        self.concurrency_spin.setFixedWidth(100)
        params_row.addWidget(self.concurrency_spin)
        params_row.addStretch()
        cg.addLayout(params_row)
        layout.addWidget(config_group)

        # Script group
        script_group = QGroupBox("Pre-request Script  (optional)")
        script_group.setCheckable(True)
        script_group.setChecked(False)
        sl = QVBoxLayout(script_group)

        self.script_editor = QPlainTextEdit()
        self.script_editor.setFont(_monospace_font(9))
        self.script_editor.setPlaceholderText(_STRESS_SCRIPT_TEMPLATE)
        self.script_editor.setMinimumHeight(180)
        sl.addWidget(self.script_editor)

        script_btn_row = QHBoxLayout()
        verify_btn = QPushButton("Verify Script")
        verify_btn.setFixedHeight(30)
        verify_btn.clicked.connect(self._verify_script)
        clear_btn = QPushButton("Load Template")
        clear_btn.setFixedHeight(30)
        clear_btn.clicked.connect(lambda: self.script_editor.setPlainText(_STRESS_SCRIPT_TEMPLATE))
        self.script_status = QLabel("")
        self.script_status.setStyleSheet("color: #28a745;")
        script_btn_row.addWidget(verify_btn)
        script_btn_row.addWidget(clear_btn)
        script_btn_row.addWidget(self.script_status, 1)
        sl.addLayout(script_btn_row)
        layout.addWidget(script_group)
        self._script_group = script_group

        # Buttons
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("Run Stress Test")
        self.run_btn.setFixedHeight(36)
        self.run_btn.clicked.connect(self.run_test)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.clicked.connect(self.stop_test)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress
        self.progress_label = QLabel("Ready — configure above and click Run")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

        # Results
        results_group = QGroupBox("Results")
        rl = QVBoxLayout(results_group)

        self.stats_label = QLabel("—")
        self.stats_label.setWordWrap(True)
        f2 = self.stats_label.font()
        f2.setFamily("Consolas")
        self.stats_label.setFont(f2)
        rl.addWidget(self.stats_label)

        self.status_table = QTableWidget(0, 3)
        self.status_table.setHorizontalHeaderLabels(["Status", "Count", "%"])
        sh = self.status_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.status_table.setMaximumHeight(160)
        self.status_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rl.addWidget(self.status_table)
        layout.addWidget(results_group)

    # ---------- Script helpers ----------

    def _compile_script(self, code):
        """Exec script and return dict of extracted callables, or raise on error."""
        import uuid as _uuid, random as _random, threading as _threading
        ns = {
            'requests': requests,
            'json': json,
            'random': _random,
            'uuid': _uuid,
            'time': time,
            'threading': _threading,
        }
        try:
            import faker as _faker
            ns['Faker'] = _faker.Faker
            ns['faker'] = _faker.Faker()
        except ImportError:
            pass
        exec(compile(code, '<stress_script>', 'exec'), ns)
        return {name: ns[name] for name in ('setup', 'before_request')
                if name in ns and callable(ns[name])}

    def _verify_script(self):
        code = self.script_editor.toPlainText().strip()
        if not code:
            self.script_status.setStyleSheet("color: #888;")
            self.script_status.setText("(empty — no script will run)")
            return
        try:
            fns = self._compile_script(code)
            names = list(fns.keys()) or ['(no functions defined)']
            self.script_status.setStyleSheet("color: #28a745;")
            self.script_status.setText(f"OK — found: {', '.join(names)}")
        except Exception as e:
            self.script_status.setStyleSheet("color: #dc3545;")
            self.script_status.setText(f"Error: {e}")

    # ---------- Run ----------

    def run_test(self):
        script_fns = {}
        if self._script_group.isChecked():
            code = self.script_editor.toPlainText().strip()
            if code:
                try:
                    script_fns = self._compile_script(code)
                except Exception as e:
                    QMessageBox.critical(self, "Script Error",
                                         f"Cannot compile pre-request script:\n{e}")
                    return

        self._results = []
        self.status_table.setRowCount(0)
        self.stats_label.setText("—")
        total = self.total_spin.value()
        concurrency = self.concurrency_spin.value()
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0 / {total}")
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.worker = StressTestWorker(self.request_config, total, concurrency, script_fns)
        self.worker.progress.connect(self._on_progress)
        self.worker.result_ready.connect(self._on_result)
        self.worker.all_done.connect(self._on_done)
        self.worker.script_error.connect(self._on_script_error)
        self.worker.start()

    def stop_test(self):
        if self.worker:
            self.worker.stop()
        self.stop_btn.setEnabled(False)

    # ---------- Slots ----------

    def _on_script_error(self, msg):
        QMessageBox.critical(self, "Script Runtime Error", msg)

    def _on_progress(self, completed, total):
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"{completed} / {total} completed")

    def _on_result(self, result):
        self._results.append(result)
        n = self.total_spin.value()
        if len(self._results) % max(1, n // 20) == 0:
            self._refresh_stats()

    def _on_done(self, summary):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._refresh_stats()
        wall = summary.get('wall_ms', 0)
        rps = summary.get('rps', 0)
        total = summary.get('total', 0)
        self.progress_label.setText(
            f"Done — {total} requests in {wall:.0f} ms  ({rps:.1f} req/s)"
        )

    def _refresh_stats(self):
        rs = self._results
        if not rs:
            return
        times = [r['elapsed'] for r in rs]
        st = sorted(times)
        n = len(st)
        p95 = st[max(0, int(n * 0.95) - 1)]
        p50 = st[n // 2]
        successes = sum(1 for r in rs if r['error'] is None and 200 <= r['status'] < 400)
        errors = sum(1 for r in rs if r['error'] is not None)

        self.stats_label.setText(
            f"Completed: {n}   Success: {successes}   Errors: {errors}\n"
            f"Avg: {sum(times)/n:.1f} ms   Min: {st[0]:.1f} ms   "
            f"Max: {st[-1]:.1f} ms   P50: {p50:.1f} ms   P95: {p95:.1f} ms"
        )

        statuses = {}
        for r in rs:
            statuses[r['status']] = statuses.get(r['status'], 0) + 1

        self.status_table.setRowCount(0)
        for status, count in sorted(statuses.items()):
            row = self.status_table.rowCount()
            self.status_table.insertRow(row)
            label = str(status) if status > 0 else 'Network Error'
            si = QTableWidgetItem(label)
            ci = QTableWidgetItem(str(count))
            pi = QTableWidgetItem(f"{count/n*100:.1f}%")
            for item in (si, ci, pi):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if status == 0 or status >= 400:
                color = QColor('#dc3545')
            elif 200 <= status < 300:
                color = QColor('#28a745')
            else:
                color = QColor('#fd7e14')
            for item in (si, ci, pi):
                item.setForeground(color)
            self.status_table.setItem(row, 0, si)
            self.status_table.setItem(row, 1, ci)
            self.status_table.setItem(row, 2, pi)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        super().closeEvent(event)


# ---------------------------
# Environment dialog
# ---------------------------
class EnvironmentDialog(QDialog):
    def __init__(self, envs, parent=None):
        super().__init__(parent)
        self.envs = envs
        self.setWindowTitle("Environment Manager")
        self.setModal(True)
        self.resize(700, 500)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        top_frame = QFrame()
        top_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        top_layout = QHBoxLayout(top_frame)

        top_layout.addWidget(QLabel("Environment:"))
        self.env_combo = QComboBox()
        if not self.envs:
            self.envs["default"] = {}
        self.env_combo.addItems(list(self.envs.keys()))
        self.env_combo.currentTextChanged.connect(self.load_environment)
        top_layout.addWidget(self.env_combo)

        btn_new = QPushButton("New")
        btn_duplicate = QPushButton("Duplicate")
        btn_delete = QPushButton("Delete")

        btn_new.clicked.connect(self.new_environment)
        btn_duplicate.clicked.connect(self.duplicate_environment)
        btn_delete.clicked.connect(self.delete_environment)

        for b in (btn_new, btn_duplicate, btn_delete):
            b.setFixedHeight(32)

        top_layout.addWidget(btn_new)
        top_layout.addWidget(btn_duplicate)
        top_layout.addWidget(btn_delete)
        top_layout.addStretch()

        layout.addWidget(top_frame)

        vars_group = QGroupBox("Variables")
        vars_layout = QVBoxLayout(vars_group)

        self.vars_widget = QWidget()
        self.vars_layout = QVBoxLayout(self.vars_widget)
        self.vars_layout.setContentsMargins(0, 0, 0, 0)
        self.vars_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidget(self.vars_widget)
        scroll.setWidgetResizable(True)
        vars_layout.addWidget(scroll)

        add_var_btn = QPushButton("Add Variable")
        add_var_btn.clicked.connect(self.add_variable_row)
        add_var_btn.setFixedHeight(32)
        vars_layout.addWidget(add_var_btn)

        layout.addWidget(vars_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.env_combo.currentText():
            self.load_environment()

    def load_environment(self):
        for i in reversed(range(self.vars_layout.count())):
            widget = self.vars_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        env_name = self.env_combo.currentText()
        if env_name in self.envs:
            env_vars = self.envs[env_name]
            for key, value in env_vars.items():
                self.add_variable_row(key, value)
        self.add_variable_row()

    def add_variable_row(self, key="", value=""):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(6, 4, 6, 4)
        row_layout.setSpacing(6)

        key_input = QLineEdit(key)
        key_input.setPlaceholderText("Variable name")
        value_input = QLineEdit(value)
        value_input.setPlaceholderText("Variable value")

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(28, 28)
        remove_btn.clicked.connect(lambda: self.remove_variable_row(row_widget))

        row_layout.addWidget(key_input, 1)
        row_layout.addWidget(value_input, 2)
        row_layout.addWidget(remove_btn, 0)

        self.vars_layout.addWidget(row_widget)

    def remove_variable_row(self, row_widget):
        row_widget.setParent(None)

    def new_environment(self):
        name, ok = QInputDialog.getText(self, "New Environment", "Environment name:")
        if ok and name:
            if name in self.envs:
                QMessageBox.warning(self, "Exists", "An environment with that name already exists.")
                return
            self.envs[name] = {}
            self.env_combo.addItem(name)
            self.env_combo.setCurrentText(name)
            self.load_environment()

    def duplicate_environment(self):
        current = self.env_combo.currentText()
        if not current:
            return
        name, ok = QInputDialog.getText(self, "Duplicate Environment", "New environment name:", text=f"{current}_copy")
        if ok and name:
            if name in self.envs:
                QMessageBox.warning(self, "Exists", "An environment with that name already exists.")
                return
            self.envs[name] = dict(self.envs.get(current, {}))
            self.env_combo.addItem(name)
            self.env_combo.setCurrentText(name)
            self.load_environment()

    def delete_environment(self):
        current = self.env_combo.currentText()
        if not current or current == "default":
            QMessageBox.warning(self, "Cannot Delete", "Cannot delete the default environment.")
            return
        reply = QMessageBox.question(self, "Delete Environment", f"Delete environment '{current}'?")
        if reply == QMessageBox.StandardButton.Yes:
            del self.envs[current]
            self.env_combo.removeItem(self.env_combo.currentIndex())

    def accept(self):
        self.save_current_environment()
        super().accept()

    def save_current_environment(self):
        env_name = self.env_combo.currentText()
        if not env_name:
            return
        env_vars = {}
        for i in range(self.vars_layout.count()):
            row_widget = self.vars_layout.itemAt(i).widget()
            if row_widget:
                layout = row_widget.layout()
                key_input = layout.itemAt(0).widget()
                value_input = layout.itemAt(1).widget()
                key = key_input.text().strip()
                value = value_input.text().strip()
                if key:
                    env_vars[key] = value
        self.envs[env_name] = env_vars


# ---------------------------
# Snippet dialog
# ---------------------------
def _monospace_font(point_size=10):
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(point_size)
    return font


class MultiSnippetDialog(QDialog):
    """Tabbed dialog rendering one request in several languages at once.

    Built for power users: switch languages without re-opening menus, copy or
    save the active tab, and edit in place before copying.
    """
    # Sensible file extension per language for "Save Current".
    _EXT = {"curl": ".sh", "PowerShell": ".ps1", "Python": ".py",
            "Java": ".java", "Axios": ".js"}

    def __init__(self, snippets: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate Code — all languages")
        self.resize(900, 600)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        mono = _monospace_font()
        for name, code in snippets.items():
            editor = QPlainTextEdit()
            editor.setPlainText(code)
            editor.setFont(mono)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.tabs.addTab(editor, name)
        layout.addWidget(self.tabs)

        btn_layout = QHBoxLayout()
        hint = QLabel("Ctrl+Shift+C copies the active tab.  Edits here stay local.")
        hint.setStyleSheet("color: #888;")
        btn_layout.addWidget(hint)
        btn_layout.addStretch()
        save_btn = QPushButton("Save Current…")
        save_btn.clicked.connect(self.save_current)
        copy_btn = QPushButton("Copy Current")
        copy_btn.setDefault(True)
        copy_btn.clicked.connect(self.copy_current)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(copy_btn)
        layout.addLayout(btn_layout)

        QShortcut(QKeySequence("Ctrl+Shift+C"), self, activated=self.copy_current)

    def _current(self):
        return self.tabs.currentWidget(), self.tabs.tabText(self.tabs.currentIndex())

    def copy_current(self):
        editor, name = self._current()
        if editor:
            QApplication.clipboard().setText(editor.toPlainText())
            self.setWindowTitle(f"Copied {name} snippet to clipboard")

    def save_current(self):
        editor, name = self._current()
        if not editor:
            return
        ext = self._EXT.get(name, ".txt")
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save Snippet", str(Path.home() / f"request{ext}"), "All Files (*)"
        )
        if not fname:
            return
        try:
            Path(fname).write_text(editor.toPlainText())
            QMessageBox.information(self, "Saved", f"Saved {name} snippet to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))


class SaveToCollectionDialog(QDialog):
    """Pick an existing collection or type a new name."""
    def __init__(self, existing_collections: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save to Collection")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)

        if existing_collections:
            layout.addWidget(QLabel("Select an existing collection:"))
            self.list_widget = QListWidget()
            self.list_widget.addItems(existing_collections)
            self.list_widget.setMaximumHeight(140)
            self.list_widget.itemClicked.connect(
                lambda item: self.name_edit.setText(item.text())
            )
            layout.addWidget(self.list_widget)
        else:
            self.list_widget = None

        layout.addWidget(QLabel("Collection name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Type a name or select above")
        layout.addWidget(self.name_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.returnPressed.connect(self.accept)

    def collection_name(self) -> str:
        return self.name_edit.text().strip()


class SnippetDialog(QDialog):
    """Simple dialog to show generated code and allow copying"""
    def __init__(self, title, snippet, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(800, 420)
        layout = QVBoxLayout(self)
        self.snippet_editor = QPlainTextEdit()
        self.snippet_editor.setReadOnly(False)
        self.snippet_editor.setPlainText(snippet)
        self.snippet_editor.setFont(_monospace_font())
        self.snippet_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.snippet_editor)

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self.copy_to_clipboard)
        save_btn = QPushButton("Save to File")
        save_btn.clicked.connect(self.save_to_file)
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(copy_btn)
        layout.addLayout(btn_layout)

    def copy_to_clipboard(self):
        clipboard: QClipboard = QApplication.clipboard()
        clipboard.setText(self.snippet_editor.toPlainText())
        QMessageBox.information(self, "Copied", "Snippet copied to clipboard.")

    def save_to_file(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save Snippet", str(Path.home() / "snippet.txt"), "Text Files (*.txt);;All Files (*)")
        if not fname:
            return
        try:
            Path(fname).write_text(self.snippet_editor.toPlainText())
            QMessageBox.information(self, "Saved", f"Saved snippet to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))


# ---------------------------
# URL bar that understands pasted curl commands
# ---------------------------
class UrlLineEdit(QLineEdit):
    """QLineEdit for the URL bar.

    When a curl command is pasted (which a single-line QLineEdit would otherwise
    mangle by stripping newlines), the full original text is emitted via
    `curlPasted` instead of being inserted, so the main window can import it.
    """
    curlPasted = pyqtSignal(str)

    def _try_import_curl(self) -> bool:
        """If the clipboard holds a curl command, emit it and report handled.

        Reads the clipboard directly (rather than relying on the text Qt is
        about to insert) so the original newlines survive — a single-line
        QLineEdit strips them, which would corrupt a multi-line --data body.
        """
        text = QApplication.clipboard().text()
        if looks_like_curl(text):
            self.curlPasted.emit(text)
            return True
        return False

    def keyPressEvent(self, event):
        # QLineEdit.paste() is a non-virtual slot, so Ctrl+V never reaches our
        # override below. Intercept the paste key sequence here instead — this
        # is the path that actually fires for keyboard pastes.
        if event.matches(QKeySequence.StandardKey.Paste) and self._try_import_curl():
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        # Reroute the right-click "Paste" entry through our curl check too.
        menu = self.createStandardContextMenu()
        paste_seq = QKeySequence(QKeySequence.StandardKey.Paste)
        for act in menu.actions():
            if act.shortcut() == paste_seq:
                try:
                    act.triggered.disconnect()
                except TypeError:
                    pass
                act.triggered.connect(self._context_paste)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _context_paste(self):
        if not self._try_import_curl():
            super().paste()

    def paste(self):
        if not self._try_import_curl():
            super().paste()

    def insertFromMimeData(self, source):
        text = source.text() if source is not None and source.hasText() else ""
        if looks_like_curl(text):
            self.curlPasted.emit(text)
            return
        handler = getattr(super(), "insertFromMimeData", None)
        if handler:
            handler(source)
        elif text:
            self.insert(text)


# ---------------------------
# Main window
# ---------------------------
class CurlProMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CurlPro - With Snippet Generator")
        self.resize(1400, 900)

        init_db()
        self.settings = load_document("settings", {
            "theme": "light",
            "font_size": 10,
            "auto_format_json": True,
            "request_timeout": 30,
            "follow_redirects": True,
            "verify_ssl": True,
            "max_redirects": 30,
            "retry_total": 0,
            "retry_backoff": 0.25,
            "retry_statuses": "429,500,502,503,504",
        })
        self.history = load_history()
        self.envs = load_document("envs", {"default": {}})
        self.collections = load_document("collections", {})
        
        self._last_response = None          # requests.Response
        self._last_response_bytes = b""     # raw body

        self._request_store = {}   # req_id -> {thread, info, snapshot, result, error, elapsed}
        self._req_counter = 0
        self.file_attachments = []  # [{field, path, filename, mime}]

        self.init_ui()
        self.init_status_bar()
        self.apply_theme()

    # ---------- UI ----------
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_widget_layout = QVBoxLayout(main_widget)
        main_widget_layout.addWidget(main_splitter)

        left_panel = self.create_left_panel()
        main_splitter.addWidget(left_panel)

        right_panel = self.create_right_panel()
        main_splitter.addWidget(right_panel)

        main_splitter.setSizes([360, 1040])

    def create_left_panel(self):
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # Collections
        collections_group = QGroupBox("Collections")
        collections_layout = QVBoxLayout(collections_group)

        collections_toolbar = QHBoxLayout()
        btn_new_collection = QPushButton("New")
        btn_import_collection = QPushButton("Import")
        btn_export_collection = QPushButton("Export")

        for b in (btn_new_collection, btn_import_collection, btn_export_collection):
            b.setFixedHeight(34)

        btn_new_collection.clicked.connect(self.create_collection)
        btn_import_collection.clicked.connect(self.import_collection)
        btn_export_collection.clicked.connect(self.export_collection)

        collections_toolbar.addWidget(btn_new_collection)
        collections_toolbar.addWidget(btn_import_collection)
        collections_toolbar.addWidget(btn_export_collection)
        collections_toolbar.addStretch()

        btn_coll_expand = QToolButton()
        btn_coll_expand.setText("⊞")
        btn_coll_expand.setToolTip("Expand all collections")
        btn_coll_expand.clicked.connect(lambda: self.collections_tree.expandAll())
        collections_toolbar.addWidget(btn_coll_expand)

        btn_coll_collapse = QToolButton()
        btn_coll_collapse.setText("⊟")
        btn_coll_collapse.setToolTip("Collapse all collections")
        btn_coll_collapse.clicked.connect(lambda: self.collections_tree.collapseAll())
        collections_toolbar.addWidget(btn_coll_collapse)

        collections_layout.addLayout(collections_toolbar)

        self.collections_tree = QTreeWidget()
        self.collections_tree.setHeaderLabel("Collections")
        self.collections_tree.itemDoubleClicked.connect(self.load_collection_item)
        collections_layout.addWidget(self.collections_tree)

        left_layout.addWidget(collections_group, 2)

        # History
        history_group = QGroupBox("Request History")
        history_layout = QVBoxLayout(history_group)

        # Search / filter row
        history_filter_layout = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search URL, method, status…")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self.reload_history)
        history_filter_layout.addWidget(self.history_search, 1)

        self.history_method_filter = QComboBox()
        self.history_method_filter.addItems(
            ["All", "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
        )
        self.history_method_filter.setFixedWidth(95)
        self.history_method_filter.currentTextChanged.connect(self.reload_history)
        history_filter_layout.addWidget(self.history_method_filter)
        history_layout.addLayout(history_filter_layout)

        # Action toolbar
        history_toolbar = QHBoxLayout()
        self.history_count_label = QLabel("0 requests")
        history_toolbar.addWidget(self.history_count_label)
        history_toolbar.addStretch()

        btn_expand = QToolButton()
        btn_expand.setText("⊞")
        btn_expand.setToolTip("Expand all groups")
        btn_expand.clicked.connect(lambda: self.history_tree.expandAll())
        history_toolbar.addWidget(btn_expand)

        btn_collapse = QToolButton()
        btn_collapse.setText("⊟")
        btn_collapse.setToolTip("Collapse all groups")
        btn_collapse.clicked.connect(lambda: self.history_tree.collapseAll())
        history_toolbar.addWidget(btn_collapse)

        btn_clear_history = QPushButton("Clear All")
        btn_clear_history.setFixedHeight(34)
        btn_clear_history.clicked.connect(self.clear_history)
        history_toolbar.addWidget(btn_clear_history)
        history_layout.addLayout(history_toolbar)

        self.history_tree = QTreeWidget()
        self.history_tree.setHeaderHidden(True)
        self.history_tree.setUniformRowHeights(True)
        self.history_tree.itemDoubleClicked.connect(self.load_history_item)
        self.history_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_tree.customContextMenuRequested.connect(self.show_history_context_menu)
        history_layout.addWidget(self.history_tree)

        # Delete key removes the selected entry
        del_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.history_tree)
        del_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        del_shortcut.activated.connect(self.delete_selected_history)

        left_layout.addWidget(history_group, 1)

        self.reload_collections()
        self.reload_history()

        return left_widget

    def create_right_panel(self):
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # --- Request group ---
        request_group = QGroupBox("Request")
        request_layout = QVBoxLayout(request_group)

        url_layout = QHBoxLayout()
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(110)
        self.method_combo.setFixedHeight(36)
        self.method_combo.setToolTip("HTTP method")
        # Color the method so the request type is obvious at a glance.
        self.method_combo.currentTextChanged.connect(self._update_method_color)

        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText("Enter URL or paste a curl command   —   press Enter to send")
        # Pasting a curl command imports it into the form instead of mangling it.
        self.url_input.curlPasted.connect(self.import_curl)
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setMinimumHeight(36)
        font = self.url_input.font()
        font.setPointSize(11)
        self.url_input.setFont(font)
        # Pressing Enter anywhere in the URL bar fires the request — the single
        # most expected interaction in a tool like this.
        self.url_input.returnPressed.connect(self.send_request)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("send_btn")
        self.send_btn.setFixedWidth(100)
        self.send_btn.setFixedHeight(36)
        self.send_btn.setToolTip("Send request  (Ctrl+Enter / F5)")
        self.send_btn.clicked.connect(self.send_request)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 86400)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setSpecialValueText("∞  (no timeout)")
        self.timeout_spin.setValue(self.settings.get("request_timeout", 30))
        self.timeout_spin.setFixedWidth(120)
        self.timeout_spin.setToolTip("Request timeout (0 = no timeout, max 86400 s / 24 h)")

        timeout_widget = QWidget()
        timeout_row = QHBoxLayout(timeout_widget)
        timeout_row.setContentsMargins(8, 4, 8, 4)
        timeout_row.addWidget(QLabel("Timeout:"))
        timeout_row.addWidget(self.timeout_spin)

        timeout_action = QWidgetAction(self)
        timeout_action.setDefaultWidget(timeout_widget)

        actions_menu = QMenu()
        actions_menu.addAction("Save to Collection", self.save_to_collection)
        actions_menu.addAction("Save as File", self.save_request_file)
        actions_menu.addAction("Load", self.load_request_file)
        actions_menu.addSeparator()
        actions_menu.addAction("Generate — All Languages…", self.generate_all_and_show)
        actions_menu.addAction("Generate — curl", lambda: self.generate_code_and_show("curl"))
        actions_menu.addAction("Generate — python-requests", lambda: self.generate_code_and_show("python-requests"))
        actions_menu.addAction("Generate — powershell", lambda: self.generate_code_and_show("powershell"))
        actions_menu.addAction("Generate — java", lambda: self.generate_code_and_show("java"))
        actions_menu.addAction("Generate — axios", lambda: self.generate_code_and_show("axios"))
        actions_menu.addSeparator()
        actions_menu.addAction("Stress Test…", self.open_stress_test)
        actions_menu.addSeparator()
        actions_menu.addAction(timeout_action)

        self.actions_btn = QToolButton()
        self.actions_btn.setText("Options ▾")
        self.actions_btn.setMenu(actions_menu)
        self.actions_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.actions_btn.setFixedHeight(36)

        url_layout.addWidget(self.method_combo)
        url_layout.addWidget(self.url_input, 1)
        url_layout.addWidget(self.send_btn)
        url_layout.addWidget(self.actions_btn)

        request_layout.addLayout(url_layout)

        # Environments
        env_layout = QHBoxLayout()
        env_layout.addWidget(QLabel("Environment:"))
        self.env_combo = QComboBox()
        if not self.envs:
            self.envs["default"] = {}
        self.env_combo.addItems(list(self.envs.keys()))
        env_layout.addWidget(self.env_combo)

        btn_manage_envs = QPushButton("Manage")
        btn_manage_envs.setFixedHeight(34)
        btn_manage_envs.clicked.connect(self.manage_environments)
        env_layout.addWidget(btn_manage_envs)
        env_layout.addStretch()

        request_layout.addLayout(env_layout)

        # Tabs
        self.request_tabs = QTabWidget()

        # Query params tab
        self.request_tabs.addTab(self.create_params_tab(), "Params")

        # Headers tab
        headers_widget = QWidget()
        headers_layout = QVBoxLayout(headers_widget)
        self.headers_text = QTextEdit()
        self.headers_text.setPlaceholderText("Content-Type: application/json\nAuthorization: Bearer {{TOKEN}}\n\nOne header per line in Key: Value format")
        self.headers_text.setMaximumHeight(200)
        headers_layout.addWidget(self.headers_text)
        self.request_tabs.addTab(headers_widget, "Headers")

        # Body tab
        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)

        body_toolbar = QHBoxLayout()
        self.body_type_combo = QComboBox()
        self.body_type_combo.addItems(["Raw", "JSON", "Form Data", "Binary"])
        self.body_type_combo.currentTextChanged.connect(self.on_body_type_changed)
        body_toolbar.addWidget(QLabel("Body Type:"))
        body_toolbar.addWidget(self.body_type_combo)

        format_btn = QPushButton("Format JSON")
        format_btn.setFixedHeight(32)
        format_btn.clicked.connect(self.format_json_body)
        body_toolbar.addWidget(format_btn)
        body_toolbar.addStretch()
        body_layout.addLayout(body_toolbar)

        self.body_text = QTextEdit()
        self.body_text.setPlaceholderText('{\n  "key": "value",\n  "user": "{{USERNAME}}"\n}')
        body_layout.addWidget(self.body_text)

        # --- Attachments panel (multipart/form-data) ---
        self.attachments_group = QGroupBox("Attachments (multipart/form-data)")
        attachments_layout = QVBoxLayout(self.attachments_group)

        self.attachments_list = QListWidget()
        attachments_layout.addWidget(self.attachments_list)

        attach_toolbar = QHBoxLayout()
        self.field_name_input = QLineEdit()
        self.field_name_input.setPlaceholderText("Form field name (e.g. file, image)")
        self.field_name_input.setFixedWidth(220)
        btn_add_file = QPushButton("Add Image/File")
        btn_remove_file = QPushButton("Remove Selected")
        attach_toolbar.addWidget(QLabel("Field:"))
        attach_toolbar.addWidget(self.field_name_input)
        attach_toolbar.addStretch()
        attach_toolbar.addWidget(btn_add_file)
        attach_toolbar.addWidget(btn_remove_file)
        attachments_layout.addLayout(attach_toolbar)

        btn_add_file.clicked.connect(self.add_attachment_file)
        btn_remove_file.clicked.connect(self.remove_selected_attachment)

        # hidden unless Form Data is selected
        self.attachments_group.setVisible(False)
        body_layout.addWidget(self.attachments_group)

        self.request_tabs.addTab(body_widget, "Body")

        # Auth tab
        self.request_tabs.addTab(self.create_auth_tab(), "Auth")

        # Advanced transport tab
        self.request_tabs.addTab(self.create_advanced_tab(), "Advanced")

        request_layout.addWidget(self.request_tabs)
        right_layout.addWidget(request_group, 1)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        # Active Requests panel
        active_group = QGroupBox("Active Requests")
        active_group.setMaximumHeight(155)
        active_layout_inner = QVBoxLayout(active_group)
        active_layout_inner.setContentsMargins(4, 4, 4, 4)
        self.active_requests_table = QTableWidget(0, 5)
        self.active_requests_table.setHorizontalHeaderLabels(["#", "Method", "URL", "Status", "ms"])
        hdr = self.active_requests_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.active_requests_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.active_requests_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.active_requests_table.cellDoubleClicked.connect(self._on_active_request_double_clicked)
        self.active_requests_table.setToolTip("Double-click a completed request to view its response")
        active_layout_inner.addWidget(self.active_requests_table)
        right_layout.addWidget(active_group)

        # --- Response group ---
        response_group = QGroupBox("Response")
        response_layout = QVBoxLayout(response_group)

        self.response_summary = QLabel("Ready to send request...")
        self.response_summary.setWordWrap(True)
        response_layout.addWidget(self.response_summary)

        self.response_tabs = QTabWidget()

        self.response_pretty = QTextEdit()
        self.response_pretty.setReadOnly(True)
        self.response_tabs.addTab(self.response_pretty, "Pretty")

        self.response_raw = QTextEdit()
        self.response_raw.setReadOnly(True)
        self.response_tabs.addTab(self.response_raw, "Raw")

        self.response_headers = QTextEdit()
        self.response_headers.setReadOnly(True)
        self.response_tabs.addTab(self.response_headers, "Headers")

        self.response_insights = QTextEdit()
        self.response_insights.setReadOnly(True)
        self.response_tabs.addTab(self.response_insights, "Insights")

        self.response_preview_label = QLabel("No preview available")
        self.response_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.response_preview_label.setMinimumSize(100, 100)
        self.response_preview_scroll = QScrollArea()
        self.response_preview_scroll.setWidget(self.response_preview_label)
        self.response_preview_scroll.setWidgetResizable(True)
        self.response_tabs.addTab(self.response_preview_scroll, "Preview")

        response_layout.addWidget(self.response_tabs)
        # Download toolbar
        download_toolbar = QHBoxLayout()
        self.btn_save_response_bytes = QPushButton("Save Response Body…")
        self.btn_save_response_bytes.setToolTip("Save raw response bytes to a file (good for PDFs/images/binary).")
        self.btn_save_response_bytes.clicked.connect(self._save_response_body_bytes)

        self.btn_extract_base64 = QPushButton("Extract & Save Base64…")
        self.btn_extract_base64.setToolTip("Parse JSON/Raw for base64 or data: URLs and save the decoded file.")
        self.btn_extract_base64.clicked.connect(self._extract_and_save_base64)

        self.btn_export_har = QPushButton("Export HAR")
        self.btn_export_har.setToolTip("Export the last request/response as an HTTP Archive file.")
        self.btn_export_har.clicked.connect(self._export_response_har)

        download_toolbar.addWidget(self.btn_save_response_bytes)
        download_toolbar.addWidget(self.btn_extract_base64)
        download_toolbar.addWidget(self.btn_export_har)
        download_toolbar.addStretch()
        response_layout.addLayout(download_toolbar)

        right_layout.addWidget(response_group, 1)

        return right_widget

    # ---------- Params ----------
    def create_params_tab(self):
        params_widget = QWidget()
        layout = QVBoxLayout(params_widget)

        toolbar = QHBoxLayout()
        btn_add = QPushButton("Add")
        btn_remove = QPushButton("Remove")
        btn_extract = QPushButton("Extract from URL")
        btn_clear = QPushButton("Clear")
        for btn in (btn_add, btn_remove, btn_extract, btn_clear):
            btn.setFixedHeight(32)
        btn_add.clicked.connect(lambda: self._add_param_row())
        btn_remove.clicked.connect(self._remove_selected_param_rows)
        btn_extract.clicked.connect(self._extract_params_from_url)
        btn_clear.clicked.connect(self._clear_params)

        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_remove)
        toolbar.addWidget(btn_extract)
        toolbar.addWidget(btn_clear)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.params_table = QTableWidget(0, 3)
        self.params_table.setHorizontalHeaderLabels(["On", "Key", "Value"])
        hdr = self.params_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.params_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.params_table.setAlternatingRowColors(True)
        layout.addWidget(self.params_table)

        return params_widget

    def _add_param_row(self, key="", value="", enabled=True):
        row = self.params_table.rowCount()
        self.params_table.insertRow(row)

        enabled_item = QTableWidgetItem("")
        enabled_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsSelectable
        )
        enabled_item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.params_table.setItem(row, 0, enabled_item)

        key_item = QTableWidgetItem(key)
        value_item = QTableWidgetItem(value)
        self.params_table.setItem(row, 1, key_item)
        self.params_table.setItem(row, 2, value_item)
        self.params_table.setCurrentCell(row, 1)

    def _remove_selected_param_rows(self):
        rows = sorted({idx.row() for idx in self.params_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            self.params_table.removeRow(row)

    def _clear_params(self):
        self.params_table.setRowCount(0)

    def _extract_params_from_url(self):
        url = self.url_input.text().strip()
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if not pairs:
            self.status_bar.showMessage("No query parameters found in URL")
            return
        for key, value in pairs:
            self._add_param_row(key, value, True)
        self.url_input.setText(strip_query_from_url(url))
        self.status_bar.showMessage(f"Extracted {len(pairs)} query parameter{'s' if len(pairs) != 1 else ''}")

    def _params_snapshot(self):
        rows = []
        if not hasattr(self, "params_table"):
            return rows
        for row in range(self.params_table.rowCount()):
            enabled_item = self.params_table.item(row, 0)
            key_item = self.params_table.item(row, 1)
            value_item = self.params_table.item(row, 2)
            rows.append({
                "enabled": enabled_item.checkState() == Qt.CheckState.Checked if enabled_item else True,
                "key": key_item.text() if key_item else "",
                "value": value_item.text() if value_item else "",
            })
        return rows

    def _restore_params(self, rows):
        self._clear_params()
        if isinstance(rows, dict):
            rows = [{"enabled": True, "key": k, "value": v} for k, v in rows.items()]
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            self._add_param_row(
                str(row.get("key", "")),
                str(row.get("value", "")),
                bool(row.get("enabled", True)),
            )

    def _resolved_params(self, env: dict):
        params = []
        for row in self._params_snapshot():
            if not row.get("enabled", True):
                continue
            key = apply_env((row.get("key") or "").strip(), env)
            if not key:
                continue
            params.append((key, apply_env(row.get("value") or "", env)))
        return params

    # ---------- Advanced transport ----------
    def create_advanced_tab(self):
        advanced_widget = QWidget()
        layout = QVBoxLayout(advanced_widget)

        transport_group = QGroupBox("Transport")
        transport_layout = QVBoxLayout(transport_group)

        self.follow_redirects_check = QCheckBox("Follow redirects")
        self.follow_redirects_check.setChecked(bool(self.settings.get("follow_redirects", True)))
        self.verify_ssl_check = QCheckBox("Verify SSL certificates")
        self.verify_ssl_check.setChecked(bool(self.settings.get("verify_ssl", True)))

        max_redirects_row = QHBoxLayout()
        max_redirects_row.addWidget(QLabel("Max redirects:"))
        self.max_redirects_spin = QSpinBox()
        self.max_redirects_spin.setRange(1, 100)
        self.max_redirects_spin.setValue(int(self.settings.get("max_redirects", 30)))
        self.max_redirects_spin.setFixedWidth(90)
        max_redirects_row.addWidget(self.max_redirects_spin)
        max_redirects_row.addStretch()

        transport_layout.addWidget(self.follow_redirects_check)
        transport_layout.addWidget(self.verify_ssl_check)
        transport_layout.addLayout(max_redirects_row)
        layout.addWidget(transport_group)

        retry_group = QGroupBox("Retries")
        retry_layout = QVBoxLayout(retry_group)

        retry_row = QHBoxLayout()
        retry_row.addWidget(QLabel("Retry attempts:"))
        self.retry_total_spin = QSpinBox()
        self.retry_total_spin.setRange(0, 10)
        self.retry_total_spin.setValue(int(self.settings.get("retry_total", 0)))
        self.retry_total_spin.setFixedWidth(90)
        retry_row.addWidget(self.retry_total_spin)
        retry_row.addSpacing(18)
        retry_row.addWidget(QLabel("Backoff:"))
        self.retry_backoff_spin = QDoubleSpinBox()
        self.retry_backoff_spin.setRange(0.0, 60.0)
        self.retry_backoff_spin.setDecimals(2)
        self.retry_backoff_spin.setSingleStep(0.25)
        self.retry_backoff_spin.setSuffix(" s")
        self.retry_backoff_spin.setValue(float(self.settings.get("retry_backoff", 0.25)))
        self.retry_backoff_spin.setFixedWidth(110)
        retry_row.addWidget(self.retry_backoff_spin)
        retry_row.addStretch()
        retry_layout.addLayout(retry_row)

        statuses_row = QHBoxLayout()
        statuses_row.addWidget(QLabel("Retry status codes:"))
        self.retry_statuses_input = QLineEdit()
        self.retry_statuses_input.setPlaceholderText("429,500,502,503,504")
        self.retry_statuses_input.setText(str(self.settings.get("retry_statuses", "429,500,502,503,504")))
        statuses_row.addWidget(self.retry_statuses_input)
        retry_layout.addLayout(statuses_row)

        layout.addWidget(retry_group)
        layout.addStretch()
        return advanced_widget

    def _advanced_snapshot(self):
        if not hasattr(self, "follow_redirects_check"):
            return {}
        return {
            "follow_redirects": self.follow_redirects_check.isChecked(),
            "verify_ssl": self.verify_ssl_check.isChecked(),
            "max_redirects": self.max_redirects_spin.value(),
            "retry_total": self.retry_total_spin.value(),
            "retry_backoff": self.retry_backoff_spin.value(),
            "retry_statuses": self.retry_statuses_input.text().strip(),
        }

    def _restore_advanced(self, cfg):
        cfg = cfg or {}
        if "follow_redirects" in cfg:
            self.follow_redirects_check.setChecked(bool(cfg.get("follow_redirects")))
        if "verify_ssl" in cfg:
            self.verify_ssl_check.setChecked(bool(cfg.get("verify_ssl")))
        if "max_redirects" in cfg:
            self.max_redirects_spin.setValue(int(cfg.get("max_redirects") or 30))
        if "retry_total" in cfg:
            self.retry_total_spin.setValue(int(cfg.get("retry_total") or 0))
        if "retry_backoff" in cfg:
            self.retry_backoff_spin.setValue(float(cfg.get("retry_backoff") or 0))
        if "retry_statuses" in cfg:
            self.retry_statuses_input.setText(str(cfg.get("retry_statuses") or ""))

    # ---------- Auth ----------
    def create_auth_tab(self):
        auth_widget = QWidget()
        auth_layout = QVBoxLayout(auth_widget)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type:"))
        self.auth_type_combo = QComboBox()
        self.auth_type_combo.addItems(["No Auth", "Bearer Token", "Basic Auth", "API Key"])
        self.auth_type_combo.setFixedWidth(180)
        type_row.addWidget(self.auth_type_combo)
        type_row.addStretch()
        auth_layout.addLayout(type_row)

        self.auth_stack = QStackedWidget()
        self.auth_type_combo.currentIndexChanged.connect(self.auth_stack.setCurrentIndex)

        # 0: No Auth
        no_auth_page = QWidget()
        no_auth_layout = QVBoxLayout(no_auth_page)
        no_auth_layout.addWidget(QLabel("This request does not use any authorization."))
        no_auth_layout.addStretch()
        self.auth_stack.addWidget(no_auth_page)

        # 1: Bearer Token
        bearer_page = QWidget()
        bearer_layout = QVBoxLayout(bearer_page)
        bearer_layout.addWidget(QLabel("Token:"))
        self.auth_bearer_token = QLineEdit()
        self.auth_bearer_token.setPlaceholderText("Token (supports {{VARS}})")
        bearer_layout.addWidget(self.auth_bearer_token)
        hint = QLabel("Adds header — Authorization: Bearer <token>")
        hint.setStyleSheet("color: #888;")
        bearer_layout.addWidget(hint)
        bearer_layout.addStretch()
        self.auth_stack.addWidget(bearer_page)

        # 2: Basic Auth
        basic_page = QWidget()
        basic_layout = QVBoxLayout(basic_page)
        basic_layout.addWidget(QLabel("Username:"))
        self.auth_basic_user = QLineEdit()
        self.auth_basic_user.setPlaceholderText("Username (supports {{VARS}})")
        basic_layout.addWidget(self.auth_basic_user)
        basic_layout.addWidget(QLabel("Password:"))
        self.auth_basic_pass = QLineEdit()
        self.auth_basic_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_basic_pass.setPlaceholderText("Password (supports {{VARS}})")
        basic_layout.addWidget(self.auth_basic_pass)
        self.auth_basic_show = QPushButton("Show Password")
        self.auth_basic_show.setCheckable(True)
        self.auth_basic_show.setFixedHeight(28)
        self.auth_basic_show.toggled.connect(
            lambda checked: self.auth_basic_pass.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        basic_layout.addWidget(self.auth_basic_show, alignment=Qt.AlignmentFlag.AlignLeft)
        basic_layout.addStretch()
        self.auth_stack.addWidget(basic_page)

        # 3: API Key
        apikey_page = QWidget()
        apikey_layout = QVBoxLayout(apikey_page)
        apikey_layout.addWidget(QLabel("Key:"))
        self.auth_apikey_key = QLineEdit()
        self.auth_apikey_key.setPlaceholderText("e.g. X-API-Key")
        apikey_layout.addWidget(self.auth_apikey_key)
        apikey_layout.addWidget(QLabel("Value:"))
        self.auth_apikey_value = QLineEdit()
        self.auth_apikey_value.setPlaceholderText("API key value (supports {{VARS}})")
        apikey_layout.addWidget(self.auth_apikey_value)
        addto_row = QHBoxLayout()
        addto_row.addWidget(QLabel("Add to:"))
        self.auth_apikey_addto = QComboBox()
        self.auth_apikey_addto.addItems(["Header", "Query Params"])
        self.auth_apikey_addto.setFixedWidth(180)
        addto_row.addWidget(self.auth_apikey_addto)
        addto_row.addStretch()
        apikey_layout.addLayout(addto_row)
        apikey_layout.addStretch()
        self.auth_stack.addWidget(apikey_page)

        auth_layout.addWidget(self.auth_stack)
        return auth_widget

    def get_auth_config(self) -> dict:
        """Return the current auth tab state as a serializable dict."""
        t = self.auth_type_combo.currentText()
        cfg = {"type": t}
        if t == "Bearer Token":
            cfg["token"] = self.auth_bearer_token.text()
        elif t == "Basic Auth":
            cfg["username"] = self.auth_basic_user.text()
            cfg["password"] = self.auth_basic_pass.text()
        elif t == "API Key":
            cfg["key"] = self.auth_apikey_key.text()
            cfg["value"] = self.auth_apikey_value.text()
            cfg["add_to"] = self.auth_apikey_addto.currentText()
        return cfg

    def set_auth_config(self, cfg: dict):
        """Restore the auth tab state from a saved dict."""
        cfg = cfg or {}
        self.auth_type_combo.setCurrentText(cfg.get("type", "No Auth"))
        self.auth_bearer_token.setText(cfg.get("token", ""))
        self.auth_basic_user.setText(cfg.get("username", ""))
        self.auth_basic_pass.setText(cfg.get("password", ""))
        self.auth_apikey_key.setText(cfg.get("key", ""))
        self.auth_apikey_value.setText(cfg.get("value", ""))
        self.auth_apikey_addto.setCurrentText(cfg.get("add_to", "Header"))

    def apply_auth(self, headers: dict, params: dict, env: dict):
        """Apply the configured auth to headers/params (mutated in place), resolving env vars.

        Returns an (username, password) tuple for HTTP Basic auth, or None.
        """
        cfg = self.get_auth_config()
        t = cfg.get("type")
        if t == "Bearer Token":
            token = apply_env(cfg.get("token", "").strip(), env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif t == "Basic Auth":
            user = apply_env(cfg.get("username", ""), env)
            pwd = apply_env(cfg.get("password", ""), env)
            if user or pwd:
                return (user, pwd)
        elif t == "API Key":
            key = apply_env(cfg.get("key", "").strip(), env)
            value = apply_env(cfg.get("value", ""), env)
            if key:
                if cfg.get("add_to") == "Query Params":
                    if hasattr(params, "append"):
                        params.append((key, value))
                    else:
                        params[key] = value
                else:
                    headers[key] = value
        return None

    def _save_response_body_bytes(self):
        if not self._last_response:
            QMessageBox.information(self, "No Response", "Send a request first.")
            return

        resp = self._last_response
        body = self._last_response_bytes or b""

        # Suggest a filename
        suggested = self._infer_filename_from_headers(resp)
        ct = resp.headers.get("Content-Type", "")
        if not suggested:
            # derive from URL or content-type
            parsed = urlparse(getattr(resp.request, "url", "") or self.url_input.text().strip())
            base_name = os.path.basename(parsed.path) or "response"
            ext = os.path.splitext(base_name)[1]
            if not ext:
                ext = self._infer_ext_from_content_type(ct) or ".bin"
            suggested = base_name + ("" if base_name.endswith(ext) else ext)

        fname, _ = QFileDialog.getSaveFileName(self, "Save Response Body", str(Path.home() / suggested), "All Files (*)")
        if not fname:
            return
        try:
            Path(fname).write_bytes(body)
            QMessageBox.information(self, "Saved", f"Saved {len(body):,} bytes to:\n{fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _extract_and_save_base64(self):
        # Try JSON first if it looks like JSON
        text = self.response_raw.toPlainText()
        data_bytes = None
        ext = ""

        # 1) If response is JSON, parse & hunt for base64/data URLs
        parsed_json = None
        try:
            parsed_json = json.loads(text)
        except Exception:
            parsed_json = None

        if parsed_json is not None:
            data_bytes, ext = self._extract_base64_from_json(parsed_json)

        # 2) If not found in JSON, look for data URLs or big base64 strings in raw text
        if data_bytes is None and isinstance(text, str):
            s = text.strip()
            if s.startswith("data:") and ";base64," in s:
                try:
                    header, b64 = s.split(";base64,", 1)
                    mime = header[5:]  # after 'data:'
                    data_bytes = base64.b64decode(b64)
                    ext = self._infer_ext_from_content_type(mime)
                except Exception:
                    pass
            elif self._is_probably_base64(s):
                try:
                    data_bytes = base64.b64decode(s)
                except Exception:
                    pass

        if data_bytes is None:
            QMessageBox.information(self, "Not Found", "No obvious base64/data: URL payload found in this response.")
            return

        # Suggest an extension if unknown (fall back to Content-Type)
        if not ext and self._last_response is not None:
            ext = self._infer_ext_from_content_type(self._last_response.headers.get("Content-Type", "")) or ""

        default_name = "decoded_file" + (ext or "")
        fname, _ = QFileDialog.getSaveFileName(self, "Save Decoded File", str(Path.home() / default_name), "All Files (*)")
        if not fname:
            return
        try:
            Path(fname).write_bytes(data_bytes)
            QMessageBox.information(self, "Saved", f"Saved decoded file ({len(data_bytes):,} bytes) to:\n{fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _export_response_har(self):
        if not self._last_response:
            QMessageBox.information(self, "No Response", "Send a request first.")
            return

        parsed = urlparse(getattr(self._last_response, "url", "") or self.url_input.text().strip())
        host = (parsed.netloc or "request").replace(":", "_")
        suggested = f"curlpro-{host}-{time.strftime('%Y%m%d-%H%M%S')}.har"
        fname, _ = QFileDialog.getSaveFileName(
            self, "Export HAR", str(Path.home() / suggested), "HAR Files (*.har);;JSON Files (*.json)"
        )
        if not fname:
            return
        try:
            har = self._response_to_har(self._last_response)
            Path(fname).write_text(json.dumps(har, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Exported", f"HAR exported to:\n{fname}")
        except Exception as e:
            QMessageBox.critical(self, "HAR Export Error", str(e))

    def _response_to_har(self, response):
        chain = list(getattr(response, "history", []) or []) + [response]
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "CurlPro", "version": "1.0"},
                "pages": [],
                "entries": [self._har_entry_for_response(resp) for resp in chain],
            }
        }

    def _har_entry_for_response(self, response):
        req = getattr(response, "request", None)
        req_url = getattr(req, "url", None) or getattr(response, "url", "")
        parsed = urlparse(req_url)
        req_headers = dict(getattr(req, "headers", {}) or {})
        resp_headers = dict(getattr(response, "headers", {}) or {})
        elapsed_ms = 0
        try:
            elapsed_ms = max(0, int(response.elapsed.total_seconds() * 1000))
        except Exception:
            pass

        started = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(milliseconds=elapsed_ms)
        started_text = started.isoformat().replace("+00:00", "Z")

        request_body = getattr(req, "body", None)
        post_data = None
        body_size = 0
        if request_body is not None:
            if isinstance(request_body, str):
                body_bytes = request_body.encode("utf-8")
                body_text = request_body
                body_encoding = None
            elif isinstance(request_body, bytes):
                body_bytes = request_body
                body_text, body_encoding = self._har_text_from_bytes(
                    body_bytes, req_headers.get("Content-Type", "")
                )
            else:
                try:
                    body_bytes = bytes(request_body)
                except Exception:
                    body_bytes = str(request_body).encode("utf-8")
                body_text, body_encoding = self._har_text_from_bytes(
                    body_bytes, req_headers.get("Content-Type", "")
                )
            body_size = len(body_bytes)
            post_data = {
                "mimeType": req_headers.get("Content-Type", ""),
                "text": body_text,
            }
            if body_encoding:
                post_data["encoding"] = body_encoding

        resp_body = getattr(response, "content", b"") or b""
        content_type = resp_headers.get("Content-Type", "")
        content_text, content_encoding = self._har_text_from_bytes(resp_body, content_type)
        content = {
            "size": len(resp_body),
            "mimeType": content_type,
            "text": content_text,
        }
        if content_encoding:
            content["encoding"] = content_encoding

        request_obj = {
            "method": getattr(req, "method", "GET"),
            "url": req_url,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [{"name": k, "value": str(v)} for k, v in req_headers.items()],
            "queryString": [
                {"name": k, "value": v}
                for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            "headersSize": -1,
            "bodySize": body_size,
        }
        if post_data is not None:
            request_obj["postData"] = post_data

        return {
            "startedDateTime": started_text,
            "time": elapsed_ms,
            "request": request_obj,
            "response": {
                "status": getattr(response, "status_code", 0),
                "statusText": getattr(response, "reason", ""),
                "httpVersion": "HTTP/1.1",
                "cookies": [],
                "headers": [{"name": k, "value": str(v)} for k, v in resp_headers.items()],
                "content": content,
                "redirectURL": resp_headers.get("Location", ""),
                "headersSize": -1,
                "bodySize": len(resp_body),
            },
            "cache": {},
            "timings": {
                "blocked": -1,
                "dns": -1,
                "connect": -1,
                "send": 0,
                "wait": elapsed_ms,
                "receive": 0,
                "ssl": -1,
            },
        }

    def _har_text_from_bytes(self, body: bytes, content_type: str):
        if not body:
            return "", None
        if self._is_binary_content_type(content_type):
            return base64.b64encode(body).decode("ascii"), "base64"
        encoding = "utf-8"
        try:
            ct = content_type or ""
            match = re.search(r"charset=([^;]+)", ct, flags=re.I)
            if match:
                encoding = match.group(1).strip()
            return body.decode(encoding), None
        except Exception:
            return base64.b64encode(body).decode("ascii"), "base64"

    def _build_response_insights(self, response, elapsed):
        req = getattr(response, "request", None)
        headers = response.headers
        content_type = headers.get("Content-Type", "")
        url = getattr(response, "url", "") or getattr(req, "url", "")
        parsed = urlparse(url)
        size = len(getattr(response, "content", b"") or b"")
        elapsed_ms = elapsed * 1000 if elapsed is not None else 0

        lines = [
            "Summary",
            f"  Method: {getattr(req, 'method', '')}",
            f"  URL: {url}",
            f"  Status: {response.status_code} {response.reason}",
            f"  Time: {elapsed_ms:.0f} ms",
            f"  Size: {size:,} bytes",
            f"  Content-Type: {content_type or '(none)'}",
            f"  Encoding: {response.encoding or '(none)'}",
            "",
            "Redirects",
        ]

        history = list(getattr(response, "history", []) or [])
        if history:
            for i, hop in enumerate(history, 1):
                loc = hop.headers.get("Location", "")
                lines.append(f"  {i}. {hop.status_code} {hop.reason} -> {loc or hop.url}")
            lines.append(f"  Final: {response.status_code} {url}")
        else:
            lines.append("  None")

        lines += ["", "Security"]
        security_headers = [
            "Strict-Transport-Security",
            "Content-Security-Policy",
            "X-Frame-Options",
            "X-Content-Type-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ]
        missing = []
        for name in security_headers:
            value = headers.get(name)
            if value:
                lines.append(f"  {name}: {value}")
            else:
                missing.append(name)
        if parsed.scheme == "https" and missing:
            lines.append("  Missing common headers: " + ", ".join(missing))

        cors = headers.get("Access-Control-Allow-Origin")
        if cors:
            lines.append(f"  CORS allow-origin: {cors}")

        lines += ["", "Cookies"]
        set_cookie = headers.get("Set-Cookie")
        if set_cookie:
            cookie_lower = set_cookie.lower()
            flags = []
            for flag in ("secure", "httponly", "samesite"):
                flags.append(f"{flag}={'yes' if flag in cookie_lower else 'no'}")
            lines.append("  Set-Cookie present (" + ", ".join(flags) + ")")
        else:
            lines.append("  No Set-Cookie header")

        lines += ["", "Cache"]
        cache_headers = ["Cache-Control", "ETag", "Last-Modified", "Expires", "Age", "Vary"]
        wrote_cache = False
        for name in cache_headers:
            value = headers.get(name)
            if value:
                lines.append(f"  {name}: {value}")
                wrote_cache = True
        if not wrote_cache:
            lines.append("  No explicit cache headers")

        lines += ["", "Server"]
        for name in ("Server", "Date", "Via", "X-Request-ID", "X-Correlation-ID"):
            value = headers.get(name)
            if value:
                lines.append(f"  {name}: {value}")
        if not any(headers.get(name) for name in ("Server", "Date", "Via", "X-Request-ID", "X-Correlation-ID")):
            lines.append("  No common server metadata headers")

        return "\n".join(lines)

    def init_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def apply_theme(self):
        if self.settings.get("theme") == "dark":
            self.setStyleSheet("QMainWindow { background-color: #2b2b2b; color: #ffffff; }")
        else:
            self.setStyleSheet("""
                QGroupBox { border: 2px solid #ddd; border-radius: 5px; margin-top: 10px; padding-top: 10px; font-weight: bold; }
                QPushButton#send_btn { background-color: #28a745; min-width: 100px; }
            """)
        font = self.font()
        font.setPointSize(self.settings.get("font_size", 10))
        self.setFont(font)
    # ---------- Download helpers ----------
    def _infer_ext_from_content_type(self, ct: str) -> str:
        ct = (ct or "").lower().split(";")[0].strip()
        ext_map = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/svg+xml": ".svg",
            "image/tiff": ".tiff",
            "image/x-icon": ".ico",
            "image/heic": ".heic",
            "video/mp4": ".mp4",
            "video/mpeg": ".mpeg",
            "video/webm": ".webm",
            "video/quicktime": ".mov",
            "video/x-msvideo": ".avi",
            "video/x-matroska": ".mkv",
            "video/x-flv": ".flv",
            "video/x-ms-wmv": ".wmv",
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/flac": ".flac",
            "audio/aac": ".aac",
            "audio/mp4": ".m4a",
            "application/zip": ".zip",
            "application/x-zip-compressed": ".zip",
            "application/x-tar": ".tar",
            "application/gzip": ".gz",
            "application/x-gzip": ".gz",
            "application/x-rar-compressed": ".rar",
            "application/x-7z-compressed": ".7z",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/msword": ".doc",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/octet-stream": ".bin",
            "text/csv": ".csv",
            "text/xml": ".xml",
            "application/xml": ".xml",
        }
        return ext_map.get(ct, "")

    def _infer_filename_from_headers(self, response) -> str:
        # Try Content-Disposition filename
        cd = response.headers.get("Content-Disposition", "")
        if "filename*" in cd:
            # RFC 5987, e.g. filename*=UTF-8''My%20File.pdf
            try:
                part = cd.split("filename*=", 1)[1].split(";")[0].strip()
                if part.lower().startswith("utf-8''"):
                    part = part[7:]
                return unquote(part.strip(' "\'')) or ""
            except Exception:
                pass
        if "filename=" in cd:
            try:
                part = cd.split("filename=", 1)[1].split(";")[0].strip().strip(' "\'')
                return part or ""
            except Exception:
                pass
        return ""

    def _is_probably_base64(self, s: str) -> bool:
        # Heuristic: base64 chars only and length % 4 == 0
        if not s or any(c.isspace() for c in s):
            s = s.strip()
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        if not s or any(c not in allowed for c in s):
            return False
        return (len(s) % 4) == 0

    def _extract_base64_from_json(self, obj):
        """
        Returns (bytes, suggested_ext) if it finds a base64 payload somewhere inside the JSON.
        It checks common keys: data, content, file, base64, blob, value.
        It also supports data URLs: data:<mime>;base64,<payload>
        """
        candidates = []

        def walker(o, key_hint=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    walker(v, k.lower())
            elif isinstance(o, list):
                for v in o:
                    walker(v, key_hint)
            elif isinstance(o, str):
                s = o.strip()
                # data URL?
                if s.startswith("data:") and ";base64," in s:
                    try:
                        header, b64 = s.split(";base64,", 1)
                        mime = header[5:]  # after 'data:'
                        data = base64.b64decode(b64)
                        ext = self._infer_ext_from_content_type(mime)
                        candidates.append((data, ext))
                        return
                    except Exception:
                        pass
                # plain base64 string in common fields
                key_is_common = key_hint in {"data", "content", "file", "base64", "blob", "value", "document", "image", "pdf"}
                if key_is_common and self._is_probably_base64(s):
                    try:
                        data = base64.b64decode(s)
                        # best guess if key mentions pdf/image
                        if "pdf" in key_hint:
                            ext = ".pdf"
                        elif "image" in key_hint or "png" in key_hint or "jpg" in key_hint or "jpeg" in key_hint:
                            ext = ""  # will guess later
                        else:
                            ext = ""
                        candidates.append((data, ext))
                    except Exception:
                        pass

        walker(obj)
        # return first candidate if present
        return candidates[0] if candidates else (None, "")

    def _is_binary_content_type(self, ct: str) -> bool:
        ct = (ct or "").lower().split(";")[0].strip()
        return any(ct.startswith(p) for p in [
            "image/", "video/", "audio/", "application/pdf",
            "application/zip", "application/x-zip", "application/octet-stream",
            "application/vnd.openxmlformats", "application/msword",
            "application/vnd.ms-", "application/x-tar", "application/gzip",
            "application/x-rar", "application/x-7z",
        ])

    def _refresh_progress(self):
        running = sum(1 for s in self._request_store.values() if s['thread'].isRunning())
        self.progress_bar.setVisible(running > 0)
        if running > 0:
            self.progress_bar.setRange(0, 0)

    def _update_active_table(self):
        self.active_requests_table.setRowCount(0)
        for req_id, store in self._request_store.items():
            info = store['info']
            row = self.active_requests_table.rowCount()
            self.active_requests_table.insertRow(row)

            id_item = QTableWidgetItem(str(req_id + 1))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_requests_table.setItem(row, 0, id_item)

            method_item = QTableWidgetItem(info['method'])
            method_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_requests_table.setItem(row, 1, method_item)

            url_item = QTableWidgetItem(info['url'])
            url_item.setData(Qt.ItemDataRole.UserRole, req_id)
            self.active_requests_table.setItem(row, 2, url_item)

            if store['result'] is not None:
                status = store['result']['response'].status_code
                status_item = QTableWidgetItem(str(status))
                status_item.setForeground(QColor('#28a745' if status < 400 else '#dc3545'))
            elif store['error'] is not None:
                status_item = QTableWidgetItem('Error')
                status_item.setForeground(QColor('#dc3545'))
            else:
                status_item = QTableWidgetItem('Running...')
                status_item.setForeground(QColor('#fd7e14'))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_requests_table.setItem(row, 3, status_item)

            elapsed = store['elapsed']
            time_item = QTableWidgetItem(f"{elapsed:.0f}" if elapsed is not None else '...')
            time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_requests_table.setItem(row, 4, time_item)

        self.active_requests_table.scrollToBottom()

    def _on_active_request_double_clicked(self, row, col):
        url_item = self.active_requests_table.item(row, 2)
        if url_item is None:
            return
        req_id = url_item.data(Qt.ItemDataRole.UserRole)
        store = self._request_store.get(req_id)
        if store is None:
            return
        if store['result'] is not None:
            self._load_response_to_ui(store['result'])
        elif store['error'] is not None:
            QMessageBox.critical(self, "Request Error", f"Request #{req_id + 1} failed:\n{store['error']}")
        else:
            QMessageBox.information(self, "In Progress", f"Request #{req_id + 1} is still running.")

    def _load_response_to_ui(self, result):
        response = result['response']
        elapsed = result['elapsed']
        status_code = response.status_code
        status_text = response.reason
        size = len(response.content)

        self._last_response = response
        self._last_response_bytes = response.content or b""

        self.response_summary.setText(
            f"Status: {status_code} {status_text} — Time: {elapsed*1000:.0f} ms — Size: {size:,} bytes"
        )

        self.response_headers.setPlainText(
            "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        )
        self.response_insights.setPlainText(self._build_response_insights(response, elapsed))
        raw_text = response.text
        self.response_raw.setPlainText(raw_text)

        content_type = response.headers.get("Content-Type", "").lower()
        image_types = ["image/png", "image/jpeg", "image/jpg", "image/webp",
                       "image/gif", "image/bmp", "image/tiff"]

        if any(x in content_type for x in image_types):
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            if not pixmap.isNull():
                w = max(self.response_preview_label.width(), 400)
                h = max(self.response_preview_label.height(), 400)
                scaled = pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                self.response_preview_label.setPixmap(scaled)
                self.response_tabs.setCurrentWidget(self.response_preview_scroll)
            else:
                self.response_preview_label.clear()
                self.response_preview_label.setText("Could not decode image")
        else:
            self.response_preview_label.clear()
            self.response_preview_label.setText("No preview available")

        if self._is_binary_content_type(content_type) and not any(x in content_type for x in image_types):
            ext = self._infer_ext_from_content_type(content_type) or ".bin"
            self.response_pretty.setPlainText(
                f"[Binary content: {content_type}]\n"
                f"Size: {size:,} bytes\n\n"
                f"Use 'Save Response Body…' to save this file.\n"
                f"Suggested extension: {ext}"
            )
        elif "application/json" in content_type or raw_text.strip().startswith(("{", "[")):
            try:
                if self.settings.get("auto_format_json", True):
                    self.response_pretty.setPlainText(json.dumps(response.json(), indent=2))
                else:
                    self.response_pretty.setPlainText(raw_text)
            except Exception:
                self.response_pretty.setPlainText(raw_text)
        else:
            self.response_pretty.setPlainText(raw_text)

    # ---------- Collections & History ----------
    def reload_collections(self):
        self.collections_tree.clear()
        for coll_name, items in self.collections.items():
            coll_item = QTreeWidgetItem([f"{coll_name} ({len(items)} requests)"])
            coll_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "name": coll_name})
            for i, req in enumerate(items):
                req_name = req.get("name", f"Request {i+1}")
                method = req.get("method", "GET")
                req_item = QTreeWidgetItem([f"{method} {req_name}"])
                req_item.setToolTip(0, req.get("url", "")[:200])
                req_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "request", "collection": coll_name, "index": i})
                coll_item.addChild(req_item)
            self.collections_tree.addTopLevelItem(coll_item)
        self.collections_tree.expandAll()

    def reload_history(self):
        if not hasattr(self, "history_tree"):
            return
        self.history_tree.clear()

        search = self.history_search.text().strip().lower() if hasattr(self, "history_search") else ""
        method_filter = self.history_method_filter.currentText() if hasattr(self, "history_method_filter") else "All"

        today = time.strftime('%Y-%m-%d', time.localtime())
        yesterday = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))

        groups = {}  # label -> QTreeWidgetItem
        shown = 0

        # newest first
        for entry in reversed(self.history[-2000:]):
            url = entry.get('url', '') or ''
            method = entry.get('method', 'GET') or 'GET'
            status = entry.get('status', '')

            if method_filter != "All" and method != method_filter:
                continue
            if search:
                hay = f"{url} {method} {status} {entry.get('reason', '')}".lower()
                if search not in hay:
                    continue

            ts = entry.get('timestamp', 0)
            day = time.strftime('%Y-%m-%d', time.localtime(ts))
            if day == today:
                label = "Today"
            elif day == yesterday:
                label = "Yesterday"
            else:
                label = day

            group = groups.get(label)
            if group is None:
                group = QTreeWidgetItem([label])
                group.setData(0, Qt.ItemDataRole.UserRole, {"group": True})
                gf = group.font(0)
                gf.setBold(True)
                group.setFont(0, gf)
                group.setFirstColumnSpanned(True)
                self.history_tree.addTopLevelItem(group)
                groups[label] = group

            time_str = time.strftime('%H:%M:%S', time.localtime(ts))
            url_disp = url[:55] + ("…" if len(url) > 55 else "")
            child = QTreeWidgetItem([f"{time_str}   {method}   {status}   {url_disp}"])
            child.setData(0, Qt.ItemDataRole.UserRole, entry)
            child.setToolTip(
                0,
                f"{method} {url}\n"
                f"Status: {status} {entry.get('reason', '')}\n"
                f"Time: {entry.get('duration_ms', 0)} ms   Size: {entry.get('size', 0):,} bytes"
            )
            child.setForeground(0, self._status_color(status))
            group.addChild(child)
            shown += 1

        self.history_tree.expandAll()
        self.history_count_label.setText(f"{shown} request{'s' if shown != 1 else ''}")

    def _status_color(self, status):
        try:
            code = int(status)
        except (TypeError, ValueError):
            return QColor("#d9534f")  # network/other error
        if 200 <= code < 300:
            return QColor("#28a745")
        if 300 <= code < 400:
            return QColor("#17a2b8")
        if 400 <= code < 500:
            return QColor("#f0ad4e")
        return QColor("#d9534f")

    # ---------- Attachments ----------
    def add_attachment_file(self):
        fname, _ = QFileDialog.getOpenFileName(
            self, "Choose Image or File",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All Files (*)"
        )
        if not fname:
            return
        field = (self.field_name_input.text().strip() or "file")
        mime, _ = mimetypes.guess_type(fname)
        if not mime:
            mime = "application/octet-stream"
        item_label = f"{field}  →  {fname}  ({mime})"
        self.attachments_list.addItem(item_label)
        self.file_attachments.append({
            "field": field,
            "path": fname,
            "filename": os.path.basename(fname),
            "mime": mime
        })

    def remove_selected_attachment(self):
        row = self.attachments_list.currentRow()
        if row < 0:
            return
        self.attachments_list.takeItem(row)
        try:
            del self.file_attachments[row]
        except Exception:
            pass

    def clear_attachments(self):
        """Drop all attached files from both the model and the visible list."""
        self.file_attachments = []
        if hasattr(self, "attachments_list"):
            self.attachments_list.clear()

    def restore_attachments(self, attachments):
        """Replace the current attachments with a saved list of metadata dicts.

        Each item is {field, path, filename, mime}. Missing files are kept so the
        user can see what was attached; a marker is shown if the path is gone.
        """
        self.clear_attachments()
        for att in attachments or []:
            if not isinstance(att, dict):
                continue
            field = att.get("field") or "file"
            path = att.get("path") or ""
            filename = att.get("filename") or (os.path.basename(path) if path else "")
            mime = att.get("mime") or "application/octet-stream"
            missing = "" if (path and os.path.exists(path)) else "  [missing]"
            self.file_attachments.append({
                "field": field,
                "path": path,
                "filename": filename,
                "mime": mime,
            })
            self.attachments_list.addItem(f"{field}  →  {path}  ({mime}){missing}")

    def _attachments_snapshot(self):
        """Return a serializable copy of the current attachments."""
        return [dict(att) for att in self.file_attachments]

    # ---------- Events ----------
    def on_body_type_changed(self, text):
        if text == "JSON":
            self.body_text.setPlaceholderText('{\n  "key": "value"\n}')
        elif text == "Form Data":
            self.body_text.setPlaceholderText("key=value&other=one  (for text fields)\nUse Attachments below for files.")
        elif text == "Binary":
            self.body_text.setPlaceholderText("(Binary payload - use Save/Load to manipulate file)")
        else:
            self.body_text.setPlaceholderText('{\n  "key": "value",\n  "user": "{{USERNAME}}"\n}')

        # show attachments only for Form Data
        show_attachments = (text == "Form Data")
        if hasattr(self, "attachments_group"):
            self.attachments_group.setVisible(show_attachments)

    def _update_method_color(self, *_args):
        """Tint the method dropdown so the request type is obvious at a glance."""
        colors = {
            "GET": "#28a745", "POST": "#fd7e14", "PUT": "#0d6efd",
            "PATCH": "#6f42c1", "DELETE": "#dc3545", "HEAD": "#6c757d",
            "OPTIONS": "#6c757d",
        }
        method = self.method_combo.currentText()
        color = colors.get(method, "#6c757d")
        self.method_combo.setStyleSheet(f"QComboBox {{ color: {color}; font-weight: bold; }}")

    def import_curl(self, text):
        """Populate the request form from a pasted curl command."""
        parsed = parse_curl_command(text)
        if not parsed.get("url"):
            # Couldn't find a URL — fall back to inserting the raw text so the
            # user isn't left with an empty field.
            self.url_input.setText(" ".join(text.split()))
            self.status_bar.showMessage("Couldn't parse curl command — pasted as text")
            return False

        headers = dict(parsed["headers"])

        # Method: explicit -X wins, otherwise infer from the presence of a body.
        method = parsed["method"]
        if not method:
            if parsed["get_with_data"]:
                method = "GET"
            elif parsed["data"] or parsed["is_form"]:
                method = "POST"
            else:
                method = "GET"
        valid_methods = [self.method_combo.itemText(i) for i in range(self.method_combo.count())]
        self.method_combo.setCurrentText(method if method in valid_methods else "GET")

        self._clear_params()
        url_query_pairs = parse_qsl(urlparse(parsed["url"]).query, keep_blank_values=True)
        imported_param_count = len(url_query_pairs)
        if url_query_pairs:
            self.url_input.setText(strip_query_from_url(parsed["url"]))
            for key, value in url_query_pairs:
                self._add_param_row(key, value, True)
        else:
            self.url_input.setText(parsed["url"])
        if parsed["get_with_data"] and parsed["data"]:
            for key, value in parse_qsl(parsed["data"], keep_blank_values=True):
                self._add_param_row(key, value, True)
                imported_param_count += 1

        # Lift recognised auth out of the headers into the Auth tab.
        auth_cfg = {"type": "No Auth"}
        auth_header = next((k for k in headers if k.lower() == "authorization"), None)
        if auth_header:
            val = headers[auth_header].strip()
            if val.lower().startswith("bearer "):
                auth_cfg = {"type": "Bearer Token", "token": val[7:].strip()}
                del headers[auth_header]
        if parsed["user"]:
            user, _, pwd = parsed["user"].partition(":")
            auth_cfg = {"type": "Basic Auth", "username": user, "password": pwd}
        self.set_auth_config(auth_cfg)

        # Headers text — one "Key: Value" per line.
        self.headers_text.setPlainText(
            "\n".join(f"{k}: {v}" for k, v in headers.items())
        )

        # Body.
        content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "").lower()
        if parsed["is_form"]:
            self.body_type_combo.setCurrentText("Form Data")
            text_fields = [f for f in parsed["form"] if "=@" not in f and not f.startswith("@")]
            self.body_text.setPlainText("&".join(text_fields))
            file_fields = [f for f in parsed["form"] if f not in text_fields]
            note = "  (file fields ignored — add them under Attachments)" if file_fields else ""
            self.status_bar.showMessage(f"Imported curl — {method} {parsed['url'][:60]}{note}")
        elif parsed["data"] and not parsed["get_with_data"]:
            body = parsed["data"]
            is_json = "application/json" in content_type or body.lstrip().startswith(("{", "["))
            if is_json:
                self.body_type_combo.setCurrentText("JSON")
                try:
                    body = json.dumps(json.loads(body), indent=2)
                except (json.JSONDecodeError, ValueError):
                    pass
            else:
                self.body_type_combo.setCurrentText("Raw")
            self.body_text.setPlainText(body)
            self.status_bar.showMessage(f"Imported curl — {method} {parsed['url'][:60]}")
        else:
            self.body_text.setPlainText("")
            suffix = (
                f" with {imported_param_count} param{'s' if imported_param_count != 1 else ''}"
                if imported_param_count else ""
            )
            self.status_bar.showMessage(f"Imported curl — {method} {parsed['url'][:60]}{suffix}")

        self._update_method_color()
        return True

    def send_request(self):
        if looks_like_curl(self.url_input.text()):
            if not self.import_curl(self.url_input.text()):
                QMessageBox.warning(
                    self,
                    "Invalid cURL",
                    "That looks like a cURL command, but CurlPro couldn't find a URL in it.",
                )
                return

        env_name = self.env_combo.currentText()
        env = self.envs.get(env_name, {})

        method = self.method_combo.currentText()
        url = apply_env(self.url_input.text().strip(), env)
        if not url:
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid URL.")
            return

        headers = {}
        for line in self.headers_text.toPlainText().splitlines():
            line = line.strip()
            if line and ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = apply_env(value.strip(), env)

        body_raw = apply_env(self.body_text.toPlainText().strip(), env)
        json_body = None
        data = None
        files = None

        content_type = headers.get("Content-Type", "").lower()
        body_type = self.body_type_combo.currentText()

        if body_type == "JSON" or "application/json" in content_type or (body_raw and body_raw.startswith(("{", "["))):
            if body_raw:
                try:
                    json_body = json.loads(body_raw)
                    if "Content-Type" not in headers:
                        headers["Content-Type"] = "application/json"
                except json.JSONDecodeError as e:
                    QMessageBox.warning(self, "Invalid JSON", f"JSON parsing error: {str(e)}")
                    return

        elif body_type == "Form Data":
            # Parse text fields (key=value&key2=two)
            data = {}
            if body_raw:
                for pair in body_raw.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        data[k] = v

            # Build files from attachments if any
            if self.file_attachments:
                files = []
                # remove explicit Content-Type so requests can set boundary
                for hk in list(headers.keys()):
                    if hk.lower() == "content-type":
                        del headers[hk]
                try:
                    for att in self.file_attachments:
                        fp = open(att["path"], "rb")
                        files.append((att["field"], (att["filename"], fp, att["mime"])))
                except Exception as e:
                    QMessageBox.critical(self, "File Error", f"Failed to open attachment: {e}")
                    for f in files or []:
                        try:
                            if hasattr(f[1][1], "close"):
                                f[1][1].close()
                        except Exception:
                            pass
                    return

        elif body_type == "Binary":
            data = body_raw.encode("utf-8") if body_raw else None
        else:
            # Raw
            data = body_raw.encode("utf-8") if body_raw else None

        # Apply query params and authentication (auth mutates headers/params).
        params = self._resolved_params(env)
        auth_tuple = self.apply_auth(headers, params, env)
        advanced = self._advanced_snapshot()

        # Snapshot current UI inputs so history is accurate if the user edits
        # the form while this request is in flight (multiple concurrent requests).
        snapshot = {
            'req_url': self.url_input.text(),
            'req_headers_text': self.headers_text.toPlainText(),
            'params': self._params_snapshot(),
            'body_type': self.body_type_combo.currentText(),
            'request_body': self.body_text.toPlainText(),
            'auth': self.get_auth_config(),
            'advanced': dict(advanced),
            'attachments': self._attachments_snapshot(),
        }

        req_id = self._req_counter
        self._req_counter += 1

        raw_timeout = self.timeout_spin.value()
        timeout = None if raw_timeout == 0 else raw_timeout
        thread = RequestThread(
            method, url, headers, json_body, data, files, timeout,
            params=params or None, auth=auth_tuple,
            allow_redirects=advanced.get("follow_redirects", True),
            verify_ssl=advanced.get("verify_ssl", True),
            max_redirects=advanced.get("max_redirects", 30),
            retry_total=advanced.get("retry_total", 0),
            retry_backoff=advanced.get("retry_backoff", 0.0),
            retry_statuses=parse_status_code_list(advanced.get("retry_statuses", "")),
        )
        self._request_store[req_id] = {
            'thread': thread,
            'info': {'method': method, 'url': url},
            'snapshot': snapshot,
            'result': None,
            'error': None,
            'elapsed': None,
        }
        thread.finished.connect(lambda result, rid=req_id: self.on_request_finished(result, rid))
        thread.error.connect(lambda err, rid=req_id: self.on_request_error(err, rid))
        thread.start()

        self._update_active_table()
        self._refresh_progress()
        self.status_bar.showMessage(f"Request #{req_id + 1} sent — {method} {url[:60]}")

    def on_request_finished(self, result, req_id):
        store = self._request_store.get(req_id, {})
        if store:
            store['result'] = result
            store['elapsed'] = result['elapsed'] * 1000

        self._update_active_table()
        self._refresh_progress()

        response = result['response']
        elapsed = result['elapsed']
        snapshot = store.get('snapshot', {}) if store else {}

        self._load_response_to_ui(result)

        # Save to history
        try:
            raw_text = response.text
            req_obj = getattr(response, "request", None)
            entry = {
                "timestamp": time.time(),
                "method": req_obj.method if req_obj is not None and hasattr(req_obj, "method") else self.method_combo.currentText(),
                "url": req_obj.url if req_obj is not None and hasattr(req_obj, "url") else self.url_input.text(),
                "status": response.status_code,
                "reason": response.reason,
                "duration_ms": int(elapsed * 1000),
                "size": len(response.content),
                "request_headers": dict(getattr(req_obj, "headers", {}) if req_obj is not None else {}),
                "response_headers": dict(response.headers),
                "request_body": snapshot.get('request_body', self.body_text.toPlainText()),
                "response_body_snippet": raw_text[:2000],
                "response_body_full": raw_text,
                "req_url": snapshot.get('req_url', self.url_input.text()),
                "req_headers_text": snapshot.get('req_headers_text', self.headers_text.toPlainText()),
                "params": snapshot.get('params', self._params_snapshot()),
                "body_type": snapshot.get('body_type', self.body_type_combo.currentText()),
                "auth": snapshot.get('auth', self.get_auth_config()),
                "advanced": snapshot.get('advanced', self._advanced_snapshot()),
                "attachments": snapshot.get('attachments', self._attachments_snapshot()),
            }
            entry["_id"] = add_history(entry)
            self.history.append(entry)
            self.reload_history()
        except Exception:
            pass

        status_code = response.status_code
        self.status_bar.showMessage(f"Request #{req_id + 1} done: {status_code} ({elapsed*1000:.0f} ms)")

        # Offer to save non-image binary (images show in the Preview tab)
        ct = (response.headers.get("Content-Type", "") or "").lower()
        if self._is_binary_content_type(ct) and "image/" not in ct:
            choice = QMessageBox.question(
                self,
                "Binary Response",
                f"Response is binary content ({ct}, {len(response.content):,} bytes).\nSave it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._save_response_body_bytes()

    def on_request_error(self, error_message, req_id):
        store = self._request_store.get(req_id, {})
        if store:
            store['error'] = error_message
            store['elapsed'] = 0

        self._update_active_table()
        self._refresh_progress()

        snapshot = store.get('snapshot', {}) if store else {}
        self.status_bar.showMessage(f"Request #{req_id + 1} failed: {error_message[:60]}")
        QMessageBox.critical(self, "Request Error", f"Request #{req_id + 1} failed:\n{error_message}")

        try:
            entry = {
                "timestamp": time.time(),
                "method": self.method_combo.currentText(),
                "url": self.url_input.text(),
                "status": 0,
                "reason": str(error_message),
                "duration_ms": 0,
                "size": 0,
                "request_headers": {},
                "response_headers": {},
                "request_body": snapshot.get('request_body', self.body_text.toPlainText()),
                "response_body_snippet": "",
                "response_body_full": "",
                "req_url": snapshot.get('req_url', self.url_input.text()),
                "req_headers_text": snapshot.get('req_headers_text', self.headers_text.toPlainText()),
                "params": snapshot.get('params', self._params_snapshot()),
                "body_type": snapshot.get('body_type', self.body_type_combo.currentText()),
                "auth": snapshot.get('auth', self.get_auth_config()),
                "advanced": snapshot.get('advanced', self._advanced_snapshot()),
                "attachments": snapshot.get('attachments', self._attachments_snapshot()),
            }
            entry["_id"] = add_history(entry)
            self.history.append(entry)
            self.reload_history()
        except Exception:
            pass

    # ---------- Save/Load ----------
    def save_to_collection(self):
        dlg = SaveToCollectionDialog(sorted(self.collections.keys()), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.collection_name()
        if not name:
            return
        coll = self.collections.setdefault(name, [])
        req = {
            "name": f"{self.method_combo.currentText()} {self.url_input.text()}",
            "method": self.method_combo.currentText(),
            "url": self.url_input.text(),
            "headers": self.headers_text.toPlainText(),
            "params": self._params_snapshot(),
            "body_type": self.body_type_combo.currentText(),
            "body": self.body_text.toPlainText(),
            "auth": self.get_auth_config(),
            "advanced": self._advanced_snapshot(),
            "attachments": self._attachments_snapshot(),
        }
        coll.append(req)
        save_document("collections", self.collections)
        self.reload_collections()
        QMessageBox.information(self, "Saved", f"Request saved to collection '{name}'")

    def save_request_file(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save Request", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        req = {
            "method": self.method_combo.currentText(),
            "url": self.url_input.text(),
            "headers": self.headers_text.toPlainText(),
            "params": self._params_snapshot(),
            "body_type": self.body_type_combo.currentText(),
            "body": self.body_text.toPlainText(),
            "auth": self.get_auth_config(),
            "advanced": self._advanced_snapshot(),
            "attachments": self._attachments_snapshot(),
        }
        try:
            Path(fname).write_text(json.dumps(req, indent=2))
            QMessageBox.information(self, "Saved", f"Request saved to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def load_request_file(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Load Request", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        try:
            data = json.loads(Path(fname).read_text())
            self.method_combo.setCurrentText(data.get("method", "GET"))
            self.url_input.setText(data.get("url", ""))
            self.headers_text.setPlainText(data.get("headers", ""))
            self._restore_params(data.get("params", []))
            self.body_type_combo.setCurrentText(data.get("body_type", "Raw"))
            self.body_text.setPlainText(data.get("body", ""))
            self.restore_attachments(data.get("attachments", []))
            self.set_auth_config(data.get("auth", {}))
            self._restore_advanced(data.get("advanced", {}))
            QMessageBox.information(self, "Loaded", f"Request loaded from {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def manage_environments(self):
        dlg = EnvironmentDialog(self.envs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.envs = dlg.envs
            save_document("envs", self.envs)
            self.env_combo.clear()
            self.env_combo.addItems(list(self.envs.keys()))
            QMessageBox.information(self, "Environments", "Environments saved.")

    def format_json_body(self):
        text = self.body_text.toPlainText().strip()
        if not text:
            return
        try:
            parsed = json.loads(text)
            pretty = json.dumps(parsed, indent=2)
            self.body_text.setPlainText(pretty)
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "Invalid JSON", f"Cannot format JSON: {e}")

    def load_collection_item(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data.get("type") == "request":
            coll = data.get("collection")
            idx = data.get("index")
            try:
                req = self.collections[coll][idx]
                self.method_combo.setCurrentText(req.get("method", "GET"))
                self.url_input.setText(req.get("url", ""))
                self.headers_text.setPlainText(req.get("headers", ""))
                self._restore_params(req.get("params", []))
                self.body_type_combo.setCurrentText(req.get("body_type", "Raw"))
                self.body_text.setPlainText(req.get("body", ""))
                self.restore_attachments(req.get("attachments", []))
                self.set_auth_config(req.get("auth", {}))
                self._restore_advanced(req.get("advanced", {}))
                QMessageBox.information(self, "Loaded", f"Loaded request from collection '{coll}'")
            except Exception as e:
                QMessageBox.critical(self, "Load Error", str(e))

    def load_history_item(self, item, _col=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("group"):
            return
        self.method_combo.setCurrentText(data.get("method", "GET"))
        # Prefer the original (unresolved) URL the user typed; fall back to the sent URL
        self.url_input.setText(data.get("req_url") or data.get("url", ""))

        # Restore headers: prefer the original headers text the user typed,
        # otherwise reconstruct from the headers that were actually sent.
        if "req_headers_text" in data:
            self.headers_text.setPlainText(data.get("req_headers_text") or "")
        elif data.get("request_headers"):
            try:
                hdrs = "\n".join(f"{k}: {v}" for k, v in data.get("request_headers", {}).items())
                self.headers_text.setPlainText(hdrs)
            except Exception:
                pass
        else:
            self.headers_text.clear()

        self._restore_params(data.get("params", []))

        # Restore body type before body so the right placeholder/attachments show
        if data.get("body_type"):
            self.body_type_combo.setCurrentText(data.get("body_type"))
        self.body_text.setPlainText(data.get("request_body", ""))

        # Restore attachments (multipart/form-data files)
        self.restore_attachments(data.get("attachments", []))

        # Restore auth configuration
        if "auth" in data:
            self.set_auth_config(data.get("auth", {}))
        if "advanced" in data:
            self._restore_advanced(data.get("advanced", {}))

        self.status_bar.showMessage(f"Loaded from history: {data.get('method', '')} {data.get('url', '')}")

    # ---------- History: advanced actions ----------
    def show_history_context_menu(self, pos):
        item = self.history_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("group"):
            return

        menu = QMenu(self)
        menu.addAction("Load into Request", lambda: self.load_history_item(item))
        menu.addAction("Load && Resend", lambda: self._history_resend(item))
        menu.addSeparator()
        menu.addAction("Copy URL", lambda: self._history_copy_url(data))
        menu.addAction("Copy as cURL", lambda: self._history_copy_curl(data))
        menu.addAction("Copy Response Body", lambda: self._history_copy_response(data))
        menu.addAction("View Response Body…", lambda: self._history_view_response(data))
        menu.addSeparator()
        menu.addAction("Delete Entry", lambda: self.delete_history_entry(data))
        menu.exec(self.history_tree.viewport().mapToGlobal(pos))

    def delete_selected_history(self):
        item = self.history_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("group"):
            return
        self.delete_history_entry(data)

    def delete_history_entry(self, entry):
        # Match by object identity so duplicates aren't accidentally removed
        for i, e in enumerate(self.history):
            if e is entry:
                del self.history[i]
                break
        else:
            return
        delete_history(entry)
        self.reload_history()
        self.status_bar.showMessage("History entry deleted")

    def _history_resend(self, item):
        self.load_history_item(item)
        self.send_request()

    def _history_copy_url(self, data):
        QApplication.clipboard().setText(data.get("url", "") or "")
        self.status_bar.showMessage("URL copied to clipboard")

    def _history_copy_curl(self, data):
        method = (data.get("method", "GET") or "GET").upper()
        url = data.get("url", "") or ""
        headers = data.get("request_headers", {}) or {}
        body = data.get("request_body", "") or ""

        parts = ["curl", "-i"]
        if method != "GET":
            parts += ["-X", method]
        for k, v in headers.items():
            parts += ["-H", shlex.quote(f"{k}: {v}")]
        if body:
            parts += ["--data-raw", shlex.quote(body)]
        parts.append(shlex.quote(url))
        QApplication.clipboard().setText(" ".join(parts))
        self.status_bar.showMessage("Copied request as cURL")

    def _history_copy_response(self, data):
        body = data.get("response_body_full") or data.get("response_body_snippet", "") or ""
        QApplication.clipboard().setText(body)
        self.status_bar.showMessage("Response body copied to clipboard")

    def _history_view_response(self, data):
        body = data.get("response_body_full") or data.get("response_body_snippet", "") or "(no response body stored)"
        title = f"Response {data.get('status', '')} — {(data.get('url', '') or '')[:60]}"
        dlg = SnippetDialog(title, body, self)
        dlg.exec()

    def create_collection(self):
        name, ok = QInputDialog.getText(self, "Create Collection", "Collection name:")
        if not ok or not name:
            return
        if name in self.collections:
            QMessageBox.warning(self, "Exists", "A collection with that name already exists.")
            return
        self.collections[name] = []
        save_document("collections", self.collections)
        self.reload_collections()
        QMessageBox.information(self, "Created", f"Collection '{name}' created.")

    def import_collection(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Import Collection", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        try:
            data = json.loads(Path(fname).read_text())
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in self.collections and isinstance(v, list):
                        self.collections[k].extend(v)
                    else:
                        self.collections[k] = v
            elif isinstance(data, list):
                name = Path(fname).stem
                self.collections.setdefault(name, []).extend(data)
            save_document("collections", self.collections)
            self.reload_collections()
            QMessageBox.information(self, "Imported", f"Imported collections from {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def export_collection(self):
        selected = self.collections_tree.currentItem()
        data_to_export = self.collections
        suggested_name = "collections"
        if selected:
            meta = selected.data(0, Qt.ItemDataRole.UserRole)
            if meta and meta.get("type") == "collection":
                coll_name = meta.get("name")
                data_to_export = {coll_name: self.collections.get(coll_name, [])}
                suggested_name = coll_name
        fname, _ = QFileDialog.getSaveFileName(self, "Export Collection", str(Path.home() / f"{suggested_name}.json"), "JSON Files (*.json)")
        if not fname:
            return
        try:
            Path(fname).write_text(json.dumps(data_to_export, indent=2))
            QMessageBox.information(self, "Exported", f"Exported collections to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def clear_history(self):
        reply = QMessageBox.question(self, "Clear History", "Clear all request history?")
        if reply == QMessageBox.StandardButton.Yes:
            self.history = []
            clear_history_db()
            self.reload_history()

    # ---------- Snippets ----------
    # Headers the HTTP client computes itself. Emitting them by hand breaks the
    # generated code — e.g. PowerShell's Invoke-RestMethod throws if you set
    # Content-Length/Host/Connection via -Headers, and curl recomputes them.
    _AUTO_HEADERS = {"content-length", "host", "connection"}

    def _snippet_headers(self, headers: dict, drop_content_type: bool = False) -> dict:
        """Strip auto-managed headers (and Content-Type for multipart) so the
        generated snippet doesn't fight the HTTP client."""
        out = {}
        for k, v in headers.items():
            kl = k.lower()
            if kl in self._AUTO_HEADERS:
                continue
            if drop_content_type and kl == "content-type":
                continue
            out[k] = v
        return out

    def _form_text_fields(self, body_raw: str) -> dict:
        """Parse `key=value&key2=two` text fields from a Form Data body."""
        fields = {}
        if body_raw and self.body_type_combo.currentText() == "Form Data":
            for pair in body_raw.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    fields[k] = v
        return fields

    def _snippet_context(self):
        """Resolve the current request (env vars + auth) into the inputs every
        generator needs: (method, url, headers, body_raw, attachments)."""
        env_name = self.env_combo.currentText()
        env = self.envs.get(env_name, {})
        method = self.method_combo.currentText()
        url = apply_env(self.url_input.text().strip(), env)
        headers = {}
        for line in self.headers_text.toPlainText().splitlines():
            line = line.strip()
            if line and ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = apply_env(value.strip(), env)
        body_raw = apply_env(self.body_text.toPlainText().strip(), env)

        # Fold query params and authentication into the snippet's URL/headers.
        params = self._resolved_params(env)
        auth_tuple = self.apply_auth(headers, params, env)
        if auth_tuple:
            user, pwd = auth_tuple
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        if params:
            url = add_params_to_url(url, params)

        attachments = list(self.file_attachments) if hasattr(self, "file_attachments") else []
        return method, url, headers, body_raw, attachments

    def _build_snippet(self, fmt: str, ctx) -> str:
        method, url, headers, body_raw, attachments = ctx
        if fmt == "curl":
            return self.generate_curl(method, url, headers, body_raw, attachments)
        if fmt == "python-requests":
            return self.generate_python_requests(method, url, headers, body_raw, attachments)
        if fmt == "powershell":
            return self.generate_powershell(method, url, headers, body_raw, attachments)
        if fmt == "java":
            return self.generate_java(method, url, headers, body_raw, attachments)
        if fmt == "axios":
            return self.generate_axios(method, url, headers, body_raw, attachments)
        return f"# Unknown format: {fmt}"

    def generate_code_and_show(self, fmt: str):
        titles = {
            "curl": "cURL Command",
            "python-requests": "Python requests",
            "powershell": "PowerShell (Invoke-RestMethod)",
            "java": "Java (HttpClient)",
            "axios": "Axios (JavaScript)",
        }
        try:
            snippet = self._build_snippet(fmt, self._snippet_context())
            dlg = SnippetDialog(titles.get(fmt, "Snippet"), snippet, self)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Generate Error", str(e))

    def generate_all_and_show(self):
        try:
            ctx = self._snippet_context()
            snippets = {
                "curl": self._build_snippet("curl", ctx),
                "PowerShell": self._build_snippet("powershell", ctx),
                "Python": self._build_snippet("python-requests", ctx),
                "Java": self._build_snippet("java", ctx),
                "Axios": self._build_snippet("axios", ctx),
            }
            MultiSnippetDialog(snippets, self).exec()
        except Exception as e:
            QMessageBox.critical(self, "Generate Error", str(e))

    def generate_curl(self, method, url, headers, body_raw, attachments):
        method = method.upper()
        # Use curl.exe, not bare `curl`: in PowerShell `curl` is an alias for
        # Invoke-WebRequest, which rejects -H/-F (the "Cannot bind parameter
        # 'Headers'" error). curl.exe runs the real curl on Windows/macOS/Linux.
        parts = ["curl.exe", "-i"]
        if method != "GET":
            parts += ["-X", method]
        # headers (Content-Type dropped for multipart so curl sets the boundary)
        for k, v in self._snippet_headers(headers, drop_content_type=bool(attachments)).items():
            parts += ["-H", shlex.quote(f"{k}: {v}")]

        if attachments:
            for k, v in self._form_text_fields(body_raw).items():
                parts += ["-F", shlex.quote(f"{k}={v}")]
            for att in attachments:
                mime = att.get("mime") or "application/octet-stream"
                parts += ["-F", shlex.quote(f"{att['field']}=@{att['path']};type={mime}")]
        else:
            if body_raw:
                parts += ["--data-raw", shlex.quote(body_raw)]

        parts += [shlex.quote(url)]
        return " ".join(parts)

    def generate_python_requests(self, method, url, headers, body_raw, attachments):
        lines = []
        lines.append("import requests")
        lines.append("")

        hdrs = self._snippet_headers(headers, drop_content_type=bool(attachments))
        if hdrs:
            lines.append("headers = {")
            for k, v in hdrs.items():
                lines.append(f"    {json.dumps(k)}: {json.dumps(v)},")
            lines.append("}")
        else:
            lines.append("headers = {}")
        lines.append("")

        if attachments:
            text_fields = self._form_text_fields(body_raw)
            if text_fields:
                lines.append("data = {")
                for k, v in text_fields.items():
                    lines.append(f"    {json.dumps(k)}: {json.dumps(v)},")
                lines.append("}")
            else:
                lines.append("data = {}")
            lines.append("")
            lines.append("# Files: (field, (filename, fileobj, content_type))")
            lines.append("files = {")
            for att in attachments:
                lines.append(
                    f"    {json.dumps(att['field'])}: ({json.dumps(att['filename'])}, open({json.dumps(att['path'])}, 'rb'), {json.dumps(att['mime'])}),"
                )
            lines.append("}")
            lines.append("")
            lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers, data=data, files=files)")
        else:
            body_is_json = False
            parsed_json = None
            if body_raw:
                try:
                    parsed_json = json.loads(body_raw)
                    body_is_json = True
                except Exception:
                    body_is_json = False

            if body_is_json:
                lines.append("json_payload = " + json.dumps(parsed_json, indent=4))
                lines.append("")
                lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers, json=json_payload)")
            elif body_raw:
                lines.append("data = " + json.dumps(body_raw))
                lines.append("")
                lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers, data=data)")
            else:
                lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers)")
        lines.append("")
        lines.append("print(resp.status_code)")
        lines.append("print(resp.headers)")
        lines.append("print(resp.text)")
        return "\n".join(lines)

    def generate_powershell(self, method, url, headers, body_raw, attachments):
        method = method.upper()
        url_ps = url.replace("'", "''")
        lines = []

        # Keys are quoted: hyphenated names like Accept-Encoding or X-Api-Key are
        # otherwise parsed as subtraction expressions and break the hashtable.
        # Content-Type is always kept out of -Headers (it's a restricted header
        # that Windows PowerShell 5.1 rejects there) and passed via -ContentType.
        content_type = next((v for k, v in headers.items()
                             if k.lower() == "content-type"), None)
        hdrs = self._snippet_headers(headers, drop_content_type=True)
        if hdrs:
            lines.append("$headers = @{")
            for k, v in hdrs.items():
                safe_k = k.replace("'", "''")
                safe_v = v.replace("'", "''")
                lines.append(f"    '{safe_k}' = '{safe_v}'")
            lines.append("}")
            lines.append("")
        else:
            lines.append("$headers = @{}")
            lines.append("")

        if attachments:
            lines.append("$form = @{")
            for k, v in self._form_text_fields(body_raw).items():
                safe_k = k.replace("'", "''")
                safe_v = v.replace("'", "''")
                lines.append(f"    '{safe_k}' = '{safe_v}'")
            for att in attachments:
                safe_field = att['field'].replace("'", "''")
                path_ps = att['path'].replace("'", "''")
                lines.append(f"    '{safe_field}' = Get-Item -LiteralPath '{path_ps}'")
            lines.append("}")
            lines.append("")
            # -Form needs PowerShell 6+. The hashtable values (Get-Item) make it
            # send proper multipart/form-data with the file's content type.
            lines.append(
                f"Invoke-RestMethod -Uri '{url_ps}' -Method {method} -Headers $headers -Form $form"
            )
            return "\n".join(lines)

        # Non-multipart
        body_is_json = False
        parsed_json = None
        try:
            parsed_json = json.loads(body_raw) if body_raw else None
            if parsed_json is not None:
                body_is_json = True
        except Exception:
            body_is_json = False

        cmd_parts = [f"Invoke-RestMethod -Uri '{url_ps}' -Method {method}"]
        if hdrs:
            cmd_parts.append("-Headers $headers")
        if body_raw:
            if body_is_json:
                # Single-quoted here-string: literal, no $-expansion. The opening
                # @' and closing '@ must each sit alone at column 0.
                lines.append("$body = @'")
                lines.append(json.dumps(parsed_json))
                lines.append("'@")
                lines.append("")
                cmd_parts.append("-Body $body")
                ct = content_type or "application/json"
                cmd_parts.append(f"-ContentType '{ct.replace(chr(39), chr(39) * 2)}'")
            else:
                raw_escaped = body_raw.replace("'", "''")
                lines.append(f"$body = '{raw_escaped}'")
                lines.append("")
                cmd_parts.append("-Body $body")
                if content_type:
                    cmd_parts.append(f"-ContentType '{content_type.replace(chr(39), chr(39) * 2)}'")

        # PowerShell line continuation is a trailing backtick.
        lines.append(" `\n    ".join(cmd_parts))
        lines.append("")
        lines.append("# Use Invoke-WebRequest instead if you need the raw response stream/status.")
        return "\n".join(lines)

    def generate_java(self, method, url, headers, body_raw, attachments):
        """Java 11+ java.net.http.HttpClient. Multipart is assembled by hand
        (HttpClient has no built-in multipart publisher)."""
        method = method.upper()

        def js(s):  # Java string-literal escaping
            return (str(s).replace("\\", "\\\\").replace('"', '\\"')
                    .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))

        hdrs = self._snippet_headers(headers, drop_content_type=bool(attachments))
        L = []

        if attachments:
            L += [
                "import java.net.URI;",
                "import java.net.http.HttpClient;",
                "import java.net.http.HttpRequest;",
                "import java.net.http.HttpResponse;",
                "import java.nio.charset.StandardCharsets;",
                "import java.nio.file.Files;",
                "import java.nio.file.Path;",
                "import java.util.ArrayList;",
                "import java.util.List;",
                "",
                "public class ApiRequest {",
                "    public static void main(String[] args) throws Exception {",
                '        String boundary = "----JavaFormBoundary" + Long.toHexString(System.currentTimeMillis());',
                "        List<byte[]> parts = new ArrayList<>();",
                "",
            ]
            for k, v in self._form_text_fields(body_raw).items():
                L.append(f'        addText(parts, boundary, "{js(k)}", "{js(v)}");')
            for att in attachments:
                mime = att.get("mime") or "application/octet-stream"
                L.append(f'        addFile(parts, boundary, "{js(att["field"])}", "{js(att["path"])}", "{js(mime)}");')
            L += [
                '        parts.add(("--" + boundary + "--\\r\\n").getBytes(StandardCharsets.UTF_8));',
                "",
                "        byte[] body = concat(parts);",
                "",
                "        HttpRequest request = HttpRequest.newBuilder()",
                f'            .uri(URI.create("{js(url)}"))',
            ]
            for k, v in hdrs.items():
                L.append(f'            .header("{js(k)}", "{js(v)}")')
            L += [
                '            .header("Content-Type", "multipart/form-data; boundary=" + boundary)',
                f'            .method("{method}", HttpRequest.BodyPublishers.ofByteArray(body))',
                "            .build();",
                "",
                "        HttpResponse<String> response = HttpClient.newHttpClient()",
                "            .send(request, HttpResponse.BodyHandlers.ofString());",
                "        System.out.println(response.statusCode());",
                "        System.out.println(response.body());",
                "    }",
                "",
                "    static void addText(List<byte[]> parts, String boundary, String name, String value) {",
                '        String h = "--" + boundary + "\\r\\n"',
                '            + "Content-Disposition: form-data; name=\\"" + name + "\\"\\r\\n\\r\\n";',
                "        parts.add(h.getBytes(StandardCharsets.UTF_8));",
                "        parts.add(value.getBytes(StandardCharsets.UTF_8));",
                '        parts.add("\\r\\n".getBytes(StandardCharsets.UTF_8));',
                "    }",
                "",
                "    static void addFile(List<byte[]> parts, String boundary, String name, String filePath, String contentType) throws Exception {",
                "        Path path = Path.of(filePath);",
                '        String h = "--" + boundary + "\\r\\n"',
                '            + "Content-Disposition: form-data; name=\\"" + name + "\\"; filename=\\"" + path.getFileName() + "\\"\\r\\n"',
                '            + "Content-Type: " + contentType + "\\r\\n\\r\\n";',
                "        parts.add(h.getBytes(StandardCharsets.UTF_8));",
                "        parts.add(Files.readAllBytes(path));",
                '        parts.add("\\r\\n".getBytes(StandardCharsets.UTF_8));',
                "    }",
                "",
                "    static byte[] concat(List<byte[]> parts) {",
                "        int total = 0;",
                "        for (byte[] p : parts) total += p.length;",
                "        byte[] out = new byte[total];",
                "        int pos = 0;",
                "        for (byte[] p : parts) { System.arraycopy(p, 0, out, pos, p.length); pos += p.length; }",
                "        return out;",
                "    }",
                "}",
            ]
            return "\n".join(L)

        # Non-multipart
        L += [
            "import java.net.URI;",
            "import java.net.http.HttpClient;",
            "import java.net.http.HttpRequest;",
            "import java.net.http.HttpResponse;",
            "",
            "public class ApiRequest {",
            "    public static void main(String[] args) throws Exception {",
            "        HttpRequest request = HttpRequest.newBuilder()",
            f'            .uri(URI.create("{js(url)}"))',
        ]
        for k, v in hdrs.items():
            L.append(f'            .header("{js(k)}", "{js(v)}")')
        if body_raw:
            try:
                body_out = json.dumps(json.loads(body_raw))  # compact, valid JSON
            except Exception:
                body_out = body_raw
            L.append(f'            .method("{method}", HttpRequest.BodyPublishers.ofString("{js(body_out)}"))')
        elif method == "GET":
            L.append("            .GET()")
        else:
            L.append(f'            .method("{method}", HttpRequest.BodyPublishers.noBody())')
        L += [
            "            .build();",
            "",
            "        HttpResponse<String> response = HttpClient.newHttpClient()",
            "            .send(request, HttpResponse.BodyHandlers.ofString());",
            "        System.out.println(response.statusCode());",
            "        System.out.println(response.body());",
            "    }",
            "}",
        ]
        return "\n".join(L)

    def generate_axios(self, method, url, headers, body_raw, attachments):
        method_lower = method.lower()
        lines = []
        if attachments:
            lines.append("// Axios multipart example (Node.js)")
            lines.append("const axios = require('axios');")
            lines.append("const FormData = require('form-data');")
            lines.append("const fs = require('fs');")
            lines.append("")
            lines.append("const form = new FormData();")
            for k, v in self._form_text_fields(body_raw).items():
                lines.append(f"form.append({json.dumps(k)}, {json.dumps(v)});")
            for att in attachments:
                lines.append(f"form.append({json.dumps(att['field'])}, fs.createReadStream({json.dumps(att['path'])}), {json.dumps(att['filename'])});")
            lines.append("")
            lines.append("const headers = {")
            for k, v in self._snippet_headers(headers, drop_content_type=True).items():
                lines.append(f"  {json.dumps(k)}: {json.dumps(v)},")
            lines.append("  ...form.getHeaders(),")
            lines.append("};")
            lines.append("")
            lines.append("axios({")
            lines.append(f"  method: {json.dumps(method_lower)},")
            lines.append(f"  url: {json.dumps(url)},")
            lines.append("  headers,")
            lines.append("  data: form")
            lines.append("})")
            lines.append(".then(res => {")
            lines.append("  console.log(res.status);")
            lines.append("  console.log(res.data);")
            lines.append("})")
            lines.append(".catch(err => {")
            lines.append("  if (err.response) {")
            lines.append("    console.log(err.response.status);")
            lines.append("    console.log(err.response.data);")
            lines.append("  } else {")
            lines.append("    console.error(err.message);")
            lines.append("  }")
            lines.append("});")
            return "\n".join(lines)

        # Non-multipart Axios
        lines.append("// Axios example (npm install axios)")
        lines.append("const axios = require('axios');")
        lines.append("")
        hdrs = self._snippet_headers(headers)
        if hdrs:
            lines.append("const headers = {")
            for k, v in hdrs.items():
                lines.append(f"  {json.dumps(k)}: {json.dumps(v)},")
            lines.append("};")
        else:
            lines.append("const headers = {};")
        lines.append("")
        body_is_json = False
        parsed_json = None
        if body_raw:
            try:
                parsed_json = json.loads(body_raw)
                body_is_json = True
            except Exception:
                body_is_json = False

        if body_is_json:
            lines.append("const data = " + json.dumps(parsed_json, indent=2) + ";")
            lines.append("")
            lines.append("axios({")
            lines.append(f"  method: {json.dumps(method_lower)},")
            lines.append(f"  url: {json.dumps(url)},")
            lines.append("  headers,")
            lines.append("  data")
            lines.append("})")
        elif body_raw:
            lines.append("const data = " + json.dumps(body_raw) + ";")
            lines.append("")
            lines.append("axios({")
            lines.append(f"  method: {json.dumps(method_lower)},")
            lines.append(f"  url: {json.dumps(url)},")
            lines.append("  headers,")
            lines.append("  data")
            lines.append("})")
        else:
            lines.append("axios({")
            lines.append(f"  method: {json.dumps(method_lower)},")
            lines.append(f"  url: {json.dumps(url)},")
            lines.append("  headers")
            lines.append("})")
        lines.append(".then(res => {")
        lines.append("  console.log(res.status);")
        lines.append("  console.log(res.data);")
        lines.append("})")
        lines.append(".catch(err => {")
        lines.append("  if (err.response) {")
        lines.append("    console.log(err.response.status);")
        lines.append("    console.log(err.response.data);")
        lines.append("  } else {")
        lines.append("    console.error(err.message);")
        lines.append("  }")
        lines.append("});")
        return "\n".join(lines)

    # ---------- Stress test ----------
    def open_stress_test(self):
        env_name = self.env_combo.currentText()
        env = self.envs.get(env_name, {})

        url = apply_env(self.url_input.text().strip(), env)
        if not url:
            QMessageBox.warning(self, "Invalid URL", "Please enter a URL first.")
            return

        headers = {}
        for line in self.headers_text.toPlainText().splitlines():
            line = line.strip()
            if line and ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = apply_env(value.strip(), env)

        body_raw = apply_env(self.body_text.toPlainText().strip(), env)
        json_body = None
        data = None
        body_type = self.body_type_combo.currentText()
        content_type = headers.get("Content-Type", "").lower()

        if body_type == "JSON" or "application/json" in content_type or (body_raw and body_raw.startswith(("{", "["))):
            if body_raw:
                try:
                    json_body = json.loads(body_raw)
                except Exception:
                    pass
        elif body_type == "Form Data":
            data = {}
            if body_raw:
                for pair in body_raw.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        data[k] = v
        elif body_raw:
            data = body_raw.encode("utf-8")

        params = self._resolved_params(env)
        auth_tuple = self.apply_auth(headers, params, env)
        advanced = self._advanced_snapshot()

        config = {
            'method': self.method_combo.currentText(),
            'url': url,
            'headers': headers,
            'json_body': json_body,
            'data': data,
            'params': params or None,
            'auth': auth_tuple,
            'timeout': None if self.timeout_spin.value() == 0 else self.timeout_spin.value(),
            'allow_redirects': advanced.get("follow_redirects", True),
            'verify_ssl': advanced.get("verify_ssl", True),
            'max_redirects': advanced.get("max_redirects", 30),
            'retry_total': advanced.get("retry_total", 0),
            'retry_backoff': advanced.get("retry_backoff", 0.0),
            'retry_statuses': parse_status_code_list(advanced.get("retry_statuses", "")),
        }

        dlg = StressTestDialog(config, self)
        dlg.show()

    # ---------- Lifecycle ----------
    def closeEvent(self, event):
        try:
            # History is persisted incrementally as requests run; only the
            # wholesale documents need flushing here.
            self.settings["request_timeout"] = self.timeout_spin.value()
            self.settings.update(self._advanced_snapshot())
            save_document("settings", self.settings)
            save_document("envs", self.envs)
            save_document("collections", self.collections)
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CurlPro")
    app.setApplicationDisplayName("CurlPro")
    app.setOrganizationName("CurlPro")
    win = CurlProMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
