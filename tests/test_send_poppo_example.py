import importlib.util
from pathlib import Path
import sys

import pytest


EXAMPLES = Path(__file__).parents[1] / "examples"
OPEN_SPEC = importlib.util.spec_from_file_location(
    "open_qingshu_and_click",
    str(EXAMPLES / "open_qingshu_and_click.py"),
)
OPEN_MODULE = importlib.util.module_from_spec(OPEN_SPEC)
sys.modules["open_qingshu_and_click"] = OPEN_MODULE
OPEN_SPEC.loader.exec_module(OPEN_MODULE)

SEND_SPEC = importlib.util.spec_from_file_location(
    "send_poppo_messages",
    str(EXAMPLES / "send_poppo_messages.py"),
)
MODULE = importlib.util.module_from_spec(SEND_SPEC)
SEND_SPEC.loader.exec_module(MODULE)


def test_cli_defaults_to_feishu_notifications():
    args = MODULE.build_parser().parse_args([])

    assert args.no_feishu is False
    assert args.feishu_config == MODULE.DEFAULT_CONFIG_PATH
    assert args.skip_send is False


def test_cli_accepts_skip_send():
    args = MODULE.build_parser().parse_args(["--skip-send"])

    assert args.skip_send is True


def test_device_connection_failure_sends_feishu_notification(monkeypatch, tmp_path):
    notifications = []
    config_path = tmp_path / "feishu.json"
    monkeypatch.setattr(MODULE, "load_approved_targets", lambda *args, **kwargs: [])

    def fail(_serial):
        raise MODULE.AdbError("等待后重试 10 次仍未发现可用设备")

    monkeypatch.setattr(MODULE, "select_device", fail)
    monkeypatch.setattr(
        MODULE,
        "notify_feishu",
        lambda message, enabled, config_path: notifications.append(
            (message, enabled, config_path)
        ),
    )

    result = MODULE.main(["--feishu-config", str(config_path)])

    assert result == 1
    assert len(notifications) == 1
    message, enabled, selected_config = notifications[0]
    assert "设备连接失败" in message
    assert "按 30 秒递增等待重试 10 次" in message
    assert enabled is True
    assert selected_config == config_path


def test_runtime_reconnect_failure_sends_feishu_notification(monkeypatch):
    notifications = []
    device = type("Device", (), {"serial": "serial-1", "model": "Phone"})()
    monkeypatch.setattr(MODULE, "load_approved_targets", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        MODULE,
        "select_device",
        lambda _serial: (object(), device),
    )

    class FailingMessenger:
        def __init__(self, *args, **kwargs):
            pass

        def run_targets(self, *args, **kwargs):
            raise MODULE.DeviceReconnectError("still offline")

    monkeypatch.setattr(MODULE, "PoppoMessenger", FailingMessenger)
    monkeypatch.setattr(
        MODULE,
        "notify_feishu",
        lambda message, enabled, config_path: notifications.append(message),
    )

    assert MODULE.main([]) == 1
    assert len(notifications) == 1
    assert "运行中设备离线" in notifications[0]
    assert "still offline" in notifications[0]


def test_runtime_reconnect_success_sends_feishu_notification(monkeypatch):
    notifications = []
    device = type("Device", (), {"serial": "serial-1", "model": "Phone"})()
    monkeypatch.setattr(MODULE, "load_approved_targets", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        MODULE,
        "select_device",
        lambda _serial: (object(), device),
    )

    class ReconnectedMessenger:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs["on_device_reconnected"]

        def run_targets(self, *args, **kwargs):
            self.callback(device)
            return []

    monkeypatch.setattr(MODULE, "PoppoMessenger", ReconnectedMessenger)
    monkeypatch.setattr(
        MODULE,
        "notify_feishu",
        lambda message, enabled, config_path: notifications.append(message),
    )

    assert MODULE.main([]) == 0
    assert len(notifications) == 1
    assert "设备重连成功" in notifications[0]
    assert "任务已继续运行" in notifications[0]


def test_cli_forwards_skip_send_to_messenger(monkeypatch):
    captured = {}
    device = type("Device", (), {"serial": "serial-1", "model": "Phone"})()
    monkeypatch.setattr(MODULE, "load_approved_targets", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        MODULE,
        "select_device",
        lambda _serial: (object(), device),
    )

    class CapturingMessenger:
        def __init__(self, *args, **kwargs):
            pass

        def run_targets(self, *args, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(MODULE, "PoppoMessenger", CapturingMessenger)

    assert MODULE.main(["--skip-send", "--limit", "1"]) == 0
    assert captured["monitor_forever"] is True
    assert captured["skip_initial_send"] is True
    assert captured["initial_success_limit"] is None


def test_cli_does_not_truncate_targets_and_forwards_success_limit(monkeypatch):
    captured = {}
    targets = [object(), object(), object()]
    device = type("Device", (), {"serial": "serial-1", "model": "Phone"})()
    monkeypatch.setattr(
        MODULE,
        "load_approved_targets",
        lambda *args, **kwargs: targets,
    )
    monkeypatch.setattr(
        MODULE,
        "select_device",
        lambda _serial: (object(), device),
    )

    class CapturingMessenger:
        def __init__(self, *args, **kwargs):
            pass

        def run_targets(self, received_targets, *args, **kwargs):
            captured["targets"] = received_targets
            captured.update(kwargs)
            return []

    monkeypatch.setattr(MODULE, "PoppoMessenger", CapturingMessenger)

    assert MODULE.main(["--limit", "2"]) == 0
    assert captured["targets"] == targets
    assert captured["initial_success_limit"] == 2


def test_cli_rejects_negative_success_limit(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "load_approved_targets",
        lambda *args, **kwargs: [],
    )

    with pytest.raises(ValueError, match="limit"):
        MODULE.main(["--limit", "-1"])
