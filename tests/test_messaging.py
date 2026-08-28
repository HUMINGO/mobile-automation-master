import csv
from pathlib import Path

import pytest
import mobile_automation.messaging as messaging_module

from mobile_automation.adb import (
    Device,
    DeviceOfflineError,
    DeviceReconnectError,
    DeviceUnavailableError,
)
from mobile_automation.messaging import (
    MessageResult,
    MessageTarget,
    MessagingTimeout,
    UserOffline,
    UserNotFound,
    append_message_history,
    chat_messages,
    find_exact_user_card,
    find_live_home_search_button,
    is_online_profile_status,
    latest_history_by_user,
    load_approved_targets,
    message_list_items,
    nav_message_has_unread,
    new_incoming_reply,
    nicknames_match,
    profile_online_status,
    reply_after_outgoing,
    PoppoMessenger,
)
from mobile_automation.ui import UiTree


HOME_XML = """<hierarchy>
<node bounds="[0,0][720,1640]">
  <node clickable="true" class="android.widget.ImageView" bounds="[547,80][608,141]" />
  <node clickable="true" class="android.widget.ImageView" bounds="[632,80][693,141]" />
</node></hierarchy>"""

RESULT_XML = """<hierarchy><node resource-id="com.baitu.qingshu:id/recyclerView">
<node resource-id="com.baitu.qingshu:id/userCard" clickable="true" bounds="[0,313][720,451]">
  <node text="kris0956" resource-id="com.baitu.qingshu:id/tvNickname" bounds="[150,337][300,380]" />
  <node text="ID:66128032" resource-id="com.baitu.qingshu:id/tvUserId" bounds="[150,410][278,436]" />
</node></node></hierarchy>"""

CHAT_XML = """<hierarchy><node bounds="[0,0][720,1640]">
<node text="hello  kris0956" resource-id="com.baitu.qingshu:id/tvMsgText" bounds="[376,480][574,520]" />
<node text="yyt " resource-id="com.baitu.qingshu:id/tvMsgText" bounds="[146,736][194,776]" />
<node text="OK" resource-id="com.baitu.qingshu:id/tvMsgText" bounds="[532,831][574,871]" />
</node></hierarchy>"""

PROFILE_ONLINE_XML = """<hierarchy><node>
<node text="  oNLinE  " resource-id="com.baitu.qingshu:id/statusText" />
</node></hierarchy>"""

PROFILE_OFFLINE_XML = """<hierarchy><node>
<node text="Offline" resource-id="com.baitu.qingshu:id/statusText" />
</node></hierarchy>"""

PROFILE_PARTY_XML = """<hierarchy><node>
<node resource-id="com.baitu.qingshu:id/llRoomState">
  <node text=" Party " resource-id="com.baitu.qingshu:id/roomState" />
</node>
</node></hierarchy>"""

MESSAGE_LIST_XML = """<hierarchy><node bounds="[0,0][720,1640]">
<node resource-id="com.baitu.qingshu:id/chatList" bounds="[0,152][720,1450]">
  <node resource-id="com.baitu.qingshu:id/itemLayout" clickable="true" bounds="[0,152][720,290]">
    <node text="Official Announcement" resource-id="com.baitu.qingshu:id/nickname" />
    <node text="Official" resource-id="com.baitu.qingshu:id/tvLabel" />
    <node text="3" resource-id="com.baitu.qingshu:id/unread" />
  </node>
  <node resource-id="com.baitu.qingshu:id/itemLayout" clickable="true" bounds="[0,290][720,428]">
    <node text="Alice..." resource-id="com.baitu.qingshu:id/nickname" />
    <node text="1" resource-id="com.baitu.qingshu:id/unread" />
    <node text="hello" resource-id="com.baitu.qingshu:id/formattedContent" />
  </node>
  <node resource-id="com.baitu.qingshu:id/itemLayout" clickable="true" bounds="[0,428][720,566]">
    <node text="Bob" resource-id="com.baitu.qingshu:id/nickname" />
    <node text="sent" resource-id="com.baitu.qingshu:id/formattedContent" />
  </node>
</node></node></hierarchy>"""


def test_finds_idless_home_search_button():
    button = find_live_home_search_button(UiTree(HOME_XML))
    assert button is not None
    assert button.bounds.center == (577, 110)


def test_finds_only_exact_user_card():
    tree = UiTree(RESULT_XML)
    assert find_exact_user_card(tree, "66128032") is not None
    assert find_exact_user_card(tree, "6612803") is None
    assert find_exact_user_card(tree, "999") is None


def test_distinguishes_incoming_and_outgoing_messages():
    messages = chat_messages(UiTree(CHAT_XML))
    assert messages == [
        ("outgoing", "hello  kris0956", 480),
        ("incoming", "yyt ", 736),
        ("outgoing", "OK", 831),
    ]
    assert reply_after_outgoing(messages, "hello  kris0956") == "yyt "
    assert new_incoming_reply(messages[:1], messages[:2]) == "yyt "


def test_message_list_excludes_system_rows_and_reads_unread():
    items = message_list_items(UiTree(MESSAGE_LIST_XML))
    assert [(item.nickname, item.unread, item.preview) for item in items] == [
        ("Alice...", "1", "hello"),
        ("Bob", "", "sent"),
    ]


@pytest.mark.parametrize(
    ("badge_xml", "expected"),
    [
        ("", False),
        (
            '<node text="" resource-id="com.baitu.qingshu:id/msgUnread" />',
            False,
        ),
        (
            '<node text="0" resource-id="com.baitu.qingshu:id/msgUnread" />',
            False,
        ),
        (
            '<node text="00" resource-id="com.baitu.qingshu:id/msgUnread" />',
            False,
        ),
        (
            '<node text="2" resource-id="com.baitu.qingshu:id/msgUnread" />',
            True,
        ),
        (
            '<node text="99+" resource-id="com.baitu.qingshu:id/msgUnread" />',
            True,
        ),
    ],
)
def test_nav_message_unread_badge_is_nonzero(badge_xml, expected):
    tree = UiTree("<hierarchy><node>{}</node></hierarchy>".format(badge_xml))

    assert nav_message_has_unread(tree) is expected


class NavUnreadGateMessenger(PoppoMessenger):
    def __init__(self, badge_xml):
        super().__init__(None)
        self.navigation = UiTree(
            "<hierarchy><node>"
            '<node resource-id="com.baitu.qingshu:id/navMsg" />'
            "{}"
            "</node></hierarchy>".format(badge_xml)
        )
        self.full_scans = 0

    def _return_to_navigation(self):
        return self.navigation

    def _process_waiting_replies(self, targets_by_user, history_path):
        self.full_scans += 1
        return [MessageResult(user_id="1", status="completed")]


@pytest.mark.parametrize(
    "badge_xml",
    [
        "",
        '<node text="" resource-id="com.baitu.qingshu:id/msgUnread" />',
        '<node text="0" resource-id="com.baitu.qingshu:id/msgUnread" />',
    ],
)
def test_round_reply_scan_skips_message_page_without_nav_unread(
    tmp_path,
    badge_xml,
):
    messenger = NavUnreadGateMessenger(badge_xml)

    assert messenger._process_waiting_replies_if_nav_unread(
        {"1": MessageTarget("1")},
        tmp_path / "history.csv",
    ) == []
    assert messenger.full_scans == 0


@pytest.mark.parametrize("unread", ["1", "2", "17", "99+"])
def test_round_reply_scan_keeps_full_scan_when_nav_has_unread(
    tmp_path,
    unread,
):
    messenger = NavUnreadGateMessenger(
        '<node text="{}" resource-id="com.baitu.qingshu:id/msgUnread" />'.format(
            unread
        )
    )

    results = messenger._process_waiting_replies_if_nav_unread(
        {"1": MessageTarget("1")},
        tmp_path / "history.csv",
    )

    assert [item.status for item in results] == ["completed"]
    assert messenger.full_scans == 1


class StaticMessageListClient:
    def __init__(self, xml):
        self.xml = xml

    def dump_ui(self):
        return self.xml


class CandidateListMessenger(PoppoMessenger):
    def _scroll_message_list_to_top(self):
        pass

    def _scroll_message_list(self, forward):
        assert forward is True


class RecordingMessageListClient:
    def __init__(self):
        self.swipes = []

    def dump_ui(self):
        return MESSAGE_LIST_XML

    def swipe(self, *args):
        self.swipes.append(args)


def test_message_list_scroll_is_short_slow_and_overlapping(monkeypatch):
    sleeps = []
    client = RecordingMessageListClient()
    messenger = PoppoMessenger(client)
    monkeypatch.setattr(messaging_module.time, "sleep", sleeps.append)

    messenger._scroll_message_list(forward=True)
    messenger._scroll_message_list(forward=False)

    assert client.swipes == [
        (360, 1060, 360, 606, 900),
        (360, 606, 360, 1060, 900),
    ]
    assert sleeps == [1.0, 1.0]


def test_reply_candidate_uses_unread_marker_without_nickname_filter():
    messenger = CandidateListMessenger(StaticMessageListClient(MESSAGE_LIST_XML))

    candidate = messenger._find_reply_candidate(set())

    assert candidate is not None
    assert candidate.nickname == "Alice..."


def test_reply_candidate_does_not_retry_read_conversation():
    xml = MESSAGE_LIST_XML.replace(
        '    <node text="1" resource-id="com.baitu.qingshu:id/unread" />\n'
        '    <node text="hello" resource-id="com.baitu.qingshu:id/formattedContent" />',
        '    <node text="hello" resource-id="com.baitu.qingshu:id/formattedContent" />',
    )
    messenger = CandidateListMessenger(StaticMessageListClient(xml))

    assert messenger._find_reply_candidate(set()) is None


def test_nickname_matching_supports_only_exact_or_truncated_prefix():
    assert nicknames_match("Alice", "Alice")
    assert nicknames_match("Alice...", "Alice Wonderland")
    assert nicknames_match("Alice Wonderland", "Alice…")
    assert not nicknames_match("Alice", "Alice Wonderland")
    assert not nicknames_match("Ali...", "Bob")


def test_profile_status_is_trimmed_and_missing_is_explicit():
    online = profile_online_status(UiTree(PROFILE_ONLINE_XML))
    party = profile_online_status(UiTree(PROFILE_PARTY_XML))
    offline = profile_online_status(UiTree(PROFILE_OFFLINE_XML))
    missing = profile_online_status(UiTree("<hierarchy><node /></hierarchy>"))

    assert online == "oNLinE"
    assert party == "Party"
    assert offline == "Offline"
    assert missing == "missing"
    assert is_online_profile_status(online)
    assert is_online_profile_status(party)
    assert not is_online_profile_status(offline)
    assert not is_online_profile_status(missing)


def test_profile_status_check_is_opt_in():
    offline = UiTree(PROFILE_OFFLINE_XML)
    missing = UiTree("<hierarchy><node /></hierarchy>")

    PoppoMessenger(None)._ensure_profile_is_online(offline, "1", "Alice")

    messenger = PoppoMessenger(None, online_only=True)
    with pytest.raises(UserOffline) as offline_error:
        messenger._ensure_profile_is_online(offline, "1", "Alice")
    assert offline_error.value.observed_status == "Offline"
    with pytest.raises(UserOffline) as missing_error:
        messenger._ensure_profile_is_online(missing, "1", "Alice")
    assert missing_error.value.observed_status == "missing"


def test_online_only_accepts_online_and_party():
    messenger = PoppoMessenger(None, online_only=True)
    messenger._ensure_profile_is_online(UiTree(PROFILE_ONLINE_XML), "1", "Alice")
    messenger._ensure_profile_is_online(UiTree(PROFILE_PARTY_XML), "2", "Bob")


def test_loads_only_approved_targets_and_deduplicates(tmp_path):
    plan = tmp_path / "plan.csv"
    plan.write_text(
        "user_id,profile_name,country,status\n1,Old,US,approved\n2,No,PH,pending_review\n1,New,PH,approved\n",
        encoding="utf-8",
    )
    config = Path(__file__).parents[1] / "config" / "poppo_message_templates.json"
    targets = load_approved_targets(plan, config)
    assert len(targets) == 1
    assert targets[0].user_id == "1"
    assert targets[0].first_message_template.startswith("Hi! Taga-Yago app ako.")
    assert "Mas maganda ang policy namin kaysa Poppo." in (
        targets[0].first_message_template
    )
    assert targets[0].followup_message.startswith("+w-app 8615960224799")
    assert targets[0].followup_message


def test_missing_country_template_falls_back_to_english(tmp_path):
    plan = tmp_path / "plan.csv"
    plan.write_text(
        "user_id,country,status\n1,PH,approved\n",
        encoding="utf-8",
    )
    config = tmp_path / "templates.json"
    config.write_text(
        '{"default_language_tag":"en","country_routes":{"PH":{"language_tag":"fil"}},'
        '"templates":{"en":{"greeting":"Hello {{name}}",'
        '"after_reply":"Thanks from {{app_name}}"}}}',
        encoding="utf-8",
    )

    target = load_approved_targets(plan, config)[0]

    assert target.first_message_template == "Hello {nickname}"
    assert target.followup_message == "Thanks from yago"


def test_history_appends_and_latest_status_wins(tmp_path):
    history = tmp_path / "history.csv"
    append_message_history(
        history,
        MessageResult(
            user_id="1", nickname="A", status="waiting_reply", first_message="hello A"
        ),
    )
    append_message_history(
        history,
        MessageResult(
            user_id="1", nickname="A", status="completed", followup_message="OK"
        ),
    )
    latest = latest_history_by_user(history)
    assert latest["1"]["status"] == "completed"
    with history.open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2


class MissingMessenger(PoppoMessenger):
    def __init__(self):
        self.saved = []
        self.starts = 0

    def _start_app_once(self):
        self.starts += 1

    def run_target(self, *args, **kwargs):
        raise UserNotFound("找不到用户 404")

    def _save_diagnostics(self, user_id, error):
        self.saved.append((user_id, str(error)))

    def _process_waiting_replies(self, targets_by_user, history_path):
        return []

    def _process_waiting_replies_if_nav_unread(
        self,
        targets_by_user,
        history_path,
    ):
        return self._process_waiting_replies(targets_by_user, history_path)


def test_user_not_found_is_recorded_and_batch_continues(tmp_path):
    messenger = MissingMessenger()
    history = tmp_path / "history.csv"
    results = messenger.run_targets([MessageTarget("404")], history)
    assert results[0].status == "user_not_found"
    assert messenger.saved == [("404", "找不到用户 404")]
    assert messenger.starts == 1
    assert latest_history_by_user(history)["404"]["status"] == "user_not_found"


def test_multiple_users_start_app_only_once(tmp_path):
    messenger = MissingMessenger()
    results = messenger.run_targets(
        [MessageTarget("404"), MessageTarget("405")],
        tmp_path / "history.csv",
    )
    assert [item.status for item in results] == ["user_not_found", "user_not_found"]
    assert messenger.starts == 1


class ReconnectClient:
    serial = "serial-1"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def reconnect_device(self, retry_count, retry_interval):
        self.calls += 1
        if self.fail:
            raise DeviceReconnectError("still offline")
        return Device(self.serial, "device", model="Phone")


class OfflineInitialMessenger(PoppoMessenger):
    def __init__(self, reconnect_fail=False):
        self.reconnect_notifications = []
        super().__init__(
            ReconnectClient(fail=reconnect_fail),
            reconnect_interval=0,
            on_device_reconnected=self.reconnect_notifications.append,
        )
        self.starts = 0
        self.attempted = []

    def _start_app_once(self):
        self.starts += 1

    def run_target(self, target, history_path):
        self.attempted.append(target.user_id)
        if target.user_id == "1":
            raise DeviceUnavailableError("adb: device 'serial-1' not found")
        result = MessageResult(
            user_id=target.user_id,
            status="waiting_reply",
            first_message="hello",
        )
        append_message_history(history_path, result)
        return result

    def _process_waiting_replies(self, targets_by_user, history_path):
        return []

    def _process_waiting_replies_if_nav_unread(
        self,
        targets_by_user,
        history_path,
    ):
        return self._process_waiting_replies(targets_by_user, history_path)


def test_initial_device_offline_is_recorded_and_next_user_continues(tmp_path):
    messenger = OfflineInitialMessenger()
    history = tmp_path / "history.csv"

    results = messenger.run_targets(
        [MessageTarget("1"), MessageTarget("2")],
        history,
    )

    assert [item.status for item in results] == [
        "device_offline",
        "waiting_reply",
    ]
    assert messenger.attempted == ["1", "2"]
    assert messenger.client.calls == 1
    assert messenger.starts == 2
    assert [item.serial for item in messenger.reconnect_notifications] == ["serial-1"]
    latest = latest_history_by_user(history)["1"]
    assert latest["status"] == "device_offline"
    assert "not found" in latest["error"]


def test_device_offline_status_is_retried_on_next_run(tmp_path):
    history = tmp_path / "history.csv"
    append_message_history(
        history,
        MessageResult(user_id="1", status="device_offline"),
    )
    messenger = WaitingMessenger()

    messenger.run_targets([MessageTarget("1")], history)

    assert messenger.sent == ["1"]
    assert latest_history_by_user(history)["1"]["status"] == "waiting_reply"


def test_reconnect_exhaustion_stops_before_next_user(tmp_path):
    messenger = OfflineInitialMessenger(reconnect_fail=True)

    with pytest.raises(DeviceReconnectError, match="still offline"):
        messenger.run_targets(
            [MessageTarget("1"), MessageTarget("2")],
            tmp_path / "history.csv",
        )

    assert messenger.attempted == ["1"]


class WaitingMessenger(MissingMessenger):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.reply_checks = 0

    def run_target(self, target, history_path):
        self.sent.append(target.user_id)
        result = MessageResult(
            user_id=target.user_id,
            nickname="A",
            status="waiting_reply",
            first_message="hello  A",
            followup_message="OK",
        )
        append_message_history(history_path, result)
        return result

    def _process_waiting_replies(self, targets_by_user, history_path):
        self.reply_checks += 1
        return []


def test_initial_send_immediately_becomes_resumable_waiting_state(tmp_path):
    messenger = WaitingMessenger()
    history = tmp_path / "history.csv"
    result = messenger.run_targets([MessageTarget("1")], history)[0]
    assert result.status == "waiting_reply"
    latest = latest_history_by_user(history)["1"]
    assert latest["status"] == "waiting_reply"
    assert latest["first_message"] == "hello  A"
    assert latest["followup_message"] == "OK"
    assert messenger.reply_checks == 1

    messenger.run_targets([MessageTarget("1")], history)
    assert messenger.sent == ["1"]
    assert messenger.reply_checks == 2


class RoundMessenger(WaitingMessenger):
    def __init__(self, failures=()):
        super().__init__()
        self.failures = set(failures)
        self.round_snapshots = []

    def run_target(self, target, history_path):
        if target.user_id in self.failures:
            raise UserNotFound("missing {}".format(target.user_id))
        return super().run_target(target, history_path)

    def _process_waiting_replies(self, targets_by_user, history_path):
        self.reply_checks += 1
        latest = latest_history_by_user(history_path)
        self.round_snapshots.append(
            sum(row.get("status") == "waiting_reply" for row in latest.values())
        )
        return []


def test_round_uses_twenty_successful_sends_and_failures_do_not_count(tmp_path):
    messenger = RoundMessenger(failures={"3", "17"})
    targets = [MessageTarget(str(index)) for index in range(1, 44)]

    results = messenger.run_targets(
        targets,
        tmp_path / "history.csv",
        round_size=20,
    )

    assert len([item for item in results if item.status == "waiting_reply"]) == 41
    assert messenger.round_snapshots == [20, 40, 41]
    assert messenger.reply_checks == 3


def test_last_round_under_twenty_still_checks_replies(tmp_path):
    messenger = RoundMessenger()
    messenger.run_targets(
        [MessageTarget(str(index)) for index in range(1, 6)],
        tmp_path / "history.csv",
    )
    assert messenger.round_snapshots == [5]


def test_failures_after_full_round_still_finish_on_reply_check(tmp_path):
    messenger = RoundMessenger(failures={"21", "22"})
    messenger.run_targets(
        [MessageTarget(str(index)) for index in range(1, 23)],
        tmp_path / "history.csv",
        round_size=20,
    )
    assert messenger.round_snapshots == [20, 20]


def test_success_limit_skips_history_and_continues_into_later_batches(tmp_path):
    history = tmp_path / "history.csv"
    for user_id, status in (
        ("1", "error"),
        ("2", "completed"),
        ("3", "waiting_reply"),
    ):
        append_message_history(
            history,
            MessageResult(user_id=user_id, status=status),
        )
    messenger = RoundMessenger(failures={"4"})
    targets = [MessageTarget(str(index)) for index in range(1, 9)]

    results = messenger.run_targets(
        targets,
        history,
        initial_success_limit=3,
    )

    assert messenger.sent == ["5", "6", "7"]
    assert [item.user_id for item in results if item.status == "waiting_reply"] == [
        "5",
        "6",
        "7",
    ]
    assert any(
        item.user_id == "4" and item.status == "user_not_found"
        for item in results
    )
    assert "8" not in latest_history_by_user(history)


def test_success_limit_exhaustion_reports_only_actual_successes(tmp_path):
    messenger = RoundMessenger(failures={"1", "3"})

    results = messenger.run_targets(
        [MessageTarget(str(index)) for index in range(1, 5)],
        tmp_path / "history.csv",
        initial_success_limit=3,
    )

    assert messenger.sent == ["2", "4"]
    assert len(
        [item for item in results if item.status == "waiting_reply"]
    ) == 2


def test_success_round_count_continues_across_quota_batches(tmp_path):
    messenger = RoundMessenger(failures={"4", "5", "6"})

    messenger.run_targets(
        [MessageTarget(str(index)) for index in range(1, 11)],
        tmp_path / "history.csv",
        initial_success_limit=6,
    )

    assert messenger.sent == ["1", "2", "3", "7", "8", "9"]
    assert messenger.round_snapshots == [5, 6]


def test_zero_success_limit_skips_initial_send(tmp_path):
    messenger = SkipInitialMessenger()

    assert messenger.run_targets(
        [MessageTarget("1")],
        tmp_path / "history.csv",
        initial_success_limit=0,
    ) == []
    assert messenger.starts == 0


def test_negative_success_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="initial_success_limit"):
        WaitingMessenger().run_targets(
            [MessageTarget("1")],
            tmp_path / "history.csv",
            initial_success_limit=-1,
        )


REPLY_CHAT_XML = """<hierarchy><node bounds="[0,0][720,1640]">
<node resource-id="com.baitu.qingshu:id/et_input" bounds="[100,1450][600,1520]" />
<node text="hello Alice" resource-id="com.baitu.qingshu:id/tvMsgText" bounds="[380,480][650,520]" />
<node text="yes" resource-id="com.baitu.qingshu:id/tvMsgText" bounds="[80,600][240,640]" />
</node></hierarchy>"""


class ReplyScanMessenger(PoppoMessenger):
    def __init__(
        self,
        fail_send=False,
        valid_reply=True,
        unread=True,
        visible_nickname=None,
    ):
        super().__init__(None, poll_interval=0)
        self.item = message_list_items(UiTree(MESSAGE_LIST_XML))[0]
        if not unread or visible_nickname is not None:
            self.item = type(self.item)(
                node=self.item.node,
                nickname=visible_nickname or self.item.nickname,
                unread=self.item.unread if unread else "",
                preview=self.item.preview,
            )
        self.fail_send = fail_send
        self.valid_reply = valid_reply
        self.sent = []
        self.saved = []

    def _open_message_page(self):
        return UiTree(MESSAGE_LIST_XML)

    def _find_reply_candidate(self, ignored_rows, max_pages=100):
        if (
            not self.item.unread
            or (self.item.nickname, self.item.preview) in ignored_rows
        ):
            return None
        return self.item

    def _retry_click(self, node, predicate, description):
        return None

    def _wait_for(self, predicate, description, timeout=None):
        xml = (
            REPLY_CHAT_XML
            if self.valid_reply
            else REPLY_CHAT_XML.replace("hello Alice", "hello somebody else")
        )
        tree = UiTree(xml)
        assert predicate(tree)
        return tree

    def _send_message(self, message):
        self.sent.append(message)
        if self.fail_send:
            raise MessagingTimeout("send failed")

    def _save_diagnostics(self, user_id, error):
        self.saved.append((user_id, str(error)))


def _write_waiting_history(path):
    append_message_history(
        path,
        MessageResult(
            user_id="1",
            nickname="Alice Wonderland",
            status="waiting_reply",
            first_message="hello Alice",
            followup_message="OK saved",
        ),
    )


def test_unread_reply_is_verified_then_followup_is_completed(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    messenger = ReplyScanMessenger()

    results = messenger._process_waiting_replies(
        {"1": MessageTarget("1", followup_message="OK new")}, history
    )

    assert [item.status for item in results] == ["completed"]
    assert messenger.sent == ["OK saved"]
    latest = latest_history_by_user(history)["1"]
    assert latest["status"] == "completed"
    assert latest["reply"] == "yes"


def test_unread_nickname_mismatch_still_matches_chat_history(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    messenger = ReplyScanMessenger(visible_nickname="Completely Different")

    results = messenger._process_waiting_replies(
        {"1": MessageTarget("1")}, history
    )

    assert [item.status for item in results] == ["completed"]
    assert messenger.sent == ["OK saved"]


def test_failed_followup_keeps_reply_for_later_retry(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    messenger = ReplyScanMessenger(fail_send=True)

    assert messenger._process_waiting_replies(
        {"1": MessageTarget("1")}, history
    ) == []

    latest = latest_history_by_user(history)["1"]
    assert latest["status"] == "waiting_reply"
    assert latest["reply"] == "yes"
    assert latest["error"] == "send failed"
    assert messenger.saved == [("1", "send failed")]


class OfflineReplyMessenger(ReplyScanMessenger):
    def __init__(self):
        super().__init__()
        self.reconnects = 0

    def _send_message(self, message):
        raise DeviceOfflineError("adb: device offline")

    def _recover_offline_device(self):
        self.reconnects += 1


def test_reply_device_offline_stays_waiting_and_is_skipped_for_run(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    messenger = OfflineReplyMessenger()

    assert messenger._process_waiting_replies(
        {"1": MessageTarget("1")}, history
    ) == []

    latest = latest_history_by_user(history)["1"]
    assert latest["status"] == "waiting_reply"
    assert latest["reply"] == "yes"
    assert latest["error"] == "adb: device offline"
    assert messenger.reconnects == 1
    assert messenger._offline_skipped_reply_users == {"1"}


def test_candidate_without_reply_after_historical_outgoing_is_not_sent(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    messenger = ReplyScanMessenger(valid_reply=False)

    messenger._process_waiting_replies({"1": MessageTarget("1")}, history)

    assert messenger.sent == []
    assert latest_history_by_user(history)["1"]["status"] == "waiting_reply"


def test_unread_with_ambiguous_first_message_is_not_sent(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    append_message_history(
        history,
        MessageResult(
            user_id="2",
            nickname="Another User",
            status="waiting_reply",
            first_message="hello Alice",
            followup_message="second followup",
        ),
    )
    messenger = ReplyScanMessenger()

    messenger._process_waiting_replies(
        {"1": MessageTarget("1"), "2": MessageTarget("2")},
        history,
    )

    assert messenger.sent == []
    latest = latest_history_by_user(history)
    assert latest["1"]["status"] == "waiting_reply"
    assert latest["2"]["status"] == "waiting_reply"


def test_saved_reply_without_unread_marker_is_not_retried(tmp_path):
    history = tmp_path / "history.csv"
    append_message_history(
        history,
        MessageResult(
            user_id="1",
            nickname="Alice Wonderland",
            status="waiting_reply",
            first_message="hello Alice",
            reply="yes",
            followup_message="OK saved",
            error="earlier send failed",
        ),
    )
    messenger = ReplyScanMessenger(unread=False)

    messenger._process_waiting_replies({"1": MessageTarget("1")}, history)

    assert messenger.sent == []
    latest = latest_history_by_user(history)["1"]
    assert latest["status"] == "waiting_reply"
    assert latest["reply"] == "yes"


class SkipInitialMessenger(WaitingMessenger):
    def run_target(self, target, history_path):
        raise AssertionError("skip_initial_send 模式不应调用首发")


def test_skip_initial_send_preserves_unsent_users_and_checks_waiting(tmp_path):
    history = tmp_path / "history.csv"
    _write_waiting_history(history)
    messenger = SkipInitialMessenger()

    assert messenger.run_targets(
        [MessageTarget("1"), MessageTarget("2")],
        history,
        skip_initial_send=True,
        initial_success_limit=1,
    ) == []

    assert messenger.starts == 1
    assert messenger.reply_checks == 1
    latest = latest_history_by_user(history)
    assert latest["1"]["status"] == "waiting_reply"
    assert "2" not in latest


class MonitorMessenger(PoppoMessenger):
    def __init__(self):
        super().__init__(None, poll_interval=10)
        self.starts = 0
        self.message_opens = 0
        self.restores = 0

    def _start_app_once(self):
        self.starts += 1

    def _open_message_page(self):
        self.message_opens += 1

    def _restore_input_method(self):
        self.restores += 1


def test_manual_stop_restores_input_and_leaves_message_page(tmp_path, monkeypatch):
    messenger = MonitorMessenger()

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(messaging_module.time, "sleep", interrupt)
    assert messenger.run_targets(
        [], tmp_path / "history.csv", monitor_forever=True
    ) == []
    assert messenger.starts == 1
    assert messenger.message_opens == 2
    assert messenger.restores == 1


class OfflineMessenger(MissingMessenger):
    def __init__(self, observed_status="Offline"):
        super().__init__()
        self.observed_status = observed_status
        self.chat_opened = False

    def _open_profile(self, user_id):
        raise UserOffline(user_id, "Alice", self.observed_status)

    def _open_chat(self, *args, **kwargs):
        self.chat_opened = True
        raise AssertionError("offline 用户不应进入聊天页")

    def run_target(self, target, history_path):
        # Use the production sequence so this also verifies chat is never opened.
        return PoppoMessenger.run_target(self, target, history_path)


def test_offline_is_recorded_without_diagnostics_and_retried(tmp_path):
    messenger = OfflineMessenger()
    history = tmp_path / "history.csv"

    first = messenger.run_targets([MessageTarget("1")], history)
    second = messenger.run_targets([MessageTarget("1")], history)

    assert [item.status for item in first + second] == ["offline", "offline"]
    assert first[0].nickname == "Alice"
    assert first[0].error == "Offline"
    assert messenger.saved == []
    assert messenger.chat_opened is False
    assert messenger.starts == 2
    assert latest_history_by_user(history)["1"]["status"] == "offline"


def test_missing_profile_status_is_recorded_as_missing(tmp_path):
    messenger = OfflineMessenger("missing")
    result = messenger.run_targets(
        [MessageTarget("1")], tmp_path / "history.csv"
    )[0]
    assert result.status == "offline"
    assert result.error == "missing"


class CompletedGuardMessenger(MissingMessenger):
    def run_target(self, *args, **kwargs):
        raise AssertionError("跳过状态的用户不应再次执行")


@pytest.mark.parametrize(
    ("status", "expected_starts"),
    [
        ("completed", 0),
        ("waiting_reply", 1),
        ("error", 0),
        ("user_not_found", 0),
    ],
)
def test_latest_terminal_status_skips_initial_send(
    tmp_path, status, expected_starts
):
    history = tmp_path / "history.csv"
    append_message_history(history, MessageResult(user_id="1", status=status))
    messenger = CompletedGuardMessenger()

    assert messenger.run_targets([MessageTarget("1")], history) == []
    assert messenger.starts == expected_starts


def test_latest_retryable_status_after_error_allows_initial_send(tmp_path):
    history = tmp_path / "history.csv"
    append_message_history(history, MessageResult(user_id="1", status="error"))
    append_message_history(
        history,
        MessageResult(user_id="1", status="device_offline"),
    )
    messenger = WaitingMessenger()

    messenger.run_targets([MessageTarget("1")], history)

    assert messenger.sent == ["1"]


NAV_HOME_XML = """<hierarchy><node bounds="[0,0][720,1640]">
<node resource-id="com.baitu.qingshu:id/navLive" clickable="true" bounds="[0,1450][144,1546]" />
<node clickable="true" class="android.widget.ImageView" bounds="[547,80][608,141]" />
</node></hierarchy>"""

NO_NAV_XML = """<hierarchy><node bounds="[0,0][720,1640]">
<node text="detail" bounds="[0,0][100,100]" />
</node></hierarchy>"""


class BackClient:
    def __init__(self, backs_needed, outside_after=None):
        self.backs_needed = backs_needed
        self.outside_after = outside_after
        self.back_count = 0
        self.restarted = False

    def dump_ui(self):
        return (
            NAV_HOME_XML
            if self.restarted or self.back_count >= self.backs_needed
            else NO_NAV_XML
        )

    def shell(self, *args, **kwargs):
        if (
            not self.restarted
            and self.outside_after is not None
            and self.back_count >= self.outside_after
        ):
            return (
                "topResumedActivity=ActivityRecord{abc u0 "
                "com.android.launcher/.Launcher t1}"
            )
        return (
            "topResumedActivity=ActivityRecord{abc u0 "
            "com.baitu.qingshu/.MainActivity t1}"
        )

    def keyevent(self, keycode):
        assert keycode == "KEYCODE_BACK"
        self.back_count += 1


class NavigationMessenger(PoppoMessenger):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.restart_count = 0

    def _start_app_once(self):
        self.restart_count += 1
        self.client.restarted = True
        return UiTree(NAV_HOME_XML)

    def _retry_click(self, node, predicate, description):
        assert node.resource_id == "com.baitu.qingshu:id/navLive"
        assert predicate()


def test_returns_zero_to_five_times_until_nav_live_is_found():
    for backs_needed in range(6):
        client = BackClient(backs_needed)
        messenger = NavigationMessenger(client, poll_interval=0)
        tree = messenger._return_to_live_home()
        assert find_live_home_search_button(tree) is not None
        assert client.back_count == backs_needed


def test_restarts_after_five_returns_when_nav_live_is_missing():
    client = BackClient(6)
    messenger = NavigationMessenger(client, poll_interval=0)

    tree = messenger._return_to_live_home()

    assert client.back_count == 5
    assert messenger.restart_count == 1
    assert find_live_home_search_button(tree) is not None


def test_restarts_immediately_when_back_leaves_poppo():
    client = BackClient(backs_needed=6, outside_after=2)
    messenger = NavigationMessenger(client, poll_interval=0)

    tree = messenger._return_to_live_home()

    assert client.back_count == 2
    assert messenger.restart_count == 1
    assert find_live_home_search_button(tree) is not None
