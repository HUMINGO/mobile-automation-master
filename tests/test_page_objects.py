from mobile_automation import UiTree
from page_objects import HomePage, MePage, TaskPage
from page_objects.base import PageElement


CLICKABLE_XML = '''<hierarchy><node text="" content-desc="Me" class="android.widget.Button" clickable="true" enabled="true" bounds="[20,80][80,120]" /></hierarchy>'''


def test_page_objects_keep_locators_with_their_own_pages():
    assert HomePage.ME_TAB.locator_kwargs() == {"content_desc": "Me"}
    assert MePage.TASKS.locator_kwargs() == {"content_desc": "Tasks"}
    assert MePage.SETTINGS.locator_kwargs() == {"content_desc": "Settings"}
    assert TaskPage.JOIN_AGENCY.locator_kwargs() == {"content_desc": "Join agency"}


def test_page_element_rejects_a_locator_without_any_condition():
    try:
        PageElement("缺少定位条件").locator_kwargs()
    except ValueError as exc:
        assert "没有配置定位条件" in str(exc)
    else:
        raise AssertionError("未配置定位条件时应抛出 ValueError")


def test_page_click_uses_page_locators_and_waits_for_destination(monkeypatch):
    import page_objects.base as page_base

    tree = UiTree(CLICKABLE_XML)
    node = tree.find_by_text("")
    calls = []
    monkeypatch.setattr(page_base, "wait_for_element_visible", lambda client, **kwargs: (calls.append(("wait", kwargs)) or node))
    monkeypatch.setattr(page_base.UiTree, "capture", lambda client: tree)
    monkeypatch.setattr(page_base.UiTree, "click", lambda client, clicked: calls.append(("click", clicked)))
    monkeypatch.setattr(page_base, "wait_for_page_ready", lambda client, before, **kwargs: (calls.append(("ready", kwargs)) or tree))

    page = HomePage(object())
    page.click(HomePage.ME_TAB, destination=MePage.TASKS)

    assert calls[0] == ("wait", {"timeout_seconds": 8, "content_desc": "Me"})
    assert calls[1] == ("click", node)
    assert calls[2] == ("ready", {"timeout_seconds": 8, "content_desc": "Tasks"})
