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
    restart_app,
    wait_for_element_visible,
)

client = AdbClient(serial='9XRWMBROZXFIZD45')

def test_enter_task_page():
    restart_app(client, 'com.boloup.pro.beta')
    dismiss_known_popups(client)
    tree = UiTree.capture(client)

    # 进入“我的”页面。启动命令返回不代表首屏已经渲染完成；轮询等待
    # content-desc="Me" 的可点击父节点，不能依赖文字子节点。
    node = wait_for_element_visible(client, content_desc="Me", timeout_seconds=12)
    before_me_navigation = UiTree.capture(client)
    UiTree.click(client, node)
    wait_for_page_ready(client, before_me_navigation, timeout_seconds=8)

    node_task = wait_for_element_visible(client, content_desc="Tasks", timeout_seconds=12)

    UiTree.click(client, node_task)

    # 获取元素
    node_join_agency = wait_for_element_visible(client, content_desc="Join agency", timeout_seconds=12)

    # 使用此元素执行脚本操作
    UiTree.click(client, node_join_agency)  # 中心坐标 540, 1017
    # client.input_text("文本")
    # client.swipe(x1, y1, x2, y2, duration_ms=300)


if __name__ == '__main__':
    test_enter_task_page()