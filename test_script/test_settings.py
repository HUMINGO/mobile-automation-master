from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))
from mobile_automation import AdbClient, UiTree


from utils.android_actions import (
    swipe_until_element_visible,
    save_screenshot,
    input_text_into_field,
    generate_timestamps,
    wait_for_page_ready,
    dismiss_known_popups,
)


client = AdbClient(serial='9XRWMBROZXFIZD45')

dismiss_known_popups(client)

# 滑动页面直到页面出现settings
node = swipe_until_element_visible(client, content_desc='Settings', max_swipes=10)
# 必须在点击前抓取当前页面，用于确认后续已真正进入 Settings 页面。
before_navigation = UiTree.capture(client)
UiTree.click(client, node)
# 确保真正进入到了setting页面
wait_for_page_ready(client, before_navigation, timeout_seconds=8)
save_screenshot(client, PROJECT_ROOT / "artifacts" / "test_cases" / f"{generate_timestamps()}.png")
# 下一步：重新抓取 UI 树并断言目标页面元素。
