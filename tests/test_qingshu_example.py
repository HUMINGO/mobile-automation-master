import builtins
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


EXAMPLE = Path(__file__).parents[1] / "examples" / "open_qingshu_and_click.py"
SPEC = importlib.util.spec_from_file_location("open_qingshu_and_click", str(EXAMPLE))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def install_fake_adb(monkeypatch, devices):
    clients = []

    class FakeAdbClient:
        def __init__(self, serial=None):
            self.serial = serial
            clients.append(self)

        def devices(self):
            return devices

    monkeypatch.setattr(MODULE, "AdbClient", FakeAdbClient)
    return clients


def device(serial, state="device", model="", product=""):
    return SimpleNamespace(
        serial=serial,
        state=state,
        model=model,
        product=product,
    )


def test_cli_defaults_to_resume_10000_iterations():
    args = MODULE.build_parser().parse_args([])
    assert args.iterations == 10000
    assert args.fresh is False
    assert args.csv_output == Path("artifacts/qingshu/users.csv")
    assert args.no_feishu is False
    assert args.feishu_config == MODULE.DEFAULT_CONFIG_PATH


def test_cli_accepts_fresh_and_state_file():
    args = MODULE.build_parser().parse_args(
        ["--iterations", "5", "--fresh", "--state-file", "custom.json"]
    )
    assert args.iterations == 5
    assert args.fresh is True
    assert args.state_file == Path("custom.json")


def test_cli_accepts_skip_gender_detection():
    args = MODULE.build_parser().parse_args(["--skip-gender-detection"])
    assert args.skip_gender_detection is True


def test_select_device_uses_explicit_serial_without_prompt(monkeypatch):
    devices = [device("one", model="Phone 1"), device("two", model="Phone 2")]
    clients = install_fake_adb(monkeypatch, devices)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("指定 serial 时不应要求选择设备"),
    )

    client, selected = MODULE.select_device("two")

    assert selected is devices[1]
    assert client.serial == "two"
    assert [item.serial for item in clients] == [None, "two"]


def test_select_device_reports_when_no_ready_device(monkeypatch):
    install_fake_adb(
        monkeypatch,
        [device("offline-one", state="offline"), device("pending", state="unauthorized")],
    )

    with pytest.raises(MODULE.AdbError, match="没有已授权的可用设备") as error:
        MODULE.select_device(retry_count=0)

    assert "offline-one (offline)" in str(error.value)
    assert "pending (unauthorized)" in str(error.value)


def test_select_device_retries_every_three_minutes_until_ready(monkeypatch):
    ready = device("ready", model="Ready Phone")
    snapshots = iter(
        [
            [],
            [device("pending", state="unauthorized")],
            [ready],
        ]
    )
    clients = []

    class FakeAdbClient:
        def __init__(self, serial=None):
            self.serial = serial
            clients.append(self)

        def devices(self):
            return next(snapshots)

    waits = []
    monkeypatch.setattr(MODULE, "AdbClient", FakeAdbClient)

    client, selected = MODULE.select_device(
        retry_count=3,
        retry_interval=180,
        sleep=waits.append,
    )

    assert selected is ready
    assert client.serial == "ready"
    assert waits == [180, 360]
    assert [item.serial for item in clients] == [None, "ready"]


def test_select_device_errors_after_three_retries(monkeypatch):
    calls = []
    waits = []

    class FakeAdbClient:
        def __init__(self, serial=None):
            self.serial = serial

        def devices(self):
            calls.append(1)
            return []

    monkeypatch.setattr(MODULE, "AdbClient", FakeAdbClient)

    with pytest.raises(MODULE.AdbError, match="重试 3 次仍未发现可用设备"):
        MODULE.select_device(
            retry_count=3,
            retry_interval=180,
            sleep=waits.append,
        )

    assert len(calls) == 4
    assert waits == [180, 360, 540]


def test_select_device_automatically_uses_only_ready_device(monkeypatch):
    devices = [
        device("offline-one", state="offline"),
        device("ready", model="Ready Phone"),
    ]
    install_fake_adb(monkeypatch, devices)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("只有一台可用设备时不应要求选择"),
    )

    client, selected = MODULE.select_device()

    assert selected is devices[1]
    assert client.serial == "ready"


def test_select_device_lists_ready_devices_and_uses_number(monkeypatch, capsys):
    devices = [
        device("first", model="Model One", product="product-one"),
        device("ignored", state="unauthorized", model="Not Ready"),
        device("second", product="product-two"),
        device("third"),
    ]
    install_fake_adb(monkeypatch, devices)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "2")

    client, selected = MODULE.select_device()

    output = capsys.readouterr().out
    assert "1. Model One (serial=first)" in output
    assert "2. product-two (serial=second)" in output
    assert "3. third (serial=third)" in output
    assert "Not Ready" not in output
    assert selected is devices[2]
    assert client.serial == "second"


def test_select_device_reprompts_for_empty_non_number_and_range(monkeypatch, capsys):
    devices = [device("one", model="One"), device("two", model="Two")]
    install_fake_adb(monkeypatch, devices)
    answers = iter(["", "abc", "0", "3", " 2 "])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    _, selected = MODULE.select_device()

    assert selected is devices[1]
    assert capsys.readouterr().out.count("输入无效") == 4


def test_select_device_eof_suggests_serial(monkeypatch):
    install_fake_adb(
        monkeypatch,
        [device("one", model="One"), device("two", model="Two")],
    )

    def end_of_input(_prompt):
        raise EOFError

    monkeypatch.setattr(builtins, "input", end_of_input)

    with pytest.raises(MODULE.AdbError, match="请使用 --serial 指定设备"):
        MODULE.select_device()


def test_notify_feishu_can_be_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(MODULE, "send_feishu_text", lambda *args, **kwargs: called.append(1))

    assert MODULE.notify_feishu("message", enabled=False) is False
    assert called == []


def test_notify_feishu_uses_selected_config(monkeypatch, tmp_path):
    calls = []

    def fake_send(message, config_path):
        calls.append((message, config_path))

    monkeypatch.setattr(MODULE, "send_feishu_text", fake_send)
    config_path = tmp_path / "feishu.json"

    assert MODULE.notify_feishu("任务结束", config_path=config_path) is True
    assert calls == [("任务结束", config_path)]


def test_main_notifies_when_task_completes(monkeypatch):
    messages = []
    device = SimpleNamespace(serial="cloud:1234", model="test-phone")
    state = SimpleNamespace(
        next_iteration=10,
        success_count=9,
        error_count=1,
        consecutive_failed_iterations=0,
    )
    monkeypatch.setattr(MODULE, "select_device", lambda serial: (object(), device))
    monkeypatch.setattr(MODULE, "collect_square_users", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        MODULE, "send_feishu_text", lambda message, config_path: messages.append(message)
    )

    assert MODULE.main(["--iterations", "10"]) == 0
    assert len(messages) == 1
    assert "任务完成" in messages[0]
    assert "完成轮次：10 / 10" in messages[0]


def test_main_notifies_when_runtime_device_reconnects(monkeypatch):
    messages = []
    selected = SimpleNamespace(serial="serial-1", model="Phone")
    state = SimpleNamespace(
        next_iteration=1,
        success_count=1,
        error_count=0,
        consecutive_failed_iterations=0,
    )
    monkeypatch.setattr(MODULE, "select_device", lambda serial: (object(), selected))

    def collect(*args, **kwargs):
        kwargs["on_device_reconnected"](selected)
        return state

    monkeypatch.setattr(MODULE, "collect_square_users", collect)
    monkeypatch.setattr(
        MODULE, "send_feishu_text", lambda message, config_path: messages.append(message)
    )

    assert MODULE.main(["--iterations", "1"]) == 0
    assert len(messages) == 2
    assert "设备重连成功" in messages[0]
    assert "采集任务已继续运行" in messages[0]


def test_main_notifies_failure_reason(monkeypatch):
    messages = []

    def fail(_serial):
        raise RuntimeError("设备连接断开")

    monkeypatch.setattr(MODULE, "select_device", fail)
    monkeypatch.setattr(
        MODULE, "send_feishu_text", lambda message, config_path: messages.append(message)
    )

    assert MODULE.main([]) == 1
    assert len(messages) == 1
    assert "任务失败" in messages[0]
    assert "失败原因：设备连接断开" in messages[0]
