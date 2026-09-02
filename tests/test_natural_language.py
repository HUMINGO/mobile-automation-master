from mobile_automation.natural_language import (
    find_planned_node,
    plan_input_request,
    plan_request,
    plan_scroll_request,
    requested_input,
    requested_target,
    requested_targets,
    requests_scroll_to_bottom,
)
from mobile_automation.ui import UiTree


def tree():
    return UiTree('''<hierarchy>
        <node text="Trade Password" resource-id="app:id/trade_password" class="android.widget.Button" clickable="true" bounds="[1,2][3,4]" />
        <node text="Notifications" resource-id="app:id/notifications" class="android.widget.Button" clickable="true" bounds="[1,5][3,7]" />
    </hierarchy>''')


def test_plans_click_from_chinese_request_and_generates_reusable_script():
    plan = plan_request("点击 Trade Password按钮，然后重置密码", tree())
    assert plan.locator_kind == "resource_id"
    assert plan.locator_value == "app:id/trade_password"
    assert plan.risk_confirmation_required is True
    assert "find_by_resource_id('app:id/trade_password')" in plan.as_dict()["generated_script"]


def test_extracts_and_generates_multiple_navigation_steps():
    plan = plan_request("进入 Settings 页面，点击 Account 按钮", UiTree('''<hierarchy>
        <node text="Settings" resource-id="settings" class="android.widget.Button" clickable="true" bounds="[1,2][3,4]" />
    </hierarchy>'''))
    assert requested_targets("进入 Settings 页面，点击 Account 按钮") == ["Settings", "Account"]
    assert plan.follow_up_targets == ("Account",)
    assert plan.as_dict()["steps"][1]["target"] == "Account"
    assert "tree.find_by_text('Account')" in plan.as_dict()["generated_script"]


def test_planned_node_is_relocated_from_fresh_tree():
    plan = plan_request("点击 Trade Password", tree())
    assert find_planned_node(tree(), plan).text == "Trade Password"


def test_rejects_ambiguous_or_non_click_request():
    assert requested_target("点击 Trade Password按钮") == "Trade Password"
    assert requested_target("进入 settings页面") == "settings"
    try:
        plan_request("重置 Trade Password", tree())
    except ValueError as exc:
        assert "点击需求" in str(exc)
    else:
        raise AssertionError("expected a clear click request error")


def test_unrelated_clickable_nodes_are_not_target_matches():
    unrelated = UiTree('''<hierarchy>
        <node text="Other" resource-id="app:id/other" class="android.widget.Button" clickable="true" bounds="[1,2][3,4]" />
    </hierarchy>''')
    try:
        plan_request("点击 Trade Password", unrelated)
    except ValueError as exc:
        assert "未找到" in str(exc)
    else:
        raise AssertionError("expected a missing target error")


def test_destination_language_merges_clickable_container_and_text_child():
    settings = UiTree('''<hierarchy>
        <node text="" resource-id="" content-desc="Settings" class="android.view.ViewGroup" clickable="true" bounds="[1,2][30,40]" />
        <node text="Settings" resource-id="" content-desc="" class="android.widget.TextView" clickable="false" bounds="[5,8][25,28]" />
    </hierarchy>''')
    plan = plan_request("进入settings页面", settings)
    assert plan.locator_kind == "content_desc"
    assert plan.locator_value == "Settings"


def test_scroll_to_bottom_plan_does_not_require_an_initially_visible_target():
    plan = plan_scroll_request("向上滑动页面，直到滑到页面底部，点击 Privacy Policy")
    assert requests_scroll_to_bottom(plan.request) is True
    assert plan.target == "Privacy Policy"
    assert plan.as_dict()["locator"] is None
    assert "client.swipe" in plan.as_dict()["generated_script"]


def test_scroll_bottom_accepts_omitted_direction_and_common_chinese_phrases():
    for request in ("滑动到页面底部，点击settings按钮", "滑到最底部", "滚动到底部", "向上滑动直到页面底部"):
        assert requests_scroll_to_bottom(request) is True


def test_enter_to_prefix_is_not_part_of_the_target_and_scroll_uses_label_fallback():
    plan = plan_scroll_request("滑动到页面底部，进入到settings页面")
    assert plan.target == "settings"
    script = plan.as_dict()["generated_script"]
    assert "def find_by_label(tree, label):" in script
    assert "find_by_label(tree, 'settings')" in script


def test_navigation_context_does_not_create_a_duplicate_click_target():
    assert requested_targets("点击 Task 按钮，进入 Task 页面后，点击 Join agency 按钮") == [
        "Task", "Join agency",
    ]


def test_plans_named_input_field_and_generates_input_script():
    input_tree = UiTree('''<hierarchy>
        <node text="Enter Agent ID" resource-id="app:id/agent_id" content-desc=""
              class="android.widget.EditText" clickable="true" enabled="true"
              bounds="[1,2][30,40]" />
    </hierarchy>''')

    plan = plan_input_request("定位到Join agency输入框，输入内容：test", input_tree)

    assert requested_input("定位到Join agency输入框，输入内容：test") == ("Join agency", "test")
    assert plan.locator_kind == "resource_id"
    assert plan.locator_value == "app:id/agent_id"
    assert plan.input_value == "test"
    assert "input_text_into_field" in plan.as_dict()["generated_script"]
    assert "INPUT_VALUE = 'test'" in plan.as_dict()["generated_script"]


def test_sensitive_input_is_confirmed_and_not_written_to_script():
    input_tree = UiTree('''<hierarchy>
        <node text="Verification Code" resource-id="app:id/code" content-desc=""
              class="android.widget.EditText" clickable="true" enabled="true"
              bounds="[1,2][30,40]" />
    </hierarchy>''')

    plan = plan_input_request("定位到验证码输入框，输入内容：123456", input_tree)

    assert plan.risk_confirmation_required is True
    assert "123456" not in plan.as_dict()["generated_script"]
