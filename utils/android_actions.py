"""Common, reviewable actions used by generated Android test cases."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Optional, Union

from mobile_automation import AdbClient, UiNode, UiTree
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
    while time.monotonic() < deadline:
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
                return ready_tree
        time.sleep(poll_seconds)
    detail = "且未出现目标元素" if target_requested else ""
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


def generate_timestamps():
    # 函数功能：生成当前时间的时间戳字符串
    # 返回值：格式为"年月日_时分秒"的时间戳字符串，例如：20230815_143022
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def dismiss_known_popups(client) -> bool:
    """
    处理升级弹窗
    :param client:
    :return:
    """
    tree = UiTree.capture(client)

    # App Upgrade：只允许忽略，不自动点击 Upgrade。
    not_now = tree.find_by_text("Not Now")
    if not_now is not None:
        UiTree.click(client, not_now)
        return True

    return False
