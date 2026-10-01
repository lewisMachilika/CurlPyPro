"""Dialogs and less common features: stress test, GraphQL schema, realtime, HAR, diff, envs."""
import json

from PyQt6.QtWidgets import QFileDialog

import curlpypro as cp
from server import start_ws_echo_server
from helpers import send_and_wait


def test_stress_test(qtbot, win, server):
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo")
    opened = []
    orig_show = cp.StressTestDialog.show
    cp.StressTestDialog.show = lambda self: opened.append(self)
    try:
        panel.open_stress_test()
    finally:
        cp.StressTestDialog.show = orig_show
    dlg = opened[0]
    qtbot.addWidget(dlg)
    dlg.total_spin.setValue(20)
    dlg.concurrency_spin.setValue(4)
    done = []
    dlg.run_test()
    dlg.worker.all_done.connect(done.append)
    qtbot.waitUntil(lambda: dlg.worker.isFinished(), timeout=15000)
    qtbot.waitUntil(lambda: dlg.run_btn.isEnabled(), timeout=5000)
    assert len(dlg._results) == 20
    assert all(r["status"] == 200 for r in dlg._results)


def test_graphql_schema_browser(qtbot, win, server):
    panel = win.current_panel()
    panel.method_combo.setCurrentText("POST")
    panel.url_input.setText(server + "/graphql")
    call = cp.prepare_request(dict(panel._build_request_dict(), body_type="GraphQL",
                                   body=cp.GRAPHQL_INTROSPECTION_QUERY), {}, 10)
    dlg = cp.GraphQLSchemaDialog(call, win.cookie_jar, panel)
    qtbot.addWidget(dlg)
    dlg.operationChosen.connect(panel._insert_graphql_operation)
    qtbot.waitUntil(lambda: dlg.schema is not None, timeout=10000)
    item = dlg.tree.topLevelItem(0).child(0)
    dlg._choose(item)
    assert "user(id: $id)" in panel.body_text.toPlainText()
    assert json.loads(panel.graphql_variables_text.toPlainText()) == {"id": None}
    assert panel.body_type_combo.currentText() == "GraphQL"


def test_sse_console(qtbot, win, server):
    dlg = cp.RealtimeConsoleDialog(server + "/sse", "", {}, True, win)
    qtbot.addWidget(dlg)
    assert dlg.mode_combo.currentText() == "Server-Sent Events"
    dlg.toggle_connection()
    qtbot.waitUntil(lambda: "Stream ended" in dlg.log.toPlainText(), timeout=10000)
    log = dlg.log.toPlainText()
    assert "[greet #1] hello" in log
    assert '"n": 2' in log


def test_websocket_console(qtbot, win):
    sock, url = start_ws_echo_server()
    try:
        dlg = cp.RealtimeConsoleDialog(url, "", {"name": "bob"}, True, win)
        qtbot.addWidget(dlg)
        assert dlg.mode_combo.currentText() == "WebSocket"
        dlg.toggle_connection()
        qtbot.waitUntil(lambda: dlg.send_btn.isEnabled(), timeout=10000)
        dlg.send_text.setPlainText("hi {{name}}")
        dlg.send_message()
        qtbot.waitUntil(lambda: "echo:hi bob" in dlg.log.toPlainText(), timeout=10000)
        assert '"hello": "client"' in dlg.log.toPlainText()
        dlg.toggle_connection()
        qtbot.waitUntil(lambda: not dlg.worker.isRunning(), timeout=10000)
    finally:
        sock.close()


def test_har_export_and_diff(qtbot, win, server, tmp_path, monkeypatch):
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo?n=1")
    send_and_wait(qtbot, panel)
    panel.url_input.setText(server + "/echo?n=2")
    send_and_wait(qtbot, panel)
    assert "Compared" in panel.diff_label.text() or panel._diff_baseline is not None
    html = panel.response_diff.toHtml()
    assert "n" in html

    har = tmp_path / "out.har"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(har), ""))
    panel._export_response_har()
    data = json.loads(har.read_text())
    entry = data["log"]["entries"][0]
    assert entry["response"]["status"] == 200 and entry["request"]["method"] == "GET"


def test_save_binary_response(qtbot, win, server, tmp_path, monkeypatch, popups):
    panel = win.current_panel()
    panel.url_input.setText(server + "/pdf")
    send_and_wait(qtbot, panel)
    # Binary responses no longer interrupt with a modal prompt.
    assert not [p for p in popups if p[0] == "question"]
    assert "binary response" in win.status_bar.currentMessage()
    out = tmp_path / "x.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    panel._save_response_body_bytes()
    assert out.read_bytes() == b"%PDF-1.4 fake"


def test_environment_dialog_roundtrip(qtbot, win, monkeypatch):
    dlg = cp.EnvironmentDialog({"default": {"A": "1"}, "staging": {"B": "2"}}, win, {})
    qtbot.addWidget(dlg)
    dlg.env_combo.setCurrentText("staging")
    dlg.add_variable_row("C", "3")
    dlg.env_combo.setCurrentText("default")
    dlg.env_combo.setCurrentText("staging")
    dlg.accept()
    assert dlg.envs["staging"] == {"B": "2", "C": "3"}
    assert dlg.envs["default"] == {"A": "1"}


def test_cookie_dialog(qtbot, win, server):
    panel = win.current_panel()
    panel.url_input.setText(server + "/set-cookie")
    send_and_wait(qtbot, panel)
    dlg = cp.CookieJarDialog(win.cookie_jar, win)
    qtbot.addWidget(dlg)
    jar = dlg.build_cookie_jar()
    assert [c.name for c in jar] == ["session"]


def test_post_response_script(qtbot, win, server):
    panel = win.current_panel()
    panel.url_input.setText(server + "/echo?x=5")
    panel._restore_scripts({"enabled": True, "post_response":
                            "def on_response(response, env):\n    env['X'] = response.json()['query']['x']\n"})
    send_and_wait(qtbot, panel)
    assert win.get_active_env()["X"] == "5"


def test_secret_env_vars_without_keychain(fresh_db):
    envs = {"default": {"TOKEN": "abc", "PLAIN": "1"}}
    secrets = {"default": ["TOKEN"]}
    failed = cp.save_envs(envs, secrets)
    # The test run disables the keychain, so the value falls back to the database.
    assert failed == ["default/TOKEN"]
    loaded, _ = cp.load_envs()
    assert loaded["default"]["TOKEN"] == "abc"


def test_sse_last_event_without_blank_line(qtbot, win, server):
    dlg = cp.RealtimeConsoleDialog(server + "/sse-unterminated", "", {}, True, win)
    qtbot.addWidget(dlg)
    dlg.toggle_connection()
    qtbot.waitUntil(lambda: "Stream ended" in dlg.log.toPlainText(), timeout=10000)
    assert "[message] last-event" in dlg.log.toPlainText()


def test_html_preview_created_lazily(qtbot, win, server):
    panel = win.current_panel()
    assert panel.response_preview_html is None
    panel.url_input.setText(server + "/html")
    send_and_wait(qtbot, panel)
    assert panel.response_preview_html is not None
