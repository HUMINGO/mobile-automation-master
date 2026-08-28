"""Execute declarative mobile automation tasks."""

from pathlib import Path
import time
from typing import Any, Dict, Iterable, Optional

from .adb import AdbClient, AdbError


class TaskRunner:
    def __init__(self, client: AdbClient, artifacts_dir: Path = Path("artifacts")) -> None:
        self.client = client
        self.artifacts_dir = artifacts_dir

    def run(self, steps: Iterable[Dict[str, Any]]) -> None:
        for index, step in enumerate(steps, start=1):
            action = step.get("action")
            if not action:
                raise ValueError("第 {} 步缺少 action".format(index))
            try:
                self._run_step(action, step)
            except (AdbError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "第 {} 步执行失败（{}）：{}".format(index, action, exc)
                ) from exc

    def _run_step(self, action: str, step: Dict[str, Any]) -> None:
        if action == "tap":
            self.client.tap(int(step["x"]), int(step["y"]))
        elif action == "swipe":
            self.client.swipe(
                int(step["x1"]), int(step["y1"]), int(step["x2"]), int(step["y2"]),
                int(step.get("duration_ms", 300)),
            )
        elif action == "input_text":
            self.client.input_text(str(step["text"]))
        elif action == "keyevent":
            self.client.keyevent(str(step["keycode"]))
        elif action == "start_app":
            self.client.start_app(str(step["package"]), _optional_string(step.get("activity")))
        elif action == "stop_app":
            self.client.stop_app(str(step["package"]))
        elif action == "wait":
            time.sleep(float(step.get("seconds", 1)))
        elif action == "screenshot":
            name = str(step.get("name", "screenshot.png"))
            self.client.screenshot(self.artifacts_dir / name)
        elif action == "assert_text":
            expected = str(step["text"])
            if expected not in self.client.dump_ui():
                raise AssertionError("页面中未找到文本：{}".format(expected))
        else:
            raise ValueError("不支持的 action：{}".format(action))


def _optional_string(value: Optional[Any]) -> Optional[str]:
    return None if value is None else str(value)

