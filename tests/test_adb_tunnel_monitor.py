import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


EXAMPLE = Path(__file__).parents[1] / "examples" / "adb_tunnel_monitor.py"
SPEC = importlib.util.spec_from_file_location("adb_tunnel_monitor", str(EXAMPLE))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_config(path):
    path.write_text(
        json.dumps(
            {
                "check_interval_seconds": 300,
                "adb": {
                    "path": "adb",
                    "host": "localhost",
                    "local_port": 62842,
                    "connect_timeout_seconds": 15,
                },
                "ssh": {
                    "path": "ssh",
                    "user": "s",
                    "host": "129.227.134.130",
                    "port": 1824,
                    "remote_host": "localhost",
                    "remote_port": 1,
                    "strict_host_key_checking": "accept-new",
                    "connect_timeout_seconds": 30,
                    "identity_file": "",
                },
            }
        ),
        encoding="utf-8",
    )


def test_load_settings_and_build_ssh_command(tmp_path):
    config = tmp_path / "monitor.json"
    write_config(config)
    settings = MODULE.load_settings(config)

    assert settings.check_interval_seconds == 300
    assert settings.adb.serial == "localhost:62842"
    command = MODULE.build_ssh_command(settings, "/usr/bin/ssh")
    assert command == [
        "/usr/bin/ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=30",
        "s@129.227.134.130",
        "-p",
        "1824",
        "-L",
        "62842:localhost:1",
        "-Nf",
    ]
    assert "secret" not in command


def test_connected_devices_only_returns_device_state(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [],
            0,
            "List of devices attached\n"
            "ready-1\tdevice\n"
            "bad-1\toffline\n"
            "bad-2\tunauthorized\n",
            "",
        ),
    )

    assert MODULE.connected_devices("/usr/bin/adb") == ["ready-1"]


def test_check_once_does_not_open_tunnel_when_any_device_is_ready(monkeypatch):
    config = Path(__file__)
    settings = MODULE.MonitorSettings(
        check_interval_seconds=300,
        adb=MODULE.AdbSettings("adb", "localhost", 62842, 15),
        ssh=MODULE.SshSettings(
            "ssh",
            "s",
            "example.test",
            1824,
            "localhost",
            1,
            "accept-new",
            30,
            None,
        ),
    )
    monkeypatch.setattr(MODULE, "resolve_command", lambda command: command)
    monkeypatch.setattr(MODULE, "connected_devices", lambda _path: ["phone-1"])
    monkeypatch.setattr(
        MODULE,
        "start_ssh_tunnel",
        lambda *args: pytest.fail("不应创建 SSH 隧道"),
    )

    assert MODULE.check_once(settings) is False
    assert config.exists()


def test_check_once_restores_tunnel_then_adb(monkeypatch):
    settings = MODULE.MonitorSettings(
        check_interval_seconds=300,
        adb=MODULE.AdbSettings("adb", "localhost", 62842, 15),
        ssh=MODULE.SshSettings(
            "ssh",
            "s",
            "example.test",
            1824,
            "localhost",
            1,
            "accept-new",
            30,
            None,
        ),
    )
    calls = []
    monkeypatch.setattr(MODULE, "resolve_command", lambda command: "/" + command)
    monkeypatch.setattr(MODULE, "connected_devices", lambda _path: [])
    monkeypatch.setattr(MODULE, "local_port_is_open", lambda *args: False)
    monkeypatch.setattr(
        MODULE,
        "start_ssh_tunnel",
        lambda selected, path, password: calls.append(
            ("ssh", selected, path, password)
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "connect_adb",
        lambda selected, path: calls.append(("adb", selected, path)),
    )

    state = MODULE.RuntimeState()
    assert MODULE.check_once(
        settings,
        state=state,
        password_reader=lambda _prompt: "memory-secret",
    ) is True
    assert calls == [
        ("ssh", settings, "/ssh", "memory-secret"),
        ("adb", settings, "/adb"),
    ]
    assert state.ssh_password == "memory-secret"


def test_connection_key_is_prompted_only_once_and_kept_in_memory(monkeypatch):
    settings = MODULE.MonitorSettings(
        check_interval_seconds=300,
        adb=MODULE.AdbSettings("adb", "localhost", 62842, 15),
        ssh=MODULE.SshSettings(
            "ssh",
            "s",
            "example.test",
            1824,
            "localhost",
            1,
            "accept-new",
            30,
            None,
        ),
    )
    state = MODULE.RuntimeState()
    prompts = []
    monkeypatch.setattr(MODULE, "resolve_command", lambda command: command)
    monkeypatch.setattr(MODULE, "connected_devices", lambda _path: [])
    monkeypatch.setattr(MODULE, "local_port_is_open", lambda *args: False)
    monkeypatch.setattr(MODULE, "connect_adb", lambda *args: None)
    monkeypatch.setattr(MODULE, "start_ssh_tunnel", lambda *args, **kwargs: None)

    for _ in range(2):
        MODULE.check_once(
            settings,
            state=state,
            password_reader=lambda prompt: prompts.append(prompt) or "secret",
        )

    assert len(prompts) == 1
    assert state.ssh_password == "secret"


def test_existing_tunnel_connects_adb_without_prompt(monkeypatch):
    settings = MODULE.MonitorSettings(
        check_interval_seconds=300,
        adb=MODULE.AdbSettings("adb", "localhost", 62842, 15),
        ssh=MODULE.SshSettings(
            "ssh",
            "s",
            "example.test",
            1824,
            "localhost",
            1,
            "accept-new",
            30,
            None,
        ),
    )
    calls = []
    monkeypatch.setattr(MODULE, "resolve_command", lambda command: command)
    monkeypatch.setattr(MODULE, "connected_devices", lambda _path: [])
    monkeypatch.setattr(MODULE, "local_port_is_open", lambda *args: True)
    monkeypatch.setattr(
        MODULE,
        "start_ssh_tunnel",
        lambda *args, **kwargs: calls.append("ssh"),
    )
    monkeypatch.setattr(
        MODULE,
        "connect_adb",
        lambda *args: calls.append("adb"),
    )

    MODULE.check_once(
        settings,
        state=MODULE.RuntimeState(),
        password_reader=lambda _prompt: pytest.fail("不应询问连接密钥"),
    )

    assert calls == ["ssh", "adb"]
