from pathlib import Path

import pytest
import utils.android_actions as android_actions
from mobile_automation import UiTree

from utils import (
    ElementNotFoundError,
    input_text_into_field,
    save_screenshot,
    swipe_until_element_visible,
    wait_for_page_ready,
)


NOT_FOUND_XML = '''<hierarchy><node text="Header" resource-id="header" class="android.view.View" clickable="false" bounds="[0,0][100,200]" /></hierarchy>'''
TARGET_XML = '''<hierarchy><node text="Continue" resource-id="continue" class="android.widget.Button" clickable="true" bounds="[20,80][80,120]" /></hierarchy>'''
OFFSCREEN_SETTINGS_XML = '''<hierarchy><node text="Settings" resource-id="settings" class="android.widget.Button" clickable="true" bounds="[20,250][80,290]" /></hierarchy>'''
VISIBLE_SETTINGS_XML = '''<hierarchy><node text="Settings" resource-id="settings" class="android.widget.Button" clickable="true" bounds="[20,80][80,120]" /></hierarchy>'''
CLIPPED_SETTINGS_XML = '''<hierarchy><node text="Settings" resource-id="settings" class="android.widget.Button" clickable="true" bounds="[20,195][80,200]" /></hierarchy>'''
INPUT_XML = '''<hierarchy><node text="old" resource-id="app:id/name" class="android.widget.EditText" clickable="true" bounds="[10,20][90,70]" /></hierarchy>'''


class ActionClient:
    def __init__(self, dumps):
        self.dumps = list(dumps)
        self.swipes = []
        self.taps = []
        self.entered = []
        self.cleared = []

    def dump_ui(self):
        return self.dumps.pop(0) if len(self.dumps) > 1 else self.dumps[0]

    def screen_size(self):
        return 100, 200

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self.swipes.append((x1, y1, x2, y2, duration_ms))

    def tap(self, x, y):
        self.taps.append((x, y))

    def clear_text(self, length):
        self.cleared.append(length)

    def input_text(self, value):
        self.entered.append(value)

    def screenshot(self, target):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path


def test_swipe_until_element_visible_returns_the_element_after_a_swipe(monkeypatch):
    monkeypatch.setattr("utils.android_actions.time.sleep", lambda _: None)
    client = ActionClient([NOT_FOUND_XML, TARGET_XML])

    node = swipe_until_element_visible(client, resource_id="continue", max_swipes=2)

    assert node.text == "Continue"
    assert client.swipes == [(50, 150, 50, 60, 350)]


def test_swipe_until_element_visible_reports_a_missing_target(monkeypatch):
    monkeypatch.setattr("utils.android_actions.time.sleep", lambda _: None)
    client = ActionClient([NOT_FOUND_XML])

    with pytest.raises(ElementNotFoundError, match="missing"):
        swipe_until_element_visible(client, text="missing", max_swipes=1)


def test_swipe_until_element_visible_does_not_treat_an_offscreen_node_as_visible(monkeypatch):
    monkeypatch.setattr("utils.android_actions.time.sleep", lambda _: None)
    client = ActionClient([OFFSCREEN_SETTINGS_XML, VISIBLE_SETTINGS_XML])

    node = swipe_until_element_visible(client, text="Settings", max_swipes=2)

    assert node.bounds.top == 80
    assert len(client.swipes) == 1


def test_swipe_until_element_visible_rejects_an_element_with_only_a_thin_visible_edge(monkeypatch):
    monkeypatch.setattr("utils.android_actions.time.sleep", lambda _: None)
    client = ActionClient([CLIPPED_SETTINGS_XML, VISIBLE_SETTINGS_XML])

    node = swipe_until_element_visible(client, text="Settings", max_swipes=2)

    assert node.bounds.top == 80
    assert len(client.swipes) == 1


def test_save_screenshot_uses_the_requested_png_path(tmp_path):
    client = ActionClient([NOT_FOUND_XML])
    target = tmp_path / "screen.png"

    result = save_screenshot(client, target)

    assert result == target
    assert target.read_bytes() == b"png"


def test_save_screenshot_resolves_relative_paths_from_project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(android_actions, "PROJECT_ROOT", tmp_path)
    client = ActionClient([NOT_FOUND_XML])

    result = save_screenshot(client, "artifacts/test_cases/page.png")

    assert result == tmp_path / "artifacts" / "test_cases" / "page.png"
    assert result.exists()


def test_input_text_into_field_focuses_clears_and_enters_text():
    client = ActionClient([INPUT_XML])

    node = input_text_into_field(
        client, "new value", resource_id="app:id/name", clear=True,
    )

    assert node.resource_id == "app:id/name"
    assert client.taps == [(50, 45)]
    assert client.cleared == [3]
    assert client.entered == ["new value"]


def test_wait_for_page_ready_waits_for_a_changed_ui_tree(monkeypatch):
    monkeypatch.setattr("utils.android_actions.time.sleep", lambda _: None)
    before = UiTree(NOT_FOUND_XML)
    client = ActionClient([TARGET_XML, TARGET_XML])

    ready = wait_for_page_ready(client, before, timeout_seconds=1)

    assert ready.find_by_resource_id("continue") is not None
