from pathlib import Path

import pytest

from mobile_automation.inspector import InspectorSession, _PAGE, _png_size


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x02\xd0\x00\x00\x06\x40\x08\x06\x00\x00\x00\x00"
)
XML = '''<?xml version="1.0"?><hierarchy><node text="登录" resource-id="login" content-desc="" class="android.widget.Button" clickable="true" enabled="true" bounds="[10,20][110,70]" /></hierarchy>'''
SETTINGS_XML = '''<hierarchy><node text="Settings" resource-id="settings" content-desc="" class="android.widget.Button" clickable="true" enabled="true" bounds="[10,20][110,70]" /></hierarchy>'''
ACCOUNT_XML = '''<hierarchy><node text="Account" resource-id="account" content-desc="" class="android.widget.Button" clickable="true" enabled="true" bounds="[10,80][110,130]" /></hierarchy>'''


class FakeClient:
    serial = "test-device"

    def screenshot(self, target, timeout=60):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(PNG)
        return Path(target)

    def dump_ui(self):
        return XML

    def tap(self, x, y):
        self.tapped = (x, y)

    def input_text(self, text):
        self.entered = text

    def swipe(self, x1, y1, x2, y2, duration):
        self.swiped = (x1, y1, x2, y2, duration)


class SequentialClient(FakeClient):
    def __init__(self):
        self.stage = 0

    def dump_ui(self):
        return (SETTINGS_XML, ACCOUNT_XML, ACCOUNT_XML)[self.stage]

    def tap(self, x, y):
        self.tapped = (x, y)
        self.stage = min(self.stage + 1, 2)


def test_refresh_exposes_ui_elements_and_screen_size(tmp_path):
    client = FakeClient()
    session = InspectorSession(client, tmp_path)

    data = session.refresh()

    assert data["screen_size"] == [720, 1600]
    assert data["nodes"][0]["text"] == "登录"
    assert data["nodes"][0]["center"] == [60, 45]


def test_png_size_rejects_invalid_data():
    assert _png_size(PNG) == (720, 1600)
    assert _png_size(b"not a png") == (0, 0)


def test_session_analyses_current_ui_requirement(tmp_path):
    session = InspectorSession(FakeClient(), tmp_path)
    session.refresh()

    plan = session.analyse_requirement("点击 登录按钮")

    assert plan["locator"] == {"kind": "resource_id", "value": "login"}
    assert "find_by_resource_id('login')" in plan["generated_script"]


def test_reviewed_plan_is_written_once_to_the_selected_test_case_file(tmp_path):
    test_cases = tmp_path / "test_script"
    session = InspectorSession(FakeClient(), tmp_path / "artifacts", test_cases)
    session.refresh()
    session.analyse_requirement("点击 登录按钮")
    session._last_plan_executed = True  # Execution itself is covered by AdbClient/UiTree tests.

    saved = session.save_test_case("login_navigation")

    assert saved == test_cases / "login_navigation.py"
    assert "find_by_resource_id('login')" in saved.read_text(encoding="utf-8")
    assert "serial='test-device'" in saved.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        session.save_test_case("login_navigation.py")


def test_reviewed_edited_script_is_written_to_the_test_case_file(tmp_path):
    test_cases = tmp_path / "test_script"
    session = InspectorSession(FakeClient(), tmp_path / "artifacts", test_cases)
    session.refresh()
    session.analyse_requirement("点击 登录按钮")
    session._last_plan_executed = True

    saved = session.save_test_case("edited_login", "print('edited test')")

    assert "print('edited test')" in saved.read_text(encoding="utf-8")


def test_execution_reanalyses_ui_before_each_natural_language_step(tmp_path, monkeypatch):
    import mobile_automation.inspector as inspector

    monkeypatch.setattr(inspector.time, "sleep", lambda _: None)
    session = InspectorSession(SequentialClient(), tmp_path)
    result = session.execute_requirement("进入 Settings 页面，点击 Account 按钮", False)

    assert [step["target"] for step in result["steps"]] == ["Settings", "Account"]
    assert result["page_changed"] is True


def test_execution_uses_the_edited_script_instead_of_replanning_it(tmp_path, monkeypatch):
    import mobile_automation.inspector as inspector

    monkeypatch.setattr(inspector.time, "sleep", lambda _: None)
    client = FakeClient()
    session = InspectorSession(client, tmp_path)
    session.refresh()
    session.analyse_requirement("点击 登录")
    script = """from mobile_automation import AdbClient, UiTree
client = AdbClient(serial='ignored')
tree = UiTree.capture(client)
node = tree.find_by_resource_id('login')
UiTree.click(client, node)
"""

    result = session.execute_requirement("点击 登录", False, script)

    assert client.tapped == (60, 45)
    assert result["edited_script"] is True
    assert result["generated_script"] == script.strip()


def test_scroll_to_bottom_executes_without_requiring_a_visible_element(tmp_path, monkeypatch):
    import mobile_automation.inspector as inspector

    monkeypatch.setattr(inspector.time, "sleep", lambda _: None)
    client = FakeClient()
    session = InspectorSession(client, tmp_path)
    result = session.execute_requirement("向上滑动页面，直到滑到页面底部", False)

    assert len(result["steps"]) == 2
    assert all(step["target"] == "向上滑动" for step in result["steps"])
    assert "client.swipe" in result["generated_script"]


def test_inspector_page_provides_copy_controls_for_both_script_panels():
    assert 'id="copyLocatorButton"' in _PAGE
    assert 'id="copyAgentButton"' in _PAGE
    assert "function copyToClipboard" in _PAGE
    assert 'id="editAgentButton"' in _PAGE
    assert "toggleAgentScriptEditor" in _PAGE
