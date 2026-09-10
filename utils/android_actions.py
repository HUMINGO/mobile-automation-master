"""Common, reviewable actions used by generated Android test cases."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Optional, Union

from mobile_automation import AdbClient, AdbError, UiNode, UiTree
from mobile_automation.reporting import record_device_step, record_screenshot
from datetime import datetime


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ElementNotFoundError(RuntimeError):
    """Raised when a UI element is not found within the configured attempts."""


class PageTransitionTimeout(RuntimeError):
    """Raised when a click does not lead to a detectable destination page."""


def _find_element(
    tree: UiTree,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    content_desc: Optional[str] = None,
    viewport_size: Optional[tuple] = None,
    min_visible_pixels: int = 24,
) -> Optional[UiNode]:
    """Find one exact locator that is actually visible in the viewport."""
    if not any((text, resource_id, content_desc)):
        raise ValueError("至少需要提供 text、resource_id 或 content_desc 中的一项")
    for node in tree.nodes:
        if text is not None and node.text != text:
            continue
        if resource_id is not None and node.resource_id != resource_id:
            continue
        if content_desc is not None and node.content_desc != content_desc:
            continue
        if not _is_visible_in_viewport(node, viewport_size, min_visible_pixels):
            continue
        return node
    return None


def _is_visible_in_viewport(
    node: UiNode, viewport_size: Optional[tuple], min_visible_pixels: int,
) -> bool:
    """Reject zero-sized, hidden and off-screen nodes kept in a UI hierarchy."""
    bounds = node.bounds
    if bounds is None or not node.visible_to_user:
        return False
    if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
        return False
    if viewport_size is None:
        return True
    width, height = viewport_size
    visible_width = min(bounds.right, width) - max(bounds.left, 0)
    visible_height = min(bounds.bottom, height) - max(bounds.top, 0)
    return visible_width >= min_visible_pixels and visible_height >= min_visible_pixels


def _viewport_size(client: AdbClient, tree: UiTree) -> tuple:
    """Prefer the physical screen; UI tree bounds can include off-screen rows."""
    get_screen_size = getattr(client, "screen_size", None)
    if callable(get_screen_size):
        width, height = get_screen_size()
        if width > 0 and height > 0:
            return width, height
    return tree.screen_size


def swipe_page(
    client: AdbClient,
    *,
    direction: str = "up",
    times: int = 1,
    duration_ms: int = 500,
    settle_seconds: float = 0.35,
) -> int:
    """Swipe the current page a fixed number of times.

    ``direction='up'`` moves page content upward (normally used to reveal
    content below the viewport); ``'down'`` does the opposite.  The Chinese
    aliases ``'向上'`` and ``'向下'`` are accepted as well.  The return value is
    the number of swipes actually issued, which is equal to ``times``.
    """
    if times < 0:
        raise ValueError("times 不能小于 0")
    if duration_ms <= 0:
        raise ValueError("duration_ms 必须大于 0")
    if settle_seconds < 0:
        raise ValueError("settle_seconds 不能小于 0")

    normalized_direction = {"向上": "up", "向下": "down"}.get(
        direction.casefold(), direction.casefold(),
    )
    if normalized_direction not in {"up", "down"}:
        raise ValueError("direction 仅支持 up、down、向上、向下")
    if times == 0:
        return 0

    get_screen_size = getattr(client, "screen_size", None)
    if not callable(get_screen_size):
        raise RuntimeError("客户端不支持获取屏幕尺寸，无法执行滑动")
    width, height = get_screen_size()
    if width <= 0 or height <= 0:
        raise RuntimeError("当前设备没有有效屏幕尺寸，无法执行滑动")

    center_x = width // 2
    if normalized_direction == "up":
        coordinates = (center_x, int(height * 0.75), center_x, int(height * 0.30))
    else:
        coordinates = (center_x, int(height * 0.30), center_x, int(height * 0.75))

    for _ in range(times):
        client.swipe(*coordinates, duration_ms=duration_ms)
        if settle_seconds:
            time.sleep(settle_seconds)

    print("页面已向{}滑动 {} 次。".format("上" if normalized_direction == "up" else "下", times))
    record_device_step(
        client,
        "页面滑动",
        "方向={}；次数={}；时长={}ms".format(normalized_direction, times, duration_ms),
    )
    return times


def swipe_until_element_visible(
    client: AdbClient,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    content_desc: Optional[str] = None,
    direction: str = "up",
    max_swipes: int = 10,
    duration_ms: int = 350,
    settle_seconds: float = 0.35,
    min_visible_pixels: int = 24,
) -> UiNode:
    """Swipe until an exactly located element appears in the current UI tree.

    ``direction='up'`` moves the page content upward, which is the common way
    to look for elements below the current viewport.  The returned ``UiNode``
    can be passed directly to ``UiTree.click``.
    """
    if max_swipes < 0:
        raise ValueError("max_swipes 不能小于 0")
    if duration_ms <= 0:
        raise ValueError("duration_ms 必须大于 0")
    if settle_seconds < 0:
        raise ValueError("settle_seconds 不能小于 0")
    if min_visible_pixels <= 0:
        raise ValueError("min_visible_pixels 必须大于 0")

    normalized_direction = direction.casefold()
    if normalized_direction not in {"up", "down", "left", "right"}:
        raise ValueError("direction 仅支持 up、down、left、right")

    last_tree: Optional[UiTree] = None
    for attempt in range(max_swipes + 1):
        tree = UiTree.capture(client)
        last_tree = tree
        viewport_size = _viewport_size(client, tree)
        node = _find_element(
            tree,
            text=text,
            resource_id=resource_id,
            content_desc=content_desc,
            viewport_size=viewport_size,
            min_visible_pixels=min_visible_pixels,
        )
        if node is not None:
            print("目标元素已在屏幕中出现：{}；已滑动 {} 次。".format(
                node.text or node.content_desc or node.resource_id, attempt,
            ))
            record_device_step(
                client,
                "滑动查找元素完成",
                "目标={}；方向={}；实际滑动 {} 次".format(
                    node.text or node.content_desc or node.resource_id,
                    normalized_direction,
                    attempt,
                ),
            )
            return node
        if attempt == max_swipes:
            break

        width, height = viewport_size
        if not width or not height:
            raise RuntimeError("当前 UI 树不包含有效屏幕尺寸，无法执行滑动")
        center_x, center_y = width // 2, height // 2
        if normalized_direction == "up":
            coordinates = (center_x, int(height * 0.75), center_x, int(height * 0.30))
        elif normalized_direction == "down":
            coordinates = (center_x, int(height * 0.30), center_x, int(height * 0.75))
        elif normalized_direction == "left":
            coordinates = (int(width * 0.80), center_y, int(width * 0.20), center_y)
        else:
            coordinates = (int(width * 0.20), center_y, int(width * 0.80), center_y)
        client.swipe(*coordinates, duration_ms=duration_ms)
        if settle_seconds:
            time.sleep(settle_seconds)

    locator = ", ".join(
        "{}={!r}".format(name, value)
        for name, value in (("text", text), ("resource_id", resource_id), ("content_desc", content_desc))
        if value is not None
    )
    screen_size = _viewport_size(client, last_tree) if last_tree is not None else (0, 0)
    raise ElementNotFoundError(
        "滑动 {} 次后仍未找到元素（{}）；当前屏幕尺寸={}"
        .format(max_swipes, locator, screen_size)
    )


def save_screenshot(client: AdbClient, output_path: Union[Path, str]) -> Path:
    """Capture the device screen; relative paths are based on project root."""
    path = Path(output_path)
    if path.suffix.casefold() != ".png":
        raise ValueError("截图文件必须使用 .png 后缀")
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    saved = client.screenshot(path)
    print("截图已保存：{}".format(saved.resolve()))
    record_screenshot(saved, detail="测试脚本显式保存的截图")
    return saved


def restart_app(
    client: AdbClient,
    package: str,
    activity: Optional[str] = None,
    *,
    wait_seconds: float = 1.0,
) -> None:
    """Stop an app and start it again, then wait for the launch to begin.

    When ``activity`` is omitted, Android launches the package's default
    launcher activity.  Pass an explicit activity for a deterministic entry
    point, for example ``".MainActivity"``.
    """
    if not package.strip():
        raise ValueError("package 不能为空")
    if wait_seconds < 0:
        raise ValueError("wait_seconds 不能小于 0")
    client.stop_app(package)
    client.start_app(package, activity)
    if wait_seconds:
        time.sleep(wait_seconds)
    print("App 已重启：{}".format(package))
    record_device_step(client, "重启 App", "包名={}".format(package))


def wait_for_element_visible(
    client: AdbClient,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    content_desc: Optional[str] = None,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.4,
) -> UiNode:
    """Wait until an exact locator is visible and can safely be clicked.

    This is intended for app startup and asynchronous rendering.  A successful
    launch command does not mean that the first screen's UI tree is ready yet.
    Transient UIAutomator dump failures are retried within the timeout.
    """
    if not any((text, resource_id, content_desc)):
        raise ValueError("至少需要提供 text、resource_id 或 content_desc 中的一项")
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("timeout_seconds 和 poll_seconds 必须大于 0")

    deadline = time.monotonic() + timeout_seconds
    last_capture_error: Optional[AdbError] = None
    while time.monotonic() < deadline:
        try:
            tree = UiTree.capture(client)
        except AdbError as exc:
            # The app window can be temporarily unavailable immediately after
            # force-stop/start.  Treat it as not-ready instead of failing fast.
            last_capture_error = exc
            time.sleep(poll_seconds)
            continue

        node = _find_element(
            tree,
            text=text,
            resource_id=resource_id,
            content_desc=content_desc,
            viewport_size=_viewport_size(client, tree),
        )
        if node is not None and node.enabled:
            label = content_desc or text or resource_id
            print("目标元素已就绪：{}".format(label))
            record_device_step(client, "等待元素可见", "目标={}".format(label))
            return node
        time.sleep(poll_seconds)

    locator = ", ".join(
        "{}={!r}".format(name, value)
        for name, value in (
            ("text", text),
            ("resource_id", resource_id),
            ("content_desc", content_desc),
        )
        if value is not None
    )
    detail = "；最近一次 UI 树读取错误：{}".format(last_capture_error) if last_capture_error else ""
    raise ElementNotFoundError(
        "{} 秒内未等到可见且可用的元素：{}{}".format(
            timeout_seconds, locator, detail
        )
    )


def wait_for_page_ready(
    client: AdbClient,
    previous_tree: UiTree,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    content_desc: Optional[str] = None,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
    settle_seconds: float = 0.5,
) -> UiTree:
    """Wait for a navigation to change UI, optionally asserting a target node.

    Capture ``previous_tree`` immediately before the navigation click.  When a
    destination locator is supplied, the method also requires that locator to
    be visibly present before returning.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if poll_seconds <= 0 or settle_seconds < 0:
        raise ValueError("poll_seconds 必须大于 0，settle_seconds 不能小于 0")
    target_requested = any((text, resource_id, content_desc))
    deadline = time.monotonic() + timeout_seconds
    page_changed = False
    last_capture_error: Optional[AdbError] = None
    while time.monotonic() < deadline:
        try:
            tree = UiTree.capture(client)
            page_changed = page_changed or tree.xml_text != previous_tree.xml_text
            if page_changed:
                viewport_size = _viewport_size(client, tree)
                target = _find_element(
                    tree,
                    text=text,
                    resource_id=resource_id,
                    content_desc=content_desc,
                    viewport_size=viewport_size,
                ) if target_requested else None
                if not target_requested or target is not None:
                    if settle_seconds:
                        time.sleep(settle_seconds)
                    ready_tree = UiTree.capture(client)
                    print("目标页面已就绪。")
                    record_device_step(client, "等待页面就绪", "已检测到页面切换")
                    return ready_tree
        except AdbError as exc:
            # UIAutomator can temporarily lose the active window while the
            # target page is animating in.  Continue polling until timeout.
            last_capture_error = exc
        time.sleep(poll_seconds)
    detail = "且未出现目标元素" if target_requested else ""
    if last_capture_error is not None:
        detail += "；最后一次 UI 树读取失败：{}".format(last_capture_error)
    raise PageTransitionTimeout(
        "{} 秒内未检测到页面切换{}".format(timeout_seconds, detail)
    )


def input_text_into_field(
    client: AdbClient,
    value: str,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    content_desc: Optional[str] = None,
    clear: bool = False,
    clear_length: Optional[int] = None,
) -> UiNode:
    """Locate an input field, focus it, optionally clear it, then enter text.

    For masked fields UIAutomator may not expose the current value.  Pass
    ``clear_length`` when ``clear=True`` to explicitly state how many existing
    characters should be removed.
    """
    if not isinstance(value, str):
        raise TypeError("value 必须是字符串")
    tree = UiTree.capture(client)
    node = _find_element(
        tree, text=text, resource_id=resource_id, content_desc=content_desc,
    )
    if node is None:
        raise ElementNotFoundError("当前 UI 树中未找到输入框")
    UiTree.click(client, node)
    if clear:
        length = len(node.text) if clear_length is None else clear_length
        if length < 0:
            raise ValueError("clear_length 不能小于 0")
        client.clear_text(length)
    client.input_text(value)
    return node


def clear_text_in_field(
    client: AdbClient,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    content_desc: Optional[str] = None,
    clear_length: Optional[int] = None,
) -> UiNode:
    """Locate an input field, focus it and clear its current text.

    ``clear_length`` is useful for masked controls where UIAutomator does not
    expose the visible value.  Otherwise the current node text is used.
    """
    tree = UiTree.capture(client)
    node = _find_element(
        tree, text=text, resource_id=resource_id, content_desc=content_desc,
    )
    if node is None:
        raise ElementNotFoundError("当前 UI 树中未找到输入框")
    UiTree.click(client, node)
    length = len(node.text) if clear_length is None else clear_length
    if length < 0:
        raise ValueError("clear_length 不能小于 0")
    client.clear_text(length)
    return node


def generate_timestamps():
    # 函数功能：生成当前时间的时间戳字符串
    # 返回值：格式为"年月日_时分秒"的时间戳字符串，例如：20230815_143022
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# def dismiss_known_popups(client) -> bool:
#     """
#     处理升级弹窗
#     :param client:
#     :return:
#     """
#     tree = UiTree.capture(client)
#
#     # App Upgrade：只允许忽略，不自动点击 Upgrade。
#     not_now = tree.find_by_text("Not Now")
#     if not_now is not None:
#         UiTree.click(client, not_now)
#         return True
#
#     return False

KNOWN_POPUPS = [
    {
        "name": "App Upgrade",
        "locator": {"text": "Not Now"},
        "action": "click",
    },
    {
        "name": "Android permission",
        "locator": {"resource_id": "com.android.permissioncontroller:id/permission_allow_button"},
        "action": "click",
    },
]

def _popup_click_target(tree: UiTree, node: UiNode) -> Optional[UiNode]:
    """Return a safe clickable container for a matched popup control.

    Some applications expose a button label as a non-clickable TextView and
    put the actual click handler on its parent.  Clicking the smallest
    clickable node covering the label works for both representations.
    """
    if node.clickable:
        return node
    if node.bounds is None:
        return None
    x, y = node.bounds.center
    candidates = tree.nodes_at(x, y, clickable_only=True)
    return candidates[0] if candidates else None


def dismiss_known_popups(
    client: AdbClient,
    *,
    max_rounds: int = 3,
    timeout_seconds: float = 4.0,
    poll_seconds: float = 0.4,
) -> list[str]:
    """Wait briefly for and dismiss only explicitly approved popup actions.

    App-launch popups are frequently displayed *after* the first UI dump.
    Therefore a single immediate capture is unreliable.  This helper polls
    during the launch window, dismisses one known popup at a time (including
    stacked dialogs), and never clicks an action that is not in
    :data:`KNOWN_POPUPS` -- for example, it will click ``Not Now`` but never
    ``Upgrade``.
    """
    if max_rounds < 0:
        raise ValueError("max_rounds 不能小于 0")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds 不能小于 0")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds 必须大于 0")

    dismissed: list[str] = []
    deadline = time.monotonic() + timeout_seconds

    while len(dismissed) < max_rounds:
        try:
            tree = UiTree.capture(client)
        except AdbError:
            # The app can be changing windows while it starts.  Keep polling
            # until the bounded launch window ends instead of failing a case.
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_seconds)
            continue

        target = None
        popup_name = ""
        for rule in KNOWN_POPUPS:
            node = _find_element(tree, **rule["locator"])
            if node is None:
                continue
            target = _popup_click_target(tree, node)
            popup_name = rule["name"]
            break

        if target is not None:
            UiTree.click(client, target)
            dismissed.append(popup_name)
            print("已关闭已知弹窗：{}".format(popup_name))
            # Give Android a short time to remove this layer before checking
            # for a second popup.
            time.sleep(poll_seconds)
            continue

        if time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)

    return dismissed
