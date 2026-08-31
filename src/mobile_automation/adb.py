"""Small, dependency-free wrapper around the Android Debug Bridge."""

import base64
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import List, Optional, Sequence


UNICODE_IME = "io.appium.settings/.UnicodeIME"
SCREEN_SIZE_PATTERN = re.compile(r"(?:Physical|Override) size:\s*(\d+)x(\d+)")


def encode_modified_utf7(text: str) -> str:
    """Encode Unicode text for the Appium Settings UnicodeIME."""
    result = []
    unicode_run = []

    def flush_unicode_run() -> None:
        if not unicode_run:
            return
        raw = "".join(unicode_run).encode("utf-16be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        result.append("&{}-".format(encoded))
        unicode_run[:] = []

    for char in text:
        if " " <= char <= "~":
            flush_unicode_run()
            result.append("&-" if char == "&" else char)
        else:
            unicode_run.append(char)
    flush_unicode_run()
    return "".join(result)


def escape_adb_input_text(text: str) -> str:
    """Escape text for Android's remote ``input text`` shell command."""
    escaped = text.replace("\\", "\\\\")
    for char in "()<>|;&*~\"'":
        escaped = escaped.replace(char, "\\" + char)
    return escaped.replace(" ", "%s")


class AdbError(RuntimeError):
    """Raised when ADB is unavailable or an ADB command fails."""


class DeviceUnavailableError(AdbError):
    """Raised when ADB cannot communicate with the selected device."""


class DeviceOfflineError(DeviceUnavailableError):
    """Raised when ADB specifically reports that the device is offline."""


class DeviceReconnectError(AdbError):
    """Raised after all attempts to reconnect an offline device fail."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""
    product: str = ""


class AdbClient:
    def __init__(self, serial: Optional[str] = None, adb_path: str = "adb") -> None:
        resolved = shutil.which(adb_path)
        if resolved is None:
            raise AdbError(
                "找不到 adb。请先安装 Android Platform Tools，并确认 adb 已加入 PATH。"
            )
        self.adb_path = resolved
        self.serial = serial
        self._original_input_method: Optional[str] = None

    def _command(self, args: Sequence[str], include_serial: bool = True) -> List[str]:
        command = [self.adb_path]
        if include_serial and self.serial:
            command.extend(["-s", self.serial])
        command.extend(args)
        return command

    def run(
        self,
        *args: str,
        include_serial: bool = True,
        binary: bool = False,
        timeout: float = 30,
    ):
        command = self._command(args, include_serial=include_serial)
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ADB 命令执行超时: {}".format(" ".join(command))) from exc

        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            normalized = message.casefold()
            if "device offline" in normalized:
                raise DeviceOfflineError(message or "adb: device offline")
            if (
                "no devices/emulators found" in normalized
                or ("device '" in normalized and "' not found" in normalized)
            ):
                raise DeviceUnavailableError(message or "ADB 设备不可用")
            raise AdbError(message or "ADB 命令执行失败")
        if binary:
            return result.stdout
        return result.stdout.decode("utf-8", errors="replace").strip()

    def devices(self) -> List[Device]:
        output = self.run("devices", "-l", include_serial=False)
        devices = []
        for line in output.splitlines()[1:]:
            if not line.strip():
                continue
            parts = line.split()
            serial, state = parts[0], parts[1]
            metadata = {}
            for part in parts[2:]:
                if ":" in part:
                    key, value = part.split(":", 1)
                    metadata[key] = value
            devices.append(
                Device(
                    serial=serial,
                    state=state,
                    model=metadata.get("model", ""),
                    product=metadata.get("product", ""),
                )
            )
        return devices

    def reconnect_device(
        self,
        retry_count: int = 10,
        retry_interval: float = 30,
        sleep=time.sleep,
    ) -> Device:
        """Reconnect the selected device and wait until ADB reports it ready."""
        if not self.serial:
            raise DeviceReconnectError("设备重连需要明确的 serial")
        if retry_count <= 0:
            raise ValueError("retry_count 必须大于 0")
        if retry_interval < 0:
            raise ValueError("retry_interval 不能为负数")

        last_error = "device offline"
        for attempt in range(1, retry_count + 1):
            wait_seconds = retry_interval * attempt
            sleep(wait_seconds)
            try:
                devices = self.devices()
            except AdbError as exc:
                last_error = str(exc)
                devices = []
            matched = next(
                (device for device in devices if device.serial == self.serial),
                None,
            )
            if matched is not None and matched.state == "device":
                return matched
            if matched is not None:
                last_error = "设备状态为 {}".format(matched.state)
            elif devices:
                last_error = "未找到指定设备 {}".format(self.serial)
            else:
                last_error = "ADB 当前未发现任何设备"

        raise DeviceReconnectError(
            "设备 {} 按 {} 秒递增等待重连 {} 次仍未恢复：{}".format(
                self.serial,
                retry_interval,
                retry_count,
                last_error,
            )
        )

    def shell(self, *args: str, timeout: float = 30) -> str:
        return self.run("shell", *args, timeout=timeout)

    def screen_size(self) -> tuple:
        """Return the active Android display size, honoring an override size."""
        matches = SCREEN_SIZE_PATTERN.findall(self.shell("wm", "size"))
        if not matches:
            raise AdbError("无法从 adb shell wm size 读取设备屏幕尺寸")
        width, height = matches[-1]
        return int(width), int(height)

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(
            "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)
        )

    def input_text(self, text: str) -> None:
        self.prepare_text_input(text)
        value = encode_modified_utf7(text) if not text.isascii() else text
        self.shell("input", "text", escape_adb_input_text(value))

    def prepare_text_input(self, text: str) -> None:
        """Select UnicodeIME before an input field is focused when needed."""
        if text.isascii() or self._original_input_method is not None:
            return
        current_ime = self.shell(
            "settings", "get", "secure", "default_input_method"
        ).strip()
        if current_ime == UNICODE_IME:
            return
        self._original_input_method = current_ime
        self.shell("ime", "enable", UNICODE_IME)
        self.shell("ime", "set", UNICODE_IME)
        time.sleep(0.5)

    def clear_text(self, length: int) -> None:
        """Clear the focused field without using clipboard APIs."""
        self.shell("input", "keyevent", "KEYCODE_MOVE_END")
        if length > 0:
            self.shell("input", "keyevent", *(["KEYCODE_DEL"] * (length * 2)))

    def restore_input_method(self) -> bool:
        original_ime = self._original_input_method
        self._original_input_method = None
        if original_ime and original_ime != "null" and original_ime != UNICODE_IME:
            self.shell("ime", "set", original_ime)
            time.sleep(0.5)
            return True
        return False

    def keyevent(self, keycode: str) -> None:
        self.shell("input", "keyevent", keycode)

    def start_app(self, package: str, activity: Optional[str] = None) -> None:
        if activity:
            self.shell("am", "start", "-n", "{}/{}".format(package, activity))
        else:
            self.shell(
                "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"
            )

    def stop_app(self, package: str) -> None:
        self.shell("am", "force-stop", package)

    def screenshot(self, target: Path, timeout: float = 60) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            self.run(
                "exec-out",
                "screencap",
                "-p",
                binary=True,
                timeout=timeout,
            )
        )
        return target

    def dump_ui(self) -> str:
        self.shell("uiautomator", "dump", "/sdcard/window_dump.xml")
        return self.shell("cat", "/sdcard/window_dump.xml")
