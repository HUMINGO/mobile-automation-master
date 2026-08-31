from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))
from mobile_automation import AdbClient, UiTree

client = AdbClient(serial='9XRWMBROZXFIZD45')
tree = UiTree.capture(client)
node = next((item for item in tree.nodes if item.content_desc == 'Tasks'), None)
if node is None:
    raise RuntimeError('未找到目标元素：Task')
UiTree.click(client, node)

# 获取元素
tree = UiTree.capture(client)
node = tree.find_by_text("Join agency")

# 使用此元素执行脚本操作
UiTree.click(client, node)  # 中心坐标 540, 1017
# client.input_text("文本")
# client.swipe(x1, y1, x2, y2, duration_ms=300)
