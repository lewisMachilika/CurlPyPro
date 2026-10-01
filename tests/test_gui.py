"""Drive the real window headlessly: build requests, send them and inspect the UI."""
import json

from PyQt6.QtWidgets import QDialog

import curlpypro as cp
from helpers import send_and_wait


def test_window_starts(win):
    assert win.tabs.count() == 1
    assert win.current_panel().url_input.text() == ""


def test_send_get_shows_pretty_json(qtbot, win, server):
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo?a=1")
    entry = send_and_wait(qtbot, panel)
    assert entry["status"] == 200
    assert '"method": "GET"' in panel.response_pretty.toPlainText()
    assert "Status: 200" in panel.response_summary.text()
    assert win.history_count_label.text() == "1 request"


def test_tests_and_capture_tabs(qtbot, win, server):
    panel = win.current_panel()
    panel.method_combo.setCurrentText("POST")
    panel.url_input.setText(server + "/login")
    panel.body_type_combo.setCurrentText("JSON")
    panel.body_text.setPlainText('{"user": "ann", "password": "pw"}')
    panel._restore_tests([{"enabled": True, "source": "Status code", "property": "", "operator": "equals", "expected": "200"},
                          {"enabled": True, "source": "JSON path", "property": "$.user.id", "operator": "equals", "expected": "8"}])
    panel._restore_captures([{"enabled": True, "variable": "TOKEN", "source": "JSON path", "expression": "$.token"}])
    send_and_wait(qtbot, panel)
    assert "Tests: 1/2 passed" in panel.response_summary.text()
    assert win.get_active_env()["TOKEN"] == "tok-123"

    # The captured variable is usable by the next request.
    panel2 = win.new_tab()
    panel2.url_input.setText(server + "/me")
    panel2.set_auth_config({"type": "Bearer Token", "token": "{{TOKEN}}"})
    assert send_and_wait(qtbot, panel2)["status"] == 200


def test_curl_paste_import(win):
    panel = win.current_panel()
    assert panel.import_curl("curl -X PUT 'http://h/x?q=1' -H 'Authorization: Bearer t' -H 'X-A: b' -d '{\"k\":1}'")
    assert panel.method_combo.currentText() == "PUT"
    assert panel.url_input.text() == "http://h/x"
    assert panel._params_snapshot()[0]["key"] == "q"
    assert panel.get_auth_config()["type"] == "Bearer Token"
    assert panel.body_type_combo.currentText() == "JSON"
    assert "X-A: b" in panel.headers_text.toPlainText()


def test_save_to_collection_and_reload(qtbot, win, monkeypatch):
    panel = win.current_panel()
    panel.url_input.setText("http://example.invalid/a")
    monkeypatch.setattr(cp.SaveToCollectionDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(cp.SaveToCollectionDialog, "collection_name", lambda self: "Mine")
    panel.save_to_collection()
    assert len(win.collections["Mine"]) == 1
    assert cp.load_document("collections", {})["Mine"][0]["url"] == "http://example.invalid/a"


def test_snippets_generate(win, server):
    panel = win.current_panel()
    panel.method_combo.setCurrentText("POST")
    panel.url_input.setText(server + "/echo")
    panel.headers_text.setPlainText("X-A: 1")
    panel.body_type_combo.setCurrentText("JSON")
    panel.body_text.setPlainText('{"a": 1}')
    ctx = panel._snippet_context()
    for fmt in ("curl", "python-requests", "powershell", "java", "axios"):
        snippet = panel._build_snippet(fmt, ctx)
        assert server + "/echo" in snippet, fmt
    curl = panel._build_snippet("curl", ctx)
    # The generated curl command must round-trip through the importer.
    parsed = cp.parse_curl_command(curl)
    assert parsed["method"] == "POST" and json.loads(parsed["data"]) == {"a": 1}


def test_collection_runner_worker(qtbot, win, server):
    login = cp._empty_request("Login", "POST", server + "/login")
    login.update(body_type="JSON", body='{"user": "ann", "password": "pw"}',
                 captures=[{"variable": "TOKEN", "source": "JSON path", "expression": "$.token"}])
    me = cp._empty_request("Me", "GET", server + "/me")
    me.update(auth={"type": "Bearer Token", "token": "{{TOKEN}}"},
              tests=[{"source": "Status code", "operator": "equals", "expected": "200"}])
    win.collections["Run"] = [login, me]
    dlg = cp.CollectionRunnerDialog(win, "Run")
    qtbot.addWidget(dlg)
    dlg.start_run()
    qtbot.waitUntil(lambda: dlg.worker is not None and dlg.worker.isFinished() and len(dlg.results) == 2, timeout=10000)
    assert [cp.run_outcome(r) for r in dlg.results] == ["PASS", "PASS"]


def test_history_reload_into_tab(qtbot, win, server):
    panel = win.current_panel()
    panel.method_combo.setCurrentText("POST")
    panel.url_input.setText(server + "/echo")
    panel.body_text.setPlainText("hello")
    entry = send_and_wait(qtbot, panel)
    other = win.new_tab()
    other.apply_history_entry(entry)
    assert other.method_combo.currentText() == "POST"
    assert other.url_input.text() == server + "/echo"
    assert other.body_text.toPlainText() == "hello"


def test_response_kinds(qtbot, win, server):
    panel = win.current_panel()
    for path, check in (
        ("/html", lambda: "<h1>" in panel.response_pretty.toPlainText()),
        ("/xml", lambda: "<b>1</b>" in panel.response_pretty.toPlainText()),
        ("/image.png", lambda: panel.response_preview_label.pixmap() is not None
            and not panel.response_preview_label.pixmap().isNull()),
    ):
        panel.url_input.setText(server + path)
        send_and_wait(qtbot, panel)
        assert check(), path


def test_error_is_reported(qtbot, win, popups):
    panel = win.current_panel()
    panel.url_input.setText("http://127.0.0.1:9/nothing")
    entry = send_and_wait(qtbot, panel)
    assert entry["status"] == 0
    assert any(kind == "critical" for kind, *_ in popups)


def test_workspace_tabs_persist(qtbot, fresh_db, popups):
    w = cp.CurlPyProMainWindow()
    qtbot.addWidget(w)
    w.current_panel().url_input.setText("http://example.invalid/one")
    w.new_tab().url_input.setText("http://example.invalid/two")
    w.close()
    w2 = cp.CurlPyProMainWindow()
    qtbot.addWidget(w2)
    urls = [w2.tabs.widget(i).url_input.text() for i in range(w2.tabs.count())]
    assert urls == ["http://example.invalid/one", "http://example.invalid/two"]


def test_ctrl_enter_sends_from_body(qtbot, win, server):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5000)
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo")
    panel.request_tabs.setCurrentIndex(2)
    panel.body_text.setFocus()
    before = len(win.history)
    qtbot.keyClick(panel.body_text, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: len(win.history) > before, timeout=10000)
    assert panel.body_text.toPlainText() == ""  # the key sent the request, not a newline


def test_save_in_place(qtbot, win, monkeypatch):
    panel = win.current_panel()
    panel.url_input.setText("http://example.invalid/a")
    monkeypatch.setattr(cp.SaveToCollectionDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(cp.SaveToCollectionDialog, "collection_name", lambda self: "Mine")
    monkeypatch.setattr(cp.SaveToCollectionDialog, "request_name", lambda self: "List things")
    panel.save()  # no origin yet: asks where to save
    assert win.collections["Mine"][0]["name"] == "List things"
    assert win.tabs.tabText(0) == "GET List things"

    panel.url_input.setText("http://example.invalid/b")
    panel.save()  # now updates in place
    assert len(win.collections["Mine"]) == 1
    assert win.collections["Mine"][0]["url"] == "http://example.invalid/b"
    assert win.collections["Mine"][0]["name"] == "List things"

    # Reopening the request from the tree reuses a blank tab and keeps the link.
    win.close_tab(0)
    win._open_collection_request("Mine", 0, reuse_blank=True)
    assert win.tabs.count() == 1
    win.current_panel().method_combo.setCurrentText("POST")
    win.current_panel().save()
    assert win.collections["Mine"][0]["method"] == "POST"


def test_collection_request_management(win, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    win.collections["C"] = [cp._empty_request("one", url="http://h/1"), cp._empty_request("two", url="http://h/2")]
    win.reload_collections()
    win._duplicate_collection_request("C", 0)
    assert [r["name"] for r in win.collections["C"]] == ["one", "one (copy)", "two"]
    win._move_collection_request("C", 2, -1)
    assert [r["name"] for r in win.collections["C"]] == ["one", "two", "one (copy)"]
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("renamed", True))
    win._rename_collection_request("C", 0)
    assert win.collections["C"][0]["name"] == "renamed"

    win.collection_filter.setText("h/2")
    coll_item = win.collections_tree.topLevelItem(0)
    visible = [coll_item.child(i).text(0) for i in range(coll_item.childCount()) if not coll_item.child(i).isHidden()]
    assert visible == ["GET two"]


def test_response_filter(qtbot, win, server):
    panel = win.current_panel()
    panel.method_combo.setCurrentText("POST")
    panel.url_input.setText(server + "/login")
    panel.body_text.setPlainText('{"user": "ann", "password": "pw"}')
    send_and_wait(qtbot, panel)
    panel.response_filter.setText("$.user.name")
    assert panel.response_pretty.toPlainText() == "ann"
    panel.response_filter.setText("$.user")
    assert json.loads(panel.response_pretty.toPlainText()) == {"id": 7, "name": "ann"}
    panel.response_filter.setText("token")
    assert panel.response_pretty.toPlainText().strip() == '"token": "tok-123",'
    panel.response_filter.setText("$.missing")
    assert "No match" in panel.response_pretty.toPlainText()
    panel.response_filter.clear()
    assert '"user"' in panel.response_pretty.toPlainText()


def test_binary_body_ui_and_snippets(qtbot, win, server, tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02")
    panel = win.current_panel()
    panel.method_combo.setCurrentText("PUT")
    panel.url_input.setText(server + "/echo")
    panel.body_type_combo.setCurrentText("Binary")
    assert not panel.binary_group.isHidden() and panel.body_text.isHidden()
    panel.binary_file_input.setText(str(f))
    entry = send_and_wait(qtbot, panel)
    assert '"body_len": 3' in panel.response_pretty.toPlainText()
    assert entry["binary_file"] == str(f)
    curl = panel._build_snippet("curl", panel._snippet_context())
    assert f"--data-binary @{f}" in curl
    assert "data=f" in panel._build_snippet("python-requests", panel._snippet_context())

    other = win.new_tab(from_data=panel.to_dict())
    assert other.binary_file_input.text() == str(f)


def test_unresolved_variables_warning(qtbot, win, server):
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo?x={{NOT_SET}}")
    send_and_wait(qtbot, panel)
    assert "{{NOT_SET}}" in win.status_bar.currentMessage()


def test_copy_as_curl_and_theme(win, server):
    from PyQt6.QtWidgets import QApplication
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo")
    panel.copy_as_curl()
    assert QApplication.clipboard().text().startswith("curl")
    win.set_dark_theme(True)
    assert QApplication.instance().palette().window().color().name() == "#2b2b2b"
    win.set_dark_theme(False)
    assert QApplication.instance().palette().window().color().name() != "#2b2b2b"
    win.change_font_size(2)
    assert win.settings["font_size"] == 12
    win.change_font_size(0)


def test_menu_bar_actions_exist(win):
    titles = [a.text() for a in win.menuBar().actions()]
    assert titles == ["&File", "&Request", "&Tools", "&View", "&Help"]
