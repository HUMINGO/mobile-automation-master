"""A small browser-based inspector for an Android device connected through ADB.

The inspector deliberately uses only the Python standard library.  It is bound to
``127.0.0.1`` by default, so the page and its device-control endpoints are not
exposed to the local network.
"""

from __future__ import annotations

import ast
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .adb import AdbClient, AdbError
from .natural_language import (
    ClickPlan,
    ScrollPlan,
    find_planned_node,
    plan_request,
    plan_scroll_request,
    render_python,
    render_scroll_python,
    requests_scroll_to_bottom,
    requested_targets,
)
from .ui import UiTree


DEFAULT_PORT = 8765
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_SCRIPT_DIR = PROJECT_ROOT / "test_script"


def select_device(client: AdbClient) -> AdbClient:
    """Return a client with a usable serial, selecting it when unambiguous."""
    if client.serial:
        return client
    available = [device for device in client.devices() if device.state == "device"]
    if not available:
        raise AdbError("未发现状态为 device 的 Android 设备")
    if len(available) > 1:
        serials = ", ".join(device.serial for device in available)
        raise AdbError("检测到多个设备，请使用 --serial 指定其中一个：{}".format(serials))
    return AdbClient(serial=available[0].serial, adb_path=client.adb_path)


def _png_size(png: bytes) -> Tuple[int, int]:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return struct.unpack(">II", png[16:24])


class InspectorSession:
    """Keeps the latest screenshot and UI dump used by the browser page."""

    def __init__(
        self, client: AdbClient, artifacts: Path,
        test_script_dir: Optional[Path] = None,
    ) -> None:
        self.client = client
        self.artifacts = artifacts
        self.screenshot_path = artifacts / "current_screen.png"
        self.ui_dump_path = artifacts / "current_ui.xml"
        self._screenshot = b""
        self._nodes: List[Dict[str, Any]] = []
        self._screen_size = (0, 0)
        self._tree: Optional[UiTree] = None
        self.test_script_dir = test_script_dir or DEFAULT_TEST_SCRIPT_DIR
        self._last_plan: Optional[Any] = None
        self._last_plan_executed = False
        # The browser polls every second while an execution may include several
        # ADB operations.  ADB/UIAutomator calls and the shared snapshot must
        # therefore be used by one request at a time.
        self._device_lock = threading.RLock()

    def refresh(self) -> Dict[str, Any]:
        with self._device_lock:
            self.client.screenshot(self.screenshot_path)
            self._screenshot = self.screenshot_path.read_bytes()
            self._screen_size = _png_size(self._screenshot)
            tree = UiTree.capture(self.client, save_to=self.ui_dump_path)
            self._tree = tree
            self._nodes = []
            for index, node in enumerate(tree.nodes):
                description = node.describe()
                description["index"] = index
                self._nodes.append(description)
            return self.snapshot()

    def analyse_requirement(self, request: str) -> Dict[str, object]:
        with self._device_lock:
            if not request.strip():
                raise ValueError("请输入测试需求")
            if self._tree is None:
                self.refresh()
            self._last_plan = (
                plan_scroll_request(request)
                if requests_scroll_to_bottom(request)
                else plan_request(request, self._tree)
            )
            self._last_plan_executed = False
            return self._plan_payload(self._last_plan)

    def _plan_payload(self, plan: Any) -> Dict[str, object]:
        payload = plan.as_dict()
        serial = self.client.serial or "YOUR_DEVICE_SERIAL"
        payload["generated_script"] = (
            render_scroll_python(plan, serial)
            if isinstance(plan, ScrollPlan)
            else render_python(plan, serial)
        )
        return payload

    def execute_requirement(
        self, request: str, confirm_sensitive: bool, edited_script: Optional[str] = None,
    ) -> Dict[str, object]:
        """Execute the one reviewed navigation step and verify a page refresh."""
        with self._device_lock:
            if edited_script is not None:
                return self._execute_edited_script(request, edited_script, confirm_sensitive)
            self.refresh()
            plan = (
                plan_scroll_request(request)
                if requests_scroll_to_bottom(request)
                else plan_request(request, self._tree)
            )
            if plan.risk_confirmation_required and not confirm_sensitive:
                raise ValueError("该需求涉及密码、重置或其他高风险操作，请勾选确认后再执行")
            results = []
            page_changed = False
            if isinstance(plan, ScrollPlan):
                stable_count = 0
                for order in range(1, 13):
                    self.refresh()
                    before_xml = self._tree.xml_text
                    width, height = self._screen_size
                    if not width or not height:
                        raise ValueError("无法读取屏幕尺寸，未执行滑动")
                    self.client.swipe(width // 2, int(height * 0.75), width // 2, int(height * 0.30), 350)
                    time.sleep(0.35)
                    self.refresh()
                    changed = before_xml != self._tree.xml_text
                    stable_count = 0 if changed else stable_count + 1
                    results.append({"order": order, "target": "向上滑动", "coordinate": [width // 2, int(height * 0.75), width // 2, int(height * 0.30)], "page_changed": changed})
                    if stable_count >= 2:
                        break
                page_changed = any(step["page_changed"] for step in results)
                if plan.target:
                    self.refresh()
                    click_plan = plan_request("点击 {}".format(plan.target), self._tree)
                    node = find_planned_node(self._tree, click_plan)
                    if node is None:
                        raise ValueError("滑动到底部后仍未找到目标元素：{}".format(plan.target))
                    before_xml = self._tree.xml_text
                    x, y = UiTree.click(self.client, node)
                    time.sleep(0.5)
                    self.refresh()
                    changed = before_xml != self._tree.xml_text
                    page_changed = page_changed or changed
                    results.append({"order": len(results) + 1, "target": plan.target, "coordinate": [x, y], "page_changed": changed})
                    plan = ScrollPlan(plan.request, plan.target, plan.risk_confirmation_required, click_plan)
            else:
                for order, target in enumerate(requested_targets(request), start=1):
                    # Later targets may only exist after a preceding navigation.
                    self.refresh()
                    current_plan = plan_request("点击 {}".format(target), self._tree)
                    before_xml = self._tree.xml_text
                    node = find_planned_node(self._tree, current_plan)
                    if node is None:
                        raise ValueError("第 {} 步执行前目标元素已不存在：{}".format(order, target))
                    x, y = UiTree.click(self.client, node)
                    time.sleep(0.5)
                    self.refresh()
                    changed = before_xml != self._tree.xml_text
                    page_changed = page_changed or changed
                    results.append({"order": order, "target": target, "coordinate": [x, y], "page_changed": changed})
            self._last_plan = plan
            self._last_plan_executed = True
            return {
                "plan": self._plan_payload(plan),
                "executed": True,
                "steps": results,
                "page_changed": page_changed,
                "generated_script": self._plan_payload(plan)["generated_script"],
                "snapshot": self.snapshot(),
            }

    def _execute_edited_script(
        self, request: str, script: str, confirm_sensitive: bool,
    ) -> Dict[str, object]:
        """Run a reviewed generated script against this session's ADB client.

        The browser is bound to loopback, but imports are still restricted so an
        edited test case can only use the project's mobile-automation API.
        """
        script = script.strip()
        if not script:
            raise ValueError("编辑后的测试脚本不能为空")
        if len(script) > 200_000:
            raise ValueError("编辑后的测试脚本过长")
        risky_operation = re.search(r"\b(?:input_text|clear_text|keyevent|stop_app)\s*\(", script)
        if risky_operation and not confirm_sensitive:
            raise ValueError("编辑脚本包含输入或高风险设备操作，请勾选授权确认后再执行")
        try:
            parsed = ast.parse(script, filename="<edited_test_script>", mode="exec")
        except SyntaxError as exc:
            raise ValueError("编辑后的脚本语法错误：{}".format(exc.msg)) from exc
        body = []
        for statement in parsed.body:
            if isinstance(statement, ast.ImportFrom):
                allowed = (
                    statement.module == "mobile_automation"
                    and all(alias.name in {"AdbClient", "UiTree"} for alias in statement.names)
                )
                if not allowed:
                    raise ValueError("编辑脚本只允许导入 mobile_automation 的 AdbClient、UiTree")
                # Use the already connected, serialised session client rather
                # than creating a second independent ADB client.
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                raise ValueError("编辑脚本不允许导入其他模块")
            body.append(statement)
        parsed.body = body
        ast.fix_missing_locations(parsed)

        self.refresh()
        before_xml = self._tree.xml_text
        safe_builtins = {
            "RuntimeError": RuntimeError, "bool": bool, "enumerate": enumerate,
            "int": int, "len": len, "next": next, "range": range, "str": str,
        }
        namespace = {
            "__name__": "__edited_test_script__",
            "__builtins__": safe_builtins,
            "AdbClient": lambda *args, **kwargs: self.client,
            "UiTree": UiTree,
        }
        try:
            exec(compile(parsed, "<edited_test_script>", "exec"), namespace, namespace)
        except Exception as exc:
            raise RuntimeError("编辑脚本执行失败：{}".format(exc)) from exc
        time.sleep(0.5)
        self.refresh()
        changed = before_xml != self._tree.xml_text
        plan = self._last_plan
        if plan is None:
            plan = (
                plan_scroll_request(request)
                if requests_scroll_to_bottom(request)
                else plan_request(request, self._tree)
            )
        self._last_plan = plan
        self._last_plan_executed = True
        payload = self._plan_payload(plan)
        payload["generated_script"] = script
        return {
            "plan": payload,
            "executed": True,
            "edited_script": True,
            "steps": [{
                "order": 1, "target": "编辑后的测试脚本", "coordinate": [],
                "page_changed": changed,
            }],
            "page_changed": changed,
            "generated_script": script,
            "snapshot": self.snapshot(),
        }

    def save_test_case(self, filename: str, script: Optional[str] = None) -> Path:
        """Save the reviewed, executed plan as one non-overwritable test case."""
        if self._last_plan is None or not self._last_plan_executed:
            raise ValueError("请先执行并确认分析出的测试步骤，再保存测试用例")
        name = filename.strip()
        if not name.endswith(".py"):
            name += ".py"
        if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]+\.py", name):
            raise ValueError("文件名仅支持中文、字母、数字、下划线、中划线，并以 .py 结尾")
        target = self.test_script_dir / name
        if target.exists():
            raise FileExistsError("测试用例文件已存在：{}；请更换文件名".format(target.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        serial = self.client.serial or "YOUR_DEVICE_SERIAL"
        if script is None:
            script = (
                render_scroll_python(self._last_plan, serial)
                if isinstance(self._last_plan, ScrollPlan)
                else render_python(self._last_plan, serial)
            )
        else:
            script = script.strip()
            if not script:
                raise ValueError("编辑后的测试脚本不能为空")
            if len(script) > 200_000:
                raise ValueError("测试脚本过长，无法写入")
        # Generated test cases live under project_root/test_script.  This
        # bootstrap lets them run from the repository root without requiring
        # the caller to set PYTHONPATH manually.
        bootstrap = "\n".join([
            "from pathlib import Path",
            "import sys",
            "PROJECT_ROOT = Path(__file__).resolve().parents[1]",
            "sys.path.insert(0, str(PROJECT_ROOT))",
            "sys.path.insert(0, str(PROJECT_ROOT / 'src'))",
            "",
        ])
        target.write_text(bootstrap + script + "\n", encoding="utf-8")
        return target

    def snapshot(self) -> Dict[str, Any]:
        return {
            "serial": self.client.serial,
            "screen_size": list(self._screen_size),
            "nodes": self._nodes,
            "node_count": len(self._nodes),
        }

    @property
    def screenshot(self) -> bytes:
        return self._screenshot

class InspectorHandler(BaseHTTPRequestHandler):
    session: InspectorSession

    def log_message(self, format: str, *args: Any) -> None:
        # Avoid duplicating browser request logs in the command prompt.
        return

    def _json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # A browser may abort an automatic polling request while navigating
            # or reloading.  The request has no client left to receive a reply.
            return

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 100_000:
            raise ValueError("请求内容无效")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象")
        return payload

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._html()
            elif path == "/api/refresh":
                self._json({"ok": True, **self.session.refresh()})
            elif path == "/api/status":
                self._json({"ok": True, **self.session.snapshot()})
            elif path == "/api/screenshot":
                if not self.session.screenshot:
                    self.session.refresh()
                image = self.session.screenshot
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(image)))
                self.end_headers()
                self.wfile.write(image)
            else:
                self._error("未找到页面", HTTPStatus.NOT_FOUND)
        except (AdbError, OSError, ValueError, RuntimeError) as exc:
            self._error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE)

    def do_POST(self) -> None:
        try:
            payload = self._body()
            path = urlparse(self.path).path
            if path == "/api/agent/save-test-case":
                filename = payload.get("filename")
                if not isinstance(filename, str):
                    raise ValueError("filename 必须是字符串")
                script = payload.get("script")
                if script is not None and not isinstance(script, str):
                    raise ValueError("script 必须是字符串")
                self._json({"ok": True, "path": str(self.session.save_test_case(filename, script))})
                return
            request = payload.get("request")
            if not isinstance(request, str):
                raise ValueError("request 必须是字符串")
            if path == "/api/agent/plan":
                self._json({"ok": True, "plan": self.session.analyse_requirement(request)})
            elif path == "/api/agent/execute":
                edited_script = payload.get("script")
                if edited_script is not None and not isinstance(edited_script, str):
                    raise ValueError("script 必须是字符串")
                self._json({"ok": True, **self.session.execute_requirement(
                    request, bool(payload.get("confirm_sensitive")), edited_script,
                )})
            else:
                self._error("未找到操作", HTTPStatus.NOT_FOUND)
        except FileExistsError as exc:
            self._error(str(exc), HTTPStatus.CONFLICT)
        except (AdbError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._error(str(exc))

    def _html(self) -> None:
        body = _PAGE.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_inspector(
    client: AdbClient, artifacts: Path, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> None:
    """Start the loopback-only inspector server until Ctrl+C is pressed."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("检查器仅允许绑定本机地址")
    session = InspectorSession(select_device(client), artifacts)
    InspectorHandler.session = session
    server = ThreadingHTTPServer((host, port), InspectorHandler)
    print("已连接设备：{}".format(session.client.serial))
    print("请在浏览器打开：http://{}:{}/".format(host, port))
    print("按 Ctrl+C 停止设备检查器。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n设备检查器已停止。")
    finally:
        server.server_close()


_PAGE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Android 设备检查器</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,"Microsoft YaHei",sans-serif;background:#f4f6f8;color:#1f2937}header{position:sticky;top:0;z-index:10;padding:14px 20px;background:#172554;color:white;display:flex;gap:16px;align-items:center;box-shadow:0 2px 6px #0003}button{border:0;border-radius:6px;padding:8px 12px;background:#2563eb;color:white;cursor:pointer}button:disabled{cursor:wait;opacity:.65}.feedback{min-width:132px;font-weight:600}.feedback.success{color:#86efac}.feedback.error{color:#fca5a5}.feedback.pending{color:#fde68a}.grid{display:grid;grid-template-columns:minmax(360px,1fr) minmax(430px,1.25fr);gap:16px;padding:16px;align-items:start}.card{background:white;border-radius:10px;padding:14px;box-shadow:0 1px 4px #0002}.agent{grid-column:1/-1}.agent textarea{display:block;width:100%;min-height:68px;margin:8px 0;padding:8px;font:14px system-ui,"Microsoft YaHei",sans-serif;border:1px solid #cbd5e1;border-radius:6px}.agent input[type=text]{padding:8px;border:1px solid #cbd5e1;border-radius:6px;min-width:260px}.agent label{margin-left:10px}.save-feedback{margin-left:8px;font-weight:600}.screen{position:relative;display:inline-block;max-width:100%;line-height:0}.screen img{display:block;max-width:100%;max-height:80vh;border:1px solid #d1d5db}.box{position:absolute;border:3px solid #f59e0b;background:#f59e0b22;pointer-events:none;display:none}.muted{color:#6b7280}.element-search{width:min(420px,100%);padding:8px;border:1px solid #cbd5e1;border-radius:6px;font:14px system-ui,"Microsoft YaHei",sans-serif}.search-count{margin-left:8px;color:#64748b}.scroll{max-height:46vh;overflow:auto;border:1px solid #e5e7eb}table{border-collapse:collapse;width:100%}th,td{padding:7px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}tr{cursor:pointer}tr:hover,tr.selected{background:#eff6ff}.small{font-size:12px;word-break:break-all}#message{min-height:20px;color:#b91c1c}.locator{margin:12px 0;padding:10px;background:#0f172a;border-radius:6px;color:#e2e8f0;white-space:pre-wrap;word-break:break-all;font:12px ui-monospace,Consolas,monospace}.script-editor{min-height:280px!important;resize:vertical}.script-editor:not([readonly]){outline:2px solid #60a5fa;background:#111c35}@media(max-width:900px){.grid{grid-template-columns:1fr}.agent{grid-column:auto}}
</style><body>
<header><strong>Android 设备检查器</strong><span id="device">尚未连接</span><span id="refreshState">自动刷新：开启（1 秒）</span><button onclick="refresh(true)" id="refreshButton">立即刷新</button><span id="refreshFeedback" class="feedback" role="status"></span><button onclick="toggleAutoRefresh()" id="autoButton">暂停自动刷新</button></header>
<main class="grid"><section class="card agent"><h2>自然语言测试 Demo</h2><p class="muted">流程：分析当前 UI → 执行验证 → 确认文件名并写入 test_script。密码、重置等需求必须勾选确认，系统不会猜测或保存密码、验证码。</p><textarea id="requirement" placeholder="输入测试需求，例如：点击 Trade Password 按钮，然后重置密码"></textarea><button onclick="analyseRequirement()">分析当前 UI</button><button id="executeButton" onclick="executeRequirement()">执行已分析步骤</button><button id="copyAgentButton" onclick="copyAgentScript()" disabled>复制生成测试脚本</button><button id="editAgentButton" onclick="toggleAgentScriptEditor()" disabled>编辑脚本</button><span id="executeFeedback" class="save-feedback"></span><label><input id="confirmSensitive" type="checkbox"> 我确认本次高风险测试在授权测试环境中执行</label><p><input id="testCaseName" type="text" placeholder="确认测试用例文件名，如 reset_trade_password"><button id="saveTestCase" onclick="saveTestCase()" disabled>确认写入 test_script</button><span id="saveFeedback" class="save-feedback"></span></p><textarea id="agentPlan" class="locator script-editor" readonly spellcheck="false">尚未分析需求</textarea></section>
<section class="card"><h2>当前手机界面</h2><p class="muted">点击右侧元素行可在截图中高亮其范围，用于确认定位是否正确。</p><div class="screen"><img id="screen" alt="手机截图"><i id="box" class="box"></i></div><div id="message"></div></section>
<section class="card"><h2>UI 元素 <span id="count"></span></h2><p class="muted">选择元素后，下方会生成可复制到自动化测试脚本的定位示例。</p><p><input id="nodeSearch" class="element-search" type="search" placeholder="搜索文字、内容描述或资源 ID" oninput="applyNodeSearch()"><button onclick="clearNodeSearch()">清除</button><span id="searchCount" class="search-count"></span></p><button id="copyLocatorButton" onclick="copyLocatorScript()" disabled>复制定位脚本</button><span id="locatorFeedback" class="save-feedback"></span><pre id="locator" class="locator">请选择一个元素</pre><div class="scroll"><table><thead><tr><th>#</th><th>文字 / 描述</th><th>资源 ID / 类</th><th>坐标</th></tr></thead><tbody id="nodes"></tbody></table></div></section></main>
<script>
let state={nodes:[],screen_size:[0,0]},selected=null,selectedNode=null,refreshing=false,autoRefresh=true,executing=false,agentScript='',scriptEditing=false;
const msg=t=>document.querySelector('#message').textContent=t||'';
async function api(url,body){const r=await fetch(url,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'操作失败');return d}
async function copyToClipboard(text,feedbackId){const status=document.querySelector('#'+feedbackId);if(!text){status.textContent='没有可复制的脚本。';return}try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text)}else{const area=document.createElement('textarea');area.value=text;document.body.appendChild(area);area.select();document.execCommand('copy');area.remove()}status.textContent='已复制到剪贴板。'}catch(e){status.textContent='复制失败，请手动复制。'}}
function setAgentPlan(text){document.querySelector('#agentPlan').value=text}
function editedAgentScript(){const text=document.querySelector('#agentPlan').value,marker='# 下次可执行脚本',start=text.indexOf(marker);if(start<0)return agentScript;let script=text.slice(start+marker.length).trim();const resultStart=script.indexOf('\n# 执行结果');if(resultStart>=0)script=script.slice(0,resultStart).trim();return script||agentScript}
function finishScriptEditing(){scriptEditing=false;const editor=document.querySelector('#agentPlan');editor.readOnly=true;agentScript=editedAgentScript();document.querySelector('#editAgentButton').textContent='编辑脚本'}
function toggleAgentScriptEditor(){const editor=document.querySelector('#agentPlan');if(!scriptEditing){scriptEditing=true;editor.readOnly=false;document.querySelector('#editAgentButton').textContent='完成编辑';document.querySelector('#executeFeedback').textContent='正在编辑脚本；编辑完成后可复制或保存。';editor.focus()}else{finishScriptEditing();document.querySelector('#executeFeedback').textContent='脚本编辑完成，可复制或保存。'}}
function copyAgentScript(){copyToClipboard(editedAgentScript(),'executeFeedback')}
function copyLocatorScript(){copyToClipboard(document.querySelector('#locator').textContent==='请选择一个元素'?'':document.querySelector('#locator').textContent,'locatorFeedback')}
function feedback(text,kind){const item=document.querySelector('#refreshFeedback');item.textContent=text;item.className='feedback '+kind}
function timeText(){return new Date().toLocaleTimeString('zh-CN',{hour12:false})}
function requirement(){return document.querySelector('#requirement').value.trim()}
function formatPlan(plan){const steps=plan.steps.map(step=>'第 '+step.order+' 步：'+step.target+'（'+step.status+'）');const locator=plan.locator?'当前定位：'+plan.locator.kind+' = '+plan.locator.value:'当前定位：滑动操作，无需初始可见元素';return ['# 分析结果',...steps,locator,plan.risk_confirmation_required?'风险：需要勾选授权确认后才能执行':'风险：低风险导航操作，可执行','', '# 下次可执行脚本',plan.generated_script].join('\n')}
async function analyseRequirement(){try{const data=await api('/api/agent/plan',{request:requirement()});if(scriptEditing)finishScriptEditing();agentScript=data.plan.generated_script;document.querySelector('#copyAgentButton').disabled=false;document.querySelector('#editAgentButton').disabled=false;setAgentPlan(formatPlan(data.plan));document.querySelector('#saveTestCase').disabled=true;document.querySelector('#saveFeedback').textContent='请执行并人工确认后再保存。'}catch(e){agentScript='';document.querySelector('#copyAgentButton').disabled=true;document.querySelector('#editAgentButton').disabled=true;setAgentPlan('分析失败：'+e.message)}}
async function executeRequirement(){const button=document.querySelector('#executeButton'),status=document.querySelector('#executeFeedback'),resumeAutoRefresh=autoRefresh;if(executing)return;if(scriptEditing)finishScriptEditing();const script=editedAgentScript();executing=true;autoRefresh=false;document.querySelector('#refreshState').textContent='自动刷新：执行中已暂停';button.disabled=true;button.textContent='正在执行…';status.textContent='正在执行编辑后的测试脚本，请勿重复点击。';try{const data=await api('/api/agent/execute',{request:requirement(),script:script,confirm_sensitive:document.querySelector('#confirmSensitive').checked});agentScript=data.generated_script;document.querySelector('#copyAgentButton').disabled=false;document.querySelector('#editAgentButton').disabled=false;const result=data.steps.map(step=>'第 '+step.order+' 步已执行 '+step.target+'，页面变化：'+(step.page_changed?'是':'否')).join('\n');setAgentPlan(formatPlan(data.plan)+'\n\n# 执行结果\n'+result+'\n任一步页面变化：'+(data.page_changed?'是':'否（请查看截图或等待页面加载）'));document.querySelector('#saveTestCase').disabled=false;document.querySelector('#saveFeedback').textContent='请确认手机执行结果无误，再输入文件名保存。';status.textContent='执行完成，可以再次执行。';await refresh()}catch(e){status.textContent='执行失败：'+e.message}finally{executing=false;autoRefresh=resumeAutoRefresh;document.querySelector('#refreshState').textContent='自动刷新：'+(autoRefresh?'开启（1 秒）':'已暂停');button.disabled=false;button.textContent='执行已分析步骤'}}
async function saveTestCase(){try{const data=await api('/api/agent/save-test-case',{filename:document.querySelector('#testCaseName').value,script:editedAgentScript()});document.querySelector('#saveFeedback').textContent='写入成功：'+data.path;document.querySelector('#saveTestCase').disabled=true}catch(e){document.querySelector('#saveFeedback').textContent='无法写入：'+e.message}}
async function refresh(manual=false){const button=document.querySelector('#refreshButton');if(refreshing){if(manual)feedback('刷新正在进行…','pending');return false}refreshing=true;const previous=selectedNode;if(manual){button.disabled=true;button.textContent='刷新中…';feedback('正在读取设备…','pending')}try{msg('正在读取设备…');state=await api('/api/refresh');selected=previous?state.nodes.findIndex(n=>sameElement(n,previous)):null;render();document.querySelector('#screen').src='/api/screenshot?t='+Date.now();msg('已刷新。');if(manual)feedback('刷新成功 · '+timeText(),'success');return true}catch(e){msg(e.message);if(manual)feedback('刷新失败 · '+e.message,'error');return false}finally{refreshing=false;if(manual){button.disabled=false;button.textContent='立即刷新'}}}
function sameElement(a,b){if(!a||!b)return false;if(a.resource_id&&a.resource_id===b.resource_id)return true;if(a.content_desc&&a.content_desc===b.content_desc&&a.class===b.class)return true;return Boolean(a.text&&a.text===b.text&&a.class===b.class)}
function nodeSearchQuery(){return document.querySelector('#nodeSearch').value.trim().toLocaleLowerCase()}
function filteredNodes(){const query=nodeSearchQuery();if(!query)return state.nodes;return state.nodes.filter(n=>[n.text,n.content_desc,n.resource_id].some(value=>(value||'').toLocaleLowerCase().includes(query)))}
function applyNodeSearch(){render()}
function clearNodeSearch(){document.querySelector('#nodeSearch').value='';render()}
function render(){document.querySelector('#device').textContent='设备：'+state.serial+'；屏幕 '+state.screen_size.join(' × ');const visibleNodes=filteredNodes(),query=nodeSearchQuery();document.querySelector('#count').textContent=query?'('+visibleNodes.length+'/'+state.node_count+')':'('+state.node_count+')';document.querySelector('#searchCount').textContent=query?'匹配 '+visibleNodes.length+' 个元素':'';const rows=visibleNodes.map(n=>`<tr data-i="${n.index}" onclick="selectNode(${n.index})"><td>${n.index}</td><td>${escapeHtml(n.text||n.content_desc||'—')} ${n.clickable?'[可点击]':''}</td><td class="small">${escapeHtml(n.resource_id||'—')}<br>${escapeHtml(n.class||'')}</td><td>${escapeHtml(n.bounds||'—')}</td></tr>`).join('');document.querySelector('#nodes').innerHTML=rows;if(!selectedNode){document.querySelector('#locator').textContent='请选择一个元素';document.querySelector('#copyLocatorButton').disabled=true;hideBox()}else if(selected===null){document.querySelector('#locator').textContent=locatorExample(selectedNode)+'\n\n# 当前刷新中未找到该元素；已保留以上定位示例和高亮范围。';document.querySelector('#copyLocatorButton').disabled=false;drawHighlight(selectedNode)}else highlightSelected()}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function selectNode(index){const node=state.nodes.find(item=>item.index===Number(index));if(!node)return;selected=state.nodes.indexOf(node);selectedNode=node;highlightSelected()}
function highlightSelected(){const n=state.nodes[selected];if(!n){selected=null;drawHighlight(selectedNode);return}document.querySelectorAll('tr[data-i]').forEach(row=>row.classList.toggle('selected',Number(row.dataset.i)===n.index));document.querySelector('#locator').textContent=locatorExample(n);document.querySelector('#copyLocatorButton').disabled=false;drawHighlight(n)}
function drawHighlight(n){const m=/\[(\d+),(\d+)\]\[(\d+),(\d+)\]/.exec((n||{}).bounds||'');if(!m)return hideBox();const img=document.querySelector('#screen'),box=document.querySelector('#box'),scale=img.clientWidth/state.screen_size[0];if(!scale)return;box.style.display='block';box.style.left=(m[1]*scale)+'px';box.style.top=(m[2]*scale)+'px';box.style.width=((m[3]-m[1])*scale)+'px';box.style.height=((m[4]-m[2])*scale)+'px'}
function locatorExample(n){const q=v=>JSON.stringify(v);let find=n.resource_id?`tree.find_by_resource_id(${q(n.resource_id)})`:n.text?`tree.find_by_text(${q(n.text)})`:n.content_desc?`next((item for item in tree.nodes if item.content_desc == ${q(n.content_desc)}), None)`:'None  # 当前元素没有稳定的文字、resource-id 或描述';let out='# 获取元素\ntree = UiTree.capture(client)\nnode = '+find+'\n\n';if(n.center)out+=`# 使用此元素执行脚本操作\nUiTree.click(client, node)  # 中心坐标 ${n.center[0]}, ${n.center[1]}\n# client.input_text("文本")\n# client.swipe(x1, y1, x2, y2, duration_ms=300)`;return out}
function hideBox(){document.querySelector('#box').style.display='none'}
function toggleAutoRefresh(){autoRefresh=!autoRefresh;document.querySelector('#refreshState').textContent='自动刷新：'+(autoRefresh?'开启（1 秒）':'已暂停');document.querySelector('#autoButton').textContent=autoRefresh?'暂停自动刷新':'开启自动刷新'}
setInterval(()=>{if(autoRefresh&&!executing)refresh()},1000);
refresh();
</script></body></html>'''
