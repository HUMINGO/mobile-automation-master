"""Optional, best-effort step telemetry for local UI test reports."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional


REPORT_DIR_ENV = "MOBILE_AUTOMATION_REPORT_DIR"
REPORT_CASE_ENV = "MOBILE_AUTOMATION_REPORT_CASE"

_SEQUENCES: Dict[str, int] = {}


def _context() -> Optional[tuple[Path, str]]:
    root = os.environ.get(REPORT_DIR_ENV)
    case_name = os.environ.get(REPORT_CASE_ENV)
    if not root or not case_name:
        return None
    return Path(root), case_name


def _next_sequence(root: Path, case_name: str) -> int:
    key = "{}:{}".format(root.resolve(), case_name)
    _SEQUENCES[key] = _SEQUENCES.get(key, 0) + 1
    return _SEQUENCES[key]


def _safe_name(value: str) -> str:
    compact = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return compact[:48] or "step"


def _append_event(root: Path, case_name: str, event: Dict[str, Any]) -> None:
    path = root / "steps" / case_name / "steps.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def record_device_step(
    client: Any,
    action: str,
    detail: str = "",
    *,
    capture_screenshot: bool = True,
) -> Optional[Dict[str, Any]]:
    """Record one action and, when enabled, a non-fatal device screenshot.

    Reporting must never turn an otherwise valid test into a failure.  Any
    screenshot issue is stored in the event instead of being raised.
    """
    context = _context()
    if context is None:
        return None
    root, case_name = context
    sequence = _next_sequence(root, case_name)
    event: Dict[str, Any] = {
        "index": sequence,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail,
    }
    if capture_screenshot:
        screenshot = root / "steps" / case_name / "{:03d}_{}.png".format(
            sequence, _safe_name(action)
        )
        try:
            client.screenshot(screenshot)
            event["screenshot"] = screenshot.relative_to(root).as_posix()
        except Exception as exc:  # Report collection is intentionally best-effort.
            event["screenshot_error"] = str(exc)
    _append_event(root, case_name, event)
    return event


def record_screenshot(path: Path, action: str = "保存截图", detail: str = "") -> Optional[Dict[str, Any]]:
    """Attach an explicitly saved screenshot to the active test report."""
    context = _context()
    if context is None:
        return None
    root, case_name = context
    sequence = _next_sequence(root, case_name)
    event: Dict[str, Any] = {
        "index": sequence,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail,
    }
    try:
        event["screenshot"] = os.path.relpath(Path(path).resolve(), root.resolve()).replace("\\", "/")
    except Exception as exc:
        event["screenshot_error"] = str(exc)
    _append_event(root, case_name, event)
    return event


def load_case_steps(root: Path, case_name: str) -> List[Dict[str, Any]]:
    """Read the events for one case; malformed lines do not break reporting."""
    path = Path(root) / "steps" / case_name / "steps.jsonl"
    if not path.is_file():
        return []
    steps: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            steps.append(item)
    return steps
