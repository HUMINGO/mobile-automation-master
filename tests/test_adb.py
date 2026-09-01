from pathlib import Path
from types import SimpleNamespace

import pytest

from mobile_automation.adb import (
    UNICODE_IME,
    AdbClient,
    AdbError,
    Device,
    DeviceOfflineError,
    DeviceReconnectError,
    DeviceUnavailableError,
    encode_modified_utf7,
    escape_adb_input_text,
)


def test_modified_utf7_encodes_unicode_runs_and_ampersand():
    assert encode_modified_utf7("hello") == "hello"
    assert encode_modified_utf7("A&B") == "A&-B"
    assert encode_modified_utf7("你好") == "&T2BZfQ-"
    assert encode_modified_utf7("😂") == "&2D3eAg-"


def test_adb_input_text_escapes_remote_shell_characters():
    assert escape_adb_input_text("hello world") == "hello%sworld"
    assert escape_adb_input_text("A&B") == r"A\&B"
    assert escape_adb_input_text("a'b") == r"a\'b"


class RecordingClient(AdbClient):
    def __init__(self):
        self.calls = []
        self._original_input_method = None

    def shell(self, *args, timeout=30):
        self.calls.append(args)
        if args[:4] == ("settings", "get", "secure", "default_input_method"):
            return "com.google.android.inputmethod.latin/.LatinIME"
        return ""


def test_ascii_input_uses_native_ime_without_switching():
    client = RecordingClient()
    client.input_text("hello world")
    assert client.calls == [("input", "text", "hello%sworld")]


def test_unicode_input_switches_once_and_restores_on_request(monkeypatch):
    monkeypatch.setattr("mobile_automation.adb.time.sleep", lambda _: None)
    client = RecordingClient()
    client.input_text("你好")
    client.input_text("😂")
    client.restore_input_method()
    assert client.calls == [
        ("settings", "get", "secure", "default_input_method"),
        ("ime", "enable", UNICODE_IME),
        ("ime", "set", UNICODE_IME),
        ("input", "text", r"\&T2BZfQ-"),
        ("input", "text", r"\&2D3eAg-"),
        ("ime", "set", "com.google.android.inputmethod.latin/.LatinIME"),
    ]
    assert client._original_input_method is None


def test_clear_text_moves_to_end_and_deletes_twice_the_visible_length():
    client = RecordingClient()
    client.clear_text(2)
    assert client.calls == [
        ("input", "keyevent", "KEYCODE_MOVE_END"),
        (
            "input",
            "keyevent",
            "KEYCODE_DEL",
            "KEYCODE_DEL",
            "KEYCODE_DEL",
            "KEYCODE_DEL",
        ),
    ]


class ScreenshotClient(AdbClient):
    def __init__(self):
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return b"png-data"


def test_screenshot_uses_sixty_second_timeout_without_retry(tmp_path: Path):
    client = ScreenshotClient()
    target = tmp_path / "screen.png"

    assert client.screenshot(target) == target
    assert target.read_bytes() == b"png-data"
    assert client.calls == [
        (
            ("exec-out", "screencap", "-p"),
            {"binary": True, "timeout": 60},
        )
    ]


def test_screen_size_prefers_the_override_size():
    class DisplayClient(AdbClient):
        def shell(self, *args, timeout=30):
            assert args == ("wm", "size")
            return "Physical size: 1080x2400\nOverride size: 720x1600"

    assert DisplayClient().screen_size() == (720, 1600)


def test_run_classifies_device_offline_separately(monkeypatch):
    client = AdbClient.__new__(AdbClient)
    client.adb_path = "adb"
    client.serial = "serial-1"
    monkeypatch.setattr(
        "mobile_automation.adb.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"ADB: DEVICE OFFLINE",
        ),
    )

    with pytest.raises(DeviceOfflineError, match="DEVICE OFFLINE"):
        client.run("shell", "true")


def test_run_keeps_other_adb_failures_generic(monkeypatch):
    client = AdbClient.__new__(AdbClient)
    client.adb_path = "adb"
    client.serial = "serial-1"
    monkeypatch.setattr(
        "mobile_automation.adb.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"permission denied",
        ),
    )

    with pytest.raises(AdbError) as error:
        client.run("shell", "true")
    assert type(error.value) is AdbError


def test_run_includes_stdout_when_adb_command_fails(monkeypatch):
    client = AdbClient.__new__(AdbClient)
    client.adb_path = "adb"
    client.serial = "serial-1"
    monkeypatch.setattr(
        "mobile_automation.adb.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"uiautomator service is not ready",
            stderr=b"",
        ),
    )

    with pytest.raises(AdbError, match="uiautomator service is not ready"):
        client.run("shell", "uiautomator", "dump")


def test_dump_ui_retries_a_transient_failure(monkeypatch):
    client = AdbClient.__new__(AdbClient)
    calls = []

    def shell(*args, timeout=30):
        calls.append(args)
        if len(calls) == 1:
            raise AdbError("window is transitioning")
        return "<hierarchy />" if args[0] == "cat" else "dumped"

    client.shell = shell
    monkeypatch.setattr("mobile_automation.adb.time.sleep", lambda _: None)

    assert client.dump_ui(retry_count=2, retry_interval=0) == "<hierarchy />"
    assert calls == [
        ("uiautomator", "dump", "/sdcard/window_dump.xml"),
        ("uiautomator", "dump", "/sdcard/window_dump.xml"),
        ("cat", "/sdcard/window_dump.xml"),
    ]


@pytest.mark.parametrize(
    "stderr",
    [
        b"adb: device 'QWV8XSU4FEOZ8D9H' not found",
        b"error: device 'serial-1' not found",
        b"adb: no devices/emulators found",
    ],
)
def test_run_classifies_missing_device_as_unavailable(monkeypatch, stderr):
    client = AdbClient.__new__(AdbClient)
    client.adb_path = "adb"
    client.serial = "serial-1"
    monkeypatch.setattr(
        "mobile_automation.adb.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=stderr,
        ),
    )

    with pytest.raises(DeviceUnavailableError):
        client.run("shell", "true")


class ReconnectingClient(AdbClient):
    def __init__(self, snapshots):
        self.serial = "serial-1"
        self.snapshots = iter(snapshots)
        self.reconnect_calls = 0

    def run(self, *args, **kwargs):
        assert args == ("reconnect",)
        self.reconnect_calls += 1
        return "reconnecting"

    def devices(self):
        return next(self.snapshots)


def test_reconnect_waits_and_returns_when_selected_device_is_ready():
    client = ReconnectingClient(
        [
            [Device("serial-1", "offline")],
            [Device("serial-1", "device", model="Phone")],
        ]
    )
    waits = []

    device = client.reconnect_device(
        retry_count=3,
        retry_interval=180,
        sleep=waits.append,
    )

    assert device.state == "device"
    assert waits == [180, 360]
    assert client.reconnect_calls == 0


def test_reconnect_does_not_reset_a_device_that_is_already_back():
    client = ReconnectingClient(
        [[Device("serial-1", "device", model="Phone")]]
    )
    waits = []

    device = client.reconnect_device(
        retry_count=10,
        retry_interval=30,
        sleep=waits.append,
    )

    assert device.state == "device"
    assert waits == [30]
    assert client.reconnect_calls == 0


def test_reconnect_raises_after_all_attempts():
    client = ReconnectingClient(
        [[Device("serial-1", "offline")]] * 3
    )

    with pytest.raises(DeviceReconnectError, match="重连 3 次仍未恢复"):
        client.reconnect_device(
            retry_count=3,
            retry_interval=0,
            sleep=lambda _: None,
        )
