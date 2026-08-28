from pathlib import Path

import pytest

from mobile_automation.adb import (
    Device,
    DeviceOfflineError,
    DeviceReconnectError,
    DeviceUnavailableError,
)
from mobile_automation.square import (
    PageState,
    append_csv,
    classify_gender_pixels,
    classify_page,
    collect_square_users,
    find_last_visible_win_record,
    is_profile_page,
    is_square_page,
    load_run_state,
    parse_profile,
    recover_to_square,
    save_run_state,
)
from mobile_automation.ui import UiTree


SQUARE_XML = """\
<hierarchy>
  <node text="Square" bounds="[200,100][500,180]" />
  <node bounds="[20,900][700,980]">
    <node text="First" bounds="[100,910][250,950]" />
    <node text="Win" bounds="[300,910][350,950]" />
    <node text="1,000" bounds="[400,910][500,950]" />
  </node>
  <node bounds="[20,1100][700,1180]">
    <node text="Last User" bounds="[100,1110][250,1150]" />
    <node text="Win" bounds="[300,1110][350,1150]" />
    <node text="2,000" bounds="[400,1110][500,1150]" />
  </node>
</hierarchy>
"""


PROFILE_XML = """\
<hierarchy>
  <node text="Profile User" bounds="[30,300][600,350]" />
  <node text="ID:12345" bounds="[160,370][320,410]" />
  <node text="10" bounds="[30,440][80,480]" />
  <node text="Following" bounds="[90,440][190,480]" />
  <node text="2K" bounds="[220,440][280,480]" />
  <node text="Followers" bounds="[300,440][400,480]" />
  <node text="Fan Club·3" bounds="[30,500][160,540]" />
  <node text="PK Rank" bounds="[500,500][650,540]" />
  <node text="Gold 1" bounds="[500,550][650,590]" />
  <node text="Personal Information" bounds="[30,700][340,750]" />
  <node text="" resource-id="com.baitu.qingshu:id/ivGender" bounds="[50,770][80,810]" />
  <node text="25" resource-id="com.baitu.qingshu:id/tvAge" bounds="[90,770][130,810]" />
  <node text="US" resource-id="com.baitu.qingshu:id/tvCountryText" bounds="[220,770][260,810]" />
  <node text="Face Authentication" bounds="[360,770][590,810]" />
  <node text="hello world" bounds="[30,830][680,880]" />
  <node text="Interest Tags" bounds="[30,900][230,950]" />
  <node text="Virgo" resource-id="com.baitu.qingshu:id/tv_interest_tag" bounds="[90,970][160,1010]" />
  <node text="Music" resource-id="com.baitu.qingshu:id/tv_interest_tag" bounds="[180,970][260,1010]" />
</hierarchy>
"""


def test_finds_last_visible_win_record():
    tree = UiTree(SQUARE_XML)
    assert is_square_page(tree)
    record = find_last_visible_win_record(tree)
    assert record.username == "Last User"
    assert record.amount == "2,000"


def test_parses_profile():
    tree = UiTree(PROFILE_XML)
    assert is_profile_page(tree)
    profile = parse_profile(tree)
    assert profile["profile_name"] == "Profile User"
    assert profile["user_id"] == "12345"
    assert profile["following"] == "10"
    assert profile["followers"] == "2K"
    assert profile["age"] == "25"
    assert profile["country"] == "US"
    assert profile["gender"] == "unknown"
    assert profile["interest_tags"] == "Virgo | Music"


def test_append_csv_creates_header_once(tmp_path: Path):
    path = tmp_path / "users.csv"
    append_csv(path, {"iteration": 0, "profile_name": "Alice", "status": "ok"})
    append_csv(path, {"iteration": 1, "profile_name": "Bob", "status": "ok"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("collected_at,iteration,")


def test_gender_color_classification():
    assert classify_gender_pixels([(220, 30, 40)] * 10) == "female"
    assert classify_gender_pixels([(30, 80, 220)] * 10) == "male"
    assert classify_gender_pixels([(120, 120, 120)] * 10) == "unknown"


def test_append_csv_migrates_old_header(tmp_path: Path):
    path = tmp_path / "users.csv"
    old_fields = [field for field in __import__("mobile_automation.square", fromlist=["CSV_FIELDS"]).CSV_FIELDS if field != "gender"]
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({"iteration": 0, "profile_name": "Old User", "status": "ok"})
    append_csv(
        path,
        {"iteration": 1, "profile_name": "New User", "gender": "female", "status": "ok"},
    )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert "gender" in rows[0]
    assert rows[0]["gender"] == ""
    assert rows[1]["gender"] == "female"


class PageClient:
    def __init__(self, state="profile"):
        self.state = state
        self.back_count = 0

    def dump_ui(self):
        if self.state == "profile":
            return PROFILE_XML
        if self.state == "square":
            return SQUARE_XML
        return '<hierarchy><node text="Unknown" bounds="[0,0][10,10]" /></hierarchy>'

    def shell(self, *args, **kwargs):
        activity = {
            "profile": "com.baitu.qingshu/com.androidrtc.chat.modules.homepage.HomepageActivity",
            "square": "com.baitu.qingshu/com.androidtool.common.webview.MyWebActivity",
            "home": "com.baitu.qingshu/com.androidrtc.chat.modules.main.MainActivity",
            "unknown": "com.other/.OtherActivity",
        }[self.state]
        return "topResumedActivity=ActivityRecord{{abc u0 {} t1}}".format(activity)

    def keyevent(self, keycode):
        assert keycode == "KEYCODE_BACK"
        self.back_count += 1
        if self.state == "profile":
            self.state = "square"

    def tap(self, x, y):
        pass


def test_classifies_square_and_profile_pages():
    client = PageClient("square")
    assert classify_page(client, UiTree(SQUARE_XML)) is PageState.SQUARE
    client.state = "profile"
    assert classify_page(client, UiTree(PROFILE_XML)) is PageState.PROFILE


def test_recovers_from_profile_with_back(tmp_path: Path):
    client = PageClient("profile")
    tree = recover_to_square(client, tmp_path, iteration=4, attempt=1)
    assert is_square_page(tree)
    assert client.back_count == 1


def test_unknown_page_falls_back_to_full_restart(tmp_path: Path, monkeypatch):
    client = PageClient("unknown")
    restarted = []

    def fake_open(client_arg, output_dir):
        restarted.append((client_arg, output_dir))
        client.state = "square"
        return UiTree(SQUARE_XML)

    monkeypatch.setattr("mobile_automation.square.open_square_page", fake_open)
    tree = recover_to_square(client, tmp_path, iteration=8, attempt=2)
    assert is_square_page(tree)
    assert client.back_count == 2
    assert len(restarted) == 1


def test_recover_to_square_does_not_swallow_device_offline(
    tmp_path: Path, monkeypatch
):
    client = PageClient("profile")
    monkeypatch.setattr(
        "mobile_automation.square.wait_for_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DeviceOfflineError("adb: device offline")
        ),
    )

    with pytest.raises(DeviceOfflineError):
        recover_to_square(client, tmp_path, iteration=4, attempt=1)


def test_loads_resume_position_from_csv(tmp_path: Path):
    csv_path = tmp_path / "users.csv"
    append_csv(csv_path, {"iteration": 0, "status": "ok"})
    append_csv(csv_path, {"iteration": 55, "status": "error"})
    state = load_run_state(csv_path, tmp_path / "missing.json", 10000)
    assert state.next_iteration == 56
    assert state.success_count == 1
    assert state.error_count == 1
    assert state.consecutive_failed_iterations == 1


def test_checkpoint_round_trip_is_atomic(tmp_path: Path):
    csv_path = tmp_path / "users.csv"
    state_path = tmp_path / "users.state.json"
    state = load_run_state(csv_path, state_path, 10000, fresh=True)
    state.next_iteration = 56
    state.success_count = 55
    save_run_state(state_path, state)
    loaded = load_run_state(csv_path, state_path, 10000)
    assert loaded.next_iteration == 56
    assert loaded.success_count == 55
    assert not (tmp_path / "users.state.json.tmp").exists()


def test_three_failed_iterations_stop_safely(tmp_path: Path, monkeypatch):
    client = PageClient("square")
    square_tree = UiTree(SQUARE_XML)
    monkeypatch.setattr(
        "mobile_automation.square.recover_to_square",
        lambda *args, **kwargs: square_tree,
    )
    monkeypatch.setattr(
        "mobile_automation.square.wait_for_profile_stable",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("profile timeout")),
    )
    monkeypatch.setattr("mobile_automation.square.save_diagnostics", lambda *args, **kwargs: None)

    csv_path = tmp_path / "users.csv"
    state = collect_square_users(
        client,
        iterations=5,
        csv_path=csv_path,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path,
        min_delay=0,
        max_delay=0,
        fresh=True,
        max_attempts=1,
    )
    assert state.next_iteration == 3
    assert state.error_count == 3
    assert state.consecutive_failed_iterations == 3
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 4


def test_device_offline_reconnects_and_retries_same_iteration(
    tmp_path: Path, monkeypatch
):
    client = PageClient("square")
    square_tree = UiTree(SQUARE_XML)
    recover_calls = []
    reconnect_calls = []
    reconnect_notifications = []
    restarts = []

    def recover(*args, **kwargs):
        recover_calls.append(1)
        if len(recover_calls) == 1:
            raise DeviceUnavailableError("adb: device 'serial-1' not found")
        return square_tree

    def reconnect_device(retry_count, retry_interval):
        reconnect_calls.append((retry_count, retry_interval))
        return Device("serial-1", "device")

    client.reconnect_device = reconnect_device
    monkeypatch.setattr("mobile_automation.square.recover_to_square", recover)
    monkeypatch.setattr(
        "mobile_automation.square.open_square_page",
        lambda *args, **kwargs: restarts.append(1) or square_tree,
    )
    monkeypatch.setattr(
        "mobile_automation.square.wait_for_profile_stable",
        lambda *args, **kwargs: UiTree(PROFILE_XML),
    )

    csv_path = tmp_path / "users.csv"
    state = collect_square_users(
        client,
        iterations=1,
        csv_path=csv_path,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path,
        min_delay=0,
        max_delay=0,
        fresh=True,
        skip_gender_detection=True,
        max_attempts=1,
        on_device_reconnected=reconnect_notifications.append,
    )

    assert state.next_iteration == 1
    assert state.success_count == 1
    assert state.error_count == 0
    assert reconnect_calls == [(10, 30)]
    assert [item.serial for item in reconnect_notifications] == ["serial-1"]
    assert restarts == [1]
    assert len(recover_calls) == 2
    assert "device_offline" not in csv_path.read_text(encoding="utf-8")


def test_reconnect_exhaustion_preserves_square_iteration(
    tmp_path: Path, monkeypatch
):
    client = PageClient("square")
    client.reconnect_device = lambda **kwargs: (_ for _ in ()).throw(
        DeviceReconnectError("still offline")
    )
    monkeypatch.setattr(
        "mobile_automation.square.recover_to_square",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DeviceOfflineError("adb: device offline")
        ),
    )
    state_path = tmp_path / "state.json"

    with pytest.raises(DeviceReconnectError, match="still offline"):
        collect_square_users(
            client,
            iterations=1,
            csv_path=tmp_path / "users.csv",
            state_path=state_path,
            output_dir=tmp_path,
            min_delay=0,
            max_delay=0,
            fresh=True,
            max_attempts=1,
        )

    saved = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    assert saved["next_iteration"] == 0
    assert saved["success_count"] == 0
    assert saved["error_count"] == 0
    assert not (tmp_path / "users.csv").exists()


def test_success_resets_consecutive_failure_count(tmp_path: Path, monkeypatch):
    client = PageClient("square")
    square_tree = UiTree(SQUARE_XML)
    calls = {"profile": 0}

    monkeypatch.setattr(
        "mobile_automation.square.recover_to_square",
        lambda *args, **kwargs: square_tree,
    )

    def profile_once_failed(*args, **kwargs):
        calls["profile"] += 1
        if calls["profile"] == 1:
            raise RuntimeError("profile timeout")
        return UiTree(PROFILE_XML)

    monkeypatch.setattr(
        "mobile_automation.square.wait_for_profile_stable", profile_once_failed
    )
    monkeypatch.setattr(
        "mobile_automation.square.wait_for_state",
        lambda *args, **kwargs: square_tree,
    )
    monkeypatch.setattr("mobile_automation.square.save_diagnostics", lambda *args, **kwargs: None)

    state = collect_square_users(
        client,
        iterations=2,
        csv_path=tmp_path / "users.csv",
        state_path=tmp_path / "state.json",
        output_dir=tmp_path,
        min_delay=0,
        max_delay=0,
        fresh=True,
        max_attempts=1,
    )
    assert state.next_iteration == 2
    assert state.success_count == 1
    assert state.error_count == 1
    assert state.consecutive_failed_iterations == 0


class GenderScreenshotClient(PageClient):
    def __init__(self, fail=False):
        super().__init__("square")
        self.fail = fail
        self.screenshot_calls = []

    def screenshot(self, target, timeout=30):
        self.screenshot_calls.append((target, timeout))
        if self.fail:
            raise RuntimeError("screenshot timeout")
        target.write_bytes(b"fake png")
        return target


def prepare_successful_collection(monkeypatch):
    square_tree = UiTree(SQUARE_XML)
    monkeypatch.setattr(
        "mobile_automation.square.recover_to_square",
        lambda *args, **kwargs: square_tree,
    )
    monkeypatch.setattr(
        "mobile_automation.square.wait_for_profile_stable",
        lambda *args, **kwargs: UiTree(PROFILE_XML),
    )
    monkeypatch.setattr(
        "mobile_automation.square.wait_for_state",
        lambda *args, **kwargs: square_tree,
    )
    monkeypatch.setattr(
        "mobile_automation.square.save_diagnostics", lambda *args, **kwargs: None
    )


def test_gender_screenshot_uses_one_sixty_second_attempt_and_falls_back(
    tmp_path: Path, monkeypatch
):
    client = GenderScreenshotClient(fail=True)
    prepare_successful_collection(monkeypatch)
    csv_path = tmp_path / "users.csv"

    state = collect_square_users(
        client,
        iterations=1,
        csv_path=csv_path,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path,
        min_delay=0,
        max_delay=0,
        fresh=True,
        max_attempts=1,
    )

    assert state.success_count == 1
    assert client.screenshot_calls == [(tmp_path / "profile_current.png", 60)]
    import csv

    with csv_path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["gender"] == "unknown"


def test_skip_gender_detection_never_takes_screenshot(tmp_path: Path, monkeypatch):
    client = GenderScreenshotClient()
    prepare_successful_collection(monkeypatch)
    csv_path = tmp_path / "users.csv"

    state = collect_square_users(
        client,
        iterations=1,
        csv_path=csv_path,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path,
        min_delay=0,
        max_delay=0,
        fresh=True,
        skip_gender_detection=True,
        max_attempts=1,
    )

    assert state.success_count == 1
    assert client.screenshot_calls == []
    import csv

    with csv_path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["gender"] == "unknown"
