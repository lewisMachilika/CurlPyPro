#!/usr/bin/env python3
"""
posty_pro_with_snippets.py - Posty Pro with code-snippet generation (curl, python requests, powershell, axios)
and support for attaching images/files in multipart/form-data.

Requirements:
    pip install PyQt6 requests
Run:
    python posty_pro_with_snippets.py
"""
import sys
import os
import json
import time
import re
import sqlite3
import mimetypes
from pathlib import Path
import shlex
import base64
from urllib.parse import unquote, urlparse, urlencode
import requests
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox, QListWidget,
    QSplitter, QMessageBox, QFileDialog, QTabWidget, QListWidgetItem,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QFrame, QScrollArea,
    QGroupBox, QDialog, QDialogButtonBox, QProgressBar, QStatusBar,
    QToolButton, QMenu, QPlainTextEdit, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QClipboard, QColor, QShortcut, QKeySequence, QFont

# ---------------------------
# Storage (SQLite, single file in the data dir)
# ---------------------------
DATA_DIR = Path.home() / ".posty_pro"
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "posty_pro.db"

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


# ---------------------------
# Network thread
# ---------------------------
class RequestThread(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, method, url, headers, json_body, data, files=None, timeout=30, params=None, auth=None):
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
        try:
            t0 = time.time()
            resp = requests.request(
                self.method, self.url,
                headers=self.headers,
                json=self.json_body,
                data=self.data,
                files=self.files,          # multipart support
                params=self.params,        # query-param auth support
                auth=self.auth,            # HTTP Basic auth support
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
# Main window
# ---------------------------
class PostyMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Posty Pro - With Snippet Generator")
        self.resize(1400, 900)

        init_db()
        self.settings = load_document("settings", {
            "theme": "light",
            "font_size": 10,
            "auto_format_json": True,
            "request_timeout": 30
        })
        self.history = load_history()
        self.envs = load_document("envs", {"default": {}})
        self.collections = load_document("collections", {})
        
        self._last_response = None          # requests.Response
        self._last_response_bytes = b""     # raw body

        self.request_thread = None
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
        self.method_combo.setFixedWidth(100)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter request URL e.g. https://api.example.com/v1/items")
        font = self.url_input.font()
        font.setPointSize(11)
        self.url_input.setFont(font)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("send_btn")
        self.send_btn.setFixedWidth(100)
        self.send_btn.setFixedHeight(36)
        self.send_btn.clicked.connect(self.send_request)

        save_menu = QMenu()
        save_menu.addAction("Save to Collection", self.save_to_collection)
        save_menu.addAction("Save as File", self.save_request_file)

        self.save_btn = QToolButton()
        self.save_btn.setText("Save")
        self.save_btn.setMenu(save_menu)
        self.save_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.save_btn.setFixedHeight(36)

        load_btn = QPushButton("Load")
        load_btn.setFixedHeight(36)
        load_btn.clicked.connect(self.load_request_file)

        # Generate menu (snippets)
        generate_menu = QMenu()
        generate_menu.addAction("All Languages…", self.generate_all_and_show)
        generate_menu.addSeparator()
        generate_menu.addAction("curl", lambda: self.generate_code_and_show("curl"))
        generate_menu.addAction("python-requests", lambda: self.generate_code_and_show("python-requests"))
        generate_menu.addAction("powershell", lambda: self.generate_code_and_show("powershell"))
        generate_menu.addAction("java", lambda: self.generate_code_and_show("java"))
        generate_menu.addAction("axios", lambda: self.generate_code_and_show("axios"))

        self.generate_btn = QToolButton()
        self.generate_btn.setText("Generate")
        self.generate_btn.setMenu(generate_menu)
        self.generate_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.generate_btn.setFixedHeight(36)
        self.generate_btn.setToolTip("Click for all languages, or use the ▾ menu for one.")
        # Clicking the button body (not the arrow) opens the all-languages view.
        self.generate_btn.clicked.connect(self.generate_all_and_show)

        url_layout.addWidget(self.method_combo)
        url_layout.addWidget(self.url_input, 1)
        url_layout.addWidget(self.send_btn)
        url_layout.addWidget(self.save_btn)
        url_layout.addWidget(load_btn)
        url_layout.addWidget(self.generate_btn)

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

        request_layout.addWidget(self.request_tabs)
        right_layout.addWidget(request_group, 1)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

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

        response_layout.addWidget(self.response_tabs)
        # Download toolbar
        download_toolbar = QHBoxLayout()
        self.btn_save_response_bytes = QPushButton("Save Response Body…")
        self.btn_save_response_bytes.setToolTip("Save raw response bytes to a file (good for PDFs/images/binary).")
        self.btn_save_response_bytes.clicked.connect(self._save_response_body_bytes)

        self.btn_extract_base64 = QPushButton("Extract & Save Base64…")
        self.btn_extract_base64.setToolTip("Parse JSON/Raw for base64 or data: URLs and save the decoded file.")
        self.btn_extract_base64.clicked.connect(self._extract_and_save_base64)

        download_toolbar.addWidget(self.btn_save_response_bytes)
        download_toolbar.addWidget(self.btn_extract_base64)
        download_toolbar.addStretch()
        response_layout.addLayout(download_toolbar)

        right_layout.addWidget(response_group, 1)

        return right_widget

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
        ct = (ct or "").lower()
        if "application/pdf" in ct:
            return ".pdf"
        if "image/png" in ct:
            return ".png"
        if "image/jpeg" in ct or "image/jpg" in ct:
            return ".jpg"
        if "image/webp" in ct:
            return ".webp"
        if "image/gif" in ct:
            return ".gif"
        if "image/bmp" in ct:
            return ".bmp"
        return ""

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

    def send_request(self):
        if self.request_thread and self.request_thread.isRunning():
            QMessageBox.information(self, "Request in Progress", "Please wait for the current request to complete.")
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

        # Apply authentication (mutates headers/params, returns Basic-auth tuple or None)
        params = {}
        auth_tuple = self.apply_auth(headers, params, env)

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.send_btn.setEnabled(False)
        self.send_btn.setText("Sending...")
        self.status_bar.showMessage("Sending request...")

        timeout = self.settings.get("request_timeout", 30)
        self.request_thread = RequestThread(
            method, url, headers, json_body, data, files, timeout,
            params=params or None, auth=auth_tuple
        )
        self.request_thread.finished.connect(self.on_request_finished)
        self.request_thread.error.connect(self.on_request_error)
        self.request_thread.start()

    def on_request_finished(self, result):
        self.progress_bar.setVisible(False)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Send")

        response = result['response']
        elapsed = result['elapsed']
        status_code = response.status_code
        status_text = response.reason
        size = len(response.content)

        self._last_response = response
        self._last_response_bytes = response.content or b""

        summary_html = f"Status: {status_code} {status_text} — Time: {elapsed*1000:.0f} ms — Size: {size:,} bytes"
        self.response_summary.setText(summary_html)

        headers_text = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        self.response_headers.setPlainText(headers_text)
        raw_text = response.text
        self.response_raw.setPlainText(raw_text)

        content_type = response.headers.get("Content-Type", "").lower()
        if "application/json" in content_type or raw_text.strip().startswith(("{", "[")):
            try:
                if self.settings.get("auto_format_json", True):
                    formatted_json = json.dumps(response.json(), indent=2)
                    self.response_pretty.setPlainText(formatted_json)
                else:
                    self.response_pretty.setPlainText(raw_text)
            except Exception:
                self.response_pretty.setPlainText(raw_text)
        else:
            self.response_pretty.setPlainText(raw_text)

        # Save to history
        try:
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
                "request_body": self.body_text.toPlainText(),
                "response_body_snippet": raw_text[:2000],
                "response_body_full": raw_text,
                # Original (unresolved) request inputs so the request can be fully restored
                "req_url": self.url_input.text(),
                "req_headers_text": self.headers_text.toPlainText(),
                "body_type": self.body_type_combo.currentText(),
                "auth": self.get_auth_config(),
                "attachments": self._attachments_snapshot(),
            }
            entry["_id"] = add_history(entry)
            self.history.append(entry)
            self.reload_history()
        except Exception:
            pass

        self.status_bar.showMessage(f"Completed: {status_code} ({elapsed*1000:.0f} ms)")
        # Offer to save if it's clearly binary media
        ct = (response.headers.get("Content-Type", "") or "").lower()
        if any(x in ct for x in ["application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"]):
            choice = QMessageBox.question(
                self,
                "Detected Binary Content",
                f"Response looks like '{ct}'. Do you want to save it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._save_response_body_bytes()

    def on_request_error(self, error_message):
        self.progress_bar.setVisible(False)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Send")
        QMessageBox.critical(self, "Request Error", f"An error occurred while making the request:\n{error_message}")
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
                "request_body": self.body_text.toPlainText(),
                "response_body_snippet": "",
                "response_body_full": "",
                # Original (unresolved) request inputs so the request can be fully restored
                "req_url": self.url_input.text(),
                "req_headers_text": self.headers_text.toPlainText(),
                "body_type": self.body_type_combo.currentText(),
                "auth": self.get_auth_config(),
                "attachments": self._attachments_snapshot(),
            }
            entry["_id"] = add_history(entry)
            self.history.append(entry)
            self.reload_history()
        except Exception:
            pass
        self.status_bar.showMessage("Error")

    # ---------- Save/Load ----------
    def save_to_collection(self):
        name, ok = QInputDialog.getText(self, "Save to Collection", "Collection name:")
        if not ok or not name:
            return
        coll = self.collections.setdefault(name, [])
        req = {
            "name": f"{self.method_combo.currentText()} {self.url_input.text()}",
            "method": self.method_combo.currentText(),
            "url": self.url_input.text(),
            "headers": self.headers_text.toPlainText(),
            "body_type": self.body_type_combo.currentText(),
            "body": self.body_text.toPlainText(),
            "auth": self.get_auth_config(),
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
            "body_type": self.body_type_combo.currentText(),
            "body": self.body_text.toPlainText(),
            "auth": self.get_auth_config(),
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
            self.body_type_combo.setCurrentText(data.get("body_type", "Raw"))
            self.body_text.setPlainText(data.get("body", ""))
            self.restore_attachments(data.get("attachments", []))
            self.set_auth_config(data.get("auth", {}))
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
                self.body_type_combo.setCurrentText(req.get("body_type", "Raw"))
                self.body_text.setPlainText(req.get("body", ""))
                self.restore_attachments(req.get("attachments", []))
                self.set_auth_config(req.get("auth", {}))
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

        # Restore body type before body so the right placeholder/attachments show
        if data.get("body_type"):
            self.body_type_combo.setCurrentText(data.get("body_type"))
        self.body_text.setPlainText(data.get("request_body", ""))

        # Restore attachments (multipart/form-data files)
        self.restore_attachments(data.get("attachments", []))

        # Restore auth configuration
        if "auth" in data:
            self.set_auth_config(data.get("auth", {}))

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

        # Fold authentication into the snippet's headers / URL query string
        params = {}
        auth_tuple = self.apply_auth(headers, params, env)
        if auth_tuple:
            user, pwd = auth_tuple
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urlencode(params)

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

    # ---------- Lifecycle ----------
    def closeEvent(self, event):
        try:
            # History is persisted incrementally as requests run; only the
            # wholesale documents need flushing here.
            save_document("settings", self.settings)
            save_document("envs", self.envs)
            save_document("collections", self.collections)
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = PostyMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
