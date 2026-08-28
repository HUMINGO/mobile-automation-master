"""Periodically restore an ADB connection through a configurable SSH tunnel."""

import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Sequence


DEFAULT_CONFIG_PATH = Path("config/adb_tunnel_monitor.local.json")


class MonitorError(RuntimeError):
    """Raised when configuration or connection recovery fails."""


@dataclass(frozen=True)
class AdbSettings:
    path: str
    host: str
    local_port: int
    connect_timeout_seconds: float

    @property
    def serial(self) -> str:
        return "{}:{}".format(self.host, self.local_port)


@dataclass(frozen=True)
class SshSettings:
    path: str
    user: str
    host: str
    port: int
    remote_host: str
    remote_port: int
    strict_host_key_checking: str
    connect_timeout_seconds: float
    identity_file: Optional[Path]


@dataclass(frozen=True)
class MonitorSettings:
    check_interval_seconds: float
    adb: AdbSettings
    ssh: SshSettings


@dataclass
class RuntimeState:
    """Sensitive runtime-only values that are never written to configuration."""

    ssh_password: Optional[str] = None


def _positive_number(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MonitorError("{} 必须是数字".format(field)) from exc
    if number <= 0:
        raise MonitorError("{} 必须大于 0".format(field))
    return number


def _port(value, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MonitorError("{} 必须是整数".format(field)) from exc
    if not 1 <= number <= 65535:
        raise MonitorError("{} 必须在 1 到 65535 之间".format(field))
    return number


def load_settings(path: Path) -> MonitorSettings:
    config_path = Path(path).expanduser()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MonitorError("找不到配置文件：{}".format(config_path)) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError("配置文件读取失败：{}".format(exc)) from exc

    adb = payload.get("adb", {})
    ssh = payload.get("ssh", {})
    required = {
        "adb.host": adb.get("host"),
        "adb.local_port": adb.get("local_port"),
        "ssh.user": ssh.get("user"),
        "ssh.host": ssh.get("host"),
        "ssh.port": ssh.get("port"),
        "ssh.remote_host": ssh.get("remote_host"),
        "ssh.remote_port": ssh.get("remote_port"),
    }
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        raise MonitorError("配置缺少字段：{}".format(", ".join(missing)))

    identity_value = str(ssh.get("identity_file", "")).strip()
    identity_file = Path(identity_value).expanduser() if identity_value else None
    if identity_file is not None and not identity_file.is_file():
        raise MonitorError("SSH 私钥文件不存在：{}".format(identity_file))

    return MonitorSettings(
        check_interval_seconds=_positive_number(
            payload.get("check_interval_seconds", 300),
            "check_interval_seconds",
        ),
        adb=AdbSettings(
            path=str(adb.get("path", "adb")),
            host=str(adb["host"]),
            local_port=_port(adb["local_port"], "adb.local_port"),
            connect_timeout_seconds=_positive_number(
                adb.get("connect_timeout_seconds", 15),
                "adb.connect_timeout_seconds",
            ),
        ),
        ssh=SshSettings(
            path=str(ssh.get("path", "ssh")),
            user=str(ssh["user"]),
            host=str(ssh["host"]),
            port=_port(ssh["port"], "ssh.port"),
            remote_host=str(ssh["remote_host"]),
            remote_port=_port(ssh["remote_port"], "ssh.remote_port"),
            strict_host_key_checking=str(
                ssh.get("strict_host_key_checking", "accept-new")
            ),
            connect_timeout_seconds=_positive_number(
                ssh.get("connect_timeout_seconds", 30),
                "ssh.connect_timeout_seconds",
            ),
            identity_file=identity_file,
        ),
    )


def resolve_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise MonitorError("找不到命令：{}".format(command))
    return resolved


def run_command(
    command: Sequence[str],
    timeout: float,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=env,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise MonitorError("命令执行超时：{}".format(command[0])) from exc
    except OSError as exc:
        raise MonitorError("命令执行失败：{}".format(exc)) from exc


def connected_devices(adb_path: str) -> List[str]:
    result = run_command([adb_path, "devices"], timeout=15)
    if result.returncode != 0:
        raise MonitorError(
            "adb devices 失败：{}".format(result.stderr.strip() or result.stdout.strip())
        )
    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def local_port_is_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def build_ssh_command(settings: MonitorSettings, ssh_path: str) -> List[str]:
    ssh = settings.ssh
    command = [
        ssh_path,
        "-o",
        "StrictHostKeyChecking={}".format(ssh.strict_host_key_checking),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout={}".format(max(1, int(ssh.connect_timeout_seconds))),
    ]
    if ssh.identity_file is not None:
        command.extend(["-i", str(ssh.identity_file)])
    command.extend(
        [
            "{}@{}".format(ssh.user, ssh.host),
            "-p",
            str(ssh.port),
            "-L",
            "{}:{}:{}".format(
                settings.adb.local_port,
                ssh.remote_host,
                ssh.remote_port,
            ),
            "-Nf",
        ]
    )
    return command


def _write_askpass_script(directory: Path, password: str) -> Path:
    script = directory / "ssh-askpass"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$ADB_TUNNEL_SSH_PASSWORD\"\n",
        encoding="utf-8",
    )
    script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return script


def start_ssh_tunnel(
    settings: MonitorSettings,
    ssh_path: str,
    password: Optional[str] = None,
) -> None:
    adb = settings.adb
    ssh = settings.ssh
    if local_port_is_open(adb.host, adb.local_port):
        print("SSH 隧道已监听：{}".format(adb.serial), flush=True)
        return

    command = build_ssh_command(settings, ssh_path)
    env = None
    if password:
        with tempfile.TemporaryDirectory(prefix="adb-tunnel-") as directory:
            helper = _write_askpass_script(Path(directory), password)
            env = os.environ.copy()
            env.update(
                {
                    "SSH_ASKPASS": str(helper),
                    "SSH_ASKPASS_REQUIRE": "force",
                    "DISPLAY": env.get("DISPLAY", "adb-tunnel-monitor"),
                    "ADB_TUNNEL_SSH_PASSWORD": password,
                }
            )
            result = run_command(
                command,
                timeout=ssh.connect_timeout_seconds + 5,
                env=env,
            )
    else:
        result = run_command(
            command,
            timeout=ssh.connect_timeout_seconds + 5,
        )
    if result.returncode != 0:
        raise MonitorError(
            "SSH 隧道创建失败：{}".format(
                result.stderr.strip() or result.stdout.strip()
            )
        )

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if local_port_is_open(adb.host, adb.local_port):
            print("SSH 隧道创建成功：{}".format(adb.serial), flush=True)
            return
        time.sleep(0.25)
    raise MonitorError("SSH 已返回，但本地端口未监听：{}".format(adb.serial))


def connect_adb(settings: MonitorSettings, adb_path: str) -> None:
    serial = settings.adb.serial
    result = run_command(
        [adb_path, "connect", serial],
        timeout=settings.adb.connect_timeout_seconds,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        raise MonitorError("adb connect 失败：{}".format(output))

    deadline = time.monotonic() + settings.adb.connect_timeout_seconds
    while time.monotonic() < deadline:
        if serial in connected_devices(adb_path):
            print("ADB 连接成功：{}".format(serial), flush=True)
            return
        time.sleep(0.5)
    raise MonitorError(
        "adb connect 已执行，但设备未进入 device 状态：{}；输出：{}".format(
            serial,
            output,
        )
    )


def check_once(
    settings: MonitorSettings,
    state: Optional[RuntimeState] = None,
    password_reader=getpass.getpass,
) -> bool:
    adb_path = resolve_command(settings.adb.path)
    devices = connected_devices(adb_path)
    if devices:
        print(
            "{} ADB 设备正常：{}".format(
                time.strftime("%Y-%m-%d %H:%M:%S"),
                ", ".join(devices),
            ),
            flush=True,
        )
        return False

    print(
        "{} 未发现 device 状态设备，开始恢复连接".format(
            time.strftime("%Y-%m-%d %H:%M:%S")
        ),
        flush=True,
    )
    ssh_path = resolve_command(settings.ssh.path)
    state = state if state is not None else RuntimeState()
    tunnel_exists = local_port_is_open(
        settings.adb.host,
        settings.adb.local_port,
    )
    if (
        not tunnel_exists
        and settings.ssh.identity_file is None
        and state.ssh_password is None
    ):
        try:
            state.ssh_password = password_reader(
                "请输入 SSH 连接密钥（不会写入磁盘）："
            )
        except EOFError as exc:
            raise MonitorError(
                "当前终端无法读取 SSH 连接密钥，请在交互式终端运行"
            ) from exc
        if not state.ssh_password:
            raise MonitorError("SSH 连接密钥不能为空")
    try:
        start_ssh_tunnel(settings, ssh_path, password=state.ssh_password)
    except MonitorError:
        if settings.ssh.identity_file is None:
            state.ssh_password = None
        raise
    connect_adb(settings, adb_path)
    return True


def run_monitor(settings: MonitorSettings, once: bool = False) -> int:
    state = RuntimeState()
    while True:
        try:
            check_once(settings, state=state)
        except MonitorError as exc:
            print(
                "{} 连接恢复失败：{}".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    exc,
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return 1
        if once:
            return 0
        time.sleep(settings.check_interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="JSON 配置文件",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只检测一次，适合验证配置",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except MonitorError as exc:
        print("配置错误：{}".format(exc), file=sys.stderr)
        return 2
    try:
        return run_monitor(settings, once=args.once)
    except KeyboardInterrupt:
        print("\n监控已停止")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
