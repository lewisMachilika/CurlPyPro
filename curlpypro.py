#!/usr/bin/env python3
"""
CurlPyPro - a lightweight, offline-first desktop API client built with PyQt6.

https://github.com/lewisMachilika/CurlPyPro
Licensed under the MIT License (see LICENSE).

Run:
    pip install -r requirements.txt
    python curlpypro.py
"""
__version__ = "1.0.0"

import sys
import os
import json
import time
import re
import sqlite3
import shutil
import mimetypes
import datetime as _dt
from pathlib import Path
import shlex
import base64
import webbrowser
import difflib
import html as _html
import ssl
import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
from html.parser import HTMLParser
from xml.dom import minidom
from urllib.parse import unquote, urlparse, urlencode, parse_qsl, parse_qs, urlunparse
import urllib.request as urllib_request
import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util import Retry
except Exception:  # pragma: no cover - depends on requests' vendored stack
    Retry = None
# Optional extras: each feature degrades gracefully when its package is missing.
try:
    import yaml  # OpenAPI / Swagger specs written in YAML
except ImportError:
    yaml = None
try:
    import websocket  # websocket-client, for the WebSocket console
except ImportError:
    websocket = None
try:
    import keyring  # OS keychain for secret environment variables
except ImportError:
    keyring = None
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox, QListWidget,
    QSplitter, QMessageBox, QFileDialog, QTabWidget, QListWidgetItem,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QFrame, QScrollArea,
    QGroupBox, QDialog, QDialogButtonBox, QProgressBar, QStatusBar,
    QToolButton, QMenu, QPlainTextEdit, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QDoubleSpinBox,
    QCheckBox, QWidgetAction, QTextBrowser, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QClipboard, QColor, QShortcut, QKeySequence, QFont, QPixmap, QIcon
try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # Keep CurlPyPro usable when the optional preview engine is absent.
    QWebEngineSettings = None
    QWebEngineView = None


class _HTMLPrettyPrinter(HTMLParser):
    """Create a readable representation of HTML without changing the response."""

    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.lines = []
        self.depth = 0

    def _add(self, value):
        value = value.strip()
        if value:
            self.lines.append(f"{'  ' * self.depth}{value}")

    def handle_decl(self, decl):
        self._add(f"<!{decl}>")

    def handle_starttag(self, tag, attrs):
        self._add(self.get_starttag_text())
        if tag.lower() not in self.VOID_ELEMENTS:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        self._add(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.depth = max(0, self.depth - 1)
        self._add(f"</{tag}>")

    def handle_data(self, data):
        for line in data.splitlines():
            self._add(line)

    def handle_entityref(self, name):
        self._add(f"&{name};")

    def handle_charref(self, name):
        self._add(f"&#{name};")

    def handle_comment(self, data):
        self._add(f"<!--{data.strip()}-->")

    def handle_pi(self, data):
        self._add(f"<?{data}>")


def _pretty_html(value):
    parser = _HTMLPrettyPrinter()
    try:
        parser.feed(value)
        parser.close()
        return "\n".join(parser.lines) or value
    except Exception:
        return value


def _pretty_xml(value):
    try:
        parsed = minidom.parseString(value)
        return parsed.toprettyxml(indent="  ")
    except Exception:
        return value

# ---------------------------
# Storage (SQLite, single file in the data dir)
# ---------------------------
DATA_DIR = Path.home() / ".curlpypro"
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "curlpypro.db"

# The app used to store its data in ~/.curlpro. Carry that over once, on first
# run under the new name, so existing history/envs/collections aren't orphaned.
_OLD_DATA_DIR = Path.home() / ".curlpro"


def resource_path(relative_path):
    """Return an asset path that works from source and from PyInstaller."""
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_dir / relative_path


def app_icon():
    """Load the platform application icon bundled with the executable."""
    candidates = (
        "assets/icon.ico",
        "assets/icon.icns",
        "assets/icon-master.png",
    )
    for relative_path in candidates:
        path = resource_path(relative_path)
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def _migrate_old_data_dir():
    if DB_FILE.exists() or not _OLD_DATA_DIR.is_dir():
        return
    old_db = _OLD_DATA_DIR / "curlpro.db"
    try:
        if old_db.exists():
            shutil.copy2(old_db, DB_FILE)
        # Legacy JSON files, if the old install never made it to SQLite.
        for name in ("settings.json", "envs.json", "collections.json", "history.json"):
            old_json = _OLD_DATA_DIR / name
            if old_json.exists() and not (DATA_DIR / name).exists():
                shutil.copy2(old_json, DATA_DIR / name)
    except Exception:
        # Never let a failed migration stop the app from starting.
        pass


_migrate_old_data_dir()

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


# ---------------------------
# Secret environment variables (OS keychain)
# ---------------------------
# Variables marked secret live in the OS keychain (Windows Credential Manager,
# macOS Keychain, Secret Service on Linux). The database keeps only their
# names, with an empty placeholder value, so the variable still shows up.
KEYRING_SERVICE = "CurlPyPro"
_keyring_written = {}  # (env, var) -> value last written, to skip redundant writes


def keyring_available():
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        return getattr(backend, "priority", 1) > 0 and "fail" not in type(backend).__module__
    except Exception:
        return False


def _secret_account(env_name, var):
    return f"env:{env_name}:{var}"


# Windows Credential Manager rejects values over ~1,280 characters, which long
# JWTs exceed, so larger values are split across numbered entries.
_KEYRING_CHUNK = 1000
_CHUNK_MARKER = "\x00chunks:"


def _keyring_set(account, value):
    value = str(value)
    old_parts = _keyring_chunk_count(account)
    if len(value) <= _KEYRING_CHUNK:
        keyring.set_password(KEYRING_SERVICE, account, value)
        parts = 0
    else:
        chunks = [value[i:i + _KEYRING_CHUNK] for i in range(0, len(value), _KEYRING_CHUNK)]
        for i, chunk in enumerate(chunks):
            keyring.set_password(KEYRING_SERVICE, f"{account}#{i}", chunk)
        keyring.set_password(KEYRING_SERVICE, account, f"{_CHUNK_MARKER}{len(chunks)}")
        parts = len(chunks)
    for i in range(parts, old_parts):
        _keyring_delete_quietly(f"{account}#{i}")


def _keyring_chunk_count(account):
    try:
        head = keyring.get_password(KEYRING_SERVICE, account)
    except Exception:
        return 0
    if head and head.startswith(_CHUNK_MARKER):
        try:
            return int(head[len(_CHUNK_MARKER):])
        except ValueError:
            return 0
    return 0


def _keyring_get(account):
    head = keyring.get_password(KEYRING_SERVICE, account)
    if head is None or not head.startswith(_CHUNK_MARKER):
        return head
    parts = []
    for i in range(int(head[len(_CHUNK_MARKER):])):
        part = keyring.get_password(KEYRING_SERVICE, f"{account}#{i}")
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)


def _keyring_delete_quietly(account):
    try:
        keyring.delete_password(KEYRING_SERVICE, account)
    except Exception:
        pass


def _keyring_delete(account):
    for i in range(_keyring_chunk_count(account)):
        _keyring_delete_quietly(f"{account}#{i}")
    _keyring_delete_quietly(account)


def load_envs():
    """Return (envs, secret_names) with secret values filled in from the keychain."""
    envs = load_document("envs", {"default": {}})
    secrets = load_document("env_secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}
    use_keyring = keyring_available()
    for env_name, names in secrets.items():
        env = envs.get(env_name)
        if env is None:
            continue
        for var in names:
            value = None
            if use_keyring:
                try:
                    value = _keyring_get(_secret_account(env_name, var))
                except Exception:
                    value = None
            if value is not None:
                env[var] = value
                _keyring_written[(env_name, var)] = value
    return envs, secrets


def save_envs(envs, secrets):
    """Persist envs, moving secret values into the keychain.

    Returns a list of "env/var" names that could not be stored in the keychain;
    those are removed from `secrets` and saved to the database instead.
    """
    failed = []
    use_keyring = keyring_available()
    stored = {}
    clean_secrets = {}
    for env_name, env_vars in envs.items():
        wanted = set(secrets.get(env_name, []))
        out = {}
        kept = []
        for var, value in env_vars.items():
            if var in wanted:
                if use_keyring:
                    key = (env_name, var)
                    try:
                        if _keyring_written.get(key) != value:
                            _keyring_set(_secret_account(env_name, var), value)
                            _keyring_written[key] = value
                        out[var] = ""
                        kept.append(var)
                        continue
                    except Exception:
                        pass
                elif not value:
                    # Keychain unavailable right now: keep the flag and the
                    # placeholder rather than forgetting the variable is secret.
                    out[var] = ""
                    kept.append(var)
                    continue
                failed.append(f"{env_name}/{var}")
            out[var] = value
        stored[env_name] = out
        if kept:
            clean_secrets[env_name] = kept

    # Remove keychain entries for variables that are no longer secret (or gone).
    if use_keyring:
        for (env_name, var) in list(_keyring_written):
            if var not in clean_secrets.get(env_name, []):
                _keyring_delete(_secret_account(env_name, var))
                _keyring_written.pop((env_name, var), None)

    secrets.clear()
    secrets.update(clean_secrets)
    save_document("envs", stored)
    save_document("env_secrets", clean_secrets)
    return failed


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


def detect_system_proxy(sample_url="https://www.example.com/"):
    """Resolve the proxy Windows itself would use for `sample_url`.

    Browsers get their proxy from WinINET/WinHTTP, which understands PAC
    (``AutoConfigURL``) and WPAD auto-detect. `requests` understands neither —
    it only reads http_proxy/https_proxy — which is why a request can succeed in
    a browser and time out here. This asks WinHTTP the same question the browser
    asks, so corporate PAC setups resolve to the real proxy.

    Returns (proxy, bypass) as strings, or (None, None) when the system is
    configured for direct access or we're not on Windows.
    """
    if os.name != "nt":
        # Non-Windows: the env vars are the system config, and requests already
        # honours them via trust_env.
        env = urllib_request.getproxies()
        return env.get("https") or env.get("http") or None, env.get("no") or None

    import ctypes
    from ctypes import wintypes

    WINHTTP_ACCESS_TYPE_NO_PROXY = 1
    WINHTTP_AUTOPROXY_AUTO_DETECT = 0x00000001
    WINHTTP_AUTOPROXY_CONFIG_URL = 0x00000002
    WINHTTP_AUTO_DETECT_TYPE_DHCP = 0x00000001
    WINHTTP_AUTO_DETECT_TYPE_DNS_A = 0x00000002

    class AUTOPROXY_OPTIONS(ctypes.Structure):
        _fields_ = [
            ("dwFlags", wintypes.DWORD),
            ("dwAutoDetectFlags", wintypes.DWORD),
            ("lpszAutoConfigUrl", wintypes.LPCWSTR),
            ("lpvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("fAutoLogonIfChallenged", wintypes.BOOL),
        ]

    class PROXY_INFO(ctypes.Structure):
        _fields_ = [
            ("dwAccessType", wintypes.DWORD),
            ("lpszProxy", wintypes.LPWSTR),
            ("lpszProxyBypass", wintypes.LPWSTR),
        ]

    class IE_PROXY_CONFIG(ctypes.Structure):
        _fields_ = [
            ("fAutoDetect", wintypes.BOOL),
            ("lpszAutoConfigUrl", wintypes.LPWSTR),
            ("lpszProxy", wintypes.LPWSTR),
            ("lpszProxyBypass", wintypes.LPWSTR),
        ]

    try:
        winhttp = ctypes.WinDLL("winhttp.dll")
        winhttp.WinHttpOpen.restype = ctypes.c_void_p
        winhttp.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR,
                                        wintypes.LPCWSTR, wintypes.DWORD]
        winhttp.WinHttpGetProxyForUrl.restype = wintypes.BOOL
        winhttp.WinHttpGetProxyForUrl.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                                  ctypes.POINTER(AUTOPROXY_OPTIONS),
                                                  ctypes.POINTER(PROXY_INFO)]
        winhttp.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]
        winhttp.WinHttpGetIEProxyConfigForCurrentUser.restype = wintypes.BOOL
        winhttp.WinHttpGetIEProxyConfigForCurrentUser.argtypes = [ctypes.POINTER(IE_PROXY_CONFIG)]

        ie = IE_PROXY_CONFIG()
        if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(ie)):
            return None, None

        # A statically configured proxy wins and needs no PAC evaluation.
        if ie.lpszProxy:
            return ie.lpszProxy, ie.lpszProxyBypass or None

        if not ie.lpszAutoConfigUrl and not ie.fAutoDetect:
            return None, None  # genuinely direct

        handle = winhttp.WinHttpOpen("CurlPyPro/proxy-detect", WINHTTP_ACCESS_TYPE_NO_PROXY,
                                     None, None, 0)
        if not handle:
            return None, None
        try:
            opts = AUTOPROXY_OPTIONS()
            if ie.lpszAutoConfigUrl:
                opts.dwFlags = WINHTTP_AUTOPROXY_CONFIG_URL
                opts.lpszAutoConfigUrl = ie.lpszAutoConfigUrl
            else:
                opts.dwFlags = WINHTTP_AUTOPROXY_AUTO_DETECT
                opts.dwAutoDetectFlags = (WINHTTP_AUTO_DETECT_TYPE_DHCP |
                                          WINHTTP_AUTO_DETECT_TYPE_DNS_A)
            opts.fAutoLogonIfChallenged = True

            info = PROXY_INFO()
            if not winhttp.WinHttpGetProxyForUrl(handle, sample_url, ctypes.byref(opts),
                                                 ctypes.byref(info)):
                return None, None
            return (info.lpszProxy or None), (info.lpszProxyBypass or None)
        finally:
            winhttp.WinHttpCloseHandle(handle)
    except Exception:
        return None, None


def normalize_proxy_url(proxy):
    """Accept `host:port`, `http://host:port`, or a PAC-style `PROXY host:port`.

    WinHTTP hands back bare `host:port` (and sometimes a semicolon-separated
    list); requests needs a full URL, so default the scheme to http.
    """
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    # PAC/WinHTTP can return a fallback list ("PROXY a:80; DIRECT") — take the
    # first entry and drop the PAC keyword, which is not part of the host.
    first = re.split(r"[;,]", proxy)[0].strip()
    m = re.match(r"^(PROXY|HTTP|HTTPS|SOCKS5?)\s+(.+)$", first, re.IGNORECASE)
    if m:
        keyword, first = m.group(1).upper(), m.group(2).strip()
        if "://" not in first:
            # PAC "HTTPS host" means TLS to the proxy itself, not to the target.
            scheme = {"SOCKS": "socks4", "SOCKS5": "socks5", "HTTPS": "https"}.get(keyword)
            if scheme:
                first = f"{scheme}://{first}"
    first = re.split(r"\s+", first)[0].strip()
    if not first or first.upper() == "DIRECT":
        return ""
    if "://" not in first:
        first = "http://" + first
    return first


def build_proxies(proxy, bypass=None):
    """Turn a proxy string into the mapping requests expects, or None."""
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return None
    proxies = {"http": proxy, "https": proxy}
    bypass = (bypass or "").strip()
    if bypass:
        # WinHTTP separates bypass entries with ';', requests' no_proxy uses ','.
        proxies["no_proxy"] = ",".join(
            p for p in re.split(r"[;,\s]+", bypass) if p and p != "<local>"
        )
    return proxies


def make_retry_session(retry_total=0, retry_backoff=0.0, retry_statuses=None, max_redirects=30,
                        cookie_jar=None, proxies=None):
    """Return a requests session configured with optional urllib3 retries.

    Passing a shared `cookie_jar` makes the session read/write into it, so
    cookies set by one request are available to later ones instead of being
    thrown away with the per-request session.
    """
    session = requests.Session()
    if cookie_jar is not None:
        session.cookies = cookie_jar
    if proxies:
        # Explicit config beats the http_proxy/https_proxy env vars requests
        # would otherwise pick up via trust_env.
        session.proxies.update(proxies)
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


def cookiejar_to_list(jar):
    """Serialize a RequestsCookieJar to plain dicts for JSON storage."""
    out = []
    try:
        for c in jar:
            out.append({
                "domain": c.domain, "path": c.path, "name": c.name, "value": c.value,
                "secure": bool(c.secure), "expires": c.expires,
            })
    except Exception:
        pass
    return out


def list_to_cookiejar(items):
    """Rebuild a RequestsCookieJar from the dicts produced by cookiejar_to_list."""
    jar = requests.cookies.RequestsCookieJar()
    for item in items or []:
        try:
            jar.set(
                item.get("name", ""), item.get("value", ""),
                domain=item.get("domain", "") or "", path=item.get("path", "/") or "/",
                secure=bool(item.get("secure", False)), expires=item.get("expires"),
            )
        except Exception:
            continue
    return jar


# ---------------------------
# curl command parsing
# ---------------------------
# Long/short curl flags that take NO argument — safe to skip while tokenising.
_CURL_NOARG_FLAGS = {
    "-i", "--include", "-s", "--silent", "-S", "--show-error", "-k", "--insecure",
    "-L", "--location", "-v", "--verbose", "-f", "--fail", "--compressed",
    "-#", "--progress-bar", "-g", "--globoff", "-N", "--no-buffer", "-#",
    "-O", "--remote-name", "-j", "--junk-session-cookies", "--raw",
    "-J", "--remote-header-name",
}

_CURL_COMMAND_RE = re.compile(r"^\s*curl(?:\.exe)?(?=$|\s)", re.IGNORECASE)


def looks_like_curl(text: str) -> bool:
    """True if `text` looks like a pasted curl command (vs. a plain URL)."""
    return bool(_CURL_COMMAND_RE.match(text or ""))


def _strip_curl_command(text: str) -> str:
    """Remove a leading curl/curl.exe command word from a pasted command."""
    return _CURL_COMMAND_RE.sub("", text or "", count=1).lstrip()


def _normalise_curl_continuations(text: str) -> str:
    """Collapse common shell line continuations before tokenising.

    Supports Unix backslash, PowerShell backtick, and Windows cmd.exe caret.
    Single-line widgets may flatten newlines before paste handlers see them, so
    also handle a dangling continuation marker followed by a likely next flag.
    """
    text = re.sub(r"\\[ \t]*\r?\n", " ", text)
    text = re.sub(r"`[ \t]*\r?\n", " ", text)
    text = re.sub(r"\^[ \t]*\r?\n", " ", text)
    next_token = r"(?=(?:-{1,2}[A-Za-z]|https?://))"
    text = re.sub(r"\\\s+" + next_token, " ", text)
    text = re.sub(r"`\s+" + next_token, " ", text)
    text = re.sub(r"\^\s+" + next_token, " ", text)
    return text


def _split_curl_tokens(text: str) -> list:
    """Tokenise a curl command, tolerant of unbalanced/odd quotes.

    Behaves like shlex in posix mode for the common cases but never raises:
    an unterminated quote simply swallows the remainder of the string as one
    token instead of corrupting every token after it.  The shlex `posix=False`
    fallback used to leave the literal quote characters inside tokens, which
    turned `-H 'X-Note: it's broken'` into the bogus header `'X-Note -> it'`
    and dropped the headers that followed.
    """
    tokens = []
    cur = []
    have = False  # whether `cur` represents a token (incl. an empty quoted one)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in (" ", "\t", "\n", "\r"):
            if have:
                tokens.append("".join(cur))
                cur, have = [], False
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            have = True
            i += 1
            while i < n and text[i] != quote:
                # Inside double quotes, honour the few shell backslash escapes.
                if (quote == '"' and text[i] == "\\" and i + 1 < n
                        and text[i + 1] in ('"', "\\", "$", "`")):
                    cur.append(text[i + 1])
                    i += 2
                else:
                    cur.append(text[i])
                    i += 1
            if i < n:
                i += 1  # consume closing quote (absent if unterminated)
        elif ch == "\\" and i + 1 < n:
            cur.append(text[i + 1])
            have = True
            i += 2
        else:
            cur.append(ch)
            have = True
            i += 1
    if have:
        tokens.append("".join(cur))
    return tokens


def parse_curl_command(text: str) -> dict:
    """Parse a curl command line into request parts.

    Returns a dict: method, url, headers (list of (key, value) pairs — order
    preserved, duplicates kept), data (str or None), is_form (bool),
    form (list of "k=v" / "k=@file" strings), user (str "u:p" or None),
    output_file (str or None).
    Best-effort: unknown flags are skipped.
    """
    result = {
        "method": None,
        "url": None,
        "headers": [],
        "data_parts": [],
        "is_form": False,
        "form": [],
        "user": None,
        "get_with_data": False,
        "output_file": None,
    }

    text = (text or "").strip()
    if looks_like_curl(text):
        text = _strip_curl_command(text)
    text = _normalise_curl_continuations(text)

    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — use a tolerant splitter that keeps the
        # remaining headers intact instead of mangling every later token.
        tokens = _split_curl_tokens(text)

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
                    result["headers"].append((k.strip(), v.strip()))
        elif tok in ("-A", "--user-agent"):
            result["headers"].append(("User-Agent", take()))
        elif tok in ("-e", "--referer"):
            result["headers"].append(("Referer", take()))
        elif tok in ("-b", "--cookie"):
            result["headers"].append(("Cookie", take()))
        elif tok in ("-u", "--user"):
            result["user"] = take()
        elif tok in ("-d", "--data", "--data-raw", "--data-ascii",
                     "--data-binary", "--data-urlencode"):
            result["data_parts"].append(take())
        elif tok in ("-F", "--form", "--form-string"):
            result["is_form"] = True
            result["form"].append(take())
        elif tok in ("-o", "--output"):
            result["output_file"] = take()
        elif tok.startswith("--output="):
            result["output_file"] = tok.split("=", 1)[1]
        elif tok.startswith("-o") and tok != "-o":
            result["output_file"] = tok[2:]
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


def parse_curl_form_value(value: str):
    """Return (kind, payload) for one curl -F value.

    kind is "text" or "file". File payloads are attachment metadata compatible
    with CurlPyPro's attachment list.
    """
    value = value or ""
    if "=" not in value:
        return "text", value

    field, spec = value.split("=", 1)
    field = field.strip() or "file"
    if not spec.startswith("@"):
        return "text", value

    file_spec = spec[1:]
    file_path, *options = file_spec.split(";")
    file_path = file_path.strip().strip('"')
    if not file_path:
        return "text", value
    opts = {}
    for option in options:
        if "=" not in option:
            continue
        key, opt_value = option.split("=", 1)
        opts[key.strip().lower()] = opt_value.strip().strip('"')

    expanded = os.path.expanduser(file_path)
    path_obj = Path(expanded)
    if not path_obj.is_absolute():
        cwd_candidate = Path.cwd() / path_obj
        if cwd_candidate.exists():
            path_obj = cwd_candidate

    path_str = str(path_obj)
    filename = opts.get("filename") or os.path.basename(file_path) or "upload"
    mime = opts.get("type") or mimetypes.guess_type(path_str)[0] or "application/octet-stream"
    return "file", {
        "field": field,
        "path": path_str,
        "filename": filename,
        "mime": mime,
    }


_POST_RESPONSE_SCRIPT_TEMPLATE = """\
# Post-response script — runs after every response for THIS request.
#
# Define:
#   def on_response(response, env):
#
# response is a plain object with:
#   response.status_code, response.headers (dict), response.text
#   response.json()  — parsed body, or None if it isn't valid JSON
#   response.elapsed_ms, response.url
#
# env is the ACTIVE environment's variables (a real dict) — mutate it
# directly (e.g. env['TOKEN'] = ...) to make {{TOKEN}} available immediately,
# including in other open tabs. Changes are saved automatically.
#
# Example: pull an access token out of a login response
# def on_response(response, env):
#     data = response.json()
#     if data and 'access_token' in data:
#         env['TOKEN'] = data['access_token']
"""


class _ScriptResponse:
    """Read-only view of a requests.Response handed to post-response scripts."""

    def __init__(self, response, elapsed_ms):
        self.status_code = response.status_code
        self.headers = dict(response.headers)
        self.text = response.text
        self.url = response.url
        self.elapsed_ms = elapsed_ms
        self._response = response

    def json(self):
        try:
            return self._response.json()
        except Exception:
            return None


# ---------------------------
# Request preparation (shared by request tabs, the collection runner and GraphQL tools)
# ---------------------------
class RequestBuildError(Exception):
    """A saved request can't be turned into an HTTP call (bad JSON, missing file...)."""

    def __init__(self, title, message, critical=False):
        super().__init__(message)
        self.title = title
        self.critical = critical


def parse_header_lines(text, env):
    """Parse `Key: Value` lines into a dict, resolving {{VAR}} in values."""
    headers = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line and ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = apply_env(value.strip(), env)
    return headers


def resolve_param_rows(rows, env):
    """Enabled query-param rows as a list of (key, value), with {{VAR}} resolved."""
    if isinstance(rows, dict):
        rows = [{"enabled": True, "key": k, "value": v} for k, v in rows.items()]
    params = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("enabled", True):
            continue
        key = apply_env(str(row.get("key") or "").strip(), env)
        if not key:
            continue
        params.append((key, apply_env(str(row.get("value") or ""), env)))
    return params


def resolve_auth(cfg, headers, params, env, oauth_token=None):
    """Apply an auth config to headers/params (mutated in place), resolving env vars.

    `oauth_token(cfg)` supplies a token for OAuth 2.0 configs. Returns a
    (username, password) tuple for HTTP Basic auth, or None.
    """
    cfg = cfg or {}
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
    elif t == "OAuth 2.0" and oauth_token is not None:
        token = oauth_token(cfg)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return None


def build_graphql_payload(query, variables_text, env):
    """Return the JSON body for a GraphQL request."""
    payload = {"query": query}
    variables_text = apply_env((variables_text or "").strip(), env)
    if variables_text:
        try:
            variables = json.loads(variables_text)
        except json.JSONDecodeError as e:
            raise RequestBuildError("Invalid GraphQL variables", f"Variables must be a JSON object: {e}")
        if not isinstance(variables, dict):
            raise RequestBuildError("Invalid GraphQL variables", "Variables must be a JSON object.")
        payload["variables"] = variables
    return payload


def close_call_files(files):
    for item in files or []:
        try:
            item[1][1].close()
        except Exception:
            pass


def prepare_request(req, env, timeout=30, oauth_token=None):
    """Turn a saved request dict into keyword arguments for `execute_call`.

    Raises RequestBuildError for problems the user must fix (invalid JSON,
    unreadable attachments, missing URL).
    """
    method = (req.get("method") or "GET").upper()
    url = apply_env((req.get("url") or "").strip(), env)
    if not url:
        raise RequestBuildError("Invalid URL", "Please enter a valid URL.")

    headers = parse_header_lines(req.get("headers", ""), env)
    body_raw = apply_env((req.get("body") or "").strip(), env)
    body_type = req.get("body_type") or "Raw"
    json_body = None
    data = None
    files = None
    content_type = headers.get("Content-Type", "").lower()

    if body_type == "GraphQL":
        # Checked first: a query like `{ users { id } }` would otherwise be
        # mistaken for a JSON body.
        json_body = build_graphql_payload(body_raw, req.get("graphql_variables", ""), env)
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
    elif body_type == "JSON" or "application/json" in content_type or (body_raw and body_raw.startswith(("{", "["))):
        if body_raw:
            try:
                json_body = json.loads(body_raw)
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"
            except json.JSONDecodeError as e:
                raise RequestBuildError("Invalid JSON", f"JSON parsing error: {str(e)}")
    elif body_type == "Form Data":
        data = {}
        if body_raw:
            for pair in body_raw.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    data[k] = v
        attachments = req.get("attachments") or []
        if attachments:
            files = []
            for hk in list(headers.keys()):
                if hk.lower() == "content-type":
                    del headers[hk]
            try:
                for att in attachments:
                    fp = open(att["path"], "rb")
                    files.append((att["field"], (att["filename"], fp, att["mime"])))
            except Exception as e:
                close_call_files(files)
                raise RequestBuildError("File Error", f"Failed to open attachment: {e}", critical=True)
    else:
        data = body_raw.encode("utf-8") if body_raw else None

    params = resolve_param_rows(req.get("params"), env)
    auth = resolve_auth(req.get("auth") or {}, headers, params, env, oauth_token)
    adv = req.get("advanced") or {}
    return {
        "method": method,
        "url": url,
        "headers": headers,
        "json_body": json_body,
        "data": data,
        "files": files,
        "params": params or None,
        "auth": auth,
        "timeout": timeout,
        "allow_redirects": adv.get("follow_redirects", True),
        "verify_ssl": adv.get("verify_ssl", True),
        "max_redirects": adv.get("max_redirects", 30),
        "retry_total": adv.get("retry_total", 0),
        "retry_backoff": adv.get("retry_backoff", 0.0),
        "retry_statuses": parse_status_code_list(adv.get("retry_statuses", "")),
        "use_cookie_jar": adv.get("use_cookie_jar", True),
        "proxies": build_proxies(
            apply_env((adv.get("proxy") or "").strip(), env),
            apply_env((adv.get("proxy_bypass") or "").strip(), env),
        ),
    }


def execute_call(call, cookie_jar=None):
    """Send a prepared call synchronously. Returns (response, elapsed_seconds)."""
    session = make_retry_session(
        call["retry_total"], call["retry_backoff"],
        call["retry_statuses"], call["max_redirects"],
        cookie_jar=cookie_jar if call.get("use_cookie_jar", True) else None,
        proxies=call["proxies"],
    )
    try:
        t0 = time.time()
        resp = session.request(
            call["method"], call["url"],
            headers=call["headers"],
            json=call["json_body"],
            data=call["data"],
            files=call["files"],
            params=call["params"],
            auth=call["auth"],
            allow_redirects=call["allow_redirects"],
            verify=call["verify_ssl"],
            timeout=call["timeout"],
        )
        return resp, time.time() - t0
    finally:
        session.close()
        close_call_files(call.get("files"))


def fetch_oauth2_token(cfg):
    """Fetch a token for the client-credentials or password grant. Returns (token, expires_in)."""
    data = {"grant_type": "client_credentials" if cfg.get("grant_type") == "Client Credentials" else "password"}
    if cfg.get("scope"):
        data["scope"] = cfg["scope"]
    if cfg.get("grant_type") == "Password Credentials":
        data["username"] = cfg.get("username", "")
        data["password"] = cfg.get("password", "")
    resp = requests.post(
        cfg.get("token_url", ""), data=data,
        auth=(cfg.get("client_id", ""), cfg.get("client_secret", "")),
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload["access_token"], int(payload.get("expires_in", 3600))


# ---------------------------
# JSON path, assertions and captures
# ---------------------------
_JSON_PATH_TOKEN = re.compile(r"""\.?([^.\[\]]+)|\[\s*(-?\d+)\s*\]|\[\s*['"](.*?)['"]\s*\]""")


def json_path_get(data, path):
    """Look up a simple JSON path such as `$.data.items[0].id`.

    Supports dotted keys, [index] (negative too), ["quoted key"], and a
    trailing `.length` on lists, objects and strings. Returns (found, value).
    """
    path = (path or "").strip()
    if path.startswith("$"):
        path = path[1:]
    value = data
    pos = 0
    while pos < len(path):
        m = _JSON_PATH_TOKEN.match(path, pos)
        if not m or m.end() == pos:
            return False, None
        pos = m.end()
        key, index, quoted = m.groups()
        if index is not None:
            if not isinstance(value, list):
                return False, None
            i = int(index)
            if not -len(value) <= i < len(value):
                return False, None
            value = value[i]
            continue
        key = quoted if quoted is not None else key.strip()
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"-?\d+", key) and -len(value) <= int(key) < len(value):
            value = value[int(key)]
        elif key == "length" and isinstance(value, (list, dict, str)):
            value = len(value)
        else:
            return False, None
    return True, value


ASSERTION_SOURCES = ["Status code", "Response time (ms)", "Header", "JSON path", "Body", "Body size (bytes)"]
ASSERTION_OPERATORS = [
    "equals", "not equals", "contains", "not contains", "exists", "not exists",
    "<", "<=", ">", ">=", "matches regex", "one of",
]
CAPTURE_SOURCES = ["JSON path", "Header", "Status code", "Body regex"]


class _ResponseReader:
    """Reads values out of a response for assertions and captures, parsing JSON once."""

    _UNPARSED = object()

    def __init__(self, response, elapsed_ms=0):
        self.response = response
        self.elapsed_ms = elapsed_ms
        self._json = self._UNPARSED

    def json(self):
        if self._json is self._UNPARSED:
            try:
                self._json = self.response.json()
            except Exception:
                self._json = None
                return False, None
        return self._json is not None or self._looks_like_null(), self._json

    def _looks_like_null(self):
        try:
            return self.response.text.strip() == "null"
        except Exception:
            return False

    def get(self, source, prop):
        """Return (found, value) for an assertion/capture source."""
        r = self.response
        if source == "Status code":
            return True, r.status_code
        if source == "Response time (ms)":
            return True, round(self.elapsed_ms)
        if source == "Body size (bytes)":
            return True, len(r.content or b"")
        if source == "Header":
            value = r.headers.get(prop)
            return value is not None, value
        if source == "Body":
            return True, r.text
        if source == "JSON path":
            ok, data = self.json()
            if not ok:
                return False, None
            return json_path_get(data, prop)
        if source == "Body regex":
            try:
                m = re.search(prop, r.text or "")
            except re.error:
                return False, None
            if not m:
                return False, None
            return True, m.group(1) if m.groups() else m.group(0)
        return False, None


def _short_repr(value, limit=120):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _values_equal(actual, expected_text):
    if isinstance(actual, str):
        return actual == expected_text
    try:
        expected = json.loads(expected_text)
    except Exception:
        return str(actual) == expected_text
    return actual == expected


def _check_operator(op, actual, expected):
    """Return (passed, reason_if_failed)."""
    if op == "equals":
        return _values_equal(actual, expected), None
    if op == "not equals":
        return not _values_equal(actual, expected), None
    if op in ("contains", "not contains"):
        if isinstance(actual, str):
            hit = expected in actual
        elif isinstance(actual, list):
            try:
                parsed = json.loads(expected)
            except Exception:
                parsed = expected
            hit = parsed in actual or expected in [str(x) for x in actual]
        elif isinstance(actual, dict):
            hit = expected in actual
        else:
            hit = expected in str(actual)
        return (hit if op == "contains" else not hit), None
    if op in ("<", "<=", ">", ">="):
        try:
            a, b = float(actual), float(expected)
        except (TypeError, ValueError):
            return False, "not a number"
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op], None
    if op == "matches regex":
        try:
            return re.search(expected, actual if isinstance(actual, str) else json.dumps(actual)) is not None, None
        except re.error as e:
            return False, f"invalid regex: {e}"
    if op == "one of":
        return any(_values_equal(actual, part.strip()) for part in expected.split(",")), None
    return False, f"unknown operator '{op}'"


def evaluate_assertions(tests, response, elapsed_ms, env):
    """Run assertion rows against a response.

    Returns a list of {"name", "passed", "message"} for each enabled row.
    """
    reader = _ResponseReader(response, elapsed_ms)
    results = []
    for row in tests or []:
        if not isinstance(row, dict) or not row.get("enabled", True):
            continue
        source = row.get("source") or "Status code"
        prop = apply_env((row.get("property") or "").strip(), env)
        op = row.get("operator") or "equals"
        expected = apply_env(str(row.get("expected") or "").strip(), env)
        label = source + (f" {prop}" if prop and source in ("Header", "JSON path") else "")
        name = f"{label} {op}" + ("" if op in ("exists", "not exists") else f" {expected}")

        found, actual = reader.get(source, prop)
        if op == "exists":
            passed, message = found, None if found else "not found"
        elif op == "not exists":
            passed, message = not found, None if not found else f"found: {_short_repr(actual)}"
        elif not found:
            passed, message = False, "not found"
        else:
            passed, reason = _check_operator(op, actual, expected)
            message = None if passed else (reason or f"actual: {_short_repr(actual)}")
        results.append({"name": name, "passed": passed, "message": message or ""})
    return results


def extract_captures(captures, response):
    """Pull values out of a response into variables.

    Returns (values, problems): values maps variable name -> string value;
    problems lists human-readable reasons for captures that matched nothing.
    """
    reader = _ResponseReader(response)
    values, problems = {}, []
    for row in captures or []:
        if not isinstance(row, dict) or not row.get("enabled", True):
            continue
        name = (row.get("variable") or "").strip()
        if not name:
            continue
        source = row.get("source") or "JSON path"
        expr = (row.get("expression") or "").strip()
        found, value = reader.get(source, expr)
        if not found:
            problems.append(f"{name}: nothing matched {source} '{expr}'")
            continue
        values[name] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return values, problems


# ---------------------------
# Response diff
# ---------------------------
def normalize_for_diff(text, sort_json_keys=True):
    """Pretty-print JSON so formatting and (optionally) key order don't show up as changes."""
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=sort_json_keys, ensure_ascii=False)
    except Exception:
        return text or ""


def diff_html(old_text, new_text, old_label, new_label, max_lines=20000):
    """Return (html, added, removed) for a colored unified diff."""
    old_lines = old_text.splitlines()[:max_lines]
    new_lines = new_text.splitlines()[:max_lines]
    added = removed = 0
    out = []
    for line in difflib.unified_diff(old_lines, new_lines, old_label, new_label, lineterm="", n=3):
        esc = _html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            out.append(f'<span style="color:#888;">{esc}</span>')
        elif line.startswith("@@"):
            out.append(f'<span style="color:#6f42c1;">{esc}</span>')
        elif line.startswith("+"):
            added += 1
            out.append(f'<span style="background-color:rgba(40,167,69,0.22);">{esc}</span>')
        elif line.startswith("-"):
            removed += 1
            out.append(f'<span style="background-color:rgba(220,53,69,0.22);">{esc}</span>')
        else:
            out.append(esc)
    body = "\n".join(out)
    return f'<pre style="font-family:Consolas,Menlo,monospace;">{body}</pre>', added, removed


# ---------------------------
# Importers: Postman collections/environments and OpenAPI/Swagger specs
# ---------------------------
def parse_import_text(text):
    """Parse JSON, or YAML when PyYAML is installed."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as json_error:
        if yaml is None:
            raise ValueError(
                f"Not valid JSON ({json_error}). To import YAML files install PyYAML: pip install PyYAML"
            )
        try:
            return yaml.safe_load(text)
        except Exception as e:
            raise ValueError(f"Not valid JSON or YAML: {e}")


def detect_import_format(data):
    if isinstance(data, dict):
        if "info" in data and "item" in data:
            return "postman-collection"
        if isinstance(data.get("values"), list) and ("_postman_variable_scope" in data or "name" in data):
            return "postman-environment"
        if "openapi" in data or "swagger" in data:
            return "openapi"
    return "curlpypro"


def _empty_request(name, method="GET", url=""):
    return {
        "name": name, "method": method, "url": url, "headers": "", "params": [],
        "body_type": "Raw", "body": "", "auth": {"type": "No Auth"}, "advanced": {},
        "attachments": [], "output_file": None, "scripts": {}, "tests": [], "captures": [],
        "graphql_variables": "",
    }


def _postman_kv(entries):
    """Postman stores auth as [{key, value}] (v2.1) or a plain dict (v2.0)."""
    if isinstance(entries, dict):
        return entries
    return {e.get("key"): e.get("value") for e in entries or [] if isinstance(e, dict)}


def _postman_auth(auth):
    if not isinstance(auth, dict):
        return None
    t = auth.get("type")
    kv = _postman_kv(auth.get(t)) if t else {}
    if t == "noauth":
        return {"type": "No Auth"}
    if t == "bearer":
        return {"type": "Bearer Token", "token": str(kv.get("token", ""))}
    if t == "basic":
        return {"type": "Basic Auth", "username": str(kv.get("username", "")), "password": str(kv.get("password", ""))}
    if t == "apikey":
        return {
            "type": "API Key", "key": str(kv.get("key", "")), "value": str(kv.get("value", "")),
            "add_to": "Query Params" if kv.get("in") == "query" else "Header",
        }
    if t == "oauth2":
        grant = {
            "client_credentials": "Client Credentials",
            "password_credentials": "Password Credentials",
            "authorization_code": "Authorization Code",
        }.get(kv.get("grant_type"), "Client Credentials")
        return {
            "type": "OAuth 2.0", "grant_type": grant,
            "token_url": str(kv.get("accessTokenUrl", "")), "auth_url": str(kv.get("authUrl", "")),
            "client_id": str(kv.get("clientId", "")), "client_secret": str(kv.get("clientSecret", "")),
            "scope": str(kv.get("scope", "")), "redirect_uri": str(kv.get("redirect_uri", "")),
            "username": str(kv.get("username", "")), "password": str(kv.get("password", "")),
        }
    return None


def _postman_url(url):
    """Return (url_without_query, param_rows) for a Postman url (string or object)."""
    if isinstance(url, str):
        raw, query = url, None
        variables = []
    else:
        url = url or {}
        raw = url.get("raw") or ""
        query = url.get("query")
        variables = url.get("variable") or []
        if not raw:
            host = url.get("host") or []
            path = url.get("path") or []
            host = ".".join(host) if isinstance(host, list) else str(host)
            path = "/".join(path) if isinstance(path, list) else str(path)
            raw = (f"{url.get('protocol')}://" if url.get("protocol") else "") + host + ("/" + path if path else "")

    base, sep, raw_query = raw.partition("?")
    if query is None:
        rows = [{"enabled": True, "key": k, "value": v} for k, v in parse_qsl(raw_query, keep_blank_values=True)]
    else:
        rows = [
            {"enabled": not q.get("disabled", False), "key": str(q.get("key") or ""), "value": str(q.get("value") or "")}
            for q in query if isinstance(q, dict)
        ]

    # Postman path variables (/users/:id) become {{id}} unless a value is given.
    values = {v.get("key"): v.get("value") for v in variables if isinstance(v, dict)}

    def repl(m):
        name = m.group(1)
        value = values.get(name)
        return "/" + (str(value) if value not in (None, "") else "{{" + name + "}}")

    base = re.sub(r"/:([A-Za-z_][A-Za-z0-9_]*)", repl, base)
    return base, rows


def _postman_tests(events):
    """Translate the simplest Postman test, pm.response.to.have.status(N), into an assertion."""
    tests = []
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("listen") != "test":
            continue
        exec_lines = (ev.get("script") or {}).get("exec") or []
        code = "\n".join(exec_lines) if isinstance(exec_lines, list) else str(exec_lines)
        for m in re.finditer(r"pm\.response\.to\.have\.status\(\s*(\d{3})\s*\)", code):
            tests.append({"enabled": True, "source": "Status code", "property": "",
                          "operator": "equals", "expected": m.group(1)})
    return tests


def _postman_request(item, name, inherited_auth, warnings):
    r = item.get("request") or {}
    if isinstance(r, str):
        r = {"url": r, "method": "GET"}
    url, params = _postman_url(r.get("url"))
    req = _empty_request(name, (r.get("method") or "GET").upper(), url)
    req["params"] = params
    req["headers"] = "\n".join(
        f"{h.get('key')}: {h.get('value', '')}"
        for h in r.get("header") or []
        if isinstance(h, dict) and h.get("key") and not h.get("disabled")
    )

    body = r.get("body") or {}
    mode = body.get("mode")
    if mode == "raw":
        req["body"] = body.get("raw") or ""
        lang = ((body.get("options") or {}).get("raw") or {}).get("language")
        req["body_type"] = "JSON" if lang == "json" else "Raw"
    elif mode == "urlencoded":
        req["body_type"] = "Form Data"
        req["body"] = "&".join(
            f"{p.get('key')}={p.get('value', '')}" for p in body.get("urlencoded") or []
            if isinstance(p, dict) and not p.get("disabled")
        )
    elif mode == "formdata":
        req["body_type"] = "Form Data"
        fields = []
        for p in body.get("formdata") or []:
            if not isinstance(p, dict) or p.get("disabled"):
                continue
            if p.get("type") == "file":
                src = p.get("src")
                src = src[0] if isinstance(src, list) and src else src
                if src:
                    req["attachments"].append({
                        "field": p.get("key") or "file", "path": str(src),
                        "filename": os.path.basename(str(src)),
                        "mime": mimetypes.guess_type(str(src))[0] or "application/octet-stream",
                    })
            else:
                fields.append(f"{p.get('key')}={p.get('value', '')}")
        req["body"] = "&".join(fields)
    elif mode == "graphql":
        gql = body.get("graphql") or {}
        req["body_type"] = "GraphQL"
        req["body"] = gql.get("query") or ""
        req["graphql_variables"] = gql.get("variables") or ""
    elif mode:
        warnings.append(f"{name}: body mode '{mode}' is not supported and was skipped")

    auth = _postman_auth(r.get("auth")) if r.get("auth") else inherited_auth
    if auth:
        req["auth"] = auth
    req["tests"] = _postman_tests(item.get("event"))
    return req


def import_postman_collection(data):
    """Convert a Postman v2.0/v2.1 collection.

    Returns {"collections": {name: [requests]}, "environments": {name: vars}, "warnings": [...]}.
    """
    info = data.get("info") or {}
    coll_name = info.get("name") or "Postman collection"
    warnings = []
    requests_out = []

    def walk(items, prefix, auth):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or "Request"
            if "item" in item:  # folder
                folder_auth = _postman_auth(item.get("auth")) if item.get("auth") else auth
                walk(item["item"], f"{prefix}{name} / ", folder_auth)
            else:
                requests_out.append(_postman_request(item, prefix + name, auth, warnings))

    walk(data.get("item"), "", _postman_auth(data.get("auth")))

    envs = {}
    variables = {
        str(v.get("key")): "" if v.get("value") is None else str(v.get("value"))
        for v in data.get("variable") or [] if isinstance(v, dict) and v.get("key")
    }
    if variables:
        envs[coll_name] = variables
    if any("pm." in json.dumps(i.get("event") or []) for i in data.get("item") or [] if isinstance(i, dict)):
        warnings.append("Postman JavaScript tests/scripts can't run here; only status-code checks were converted.")
    return {"collections": {coll_name: requests_out}, "environments": envs, "warnings": warnings}


def import_postman_environment(data):
    name = data.get("name") or "Postman environment"
    variables = {
        str(v.get("key")): "" if v.get("value") is None else str(v.get("value"))
        for v in data.get("values") or []
        if isinstance(v, dict) and v.get("key") and v.get("enabled", True)
    }
    return {"collections": {}, "environments": {name: variables}, "warnings": []}


def _resolve_ref(spec, node, seen=None):
    """Follow local $ref pointers (#/components/...)."""
    seen = seen or set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen or not isinstance(ref, str) or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        target = spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return {}
            target = target[part]
        node = target
    return node


def schema_example(spec, schema, depth=0):
    """Build an example value from a JSON schema (OpenAPI flavour)."""
    schema = _resolve_ref(spec, schema)
    if not isinstance(schema, dict) or depth > 6:
        return None
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    if isinstance(schema.get("examples"), list) and schema["examples"]:
        return schema["examples"][0]
    if schema.get("enum"):
        return schema["enum"][0]
    if "allOf" in schema:
        merged = {}
        for part in schema["allOf"]:
            value = schema_example(spec, part, depth + 1)
            if isinstance(value, dict):
                merged.update(value)
        return merged
    for key in ("oneOf", "anyOf"):
        if schema.get(key):
            return schema_example(spec, schema[key][0], depth + 1)

    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), t[0] if t else None)
    if t == "object" or (t is None and "properties" in schema):
        return {
            name: schema_example(spec, prop, depth + 1)
            for name, prop in (schema.get("properties") or {}).items()
        }
    if t == "array":
        item = schema_example(spec, schema.get("items") or {}, depth + 1)
        return [] if item is None else [item]
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return True
    if t == "string":
        return {
            "date-time": "2024-01-01T00:00:00Z", "date": "2024-01-01", "email": "user@example.com",
            "uuid": "00000000-0000-0000-0000-000000000000", "uri": "https://example.com",
        }.get(schema.get("format"), "string")
    return None


def _openapi_base_url(spec, warnings):
    if "openapi" in spec:
        servers = spec.get("servers") or []
        if not servers:
            warnings.append("The spec has no servers; set the baseUrl variable to your API's address.")
            return ""
        server = servers[0]
        url = server.get("url") or ""
        for name, var in (server.get("variables") or {}).items():
            url = url.replace("{" + name + "}", str((var or {}).get("default", "")))
        if url.startswith("/"):
            warnings.append(f"The server URL '{url}' is relative; prefix baseUrl with your API's host.")
        return url.rstrip("/")
    scheme = (spec.get("schemes") or ["https"])[0]
    host = spec.get("host") or ""
    if not host:
        warnings.append("The spec has no host; set the baseUrl variable to your API's address.")
    return f"{scheme}://{host}{spec.get('basePath') or ''}".rstrip("/") if host else (spec.get("basePath") or "").rstrip("/")


def _openapi_security(spec, op, env_vars):
    requirements = op.get("security", spec.get("security")) or []
    schemes = (spec.get("components") or {}).get("securitySchemes") or spec.get("securityDefinitions") or {}
    for requirement in requirements:
        for name in (requirement or {}):
            s = _resolve_ref(spec, schemes.get(name) or {})
            t = (s.get("type") or "").lower()
            scheme = (s.get("scheme") or "").lower()
            if (t == "http" and scheme == "bearer") or (t == "oauth2" and not s.get("flows") and not s.get("tokenUrl")):
                env_vars.setdefault("bearerToken", "")
                return {"type": "Bearer Token", "token": "{{bearerToken}}"}
            if (t == "http" and scheme == "basic") or t == "basic":
                env_vars.setdefault("username", "")
                env_vars.setdefault("password", "")
                return {"type": "Basic Auth", "username": "{{username}}", "password": "{{password}}"}
            if t == "apikey":
                env_vars.setdefault("apiKey", "")
                return {
                    "type": "API Key", "key": s.get("name") or "X-API-Key", "value": "{{apiKey}}",
                    "add_to": "Query Params" if s.get("in") == "query" else "Header",
                }
            if t == "oauth2":
                flows = s.get("flows") or {}
                env_vars.setdefault("clientId", "")
                env_vars.setdefault("clientSecret", "")
                cfg = {"type": "OAuth 2.0", "client_id": "{{clientId}}", "client_secret": "{{clientSecret}}"}
                if "clientCredentials" in flows or s.get("flow") == "application":
                    f = flows.get("clientCredentials") or s
                    cfg.update(grant_type="Client Credentials", token_url=f.get("tokenUrl", ""))
                elif "password" in flows or s.get("flow") == "password":
                    f = flows.get("password") or s
                    cfg.update(grant_type="Password Credentials", token_url=f.get("tokenUrl", ""))
                else:
                    f = flows.get("authorizationCode") or s
                    cfg.update(grant_type="Authorization Code", token_url=f.get("tokenUrl", ""),
                               auth_url=f.get("authorizationUrl", ""))
                scopes = f.get("scopes") or {}
                cfg["scope"] = " ".join(scopes.keys()) if isinstance(scopes, dict) else ""
                return cfg
    return None


def _form_body_from_schema(spec, schema):
    example = schema_example(spec, schema)
    if not isinstance(example, dict):
        return ""
    return "&".join(f"{k}={'' if v is None else v}" for k, v in example.items() if not isinstance(v, (dict, list)))


def import_openapi(spec):
    """Convert an OpenAPI 3.x or Swagger 2.0 spec into one collection plus an environment."""
    warnings = []
    info = spec.get("info") or {}
    title = info.get("title") or "OpenAPI import"
    env_vars = {"baseUrl": _openapi_base_url(spec, warnings)}
    requests_out = []
    methods = ("get", "post", "put", "patch", "delete", "head", "options")

    for path, path_item in (spec.get("paths") or {}).items():
        path_item = _resolve_ref(spec, path_item)
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for method in methods:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            label = op.get("summary") or op.get("operationId") or f"{method.upper()} {path}"
            tags = op.get("tags") or []
            name = f"{tags[0]} / {label}" if tags else label
            url_path = re.sub(r"\{([^}/]+)\}", lambda m: "{{" + m.group(1) + "}}", path)
            req = _empty_request(name, method.upper(), "{{baseUrl}}" + url_path)

            # Operation-level parameters override path-level ones with the same name+location.
            params_by_key = {}
            for p in list(shared_params) + list(op.get("parameters") or []):
                p = _resolve_ref(spec, p)
                if isinstance(p, dict) and p.get("name"):
                    params_by_key[(p.get("in"), p["name"])] = p

            header_lines, form_fields = [], []
            for (location, pname), p in params_by_key.items():
                example = p.get("example")
                if example is None:
                    example = schema_example(spec, p.get("schema") or p)
                if example is None or isinstance(example, (dict, list)):
                    value = ""
                elif isinstance(example, bool):
                    value = "true" if example else "false"
                else:
                    value = str(example)
                if location == "query":
                    req["params"].append({"enabled": bool(p.get("required")), "key": pname, "value": value})
                elif location == "header" and p.get("required"):
                    header_lines.append(f"{pname}: {value}")
                elif location == "path":
                    env_vars.setdefault(pname, value)
                elif location == "body":  # Swagger 2.0
                    body = schema_example(spec, p.get("schema") or {})
                    if body is not None:
                        req["body_type"] = "JSON"
                        req["body"] = json.dumps(body, indent=2)
                        header_lines.append("Content-Type: application/json")
                elif location == "formData":  # Swagger 2.0
                    if p.get("type") != "file":
                        form_fields.append(f"{pname}={value}")
            if form_fields:
                req["body_type"] = "Form Data"
                req["body"] = "&".join(form_fields)

            request_body = _resolve_ref(spec, op.get("requestBody") or {})
            content = request_body.get("content") or {}
            json_type = next((ct for ct in content if "json" in ct), None)
            if json_type:
                media = content[json_type] or {}
                example = media.get("example")
                if example is None and isinstance(media.get("examples"), dict) and media["examples"]:
                    first = _resolve_ref(spec, next(iter(media["examples"].values())))
                    example = first.get("value") if isinstance(first, dict) else None
                if example is None:
                    example = schema_example(spec, media.get("schema") or {})
                req["body_type"] = "JSON"
                req["body"] = json.dumps(example, indent=2) if example is not None else "{}"
                header_lines.append(f"Content-Type: {json_type}")
            elif "application/x-www-form-urlencoded" in content:
                req["body_type"] = "Form Data"
                req["body"] = _form_body_from_schema(spec, (content["application/x-www-form-urlencoded"] or {}).get("schema") or {})
            elif "multipart/form-data" in content:
                req["body_type"] = "Form Data"
                req["body"] = _form_body_from_schema(spec, (content["multipart/form-data"] or {}).get("schema") or {})
                warnings.append(f"{name}: add file attachments manually (multipart body).")

            req["headers"] = "\n".join(header_lines)
            auth = _openapi_security(spec, op, env_vars)
            if auth:
                req["auth"] = auth
            requests_out.append(req)

    if not requests_out:
        warnings.append("No operations were found under 'paths'.")
    return {"collections": {title: requests_out}, "environments": {title: env_vars}, "warnings": warnings}


def import_any(data):
    """Dispatch to the right importer. Returns the importer's result dict."""
    kind = detect_import_format(data)
    if kind == "postman-collection":
        return import_postman_collection(data)
    if kind == "postman-environment":
        return import_postman_environment(data)
    if kind == "openapi":
        return import_openapi(data)
    if isinstance(data, list):
        return {"collections": {None: data}, "environments": {}, "warnings": []}
    if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
        return {"collections": data, "environments": {}, "warnings": []}
    raise ValueError("Unrecognised file: expected a CurlPyPro, Postman or OpenAPI/Swagger export.")


# ---------------------------
# GraphQL introspection
# ---------------------------
GRAPHQL_INTROSPECTION_QUERY = """\
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind name description
      fields(includeDeprecated: false) {
        name description
        args { name type { ...TypeRef } defaultValue }
        type { ...TypeRef }
      }
    }
  }
}
fragment TypeRef on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
}
"""


def graphql_type_str(t):
    """Render an introspection type reference as SDL, e.g. [User!]!."""
    if not t:
        return "?"
    kind = t.get("kind")
    if kind == "NON_NULL":
        return graphql_type_str(t.get("ofType")) + "!"
    if kind == "LIST":
        return "[" + graphql_type_str(t.get("ofType")) + "]"
    return t.get("name") or "?"


def graphql_named_type(t):
    while t and t.get("kind") in ("NON_NULL", "LIST"):
        t = t.get("ofType")
    return t or {}


def graphql_operation_for_field(schema, root_kind, field):
    """Build (query_text, variables_dict) that calls one root field with a scalar selection."""
    types = {t["name"]: t for t in schema.get("types") or [] if t.get("name")}

    def selection(type_ref, depth, indent):
        named = types.get(graphql_named_type(type_ref).get("name"), {})
        if named.get("kind") not in ("OBJECT", "INTERFACE") or depth > 1:
            return ""
        lines = []
        for f in named.get("fields") or []:
            if any(graphql_type_str(a["type"]).endswith("!") for a in f.get("args") or []):
                continue  # needs arguments; leave it out of the skeleton
            inner = graphql_named_type(f["type"])
            sub_kind = types.get(inner.get("name"), {}).get("kind")
            if sub_kind in ("SCALAR", "ENUM"):
                lines.append(f"{indent}  {f['name']}")
            elif depth == 0 and sub_kind in ("OBJECT", "INTERFACE"):
                sub = selection(f["type"], depth + 1, indent + "  ")
                if sub:
                    lines.append(f"{indent}  {f['name']} {sub}")
        if not lines:
            lines.append(f"{indent}  __typename")
        return "{\n" + "\n".join(lines) + f"\n{indent}}}"

    args = field.get("args") or []
    var_decls = ", ".join(f"${a['name']}: {graphql_type_str(a['type'])}" for a in args)
    call_args = ", ".join(f"{a['name']}: ${a['name']}" for a in args)
    op_name = field["name"][:1].upper() + field["name"][1:]
    header = f"{root_kind} {op_name}" + (f"({var_decls})" if var_decls else "")
    call = field["name"] + (f"({call_args})" if call_args else "")
    sel = selection(field["type"], 0, "  ")
    query = f"{header} {{\n  {call}" + (f" {sel}" if sel else "") + "\n}\n"
    variables = {a["name"]: None for a in args}
    return query, variables


# ---------------------------
# OAuth 2.0 token acquisition (runs off the UI thread)
# ---------------------------
class OAuth2TokenWorker(QThread):
    """Fetches an OAuth2 access token for one of three grant types.

    Client Credentials / Password Credentials are a single blocking POST.
    Authorization Code additionally opens the system browser and spins up a
    short-lived local HTTP server to catch the `?code=` redirect.
    """
    token_ready = pyqtSignal(str, int)   # access_token, expires_in (seconds)
    error = pyqtSignal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self):
        try:
            grant = self.cfg.get("grant_type", "Client Credentials")
            if grant == "Authorization Code":
                token, expires_in = self._authorization_code_flow()
            else:
                token, expires_in = self._token_request_flow(grant)
            self.token_ready.emit(token, expires_in)
        except Exception as e:
            self.error.emit(str(e))

    def _token_request_flow(self, grant):
        data = {"grant_type": "client_credentials" if grant == "Client Credentials" else "password"}
        if self.cfg.get("scope"):
            data["scope"] = self.cfg["scope"]
        if grant == "Password Credentials":
            data["username"] = self.cfg.get("username", "")
            data["password"] = self.cfg.get("password", "")
        resp = requests.post(
            self.cfg.get("token_url", ""), data=data,
            auth=(self.cfg.get("client_id", ""), self.cfg.get("client_secret", "")),
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload["access_token"], int(payload.get("expires_in", 3600))

    def _authorization_code_flow(self):
        redirect_uri = self.cfg.get("redirect_uri") or "http://localhost:8765/callback"
        parsed = urlparse(redirect_uri)
        port = parsed.port or 8765
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                qs = parse_qs(urlparse(self.path).query)
                if "code" in qs:
                    result["code"] = qs["code"][0]
                    body = b"<html><body>Login complete \xe2\x80\x94 you can close this tab.</body></html>"
                else:
                    result["error"] = qs.get("error", ["unknown_error"])[0]
                    body = b"<html><body>Authorization failed \xe2\x80\x94 you can close this tab.</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("localhost", port), Handler)
        server.timeout = 120

        params = {
            "response_type": "code",
            "client_id": self.cfg.get("client_id", ""),
            "redirect_uri": redirect_uri,
        }
        if self.cfg.get("scope"):
            params["scope"] = self.cfg["scope"]
        auth_url = self.cfg.get("auth_url", "") + "?" + urlencode(params)
        webbrowser.open(auth_url)

        server.handle_request()  # blocks up to server.timeout seconds
        server.server_close()

        if "code" not in result:
            raise RuntimeError(result.get("error", "Timed out waiting for the authorization redirect"))

        data = {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
        }
        resp = requests.post(
            self.cfg.get("token_url", ""), data=data,
            auth=(self.cfg.get("client_id", ""), self.cfg.get("client_secret", "")),
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload["access_token"], int(payload.get("expires_in", 3600))


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
        retry_statuses=None, cookie_jar=None, proxies=None,
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
        self.cookie_jar = cookie_jar  # shared RequestsCookieJar, or None to stay stateless
        self.proxies = proxies        # {'http': ..., 'https': ...} or None to use env/direct

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
                self.retry_statuses, self.max_redirects,
                cookie_jar=self.cookie_jar,
                proxies=self.proxies,
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
# ctx also carries the request you configured in the app, so you can mutate ANY
# field dynamically without hardcoding it:
#     ctx['base_json']    – deep copy of the configured JSON body (dict/list/None)
#     ctx['base_headers'] – dict copy of the configured headers
#     ctx['base_data']    – deep copy of the configured form/raw data
#     ctx['base_params']  – dict copy of the configured query params
#     ctx['method'], ctx['url']
#
# ── Example 0: change ONE field per request, keep everything else as configured ─
# #  Set FIELD to whatever key you want to vary (e.g. 'otp', 'email', 'amount').
# FIELD = 'otp'
# def before_request(ctx):
#     body = dict(ctx.get('base_json') or {})
#     body[FIELD] = f"{random.randint(0, 999999):06d}"   # your mutation here
#     return {'json_body': body}
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
                cookie_jar=cfg.get('cookie_jar'),
                proxies=cfg.get('proxies'),
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
        import copy as _copy

        # Expose the configured request to scripts so they can mutate ANY field
        # dynamically instead of hardcoding a body. Deep copies are handed out so
        # scripts can freely modify them without touching the shared config.
        cfg = self.config
        self._ctx = {
            '_lock': _threading.Lock(),
            'method': cfg.get('method'),
            'url': cfg.get('url'),
            'base_json': _copy.deepcopy(cfg.get('json_body')),
            'base_headers': dict(cfg.get('headers') or {}),
            'base_data': _copy.deepcopy(cfg.get('data')),
            'base_params': dict(cfg.get('params') or {}),
        }

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
        self._error_counts = {}     # error message -> count
        self._log_cap = 500         # max rows kept in the Live Log table
        self._seq = 0               # per-request sequence number for the log
        self._t_start = None        # wall-clock start, for live RPS
        self.setWindowTitle("Stress Test")
        self.resize(1000, 840)
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
        self.script_editor.setMinimumHeight(140)
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

        # Live one-line counters (always visible, update as requests complete)
        self.live_label = QLabel("—")
        self.live_label.setFont(_monospace_font(10))
        self.live_label.setStyleSheet("color:#0d6efd;")
        layout.addWidget(self.live_label)

        # Results — tabbed so there is room for detail
        results_tabs = QTabWidget()

        # -- Summary tab --
        summary_tab = QWidget()
        stab = QVBoxLayout(summary_tab)

        self.stats_label = QLabel("—")
        self.stats_label.setWordWrap(True)
        self.stats_label.setFont(_monospace_font(10))
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.stats_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        stab.addWidget(self.stats_label)

        self.latency_label = QLabel("")
        self.latency_label.setFont(_monospace_font(9))
        self.latency_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.latency_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        stab.addWidget(self.latency_label)

        stab.addWidget(QLabel("Status codes:"))
        self.status_table = QTableWidget(0, 3)
        self.status_table.setHorizontalHeaderLabels(["Status", "Count", "%"])
        sh = self.status_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.status_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.status_table.verticalHeader().setVisible(False)
        stab.addWidget(self.status_table, 1)
        results_tabs.addTab(summary_tab, "Summary")

        # -- Live Log tab --
        log_tab = QWidget()
        ltab = QVBoxLayout(log_tab)
        log_hint = QLabel(f"Most recent requests (newest first, capped at {self._log_cap} rows).")
        log_hint.setStyleSheet("color:#888;")
        ltab.addWidget(log_hint)
        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(["#", "Status", "Latency (ms)", "Error"])
        lh = self.log_table.horizontalHeader()
        lh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        lh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        lh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.verticalHeader().setVisible(False)
        ltab.addWidget(self.log_table, 1)
        results_tabs.addTab(log_tab, "Live Log")

        # -- Errors tab --
        err_tab = QWidget()
        etab = QVBoxLayout(err_tab)
        etab.addWidget(QLabel("Distinct errors and how many times each occurred:"))
        self.errors_table = QTableWidget(0, 2)
        self.errors_table.setHorizontalHeaderLabels(["Count", "Error message"])
        eh = self.errors_table.horizontalHeader()
        eh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        eh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.errors_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.errors_table.verticalHeader().setVisible(False)
        etab.addWidget(self.errors_table, 1)
        results_tabs.addTab(err_tab, "Errors")

        layout.addWidget(results_tabs, 1)

        # Export row
        export_row = QHBoxLayout()
        export_row.addStretch()
        self.export_btn = QPushButton("Export Results (CSV)…")
        self.export_btn.clicked.connect(self._export_results)
        self.export_btn.setEnabled(False)
        export_row.addWidget(self.export_btn)
        layout.addLayout(export_row)

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
        self._error_counts = {}
        self._seq = 0
        self._t_start = time.time()
        self.status_table.setRowCount(0)
        self.log_table.setRowCount(0)
        self.errors_table.setRowCount(0)
        self.stats_label.setText("—")
        self.latency_label.setText("")
        self.live_label.setText("Running…")
        self.export_btn.setEnabled(False)
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
        self._seq += 1
        if result.get('error'):
            msg = result['error']
            self._error_counts[msg] = self._error_counts.get(msg, 0) + 1
        self._append_log_row(self._seq, result)
        self._update_live_label()

        n = self.total_spin.value()
        # Heavy full-sort stats are throttled so huge runs stay responsive.
        if len(self._results) % max(1, n // 20) == 0:
            self._refresh_stats()
            self._refresh_errors()

    def _on_done(self, summary):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(bool(self._results))
        self._refresh_stats()
        self._refresh_errors()
        self._update_live_label()
        wall = summary.get('wall_ms', 0)
        rps = summary.get('rps', 0)
        total = summary.get('total', 0)
        self.progress_label.setText(
            f"Done — {total} requests in {wall:.0f} ms  ({rps:.1f} req/s)"
        )

    # ---------- Live / stats rendering ----------

    def _update_live_label(self):
        rs = self._results
        n = len(rs)
        if not n:
            self.live_label.setText("—")
            return
        successes = sum(1 for r in rs if r['error'] is None and 200 <= r['status'] < 400)
        errors = sum(1 for r in rs if r['error'] is not None)
        elapsed = max(1e-6, time.time() - (self._t_start or time.time()))
        rps = n / elapsed
        total = self.total_spin.value()
        remaining = max(0, total - n)
        self.live_label.setText(
            f"Done {n}/{total}   ✓ {successes}   ✗ {errors}   "
            f"{rps:.0f} req/s   remaining {remaining}"
        )

    @staticmethod
    def _pct(sorted_times, q):
        if not sorted_times:
            return 0.0
        n = len(sorted_times)
        return sorted_times[min(n - 1, max(0, int(round(q * n)) - 1))]

    def _refresh_stats(self):
        rs = self._results
        if not rs:
            return
        times = [r['elapsed'] for r in rs]
        st = sorted(times)
        n = len(st)
        p50 = self._pct(st, 0.50)
        p90 = self._pct(st, 0.90)
        p95 = self._pct(st, 0.95)
        p99 = self._pct(st, 0.99)
        successes = sum(1 for r in rs if r['error'] is None and 200 <= r['status'] < 400)
        errors = sum(1 for r in rs if r['error'] is not None)

        self.stats_label.setText(
            f"Completed: {n}    Success: {successes}    Errors: {errors}\n"
            f"Latency  avg {sum(times)/n:7.1f} ms   min {st[0]:7.1f} ms   max {st[-1]:7.1f} ms\n"
            f"         p50 {p50:7.1f} ms   p90 {p90:7.1f} ms   "
            f"p95 {p95:7.1f} ms   p99 {p99:7.1f} ms"
        )
        self.latency_label.setText(self._latency_histogram(st))

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
            color = self._status_color(status)
            for item in (si, ci, pi):
                item.setForeground(color)
            self.status_table.setItem(row, 0, si)
            self.status_table.setItem(row, 1, ci)
            self.status_table.setItem(row, 2, pi)

    def _latency_histogram(self, sorted_times, buckets=10, width=40):
        """A tiny text histogram of latencies — no extra dependencies."""
        if not sorted_times:
            return ""
        lo, hi = sorted_times[0], sorted_times[-1]
        if hi <= lo:
            return f"Latency spread: all ≈ {lo:.1f} ms"
        span = hi - lo
        counts = [0] * buckets
        for t in sorted_times:
            idx = min(buckets - 1, int((t - lo) / span * buckets))
            counts[idx] += 1
        peak = max(counts) or 1
        lines = ["Latency distribution:"]
        for i, c in enumerate(counts):
            b_lo = lo + span * i / buckets
            b_hi = lo + span * (i + 1) / buckets
            bar = "█" * int(c / peak * width)
            lines.append(f"{b_lo:7.0f}–{b_hi:<7.0f} ms | {bar} {c}")
        return "\n".join(lines)

    def _refresh_errors(self):
        self.errors_table.setRowCount(0)
        for msg, count in sorted(self._error_counts.items(), key=lambda kv: -kv[1]):
            row = self.errors_table.rowCount()
            self.errors_table.insertRow(row)
            ci = QTableWidgetItem(str(count))
            ci.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            mi = QTableWidgetItem(msg)
            self.errors_table.setItem(row, 0, ci)
            self.errors_table.setItem(row, 1, mi)

    @staticmethod
    def _status_color(status):
        if status == 0 or status >= 400:
            return QColor('#dc3545')
        if 200 <= status < 300:
            return QColor('#28a745')
        return QColor('#fd7e14')

    def _append_log_row(self, seq, result):
        status = result.get('status', 0)
        table = self.log_table
        table.insertRow(0)
        label = str(status) if status else 'ERR'
        cells = [
            QTableWidgetItem(str(seq)),
            QTableWidgetItem(label),
            QTableWidgetItem(f"{result.get('elapsed', 0):.1f}"),
            QTableWidgetItem(result.get('error') or ""),
        ]
        color = self._status_color(status)
        for col, item in enumerate(cells):
            if col != 3:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(color)
            table.setItem(0, col, item)
        # Cap the table so long runs don't grow the widget without bound.
        while table.rowCount() > self._log_cap:
            table.removeRow(table.rowCount() - 1)

    def _export_results(self):
        if not self._results:
            return
        fname, _ = QFileDialog.getSaveFileName(
            self, "Export Stress Results",
            str(Path.home() / "stress_results.csv"),
            "CSV Files (*.csv);;All Files (*)"
        )
        if not fname:
            return
        try:
            import csv
            with open(fname, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["seq", "status", "elapsed_ms", "error"])
                for i, r in enumerate(self._results, 1):
                    writer.writerow([i, r.get('status', 0),
                                     f"{r.get('elapsed', 0):.3f}", r.get('error') or ""])
            QMessageBox.information(self, "Exported", f"Wrote {len(self._results)} rows to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        super().closeEvent(event)


# ---------------------------
# Environment dialog
# ---------------------------
class EnvironmentDialog(QDialog):
    def __init__(self, envs, parent=None, secrets=None):
        super().__init__(parent)
        self.envs = envs
        self.secrets = {k: list(v) for k, v in (secrets or {}).items()}
        self._loaded_env = None  # env whose rows are currently shown
        self.setWindowTitle("Environment Manager")
        self.setModal(True)
        self.resize(760, 520)
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
        self.vars_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidget(self.vars_widget)
        scroll.setWidgetResizable(True)
        vars_layout.addWidget(scroll)

        add_var_btn = QPushButton("Add Variable")
        add_var_btn.clicked.connect(lambda: self.add_variable_row())
        add_var_btn.setFixedHeight(32)
        vars_layout.addWidget(add_var_btn)

        if keyring_available():
            note = ("Secret values are stored in your OS keychain (Windows Credential Manager, "
                    "macOS Keychain or Secret Service), not in the CurlPyPro database.")
        else:
            note = ("Install the 'keyring' package to store secret values in your OS keychain. "
                    "Until then every value is saved in ~/.curlpypro/curlpypro.db.")
        note_label = QLabel(note)
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color:#888;")
        vars_layout.addWidget(note_label)

        layout.addWidget(vars_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.env_combo.currentText():
            self.load_environment()

    def load_environment(self, *_args):
        # Keep edits made to the environment being switched away from.
        if self._loaded_env is not None and self._loaded_env in self.envs:
            self._save_env(self._loaded_env)

        for i in reversed(range(self.vars_layout.count())):
            widget = self.vars_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        env_name = self.env_combo.currentText()
        secret_names = set(self.secrets.get(env_name, []))
        if env_name in self.envs:
            env_vars = self.envs[env_name]
            for key, value in env_vars.items():
                self.add_variable_row(key, value, key in secret_names)
        self.add_variable_row()
        self._loaded_env = env_name or None

    def add_variable_row(self, key="", value="", secret=False):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(6, 4, 6, 4)
        row_layout.setSpacing(6)

        key_input = QLineEdit(key)
        key_input.setPlaceholderText("Variable name")
        value_input = QLineEdit(str(value))
        value_input.setPlaceholderText("Variable value")

        secret_check = QCheckBox("Secret")
        secret_check.setToolTip("Store this value in the OS keychain and hide it on screen")
        if not keyring_available():
            secret_check.setEnabled(False)
            secret_check.setToolTip("Install the 'keyring' package to store secrets in the OS keychain")

        def apply_mask(checked):
            value_input.setEchoMode(
                QLineEdit.EchoMode.PasswordEchoOnEdit if checked else QLineEdit.EchoMode.Normal
            )

        secret_check.toggled.connect(apply_mask)
        secret_check.setChecked(secret)
        apply_mask(secret)

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(28, 28)
        remove_btn.clicked.connect(lambda: self.remove_variable_row(row_widget))

        row_layout.addWidget(key_input, 1)
        row_layout.addWidget(value_input, 2)
        row_layout.addWidget(secret_check, 0)
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

    def duplicate_environment(self):
        current = self.env_combo.currentText()
        if not current:
            return
        name, ok = QInputDialog.getText(self, "Duplicate Environment", "New environment name:", text=f"{current}_copy")
        if ok and name:
            if name in self.envs:
                QMessageBox.warning(self, "Exists", "An environment with that name already exists.")
                return
            self._save_env(current)
            self.envs[name] = dict(self.envs.get(current, {}))
            if self.secrets.get(current):
                self.secrets[name] = list(self.secrets[current])
            self.env_combo.addItem(name)
            self.env_combo.setCurrentText(name)

    def delete_environment(self):
        current = self.env_combo.currentText()
        if not current or current == "default":
            QMessageBox.warning(self, "Cannot Delete", "Cannot delete the default environment.")
            return
        reply = QMessageBox.question(self, "Delete Environment", f"Delete environment '{current}'?")
        if reply == QMessageBox.StandardButton.Yes:
            del self.envs[current]
            self.secrets.pop(current, None)
            self._loaded_env = None  # so switching away does not write the deleted env back
            self.env_combo.removeItem(self.env_combo.currentIndex())

    def accept(self):
        self.save_current_environment()
        super().accept()

    def save_current_environment(self):
        env_name = self.env_combo.currentText()
        if env_name:
            self._save_env(env_name)

    def _save_env(self, env_name):
        env_vars = {}
        secret_names = []
        for i in range(self.vars_layout.count()):
            row_widget = self.vars_layout.itemAt(i).widget()
            if row_widget:
                layout = row_widget.layout()
                key = layout.itemAt(0).widget().text().strip()
                value = layout.itemAt(1).widget().text().strip()
                if key:
                    env_vars[key] = value
                    if layout.itemAt(2).widget().isChecked():
                        secret_names.append(key)
        self.envs[env_name] = env_vars
        if secret_names:
            self.secrets[env_name] = secret_names
        else:
            self.secrets.pop(env_name, None)


# ---------------------------
# Cookie jar dialog
# ---------------------------
class CookieJarDialog(QDialog):
    """Inspect/edit the shared cookie jar used by every request/tab."""

    def __init__(self, cookie_jar, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cookie Jar")
        self.resize(760, 420)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Cookies collected from Set-Cookie responses (or added manually) are "
            "sent automatically on later requests that opt into the shared jar."
        ))

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Domain", "Path", "Name", "Value", "Secure", "Expires"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        for c in cookie_jar:
            self._add_row(c.domain, c.path, c.name, c.value, bool(c.secure), c.expires)

        toolbar = QHBoxLayout()
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(lambda: self._add_row("", "/", "", "", False, None))
        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(lambda: self.table.setRowCount(0))
        for b in (btn_add, btn_remove, btn_clear):
            b.setFixedHeight(32)
        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_remove)
        toolbar.addWidget(btn_clear)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(self, domain, path, name, value, secure, expires):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(domain or ""))
        self.table.setItem(row, 1, QTableWidgetItem(path or "/"))
        self.table.setItem(row, 2, QTableWidgetItem(name or ""))
        self.table.setItem(row, 3, QTableWidgetItem(value or ""))
        secure_item = QTableWidgetItem("")
        secure_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
        )
        secure_item.setCheckState(Qt.CheckState.Checked if secure else Qt.CheckState.Unchecked)
        self.table.setItem(row, 4, secure_item)
        self.table.setItem(row, 5, QTableWidgetItem(str(int(expires)) if expires else ""))

    def _remove_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def build_cookie_jar(self):
        """Return a fresh RequestsCookieJar reflecting the table's current rows."""
        jar = requests.cookies.RequestsCookieJar()
        for row in range(self.table.rowCount()):
            def cell(col):
                item = self.table.item(row, col)
                return item.text().strip() if item else ""

            name = cell(2)
            if not name:
                continue
            secure_item = self.table.item(row, 4)
            secure = secure_item.checkState() == Qt.CheckState.Checked if secure_item else False
            expires_text = cell(5)
            expires = None
            if expires_text:
                try:
                    expires = int(float(expires_text))
                except ValueError:
                    expires = None
            try:
                jar.set(name, cell(3), domain=cell(0), path=cell(1) or "/",
                        secure=secure, expires=expires)
            except Exception:
                continue
        return jar


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
# ---------------------------
# Request panel (one per open tab)
# ---------------------------
class RequestPanel(QWidget):
    """One open request+response tab.

    Owns everything about a single request/response pair - method/url/params/
    headers/body/auth/advanced settings/scripts, in-flight request tracking,
    and the response view. Shared state (environments, collections, history,
    settings, the cookie jar) lives on `main` (the CurlPyProMainWindow).
    """

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window

        self._last_response = None          # requests.Response
        self._last_response_bytes = b""     # raw body

        self._request_store = {}   # req_id -> {thread, info, snapshot, result, error, elapsed}
        self._req_counter = 0
        self.file_attachments = []  # [{field, path, filename, mime}]
        self._pending_output_file = None  # from imported curl --output/-o
        self._last_output_file = None     # suggested name for the loaded response

        self._oauth_access_token = None
        self._oauth_token_expires_at = 0

        self.init_ui()

    # ---------- UI ----------
    def init_ui(self):
        # The vertical splitter must own the available viewport directly.
        # Putting it inside a QScrollArea lets its children's size hints grow
        # the whole page, which is why an empty Params table consumed nearly
        # all the visible height and pushed the Response section below it.
        right_layout = QVBoxLayout(self)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # --- Request group ---
        request_group = QGroupBox("Request")
        request_layout = QVBoxLayout(request_group)

        url_layout = QHBoxLayout()
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(110)
        self.method_combo.setFixedHeight(36)
        self.method_combo.setToolTip("HTTP method")
        self.method_combo.currentTextChanged.connect(self._update_method_color)

        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText("Enter URL or paste a curl command   -   press Enter to send")
        self.url_input.curlPasted.connect(self.import_curl)
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setMinimumHeight(36)
        font = self.url_input.font()
        font.setPointSize(11)
        self.url_input.setFont(font)
        self.url_input.returnPressed.connect(self.send_request)
        self.url_input.textEdited.connect(self._clear_pending_output_file)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("send_btn")
        self.send_btn.setFixedWidth(100)
        self.send_btn.setFixedHeight(36)
        self.send_btn.setToolTip("Send request  (Ctrl+Enter / F5)")
        self.send_btn.clicked.connect(self.send_request)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 86400)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setSpecialValueText("infinite (no timeout)")
        self.timeout_spin.setValue(self.main.settings.get("request_timeout", 30))
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
        actions_menu.addAction("Generate - All Languages...", self.generate_all_and_show)
        actions_menu.addAction("Generate - curl", lambda: self.generate_code_and_show("curl"))
        actions_menu.addAction("Generate - python-requests", lambda: self.generate_code_and_show("python-requests"))
        actions_menu.addAction("Generate - powershell", lambda: self.generate_code_and_show("powershell"))
        actions_menu.addAction("Generate - java", lambda: self.generate_code_and_show("java"))
        actions_menu.addAction("Generate - axios", lambda: self.generate_code_and_show("axios"))
        actions_menu.addSeparator()
        actions_menu.addAction("Stress Test...", self.open_stress_test)
        actions_menu.addAction("WebSocket / SSE Console...", self.open_realtime_console)
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

        self.request_tabs = QTabWidget()
        self.request_tabs.addTab(self.create_params_tab(), "Params")

        headers_widget = QWidget()
        headers_layout = QVBoxLayout(headers_widget)
        self.headers_text = QTextEdit()
        self.headers_text.setPlaceholderText("Content-Type: application/json\nAuthorization: Bearer {{TOKEN}}\n\nOne header per line in Key: Value format")
        self.headers_text.setMaximumHeight(200)
        headers_layout.addWidget(self.headers_text)
        self.request_tabs.addTab(headers_widget, "Headers")

        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)

        body_toolbar = QHBoxLayout()
        self.body_type_combo = QComboBox()
        self.body_type_combo.addItems(["Raw", "JSON", "Form Data", "Binary", "GraphQL"])
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

        self.attachments_group.setVisible(False)
        body_layout.addWidget(self.attachments_group)

        self.graphql_group = QGroupBox("GraphQL variables (JSON)")
        graphql_layout = QVBoxLayout(self.graphql_group)
        self.graphql_variables_text = QPlainTextEdit()
        self.graphql_variables_text.setFont(_monospace_font(9))
        self.graphql_variables_text.setPlaceholderText('{\n  "id": "{{USER_ID}}"\n}')
        self.graphql_variables_text.setMaximumHeight(110)
        graphql_layout.addWidget(self.graphql_variables_text)
        graphql_toolbar = QHBoxLayout()
        schema_btn = QPushButton("Browse Schema…")
        schema_btn.setToolTip("Run an introspection query against this endpoint and browse its operations.")
        schema_btn.clicked.connect(self.open_graphql_schema)
        graphql_toolbar.addWidget(schema_btn)
        graphql_toolbar.addStretch()
        graphql_layout.addLayout(graphql_toolbar)
        self.graphql_group.setVisible(False)
        body_layout.addWidget(self.graphql_group)

        self.request_tabs.addTab(body_widget, "Body")
        self.request_tabs.addTab(self.create_auth_tab(), "Auth")
        self.request_tabs.addTab(self.create_advanced_tab(), "Advanced")
        self.request_tabs.addTab(self.create_tests_tab(), "Tests")
        self.request_tabs.addTab(self.create_capture_tab(), "Capture")
        self.request_tabs.addTab(self.create_scripts_tab(), "Scripts")

        request_layout.addWidget(self.request_tabs)
        right_layout.addWidget(request_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        active_group = QGroupBox("Active Requests")
        active_group.setMinimumHeight(90)
        active_group.setMaximumHeight(140)
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

        response_group = QGroupBox("Response")
        response_group.setMinimumHeight(220)
        response_layout = QVBoxLayout(response_group)

        self.response_summary = QLabel("Ready to send request...")
        self.response_summary.setWordWrap(True)
        response_layout.addWidget(self.response_summary)

        self.response_tabs = QTabWidget()
        self.response_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

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

        self.response_tests = QTextBrowser()
        self.response_tests.setHtml("<p style='color:#888;'>Add assertions on the request's Tests tab.</p>")
        self.response_tabs.addTab(self.response_tests, "Tests")

        diff_widget = QWidget()
        diff_layout = QVBoxLayout(diff_widget)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_toolbar = QHBoxLayout()
        self.diff_label = QLabel("Send this request again to compare responses.")
        self.diff_label.setWordWrap(True)
        self.diff_sort_keys_check = QCheckBox("Ignore JSON key order")
        self.diff_sort_keys_check.setChecked(True)
        self.diff_sort_keys_check.toggled.connect(self._render_diff)
        diff_toolbar.addWidget(self.diff_label, 1)
        diff_toolbar.addWidget(self.diff_sort_keys_check)
        diff_layout.addLayout(diff_toolbar)
        self.response_diff = QTextBrowser()
        self.response_diff.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        diff_layout.addWidget(self.response_diff)
        self.response_tabs.addTab(diff_widget, "Diff")
        self._diff_widget = diff_widget
        self._diff_baseline = None  # (label, body_text) the current response is compared against
        self._diff_current = None

        # API payloads commonly contain long tokens/base64 values.  Wrapping
        # keeps those lines inside the response viewport instead of making the
        # whole request panel appear to overflow horizontally.
        for editor in (
            self.response_pretty,
            self.response_raw,
            self.response_headers,
            self.response_insights,
        ):
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            editor.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

        self.response_preview_label = QLabel("No preview available")
        self.response_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.response_preview_label.setMinimumSize(100, 100)
        self.response_preview_scroll = QScrollArea()
        self.response_preview_scroll.setWidget(self.response_preview_label)
        self.response_preview_scroll.setWidgetResizable(True)

        if QWebEngineView is not None:
            self.response_preview_html = QWebEngineView()
            settings = self.response_preview_html.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False
            )
        else:
            self.response_preview_html = QTextBrowser()
            self.response_preview_html.setOpenExternalLinks(True)
            self.response_preview_html.setReadOnly(True)

        self.response_preview_stack = QStackedWidget()
        self.response_preview_stack.addWidget(self.response_preview_scroll)
        self.response_preview_stack.addWidget(self.response_preview_html)
        self.response_tabs.addTab(self.response_preview_stack, "Preview")

        response_layout.addWidget(self.response_tabs)
        download_toolbar = QHBoxLayout()
        self.btn_save_response_bytes = QPushButton("Save Response Body...")
        self.btn_save_response_bytes.setToolTip("Save raw response bytes to a file (good for PDFs/images/binary).")
        self.btn_save_response_bytes.clicked.connect(self._save_response_body_bytes)

        self.btn_extract_base64 = QPushButton("Extract & Save Base64...")
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

        right_layout.addWidget(response_group)

        # Let the user decide how much vertical room each section needs.  The
        # old stretch/fixed-height combination made an empty Params table fill
        # most of the window and kept Active Requests permanently cramped.
        content_splitter = QSplitter(Qt.Orientation.Vertical)
        for section in (request_group, active_group, response_group):
            right_layout.removeWidget(section)
            content_splitter.addWidget(section)
        content_splitter.setChildrenCollapsible(False)
        # Request controls should stay compact.  Without a maximum, QSplitter
        # gives the tab widget most of the extra window height and pushes the
        # actual response below the visible area.
        request_group.setMinimumHeight(250)
        request_group.setMaximumHeight(380)
        content_splitter.setStretchFactor(0, 0)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setStretchFactor(2, 1)
        # Results are the primary output.  Give them the largest initial share
        # while retaining enough room to edit a request and inspect the queue.
        content_splitter.setSizes([300, 110, 500])
        self.content_splitter = content_splitter
        right_layout.insertWidget(0, content_splitter, 1)

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
        # Cap the table's growth like the Headers tab's QTextEdit; otherwise
        # an empty/short table stretches to fill whatever height the Request
        # splitter section has, dwarfing Active Requests and Response below it.
        self.params_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.params_table.setMaximumHeight(160)
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
            self.main.status_bar.showMessage("No query parameters found in URL")
            return
        for key, value in pairs:
            self._add_param_row(key, value, True)
        self.url_input.setText(strip_query_from_url(url))
        self.main.status_bar.showMessage(f"Extracted {len(pairs)} query parameter{'s' if len(pairs) != 1 else ''}")

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
        return resolve_param_rows(self._params_snapshot(), env)

    # ---------- Advanced transport ----------
    def create_advanced_tab(self):
        advanced_widget = QWidget()
        layout = QVBoxLayout(advanced_widget)

        # Keeps the Proxy/Max redirects/Retry labels in one column, wide enough
        # for the longest of them ("Retry status codes:").
        label_width = 130

        transport_group = QGroupBox("Transport")
        transport_layout = QVBoxLayout(transport_group)

        self.follow_redirects_check = QCheckBox("Follow redirects")
        self.follow_redirects_check.setChecked(bool(self.main.settings.get("follow_redirects", True)))
        self.verify_ssl_check = QCheckBox("Verify SSL certificates")
        self.verify_ssl_check.setChecked(bool(self.main.settings.get("verify_ssl", True)))
        self.use_cookie_jar_check = QCheckBox("Use shared cookie jar")
        self.use_cookie_jar_check.setChecked(bool(self.main.settings.get("use_cookie_jar", True)))
        self.use_cookie_jar_check.setToolTip(
            "Send/store cookies in CurlPyPro's shared jar (Options menu → Cookies). "
            "Turn off for stateless requests."
        )

        max_redirects_row = QHBoxLayout()
        max_redirects_label = QLabel("Max redirects:")
        max_redirects_label.setFixedWidth(label_width)
        max_redirects_row.addWidget(max_redirects_label)
        self.max_redirects_spin = QSpinBox()
        self.max_redirects_spin.setRange(1, 100)
        self.max_redirects_spin.setValue(int(self.main.settings.get("max_redirects", 30)))
        self.max_redirects_spin.setFixedWidth(90)
        max_redirects_row.addWidget(self.max_redirects_spin)
        max_redirects_row.addStretch()

        proxy_row = QHBoxLayout()
        proxy_label = QLabel("Proxy:")
        proxy_label.setFixedWidth(label_width)
        proxy_row.addWidget(proxy_label)
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("http://127.0.0.1:8080  (blank = direct / http_proxy env var)")
        self.proxy_input.setText(str(self.main.settings.get("proxy", "")))
        self.proxy_input.setToolTip(
            "Route requests through an HTTP proxy, e.g. a corporate gateway.\n"
            "Leave blank to connect directly (the http_proxy/https_proxy environment\n"
            "variables are still honoured when this is empty).\n\n"
            "If a URL loads in your browser but times out here, your machine is almost\n"
            "certainly using a PAC/auto-config proxy — click Detect to read it."
        )
        proxy_row.addWidget(self.proxy_input, 1)
        self.proxy_detect_btn = QPushButton("Detect")
        self.proxy_detect_btn.setToolTip("Ask Windows which proxy it would use (understands PAC and WPAD).")
        self.proxy_detect_btn.clicked.connect(self._detect_proxy)
        self.proxy_detect_btn.setFixedWidth(80)
        proxy_row.addWidget(self.proxy_detect_btn)

        proxy_bypass_row = QHBoxLayout()
        proxy_bypass_label = QLabel("Proxy bypass:")
        proxy_bypass_label.setFixedWidth(label_width)
        proxy_bypass_row.addWidget(proxy_bypass_label)
        self.proxy_bypass_input = QLineEdit()
        self.proxy_bypass_input.setPlaceholderText("localhost,127.0.0.1,*.internal.corp")
        self.proxy_bypass_input.setText(str(self.main.settings.get("proxy_bypass", "")))
        self.proxy_bypass_input.setToolTip("Comma-separated hosts that should skip the proxy.")
        proxy_bypass_row.addWidget(self.proxy_bypass_input, 1)
        # Line up with the proxy field above, which gives up 80px to Detect.
        proxy_bypass_row.addSpacing(80 + proxy_row.spacing())

        transport_layout.addWidget(self.follow_redirects_check)
        transport_layout.addWidget(self.verify_ssl_check)
        transport_layout.addWidget(self.use_cookie_jar_check)
        transport_layout.addLayout(max_redirects_row)
        transport_layout.addLayout(proxy_row)
        transport_layout.addLayout(proxy_bypass_row)
        layout.addWidget(transport_group)

        retry_group = QGroupBox("Retries")
        retry_layout = QVBoxLayout(retry_group)

        retry_row = QHBoxLayout()
        retry_label = QLabel("Retry attempts:")
        retry_label.setFixedWidth(label_width)
        retry_row.addWidget(retry_label)
        self.retry_total_spin = QSpinBox()
        self.retry_total_spin.setRange(0, 10)
        self.retry_total_spin.setValue(int(self.main.settings.get("retry_total", 0)))
        self.retry_total_spin.setFixedWidth(90)
        retry_row.addWidget(self.retry_total_spin)
        retry_row.addSpacing(18)
        retry_row.addWidget(QLabel("Backoff:"))
        self.retry_backoff_spin = QDoubleSpinBox()
        self.retry_backoff_spin.setRange(0.0, 60.0)
        self.retry_backoff_spin.setDecimals(2)
        self.retry_backoff_spin.setSingleStep(0.25)
        self.retry_backoff_spin.setSuffix(" s")
        self.retry_backoff_spin.setValue(float(self.main.settings.get("retry_backoff", 0.25)))
        self.retry_backoff_spin.setFixedWidth(110)
        retry_row.addWidget(self.retry_backoff_spin)
        retry_row.addStretch()
        retry_layout.addLayout(retry_row)

        statuses_row = QHBoxLayout()
        statuses_label = QLabel("Retry status codes:")
        statuses_label.setFixedWidth(label_width)
        statuses_row.addWidget(statuses_label)
        self.retry_statuses_input = QLineEdit()
        self.retry_statuses_input.setPlaceholderText("429,500,502,503,504")
        self.retry_statuses_input.setText(str(self.main.settings.get("retry_statuses", "429,500,502,503,504")))
        statuses_row.addWidget(self.retry_statuses_input)
        retry_layout.addLayout(statuses_row)

        layout.addWidget(retry_group)
        layout.addStretch()

        # The Request panel is height-capped, which is not enough for both groups:
        # without a scroll area Qt squeezes the rows until the proxy fields are
        # cut in half and Retries falls off the bottom entirely.
        scroll = QScrollArea()
        scroll.setWidget(advanced_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _detect_proxy(self):
        # PAC files can return different proxies per destination, so ask about the
        # URL actually being sent when there is one.
        sample = apply_env(self.url_input.text().strip(), self.main.get_active_env())
        if not sample.lower().startswith(("http://", "https://")):
            sample = "https://www.example.com/"

        proxy, bypass = detect_system_proxy(sample)
        if not proxy:
            QMessageBox.information(
                self, "No proxy detected",
                "Windows reports direct access for this URL — no proxy needed.\n\n"
                "If requests still time out, the block is elsewhere (firewall, VPN, "
                "or the host is genuinely unreachable).",
            )
            return

        self.proxy_input.setText(normalize_proxy_url(proxy))
        if bypass:
            self.proxy_bypass_input.setText(
                ",".join(p for p in re.split(r"[;,\s]+", bypass) if p and p != "<local>")
            )
        QMessageBox.information(
            self, "Proxy detected",
            f"Using the same proxy your browser would for:\n{sample}\n\n{normalize_proxy_url(proxy)}",
        )

    def _resolved_proxies(self, env):
        """Proxy mapping for this request, or None to leave requests on its default."""
        if not hasattr(self, "proxy_input"):
            return None
        return build_proxies(
            apply_env(self.proxy_input.text().strip(), env),
            apply_env(self.proxy_bypass_input.text().strip(), env),
        )

    def _advanced_snapshot(self):
        if not hasattr(self, "follow_redirects_check"):
            return {}
        return {
            "follow_redirects": self.follow_redirects_check.isChecked(),
            "verify_ssl": self.verify_ssl_check.isChecked(),
            "use_cookie_jar": self.use_cookie_jar_check.isChecked(),
            "max_redirects": self.max_redirects_spin.value(),
            "retry_total": self.retry_total_spin.value(),
            "retry_backoff": self.retry_backoff_spin.value(),
            "retry_statuses": self.retry_statuses_input.text().strip(),
            "proxy": self.proxy_input.text().strip(),
            "proxy_bypass": self.proxy_bypass_input.text().strip(),
        }

    def _restore_advanced(self, cfg):
        cfg = cfg or {}
        if "follow_redirects" in cfg:
            self.follow_redirects_check.setChecked(bool(cfg.get("follow_redirects")))
        if "verify_ssl" in cfg:
            self.verify_ssl_check.setChecked(bool(cfg.get("verify_ssl")))
        if "use_cookie_jar" in cfg:
            self.use_cookie_jar_check.setChecked(bool(cfg.get("use_cookie_jar")))
        if "max_redirects" in cfg:
            self.max_redirects_spin.setValue(int(cfg.get("max_redirects") or 30))
        if "retry_total" in cfg:
            self.retry_total_spin.setValue(int(cfg.get("retry_total") or 0))
        if "retry_backoff" in cfg:
            self.retry_backoff_spin.setValue(float(cfg.get("retry_backoff") or 0))
        if "retry_statuses" in cfg:
            self.retry_statuses_input.setText(str(cfg.get("retry_statuses") or ""))
        if "proxy" in cfg:
            self.proxy_input.setText(str(cfg.get("proxy") or ""))
        if "proxy_bypass" in cfg:
            self.proxy_bypass_input.setText(str(cfg.get("proxy_bypass") or ""))

    # ---------- Auth ----------
    def create_auth_tab(self):
        auth_widget = QWidget()
        auth_layout = QVBoxLayout(auth_widget)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type:"))
        self.auth_type_combo = QComboBox()
        self.auth_type_combo.addItems(["No Auth", "Bearer Token", "Basic Auth", "API Key", "OAuth 2.0"])
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

        # 4: OAuth 2.0
        self.auth_stack.addWidget(self._create_oauth2_page())

        auth_layout.addWidget(self.auth_stack)
        return auth_widget

    def _create_oauth2_page(self):
        oauth_page = QWidget()
        oauth_layout = QVBoxLayout(oauth_page)

        grant_row = QHBoxLayout()
        grant_row.addWidget(QLabel("Grant Type:"))
        self.oauth_grant_combo = QComboBox()
        self.oauth_grant_combo.addItems(["Client Credentials", "Authorization Code", "Password Credentials"])
        grant_row.addWidget(self.oauth_grant_combo)
        grant_row.addStretch()
        oauth_layout.addLayout(grant_row)

        oauth_layout.addWidget(QLabel("Access Token URL:"))
        self.oauth_token_url = QLineEdit()
        self.oauth_token_url.setPlaceholderText("https://auth.example.com/oauth/token")
        oauth_layout.addWidget(self.oauth_token_url)

        oauth_layout.addWidget(QLabel("Client ID:"))
        self.oauth_client_id = QLineEdit()
        oauth_layout.addWidget(self.oauth_client_id)

        oauth_layout.addWidget(QLabel("Client Secret:"))
        self.oauth_client_secret = QLineEdit()
        self.oauth_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        oauth_layout.addWidget(self.oauth_client_secret)
        self.oauth_secret_show = QPushButton("Show Secret")
        self.oauth_secret_show.setCheckable(True)
        self.oauth_secret_show.setFixedHeight(28)
        self.oauth_secret_show.toggled.connect(
            lambda checked: self.oauth_client_secret.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        oauth_layout.addWidget(self.oauth_secret_show, alignment=Qt.AlignmentFlag.AlignLeft)

        oauth_layout.addWidget(QLabel("Scope (optional):"))
        self.oauth_scope = QLineEdit()
        oauth_layout.addWidget(self.oauth_scope)

        self.oauth_grant_stack = QStackedWidget()
        self.oauth_grant_combo.currentIndexChanged.connect(self.oauth_grant_stack.setCurrentIndex)

        cc_page = QWidget()
        QVBoxLayout(cc_page).addWidget(QLabel("No extra fields needed for Client Credentials."))
        self.oauth_grant_stack.addWidget(cc_page)

        ac_page = QWidget()
        ac_layout = QVBoxLayout(ac_page)
        ac_layout.addWidget(QLabel("Auth URL:"))
        self.oauth_auth_url = QLineEdit()
        self.oauth_auth_url.setPlaceholderText("https://auth.example.com/oauth/authorize")
        ac_layout.addWidget(self.oauth_auth_url)
        ac_layout.addWidget(QLabel("Redirect URI:"))
        self.oauth_redirect_uri = QLineEdit("http://localhost:8765/callback")
        ac_layout.addWidget(self.oauth_redirect_uri)
        self.oauth_grant_stack.addWidget(ac_page)

        pw_page = QWidget()
        pw_layout = QVBoxLayout(pw_page)
        pw_layout.addWidget(QLabel("Username:"))
        self.oauth_username = QLineEdit()
        pw_layout.addWidget(self.oauth_username)
        pw_layout.addWidget(QLabel("Password:"))
        self.oauth_password = QLineEdit()
        self.oauth_password.setEchoMode(QLineEdit.EchoMode.Password)
        pw_layout.addWidget(self.oauth_password)
        self.oauth_grant_stack.addWidget(pw_page)

        oauth_layout.addWidget(self.oauth_grant_stack)

        token_row = QHBoxLayout()
        self.oauth_get_token_btn = QPushButton("Get New Access Token")
        self.oauth_get_token_btn.clicked.connect(self._oauth2_get_token)
        token_row.addWidget(self.oauth_get_token_btn)
        token_row.addStretch()
        oauth_layout.addLayout(token_row)

        oauth_layout.addWidget(QLabel("Current Token:"))
        self.oauth_token_display = QLineEdit()
        self.oauth_token_display.setReadOnly(True)
        self.oauth_token_display.setPlaceholderText("(no token yet)")
        oauth_layout.addWidget(self.oauth_token_display)

        self.oauth_status_label = QLabel("")
        self.oauth_status_label.setStyleSheet("color:#888;")
        oauth_layout.addWidget(self.oauth_status_label)
        oauth_layout.addStretch()

        # OAuth needs far more rows than the other auth types, and the Auth tab
        # is height-capped: without a scroll area Qt crushes the rows past their
        # minimums and the labels overlap the fields they belong to.
        scroll = QScrollArea()
        scroll.setWidget(oauth_page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

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
        elif t == "OAuth 2.0":
            cfg["grant_type"] = self.oauth_grant_combo.currentText()
            cfg["token_url"] = self.oauth_token_url.text()
            cfg["client_id"] = self.oauth_client_id.text()
            cfg["client_secret"] = self.oauth_client_secret.text()
            cfg["scope"] = self.oauth_scope.text()
            cfg["auth_url"] = self.oauth_auth_url.text()
            cfg["redirect_uri"] = self.oauth_redirect_uri.text()
            cfg["username"] = self.oauth_username.text()
            cfg["password"] = self.oauth_password.text()
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
        self.oauth_grant_combo.setCurrentText(cfg.get("grant_type", "Client Credentials"))
        self.oauth_token_url.setText(cfg.get("token_url", ""))
        self.oauth_client_id.setText(cfg.get("client_id", ""))
        self.oauth_client_secret.setText(cfg.get("client_secret", ""))
        self.oauth_scope.setText(cfg.get("scope", ""))
        self.oauth_auth_url.setText(cfg.get("auth_url", ""))
        self.oauth_redirect_uri.setText(cfg.get("redirect_uri") or "http://localhost:8765/callback")
        self.oauth_username.setText(cfg.get("username", "") if cfg.get("type") == "OAuth 2.0" else self.oauth_username.text())
        self.oauth_password.setText(cfg.get("password", "") if cfg.get("type") == "OAuth 2.0" else self.oauth_password.text())

    def apply_auth(self, headers: dict, params: dict, env: dict):
        """Apply the configured auth to headers/params (mutated in place), resolving env vars.

        Returns an (username, password) tuple for HTTP Basic auth, or None.
        """
        return resolve_auth(self.get_auth_config(), headers, params, env, self._ensure_oauth_token)

    # ---------- OAuth 2.0 ----------
    def _ensure_oauth_token(self, cfg):
        now = time.time()
        if self._oauth_access_token and now < (self._oauth_token_expires_at or 0):
            return self._oauth_access_token
        grant = cfg.get("grant_type", "Client Credentials")
        if grant == "Authorization Code":
            if self._oauth_access_token:
                return self._oauth_access_token
            self.main.status_bar.showMessage(
                "OAuth 2.0 token expired — click 'Get New Access Token' on the Auth tab."
            )
            return None
        try:
            token, expires_in = self._oauth2_fetch_token_sync(cfg)
        except Exception as e:
            self.main.status_bar.showMessage(f"OAuth 2.0 token fetch failed: {e}")
            return None
        self._oauth_access_token = token
        self._oauth_token_expires_at = time.time() + max(0, expires_in - 30)
        self._update_oauth_token_display()
        return token

    def _oauth2_fetch_token_sync(self, cfg):
        return fetch_oauth2_token(resolve_env_in_config(cfg, self.main.get_active_env()))

    def _oauth2_get_token(self):
        cfg = self.get_auth_config()
        if cfg.get("type") != "OAuth 2.0":
            return
        self.oauth_get_token_btn.setEnabled(False)
        self.oauth_status_label.setStyleSheet("color:#888;")
        self.oauth_status_label.setText("Requesting token…")
        self._oauth_worker = OAuth2TokenWorker(resolve_env_in_config(cfg, self.main.get_active_env()))
        self._oauth_worker.token_ready.connect(self._on_oauth_token_ready)
        self._oauth_worker.error.connect(self._on_oauth_token_error)
        self._oauth_worker.start()

    def _on_oauth_token_ready(self, token, expires_in):
        self._oauth_access_token = token
        self._oauth_token_expires_at = time.time() + max(0, expires_in - 30)
        self._update_oauth_token_display()
        self.oauth_get_token_btn.setEnabled(True)
        self.oauth_status_label.setStyleSheet("color:#28a745;")
        self.oauth_status_label.setText(f"Token acquired — expires in {expires_in}s")

    def _on_oauth_token_error(self, message):
        self.oauth_get_token_btn.setEnabled(True)
        self.oauth_status_label.setStyleSheet("color:#dc3545;")
        self.oauth_status_label.setText(f"Error: {message}")

    def _update_oauth_token_display(self):
        token = self._oauth_access_token or ""
        shown = (token[:6] + "…" + token[-4:]) if len(token) > 14 else token
        self.oauth_token_display.setText(shown)

    # ---------- Tests (assertions) & captures ----------
    @staticmethod
    def _make_rule_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, len(headers)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    @staticmethod
    def _check_item(enabled):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        return item

    @staticmethod
    def _combo_cell(options, current):
        combo = QComboBox()
        combo.addItems(options)
        if current in options:
            combo.setCurrentText(current)
        return combo

    @staticmethod
    def _remove_selected_rows(table):
        for row in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

    @staticmethod
    def _cell_text(table, row, col):
        widget = table.cellWidget(row, col)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        item = table.item(row, col)
        return item.text() if item else ""

    def create_tests_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        hint = QLabel(
            "Assertions run after every response and in the collection runner. "
            "JSON paths look like $.data.items[0].id (add .length for a count). "
            "{{VARS}} work in Property and Expected."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        layout.addWidget(hint)

        self.tests_table = self._make_rule_table(["", "Source", "Property (header / JSON path)", "Operator", "Expected"])
        layout.addWidget(self.tests_table)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("Add Assertion")
        add_btn.clicked.connect(lambda: self._add_test_row())
        presets_btn = QToolButton()
        presets_btn.setText("Quick add ▾")
        presets_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        presets = QMenu(presets_btn)
        presets.addAction("Status is 200", lambda: self._add_test_row("Status code", "", "equals", "200"))
        presets.addAction("Status is 2xx", lambda: self._add_test_row("Status code", "", "matches regex", r"^2\d\d$"))
        presets.addAction("Response time < 1000 ms", lambda: self._add_test_row("Response time (ms)", "", "<", "1000"))
        presets.addAction("Content-Type is JSON", lambda: self._add_test_row("Header", "Content-Type", "contains", "json"))
        presets.addAction("JSON field exists", lambda: self._add_test_row("JSON path", "$.id", "exists", ""))
        presets_btn.setMenu(presets)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(lambda: self._remove_selected_rows(self.tests_table))
        toolbar.addWidget(add_btn)
        toolbar.addWidget(presets_btn)
        toolbar.addStretch()
        toolbar.addWidget(remove_btn)
        layout.addLayout(toolbar)
        return widget

    def _add_test_row(self, source="Status code", prop="", operator="equals", expected="200", enabled=True):
        row = self.tests_table.rowCount()
        self.tests_table.insertRow(row)
        self.tests_table.setItem(row, 0, self._check_item(enabled))
        self.tests_table.setCellWidget(row, 1, self._combo_cell(ASSERTION_SOURCES, source))
        self.tests_table.setItem(row, 2, QTableWidgetItem(prop))
        self.tests_table.setCellWidget(row, 3, self._combo_cell(ASSERTION_OPERATORS, operator))
        self.tests_table.setItem(row, 4, QTableWidgetItem(expected))

    def _tests_snapshot(self):
        rows = []
        for row in range(self.tests_table.rowCount()):
            check = self.tests_table.item(row, 0)
            rows.append({
                "enabled": check is None or check.checkState() == Qt.CheckState.Checked,
                "source": self._cell_text(self.tests_table, row, 1),
                "property": self._cell_text(self.tests_table, row, 2),
                "operator": self._cell_text(self.tests_table, row, 3),
                "expected": self._cell_text(self.tests_table, row, 4),
            })
        return rows

    def _restore_tests(self, rows):
        self.tests_table.setRowCount(0)
        for r in rows or []:
            if isinstance(r, dict):
                self._add_test_row(r.get("source", "Status code"), r.get("property", ""),
                                   r.get("operator", "equals"), str(r.get("expected", "")),
                                   bool(r.get("enabled", True)))

    def create_capture_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        hint = QLabel(
            "After each response, matched values are saved into the active environment so later "
            "requests can use {{VARIABLE}}. Example: variable TOKEN, source JSON path, "
            "expression $.access_token. Body regex uses the first capture group."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        layout.addWidget(hint)

        self.captures_table = self._make_rule_table(["", "Variable", "Source", "Expression"])
        layout.addWidget(self.captures_table)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("Add Capture")
        add_btn.clicked.connect(lambda: self._add_capture_row())
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(lambda: self._remove_selected_rows(self.captures_table))
        toolbar.addWidget(add_btn)
        toolbar.addStretch()
        toolbar.addWidget(remove_btn)
        layout.addLayout(toolbar)
        return widget

    def _add_capture_row(self, variable="", source="JSON path", expression="$.", enabled=True):
        row = self.captures_table.rowCount()
        self.captures_table.insertRow(row)
        self.captures_table.setItem(row, 0, self._check_item(enabled))
        self.captures_table.setItem(row, 1, QTableWidgetItem(variable))
        self.captures_table.setCellWidget(row, 2, self._combo_cell(CAPTURE_SOURCES, source))
        self.captures_table.setItem(row, 3, QTableWidgetItem(expression))

    def _captures_snapshot(self):
        rows = []
        for row in range(self.captures_table.rowCount()):
            check = self.captures_table.item(row, 0)
            rows.append({
                "enabled": check is None or check.checkState() == Qt.CheckState.Checked,
                "variable": self._cell_text(self.captures_table, row, 1),
                "source": self._cell_text(self.captures_table, row, 2),
                "expression": self._cell_text(self.captures_table, row, 3),
            })
        return rows

    def _restore_captures(self, rows):
        self.captures_table.setRowCount(0)
        for r in rows or []:
            if isinstance(r, dict):
                self._add_capture_row(r.get("variable", ""), r.get("source", "JSON path"),
                                      r.get("expression", ""), bool(r.get("enabled", True)))

    def _apply_captures(self, captures, response):
        """Store captured values in the active environment. Returns a status note."""
        if not captures:
            return ""
        values, problems = extract_captures(captures, response)
        note = ""
        if values:
            self.main.get_active_env().update(values)
            self.main.persist_envs()
            note = f" — captured {', '.join(values)}"
        if problems:
            note += f" — capture failed: {problems[0]}" + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
        return note

    def _render_test_results(self, results):
        """Show assertion results in the response Tests tab. Returns (passed, total) or None."""
        idx = self.response_tabs.indexOf(self.response_tests)
        if not results:
            self.response_tabs.setTabText(idx, "Tests")
            self.response_tests.setHtml("<p style='color:#888;'>No assertions on this request. "
                                        "Add them on the request's Tests tab.</p>")
            return None
        passed = sum(1 for r in results if r["passed"])
        self.response_tabs.setTabText(idx, f"Tests ({passed}/{len(results)})")
        lines = []
        for r in results:
            icon, color = ("✔", "#28a745") if r["passed"] else ("✘", "#dc3545")
            msg = f" <span style='color:#888;'>— {_html.escape(r['message'])}</span>" if r["message"] else ""
            lines.append(f"<div style='margin:3px 0;'><b style='color:{color};'>{icon}</b> "
                         f"{_html.escape(r['name'])}{msg}</div>")
        self.response_tests.setHtml("".join(lines))
        return passed, len(results)

    # ---------- Response diff ----------
    @staticmethod
    def _diff_text(response):
        ct = (response.headers.get("Content-Type", "") or "").lower()
        if any(x in ct for x in ("image/", "audio/", "video/", "octet-stream", "application/pdf", "application/zip")):
            return f"[binary body: {ct}, {len(response.content or b''):,} bytes]"
        return response.text or ""

    def _push_diff_response(self, label, body_text):
        """Make the newest response 'current' and the previous one the baseline."""
        if self._diff_current is not None:
            self._diff_baseline = self._diff_current
        self._diff_current = (label, body_text)
        self._render_diff()

    def show_diff_against(self, label, body_text):
        """Compare this tab's latest response with an arbitrary baseline (e.g. from history)."""
        if self._diff_current is None:
            QMessageBox.information(self, "Compare Responses", "Send a request in this tab first, then compare.")
            return
        self._diff_baseline = (label, body_text or "")
        self._render_diff()
        self.response_tabs.setCurrentWidget(self._diff_widget)

    def _render_diff(self, *_args):
        if not self._diff_baseline or not self._diff_current:
            self.diff_label.setText("Send this request again to compare responses.")
            self.response_diff.clear()
            return
        sort_keys = self.diff_sort_keys_check.isChecked()
        (old_label, old_body), (new_label, new_body) = self._diff_baseline, self._diff_current
        old_norm = normalize_for_diff(old_body, sort_keys)
        new_norm = normalize_for_diff(new_body, sort_keys)
        if old_norm == new_norm:
            self.diff_label.setText(f"{old_label}  →  {new_label}: no differences")
            self.response_diff.setHtml("<p style='color:#28a745;'>The responses are identical.</p>")
            return
        body, added, removed = diff_html(old_norm, new_norm, old_label, new_label)
        self.diff_label.setText(f"{old_label}  →  {new_label}:  +{added} / −{removed} lines")
        self.response_diff.setHtml(body)

    # ---------- GraphQL ----------
    def open_graphql_schema(self):
        req = self._build_request_dict()
        req["body_type"] = "GraphQL"
        req["body"] = GRAPHQL_INTROSPECTION_QUERY
        req["graphql_variables"] = ""
        raw_timeout = self.timeout_spin.value()
        try:
            call = prepare_request(req, self.main.get_active_env(), raw_timeout or None,
                                   oauth_token=self._ensure_oauth_token)
        except RequestBuildError as e:
            QMessageBox.warning(self, e.title, str(e))
            return
        dlg = GraphQLSchemaDialog(call, self.main.cookie_jar, self)
        dlg.operationChosen.connect(self._insert_graphql_operation)
        dlg.show()

    def _insert_graphql_operation(self, query, variables):
        self.body_type_combo.setCurrentText("GraphQL")
        self.body_text.setPlainText(query)
        self.graphql_variables_text.setPlainText(json.dumps(variables, indent=2) if variables else "")
        self.request_tabs.setCurrentIndex(2)

    # ---------- WebSocket / SSE ----------
    def open_realtime_console(self):
        env = self.main.get_active_env()
        dlg = RealtimeConsoleDialog(
            apply_env(self.url_input.text().strip(), env),
            self.headers_text.toPlainText(),
            env,
            self._advanced_snapshot().get("verify_ssl", True),
            self,
        )
        dlg.show()

    # ---------- Post-response scripts ----------
    def create_scripts_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.scripts_group = QGroupBox("Run after each response for this request")
        self.scripts_group.setCheckable(True)
        self.scripts_group.setChecked(False)
        sl = QVBoxLayout(self.scripts_group)

        self.post_response_script_editor = QPlainTextEdit()
        self.post_response_script_editor.setFont(_monospace_font(9))
        self.post_response_script_editor.setPlaceholderText(_POST_RESPONSE_SCRIPT_TEMPLATE)
        self.post_response_script_editor.setMinimumHeight(160)
        sl.addWidget(self.post_response_script_editor)

        btn_row = QHBoxLayout()
        verify_btn = QPushButton("Verify Script")
        verify_btn.setFixedHeight(30)
        verify_btn.clicked.connect(self._verify_post_response_script)
        template_btn = QPushButton("Load Template")
        template_btn.setFixedHeight(30)
        template_btn.clicked.connect(
            lambda: self.post_response_script_editor.setPlainText(_POST_RESPONSE_SCRIPT_TEMPLATE)
        )
        self.post_response_script_status = QLabel("")
        btn_row.addWidget(verify_btn)
        btn_row.addWidget(template_btn)
        btn_row.addWidget(self.post_response_script_status, 1)
        sl.addLayout(btn_row)

        layout.addWidget(self.scripts_group)
        layout.addStretch()
        return widget

    def _compile_post_response_script(self, code):
        return compile_post_response_script(code)

    def _verify_post_response_script(self):
        code = self.post_response_script_editor.toPlainText().strip()
        if not code:
            self.post_response_script_status.setStyleSheet("color:#888;")
            self.post_response_script_status.setText("(empty — no script will run)")
            return
        try:
            self._compile_post_response_script(code)
            self.post_response_script_status.setStyleSheet("color:#28a745;")
            self.post_response_script_status.setText("OK — found on_response()")
        except Exception as e:
            self.post_response_script_status.setStyleSheet("color:#dc3545;")
            self.post_response_script_status.setText(f"Error: {e}")

    def _run_post_response_script(self, response, elapsed):
        if not getattr(self, "scripts_group", None) or not self.scripts_group.isChecked():
            return
        code = self.post_response_script_editor.toPlainText().strip()
        if not code:
            return
        try:
            fn = self._compile_post_response_script(code)
            env = self.main.get_active_env()
            fn(_ScriptResponse(response, elapsed * 1000), env)
            self.main.persist_envs()
        except Exception as e:
            QMessageBox.critical(self, "Post-response Script Error", str(e))

    def _scripts_snapshot(self):
        return {
            "enabled": self.scripts_group.isChecked() if hasattr(self, "scripts_group") else False,
            "post_response": self.post_response_script_editor.toPlainText() if hasattr(self, "post_response_script_editor") else "",
        }

    def _restore_scripts(self, data):
        data = data or {}
        if hasattr(self, "scripts_group"):
            self.scripts_group.setChecked(bool(data.get("enabled", False)))
        if hasattr(self, "post_response_script_editor"):
            self.post_response_script_editor.setPlainText(data.get("post_response", ""))

    def _save_response_body_bytes(self):
        if not self._last_response:
            QMessageBox.information(self, "No Response", "Send a request first.")
            return

        resp = self._last_response
        body = self._last_response_bytes or b""

        preferred_output = (self._last_output_file or "").strip()
        if preferred_output == "-":
            preferred_output = ""
        suggested = preferred_output or self._infer_filename_from_headers(resp)
        ct = resp.headers.get("Content-Type", "")
        if not suggested:
            parsed = urlparse(getattr(resp.request, "url", "") or self.url_input.text().strip())
            base_name = os.path.basename(parsed.path) or "response"
            ext = os.path.splitext(base_name)[1]
            if not ext:
                ext = self._infer_ext_from_content_type(ct) or ".bin"
            suggested = base_name + ("" if base_name.endswith(ext) else ext)

        suggested_path = Path(os.path.expanduser(suggested))
        if not suggested_path.is_absolute():
            base_dir = Path.cwd() if preferred_output else Path.home()
            suggested_path = base_dir / suggested_path

        fname, _ = QFileDialog.getSaveFileName(self, "Save Response Body", str(suggested_path), "All Files (*)")
        if not fname:
            return
        try:
            Path(fname).write_bytes(body)
            QMessageBox.information(self, "Saved", f"Saved {len(body):,} bytes to:\n{fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _extract_and_save_base64(self):
        text = self.response_raw.toPlainText()
        data_bytes = None
        ext = ""

        parsed_json = None
        try:
            parsed_json = json.loads(text)
        except Exception:
            parsed_json = None

        if parsed_json is not None:
            data_bytes, ext = self._extract_base64_from_json(parsed_json)

        if data_bytes is None and isinstance(text, str):
            s = text.strip()
            if s.startswith("data:") and ";base64," in s:
                try:
                    header, b64 = s.split(";base64,", 1)
                    mime = header[5:]
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
        suggested = f"curlpypro-{host}-{time.strftime('%Y%m%d-%H%M%S')}.har"
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
                "creator": {"name": "CurlPyPro", "version": "1.0"},
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
        cd = response.headers.get("Content-Disposition", "")
        if "filename*" in cd:
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
        if not s or any(c.isspace() for c in s):
            s = s.strip()
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        if not s or any(c not in allowed for c in s):
            return False
        return (len(s) % 4) == 0

    def _extract_base64_from_json(self, obj):
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
                if s.startswith("data:") and ";base64," in s:
                    try:
                        header, b64 = s.split(";base64,", 1)
                        mime = header[5:]
                        data = base64.b64decode(b64)
                        ext = self._infer_ext_from_content_type(mime)
                        candidates.append((data, ext))
                        return
                    except Exception:
                        pass
                key_is_common = key_hint in {"data", "content", "file", "base64", "blob", "value", "document", "image", "pdf"}
                if key_is_common and self._is_probably_base64(s):
                    try:
                        data = base64.b64decode(s)
                        if "pdf" in key_hint:
                            ext = ".pdf"
                        elif "image" in key_hint or "png" in key_hint or "jpg" in key_hint or "jpeg" in key_hint:
                            ext = ""
                        else:
                            ext = ""
                        candidates.append((data, ext))
                    except Exception:
                        pass

        walker(obj)
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
        # The worker emits its result just before QThread.isRunning() becomes
        # false, so using isRunning() here can leave the busy indicator visible
        # forever after the final request.  Track the logical request state
        # instead.
        running = sum(
            1 for store in self._request_store.values()
            if store.get('running', False)
        )
        if running > 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setVisible(True)
        else:
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)

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
            self._render_test_results(store.get('tests'))
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
        self._last_output_file = result.get('output_file')

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
        stripped_text = raw_text.lstrip()
        is_html = (
            "text/html" in content_type
            or "application/xhtml+xml" in content_type
            or stripped_text.lower().startswith(("<!doctype html", "<html"))
        )
        is_xml = (
            not is_html
            and ("xml" in content_type or stripped_text.startswith("<?xml"))
        )
        image_types = ["image/png", "image/jpeg", "image/jpg", "image/webp",
                       "image/gif", "image/bmp", "image/tiff"]

        if any(x in content_type for x in image_types):
            self.response_preview_stack.setCurrentWidget(self.response_preview_scroll)
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            if not pixmap.isNull():
                w = max(self.response_preview_label.width(), 400)
                h = max(self.response_preview_label.height(), 400)
                scaled = pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                self.response_preview_label.setPixmap(scaled)
                self.response_tabs.setCurrentWidget(self.response_preview_stack)
            else:
                self.response_preview_label.clear()
                self.response_preview_label.setText("Could not decode image")
        elif is_html:
            if QWebEngineView is not None:
                self.response_preview_html.setHtml(raw_text, QUrl(response.url))
            else:
                self.response_preview_html.document().setBaseUrl(QUrl(response.url))
                self.response_preview_html.setHtml(raw_text)
            self.response_preview_stack.setCurrentWidget(self.response_preview_html)
        else:
            self.response_preview_stack.setCurrentWidget(self.response_preview_scroll)
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
        elif "application/json" in content_type or stripped_text.startswith(("{", "[")):
            try:
                if self.main.settings.get("auto_format_json", True):
                    self.response_pretty.setPlainText(json.dumps(response.json(), indent=2))
                else:
                    self.response_pretty.setPlainText(raw_text)
            except Exception:
                self.response_pretty.setPlainText(raw_text)
        elif is_html:
            self.response_pretty.setPlainText(_pretty_html(raw_text))
        elif is_xml:
            self.response_pretty.setPlainText(_pretty_xml(raw_text))
        else:
            self.response_pretty.setPlainText(raw_text)

        # QTextEdit may preserve the old scroll offsets across setPlainText().
        # Always reveal the beginning of a newly loaded result.
        for editor in (
            self.response_pretty,
            self.response_raw,
            self.response_headers,
            self.response_insights,
        ):
            editor.moveCursor(editor.textCursor().MoveOperation.Start)
            editor.ensureCursorVisible()
            editor.horizontalScrollBar().setValue(0)

        self.response_tabs.setCurrentWidget(self.response_pretty)

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
        self.file_attachments = []
        if hasattr(self, "attachments_list"):
            self.attachments_list.clear()

    def restore_attachments(self, attachments):
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
        return [dict(att) for att in self.file_attachments]

    # ---------- Events ----------
    def on_body_type_changed(self, text):
        if text == "JSON":
            self.body_text.setPlaceholderText('{\n  "key": "value"\n}')
        elif text == "Form Data":
            self.body_text.setPlaceholderText("key=value&other=one  (for text fields)\nUse Attachments below for files.")
        elif text == "Binary":
            self.body_text.setPlaceholderText("(Binary payload - use Save/Load to manipulate file)")
        elif text == "GraphQL":
            self.body_text.setPlaceholderText("query GetUser($id: ID!) {\n  user(id: $id) {\n    id\n    name\n  }\n}")
        else:
            self.body_text.setPlaceholderText('{\n  "key": "value",\n  "user": "{{USERNAME}}"\n}')

        show_attachments = (text == "Form Data")
        if hasattr(self, "attachments_group"):
            self.attachments_group.setVisible(show_attachments)
        if hasattr(self, "graphql_group"):
            self.graphql_group.setVisible(text == "GraphQL")

    def _update_method_color(self, *_args):
        colors = {
            "GET": "#28a745", "POST": "#fd7e14", "PUT": "#0d6efd",
            "PATCH": "#6f42c1", "DELETE": "#dc3545", "HEAD": "#6c757d",
            "OPTIONS": "#6c757d",
        }
        method = self.method_combo.currentText()
        color = colors.get(method, "#6c757d")
        self.method_combo.setStyleSheet(f"QComboBox {{ color: {color}; font-weight: bold; }}")

    def _clear_pending_output_file(self, *_args):
        self._pending_output_file = None

    def import_curl(self, text):
        """Populate the request form from a pasted curl command."""
        parsed = parse_curl_command(text)
        if not parsed.get("url"):
            self.url_input.setText(" ".join(text.split()))
            self.main.status_bar.showMessage("Couldn't parse curl command — pasted as text")
            return False

        header_pairs = list(parsed["headers"])

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

        auth_cfg = {"type": "No Auth"}
        kept_pairs = []
        for k, v in header_pairs:
            if (k.lower() == "authorization" and auth_cfg["type"] == "No Auth"
                    and v.strip().lower().startswith("bearer ")):
                auth_cfg = {"type": "Bearer Token", "token": v.strip()[7:].strip()}
                continue
            kept_pairs.append((k, v))
        if parsed["user"]:
            user, _, pwd = parsed["user"].partition(":")
            auth_cfg = {"type": "Basic Auth", "username": user, "password": pwd}
        self.set_auth_config(auth_cfg)

        self.headers_text.setPlainText(
            "\n".join(f"{k}: {v}" for k, v in kept_pairs)
        )

        self._pending_output_file = (parsed.get("output_file") or "").strip() or None
        status_notes = []
        if self._pending_output_file and self._pending_output_file != "-":
            status_notes.append(f"output: {self._pending_output_file}")

        self.clear_attachments()
        content_type = next(
            (v for k, v in kept_pairs if k.lower() == "content-type"), ""
        ).lower()
        if parsed["is_form"]:
            self.body_type_combo.setCurrentText("Form Data")
            text_fields = []
            file_attachments = []
            for form_value in parsed["form"]:
                kind, payload = parse_curl_form_value(form_value)
                if kind == "file":
                    file_attachments.append(payload)
                else:
                    text_fields.append(payload)
            self.body_text.setPlainText("&".join(text_fields))
            if file_attachments:
                self.restore_attachments(file_attachments)
                status_notes.append(
                    f"{len(file_attachments)} file field"
                    f"{'s' if len(file_attachments) != 1 else ''}"
                )
                missing = sum(
                    1 for att in file_attachments
                    if not (att.get("path") and os.path.exists(att["path"]))
                )
                if missing:
                    status_notes.append(f"{missing} missing")
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
        else:
            self.body_text.setPlainText("")

        if imported_param_count:
            status_notes.append(
                f"{imported_param_count} param"
                f"{'s' if imported_param_count != 1 else ''}"
            )
        note = f" ({'; '.join(status_notes)})" if status_notes else ""
        self.main.status_bar.showMessage(f"Imported curl — {method} {parsed['url'][:60]}{note}")

        self._update_method_color()
        self.main._update_tab_title(self)
        return True

    def send_request(self):
        if looks_like_curl(self.url_input.text()):
            if not self.import_curl(self.url_input.text()):
                QMessageBox.warning(
                    self,
                    "Invalid cURL",
                    "That looks like a cURL command, but CurlPyPro couldn't find a URL in it.",
                )
                return

        env = self.main.get_active_env()
        raw_timeout = self.timeout_spin.value()
        timeout = None if raw_timeout == 0 else raw_timeout
        try:
            call = prepare_request(self._build_request_dict(), env, timeout,
                                   oauth_token=self._ensure_oauth_token)
        except RequestBuildError as e:
            if e.critical:
                QMessageBox.critical(self, e.title, str(e))
            else:
                QMessageBox.warning(self, e.title, str(e))
            return
        method = call["method"]
        url = call["url"]

        self.main._update_tab_title(self)

        advanced = self._advanced_snapshot()
        snapshot = {
            'req_url': self.url_input.text(),
            'req_headers_text': self.headers_text.toPlainText(),
            'params': self._params_snapshot(),
            'body_type': self.body_type_combo.currentText(),
            'request_body': self.body_text.toPlainText(),
            'graphql_variables': self.graphql_variables_text.toPlainText(),
            'auth': self.get_auth_config(),
            'advanced': dict(advanced),
            'attachments': self._attachments_snapshot(),
            'output_file': self._pending_output_file,
            'scripts': self._scripts_snapshot(),
            'tests': self._tests_snapshot(),
            'captures': self._captures_snapshot(),
        }

        req_id = self._req_counter
        self._req_counter += 1

        thread = RequestThread(
            method, url, call["headers"], call["json_body"], call["data"], call["files"], timeout,
            params=call["params"], auth=call["auth"],
            allow_redirects=call["allow_redirects"],
            verify_ssl=call["verify_ssl"],
            max_redirects=call["max_redirects"],
            retry_total=call["retry_total"],
            retry_backoff=call["retry_backoff"],
            retry_statuses=call["retry_statuses"],
            cookie_jar=self.main.cookie_jar if call["use_cookie_jar"] else None,
            proxies=call["proxies"],
        )
        self._request_store[req_id] = {
            'thread': thread,
            'info': {'method': method, 'url': url},
            'snapshot': snapshot,
            'result': None,
            'error': None,
            'elapsed': None,
            'running': True,
        }
        thread.finished.connect(lambda result, rid=req_id: self.on_request_finished(result, rid))
        thread.error.connect(lambda err, rid=req_id: self.on_request_error(err, rid))
        thread.start()

        self._update_active_table()
        self._refresh_progress()
        self.main.status_bar.showMessage(f"Request #{req_id + 1} sent — {method} {url[:60]}")

    def on_request_finished(self, result, req_id):
        store = self._request_store.get(req_id, {})
        if store:
            store['running'] = False
            store['result'] = result
            store['elapsed'] = result['elapsed'] * 1000

        self._update_active_table()
        self._refresh_progress()

        response = result['response']
        elapsed = result['elapsed']
        snapshot = store.get('snapshot', {}) if store else {}
        result['output_file'] = snapshot.get('output_file')

        self._load_response_to_ui(result)
        capture_note = self._apply_captures(snapshot.get('captures'), response)
        self._run_post_response_script(response, elapsed)

        test_results = evaluate_assertions(
            snapshot.get('tests'), response, elapsed * 1000, self.main.get_active_env()
        )
        if store:
            store['tests'] = test_results
        test_summary = self._render_test_results(test_results)
        test_note = ""
        if test_summary:
            passed, total = test_summary
            test_note = f" — tests {passed}/{total} passed"
            self.response_summary.setText(self.response_summary.text() + f" — Tests: {passed}/{total} passed")
            if passed < total:
                self.response_tabs.setCurrentWidget(self.response_tests)

        self._push_diff_response(
            f"#{req_id + 1} ({response.status_code}, {time.strftime('%H:%M:%S')})",
            self._diff_text(response),
        )

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
                "output_file": snapshot.get('output_file'),
                "scripts": snapshot.get('scripts', self._scripts_snapshot()),
                "graphql_variables": snapshot.get('graphql_variables', ""),
                "tests": snapshot.get('tests', []),
                "captures": snapshot.get('captures', []),
            }
            self.main.record_history(entry)
        except Exception:
            pass

        status_code = response.status_code
        self.main.status_bar.showMessage(
            f"Request #{req_id + 1} done: {status_code} ({elapsed*1000:.0f} ms){test_note}{capture_note}"
        )

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
            store['running'] = False
            store['error'] = error_message
            store['elapsed'] = 0

        self._update_active_table()
        self._refresh_progress()

        snapshot = store.get('snapshot', {}) if store else {}
        self.main.status_bar.showMessage(f"Request #{req_id + 1} failed: {error_message[:60]}")
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
                "output_file": snapshot.get('output_file'),
                "scripts": snapshot.get('scripts', self._scripts_snapshot()),
                "graphql_variables": snapshot.get('graphql_variables', ""),
                "tests": snapshot.get('tests', []),
                "captures": snapshot.get('captures', []),
            }
            self.main.record_history(entry)
        except Exception:
            pass

    # ---------- Save/Load ----------
    def _build_request_dict(self):
        return {
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
            "output_file": self._pending_output_file,
            "scripts": self._scripts_snapshot(),
            "graphql_variables": self.graphql_variables_text.toPlainText(),
            "tests": self._tests_snapshot(),
            "captures": self._captures_snapshot(),
        }

    def save_to_collection(self):
        dlg = SaveToCollectionDialog(sorted(self.main.collections.keys()), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.collection_name()
        if not name:
            return
        coll = self.main.collections.setdefault(name, [])
        coll.append(self._build_request_dict())
        save_document("collections", self.main.collections)
        self.main.reload_collections()
        QMessageBox.information(self, "Saved", f"Request saved to collection '{name}'")

    def save_request_file(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save Request", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        try:
            Path(fname).write_text(json.dumps(self._build_request_dict(), indent=2))
            QMessageBox.information(self, "Saved", f"Request saved to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def load_request_file(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Load Request", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        try:
            data = json.loads(Path(fname).read_text())
            self.apply_saved_request(data)
            QMessageBox.information(self, "Loaded", f"Request loaded from {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def apply_saved_request(self, req):
        self.method_combo.setCurrentText(req.get("method", "GET"))
        self.url_input.setText(req.get("url", ""))
        self.headers_text.setPlainText(req.get("headers", ""))
        self._restore_params(req.get("params", []))
        self.body_type_combo.setCurrentText(req.get("body_type", "Raw"))
        self.body_text.setPlainText(req.get("body", ""))
        self.restore_attachments(req.get("attachments", []))
        self.set_auth_config(req.get("auth", {}))
        self._restore_advanced(req.get("advanced", {}))
        self._pending_output_file = req.get("output_file") or None
        self._restore_scripts(req.get("scripts", {}))
        self.graphql_variables_text.setPlainText(req.get("graphql_variables", "") or "")
        self._restore_tests(req.get("tests", []))
        self._restore_captures(req.get("captures", []))
        self.main._update_tab_title(self)

    def apply_history_entry(self, data):
        self.method_combo.setCurrentText(data.get("method", "GET"))
        self.url_input.setText(data.get("req_url") or data.get("url", ""))

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

        if data.get("body_type"):
            self.body_type_combo.setCurrentText(data.get("body_type"))
        self.body_text.setPlainText(data.get("request_body", ""))

        self.restore_attachments(data.get("attachments", []))

        if "auth" in data:
            self.set_auth_config(data.get("auth", {}))
        if "advanced" in data:
            self._restore_advanced(data.get("advanced", {}))
        if "scripts" in data:
            self._restore_scripts(data.get("scripts", {}))
        self.graphql_variables_text.setPlainText(data.get("graphql_variables", "") or "")
        if "tests" in data:
            self._restore_tests(data.get("tests", []))
        if "captures" in data:
            self._restore_captures(data.get("captures", []))

        self._pending_output_file = data.get("output_file") or None
        self.main._update_tab_title(self)

    def to_dict(self):
        return self._build_request_dict()

    def from_dict(self, data):
        self.apply_saved_request(data)

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

    # ---------- Snippets ----------
    _AUTO_HEADERS = {"content-length", "host", "connection"}

    def _snippet_headers(self, headers: dict, drop_content_type: bool = False) -> dict:
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
        fields = {}
        if body_raw and self.body_type_combo.currentText() == "Form Data":
            for pair in body_raw.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    fields[k] = v
        return fields

    def _snippet_context(self):
        env = self.main.get_active_env()
        method = self.method_combo.currentText()
        url = apply_env(self.url_input.text().strip(), env)
        headers = {}
        for line in self.headers_text.toPlainText().splitlines():
            line = line.strip()
            if line and ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = apply_env(value.strip(), env)
        body_raw = apply_env(self.body_text.toPlainText().strip(), env)
        if self.body_type_combo.currentText() == "GraphQL":
            try:
                payload = build_graphql_payload(body_raw, self.graphql_variables_text.toPlainText(), env)
            except RequestBuildError:
                payload = {"query": body_raw}
            body_raw = json.dumps(payload)
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"

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
        parts = ["curl.exe", "-i"]
        proxies = self._resolved_proxies(self.main.get_active_env())
        if proxies:
            parts += ["--proxy", shlex.quote(proxies.get("https") or proxies["http"])]
            if proxies.get("no_proxy"):
                parts += ["--noproxy", shlex.quote(proxies["no_proxy"])]
        if method != "GET":
            parts += ["-X", method]
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

        output_file = (self._pending_output_file or "").strip()
        if output_file and output_file != "-":
            parts += ["--output", shlex.quote(output_file)]

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

        proxies = self._resolved_proxies(self.main.get_active_env())
        proxy_kw = ""
        if proxies:
            lines.append("# requests ignores Windows PAC/system proxy settings, so pass it explicitly.")
            lines.append("proxies = {")
            for k, v in proxies.items():
                lines.append(f"    {json.dumps(k)}: {json.dumps(v)},")
            lines.append("}")
            lines.append("")
            proxy_kw = ", proxies=proxies"

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
            lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers, data=data, files=files{proxy_kw})")
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
                lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers, json=json_payload{proxy_kw})")
            elif body_raw:
                lines.append("data = " + json.dumps(body_raw))
                lines.append("")
                lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers, data=data{proxy_kw})")
            else:
                lines.append(f"resp = requests.{method.lower()}({json.dumps(url)}, headers=headers{proxy_kw})")
        lines.append("")
        lines.append("print(resp.status_code)")
        lines.append("print(resp.headers)")
        lines.append("print(resp.text)")
        return "\n".join(lines)

    def generate_powershell(self, method, url, headers, body_raw, attachments):
        method = method.upper()
        url_ps = url.replace("'", "''")
        lines = []

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
            lines.append(
                f"Invoke-RestMethod -Uri '{url_ps}' -Method {method} -Headers $headers -Form $form"
            )
            return "\n".join(lines)

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

        lines.append(" `\n    ".join(cmd_parts))
        lines.append("")
        lines.append("# Use Invoke-WebRequest instead if you need the raw response stream/status.")
        return "\n".join(lines)

    def generate_java(self, method, url, headers, body_raw, attachments):
        method = method.upper()

        def js(s):
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
                body_out = json.dumps(json.loads(body_raw))
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
        env = self.main.get_active_env()

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

        if body_type == "GraphQL":
            try:
                json_body = build_graphql_payload(body_raw, self.graphql_variables_text.toPlainText(), env)
            except RequestBuildError as e:
                QMessageBox.warning(self, e.title, str(e))
                return
            headers.setdefault("Content-Type", "application/json")
        elif body_type == "JSON" or "application/json" in content_type or (body_raw and body_raw.startswith(("{", "["))):
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
            'cookie_jar': self.main.cookie_jar if advanced.get("use_cookie_jar", True) else None,
            'proxies': self._resolved_proxies(env),
        }

        dlg = StressTestDialog(config, self)
        dlg.show()


# Threads still running when their dialog closes are parked here until they
# finish, so Qt never destroys a QThread that is still executing.
_detached_threads = set()


def _keep_thread_alive(thread):
    if thread is None or not thread.isRunning():
        return
    _detached_threads.add(thread)
    thread.finished.connect(lambda t=thread: _detached_threads.discard(t))
    thread.setParent(None)


def resolve_env_in_config(cfg, env):
    """Resolve {{VAR}} in every string value of an auth config."""
    return {k: apply_env(v, env) if isinstance(v, str) else v for k, v in (cfg or {}).items()}


def compile_post_response_script(code):
    ns = {}
    exec(compile(code, '<post_response_script>', 'exec'), ns)
    fn = ns.get('on_response')
    if not callable(fn):
        raise ValueError("Script must define on_response(response, env)")
    return fn


# ---------------------------
# Collection runner
# ---------------------------
class CollectionRunWorker(QThread):
    """Runs a list of saved requests in order, with captures, scripts and assertions."""

    result = pyqtSignal(dict)
    variables = pyqtSignal(dict)

    def __init__(self, requests_list, env, iterations=1, delay_ms=0, stop_on_failure=False,
                 timeout=30, cookie_jar=None, run_scripts=True):
        super().__init__()
        self.requests_list = requests_list
        self.env = env  # a private copy; changes are reported through `variables`
        self.iterations = max(1, iterations)
        self.delay_ms = delay_ms
        self.stop_on_failure = stop_on_failure
        self.timeout = timeout
        self.cookie_jar = cookie_jar
        self.run_scripts = run_scripts
        self._stop = False
        self._tokens = {}

    def stop(self):
        self._stop = True

    def _oauth_token(self, cfg):
        if cfg.get("grant_type") == "Authorization Code":
            raise RuntimeError("Authorization Code tokens need a browser login; get one on the request's Auth tab first")
        cfg = resolve_env_in_config(cfg, self.env)
        key = (cfg.get("token_url"), cfg.get("client_id"), cfg.get("scope"), cfg.get("username"))
        cached = self._tokens.get(key)
        if cached and cached[1] > time.time():
            return cached[0]
        token, expires_in = fetch_oauth2_token(cfg)
        self._tokens[key] = (token, time.time() + max(0, expires_in - 30))
        return token

    def _run_script(self, req, response, elapsed, row):
        scripts = req.get("scripts") or {}
        code = (scripts.get("post_response") or "").strip()
        if not (self.run_scripts and scripts.get("enabled") and code):
            return
        before = dict(self.env)
        try:
            compile_post_response_script(code)(_ScriptResponse(response, elapsed * 1000), self.env)
        except Exception as e:
            row["notes"].append(f"Post-response script error: {e}")
        changed = {k: v for k, v in self.env.items() if before.get(k) != v}
        if changed:
            self.variables.emit(changed)

    def _sleep(self):
        end = time.time() + self.delay_ms / 1000.0
        while not self._stop and time.time() < end:
            time.sleep(0.05)

    def run(self):
        for iteration in range(1, self.iterations + 1):
            for index, req in enumerate(self.requests_list):
                if self._stop:
                    return
                row = {
                    "iteration": iteration, "index": index,
                    "name": req.get("name") or f"Request {index + 1}",
                    "method": (req.get("method") or "GET").upper(),
                    "url": req.get("url") or "", "status": None, "reason": "",
                    "elapsed_ms": None, "size": 0, "tests": [], "error": "", "notes": [],
                    "response_headers": {}, "response_body": "",
                }
                try:
                    def oauth(cfg):
                        try:
                            return self._oauth_token(cfg)
                        except Exception as e:
                            row["notes"].append(f"OAuth 2.0: {e}")
                            return None

                    call = prepare_request(req, self.env, self.timeout, oauth)
                    row["url"] = call["url"]
                    response, elapsed = execute_call(call, self.cookie_jar)
                    row.update(
                        status=response.status_code, reason=response.reason,
                        elapsed_ms=round(elapsed * 1000), size=len(response.content or b""),
                        response_headers=dict(response.headers),
                        response_body=(response.text or "")[:20000],
                    )
                    values, problems = extract_captures(req.get("captures"), response)
                    if values:
                        self.env.update(values)
                        self.variables.emit(values)
                    row["notes"].extend(problems)
                    self._run_script(req, response, elapsed, row)
                    row["tests"] = evaluate_assertions(req.get("tests"), response, elapsed * 1000, self.env)
                except RequestBuildError as e:
                    row["error"] = f"{e.title}: {e}"
                except Exception as e:
                    row["error"] = str(e)
                self.result.emit(row)

                failed = bool(row["error"]) or any(not t["passed"] for t in row["tests"])
                if failed and self.stop_on_failure:
                    return
                if self.delay_ms:
                    self._sleep()


class CollectionRunnerDialog(QDialog):
    def __init__(self, main_window, collection_name):
        super().__init__(main_window)
        self.main = main_window
        self.collection_name = collection_name
        self.requests_list = list(main_window.collections.get(collection_name, []))
        self.results = []
        self.worker = None
        self._started_at = 0
        self.setWindowTitle(f"Run Collection — {collection_name}")
        self.resize(1150, 720)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: run settings + request selection ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        settings_group = QGroupBox("Run settings")
        form = QVBoxLayout(settings_group)

        env_row = QHBoxLayout()
        env_row.addWidget(QLabel("Environment:"))
        self.env_combo = QComboBox()
        self.env_combo.addItems(list(self.main.envs.keys()))
        self.env_combo.setCurrentText(self.main.env_combo.currentText())
        env_row.addWidget(self.env_combo, 1)
        form.addLayout(env_row)

        iter_row = QHBoxLayout()
        iter_row.addWidget(QLabel("Iterations:"))
        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(1, 10000)
        iter_row.addWidget(self.iterations_spin)
        iter_row.addWidget(QLabel("Delay:"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 600000)
        self.delay_spin.setSuffix(" ms")
        iter_row.addWidget(self.delay_spin)
        form.addLayout(iter_row)

        self.stop_on_failure_check = QCheckBox("Stop on first failure")
        self.save_vars_check = QCheckBox("Save captured variables to the environment")
        self.save_vars_check.setChecked(True)
        self.run_scripts_check = QCheckBox("Run post-response scripts")
        self.run_scripts_check.setChecked(True)
        for w in (self.stop_on_failure_check, self.save_vars_check, self.run_scripts_check):
            form.addWidget(w)
        left_layout.addWidget(settings_group)

        requests_group = QGroupBox("Requests (run top to bottom)")
        rl = QVBoxLayout(requests_group)
        self.request_list = QListWidget()
        for req in self.requests_list:
            item = QListWidgetItem(f"{(req.get('method') or 'GET').upper()}  {req.get('name') or req.get('url', '')}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.request_list.addItem(item)
        rl.addWidget(self.request_list)
        sel_row = QHBoxLayout()
        all_btn = QPushButton("Select All")
        none_btn = QPushButton("Select None")
        all_btn.clicked.connect(lambda: self._set_all_checked(True))
        none_btn.clicked.connect(lambda: self._set_all_checked(False))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        rl.addLayout(sel_row)
        left_layout.addWidget(requests_group, 1)

        # --- Right: results ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.summary_label = QLabel("Choose requests and press Run.")
        self.summary_label.setWordWrap(True)
        right_layout.addWidget(self.summary_label)
        self.progress = QProgressBar()
        right_layout.addWidget(self.progress)

        self.results_table = QTableWidget(0, 7)
        self.results_table.setHorizontalHeaderLabels(["Iter", "#", "Request", "Status", "ms", "Tests", "Result"])
        hdr = self.results_table.horizontalHeader()
        for col in range(7):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.currentCellChanged.connect(lambda row, *_: self._show_details(row))

        self.details = QTextBrowser()
        results_splitter = QSplitter(Qt.Orientation.Vertical)
        results_splitter.addWidget(self.results_table)
        results_splitter.addWidget(self.details)
        results_splitter.setSizes([420, 240])
        right_layout.addWidget(results_splitter, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([340, 810])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.setObjectName("send_btn")
        self.run_btn.clicked.connect(self.start_run)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_run)
        self.export_btn = QPushButton("Export Results…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_results)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        for b in (self.run_btn, self.stop_btn, self.export_btn):
            b.setFixedHeight(34)
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.request_list.count()):
            self.request_list.item(i).setCheckState(state)

    def start_run(self):
        selected = [
            copy.deepcopy(req) for i, req in enumerate(self.requests_list)
            if self.request_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not selected:
            QMessageBox.information(self, "Run Collection", "Select at least one request.")
            return
        self.results = []
        self.results_table.setRowCount(0)
        self.details.clear()
        self.env_name = self.env_combo.currentText()
        iterations = self.iterations_spin.value()
        self.progress.setRange(0, len(selected) * iterations)
        self.progress.setValue(0)

        panel = self.main.current_panel()
        raw_timeout = panel.timeout_spin.value() if panel is not None else 30
        self.worker = CollectionRunWorker(
            selected,
            dict(self.main.envs.get(self.env_name, {})),
            iterations=iterations,
            delay_ms=self.delay_spin.value(),
            stop_on_failure=self.stop_on_failure_check.isChecked(),
            timeout=raw_timeout or None,
            cookie_jar=self.main.cookie_jar,
            run_scripts=self.run_scripts_check.isChecked(),
        )
        self.worker.result.connect(self._on_result)
        self.worker.variables.connect(self._on_variables)
        self.worker.finished.connect(self._on_finished)
        self._started_at = time.time()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.summary_label.setText("Running…")
        self.worker.start()

    def stop_run(self):
        if self.worker is not None:
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.summary_label.setText("Stopping after the current request…")

    @staticmethod
    def _row_outcome(row):
        if row["error"]:
            return "ERROR", QColor("#d9534f")
        if any(not t["passed"] for t in row["tests"]):
            return "FAIL", QColor("#dc3545")
        return "PASS", QColor("#28a745")

    def _on_result(self, row):
        self.results.append(row)
        r = self.results_table.rowCount()
        self.results_table.insertRow(r)
        passed = sum(1 for t in row["tests"] if t["passed"])
        outcome, color = self._row_outcome(row)
        cells = [
            str(row["iteration"]), str(row["index"] + 1), f"{row['method']} {row['name']}",
            "" if row["status"] is None else str(row["status"]),
            "" if row["elapsed_ms"] is None else str(row["elapsed_ms"]),
            f"{passed}/{len(row['tests'])}" if row["tests"] else "—",
            outcome,
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == 6:
                item.setForeground(color)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if col == 3 and row["status"] is not None:
                item.setForeground(self.main._status_color(row["status"]))
            self.results_table.setItem(r, col, item)
        self.results_table.scrollToBottom()
        self.progress.setValue(len(self.results))
        self._update_summary(running=True)

    def _on_variables(self, values):
        if not self.save_vars_check.isChecked():
            return
        env = self.main.envs.setdefault(self.env_name, {})
        env.update(values)
        self.main.persist_envs()

    def _on_finished(self):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(bool(self.results))
        self._update_summary(running=False)
        self.main.status_bar.showMessage(f"Collection run finished — {self.summary_label.text()}")

    def _update_summary(self, running):
        total_tests = sum(len(r["tests"]) for r in self.results)
        passed_tests = sum(1 for r in self.results for t in r["tests"] if t["passed"])
        errors = sum(1 for r in self.results if r["error"])
        failed_requests = sum(1 for r in self.results if self._row_outcome(r)[0] != "PASS")
        times = [r["elapsed_ms"] for r in self.results if r["elapsed_ms"] is not None]
        avg = f"{sum(times) / len(times):.0f} ms avg" if times else "—"
        prefix = "Running… " if running else ("Done. " if not failed_requests else "Finished with failures. ")
        self.summary_label.setText(
            f"{prefix}{len(self.results)} requests · {passed_tests}/{total_tests} tests passed · "
            f"{failed_requests} failed ({errors} errors) · {avg} · {time.time() - self._started_at:.1f} s"
        )

    def _show_details(self, row_index):
        if not 0 <= row_index < len(self.results):
            return
        row = self.results[row_index]
        esc = _html.escape
        parts = [f"<h3 style='margin:0;'>{esc(row['method'])} {esc(row['name'])}</h3>",
                 f"<p style='color:#888;'>{esc(row['url'])}</p>"]
        if row["error"]:
            parts.append(f"<p style='color:#d9534f;'><b>Error:</b> {esc(row['error'])}</p>")
        else:
            parts.append(f"<p><b>Status:</b> {row['status']} {esc(row['reason'] or '')} · "
                         f"<b>Time:</b> {row['elapsed_ms']} ms · <b>Size:</b> {row['size']:,} bytes</p>")
        for t in row["tests"]:
            icon, color = ("✔", "#28a745") if t["passed"] else ("✘", "#dc3545")
            msg = f" <span style='color:#888;'>— {esc(t['message'])}</span>" if t["message"] else ""
            parts.append(f"<div><b style='color:{color};'>{icon}</b> {esc(t['name'])}{msg}</div>")
        for note in row["notes"]:
            parts.append(f"<div style='color:#f0ad4e;'>⚠ {esc(note)}</div>")
        if row["response_body"]:
            parts.append(f"<pre>{esc(normalize_for_diff(row['response_body'], False)[:8000])}</pre>")
        self.details.setHtml("".join(parts))

    def export_results(self):
        fname, chosen = QFileDialog.getSaveFileName(
            self, "Export Run Results", str(Path.home() / f"{self.collection_name}-run.json"),
            "JSON report (*.json);;JUnit XML (*.xml)",
        )
        if not fname:
            return
        try:
            if fname.lower().endswith(".xml") or "JUnit" in chosen:
                Path(fname).write_text(self._junit_xml(), encoding="utf-8")
            else:
                report = {
                    "collection": self.collection_name,
                    "environment": self.env_name,
                    "finished_at": _dt.datetime.now().isoformat(timespec="seconds"),
                    "results": [{k: v for k, v in r.items() if k != "response_body"} for r in self.results],
                }
                Path(fname).write_text(json.dumps(report, indent=2), encoding="utf-8")
            self.main.status_bar.showMessage(f"Run results exported to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _junit_xml(self):
        from xml.sax.saxutils import escape, quoteattr
        failures = sum(1 for r in self.results if not r["error"] and any(not t["passed"] for t in r["tests"]))
        errors = sum(1 for r in self.results if r["error"])
        total_time = sum((r["elapsed_ms"] or 0) for r in self.results) / 1000.0
        suite = quoteattr(self.collection_name)
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<testsuites name={suite} tests="{len(self.results)}" failures="{failures}" '
            f'errors="{errors}" time="{total_time:.3f}">',
            f'  <testsuite name={suite} tests="{len(self.results)}" failures="{failures}" '
            f'errors="{errors}" time="{total_time:.3f}">',
        ]
        for r in self.results:
            name = quoteattr(f"{r['method']} {r['name']}")
            classname = quoteattr(f"{self.collection_name}.iteration{r['iteration']}")
            lines.append(f'    <testcase classname={classname} name={name} time="{(r["elapsed_ms"] or 0) / 1000.0:.3f}">')
            if r["error"]:
                lines.append(f'      <error message={quoteattr(r["error"])}/>')
            failed = [t for t in r["tests"] if not t["passed"]]
            if failed:
                text = "\n".join(f"{t['name']}: {t['message']}" for t in failed)
                lines.append(f'      <failure message={quoteattr(f"{len(failed)} assertion(s) failed")}>{escape(text)}</failure>')
            lines.append('    </testcase>')
        lines += ['  </testsuite>', '</testsuites>', '']
        return "\n".join(lines)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            _keep_thread_alive(self.worker)
        super().closeEvent(event)


# ---------------------------
# GraphQL schema browser
# ---------------------------
class CallWorker(QThread):
    """Sends one prepared call off the UI thread."""

    done = pyqtSignal(object, float)
    failed = pyqtSignal(str)

    def __init__(self, call, cookie_jar=None):
        super().__init__()
        self.call = call
        self.cookie_jar = cookie_jar

    def run(self):
        try:
            response, elapsed = execute_call(self.call, self.cookie_jar)
            self.done.emit(response, elapsed)
        except Exception as e:
            self.failed.emit(str(e))


class GraphQLSchemaDialog(QDialog):
    operationChosen = pyqtSignal(str, object)

    def __init__(self, call, cookie_jar, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GraphQL Schema")
        self.resize(760, 620)
        self.schema = None

        layout = QVBoxLayout(self)
        self.status_label = QLabel(f"Introspecting {call['url']} …")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter operations…")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_input)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Operation", "Returns"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._choose(item))
        self.tree.currentItemChanged.connect(self._show_description)
        layout.addWidget(self.tree, 1)

        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color:#888;")
        layout.addWidget(self.description)

        buttons = QHBoxLayout()
        insert_btn = QPushButton("Use Operation")
        insert_btn.setToolTip("Replace the request body with a query for the selected operation (or double-click it).")
        insert_btn.clicked.connect(lambda: self._choose(self.tree.currentItem()))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(insert_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.worker = CallWorker(call, cookie_jar)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(lambda msg: self.status_label.setText(f"Introspection failed: {msg}"))
        self.worker.start()

    def _on_done(self, response, _elapsed):
        try:
            payload = response.json()
        except Exception:
            self.status_label.setText(f"HTTP {response.status_code}: the endpoint did not return JSON.")
            return
        schema = ((payload or {}).get("data") or {}).get("__schema")
        if not schema:
            errors = (payload or {}).get("errors") or []
            msg = errors[0].get("message") if errors and isinstance(errors[0], dict) else f"HTTP {response.status_code}"
            self.status_label.setText(f"No schema returned ({msg}). Introspection may be disabled on this server.")
            return
        self.schema = schema
        types = {t["name"]: t for t in schema.get("types") or [] if t.get("name")}
        count = 0
        for root_kind, key in (("query", "queryType"), ("mutation", "mutationType"), ("subscription", "subscriptionType")):
            root_name = (schema.get(key) or {}).get("name")
            root_type = types.get(root_name)
            if not root_type:
                continue
            root_item = QTreeWidgetItem([f"{root_kind.capitalize()} ({root_name})", ""])
            font = root_item.font(0)
            font.setBold(True)
            root_item.setFont(0, font)
            for field in sorted(root_type.get("fields") or [], key=lambda f: f["name"]):
                args = ", ".join(f"{a['name']}: {graphql_type_str(a['type'])}" for a in field.get("args") or [])
                item = QTreeWidgetItem([f"{field['name']}({args})" if args else field["name"],
                                        graphql_type_str(field.get("type"))])
                item.setData(0, Qt.ItemDataRole.UserRole, (root_kind, field))
                root_item.addChild(item)
                count += 1
            self.tree.addTopLevelItem(root_item)
        self.tree.expandAll()
        self.status_label.setText(f"{count} operations. Double-click one to build a query.")

    def _apply_filter(self, text):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            for j in range(root.childCount()):
                child = root.child(j)
                child.setHidden(bool(text) and text not in child.text(0).lower())

    def _show_description(self, item, _previous=None):
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.description.setText((data[1].get("description") or "") if data else "")

    def _choose(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data or not self.schema:
            return
        root_kind, field = data
        query, variables = graphql_operation_for_field(self.schema, root_kind, field)
        self.operationChosen.emit(query, variables)
        self.close()

    def closeEvent(self, event):
        _keep_thread_alive(self.worker)
        super().closeEvent(event)


# ---------------------------
# WebSocket / Server-Sent Events console
# ---------------------------
class WebSocketWorker(QThread):
    message = pyqtSignal(str, str)  # direction ("in", "out", "info"), text
    connected = pyqtSignal(str)
    closed = pyqtSignal(str)

    def __init__(self, url, headers, verify_ssl=True):
        super().__init__()
        self.url = url
        self.headers = headers
        self.verify_ssl = verify_ssl
        self.ws = None
        self._stopping = False

    def run(self):
        sslopt = None if self.verify_ssl else {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
        try:
            self.ws = websocket.create_connection(
                self.url, header=[f"{k}: {v}" for k, v in self.headers.items()],
                sslopt=sslopt, timeout=15,
            )
        except Exception as e:
            self.closed.emit(f"Connection failed: {e}")
            return
        self.ws.settimeout(None)
        self.connected.emit(f"Connected to {self.url}")
        reason = "Disconnected"
        try:
            while True:
                opcode, data = self.ws.recv_data()
                if opcode == websocket.ABNF.OPCODE_TEXT:
                    self.message.emit("in", data.decode("utf-8", "replace"))
                elif opcode == websocket.ABNF.OPCODE_BINARY:
                    preview = base64.b64encode(data[:48]).decode("ascii")
                    self.message.emit("in", f"<binary {len(data):,} bytes> {preview}{'…' if len(data) > 48 else ''}")
                elif opcode == websocket.ABNF.OPCODE_CLOSE:
                    reason = "Server closed the connection"
                    break
        except Exception as e:
            if not self._stopping:
                reason = f"Connection lost: {e}"
        finally:
            try:
                self.ws.close()
            except Exception:
                pass
        self.closed.emit(reason)

    def send(self, text):
        # websocket-client serialises sends with its own lock, so this is safe
        # to call from the UI thread while run() is blocked in recv.
        if self.ws is None:
            return
        try:
            self.ws.send(text)
            self.message.emit("out", text)
        except Exception as e:
            self.message.emit("info", f"Send failed: {e}")

    def stop(self):
        self._stopping = True
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass


class SSEWorker(QThread):
    message = pyqtSignal(str, str)
    connected = pyqtSignal(str)
    closed = pyqtSignal(str)

    def __init__(self, url, headers, verify_ssl=True):
        super().__init__()
        self.url = url
        self.headers = headers
        self.verify_ssl = verify_ssl
        self._response = None
        self._stopping = False

    def run(self):
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        headers.update(self.headers)
        reason = "Stream ended"
        try:
            with requests.get(self.url, headers=headers, stream=True, timeout=(15, None),
                              verify=self.verify_ssl) as resp:
                self._response = resp
                if resp.status_code >= 400:
                    self.closed.emit(f"HTTP {resp.status_code} {resp.reason}: {resp.text[:300]}")
                    return
                resp.encoding = resp.encoding or "utf-8"
                self.connected.emit(f"HTTP {resp.status_code} — {resp.headers.get('Content-Type', '')}")
                event, event_id, data_lines = "", "", []
                for line in resp.iter_lines(chunk_size=None, decode_unicode=True):
                    if self._stopping:
                        break
                    if line is None:
                        continue
                    if line == "":
                        if data_lines:
                            label = (event or "message") + (f" #{event_id}" if event_id else "")
                            self.message.emit("in", f"[{label}] " + "\n".join(data_lines))
                        event, data_lines = "", []
                        continue
                    if line.startswith(":"):
                        continue  # comment / keep-alive
                    field, _, value = line.partition(":")
                    if value.startswith(" "):
                        value = value[1:]
                    if field == "data":
                        data_lines.append(value)
                    elif field == "event":
                        event = value
                    elif field == "id":
                        event_id = value
        except Exception as e:
            if not self._stopping:
                reason = f"Connection lost: {e}"
        self.closed.emit("Disconnected" if self._stopping else reason)

    def stop(self):
        self._stopping = True
        if self._response is not None:
            try:
                self._response.close()
            except Exception:
                pass


class RealtimeConsoleDialog(QDialog):
    MODES = ["WebSocket", "Server-Sent Events"]

    def __init__(self, url, headers_text, env, verify_ssl=True, parent=None):
        super().__init__(parent)
        self.env = env
        self.verify_ssl = verify_ssl
        self.worker = None
        self.setWindowTitle("WebSocket / SSE Console")
        self.resize(900, 680)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(self.MODES)
        self.url_input = QLineEdit(url)
        self.url_input.setPlaceholderText("wss://example.com/socket  or  https://example.com/events")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("send_btn")
        self.connect_btn.setFixedWidth(110)
        self.connect_btn.clicked.connect(self.toggle_connection)
        top.addWidget(self.mode_combo)
        top.addWidget(self.url_input, 1)
        top.addWidget(self.connect_btn)
        layout.addLayout(top)

        self.mode_combo.setCurrentText("WebSocket" if url.lower().startswith(("ws://", "wss://")) else "Server-Sent Events")
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)

        layout.addWidget(QLabel("Headers (Key: Value, {{VARS}} resolved on connect):"))
        self.headers_text = QPlainTextEdit(headers_text)
        self.headers_text.setMaximumHeight(70)
        layout.addWidget(self.headers_text)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(_monospace_font(9))
        layout.addWidget(self.log, 1)

        self.send_group = QGroupBox("Send message")
        send_layout = QHBoxLayout(self.send_group)
        self.send_text = QPlainTextEdit()
        self.send_text.setMaximumHeight(80)
        self.send_text.setPlaceholderText('{"type": "ping"}   — Ctrl+Enter to send')
        self.send_btn = QPushButton("Send")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self.send_message)
        send_layout.addWidget(self.send_text, 1)
        send_layout.addWidget(self.send_btn)
        layout.addWidget(self.send_group)
        QShortcut(QKeySequence("Ctrl+Return"), self.send_text, activated=self.send_message)

        bottom = QHBoxLayout()
        self.pretty_check = QCheckBox("Pretty-print JSON")
        self.pretty_check.setChecked(True)
        self.status_label = QLabel("Disconnected")
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.log.clear)
        save_btn = QPushButton("Save Log…")
        save_btn.clicked.connect(self.save_log)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.pretty_check)
        bottom.addWidget(clear_btn)
        bottom.addWidget(save_btn)
        layout.addLayout(bottom)
        self._on_mode_changed(self.mode_combo.currentText())

    def _on_mode_changed(self, mode):
        url = self.url_input.text().strip()
        if mode == "WebSocket":
            url = re.sub(r"^http(s?)://", r"ws\1://", url, flags=re.I)
        else:
            url = re.sub(r"^ws(s?)://", r"http\1://", url, flags=re.I)
        self.url_input.setText(url)
        self.send_group.setVisible(mode == "WebSocket")

    def toggle_connection(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.connect_btn.setEnabled(False)
            return
        url = apply_env(self.url_input.text().strip(), self.env)
        if not url:
            return
        headers = parse_header_lines(self.headers_text.toPlainText(), self.env)
        if self.mode_combo.currentText() == "WebSocket":
            if websocket is None:
                QMessageBox.warning(self, "WebSocket", "WebSocket support needs the websocket-client package:\n\n"
                                                       "pip install websocket-client")
                return
            self.worker = WebSocketWorker(url, headers, self.verify_ssl)
        else:
            self.worker = SSEWorker(url, headers, self.verify_ssl)
        self.worker.message.connect(self._append)
        self.worker.connected.connect(self._on_connected)
        self.worker.closed.connect(self._on_closed)
        self.mode_combo.setEnabled(False)
        self.connect_btn.setText("Disconnect")
        self.status_label.setText(f"Connecting to {url} …")
        self._append("info", f"Connecting to {url}")
        self.worker.start()

    def _on_connected(self, text):
        self.status_label.setText(text)
        self._append("info", text)
        self.send_btn.setEnabled(isinstance(self.worker, WebSocketWorker))

    def _on_closed(self, reason):
        self._append("info", reason)
        self.status_label.setText(reason)
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.send_btn.setEnabled(False)
        self.mode_combo.setEnabled(True)

    def _append(self, direction, text):
        arrow = {"in": "↓", "out": "↑"}.get(direction, "•")
        if self.pretty_check.isChecked() and direction in ("in", "out"):
            prefix, sep, rest = text.partition("] ") if text.startswith("[") else ("", "", text)
            try:
                rest = json.dumps(json.loads(rest), indent=2, ensure_ascii=False)
                text = f"{prefix}{sep}{rest}"
            except Exception:
                pass
        stamp = time.strftime("%H:%M:%S")
        self.log.appendPlainText(f"{stamp}  {arrow}  {text}")

    def send_message(self):
        if not isinstance(self.worker, WebSocketWorker) or not self.send_btn.isEnabled():
            return
        text = apply_env(self.send_text.toPlainText(), self.env)
        if text:
            self.worker.send(text)

    def save_log(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save Log", str(Path.home() / "realtime-log.txt"),
                                               "Text Files (*.txt);;All Files (*)")
        if fname:
            try:
                Path(fname).write_text(self.log.toPlainText(), encoding="utf-8")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", str(e))

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            _keep_thread_alive(self.worker)
        super().closeEvent(event)


# ---------------------------
# Main window
# ---------------------------
class CurlPyProMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CurlPyPro - With Snippet Generator")
        self.setWindowIcon(app_icon())
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
            "use_cookie_jar": True,
            "proxy": "",
            "proxy_bypass": "",
        })
        self.history = load_history()
        self.envs, self.env_secrets = load_envs()
        self.collections = load_document("collections", {})
        self.cookie_jar = list_to_cookiejar(load_document("cookies", []))

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

        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.new_tab())
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.close_tab(self.tabs.currentIndex()))

    def create_left_panel(self):
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # Collections
        collections_group = QGroupBox("Collections")
        collections_layout = QVBoxLayout(collections_group)

        collections_toolbar = QHBoxLayout()
        btn_new_collection = QPushButton("New")
        btn_import_collection = QPushButton("Import")
        btn_import_collection.setToolTip("Import a CurlPyPro export, a Postman collection or environment, "
                                         "or an OpenAPI/Swagger spec (JSON or YAML)")
        btn_export_collection = QPushButton("Export")
        btn_run_collection = QPushButton("Run")
        btn_run_collection.setToolTip("Run the selected collection with its tests")

        for b in (btn_new_collection, btn_import_collection, btn_export_collection, btn_run_collection):
            b.setFixedHeight(34)

        btn_new_collection.clicked.connect(self.create_collection)
        btn_import_collection.clicked.connect(self.import_collection)
        btn_export_collection.clicked.connect(self.export_collection)
        btn_run_collection.clicked.connect(self.run_selected_collection)

        collections_toolbar.addWidget(btn_new_collection)
        collections_toolbar.addWidget(btn_import_collection)
        collections_toolbar.addWidget(btn_export_collection)
        collections_toolbar.addWidget(btn_run_collection)
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
        self.collections_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.collections_tree.customContextMenuRequested.connect(self.show_collection_context_menu)
        collections_layout.addWidget(self.collections_tree)

        left_layout.addWidget(collections_group)

        # History
        history_group = QGroupBox("Request History")
        history_layout = QVBoxLayout(history_group)

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

        del_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.history_tree)
        del_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        del_shortcut.activated.connect(self.delete_selected_history)

        left_layout.addWidget(history_group)

        # Collections and history share a movable boundary instead of fixed
        # layout proportions, so either tree can grow when its content matters.
        sidebar_splitter = QSplitter(Qt.Orientation.Vertical)
        left_layout.removeWidget(collections_group)
        left_layout.removeWidget(history_group)
        sidebar_splitter.addWidget(collections_group)
        sidebar_splitter.addWidget(history_group)
        sidebar_splitter.setChildrenCollapsible(False)
        sidebar_splitter.setStretchFactor(0, 1)
        sidebar_splitter.setStretchFactor(1, 1)
        sidebar_splitter.setSizes([430, 430])
        left_layout.addWidget(sidebar_splitter, 1)

        self.reload_collections()
        self.reload_history()

        return left_widget

    def create_right_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)

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

        btn_manage_cookies = QPushButton("Cookies")
        btn_manage_cookies.setFixedHeight(34)
        btn_manage_cookies.clicked.connect(self.manage_cookies)
        env_layout.addWidget(btn_manage_cookies)

        env_layout.addStretch()
        layout.addLayout(env_layout)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._show_tab_context_menu)

        new_tab_btn = QToolButton()
        new_tab_btn.setText("+")
        new_tab_btn.setToolTip("New request tab (Ctrl+T)")
        new_tab_btn.clicked.connect(lambda: self.new_tab())
        self.tabs.setCornerWidget(new_tab_btn, Qt.Corner.TopRightCorner)

        layout.addWidget(self.tabs, 1)

        self._restore_tabs_or_blank()

        return container

    # ---------- Tabs ----------
    def new_tab(self, from_data=None, activate=True):
        panel = RequestPanel(self)
        if from_data:
            panel.from_dict(from_data)
        index = self.tabs.addTab(panel, self._tab_title_for(panel))
        if activate:
            self.tabs.setCurrentIndex(index)
        return panel

    def current_panel(self):
        return self.tabs.currentWidget()

    def _tab_title_for(self, panel):
        method = panel.method_combo.currentText() if hasattr(panel, "method_combo") else "GET"
        url = panel.url_input.text().strip() if hasattr(panel, "url_input") else ""
        if not url:
            return "New Request"
        short = url if len(url) <= 28 else url[:27] + "…"
        return f"{method} {short}"

    def _update_tab_title(self, panel):
        idx = self.tabs.indexOf(panel)
        if idx >= 0:
            self.tabs.setTabText(idx, self._tab_title_for(panel))

    def close_tab(self, index):
        if index < 0:
            return
        if self.tabs.count() <= 1:
            # Always keep at least one tab open — swap the last one for a blank tab.
            self.tabs.removeTab(index)
            self.new_tab()
            return
        self.tabs.removeTab(index)

    def _duplicate_tab(self, index):
        panel = self.tabs.widget(index)
        if panel is None:
            return
        self.new_tab(from_data=panel.to_dict())

    def _close_other_tabs(self, index):
        for i in reversed(range(self.tabs.count())):
            if i != index:
                self.tabs.removeTab(i)

    def _show_tab_context_menu(self, pos):
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        menu = QMenu(self)
        menu.addAction("Duplicate Tab", lambda: self._duplicate_tab(index))
        menu.addAction("Close Tab", lambda: self.close_tab(index))
        menu.addAction("Close Other Tabs", lambda: self._close_other_tabs(index))
        menu.exec(bar.mapToGlobal(pos))

    def _restore_tabs_or_blank(self):
        data = load_document("workspace_tabs", None)
        tabs_data = (data or {}).get("tabs") if isinstance(data, dict) else None
        if tabs_data:
            for req in tabs_data:
                self.new_tab(from_data=req, activate=False)
            active = (data or {}).get("active", 0)
            if 0 <= active < self.tabs.count():
                self.tabs.setCurrentIndex(active)
        else:
            self.new_tab()

    # ---------- Shared environment / history / cookies ----------
    def get_active_env(self):
        name = self.env_combo.currentText()
        return self.envs.get(name, {})

    def record_history(self, entry):
        entry["_id"] = add_history(entry)
        self.history.append(entry)
        self.reload_history()

    def manage_cookies(self):
        dlg = CookieJarDialog(self.cookie_jar, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cookie_jar = dlg.build_cookie_jar()
            save_document("cookies", cookiejar_to_list(self.cookie_jar))
            self.status_bar.showMessage("Cookie jar updated")

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

        groups = {}
        shown = 0

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
            return QColor("#d9534f")
        if 200 <= code < 300:
            return QColor("#28a745")
        if 300 <= code < 400:
            return QColor("#17a2b8")
        if 400 <= code < 500:
            return QColor("#f0ad4e")
        return QColor("#d9534f")

    def persist_envs(self):
        """Save environments; secret variables go to the OS keychain."""
        failed = save_envs(self.envs, self.env_secrets)
        if failed:
            self.status_bar.showMessage(
                f"Couldn't store {', '.join(failed)} in the OS keychain; saved in the local database instead."
            )

    def refresh_env_combo(self):
        current = self.env_combo.currentText()
        self.env_combo.blockSignals(True)
        self.env_combo.clear()
        self.env_combo.addItems(list(self.envs.keys()))
        if current in self.envs:
            self.env_combo.setCurrentText(current)
        self.env_combo.blockSignals(False)

    def manage_environments(self):
        # Edit a copy so Cancel really discards changes.
        dlg = EnvironmentDialog(copy.deepcopy(self.envs), self, self.env_secrets)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.envs = dlg.envs
            self.env_secrets = dlg.secrets
            self.persist_envs()
            self.refresh_env_combo()
            QMessageBox.information(self, "Environments", "Environments saved.")

    def load_collection_item(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "request":
            return
        coll = data.get("collection")
        idx = data.get("index")
        try:
            req = self.collections[coll][idx]
            self.current_panel().apply_saved_request(req)
            QMessageBox.information(self, "Loaded", f"Loaded request from collection '{coll}'")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def load_history_item(self, item, _col=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("group"):
            return
        self.current_panel().apply_history_entry(data)
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
        menu.addAction("Compare with Current Response", lambda: self._history_compare(data))
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
        self.current_panel().send_request()

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
        output_file = (data.get("output_file") or "").strip()
        if output_file and output_file != "-":
            parts += ["--output", shlex.quote(output_file)]
        parts.append(shlex.quote(url))
        QApplication.clipboard().setText(" ".join(parts))
        self.status_bar.showMessage("Copied request as cURL")

    def _history_copy_response(self, data):
        body = data.get("response_body_full") or data.get("response_body_snippet", "") or ""
        QApplication.clipboard().setText(body)
        self.status_bar.showMessage("Response body copied to clipboard")

    def _history_compare(self, data):
        panel = self.current_panel()
        if panel is None:
            return
        body = data.get("response_body_full") or data.get("response_body_snippet", "") or ""
        stamp = time.strftime("%H:%M:%S", time.localtime(data.get("timestamp", 0)))
        panel.show_diff_against(f"history {data.get('method', '')} ({data.get('status', '')}, {stamp})", body)

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
        fname, _ = QFileDialog.getOpenFileName(
            self, "Import Collection", str(Path.home()),
            "Collections and API specs (*.json *.yaml *.yml);;All Files (*)",
        )
        if not fname:
            return
        try:
            data = parse_import_text(Path(fname).read_text(encoding="utf-8-sig"))
            result = import_any(data)
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))
            return

        added_collections, added_requests = [], 0
        for name, items in result["collections"].items():
            if not isinstance(items, list):
                continue
            if name is None:  # a bare list of requests: name it after the file
                name = Path(fname).stem
                self.collections.setdefault(name, []).extend(items)
            elif detect_import_format(data) == "curlpypro" and name in self.collections:
                self.collections[name].extend(items)
            else:
                base, n = name, 2
                while name in self.collections:
                    name = f"{base} ({n})"
                    n += 1
                self.collections[name] = items
            added_collections.append(name)
            added_requests += len(items)

        added_envs = []
        for env_name, variables in result["environments"].items():
            env = self.envs.setdefault(env_name, {})
            for key, value in variables.items():
                if not env.get(key):  # never overwrite a value the user already set
                    env[key] = value
            added_envs.append(env_name)

        save_document("collections", self.collections)
        self.reload_collections()
        if added_envs:
            self.persist_envs()
            self.refresh_env_combo()

        lines = []
        if added_collections:
            lines.append(f"{added_requests} request(s) into: {', '.join(added_collections)}")
        if added_envs:
            lines.append(f"Environment(s): {', '.join(added_envs)}")
        if result["warnings"]:
            shown = result["warnings"][:8]
            more = len(result["warnings"]) - len(shown)
            lines.append("\nNotes:\n• " + "\n• ".join(shown) + (f"\n…and {more} more" if more else ""))
        QMessageBox.information(self, "Imported", "\n".join(lines) or "Nothing to import.")

    def _selected_collection_name(self):
        item = self.collections_tree.currentItem()
        meta = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not meta:
            return None
        return meta.get("name") if meta.get("type") == "collection" else meta.get("collection")

    def run_selected_collection(self, name=None):
        name = name or self._selected_collection_name()
        if not name and len(self.collections) == 1:
            name = next(iter(self.collections))
        if not name:
            QMessageBox.information(self, "Run Collection", "Select a collection to run.")
            return
        if not self.collections.get(name):
            QMessageBox.information(self, "Run Collection", f"'{name}' has no requests yet.")
            return
        dlg = CollectionRunnerDialog(self, name)
        dlg.show()

    def show_collection_context_menu(self, pos):
        item = self.collections_tree.itemAt(pos)
        meta = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not meta:
            return
        menu = QMenu(self)
        if meta.get("type") == "collection":
            name = meta.get("name")
            menu.addAction("Run Collection…", lambda: self.run_selected_collection(name))
            menu.addAction("Rename…", lambda: self._rename_collection(name))
            menu.addSeparator()
            menu.addAction("Delete Collection", lambda: self._delete_collection(name))
        else:
            coll, idx = meta.get("collection"), meta.get("index")
            menu.addAction("Open in New Tab", lambda: self._open_collection_request(coll, idx))
            menu.addSeparator()
            menu.addAction("Delete Request", lambda: self._delete_collection_request(coll, idx))
        menu.exec(self.collections_tree.viewport().mapToGlobal(pos))

    def _open_collection_request(self, coll, idx):
        try:
            self.new_tab(from_data=self.collections[coll][idx])
        except (KeyError, IndexError):
            pass

    def _rename_collection(self, name):
        new_name, ok = QInputDialog.getText(self, "Rename Collection", "New name:", text=name)
        new_name = (new_name or "").strip()
        if not ok or not new_name or new_name == name:
            return
        if new_name in self.collections:
            QMessageBox.warning(self, "Exists", "A collection with that name already exists.")
            return
        self.collections = {(new_name if k == name else k): v for k, v in self.collections.items()}
        save_document("collections", self.collections)
        self.reload_collections()

    def _delete_collection(self, name):
        count = len(self.collections.get(name, []))
        reply = QMessageBox.question(self, "Delete Collection",
                                     f"Delete collection '{name}' and its {count} request(s)?")
        if reply == QMessageBox.StandardButton.Yes:
            self.collections.pop(name, None)
            save_document("collections", self.collections)
            self.reload_collections()

    def _delete_collection_request(self, coll, idx):
        try:
            req = self.collections[coll][idx]
        except (KeyError, IndexError):
            return
        reply = QMessageBox.question(self, "Delete Request", f"Delete '{req.get('name', 'request')}' from '{coll}'?")
        if reply == QMessageBox.StandardButton.Yes:
            del self.collections[coll][idx]
            save_document("collections", self.collections)
            self.reload_collections()

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

    # ---------- Lifecycle ----------
    def closeEvent(self, event):
        try:
            panel = self.current_panel()
            if panel is not None:
                self.settings["request_timeout"] = panel.timeout_spin.value()
                self.settings.update(panel._advanced_snapshot())
            save_document("settings", self.settings)
            self.persist_envs()
            save_document("collections", self.collections)
            save_document("cookies", cookiejar_to_list(self.cookie_jar))
            save_document("workspace_tabs", {
                "tabs": [self.tabs.widget(i).to_dict() for i in range(self.tabs.count())],
                "active": self.tabs.currentIndex(),
            })
        except Exception:
            pass
        super().closeEvent(event)


def main():
    if sys.platform == "win32":
        # Gives the taskbar/window a stable identity instead of Python's icon.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "CurlPyPro.CurlPyPro"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("CurlPyPro")
    app.setApplicationDisplayName("CurlPyPro")
    app.setOrganizationName("CurlPyPro")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    win = CurlPyProMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
