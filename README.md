# Python 手机自动化工具

一个面向内部使用的轻量 Android 自动化项目。Python 负责组织任务，ADB 负责连接和控制手机。目前支持：

- 发现 USB / Wi-Fi 连接的 Android 设备
- 点击、滑动、文字输入和系统按键
- 启动、停止 App
- 截图和导出当前页面 UI XML
- 通过 JSON 编排步骤，并在页面文本缺失时让任务失败

## 1. 环境准备

需要 Python 3.8+ 和 Android Platform Tools。

macOS：

```bash
brew install android-platform-tools
```

在 Android 手机上打开“开发者选项”和“USB 调试”，用数据线连接并允许电脑调试。然后检查连接：

```bash
adb devices
```

## 2. 安装项目

在项目目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

如果是本机自带的旧版 `pip 20.x`，且提示缺少 `bdist_wheel`，可以使用兼容安装方式：

```bash
python setup.py develop
```

开发和测试额外安装：

```bash
python -m pip install pytest
pytest
```

可编辑模式下，修改 src/mobile_automation/ 中的 Python 代码后通常不需要重新安装；但如果修改了依赖、命令入口或项目元数据，建议重新执行安装命令。
以后再次进入项目，通常只需要：


```bash
cd /Users/haifyao/Documents/mobile-automation
source .venv/bin/activate

```

## 3. 使用

```bash
# 查看设备
mobile-auto devices

# 运行示例任务
mobile-auto run examples/open_settings.json

# 多台设备时指定其中一台
mobile-auto --serial SERIAL run examples/open_settings.json

# 单独截图
mobile-auto screenshot artifacts/screen.png
```

### 设备检查器（截图、UI 元素与手动操作）

连接手机后可启动本机网页检查器。页面会同步显示当前手机截图和通过
UIAutomator 导出的元素列表，包括文本、`resource-id`、`content-desc`、类型、
边界和中心坐标。选择元素可在截图中高亮，并生成可复制到自动化测试脚本的
定位示例；实际的点击、输入、滑动由测试脚本执行。

```bash
# 已安装项目命令入口时
mobile-auto --serial YOUR_DEVICE_SERIAL inspect

# 或无需重新安装，直接使用源码
PYTHONPATH=src python -m mobile_automation --serial YOUR_DEVICE_SERIAL inspect
```

启动后在浏览器打开 <http://127.0.0.1:8765/>。未传 `--serial` 时，仅连接了一台
状态为 `device` 的设备会自动选中；多设备时必须指定序列号。检查器只绑定
本机地址，不会开放给局域网。每次刷新会保留最新截图和 XML 到
`artifacts/inspector/`；按 `Ctrl+C` 停止。

如默认端口已被占用，可指定端口：

```bash
mobile-auto --serial YOUR_DEVICE_SERIAL inspect --port 8766
```

### 测试脚本公共方法

根目录的 `utils` 提供测试用例可直接复用的 Android 操作。生成到
`test_script/` 的用例已自动把项目根目录加入导入路径：

```python
from utils import (
    input_text_into_field,
    restart_app,
    save_screenshot,
    swipe_until_element_visible,
    wait_for_page_ready,
)

# 重新启动 App；可选地指定启动 Activity。
restart_app(client, "com.example.app", activity=".MainActivity")

# 向上滑动，直到元素进入 UI 树；返回的 node 可继续点击。
node = swipe_until_element_visible(
    client, resource_id="app:id/submit", max_swipes=8,
)

# 点击导航元素后，确认页面已切换再继续截图或断言。
before_navigation = UiTree.capture(client)
UiTree.click(client, node)
wait_for_page_ready(client, before_navigation, timeout_seconds=8)

# 保存当前页面截图。相对路径以项目根目录为基准，而非 IDE 的工作目录。
save_screenshot(client, "artifacts/test_cases/submit_page.png")

# 定位输入框、聚焦、清空当前文本后输入新内容。
input_text_into_field(
    client, "test@example.com", resource_id="app:id/email", clear=True,
)
```

`swipe_until_element_visible` 支持 `up`、`down`、`left`、`right` 四个方向；
超过 `max_swipes` 仍找不到元素会抛出 `ElementNotFoundError`，避免测试静默通过。
默认至少需要有 24 像素宽和高进入屏幕，避免仅露出一条边的元素被误判为可见；
可通过 `min_visible_pixels` 调整阈值。

### 批量运行测试用例

批量运行器会按文件名发现 `test_script/test_*.py`，每个用例使用独立进程，
并将单独日志、`report.json` 和可视化的 `report.html` 写入
`artifacts/test_runs/时间戳/`。`report.html` 会展示每个用例的执行结果、操作步骤、
步骤截图和原始日志；可直接双击在浏览器中打开。默认失败即停止，避免异常页面状态
影响后续用例：

```powershell
python -m utils.run_test_scripts
```

先仅检查即将执行哪些脚本：

```powershell
python -m utils.run_test_scripts --dry-run
```

失败后仍继续执行、或仅运行某一类用例：

```powershell
python -m utils.run_test_scripts --continue-on-error
python -m utils.run_test_scripts --pattern "test_settings.py"
```

可使用 `--timeout 180` 调整单个用例最大执行时长。每个用例沿用其脚本中配置的
设备序列号；批量运行前请确认全部用例面向同一授权测试设备。

批量运行时，项目的 `UiTree.click`、`client.swipe`、`client.input_text`，以及
`utils.android_actions` 中的重启、等待、显式截图等公共操作会自动记录到报告中。
输入操作只记录长度，不会将实际输入内容写入报告。

### Play Ocean Hunt 自动点击

先在真机上手动进入 Play Ocean Hunt 游戏页面，并保持手机竖屏。下面的脚本会
每轮先点击已标定的 `COLLECT` 坐标 `(341, 1239)`，短暂等待后再点击 `SPIN`
坐标 `(622, 1384)`，然后随机等待 1–3 秒。出现 Big Win 弹窗时会自动收取，
没有弹窗时 COLLECT 坐标的点击不会影响游戏。
脚本不会启动 App、切换页面或处理 Big Win 以外的游戏弹窗；按 `Ctrl+C`
可以安全停止：

```bash
source .venv/bin/activate
python examples/Play_ocean_hunt.py
```

默认使用设备 `QWV8XSU4FEOZ8D9H`。可以覆盖设备、点击坐标、等待区间，或指定
有限点击次数：

```bash
python examples/Play_ocean_hunt.py \
  --serial YOUR_DEVICE_SERIAL \
  --x 622 \
  --y 1384 \
  --collect-x 341 \
  --collect-y 1239 \
  --collect-delay 0.2 \
  --min-delay 1 \
  --max-delay 3 \
  --iterations 10
```

`--iterations 0` 为默认值，表示持续运行直到按 `Ctrl+C`。

每次点击 SPIN 后，脚本会等待本轮随机间隔，再截取真机画面并识别 Diamond
余额。识别结果会实时输出到终端，并以逐行 JSON 追加到
`artifacts/play_ocean_hunt/balance.log`。最新全屏截图和余额裁剪图保存在
`artifacts/play_ocean_hunt/`，方便排查识别错误：

```text
余额识别：第 1 次，余额=84,049
```

可用 `--log-file`、`--output-dir` 和 `--ocr-path` 覆盖日志、诊断图片和
Tesseract 路径。默认使用 `/opt/homebrew/bin/tesseract`。

### Boloup Jungle Hunt 自动点击

在 Boloup 中手动打开 Jungle Hunt 并保持竖屏，然后运行：

```bash
source .venv/bin/activate
python examples/Play_Jungle_hunt_boloup.py
```

脚本默认先点击结果弹窗兜底坐标 `(360, 1239)`，再点击 SPIN `(631, 1433)`，
等待 1–3 秒后 OCR 识别顶部余额。余额实时输出到终端，并追加写入
`artifacts/play_jungle_hunt_boloup/balance.log`；最新截图与裁剪图保存在同目录。
默认持续运行，按 `Ctrl+C` 停止；可用 `--iterations 10` 运行有限轮次，其他
坐标、间隔、日志和 OCR 参数与 Ocean Hunt 脚本一致。

### 青书 App 实测脚本

下面的脚本会连接唯一一台已授权设备，自动打开 `com.baitu.qingshu`，依次
点击 `navMe`、`Fun Island` 和首页动态提示条中的 `Win`，进入 Square。

找到并点击 `navMe` 后，脚本会重新导出 UI 树，并按照 XPath
`//*[@text="Fun Island"]` 查找 `Fun Island`；节点存在时自动点击。
进入页面后，脚本继续按照 XPath `//*[@text="Win"]` 查找并点击 `Win`。
每个导航控件最多检测 3 次，每次间隔 5 秒。

进入 `Square` WebView 后，脚本默认循环 10000 次。每轮选择最后一条可见
`Win` 记录，点击同行用户名打开详情，等待页面稳定，解析基本信息并以追加
模式写入 CSV，然后返回 Square 并随机等待 5–10 秒：

个人信息包含年龄、国家、性别和 Interest Tags。性别根据 `ivGender` 与年龄
区域的颜色判断：红色写入 `female`，蓝色写入 `male`，无法可靠区分时写入
`unknown`。截图单次超时为 60 秒且不重试；远程设备可增加
`--skip-gender-detection` 完全跳过截图和性别识别，直接写入 `unknown`。
兴趣标签直接读取所有 `tv_interest_tag` 节点。旧 CSV 会自动
迁移并新增 `gender` 列，历史记录该列留空。

```bash
# 测试 1 次
python examples/open_qingshu_and_click.py --iterations 1

# 远程设备跳过截图性别识别
python examples/open_qingshu_and_click.py \
  --serial localhost:57144 \
  --skip-gender-detection

# 自定义次数和 CSV 路径
python examples/open_qingshu_and_click.py \
  --iterations 100 \
  --csv-output artifacts/qingshu/users.csv
```


# 自定义次数和 CSV 路径
python examples/open_qingshu_and_click.py \
  --iterations 100 \
  --csv-output artifacts/qingshu/users.csv \
  --serial localhost:57144
```


`--iterations` 可设置为 0–10000，默认 10000。CSV 不存在时会自动创建并
写入表头，已存在时只追加新记录。`--iterations` 表示总目标次数；默认读取
`artifacts/qingshu/users.state.json` 断点续跑。状态文件缺失时，会读取 CSV
最大的 `iteration` 并从下一轮继续。

未提供 `--serial` 时，如果只有一台已授权设备，程序会自动连接；如果检测到
多台已授权设备，会列出设备名称和 serial，并提示输入数字编号选择。离线或
尚未授权的设备不会出现在选择菜单中。

```bash
# 从断点继续到总计 10000 轮
python examples/open_qingshu_and_click.py --iterations 10000

# 明确忽略断点，从 iteration 0 开始（CSV 仍保持追加模式）
python examples/open_qingshu_and_click.py --iterations 100 --fresh

# 自定义状态文件
python examples/open_qingshu_and_click.py \
  --iterations 10000 \
  --state-file artifacts/qingshu/custom.state.json
```

页面异常时，脚本会先尝试从详情页返回、从 App 首页重新导航，最后才强制
重启 App。每个逻辑迭代最多尝试 3 次，只写一行 CSV；连续 3 个迭代完全
恢复失败后安全停止。失败现场的 XML、截图和错误日志保存在
`artifacts/qingshu/recovery/`。

```bash
source .venv/bin/activate
python examples/open_qingshu_and_click.py
```

如果连接了多台设备：

```bash
python examples/open_qingshu_and_click.py --serial YOUR_DEVICE_SERIAL
```

### 用户筛选与私信计划（预演）

采集完成后，可以按性别、国家、年龄、Win 金额和实名认证筛选，
并生成去重后的待审核私信清单。该命令只写 CSV，不会打开 App 或实际发送：

```bash
python examples/plan_poppo_outreach.py \
  --gender female \
  --country US,CA \
  --min-age 21 \
  --max-age 40 \
  --min-source-amount 1000000 \
  --limit 50
```

输出默认为 `artifacts/qingshu/outreach_plan.csv`，每个 `user_id` 只保留最新
资料。输出 CSV 只包含 `user_id`、`profile_name`、`gender`、`age`、
`country`、`status` 和 `reason` 七列，状态统一为 `pending_review`。
通过 `--history` 传入历史结果 CSV 后，
其中 `status=sent` 的用户不会再次进入清单。年龄筛选下限不允许低于 18 岁；
年龄缺失或未满 18 岁的资料始终不会进入私信清单。

审核清单后，把允许联系的行从 `pending_review` 改为 `approved`，再运行私信
执行器。执行器只处理 `approved`，按照 `country` 从
`config/poppo_message_templates.json` 选择语言、首条文案和回复后文案。文案中的
`{{app_name}}` 默认替换为 `yago`：

```bash
python examples/send_poppo_messages.py \
  --plan artifacts/qingshu/outreach_plan.csv \
  --templates config/poppo_message_templates.json \
  --history artifacts/qingshu/message_history.csv \
  --limit 100 \
  --poll-interval 10
```

`--limit` 表示本次运行的首发成功配额，而不是只读取计划表前 N 人。
程序按计划顺序每批读取 N 人，历史跳过用户和发送失败用户都不占配额；
当前批次不足时继续读取下一批，直到成功记录 N 个新的 `waiting_reply`，
或 approved 数据全部耗尽。`--skip-send` 会忽略该配额，只监控全部计划内
已有的 `waiting_reply`。

如果只需监控并处理已经首发用户的新回复，可增加 `--skip-send`。该参数不会
发送任何首条私信，也不会修改尚未首发用户的历史状态；程序会直接进入
`navMsg` 持续扫描。聊天内确认回复后，仍会发送计划中的后续文案：

```bash
python examples/send_poppo_messages.py --skip-send
```

启动时如果没有已授权的可用设备，程序会按递增间隔重新检测：第 1 次等待
30 秒，第 N 次等待 `30 × N` 秒，最多重试 10 次。十次重试后仍无法连接时，
程序退出并通过飞书机器人发送失败通知；可用 `--feishu-config` 指定机器人
配置，或用 `--no-feishu` 关闭该通知。
运行中如果 ADB 返回 `device offline`、`device '<serial>' not found` 或
`no devices/emulators found`，私信和 Square 采集任务也会采用相同的间隔与
次数重连。私信任务跳过当时用户并在下次运行重试；采集任务恢复后重试当前
iteration。重连成功时发送飞书恢复通知；最终失败时保留断点、停止任务并发送
飞书失败通知。
恢复期间只轮询 `adb devices -l`，不会执行 `adb reconnect` 主动重置 USB 或
TCP 设备连接。

默认不检查个人页状态，所有审核通过的用户都会进入私信流程。如果只希望联系
当前在线用户，执行时增加 `--online-only`；此模式仅把 `statusText=Online` 或
`roomState=Party`（忽略大小写和两端空格）视为在线。
执行期间会实时输出带时间戳的关键步骤日志，包括 App 启动、首页恢复、搜索、
详情核对、状态判断、消息确认、轮次切换、未读扫描和每位用户的最终结果。

程序开始处理第一个有效用户前会强制重启一次 App。后续每个用户开始时会
根据当前所处的搜索页、详情页或会话页按返回键，最多返回 5 次，直到 UI 树
重新出现 `navLive`；然后点击 `navLive` 并确认首页搜索入口存在，不再为每个
用户重启 App。如果返回过程中前台 Activity 已不属于 Poppo，或返回 5 次后仍
找不到 `navLive`，程序会强制重启 Poppo，再确认 `navLive` 和首页搜索入口。
之后执行全站搜索精确 `user_id`、核对详情页 ID、进入对应
会话并确认第一条消息已形成右侧气泡，随后立即记录 `waiting_reply` 并处理下一
用户。每成功首发 5 人为一轮；失败用户不占名额，本次运行不重复尝试。

每轮结束后程序进入 `navMsg` Message 页，打开所有非系统且带未读标记的会话，
不使用列表昵称筛选。进入聊天后，程序把气泡与当前计划内全部
`waiting_reply` 历史逐一核对：必须精确找到历史首条外发消息，并确认其后存在
对方回复；只有唯一用户匹配时才发送保存的后续文案并记录 `completed`。无匹配
或多个用户匹配时均跳过，因此计划外未读只会被打开并变为已读，不会发送。
没有未读标记的会话不会因历史中保存了回复而主动重试。每处理或跳过一个会话
后都会重新返回 Message 页并从列表顶部扫描。所有首发目标处理完后，程序按
`--poll-interval` 持续扫描 Message 页，直到按 Ctrl+C；停止时恢复原输入法并
尽量保持 Poppo 停留在 Message 列表。找不到精确用户时记录
`user_not_found`；后续消息发送失败时保留 `waiting_reply` 和已读取回复，等待
该会话再次出现未读后处理。非 Online 或状态控件缺失时记录 `offline` 并跳过，
下次执行会重新检查，不会生成故障截图。

首发前会按 `user_id` 读取消息历史中的最新状态。`completed`、
`waiting_reply`、`error` 和 `user_not_found` 会完全跳过首发；
`device_offline`、`offline` 及没有历史记录的用户仍允许再次尝试。

真机调试时可只验证导航链路，不发送任何内容：

```bash
python examples/send_poppo_messages.py --verify-user-id 66128032
```

### ADB SSH 隧道监控

`adb_tunnel_monitor.py` 每隔 5 分钟执行一次 `adb devices`。只要存在至少一个
状态为 `device` 的设备，就不会修改连接；如果没有可用设备，则复用或建立
配置中的 SSH 本地转发，并执行 `adb connect`：

```bash
.venv/bin/python examples/adb_tunnel_monitor.py
```

首次确实需要建立 SSH 隧道时，程序会隐藏提示输入连接密钥。密钥只保存在
当前 Python 进程的内存中，不写入配置文件；同一次运行的后续重连会复用。
如果认证失败，内存中的密钥会被清除，下次恢复时重新询问。

当前本地配置位于 `config/adb_tunnel_monitor.local.json`，该文件已加入
`.gitignore`。IP、SSH 端口、本地端口、远端 ADB 端口和检测间隔均可修改。
模板见 `config/adb_tunnel_monitor.example.json`。可先执行一次性检查验证配置：

```bash
.venv/bin/python examples/adb_tunnel_monitor.py --once
```

也可以不安装命令入口，直接运行：

```bash
PYTHONPATH=src python3 -m mobile_automation devices
```

## 任务格式

```json
{
  "steps": [
    {"action": "start_app", "package": "com.android.settings"},
    {"action": "wait", "seconds": 1},
    {"action": "tap", "x": 500, "y": 1200},
    {"action": "input_text", "text": "hello world"},
    {"action": "swipe", "x1": 500, "y1": 1500, "x2": 500, "y2": 500, "duration_ms": 300},
    {"action": "keyevent", "keycode": "KEYCODE_BACK"},
    {"action": "assert_text", "text": "设置"},
    {"action": "screenshot", "name": "result.png"},
    {"action": "stop_app", "package": "com.android.settings"}
  ]
}
```

截图默认写入 `artifacts/`。任务文件应当只来自可信来源；不要用它自动处理支付、验证码或绕过平台风控。

## 检查当前 UI 树

`UiTree` 工具类支持抓取 UI、保存 XML、按文本/resource-id/XPath 查找、筛选
屏幕中央节点和安全计算点击坐标。查看当前页面中央的候选控件：

```bash
python examples/inspect_current_ui.py --serial YOUR_DEVICE_SERIAL
```

确定目标后可以按 XPath 点击：

```python
from pathlib import Path
from mobile_automation import AdbClient, UiTree

client = AdbClient(serial="YOUR_DEVICE_SERIAL")
tree = UiTree.capture(client, Path("artifacts/current.xml"))
node = tree.find_xpath('//*[@text="确定"]')
if node is not None:
    tree.click(client, node)
```

## 下一步建议

当坐标点击不够稳定时，可增加 UIAutomator2 的控件选择器（按文本、resource-id 查找）；需要 iPhone 时，再增加 Appium + WebDriverAgent 驱动层。核心任务格式可以保持不变。
