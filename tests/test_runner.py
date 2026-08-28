from pathlib import Path

import pytest

from mobile_automation.runner import TaskRunner


class FakeClient:
    def __init__(self):
        self.calls = []

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def swipe(self, x1, y1, x2, y2, duration_ms):
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))

    def input_text(self, value):
        self.calls.append(("input_text", value))

    def dump_ui(self):
        return '<node text="设置" />'


def test_runner_dispatches_actions(tmp_path: Path):
    client = FakeClient()
    runner = TaskRunner(client, tmp_path)
    runner.run([
        {"action": "tap", "x": 10, "y": 20},
        {"action": "input_text", "text": "hello"},
        {"action": "assert_text", "text": "设置"},
    ])
    assert client.calls == [("tap", 10, 20), ("input_text", "hello")]


def test_runner_reports_unknown_action(tmp_path: Path):
    with pytest.raises(RuntimeError, match="不支持的 action"):
        TaskRunner(FakeClient(), tmp_path).run([{"action": "unknown"}])

