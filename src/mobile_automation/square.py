"""Resilient automation for collecting public profile data from Square."""

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import random
import re
import time
from typing import Callable, Dict, List, Optional

from .adb import AdbClient, DeviceUnavailableError
from .ui import UiNode, UiTree


PACKAGE = "com.baitu.qingshu"
NAV_ME_RESOURCE_ID = "com.baitu.qingshu:id/navMe"
FUN_ISLAND_XPATH = '//*[@text="Fun Island"]'
WIN_XPATH = '//*[@text="Win"]'
MAIN_ACTIVITY = "com.androidrtc.chat.modules.main.MainActivity"
SQUARE_ACTIVITY = "com.androidtool.common.webview.MyWebActivity"
PROFILE_ACTIVITY = "com.androidrtc.chat.modules.homepage.HomepageActivity"

CSV_FIELDS = [
    "collected_at",
    "iteration",
    "source_username",
    "source_amount",
    "profile_name",
    "user_id",
    "gender",
    "age",
    "country",
    "face_authentication",
    "following",
    "followers",
    "fan_club",
    "gift_gallery",
    "pk_rank",
    "bio",
    "interest_tags",
    "online_status",
    "participants_on_rank",
    "status",
    "error",
]


class PageState(Enum):
    SQUARE = "square"
    PROFILE = "profile"
    APP_HOME = "app_home"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SquareRecord:
    username: str
    amount: str
    username_node: UiNode
    win_node: UiNode


@dataclass
class RunState:
    target_iterations: int
    next_iteration: int = 0
    consecutive_failed_iterations: int = 0
    success_count: int = 0
    error_count: int = 0
    updated_at: str = ""


def is_square_page(tree: UiTree) -> bool:
    node = tree.find_by_text("Square")
    return node is not None and node.bounds is not None and node.bounds.area > 0


def is_profile_page(tree: UiTree) -> bool:
    has_information = tree.find_by_text("Personal Information") is not None
    has_id = any(node.text.strip().startswith("ID:") for node in tree.nodes)
    return has_information and has_id


def current_activity(client: AdbClient) -> str:
    output = client.shell("dumpsys", "activity", "activities", timeout=30)
    match = re.search(
        r"topResumedActivity=.*?\bu\d+\s+([^/\s]+)/([^\s}]+)", output
    )
    if not match:
        return ""
    return "{}/{}".format(match.group(1), match.group(2))


def classify_page(client: AdbClient, tree: Optional[UiTree] = None) -> PageState:
    tree = tree or UiTree.capture(client)
    activity = current_activity(client)
    if PROFILE_ACTIVITY in activity or is_profile_page(tree):
        return PageState.PROFILE
    if SQUARE_ACTIVITY in activity and is_square_page(tree):
        return PageState.SQUARE
    if MAIN_ACTIVITY in activity or tree.find_by_resource_id(NAV_ME_RESOURCE_ID):
        return PageState.APP_HOME
    return PageState.UNKNOWN


def find_last_visible_win_record(tree: UiTree) -> SquareRecord:
    parents = {child: parent for parent in tree.root.iter() for child in parent}
    wins = [
        node
        for node in tree.nodes
        if node.text.strip() == "Win"
        and node.bounds is not None
        and node.bounds.area > 0
    ]
    if not wins:
        raise RuntimeError("Square 页面没有可见的 Win 记录")

    win_node = max(wins, key=lambda node: node.bounds.center[1])
    parent = parents.get(win_node.element)
    if parent is None:
        raise RuntimeError("无法解析最后一条 Win 记录的父节点")

    siblings = [
        UiNode(element)
        for element in parent.iter("node")
        if element is not parent and element.attrib.get("text", "").strip()
    ]
    win_bounds = win_node.bounds
    left = [
        node
        for node in siblings
        if node.text.strip() != "Win"
        and node.bounds is not None
        and node.bounds.center[0] < win_bounds.center[0]
        and abs(node.bounds.center[1] - win_bounds.center[1]) <= 30
    ]
    right = [
        node
        for node in siblings
        if node.text.strip() != "Win"
        and node.bounds is not None
        and node.bounds.center[0] > win_bounds.center[0]
        and abs(node.bounds.center[1] - win_bounds.center[1]) <= 30
    ]
    if not left:
        raise RuntimeError("无法解析最后一条 Win 记录的用户名")

    username_node = max(left, key=lambda node: node.bounds.center[0])
    amount_node = min(right, key=lambda node: node.bounds.center[0]) if right else None
    return SquareRecord(
        username=username_node.text.strip(),
        amount=amount_node.text.strip() if amount_node else "",
        username_node=username_node,
        win_node=win_node,
    )


def wait_for_page(
    client: AdbClient,
    predicate: Callable[[UiTree], bool],
    page_name: str,
    timeout: float = 20.0,
    interval: float = 1.0,
) -> UiTree:
    deadline = time.monotonic() + timeout
    last_tree = None
    while time.monotonic() < deadline:
        last_tree = UiTree.capture(client)
        if predicate(last_tree):
            return last_tree
        time.sleep(interval)
    node_count = len(last_tree.nodes) if last_tree is not None else 0
    raise RuntimeError("等待 {} 页面超时，最后 UI 节点数={}".format(page_name, node_count))


def wait_for_state(
    client: AdbClient,
    expected: PageState,
    timeout: float = 20.0,
    interval: float = 1.0,
) -> UiTree:
    deadline = time.monotonic() + timeout
    last_state = PageState.UNKNOWN
    last_tree = None
    while time.monotonic() < deadline:
        last_tree = UiTree.capture(client)
        last_state = classify_page(client, last_tree)
        if last_state is expected:
            return last_tree
        time.sleep(interval)
    node_count = len(last_tree.nodes) if last_tree is not None else 0
    raise RuntimeError(
        "等待页面状态 {} 超时，最后状态={}，UI 节点数={}".format(
            expected.value, last_state.value, node_count
        )
    )


def wait_for_profile_stable(
    client: AdbClient, timeout: float = 20.0, interval: float = 1.0
) -> UiTree:
    deadline = time.monotonic() + timeout
    previous = None
    stable_count = 0
    while time.monotonic() < deadline:
        tree = UiTree.capture(client)
        if not is_profile_page(tree):
            previous = None
            stable_count = 0
            time.sleep(interval)
            continue
        signature = tuple(
            (node.text, node.element.attrib.get("bounds", ""))
            for node in _visible_text_nodes(tree)
        )
        if signature == previous:
            stable_count += 1
        else:
            stable_count = 0
        previous = signature
        if stable_count >= 1:
            return tree
        time.sleep(interval)
    raise RuntimeError("等待用户详情页面加载稳定超时")


def _wait_for_node(
    client: AdbClient,
    finder: Callable[[UiTree], Optional[UiNode]],
    description: str,
    output_dir: Path,
    max_attempts: int = 3,
    interval: float = 5.0,
) -> UiNode:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", description).strip("_") or "node"
    for attempt in range(1, max_attempts + 1):
        tree = UiTree.capture(client)
        path = output_dir / "ui_{}_{}_attempt{}.xml".format(
            timestamp, safe_name, attempt
        )
        path.write_text(tree.xml_text, encoding="utf-8")
        node = finder(tree)
        if node is not None:
            return node
        if attempt < max_attempts:
            print("未找到 {}，{} 秒后重试".format(description, interval))
            time.sleep(interval)
    raise RuntimeError("连续 {} 次未找到 {}".format(max_attempts, description))


def navigate_home_to_square(
    client: AdbClient, output_dir: Path = Path("artifacts/qingshu")
) -> UiTree:
    nav_me = _wait_for_node(
        client,
        lambda tree: tree.find_by_resource_id(NAV_ME_RESOURCE_ID),
        "navMe",
        output_dir,
    )
    x, y = UiTree.click(client, nav_me)
    print("已点击 navMe：({}, {})".format(x, y))

    fun_island = _wait_for_node(
        client,
        lambda tree: tree.find_xpath(FUN_ISLAND_XPATH),
        "Fun_Island",
        output_dir,
    )
    x, y = UiTree.click(client, fun_island)
    print("已点击 Fun Island：({}, {})".format(x, y))

    win = _wait_for_node(
        client,
        lambda tree: tree.find_xpath(WIN_XPATH),
        "Win",
        output_dir,
    )
    x, y = UiTree.click(client, win)
    print("已点击首页 Win：({}, {})".format(x, y))
    return wait_for_state(client, PageState.SQUARE, timeout=30)


def open_square_page(
    client: AdbClient,
    output_dir: Path = Path("artifacts/qingshu"),
    start_wait: float = 3.0,
) -> UiTree:
    client.stop_app(PACKAGE)
    time.sleep(0.5)
    client.keyevent("KEYCODE_HOME")
    time.sleep(0.8)
    client.start_app(PACKAGE)
    time.sleep(start_wait)
    print("已重新启动 {}，准备进入 Square".format(PACKAGE))
    return navigate_home_to_square(client, output_dir)


def recover_to_square(
    client: AdbClient,
    output_dir: Path,
    iteration: int,
    attempt: int,
) -> UiTree:
    tree = UiTree.capture(client)
    state = classify_page(client, tree)
    print(
        "[恢复 iteration={} attempt={}] 当前页面={}".format(
            iteration, attempt, state.value
        )
    )
    if state is PageState.SQUARE:
        return tree

    if state is PageState.PROFILE:
        client.keyevent("KEYCODE_BACK")
        try:
            return wait_for_state(client, PageState.SQUARE, timeout=12)
        except DeviceUnavailableError:
            raise
        except Exception:
            pass

    if state is PageState.APP_HOME:
        try:
            return navigate_home_to_square(client, output_dir)
        except DeviceUnavailableError:
            raise
        except Exception:
            pass

    for _ in range(2):
        client.keyevent("KEYCODE_BACK")
        time.sleep(1.0)
        tree = UiTree.capture(client)
        state = classify_page(client, tree)
        if state is PageState.SQUARE:
            return tree
        if state is PageState.APP_HOME:
            try:
                return navigate_home_to_square(client, output_dir)
            except DeviceUnavailableError:
                raise
            except Exception:
                break

    print("局部恢复失败，强制重启 App 并重新进入 Square")
    return open_square_page(client, output_dir)


def save_diagnostics(
    client: AdbClient,
    output_dir: Path,
    iteration: int,
    attempt: int,
    error: Exception,
) -> None:
    directory = output_dir / "recovery"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = "iteration_{}_attempt_{}_{}".format(iteration, attempt, timestamp)
    activity = ""
    try:
        activity = current_activity(client)
    except Exception:
        pass
    try:
        tree = UiTree.capture(client)
        (directory / "{}.xml".format(stem)).write_text(
            tree.xml_text, encoding="utf-8"
        )
    except Exception:
        pass
    try:
        client.screenshot(directory / "{}.png".format(stem))
    except Exception:
        pass
    (directory / "{}.txt".format(stem)).write_text(
        "activity={}\nerror={}\n".format(activity, error), encoding="utf-8"
    )


def parse_profile(tree: UiTree, gender: str = "unknown") -> Dict[str, str]:
    nodes = _visible_text_nodes(tree)
    id_node = next((node for node in nodes if node.text.strip().startswith("ID:")), None)
    if id_node is None:
        raise RuntimeError("用户详情页缺少 ID 字段")

    profile_name = _closest_above(nodes, id_node)
    personal = _find_text(nodes, "Personal Information")
    interest = _find_text(nodes, "Interest Tags")
    info_nodes = _nodes_below(nodes, personal, max_distance=130) if personal else []
    age_node = tree.find_by_resource_id("com.baitu.qingshu:id/tvAge")
    country_node = tree.find_by_resource_id("com.baitu.qingshu:id/tvCountryText")
    age = age_node.text.strip() if age_node else next(
        (
            node.text.strip()
            for node in info_nodes
            if re.fullmatch(r"\d{1,3}", node.text.strip())
        ),
        "",
    )
    country = country_node.text.strip() if country_node else next(
        (
            node.text.strip()
            for node in info_nodes
            if re.fullmatch(r"[A-Z]{2}", node.text.strip())
        ),
        "",
    )
    face_auth = next(
        (node.text.strip() for node in info_nodes if "Authentication" in node.text),
        "",
    )

    bio = ""
    if personal and interest and personal.bounds and interest.bounds:
        candidates = [
            node.text.strip()
            for node in nodes
            if node.bounds
            and node.bounds.top > personal.bounds.bottom
            and node.bounds.bottom < interest.bounds.top
            and node.text.strip() not in {age, country, face_auth}
            and not re.fullmatch(r"[^\w\s]{1,4}", node.text.strip())
        ]
        bio = max(candidates, key=len) if candidates else ""

    tags = []
    for node in tree.nodes:
        text = node.text.strip()
        if (
            node.resource_id == "com.baitu.qingshu:id/tv_interest_tag"
            and text
            and text not in tags
        ):
            tags.append(text)
    if not tags and interest and interest.bounds:
        for node in nodes:
            text = node.text.strip()
            if (
                node.bounds
                and node.bounds.top >= interest.bounds.bottom
                and text not in {"Message", "Online"}
                and text not in tags
            ):
                tags.append(text)

    return {
        "profile_name": profile_name.text.strip() if profile_name else "",
        "user_id": id_node.text.partition(":")[2].strip(),
        "gender": gender,
        "age": age,
        "country": country,
        "face_authentication": face_auth,
        "following": _value_left_of(nodes, "Following"),
        "followers": _value_left_of(nodes, "Followers"),
        "fan_club": _text_starting_with(nodes, "Fan Club"),
        "gift_gallery": _value_below(nodes, "Gift Gallery"),
        "pk_rank": _value_below(nodes, "PK Rank"),
        "bio": bio,
        "interest_tags": " | ".join(tags),
        "online_status": _find_text_value(nodes, "Online"),
        "participants_on_rank": _value_right_of(nodes, "Participants on rank:"),
    }


def append_csv(path: Path, row: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    migrate_csv_schema(path)
    write_header = not path.exists() or path.stat().st_size == 0
    safe_row = {field: _csv_safe(row.get(field, "")) for field in CSV_FIELDS}
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(safe_row)


def migrate_csv_schema(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        old_fields = reader.fieldnames or []
        if old_fields == CSV_FIELDS:
            return
        rows = list(reader)

    temporary = path.with_name("{}.schema.tmp".format(path.name))
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    temporary.replace(path)


def classify_gender_pixels(pixels) -> str:
    red_count = 0
    blue_count = 0
    for pixel in pixels:
        red, green, blue = pixel[:3]
        if red >= 90 and red > green * 1.15 and red > blue * 1.20:
            red_count += 1
        if blue >= 90 and blue > green * 1.05 and blue > red * 1.20:
            blue_count += 1
    minimum = 5
    if red_count >= minimum and red_count > blue_count * 1.30:
        return "female"
    if blue_count >= minimum and blue_count > red_count * 1.30:
        return "male"
    return "unknown"


def detect_gender_from_screenshot(tree: UiTree, screenshot_path: Path) -> str:
    gender_node = tree.find_by_resource_id("com.baitu.qingshu:id/ivGender")
    age_node = tree.find_by_resource_id("com.baitu.qingshu:id/tvAge")
    if gender_node is None or gender_node.bounds is None:
        return "unknown"
    bounds = gender_node.bounds
    left, top, right, bottom = bounds.left, bounds.top, bounds.right, bounds.bottom
    if age_node is not None and age_node.bounds is not None:
        left = min(left, age_node.bounds.left)
        top = min(top, age_node.bounds.top)
        right = max(right, age_node.bounds.right)
        bottom = max(bottom, age_node.bounds.bottom)

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("识别性别需要安装 Pillow") from exc

    with Image.open(str(screenshot_path)) as image:
        image = image.convert("RGB")
        left = max(0, min(left, image.width))
        top = max(0, min(top, image.height))
        right = max(left, min(right, image.width))
        bottom = max(top, min(bottom, image.height))
        if right <= left or bottom <= top:
            return "unknown"
        pixels = list(image.crop((left, top, right, bottom)).getdata())
    return classify_gender_pixels(pixels)


def default_state_path(csv_path: Path) -> Path:
    return csv_path.with_name("{}.state.json".format(csv_path.stem))


def load_run_state(
    csv_path: Path,
    state_path: Path,
    target_iterations: int,
    fresh: bool = False,
) -> RunState:
    if fresh:
        return RunState(target_iterations=target_iterations)
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            state = RunState(
                target_iterations=target_iterations,
                next_iteration=int(payload.get("next_iteration", 0)),
                consecutive_failed_iterations=int(
                    payload.get("consecutive_failed_iterations", 0)
                ),
                success_count=int(payload.get("success_count", 0)),
                error_count=int(payload.get("error_count", 0)),
                updated_at=str(payload.get("updated_at", "")),
            )
            state.next_iteration = min(state.next_iteration, target_iterations)
            return state
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    rows = []
    if csv_path.exists() and csv_path.stat().st_size > 0:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    iterations = []
    for row in rows:
        try:
            iterations.append(int(row.get("iteration", "")))
        except ValueError:
            continue
    consecutive = 0
    for row in reversed(rows):
        if row.get("status") == "error":
            consecutive += 1
        else:
            break
    return RunState(
        target_iterations=target_iterations,
        next_iteration=min(max(iterations) + 1 if iterations else 0, target_iterations),
        consecutive_failed_iterations=consecutive,
        success_count=sum(row.get("status") == "ok" for row in rows),
        error_count=sum(row.get("status") == "error" for row in rows),
    )


def save_run_state(path: Path, state: RunState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now().isoformat(timespec="seconds")
    temporary = path.with_name("{}.tmp".format(path.name))
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def collect_square_users(
    client: AdbClient,
    iterations: int = 10000,
    csv_path: Path = Path("artifacts/qingshu/users.csv"),
    min_delay: float = 1.0,
    max_delay: float = 5.0,
    output_dir: Path = Path("artifacts/qingshu"),
    state_path: Optional[Path] = None,
    fresh: bool = False,
    skip_gender_detection: bool = False,
    max_attempts: int = 3,
    max_consecutive_failed_iterations: int = 3,
    reconnect_retries: int = 10,
    reconnect_interval: float = 30,
    on_device_reconnected: Optional[Callable] = None,
) -> RunState:
    if not 0 <= iterations <= 10000:
        raise ValueError("iterations 必须在 0 到 10000 之间")
    if min_delay < 0 or max_delay < min_delay:
        raise ValueError("随机等待区间无效")
    state_path = state_path or default_state_path(csv_path)
    state = load_run_state(csv_path, state_path, iterations, fresh=fresh)
    save_run_state(state_path, state)
    print(
        "任务进度：从 iteration={} 继续，目标={}，成功={}，错误={}".format(
            state.next_iteration,
            iterations,
            state.success_count,
            state.error_count,
        )
    )

    for iteration in range(state.next_iteration, iterations):
        record = None
        last_error = None
        success_row = None

        while True:
            device_offline_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    square_tree = recover_to_square(
                        client, output_dir, iteration, attempt
                    )
                    record = find_last_visible_win_record(square_tree)
                    print(

                        "[{}/{} attempt={}/{}] 最后一条 Win：用户={}，金额={}".format(
                            iteration + 1,
                            iterations,
                            attempt,
                            max_attempts,
                            record.username,
                            record.amount,
                        )
                    )
                    x, y = square_tree.click(client, record.username_node)
                    print("点击同行用户名坐标=({}, {})".format(x, y))

                    profile_tree = wait_for_profile_stable(client)
                    gender = "unknown"
                    if skip_gender_detection:
                        print("已跳过性别识别，写入 unknown")
                    else:
                        screenshot_path = output_dir / "profile_current.png"
                        try:
                            client.screenshot(screenshot_path, timeout=60)
                            gender = detect_gender_from_screenshot(
                                profile_tree, screenshot_path
                            )
                        except DeviceUnavailableError:
                            raise
                        except Exception as gender_error:
                            print(
                                "性别识别失败，写入 unknown：{}".format(gender_error)
                            )
                    profile = parse_profile(profile_tree, gender=gender)
                    success_row = {
                        "collected_at": datetime.now().isoformat(timespec="seconds"),
                        "iteration": iteration,
                        "source_username": record.username,
                        "source_amount": record.amount,
                        "status": "ok",
                        "error": "",
                    }
                    success_row.update(profile)

                    client.keyevent("KEYCODE_BACK")
                    try:
                        wait_for_state(client, PageState.SQUARE, timeout=12)
                    except DeviceUnavailableError:
                        raise
                    except Exception:
                        recover_to_square(client, output_dir, iteration, attempt)

                    append_csv(csv_path, success_row)
                    print(
                        "[{}/{}] 已追加 CSV：user_id={}".format(
                            iteration + 1, iterations, profile.get("user_id", "")
                        )
                    )
                    break
                except DeviceUnavailableError as exc:
                    device_offline_error = exc
                    success_row = None
                    break
                except Exception as exc:
                    last_error = exc
                    success_row = None
                    save_diagnostics(client, output_dir, iteration, attempt, exc)
                    print(
                        "[{}/{} attempt={}/{}] 失败：{}".format(
                            iteration + 1, iterations, attempt, max_attempts, exc
                        )
                    )
                    if attempt < max_attempts:
                        try:
                            recover_to_square(client, output_dir, iteration, attempt)
                        except DeviceUnavailableError as recovery_error:
                            device_offline_error = recovery_error
                            break
                        except Exception as recovery_error:
                            save_diagnostics(
                                client,
                                output_dir,
                                iteration,
                                attempt,
                                recovery_error,
                            )
                            print("本次恢复失败：{}".format(recovery_error))

            if device_offline_error is None:
                break

            save_run_state(state_path, state)
            print(
                "[{}/{}] ADB 设备不可用，保留 iteration={} 并开始重连：{}".format(
                    iteration + 1,
                    iterations,
                    iteration,
                    device_offline_error,
                )
            )
            reconnected_device = client.reconnect_device(
                retry_count=reconnect_retries,
                retry_interval=reconnect_interval,
            )
            if on_device_reconnected is not None:
                on_device_reconnected(reconnected_device)
            print("设备已恢复，重启 Poppo 并重试当前 iteration={}".format(iteration))
            open_square_page(client, output_dir)
            record = None
            last_error = None
            success_row = None

        if success_row is not None:
            state.success_count += 1
            state.consecutive_failed_iterations = 0
        else:
            append_csv(
                csv_path,
                {
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                    "iteration": iteration,
                    "source_username": record.username if record else "",
                    "source_amount": record.amount if record else "",
                    "status": "error",
                    "error": str(last_error or "未知错误"),
                },
            )
            state.error_count += 1
            state.consecutive_failed_iterations += 1

        state.next_iteration = iteration + 1
        save_run_state(state_path, state)

        if (
            state.consecutive_failed_iterations
            >= max_consecutive_failed_iterations
        ):
            print(
                "连续 {} 轮完全恢复失败，任务安全停止；可从 iteration={} 续跑".format(
                    state.consecutive_failed_iterations, state.next_iteration
                )
            )
            return state

        if iteration + 1 < iterations:
            delay = random.uniform(min_delay, max_delay)
            print("下一轮将在 {:.2f} 秒后执行".format(delay))
            time.sleep(delay)

    return state


def _visible_text_nodes(tree: UiTree) -> List[UiNode]:
    return [
        node
        for node in tree.nodes
        if node.text.strip() and node.bounds is not None and node.bounds.area > 0
    ]


def _find_text(nodes: List[UiNode], text: str) -> Optional[UiNode]:
    return next((node for node in nodes if node.text.strip() == text), None)


def _find_text_value(nodes: List[UiNode], text: str) -> str:
    node = _find_text(nodes, text)
    return node.text.strip() if node else ""


def _text_starting_with(nodes: List[UiNode], prefix: str) -> str:
    node = next((node for node in nodes if node.text.strip().startswith(prefix)), None)
    return node.text.strip() if node else ""


def _closest_above(nodes: List[UiNode], anchor: UiNode) -> Optional[UiNode]:
    if anchor.bounds is None:
        return None
    candidates = [
        node
        for node in nodes
        if node is not anchor
        and node.bounds is not None
        and node.bounds.bottom <= anchor.bounds.top
        and anchor.bounds.top - node.bounds.bottom <= 150
        and node.text.strip() != "Online"
    ]
    return max(candidates, key=lambda node: node.bounds.bottom) if candidates else None


def _nodes_below(
    nodes: List[UiNode], anchor: UiNode, max_distance: int
) -> List[UiNode]:
    if anchor.bounds is None:
        return []
    return [
        node
        for node in nodes
        if node.bounds is not None
        and node.bounds.top >= anchor.bounds.bottom
        and node.bounds.top - anchor.bounds.bottom <= max_distance
    ]


def _value_left_of(nodes: List[UiNode], label: str) -> str:
    anchor = _find_text(nodes, label)
    if anchor is None or anchor.bounds is None:
        return ""
    candidates = [
        node
        for node in nodes
        if node.bounds is not None
        and node.bounds.right <= anchor.bounds.left
        and abs(node.bounds.center[1] - anchor.bounds.center[1]) <= 25
    ]
    node = max(candidates, key=lambda item: item.bounds.right) if candidates else None
    return node.text.strip() if node else ""


def _value_right_of(nodes: List[UiNode], label: str) -> str:
    anchor = _find_text(nodes, label)
    if anchor is None or anchor.bounds is None:
        return ""
    candidates = [
        node
        for node in nodes
        if node.bounds is not None
        and node.bounds.left >= anchor.bounds.right
        and abs(node.bounds.center[1] - anchor.bounds.center[1]) <= 25
    ]
    node = min(candidates, key=lambda item: item.bounds.left) if candidates else None
    return node.text.strip() if node else ""


def _value_below(nodes: List[UiNode], label: str) -> str:
    anchor = _find_text(nodes, label)
    if anchor is None or anchor.bounds is None:
        return ""
    candidates = [
        node
        for node in nodes
        if node.bounds is not None
        and node.bounds.top >= anchor.bounds.bottom
        and node.bounds.top - anchor.bounds.bottom <= 80
        and abs(node.bounds.center[0] - anchor.bounds.center[0]) <= 120
    ]
    node = min(candidates, key=lambda item: item.bounds.top) if candidates else None
    return node.text.strip() if node else ""


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'{}".format(value)
    return value
