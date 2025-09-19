#!/usr/bin/env python3
"""
posty_pro.py - Professional, intuitive Postman-like API testing tool.

Requirements:
    pip install PyQt6 requests

Run:
    python posty_pro.py
"""

import sys
import json
import time
import re
from pathlib import Path

import requests
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox, QListWidget,
    QSplitter, QMessageBox, QFileDialog, QTabWidget, QListWidgetItem,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QFrame, QScrollArea,
    QGroupBox, QDialog, QDialogButtonBox, QProgressBar, QStatusBar,
    QToolButton, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

DATA_DIR = Path.home() / ".posty_pro"
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "history.json"
ENVS_FILE = DATA_DIR / "envs.json"
COLLECTIONS_FILE = DATA_DIR / "collections.json"
SETTINGS_FILE = DATA_DIR / "settings.json"


def load_json_file(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_json_file(path, data):
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception:
        # Best-effort save; ignore errors for UI stability
        pass


# Enhanced placeholder substitution with better error handling
placeholder_pattern = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def apply_env(text: str, env: dict):
    if not text:
        return text

    def repl(m):
        key = m.group(1)
        return str(env.get(key, m.group(0)))

    return placeholder_pattern.sub(repl, text)


class RequestThread(QThread):
    """Background thread for HTTP requests to keep UI responsive"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, method, url, headers, json_body, data, timeout=30):
        super().__init__()
        self.method = method
        self.url = url
        self.headers = headers
        self.json_body = json_body
        self.data = data
        self.timeout = timeout

    def run(self):
        try:
            t0 = time.time()
            resp = requests.request(
                self.method, self.url,
                headers=self.headers,
                json=self.json_body,
                data=self.data,
                timeout=self.timeout
            )
            elapsed = time.time() - t0
            self.finished.emit({
                'response': resp,
                'elapsed': elapsed
            })
        except Exception as e:
            self.error.emit(str(e))


class EnvironmentDialog(QDialog):
    """Professional environment management dialog"""

    def __init__(self, envs, parent=None):
        # NOTE: pass the actual envs dict (not a copy) so edits persist
        super().__init__(parent)
        self.envs = envs
        self.setWindowTitle("Environment Manager")
        self.setModal(True)
        self.resize(700, 500)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Top section with environment selection
        top_frame = QFrame()
        top_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        top_layout = QHBoxLayout(top_frame)

        top_layout.addWidget(QLabel("Environment:"))
        self.env_combo = QComboBox()
        # Ensure there is always at least a default
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

        # Variables section
        vars_group = QGroupBox("Variables")
        vars_layout = QVBoxLayout(vars_group)

        # Variables table-like editor
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

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.env_combo.currentText():
            self.load_environment()

    def load_environment(self):
        """Load environment variables into the editor"""
        # Clear existing variables
        for i in reversed(range(self.vars_layout.count())):
            widget = self.vars_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        env_name = self.env_combo.currentText()
        if env_name in self.envs:
            env_vars = self.envs[env_name]
            for key, value in env_vars.items():
                self.add_variable_row(key, value)

        # Add empty row for new variables
        self.add_variable_row()

    def add_variable_row(self, key="", value=""):
        """Add a variable input row"""
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
        """Remove a variable row"""
        row_widget.setParent(None)

    def new_environment(self):
        """Create new environment"""
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
        """Duplicate current environment"""
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
        """Delete current environment"""
        current = self.env_combo.currentText()
        if not current or current == "default":
            QMessageBox.warning(self, "Cannot Delete", "Cannot delete the default environment.")
            return

        reply = QMessageBox.question(self, "Delete Environment", f"Delete environment '{current}'?")
        if reply == QMessageBox.StandardButton.Yes:
            del self.envs[current]
            self.env_combo.removeItem(self.env_combo.currentIndex())

    def accept(self):
        """Save changes when dialog is accepted"""
        self.save_current_environment()
        super().accept()

    def save_current_environment(self):
        """Save current environment from UI"""
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


class PostyMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Posty Pro - Professional API Testing Tool")
        self.resize(1400, 900)

        # Load settings and data
        self.settings = load_json_file(SETTINGS_FILE, {
            "theme": "light",
            "font_size": 10,
            "auto_format_json": True,
            "request_timeout": 30
        })
        self.history = load_json_file(HISTORY_FILE, [])
        self.envs = load_json_file(ENVS_FILE, {"default": {}})
        self.collections = load_json_file(COLLECTIONS_FILE, {})

        # Request thread
        self.request_thread = None

        self.init_ui()
        self.init_status_bar()
        self.apply_theme()

    def init_ui(self):
        """Initialize the main user interface"""
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # Main horizontal splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_widget_layout = QVBoxLayout(main_widget)
        main_widget_layout.addWidget(main_splitter)

        # Left sidebar (30% width)
        left_panel = self.create_left_panel()
        main_splitter.addWidget(left_panel)

        # Right panel (70% width)
        right_panel = self.create_right_panel()
        main_splitter.addWidget(right_panel)

        # Set splitter proportions
        main_splitter.setSizes([400, 1000])

    def create_left_panel(self):
        """Create the left sidebar with collections and history"""
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # Collections section
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
        collections_layout.addLayout(collections_toolbar)

        # Collections tree
        self.collections_tree = QTreeWidget()
        self.collections_tree.setHeaderLabel("Collections")
        self.collections_tree.itemDoubleClicked.connect(self.load_collection_item)
        collections_layout.addWidget(self.collections_tree)

        left_layout.addWidget(collections_group, 2)

        # History section
        history_group = QGroupBox("Recent Requests")
        history_layout = QVBoxLayout(history_group)

        history_toolbar = QHBoxLayout()
        btn_clear_history = QPushButton("Clear All")
        btn_clear_history.setFixedHeight(34)
        btn_clear_history.clicked.connect(self.clear_history)
        history_toolbar.addWidget(btn_clear_history)
        history_toolbar.addStretch()
        history_layout.addLayout(history_toolbar)

        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self.load_history_item)
        history_layout.addWidget(self.history_list)

        left_layout.addWidget(history_group, 1)

        # Load data
        self.reload_collections()
        self.reload_history()

        return left_widget

    def create_right_panel(self):
        """Create the main request/response panel"""
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Request section
        request_group = QGroupBox("Request")
        request_layout = QVBoxLayout(request_group)

        # URL bar
        url_layout = QHBoxLayout()
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(100)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter request URL...")
        font = self.url_input.font()
        font.setPointSize(11)
        self.url_input.setFont(font)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("send_btn")
        self.send_btn.setFixedWidth(100)
        self.send_btn.setFixedHeight(36)
        self.send_btn.clicked.connect(self.send_request)

        # Save/Load buttons
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

        url_layout.addWidget(self.method_combo)
        url_layout.addWidget(self.url_input, 1)
        url_layout.addWidget(self.send_btn)
        url_layout.addWidget(self.save_btn)
        url_layout.addWidget(load_btn)

        request_layout.addLayout(url_layout)

        # Environment selector
        env_layout = QHBoxLayout()
        env_layout.addWidget(QLabel("Environment:"))
        self.env_combo = QComboBox()
        # ensure env combo lists actual envs
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

        # Request details tabs
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
        self.request_tabs.addTab(body_widget, "Body")

        # Auth tab
        auth_widget = QWidget()
        auth_layout = QVBoxLayout(auth_widget)
        auth_layout.addWidget(QLabel("Authentication (Coming Soon)"))
        self.request_tabs.addTab(auth_widget, "Auth")

        request_layout.addWidget(self.request_tabs)
        right_layout.addWidget(request_group, 1)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        # Response section
        response_group = QGroupBox("Response")
        response_layout = QVBoxLayout(response_group)

        # Response summary
        self.response_summary = QLabel("Ready to send request...")
        self.response_summary.setStyleSheet("QLabel { padding: 10px; background-color:#3c3c3c; border: 1px solid #ddd; border-radius: 4px; }")
        self.response_summary.setWordWrap(True)
        response_layout.addWidget(self.response_summary)

        # Response tabs
        self.response_tabs = QTabWidget()

        # Pretty tab
        self.response_pretty = QTextEdit()
        self.response_pretty.setReadOnly(True)
        self.response_tabs.addTab(self.response_pretty, "Pretty")

        # Raw tab
        self.response_raw = QTextEdit()
        self.response_raw.setReadOnly(True)
        self.response_tabs.addTab(self.response_raw, "Raw")

        # Headers tab
        self.response_headers = QTextEdit()
        self.response_headers.setReadOnly(True)
        self.response_tabs.addTab(self.response_headers, "Headers")

        response_layout.addWidget(self.response_tabs)
        right_layout.addWidget(response_group, 1)

        return right_widget

    def init_status_bar(self):
        """Initialize status bar"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def apply_theme(self):
        """Apply the selected theme"""
        if self.settings.get("theme") == "dark":
            self.setStyleSheet("""
                QMainWindow { background-color: #2b2b2b; color: #ffffff; }
                QGroupBox { border: 2px solid #555; border-radius: 5px; margin-top: 10px; padding-top: 10px; }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
                QTextEdit { background-color: #3c3c3c; border: 1px solid #555; color: #ffffff; }
                QLineEdit { background-color: #3c3c3c; border: 1px solid #555; color: #ffffff; padding: 5px; }
                QPushButton { background-color: #4a90e2; color: white; border: none; padding: 8px; border-radius: 4px; }
                QPushButton:hover { background-color: #357abd; }
                QComboBox { background-color: #3c3c3c; border: 1px solid #555; color: #ffffff; padding: 5px; }
                QListWidget, QTreeWidget { background-color: #3c3c3c; border: 1px solid #555; color: #ffffff; }
                QTabWidget::pane { border: 1px solid #555; }
                QTabBar::tab { background-color: #4a4a4a; color: #ffffff; padding: 8px; margin-right: 2px; }
                QTabBar::tab:selected { background-color: #4a90e2; }
            """)
        else:
            # Light theme (default) — ensure send_btn style applied via objectName selector
            self.setStyleSheet("""
                QGroupBox { border: 2px solid #ddd; border-radius: 5px; margin-top: 10px; padding-top: 10px; font-weight: bold; }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
                QTextEdit { border: 1px solid #ddd; border-radius: 4px; padding: 5px; }
                QLineEdit { border: 1px solid #ddd; border-radius: 4px; padding: 8px; }
                QPushButton { background-color: #4a90e2; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; min-height: 32px; }
                QPushButton:hover { background-color: #357abd; }
                QPushButton:pressed { background-color: #2968a3; }
                QComboBox { border: 1px solid #ddd; border-radius: 4px; padding: 8px; min-height: 30px; }
                QListWidget, QTreeWidget { border: 1px solid #ddd; border-radius: 4px; }
                QTabWidget::pane { border: 1px solid #ddd; }
                QTabBar::tab { padding: 10px 20px; margin-right: 2px; background-color: #f5f5f5; border: 1px solid #ddd; }
                QTabBar::tab:selected { background-color: white; border-bottom: 2px solid #4a90e2; }
                QToolButton { min-height: 32px; padding: 6px 12px; }
                QPushButton#send_btn { background-color: #28a745; min-width: 100px; }
                QPushButton#send_btn:hover { background-color: #218838; }
            """)

        # Apply font size
        font = self.font()
        font.setPointSize(self.settings.get("font_size", 10))
        self.setFont(font)

    def reload_collections(self):
        """Reload collections tree"""
        self.collections_tree.clear()
        for coll_name, items in self.collections.items():
            coll_item = QTreeWidgetItem([f"{coll_name} ({len(items)} requests)"])
            coll_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "name": coll_name})

            for i, req in enumerate(items):
                req_name = req.get("name", f"Request {i+1}")
                method = req.get("method", "GET")
                url = req.get("url", "")[:50] + ("..." if len(req.get("url", "")) > 50 else "")

                req_item = QTreeWidgetItem([f"{method} {req_name}"])
                req_item.setToolTip(0, url)
                req_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "request", "collection": coll_name, "index": i})
                coll_item.addChild(req_item)

            self.collections_tree.addTopLevelItem(coll_item)

        self.collections_tree.expandAll()

    def reload_history(self):
        """Reload history list"""
        self.history_list.clear()
        # show latest entries last -> we display from oldest to newest
        for item in self.history[-1000:]:
            timestamp = item.get('timestamp', 0)
            time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
            method = item.get('method', 'GET')
            url = item.get('url', '')[:60] + ("..." if len(item.get('url', '')) > 60 else "")
            status = item.get('status', '')

            list_item = QListWidgetItem(f"{time_str} | {method} | {status} | {url}")
            list_item.setData(Qt.ItemDataRole.UserRole, item)

            self.history_list.addItem(list_item)

    def send_request(self):
        """Send HTTP request in background thread"""
        if self.request_thread and self.request_thread.isRunning():
            QMessageBox.information(self, "Request in Progress", "Please wait for the current request to complete.")
            return

        # Get current environment
        env_name = self.env_combo.currentText()
        env = self.envs.get(env_name, {})

        # Build request
        method = self.method_combo.currentText()
        url = apply_env(self.url_input.text().strip(), env)

        if not url:
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid URL.")
            return

        # Parse headers
        headers = {}
        for line in self.headers_text.toPlainText().splitlines():
            line = line.strip()
            if line and ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = apply_env(value.strip(), env)

        # Prepare body
        body_raw = apply_env(self.body_text.toPlainText().strip(), env)
        json_body = None
        data = None

        if body_raw:
            content_type = headers.get("Content-Type", "").lower()
            body_type = self.body_type_combo.currentText()

            if body_type == "JSON" or "application/json" in content_type or body_raw.startswith(("{", "[")):
                try:
                    json_body = json.loads(body_raw)
                    if "Content-Type" not in headers:
                        headers["Content-Type"] = "application/json"
                except json.JSONDecodeError as e:
                    QMessageBox.warning(self, "Invalid JSON", f"JSON parsing error: {str(e)}")
                    return
            else:
                data = body_raw.encode("utf-8")

        # Show progress
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        self.send_btn.setEnabled(False)
        self.send_btn.setText("Sending...")
        self.status_bar.showMessage("Sending request...")

        # Create and start request thread
        timeout = self.settings.get("request_timeout", 30)
        self.request_thread = RequestThread(method, url, headers, json_body, data, timeout)
        self.request_thread.finished.connect(self.on_request_finished)
        self.request_thread.error.connect(self.on_request_error)
        self.request_thread.start()

    def on_request_finished(self, result):
        """Handle successful request completion"""
        self.progress_bar.setVisible(False)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Send")

        response = result['response']
        elapsed = result['elapsed']

        # Update response summary
        status_code = response.status_code
        status_text = response.reason
        size = len(response.content)

        if 200 <= status_code < 300:
            badge_color = "#28a745" # green
            text_color = "#ffffff"
        elif status_code >= 400:
            badge_color = "#dc3545" # red
            text_color = "#ffffff"
        else:
            badge_color = "#ff9800" # orange
            text_color = "#000000"


        summary_html = f"""
        <div style='font-size:12px;'>
        <b>Status:</b>
        <span style='background-color: {badge_color}; color: {text_color}; padding:4px 8px; border-radius:4px; font-weight:bold; display:inline-block; margin-left:6px;'>
        {status_code} {status_text}
        </span>
        <br>
        <b>Time:</b> {elapsed*1000:.0f} ms<br>
        <b>Size:</b> {size:,} bytes
        </div>
        """
        self.response_summary.setText(summary_html)

        # Update response headers
        headers_text = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        self.response_headers.setPlainText(headers_text)

        # Update response body
        raw_text = response.text
        self.response_raw.setPlainText(raw_text)

        # Try to format as JSON
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

        # Add to history — FULL details stored for each request (no trimming)
        try:
            # Try to capture the original request object details if available
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
                # store request body as string (if available) and response body snippet
                "request_body": self.body_text.toPlainText(),
                "response_body_snippet": raw_text[:2000],  # keep snippet visible quickly
                # Keep full response body too (could be big), user asked to keep every request so we store it
                "response_body_full": raw_text,
            }
            # Append to full history (no trimming)
            self.history.append(entry)
            save_json_file(HISTORY_FILE, self.history)
            self.reload_history()
        except Exception:
            # Non-fatal: ignore history save issues
            pass

        self.status_bar.showMessage(f"Completed: {status_code} ({elapsed*1000:.0f} ms)")

    def on_request_error(self, error_message):
        """Handle errors from request thread"""
        self.progress_bar.setVisible(False)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Send")
        QMessageBox.critical(self, "Request Error", f"An error occurred while making the request:\n{error_message}")
        # Log error in history
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
            }
            self.history.append(entry)
            save_json_file(HISTORY_FILE, self.history)
            self.reload_history()
        except Exception:
            pass
        self.status_bar.showMessage("Error")

    def save_to_collection(self):
        """Save current request to a collection"""
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
        }
        coll.append(req)
        save_json_file(COLLECTIONS_FILE, self.collections)
        self.reload_collections()
        QMessageBox.information(self, "Saved", f"Request saved to collection '{name}'")

    def save_request_file(self):
        """Save request to a local JSON file"""
        fname, _ = QFileDialog.getSaveFileName(self, "Save Request", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        req = {
            "method": self.method_combo.currentText(),
            "url": self.url_input.text(),
            "headers": self.headers_text.toPlainText(),
            "body_type": self.body_type_combo.currentText(),
            "body": self.body_text.toPlainText(),
        }
        try:
            Path(fname).write_text(json.dumps(req, indent=2))
            QMessageBox.information(self, "Saved", f"Request saved to {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def load_request_file(self):
        """Load request from a JSON file"""
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
            QMessageBox.information(self, "Loaded", f"Request loaded from {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def manage_environments(self):
        # Pass the actual envs dict so EnvironmentDialog edits persist
        dlg = EnvironmentDialog(self.envs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # dlg.envs modified in-place
            self.envs = dlg.envs
            save_json_file(ENVS_FILE, self.envs)
            # Refresh combo
            self.env_combo.clear()
            self.env_combo.addItems(list(self.envs.keys()))
            QMessageBox.information(self, "Environments", "Environments saved.")

    def format_json_body(self):
        """Pretty print body if it's JSON"""
        text = self.body_text.toPlainText().strip()
        if not text:
            return
        try:
            parsed = json.loads(text)
            pretty = json.dumps(parsed, indent=2)
            self.body_text.setPlainText(pretty)
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "Invalid JSON", f"Cannot format JSON: {e}")

    def on_body_type_changed(self, text):
        # Adjust placeholder or behavior depending on body type
        if text == "JSON":
            self.body_text.setPlaceholderText('{\n  "key": "value"\n}')
        elif text == "Form Data":
            self.body_text.setPlaceholderText("key=value&other=one")
        elif text == "Binary":
            self.body_text.setPlaceholderText("(Binary payload - use Save/Load to manipulate file)")
        else:
            self.body_text.setPlaceholderText('{\n  "key": "value",\n  "user": "{{USERNAME}}"\n}')

    def load_collection_item(self, item, col):
        """Load a request from a collection when double-clicked"""
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
                QMessageBox.information(self, "Loaded", f"Loaded request from collection '{coll}'")
            except Exception as e:
                QMessageBox.critical(self, "Load Error", str(e))

    def load_history_item(self, item):
        """Load a historical request when double-clicked"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        # Populate what we can
        self.method_combo.setCurrentText(data.get("method", "GET"))
        self.url_input.setText(data.get("url", ""))
        # restore headers/body if available in history
        if data.get("request_headers"):
            try:
                # convert headers dict to lines
                hdrs = "\n".join(f"{k}: {v}" for k, v in data.get("request_headers", {}).items())
                self.headers_text.setPlainText(hdrs)
            except Exception:
                pass
        if data.get("request_body"):
            self.body_text.setPlainText(data.get("request_body"))
        QMessageBox.information(self, "Loaded", "Loaded request details from history.")

    def create_collection(self):
        name, ok = QInputDialog.getText(self, "Create Collection", "Collection name:")
        if not ok or not name:
            return
        if name in self.collections:
            QMessageBox.warning(self, "Exists", "A collection with that name already exists.")
            return
        self.collections[name] = []
        save_json_file(COLLECTIONS_FILE, self.collections)
        self.reload_collections()
        QMessageBox.information(self, "Created", f"Collection '{name}' created.")

    def import_collection(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Import Collection", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        try:
            data = json.loads(Path(fname).read_text())
            if isinstance(data, dict):
                # Merge dictionaries
                for k, v in data.items():
                    if k in self.collections and isinstance(v, list):
                        self.collections[k].extend(v)
                    else:
                        self.collections[k] = v
            elif isinstance(data, list):
                # Put into a default import collection
                name = Path(fname).stem
                self.collections.setdefault(name, []).extend(data)
            save_json_file(COLLECTIONS_FILE, self.collections)
            self.reload_collections()
            QMessageBox.information(self, "Imported", f"Imported collections from {fname}")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def export_collection(self):
        # If a collection top-level item is selected, export that, otherwise export all
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
            save_json_file(HISTORY_FILE, self.history)
            self.reload_history()

    def closeEvent(self, event):
        # Save settings and any remaining data
        try:
            save_json_file(SETTINGS_FILE, self.settings)
            save_json_file(HISTORY_FILE, self.history)
            save_json_file(ENVS_FILE, self.envs)
            save_json_file(COLLECTIONS_FILE, self.collections)
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
