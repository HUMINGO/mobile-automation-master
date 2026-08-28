"""Verified Poppo user search and one-to-one message workflow."""

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .adb import (
    AdbClient,
    AdbError,
    Device,
    DeviceReconnectError,
    DeviceUnavailableError,
)
from .square import PACKAGE, current_activity
from .ui import UiNode, UiTree


logger = logging.getLogger(__name__)


NAV_LIVE_ID = "com.baitu.qingshu:id/navLive"
SEARCH_INPUT_ID = "com.baitu.qingshu:id/etKeyword"
SEARCH_USER_ID = "com.baitu.qingshu:id/tvUserId"
SEARCH_USER_CARD_ID = "com.baitu.qingshu:id/userCard"
PROFILE_USER_ID = "com.baitu.qingshu:id/tv_id"
PROFILE_NICKNAME_ID = "com.baitu.qingshu:id/tv_nickname_and_tag"
PROFILE_STATUS_ID = "com.baitu.qingshu:id/statusText"
PROFILE_ROOM_STATUS_ID = "com.baitu.qingshu:id/roomState"
PROFILE_MESSAGE_ID = "com.baitu.qingshu:id/hitOn"
CHAT_ACTIVITY = "com.androidrtc.im.ui.chat.ChatActivity"
CHAT_TITLE_ID = "com.baitu.qingshu:id/tv_title"
CHAT_INPUT_ID = "com.baitu.qingshu:id/et_input"
CHAT_SEND_ID = "com.baitu.qingshu:id/btn_send"
CHAT_MESSAGE_ID = "com.baitu.qingshu:id/tvMsgText"
NAV_MESSAGE_ID = "com.baitu.qingshu:id/navMsg"
NAV_MESSAGE_UNREAD_ID = "com.baitu.qingshu:id/msgUnread"
MESSAGE_LIST_ID = "com.baitu.qingshu:id/chatList"
MESSAGE_ITEM_ID = "com.baitu.qingshu:id/itemLayout"
MESSAGE_NICKNAME_ID = "com.baitu.qingshu:id/nickname"
MESSAGE_UNREAD_ID = "com.baitu.qingshu:id/unread"
MESSAGE_PREVIEW_ID = "com.baitu.qingshu:id/formattedContent"

HISTORY_FIELDS = [
    "recorded_at",
    "user_id",
    "nickname",
    "status",
    "first_message",
    "reply",
    "followup_message",
    "error",
]

DEFAULT_TEMPLATE_CONFIG = Path("config/poppo_message_templates.json")
DEFAULT_APP_NAME = "yago"
ONLINE_PROFILE_STATUSES = frozenset({"online", "party"})
INITIAL_SEND_SKIP_STATUSES = frozenset(
    {"completed", "waiting_reply", "error", "user_not_found"}
)
UI_POLL_INTERVAL = 0.5
MESSAGE_SCROLL_DURATION_MS = 900
MESSAGE_SCROLL_SETTLE_SECONDS = 1.0


class UserNotFound(RuntimeError):
    """Raised when an exact user ID does not appear in search results."""


class UserOffline(RuntimeError):
    """Raised when the current profile status is not an allowed online state."""

    def __init__(self, user_id: str, nickname: str, observed_status: str) -> None:
        self.user_id = user_id
        self.nickname = nickname
        self.observed_status = observed_status
        super().__init__(
            "用户 {} 当前状态不是 Online：{}".format(user_id, observed_status)
        )


class MessagingTimeout(RuntimeError):
    """Raised when a verified UI transition or reply takes too long."""


@dataclass(frozen=True)
class MessageTarget:
    user_id: str
    first_message_template: str = "hello  {nickname}"
    followup_message: str = "OK"


@dataclass
class MessageResult:
    user_id: str
    nickname: str = ""
    status: str = "error"
    first_message: str = ""
    reply: str = ""
    followup_message: str = ""
    error: str = ""
    recorded_at: str = ""


@dataclass(frozen=True)
class MessageListItem:
    """A direct-chat row currently rendered in the Message list."""

    node: UiNode
    nickname: str
    unread: str
    preview: str


OFFICIAL_MESSAGE_ROWS = frozenset(
    {"official announcement", "new followers", "system message", "stranger message"}
)


def find_live_home_search_button(tree: UiTree) -> Optional[UiNode]:
    """Find the ID-less magnifier verified on the navLive home page."""
    candidates = [
        node
        for node in tree.nodes
        if node.clickable
        and not node.resource_id
        and node.bounds is not None
        and 530 <= node.bounds.left <= 570
        and 60 <= node.bounds.top <= 100
        and node.bounds.right <= 630
        and node.bounds.bottom <= 170
    ]
    return min(candidates, key=lambda node: node.bounds.left) if candidates else None


def find_exact_user_card(tree: UiTree, user_id: str) -> Optional[UiNode]:
    expected = "ID:{}".format(user_id.strip())
    target = next(
        (
            node
            for node in tree.nodes
            if node.resource_id == SEARCH_USER_ID and node.text.strip() == expected
        ),
        None,
    )
    if target is None:
        return None
    parents = {child: parent for parent in tree.root.iter() for child in parent}
    element = target.element
    while element is not None:
        if element.attrib.get("resource-id") == SEARCH_USER_CARD_ID:
            return UiNode(element)
        element = parents.get(element)
    return None


def exact_profile_user_id(tree: UiTree) -> str:
    for node in tree.nodes:
        if node.resource_id == PROFILE_USER_ID and node.text.strip().startswith("ID:"):
            return node.text.partition(":")[2].strip()
    return ""


def profile_online_status(tree: UiTree) -> str:
    """Return the live profile status, using ``missing`` when unavailable."""
    for resource_id in (PROFILE_STATUS_ID, PROFILE_ROOM_STATUS_ID):
        node = tree.find_by_resource_id(resource_id)
        status = node.text.strip() if node is not None else ""
        if status:
            return status
    return "missing"


def is_online_profile_status(status: str) -> bool:
    """Return whether a profile status allows messaging."""
    return status.strip().casefold() in ONLINE_PROFILE_STATUSES


def chat_messages(tree: UiTree) -> List[Tuple[str, str, int]]:
    """Return visible messages as (direction, text, top)."""
    width, _ = tree.screen_size
    midpoint = width / 2.0
    messages = []
    for node in tree.nodes:
        if node.resource_id != CHAT_MESSAGE_ID or node.bounds is None:
            continue
        direction = "incoming" if node.bounds.center[0] < midpoint else "outgoing"
        messages.append((direction, node.text, node.bounds.top))
    return sorted(messages, key=lambda item: item[2])


def new_incoming_reply(
    before: Iterable[Tuple[str, str, int]],
    after: Iterable[Tuple[str, str, int]],
) -> str:
    previous = Counter(text for direction, text, _ in before if direction == "incoming")
    for direction, text, _ in after:
        if direction != "incoming":
            continue
        if previous[text]:
            previous[text] -= 1
        else:
            return text
    return ""


def reply_after_outgoing(
    messages: Iterable[Tuple[str, str, int]], first_message: str
) -> str:
    messages = list(messages)
    sent_tops = [
        top
        for direction, text, top in messages
        if direction == "outgoing" and text == first_message
    ]
    if not sent_tops:
        return ""
    last_sent_top = max(sent_tops)
    return next(
        (
            text
            for direction, text, top in messages
            if direction == "incoming" and top > last_sent_top
        ),
        "",
    )


def message_list_items(tree: UiTree) -> List[MessageListItem]:
    """Return rendered direct-message rows, excluding Poppo system buckets."""
    items = []
    for row in tree.nodes:
        if row.resource_id != MESSAGE_ITEM_ID:
            continue
        descendants = [UiNode(element) for element in row.element.iter("node")]
        nickname_node = next(
            (node for node in descendants if node.resource_id == MESSAGE_NICKNAME_ID),
            None,
        )
        if nickname_node is None or not nickname_node.text.strip():
            continue
        nickname = nickname_node.text.strip()
        official_label = any(
            node.resource_id.endswith(":id/tvLabel")
            and node.text.strip().casefold() == "official"
            for node in descendants
        )
        if official_label or nickname.casefold() in OFFICIAL_MESSAGE_ROWS:
            continue
        unread_node = next(
            (node for node in descendants if node.resource_id == MESSAGE_UNREAD_ID),
            None,
        )
        preview_node = next(
            (node for node in descendants if node.resource_id == MESSAGE_PREVIEW_ID),
            None,
        )
        items.append(
            MessageListItem(
                node=row,
                nickname=nickname,
                unread=unread_node.text.strip() if unread_node else "",
                preview=preview_node.text.strip() if preview_node else "",
            )
        )
    return items


def nav_message_has_unread(tree: UiTree) -> bool:
    """Return whether the main-navigation Message button has a non-zero badge."""
    node = tree.find_by_resource_id(NAV_MESSAGE_UNREAD_ID)
    value = node.text.strip() if node is not None else ""
    if not value:
        return False
    if value.isdigit():
        return int(value) > 0
    return True


def _nickname_prefix(value: str) -> Tuple[str, bool]:
    normalized = " ".join(value.replace("\xa0", " ").split()).casefold()
    positions = [
        position
        for marker in ("...", "…")
        for position in [normalized.find(marker)]
        if position >= 0
    ]
    if not positions:
        return normalized, False
    return normalized[: min(positions)].rstrip(), True


def nicknames_match(left: str, right: str) -> bool:
    """Match exact names or a UI-truncated ellipsis prefix."""
    left_value, left_truncated = _nickname_prefix(left)
    right_value, right_truncated = _nickname_prefix(right)
    if left_value == right_value:
        return True
    if left_truncated and left_value and right_value.startswith(left_value):
        return True
    if right_truncated and right_value and left_value.startswith(right_value):
        return True
    return False


def load_history(path: Path) -> List[Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_history_by_user(path: Path) -> Dict[str, Dict[str, str]]:
    latest = {}
    for row in load_history(path):
        user_id = row.get("user_id", "").strip()
        if user_id:
            latest[user_id] = row
    return latest


def append_message_history(path: Path, result: MessageResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    if not result.recorded_at:
        result.recorded_at = datetime.now().isoformat(timespec="seconds")
    row = asdict(result)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in HISTORY_FIELDS})


def load_message_templates(path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("无法读取文案 JSON：{}".format(path)) from exc
    if not isinstance(payload.get("country_routes"), dict) or not isinstance(
        payload.get("templates"), dict
    ):
        raise ValueError("文案 JSON 缺少 country_routes 或 templates")
    return payload


def _render_message_template(template: str, app_name: str) -> str:
    return template.replace("{{name}}", "{nickname}").replace(
        "{{app_name}}", app_name
    )


def load_approved_targets(
    path: Path,
    template_path: Path = DEFAULT_TEMPLATE_CONFIG,
    app_name: str = DEFAULT_APP_NAME,
) -> List[MessageTarget]:
    """Load only explicitly approved rows, newest duplicate wins."""
    app_name = app_name.strip() or DEFAULT_APP_NAME
    config = load_message_templates(template_path)
    routes = config["country_routes"]
    templates = config["templates"]
    fallback = str(config.get("default_language_tag", "en"))
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    targets = []
    seen = set()
    for row in reversed(rows):
        user_id = row.get("user_id", "").strip()
        if not user_id or user_id in seen or row.get("status", "").strip() != "approved":
            continue
        country = row.get("country", "").strip().upper()
        route = routes.get(country, {})
        language_tag = str(route.get("language_tag", fallback))
        language_template = templates.get(language_tag)
        if not isinstance(language_template, dict):
            logger.warning(
                "国家语言模板缺失，改用默认模板：country=%s missing_language_tag=%s fallback=%s",
                country or "unknown",
                language_tag,
                fallback,
            )
            language_tag = fallback
            language_template = templates.get(language_tag)
        if not isinstance(language_template, dict):
            raise ValueError("默认语言模板不存在：{}".format(fallback))
        greeting = str(language_template.get("greeting", ""))
        after_reply = str(language_template.get("after_reply", ""))
        if not greeting or not after_reply:
            raise ValueError("语言模板 {} 内容不完整".format(language_tag))
        targets.append(
            MessageTarget(
                user_id=user_id,
                first_message_template=_render_message_template(
                    greeting, app_name
                ),
                followup_message=_render_message_template(
                    after_reply, app_name
                ),
            )
        )
        seen.add(user_id)
    return list(reversed(targets))


class PoppoMessenger:
    def __init__(
        self,
        client: AdbClient,
        output_dir: Path = Path("artifacts/qingshu/messaging"),
        transition_timeout: float = 20.0,
        poll_interval: float = 2.0,
        online_only: bool = False,
        reconnect_retries: int = 10,
        reconnect_interval: float = 30,
        on_device_reconnected: Optional[Callable[[Device], None]] = None,
    ) -> None:
        self.client = client
        self.output_dir = output_dir
        self.transition_timeout = transition_timeout
        self.poll_interval = poll_interval
        self.online_only = online_only
        self.reconnect_retries = reconnect_retries
        self.reconnect_interval = reconnect_interval
        self.on_device_reconnected = on_device_reconnected
        self._offline_skipped_reply_users = set()

    def run_targets(
        self,
        targets: Iterable[MessageTarget],
        history_path: Path,
        round_size: int = 5,
        monitor_forever: bool = False,
        skip_initial_send: bool = False,
        initial_success_limit: Optional[int] = None,
    ) -> List[MessageResult]:
        """Send first messages in rounds and process replies between rounds."""
        if round_size <= 0:
            raise ValueError("round_size 必须大于 0")
        if initial_success_limit is not None and initial_success_limit < 0:
            raise ValueError("initial_success_limit 必须大于或等于 0")
        targets = list(targets)
        results = []
        latest = latest_history_by_user(history_path)
        eligible_initial_count = sum(
            latest.get(target.user_id, {}).get("status")
            not in INITIAL_SEND_SKIP_STATUSES
            for target in targets
        )
        waiting_count = sum(
            latest.get(target.user_id, {}).get("status") == "waiting_reply"
            for target in targets
        )
        send_initial = (
            not skip_initial_send
            and initial_success_limit != 0
        )
        if (
            (not send_initial or not eligible_initial_count)
            and not waiting_count
            and not monitor_forever
        ):
            logger.info("没有待处理用户：目标为空或最新状态均要求跳过")
            return results
        logger.info(
            "开始私信流程：可尝试首发用户=%d waiting_reply=%d "
            "round_size=%d success_limit=%s",
            eligible_initial_count if send_initial else 0,
            waiting_count,
            round_size,
            initial_success_limit if initial_success_limit is not None else "all",
        )
        if skip_initial_send:
            logger.info("已启用跳过首发模式，直接进入 Message 页等待回复")
        elif initial_success_limit == 0:
            logger.info("首发成功配额为 0，跳过首发并直接处理已有回复")

        if skip_initial_send or initial_success_limit == 0:
            target_batches = []
            targets_by_user = {target.user_id: target for target in targets}
        elif initial_success_limit is None:
            target_batches = [targets]
            targets_by_user = {}
        else:
            target_batches = [
                targets[index : index + initial_success_limit]
                for index in range(0, len(targets), initial_success_limit)
            ]
            targets_by_user = {}
        try:
            self._start_app_once()
        except DeviceUnavailableError:
            self._recover_offline_device()
        try:
            successful_in_round = 0
            successful_initial_total = 0
            attempts_since_check = 0
            attempted_initial_total = 0
            checked_replies = False
            quota_reached = False
            for batch_index, batch in enumerate(target_batches, start=1):
                targets_by_user.update(
                    {target.user_id: target for target in batch}
                )
                pending_batch = [
                    target
                    for target in batch
                    if latest.get(target.user_id, {}).get("status")
                    not in INITIAL_SEND_SKIP_STATUSES
                ]
                logger.info(
                    "加载首发批次：batch=%d batch_targets=%d "
                    "batch_pending=%d success=%d/%s",
                    batch_index,
                    len(batch),
                    len(pending_batch),
                    successful_initial_total,
                    initial_success_limit,
                )
                for target in pending_batch:
                    attempts_since_check += 1
                    attempted_initial_total += 1
                    logger.info(
                        "开始首发用户：attempt=%d user_id=%s "
                        "round_success=%d/%d total_success=%d/%s",
                        attempted_initial_total,
                        target.user_id,
                        successful_in_round,
                        round_size,
                        successful_initial_total,
                        initial_success_limit
                        if initial_success_limit is not None
                        else "all",
                    )
                    try:
                        result = self.run_target(target, history_path)
                    except DeviceUnavailableError as exc:
                        result = MessageResult(
                            user_id=target.user_id,
                            status="device_offline",
                            error=str(exc),
                        )
                        append_message_history(history_path, result)
                        logger.error(
                            "设备不可用，跳过当前用户并开始重连："
                            "user_id=%s error=%s",
                            target.user_id,
                            exc,
                        )
                        results.append(result)
                        latest[target.user_id] = asdict(result)
                        self._recover_offline_device()
                        attempts_since_check = 0
                        continue
                    except UserNotFound as exc:
                        result = MessageResult(
                            user_id=target.user_id,
                            status="user_not_found",
                            error=str(exc),
                        )
                        self._save_diagnostics(target.user_id, exc)
                        append_message_history(history_path, result)
                        logger.warning(
                            "用户查找失败：user_id=%s error=%s",
                            target.user_id,
                            exc,
                        )
                    except UserOffline as exc:
                        result = MessageResult(
                            user_id=target.user_id,
                            nickname=exc.nickname,
                            status="offline",
                            error=exc.observed_status,
                        )
                        append_message_history(history_path, result)
                        logger.info(
                            "用户非在线状态，跳过："
                            "user_id=%s profile_status=%s",
                            target.user_id,
                            exc.observed_status,
                        )
                    except Exception as exc:
                        result = MessageResult(
                            user_id=target.user_id,
                            status="error",
                            error=str(exc),
                        )
                        self._save_diagnostics(target.user_id, exc)
                        append_message_history(history_path, result)
                        logger.exception(
                            "用户处理失败：user_id=%s", target.user_id
                        )
                    results.append(result)
                    latest[target.user_id] = asdict(result)
                    logger.info(
                        "用户处理结束：user_id=%s status=%s",
                        target.user_id,
                        result.status,
                    )
                    if result.status == "waiting_reply":
                        successful_in_round += 1
                        successful_initial_total += 1
                    if successful_in_round == round_size:
                        logger.info(
                            "本轮首发成功满 %d 人，开始集中检查回复",
                            round_size,
                        )
                        results.extend(
                            self._process_waiting_replies_if_nav_unread(
                                targets_by_user,
                                history_path,
                            )
                        )
                        latest = latest_history_by_user(history_path)
                        successful_in_round = 0
                        attempts_since_check = 0
                        checked_replies = True
                    if (
                        initial_success_limit is not None
                        and successful_initial_total >= initial_success_limit
                    ):
                        quota_reached = True
                        logger.info(
                            "首发成功配额已完成：success=%d limit=%d",
                            successful_initial_total,
                            initial_success_limit,
                        )
                        break
                if quota_reached:
                    break

            if (
                initial_success_limit is not None
                and not quota_reached
                and send_initial
            ):
                logger.info(
                    "计划数据已耗尽：首发成功=%d 配额=%d",
                    successful_initial_total,
                    initial_success_limit,
                )

            active_waiting_count = sum(
                latest.get(user_id, {}).get("status") == "waiting_reply"
                for user_id in targets_by_user
            )
            if attempts_since_check or (
                not checked_replies and active_waiting_count
            ):
                logger.info(
                    "最后一轮结束：首发成功=%d，开始集中检查回复",
                    successful_in_round,
                )
                results.extend(
                    self._process_waiting_replies_if_nav_unread(
                        targets_by_user,
                        history_path,
                    )
                )
                checked_replies = True

            if monitor_forever:
                try:
                    self._open_message_page()
                except DeviceUnavailableError:
                    self._recover_offline_device()
                    self._open_message_page()
                logger.info(
                    "首发目标已处理完，驻留 Message 页持续监控；间隔=%ss",
                    self.poll_interval,
                )
                while True:
                    time.sleep(max(0.1, self.poll_interval))
                    results.extend(
                        self._process_waiting_replies(targets_by_user, history_path)
                    )
        except KeyboardInterrupt:
            logger.info("收到手动停止信号，返回并停留在 Message 列表页")
            try:
                self._open_message_page()
            except Exception as exc:
                logger.warning("停止时无法返回 Message 列表页：%s", exc)
        finally:
            try:
                self._restore_input_method()
            except AdbError as exc:
                logger.warning("任务结束时恢复输入法失败：%s", exc)
        logger.info("私信流程结束：结果记录=%d", len(results))
        return results

    def verify_target(self, user_id: str) -> MessageResult:
        """Navigate through the exact-user chat chain without sending anything."""
        try:
            self._start_app_once()
            profile, nickname = self._open_profile(user_id)
            _, nickname = self._open_chat(profile, user_id, nickname)
            return MessageResult(
                user_id=user_id, nickname=nickname, status="verified_no_send"
            )
        except UserNotFound as exc:
            self._save_diagnostics(user_id, exc)
            return MessageResult(
                user_id=user_id, status="user_not_found", error=str(exc)
            )
        except UserOffline as exc:
            return MessageResult(
                user_id=user_id,
                nickname=exc.nickname,
                status="offline",
                error=exc.observed_status,
            )
        except Exception as exc:
            self._save_diagnostics(user_id, exc)
            return MessageResult(user_id=user_id, status="error", error=str(exc))

    def run_target(
        self,
        target: MessageTarget,
        history_path: Path,
    ) -> MessageResult:
        """Send and persist one initial message without waiting in the chat."""
        profile_tree, nickname = self._open_profile(target.user_id)
        _, nickname = self._open_chat(profile_tree, target.user_id, nickname)
        first_message = target.first_message_template.format(
            nickname=nickname, user_id=target.user_id
        )
        logger.info("发送首条消息：user_id=%s", target.user_id)
        self._send_message(first_message)
        waiting = MessageResult(
            user_id=target.user_id,
            nickname=nickname,
            status="waiting_reply",
            first_message=first_message,
            followup_message=target.followup_message,
        )
        append_message_history(history_path, waiting)
        logger.info("首条消息已确认并记录 waiting_reply：user_id=%s", target.user_id)
        return waiting

    def _process_waiting_replies(
        self,
        targets_by_user: Dict[str, MessageTarget],
        history_path: Path,
    ) -> List[MessageResult]:
        """Process all currently actionable replies, restarting at list top each time."""
        completed = []
        ignored_rows = set()
        while True:
            latest = latest_history_by_user(history_path)
            waiting = {
                user_id: row
                for user_id, row in latest.items()
                if user_id in targets_by_user and row.get("status") == "waiting_reply"
                and user_id not in self._offline_skipped_reply_users
            }
            try:
                self._open_message_page()
            except DeviceUnavailableError as exc:
                logger.error("扫描回复时设备不可用，开始重连：%s", exc)
                self._recover_offline_device()
                continue
            if not waiting:
                logger.info("当前计划没有 waiting_reply 用户")
                return completed
            try:
                candidate = self._find_reply_candidate(ignored_rows)
            except DeviceUnavailableError as exc:
                logger.error("查找回复会话时设备不可用，开始重连：%s", exc)
                self._recover_offline_device()
                continue
            if candidate is None:
                logger.info("Message 页当前没有可处理的非系统未读会话")
                return completed
            item = candidate
            row_key = (item.nickname, item.preview)
            logger.info(
                "发现未读候选会话：nickname=%s unread=%s waiting_users=%d",
                item.nickname,
                item.unread,
                len(waiting),
            )
            matched_user = ""
            reply = ""
            try:
                self._retry_click(
                    item.node,
                    lambda: CHAT_ACTIVITY in current_activity(self.client),
                    "Message 会话 {}".format(item.nickname),
                )
                chat = self._wait_for(
                    lambda tree: tree.find_by_resource_id(CHAT_INPUT_ID) is not None,
                    "用户会话 {}".format(item.nickname),
                )
                messages = chat_messages(chat)
                matches = []
                for user_id, row in waiting.items():
                    first_message = row.get("first_message", "")
                    observed_reply = reply_after_outgoing(messages, first_message)
                    if first_message and observed_reply:
                        matches.append((user_id, observed_reply))
                if len(matches) != 1:
                    logger.warning(
                        "未读会话历史核对结果不唯一，跳过：nickname=%s matches=%d",
                        item.nickname,
                        len(matches),
                    )
                    ignored_rows.add(row_key)
                    continue
                matched_user, reply = matches[0]

                row = waiting[matched_user]
                target = targets_by_user[matched_user]
                followup = row.get("followup_message", "") or target.followup_message
                logger.info("回复核对通过，发送后续消息：user_id=%s", matched_user)
                self._send_message(followup)
                result = MessageResult(
                    user_id=matched_user,
                    nickname=row.get("nickname", item.nickname),
                    status="completed",
                    first_message=row.get("first_message", ""),
                    reply=reply,
                    followup_message=followup,
                )
                append_message_history(history_path, result)
                completed.append(result)
                logger.info("后续消息已确认，记录 completed：user_id=%s", matched_user)
            except DeviceUnavailableError as exc:
                if matched_user and matched_user in waiting:
                    row = waiting[matched_user]
                    target = targets_by_user[matched_user]
                    retry = MessageResult(
                        user_id=matched_user,
                        nickname=row.get("nickname", item.nickname),
                        status="waiting_reply",
                        first_message=row.get("first_message", ""),
                        reply=reply or row.get("reply", ""),
                        followup_message=row.get("followup_message", "")
                        or target.followup_message,
                        error=str(exc),
                    )
                    append_message_history(history_path, retry)
                    self._offline_skipped_reply_users.add(matched_user)
                    logger.error(
                        "回复处理时设备不可用，本次运行跳过用户：user_id=%s",
                        matched_user,
                    )
                else:
                    logger.error("候选会话处理时设备不可用：nickname=%s", item.nickname)
                ignored_rows.add(row_key)
                self._recover_offline_device()
            except Exception as exc:
                if matched_user and matched_user in waiting:
                    row = waiting[matched_user]
                    target = targets_by_user[matched_user]
                    retry = MessageResult(
                        user_id=matched_user,
                        nickname=row.get("nickname", item.nickname),
                        status="waiting_reply",
                        first_message=row.get("first_message", ""),
                        reply=reply or row.get("reply", ""),
                        followup_message=row.get("followup_message", "")
                        or target.followup_message,
                        error=str(exc),
                    )
                    append_message_history(history_path, retry)
                    self._save_diagnostics(matched_user, exc)
                    logger.exception(
                        "后续消息发送失败，保持 waiting_reply：user_id=%s",
                        matched_user,
                    )
                else:
                    logger.exception("候选会话处理失败：nickname=%s", item.nickname)
                ignored_rows.add(row_key)

    def _process_waiting_replies_if_nav_unread(
        self,
        targets_by_user: Dict[str, MessageTarget],
        history_path: Path,
    ) -> List[MessageResult]:
        """Skip a round reply scan unless navMsg currently shows unread mail."""
        while True:
            try:
                navigation = self._return_to_navigation()
                break
            except DeviceUnavailableError as exc:
                logger.error("检查 navMsg 未读角标时设备不可用，开始重连：%s", exc)
                self._recover_offline_device()
        unread_node = navigation.find_by_resource_id(NAV_MESSAGE_UNREAD_ID)
        unread_text = unread_node.text.strip() if unread_node is not None else ""
        if not nav_message_has_unread(navigation):
            logger.info(
                "navMsg 没有未读角标，本轮跳过 Message 回复扫描：unread=%s",
                unread_text or "0",
            )
            return []
        logger.info(
            "navMsg 存在未读角标，开始完整扫描 Message 列表：unread=%s",
            unread_text,
        )
        return self._process_waiting_replies(targets_by_user, history_path)

    def _recover_offline_device(self) -> None:
        logger.warning(
            "等待设备恢复：serial=%s interval=%ss retries=%d",
            self.client.serial,
            self.reconnect_interval,
            self.reconnect_retries,
        )
        device = self.client.reconnect_device(
            retry_count=self.reconnect_retries,
            retry_interval=self.reconnect_interval,
        )
        logger.info("设备重连成功：serial=%s model=%s", device.serial, device.model)
        if self.on_device_reconnected is not None:
            self.on_device_reconnected(device)
        try:
            self._start_app_once()
        except DeviceUnavailableError as exc:
            raise DeviceReconnectError(
                "设备 {} 重连后在重启 Poppo 时再次离线：{}".format(
                    self.client.serial,
                    exc,
                )
            ) from exc

    def _find_reply_candidate(
        self,
        ignored_rows: set,
        max_pages: int = 100,
    ) -> Optional[MessageListItem]:
        """Find the next unread non-system row across the chat list."""
        self._scroll_message_list_to_top()
        previous_signature = None
        for _ in range(max_pages):
            tree = UiTree.capture(self.client)
            rows = message_list_items(tree)
            signature = tuple(
                (item.nickname, item.preview, item.unread) for item in rows
            )
            for item in rows:
                row_key = (item.nickname, item.preview)
                if row_key in ignored_rows:
                    continue
                if item.unread:
                    return item
            if not rows or signature == previous_signature:
                return None
            previous_signature = signature
            self._scroll_message_list(forward=True)
        logger.warning("Message 列表扫描达到最大页数=%d", max_pages)
        return None

    def _open_message_page(self) -> UiTree:
        navigation = self._return_to_navigation()
        nav_message = navigation.find_by_resource_id(NAV_MESSAGE_ID)
        if nav_message is None:
            raise MessagingTimeout("主导航缺少 navMsg")
        logger.info("点击 navMsg 进入 Message 页")
        self._retry_click(
            nav_message,
            lambda: UiTree.capture(self.client).find_by_resource_id(MESSAGE_LIST_ID)
            is not None,
            "navMsg",
        )
        page = self._wait_for(
            lambda tree: tree.find_by_resource_id(MESSAGE_LIST_ID) is not None,
            "Message 列表",
        )
        logger.info("Message 列表加载完成")
        return page

    def _return_to_navigation(self, max_back_steps: int = 5) -> UiTree:
        tree = UiTree.capture(self.client)
        for back_steps in range(max_back_steps + 1):
            if (
                tree.find_by_resource_id(NAV_LIVE_ID) is not None
                and tree.find_by_resource_id(NAV_MESSAGE_ID) is not None
            ):
                logger.info("已返回主导航：返回次数=%d", back_steps)
                return tree
            if back_steps == max_back_steps:
                break
            logger.info("当前页面没有主导航，执行返回：第%d次", back_steps + 1)
            self.client.keyevent("KEYCODE_BACK")
            time.sleep(1.0)
            tree = UiTree.capture(self.client)
        raise MessagingTimeout(
            "连续返回 {} 次后仍未找到主导航".format(max_back_steps)
        )

    def _scroll_message_list_to_top(self, max_swipes: int = 100) -> None:
        previous_signature = None
        for _ in range(max_swipes):
            tree = UiTree.capture(self.client)
            signature = tuple(
                (item.nickname, item.preview, item.unread)
                for item in message_list_items(tree)
            )
            if not signature:
                return
            if signature == previous_signature:
                return
            previous_signature = signature
            self._scroll_message_list(forward=False)

    def _scroll_message_list(self, forward: bool) -> None:
        tree = UiTree.capture(self.client)
        chat_list = tree.find_by_resource_id(MESSAGE_LIST_ID)
        if chat_list is None or chat_list.bounds is None:
            raise MessagingTimeout("Message 页缺少 chatList")
        bounds = chat_list.bounds
        x = bounds.center[0]
        height = bounds.bottom - bounds.top
        # Keep a large overlap between adjacent captures. A long RecyclerView swipe
        # can coast past a row near the page boundary before uiautomator captures
        # the next tree, especially while avatars are still being laid out.
        upper = bounds.top + max(100, height * 35 // 100)
        lower = bounds.top + min(height - 100, height * 70 // 100)
        start_y, end_y = (lower, upper) if forward else (upper, lower)
        self.client.swipe(
            x,
            start_y,
            x,
            end_y,
            MESSAGE_SCROLL_DURATION_MS,
        )
        time.sleep(MESSAGE_SCROLL_SETTLE_SECONDS)

    def _open_profile(self, user_id: str) -> Tuple[UiTree, str]:
        self._restore_input_method()
        logger.info("准备搜索用户：user_id=%s", user_id)
        home = self._return_to_live_home()
        search = find_live_home_search_button(home)
        if search is None:
            raise MessagingTimeout("navLive 首页缺少顶部查询按钮")
        self._retry_click(
            search,
            lambda: UiTree.capture(self.client).find_by_resource_id(SEARCH_INPUT_ID)
            is not None,
            "首页查询按钮",
        )
        search_tree = UiTree.capture(self.client)
        input_node = search_tree.find_by_resource_id(SEARCH_INPUT_ID)
        if input_node is None:
            raise MessagingTimeout("用户搜索输入框未出现")
        self._focus_and_type(input_node, user_id, SEARCH_INPUT_ID)
        logger.info("已输入用户 ID，提交搜索：user_id=%s", user_id)
        tree = UiTree.capture(self.client)
        width, height = tree.screen_size
        if not width or not height:
            width, height = 720, 1640
        self.client.tap(width - 59, height - 156)

        try:
            results = self._wait_for(
                lambda item: find_exact_user_card(item, user_id) is not None,
                "用户 {} 搜索结果".format(user_id),
            )
        except MessagingTimeout as exc:
            raise UserNotFound("找不到用户 {}".format(user_id)) from exc
        card = find_exact_user_card(results, user_id)
        if card is None:
            raise UserNotFound("找不到用户 {}".format(user_id))
        logger.info("找到精确搜索结果，进入详情页：user_id=%s", user_id)
        self._retry_click(
            card,
            lambda: exact_profile_user_id(UiTree.capture(self.client)) == user_id,
            "用户卡片 {}".format(user_id),
        )
        profile = self._wait_for(
            lambda item: exact_profile_user_id(item) == user_id,
            "用户 {} 详情页".format(user_id),
        )
        nickname_node = profile.find_by_resource_id(PROFILE_NICKNAME_ID)
        nickname = nickname_node.text.strip() if nickname_node else ""
        if not nickname:
            raise RuntimeError("用户 {} 详情页缺少昵称".format(user_id))
        logger.info("详情页身份核对通过：user_id=%s nickname=%s", user_id, nickname)
        self._ensure_profile_is_online(profile, user_id, nickname)
        return profile, nickname

    def _ensure_profile_is_online(
        self, profile: UiTree, user_id: str, nickname: str
    ) -> None:
        if not self.online_only:
            logger.info("未启用 --online-only，跳过状态检查：user_id=%s", user_id)
            return
        observed_status = profile_online_status(profile)
        logger.info(
            "读取个人页实时状态：user_id=%s profile_status=%s",
            user_id,
            observed_status,
        )
        if not is_online_profile_status(observed_status):
            raise UserOffline(user_id, nickname, observed_status)
        logger.info("在线状态检查通过：user_id=%s", user_id)

    def _start_app_once(self) -> UiTree:
        logger.info("强制关闭并重新启动 Poppo：package=%s", PACKAGE)
        self.client.stop_app(PACKAGE)
        time.sleep(0.5)
        self.client.start_app(PACKAGE)
        tree = self._wait_for(
            lambda tree: tree.find_by_resource_id(NAV_LIVE_ID) is not None,
            "Poppo 主页面",
            timeout=max(30.0, self.transition_timeout),
        )
        logger.info("Poppo 主页面加载完成")
        return tree

    def _return_to_live_home(self, max_back_steps: int = 5) -> UiTree:
        """Recover from search/profile/chat using Back, then enter navLive."""
        tree = UiTree.capture(self.client)
        for back_steps in range(max_back_steps + 1):
            activity = current_activity(self.client)
            if activity and activity.split("/", 1)[0] != PACKAGE:
                logger.warning(
                    "返回过程中已离开 Poppo，强制重启：activity=%s back_steps=%d",
                    activity,
                    back_steps,
                )
                return self._enter_live_home(self._start_app_once(), "重启后的 navLive")
            nav_live = tree.find_by_resource_id(NAV_LIVE_ID)
            if nav_live is not None:
                logger.info("已找到 navLive：返回次数=%d", back_steps)
                return self._enter_live_home(tree, "navLive")
            if back_steps == max_back_steps:
                break
            logger.info("当前页面没有 navLive，执行返回：第%d次", back_steps + 1)
            self.client.keyevent("KEYCODE_BACK")
            time.sleep(1.0)
            tree = UiTree.capture(self.client)
        logger.warning(
            "连续返回 %d 次仍未找到 navLive，强制重启 Poppo",
            max_back_steps,
        )
        return self._enter_live_home(self._start_app_once(), "重启后的 navLive")

    def _enter_live_home(self, navigation: UiTree, description: str) -> UiTree:
        nav_live = navigation.find_by_resource_id(NAV_LIVE_ID)
        if nav_live is None:
            raise MessagingTimeout("{} 缺少 navLive".format(description))
        self._retry_click(
            nav_live,
            lambda: self._live_home_ready(),
            description,
        )
        home = UiTree.capture(self.client)
        if find_live_home_search_button(home) is None:
            raise MessagingTimeout("点击 {} 后未进入首页".format(description))
        logger.info("已进入 Poppo 导航首页")
        return home

    def _open_chat(
        self, profile: UiTree, user_id: str, nickname: str
    ) -> Tuple[UiTree, str]:
        logger.info("点击 Message 进入会话：user_id=%s", user_id)
        message_button = profile.find_by_resource_id(PROFILE_MESSAGE_ID)
        if message_button is None:
            raise RuntimeError("用户 {} 详情页缺少 Message 按钮".format(user_id))
        self._retry_click(
            message_button,
            lambda: CHAT_ACTIVITY in current_activity(self.client),
            "Message",
        )
        chat = self._wait_for(
            lambda tree: tree.find_by_resource_id(CHAT_INPUT_ID) is not None
            and tree.find_by_resource_id(CHAT_TITLE_ID) is not None,
            "用户 {} 会话".format(user_id),
        )
        title = chat.find_by_resource_id(CHAT_TITLE_ID)
        chat_nickname = title.text.strip() if title else ""
        if not chat_nickname:
            raise RuntimeError("用户 {} 会话缺少标题".format(user_id))
        logger.info("会话页面核对完成：user_id=%s title=%s", user_id, chat_nickname)
        return chat, chat_nickname or nickname

    def _send_message(self, message: str) -> None:
        try:
            tree = UiTree.capture(self.client)
            before_count = sum(
                direction == "outgoing" and text == message
                for direction, text, _ in chat_messages(tree)
            )
            input_node = tree.find_by_resource_id(CHAT_INPUT_ID)
            if input_node is None:
                raise RuntimeError("会话输入框不存在")
            self._focus_and_type(input_node, message, CHAT_INPUT_ID)
            for _ in range(4):
                tree = UiTree.capture(self.client)
                send = tree.find_by_resource_id(CHAT_SEND_ID)
                if send is None:
                    raise RuntimeError("输入消息后未出现发送按钮")
                UiTree.click(self.client, send)
                time.sleep(1.0)
                after = UiTree.capture(self.client)
                after_count = sum(
                    direction == "outgoing" and text == message
                    for direction, text, _ in chat_messages(after)
                )
                input_after = after.find_by_resource_id(CHAT_INPUT_ID)
                if (
                    after_count > before_count
                    and input_after is not None
                    and input_after.text.replace("\xa0", " ").strip()
                    in {"", "Say something"}
                ):
                    logger.info("消息气泡确认成功")
                    return
            raise MessagingTimeout(
                "消息发送后未在聊天记录中确认：{}".format(message)
            )
        finally:
            self._restore_input_method()

    def _restore_input_method(self) -> None:
        restore_input_method = getattr(
            getattr(self, "client", None), "restore_input_method", None
        )
        if callable(restore_input_method) and restore_input_method():
            logger.info("已恢复原输入法")

    def _focus_and_type(self, node: UiNode, text: str, resource_id: str) -> None:
        prepare_text_input = getattr(self.client, "prepare_text_input", None)
        if callable(prepare_text_input):
            prepare_text_input(text)
            prepared_node = UiTree.capture(self.client).find_by_resource_id(resource_id)
            if prepared_node is None:
                raise MessagingTimeout(
                    "准备输入法后控件 {} 已离开当前页面".format(resource_id)
                )
            node = prepared_node

        for attempt in range(1, 4):
            UiTree.click(self.client, node)
            time.sleep(0.6)
            current = UiTree.capture(self.client).find_by_resource_id(resource_id)
            if current is not None and current.element.attrib.get("focused") == "true":
                if current.text:
                    clear_text = getattr(self.client, "clear_text", None)
                    if not callable(clear_text):
                        raise MessagingTimeout(
                            "输入控件 {} 已有内容，无法安全清空后重试".format(
                                resource_id
                            )
                        )
                    logger.warning(
                        "输入校验重试前清空已有内容：resource_id=%s actual_length=%d",
                        resource_id,
                        len(current.text),
                    )
                    clear_text(len(current.text))
                self.client.input_text(text)
                time.sleep(0.6)
                typed = UiTree.capture(self.client).find_by_resource_id(resource_id)
                if typed is not None and typed.text.replace("\xa0", " ") == text:
                    return
                if typed is None:
                    raise MessagingTimeout(
                        "输入后控件 {} 已离开当前页面".format(resource_id)
                    )
                logger.warning(
                    "输入内容校验不一致：resource_id=%s attempt=%d "
                    "target_length=%d actual_length=%d "
                    "target_utf8_bytes=%d actual_utf8_bytes=%d "
                    "target_utf16_units=%d actual_utf16_units=%d",
                    resource_id,
                    attempt,
                    len(text),
                    len(typed.text.replace("\xa0", " ")),
                    len(text.encode("utf-8")),
                    len(typed.text.replace("\xa0", " ").encode("utf-8")),
                    len(text.encode("utf-16-le")) // 2,
                    len(typed.text.replace("\xa0", " ").encode("utf-16-le")) // 2,
                )
                actual_text = typed.text.replace("\xa0", " ")
                mismatch_at = next(
                    (
                        index
                        for index, (expected_char, actual_char) in enumerate(
                            zip(text, actual_text)
                        )
                        if expected_char != actual_char
                    ),
                    min(len(text), len(actual_text)),
                )
                logger.warning(
                    "输入差异详情：first_mismatch=%d target=%r actual=%r",
                    mismatch_at,
                    text,
                    actual_text,
                )
                node = typed
                continue
            if current is None:
                raise MessagingTimeout(
                    "聚焦输入时控件 {} 已离开当前页面".format(resource_id)
                )
            node = current
        raise MessagingTimeout("输入控件 {} 无法可靠写入文本".format(resource_id))

    def _retry_click(
        self,
        node: UiNode,
        predicate: Callable[[], bool],
        description: str,
    ) -> None:
        for _ in range(3):
            UiTree.click(self.client, node)
            time.sleep(1.0)
            if predicate():
                return
        raise MessagingTimeout("点击 {} 后页面未切换".format(description))

    def _live_home_ready(self) -> bool:
        tree = UiTree.capture(self.client)
        return find_live_home_search_button(tree) is not None

    def _wait_for(
        self,
        predicate: Callable[[UiTree], bool],
        description: str,
        timeout: Optional[float] = None,
    ) -> UiTree:
        deadline = time.monotonic() + (timeout or self.transition_timeout)
        while time.monotonic() < deadline:
            tree = UiTree.capture(self.client)
            if predicate(tree):
                return tree
            time.sleep(UI_POLL_INTERVAL)
        raise MessagingTimeout("等待{}超时".format(description))

    def _save_diagnostics(self, user_id: str, error: Exception) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = "user_{}_{}".format(user_id, timestamp)
        try:
            tree = UiTree.capture(self.client)
            (self.output_dir / "{}.xml".format(stem)).write_text(
                tree.xml_text, encoding="utf-8"
            )
        except Exception:
            pass
        try:
            self.client.screenshot(self.output_dir / "{}.png".format(stem))
        except Exception:
            pass
        (self.output_dir / "{}.txt".format(stem)).write_text(
            "error={}\n".format(error), encoding="utf-8"
        )
