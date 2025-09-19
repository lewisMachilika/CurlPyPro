#!/usr/bin/env python3
"""
posty_clone.py - Minimal, intuitive Postman-like app prototype.

Requirements:
    pip install PyQt6 requests

Run:
    python posty_clone.py
"""

import sys
import json
import time
import re
from pathlib import Path
from functools import partial

import requests
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox, QListWidget,
    QSplitter, QMessageBox, QFileDialog, QTabWidget, QListWidgetItem,
    QInputDialog
)
from PyQt6.QtCore import Qt

DATA_DIR = Path.home() / ".posty_clone"
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "history.json"
ENVS_FILE = DATA_DIR / "envs.json"
COLLECTIONS_FILE = DATA_DIR / "collections.json"


def load_json_file(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_json_file(path, data):
    path.write_text(json.dumps(data, indent=2))


# Simple placeholder substitution: {{VAR}}
placeholder_pattern = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def apply_env(text: str, env: dict):
    if not text:
        return text

    def repl(m):
        key = m.group(1)
        return str(env.get(key, m.group(0)))

    return placeholder_pattern.sub(repl, text)


class PostyMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Posty — simple Postman clone")
        self.resize(1100, 700)

        # load persisted stuff
        self.history = load_json_file(HISTORY_FILE, [])
        self.envs = load_json_file(ENVS_FILE, {"default": {}})
        self.collections = load_json_file(COLLECTIONS_FILE, {})

        # main layout
        main = QWidget()
        self.setCentralWidget(main)
        root = QHBoxLayout(main)

        # Left: history / collections
        left = QVBoxLayout()
        self.history_list = QListWidget()
        self.collections_list = QListWidget()
        self.reload_history()
        self.reload_collections()

        left.addWidget(QLabel("History"))
        left.addWidget(self.history_list, 1)
        left.addWidget(QLabel("Collections"))
        left.addWidget(self.collections_list, 1)

        left_buttons = QHBoxLayout()
        btn_save_coll = QPushButton("Save to Collection")
        btn_new_coll = QPushButton("New Collection")
        left_buttons.addWidget(btn_save_coll)
        left_buttons.addWidget(btn_new_coll)
        left.addLayout(left_buttons)

        # Right: request builder & response
        right = QVBoxLayout()

        # Request row (method + url)
        row = QHBoxLayout()
        self.method = QComboBox()
        self.method.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.url_input = QLineEdit()
        self.send_btn = QPushButton("Send ▸")
        row.addWidget(self.method, 0)
        row.addWidget(self.url_input, 1)
        row.addWidget(self.send_btn, 0)

        # headers and body editors in splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        # headers and body area
        top_editor = QWidget()
        te_layout = QVBoxLayout(top_editor)
        env_row = QHBoxLayout()
        env_row.addWidget(QLabel("Environment:"))
        self.env_combo = QComboBox()
        self.env_combo.addItems(list(self.envs.keys()))
        btn_manage_envs = QPushButton("Manage Envs")
        env_row.addWidget(self.env_combo)
        env_row.addWidget(btn_manage_envs)
        te_layout.addLayout(env_row)

        te_layout.addWidget(QLabel("Headers (one per line, Key: Value)"))
        self.headers_text = QTextEdit()
        self.headers_text.setPlaceholderText("Content-Type: application/json\nAuthorization: Bearer {{TOKEN}}")
        te_layout.addWidget(self.headers_text, 1)

        te_layout.addWidget(QLabel("Body (raw)"))
        self.body_text = QTextEdit()
        self.body_text.setPlaceholderText('{"name": "alice"}')
        te_layout.addWidget(self.body_text, 2)

        splitter.addWidget(top_editor)

        # Response viewer
        resp_widget = QWidget()
        resp_layout = QVBoxLayout(resp_widget)
        self.response_tabs = QTabWidget()
        # summary tab
        self.summary_label = QLabel("No response yet.")
        self.summary_label.setWordWrap(True)
        summary_tab = QWidget()
        summary_tab_layout = QVBoxLayout(summary_tab)
        summary_tab_layout.addWidget(self.summary_label)
        # headers tab
        self.resp_headers = QTextEdit()
        self.resp_headers.setReadOnly(True)
        # body tab
        self.resp_body = QTextEdit()
        self.resp_body.setReadOnly(True)
        # raw tab
        self.resp_raw = QTextEdit()
        self.resp_raw.setReadOnly(True)

        self.response_tabs.addTab(summary_tab, "Summary")
        self.response_tabs.addTab(self.resp_headers, "Headers")
        self.response_tabs.addTab(self.resp_body, "Body")
        self.response_tabs.addTab(self.resp_raw, "Raw")

        resp_layout.addWidget(self.response_tabs)
        splitter.addWidget(resp_widget)

        # footer (save/load)
        footer = QHBoxLayout()
        btn_save_request = QPushButton("Save Request")
        btn_load_request = QPushButton("Load Request")
        btn_clear = QPushButton("Clear")
        footer.addWidget(btn_save_request)
        footer.addWidget(btn_load_request)
        footer.addWidget(btn_clear)
        footer.addStretch()

        # assemble right
        right.addLayout(row)
        right.addWidget(splitter, 1)
        right.addLayout(footer)

        # add left and right to root
        root.addLayout(left, 1)
        root.addLayout(right, 3)

        # signals
        self.send_btn.clicked.connect(self.on_send)
        btn_manage_envs.clicked.connect(self.manage_envs)
        btn_save_request.clicked.connect(self.on_save_request)
        btn_load_request.clicked.connect(self.on_load_request)
        btn_clear.clicked.connect(self.on_clear)
        btn_save_coll.clicked.connect(self.save_to_collection)
        btn_new_coll.clicked.connect(self.create_collection)
        self.history_list.itemDoubleClicked.connect(self.load_history_item)
        self.collections_list.itemDoubleClicked.connect(self.load_collection_item)

    # ------- persistence helpers
    def reload_history(self):
        self.history_list.clear()
        for item in reversed(self.history[-200:]):  # show recent first (cap)
            pretty = f"{item.get('method')} {item.get('url')} [{item.get('status', '')}]"
            li = QListWidgetItem(pretty)
            li.setData(Qt.ItemDataRole.UserRole, item)
            self.history_list.addItem(li)

    def reload_collections(self):
        self.collections_list.clear()
        for coll_name, items in self.collections.items():
            li = QListWidgetItem(f"{coll_name} ({len(items)} items)")
            li.setData(Qt.ItemDataRole.UserRole, coll_name)
            self.collections_list.addItem(li)

    def persist_history(self):
        save_json_file(HISTORY_FILE, self.history)

    def persist_envs(self):
        save_json_file(ENVS_FILE, self.envs)

    def persist_collections(self):
        save_json_file(COLLECTIONS_FILE, self.collections)

    # ------- UI actions
    def on_clear(self):
        self.url_input.clear()
        self.headers_text.clear()
        self.body_text.clear()
        self.summary_label.setText("Cleared.")
        self.resp_body.clear()
        self.resp_headers.clear()
        self.resp_raw.clear()

    def on_save_request(self):
        name, ok = QInputDialog.getText(self, "Save Request", "Enter request name:")
        if not ok or not name:
            return
        req = self._build_request_descriptor()
        # save under "unsorted" collection by default
        coll = self.collections.setdefault("unsorted", [])
        coll.append({"name": name, **req})
        self.persist_collections()
        self.reload_collections()
        QMessageBox.information(self, "Saved", f"Request '{name}' saved to collection 'unsorted'.")

    def on_load_request(self):
        # pick a collection and item
        if not self.collections:
            QMessageBox.information(self, "No Collections", "No collections available.")
            return
        coll_names = list(self.collections.keys())
        coll_name, ok = QInputDialog.getItem(self, "Pick collection", "Collection:", coll_names, editable=False)
        if not ok:
            return
        items = self.collections.get(coll_name, [])
        if not items:
            QMessageBox.information(self, "Empty", "That collection has no items.")
            return
        names = [it.get("name", f"{i}") for i, it in enumerate(items)]
        pick, ok = QInputDialog.getItem(self, "Pick request", "Request:", names, editable=False)
        if not ok:
            return
        idx = names.index(pick)
        self._load_descriptor(items[idx])

    def save_to_collection(self):
        name, ok = QInputDialog.getText(self, "Save to collection", "Enter collection name (new or existing):")
        if not ok or not name:
            return
        req = self._build_request_descriptor()
        coll = self.collections.setdefault(name, [])
        coll.append({"name": req.get("name") or time.strftime("%Y%m%d-%H%M%S"), **req})
        self.persist_collections()
        self.reload_collections()
        QMessageBox.information(self, "Saved", f"Request saved to collection '{name}'.")

    def create_collection(self):
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if ok and name:
            self.collections.setdefault(name, [])
            self.persist_collections()
            self.reload_collections()

    def load_history_item(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self._load_descriptor(data.get("request", data))

    def load_collection_item(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            items = self.collections.get(name, [])
            if not items:
                QMessageBox.information(self, "Empty", "That collection has no requests.")
                return
            names = [it.get("name", f"{i}") for i, it in enumerate(items)]
            pick, ok = QInputDialog.getItem(self, "Pick request", "Request:", names, editable=False)
            if not ok:
                return
            idx = names.index(pick)
            self._load_descriptor(items[idx])

    def manage_envs(self):
        # very small environment editor dialog
        dlg = QWidget()
        dlg.setWindowTitle("Manage Environments")
        dlg.resize(600, 400)
        layout = QVBoxLayout(dlg)
        env_list = QListWidget()
        env_list.addItems(list(self.envs.keys()))
        layout.addWidget(env_list)
        inner = QHBoxLayout()
        btn_add = QPushButton("Add")
        btn_remove = QPushButton("Remove")
        btn_edit = QPushButton("Edit")
        inner.addWidget(btn_add); inner.addWidget(btn_remove); inner.addWidget(btn_edit)
        layout.addLayout(inner)

        def add_env():
            name, ok = QInputDialog.getText(dlg, "Add env", "Env name:")
            if not ok or not name: return
            self.envs.setdefault(name, {})
            env_list.addItem(name)
            self.env_combo.addItem(name)
            self.persist_envs()

        def remove_env():
            cur = env_list.currentItem()
            if not cur: return
            name = cur.text()
            if name in self.envs:
                del self.envs[name]
                self.persist_envs()
                env_list.takeItem(env_list.row(cur))
                # refresh combo
                self.env_combo.clear()
                self.env_combo.addItems(list(self.envs.keys()))

        def edit_env():
            cur = env_list.currentItem()
            if not cur: return
            name = cur.text()
            data = self.envs.get(name, {})
            txt, ok = QInputDialog.getMultiLineText(dlg, f"Edit env '{name}'", "Enter key=value per line:", "\n".join(f"{k}={v}" for k, v in data.items()))
            if not ok: return
            new = {}
            for line in txt.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    new[k.strip()] = v.strip()
            self.envs[name] = new
            self.persist_envs()

        btn_add.clicked.connect(add_env)
        btn_remove.clicked.connect(remove_env)
        btn_edit.clicked.connect(edit_env)

        dlg.show()

    # ------- request building and loading
    def _build_request_descriptor(self):
        return {
            "method": self.method.currentText(),
            "url": self.url_input.text(),
            "headers": self.headers_text.toPlainText(),
            "body": self.body_text.toPlainText(),
            "env": self.env_combo.currentText(),
            "name": None
        }

    def _load_descriptor(self, desc):
        self.method.setCurrentText(desc.get("method", "GET"))
        self.url_input.setText(desc.get("url", ""))
        self.headers_text.setPlainText(desc.get("headers", ""))
        self.body_text.setPlainText(desc.get("body", ""))
        env_name = desc.get("env", self.env_combo.currentText())
        if env_name in self.envs:
            idx = self.env_combo.findText(env_name)
            if idx >= 0:
                self.env_combo.setCurrentIndex(idx)

    # ------- core HTTP logic
    def parse_headers(self, text, env):
        headers = {}
        for line in text.splitlines():
            if not line.strip(): continue
            if ":" in line:
                k, v = line.split(":", 1)
                v = apply_env(v.strip(), env)
                headers[k.strip()] = v
        return headers

    def on_send(self):
        req = self._build_request_descriptor()
        env_name = req.get("env", "default")
        env = self.envs.get(env_name, {})

        url = apply_env(req["url"], env)
        headers = self.parse_headers(req["headers"], env)
        body_raw = apply_env(req["body"], env)
        method = req["method"]

        # try to parse body as json if looks like JSON
        data = None
        json_body = None
        # heuristic: if header content-type is json or body starts with '{' or '['
        ct = headers.get("Content-Type", "").lower()
        is_json = "application/json" in ct or body_raw.strip().startswith(("{", "["))
        if is_json and body_raw.strip():
            try:
                json_body = json.loads(body_raw)
            except Exception:
                # fallback: send raw text
                data = body_raw.encode("utf-8")
        else:
            data = body_raw.encode("utf-8") if body_raw else None

        # send
        try:
            t0 = time.time()
            resp = requests.request(method, url, headers=headers, json=json_body, data=data, timeout=30)
            elapsed = time.time() - t0
        except Exception as e:
            QMessageBox.critical(self, "Request failed", str(e))
            return

        # display
        summary = f"Status: {resp.status_code} {resp.reason}\nTime: {elapsed*1000:.0f} ms\nSize: {len(resp.content)} bytes"
        self.summary_label.setText(summary)
        # headers
        headers_text = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
        self.resp_headers.setPlainText(headers_text)
        # try pretty JSON body
        raw_text = resp.text
        self.resp_raw.setPlainText(raw_text)
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type or (raw_text.strip().startswith("{") or raw_text.strip().startswith("[")):
            try:
                pretty = json.dumps(resp.json(), indent=2)
                self.resp_body.setPlainText(pretty)
            except Exception:
                self.resp_body.setPlainText(raw_text)
        else:
            self.resp_body.setPlainText(raw_text)

        # add to history
        hist_item = {
            "timestamp": int(time.time()),
            "method": method,
            "url": url,
            "status": resp.status_code,
            "request": req
        }
        self.history.append(hist_item)
        # keep history reasonably sized
        if len(self.history) > 2000:
            self.history = self.history[-2000:]
        self.persist_history()
        self.reload_history()


def main():
    app = QApplication(sys.argv)
    w = PostyMainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
