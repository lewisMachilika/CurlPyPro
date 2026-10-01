"""Helpers shared by the GUI tests."""


def send_and_wait(qtbot, panel, timeout=10000):
    before = len(panel.main.history)
    panel.send_request()
    qtbot.waitUntil(lambda: len(panel.main.history) > before, timeout=timeout)
    return panel.main.history[-1]
