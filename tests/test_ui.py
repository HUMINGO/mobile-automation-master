from mobile_automation.ui import Bounds, UiTree


XML = """\
<hierarchy>
  <node text="" resource-id="root" class="android.view.View" clickable="false"
        enabled="true" bounds="[0,0][720,1600]">
    <node text="取消" resource-id="cancel" class="android.widget.Button"
          clickable="true" enabled="true" bounds="[100,900][300,1000]" />
    <node text="确定" resource-id="confirm" class="android.widget.Button"
          clickable="true" enabled="true" bounds="[420,900][620,1000]" />
  </node>
</hierarchy>
"""


def test_bounds_and_find_methods():
    tree = UiTree(XML)
    assert tree.screen_size == (720, 1600)
    assert tree.find_by_text("确定").resource_id == "confirm"
    assert tree.find_by_resource_id("cancel").text == "取消"
    assert tree.find_xpath('//*[@text="确定"]').resource_id == "confirm"
    assert Bounds.parse("[420,900][620,1000]").center == (520, 950)


def test_center_candidates_are_clickable():
    tree = UiTree(XML)
    candidates = tree.center_candidates()
    assert len(candidates) == 2
    assert all(node.clickable for node in candidates)
