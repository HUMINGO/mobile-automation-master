"""Small, reviewable natural-language planner for Android UI trees.

This module intentionally does not invent credentials or bypass confirmation
screens.  It converts a clearly named target, such as ``点击 Trade Password
按钮``, into one verified click step.  A hosted LLM can later be added before
this deterministic resolver, while keeping the same execution safeguards.
"""

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple

from .ui import UiNode, UiTree


SENSITIVE_WORDS = ("密码", "password", "重置", "reset", "删除", "支付", "transfer")
# “滑动到页面底部” is naturally understood as upward content scrolling even
# when the user omits the direction.  Keep the explicit-upward forms too.
SCROLL_BOTTOM_PATTERN = re.compile(
    r"(?:(?:向上|上)\s*)?(?:滑动|滑|滚动|滚)\s*(?:页面\s*)?(?:到|至|直到)?\s*(?:页面\s*)?(?:最)?(?:底部|bottom)",
    re.IGNORECASE,
)
CLICK_PATTERN = re.compile(
    r"(?:点击|点开|打开|进入)\s*[“\"']?(.*?)[”\"']?(?:按钮|button|控件|入口)?(?=，|,|。|然后|并且|并|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClickPlan:
    request: str
    target: str
    locator_kind: str
    locator_value: str
    description: str
    risk_confirmation_required: bool
    follow_up_targets: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, object]:
        return {
            "request": self.request,
            "target": self.target,
            "locator": {"kind": self.locator_kind, "value": self.locator_value},
            "description": self.description,
            "steps": [
                {"order": 1, "target": self.target, "status": "当前 UI 已定位"}
            ] + [
                {"order": index, "target": target, "status": "将在前一步后重新分析 UI"}
                for index, target in enumerate(self.follow_up_targets, start=2)
            ],
            "risk_confirmation_required": self.risk_confirmation_required,
            "generated_script": render_python(self),
        }


@dataclass(frozen=True)
class ScrollPlan:
    """A scroll-to-bottom operation, optionally followed by a later click."""
    request: str
    target: Optional[str]
    risk_confirmation_required: bool
    resolved_click: Optional[ClickPlan] = None

    def as_dict(self) -> Dict[str, object]:
        steps = [{"order": 1, "target": "向上滑动直到页面底部", "status": "将循环刷新 UI 树直到到底"}]
        if self.target:
            steps.append({
                "order": 2,
                "target": self.target,
                "status": "滑动结束后重新分析 UI 并定位",
            })
        return {
            "request": self.request,
            "target": self.target or "页面底部",
            "locator": None,
            "description": "向上滑动页面直到页面底部",
            "steps": steps,
            "risk_confirmation_required": self.risk_confirmation_required,
            "generated_script": render_scroll_python(self),
        }


def _compact(value: str) -> str:
    return re.sub(r"[\s_\-:：]", "", value).casefold()


def _normalise_target(value: str) -> Optional[str]:
    target = value.strip(" \t“”\"'")
    # Natural instructions commonly name a destination as “Settings 页面” or
    # “Trade Password 界面”.  These are action/context words, not part of the
    # UI label used for matching.
    target = re.sub(r"(?:按钮|button|控件|入口|页面|界面|page|screen)(?:后|之后)?$", "", target,
                    flags=re.IGNORECASE).strip()
    # “进入到 Settings 页面” is a common variant of “进入 Settings 页面”.
    # The leading 到/至 belongs to the navigation verb, not the UI label.
    target = re.sub(r"^(?:到|至)\s*", "", target).strip()
    return target or None


def requested_targets(request: str) -> List[str]:
    """Extract every explicit navigation/click target in input order."""
    targets = [
        target
        for target in (_normalise_target(match.group(1)) for match in CLICK_PATTERN.finditer(request.strip()))
        if target
    ]
    # “点击 Task 按钮，进入 Task 页面后，点击 Join agency” describes one
    # navigation into Task, not two consecutive Task clicks.
    return [target for index, target in enumerate(targets) if not index or target != targets[index - 1]]


def requests_scroll_to_bottom(request: str) -> bool:
    return bool(SCROLL_BOTTOM_PATTERN.search(request))


def requested_target(request: str) -> Optional[str]:
    targets = requested_targets(request)
    return targets[0] if targets else None


def _score(target: str, node: UiNode) -> int:
    wanted = _compact(target)
    fields = (node.resource_id, node.content_desc, node.text)
    best = 0
    for field in fields:
        value = _compact(field)
        if not value:
            continue
        if value == wanted:
            best = max(best, 100)
        elif wanted in value:
            best = max(best, 85)
        elif value in wanted and len(value) >= 3:
            best = max(best, 70)
    return best + (5 if best and node.clickable else 0)


def _locator_for(node: UiNode) -> tuple:
    if node.resource_id:
        return "resource_id", node.resource_id
    if node.text:
        return "text", node.text
    if node.content_desc:
        return "content_desc", node.content_desc
    raise ValueError("匹配到的元素没有可复用定位信息")


def _semantic_key(node: UiNode) -> str:
    """Group a clickable container with its visible text child."""
    return _compact(node.content_desc or node.text or node.resource_id)


def plan_request(request: str, tree: UiTree) -> ClickPlan:
    """Create one unambiguous click plan from the current page's UI tree."""
    targets = requested_targets(request)
    if not targets:
        raise ValueError("暂时只支持明确的点击需求，例如：点击 Trade Password 按钮")
    target = targets[0]
    # UIAutomator commonly exposes the same visual control as a clickable
    # container and a child TextView.  Collapse identical reusable locators
    # before checking whether the user's request is genuinely ambiguous.
    unique = {}
    for node in tree.nodes:
        score = _score(target, node)
        if score <= 0:
            continue
        try:
            locator = _locator_for(node)
        except ValueError:
            continue
        # A Settings container with content-desc=Settings and its nested
        # TextView(text=Settings) are one visible target, despite having
        # different technical locators.  Prefer the higher-scored (usually
        # clickable) node for the resulting script.
        key = _semantic_key(node) or locator
        existing = unique.get(key)
        if existing is None or score > existing[0]:
            unique[key] = (score, node)
    candidates = sorted(unique.values(), key=lambda item: item[0], reverse=True)
    if not candidates or candidates[0][0] < 70:
        raise ValueError("当前 UI 树中未找到目标元素：{}".format(target))
    score, node = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    if second_score >= score - 5:
        raise ValueError("目标元素存在多个相近匹配，请补充更精确的文字或 resource-id")
    locator_kind, locator_value = _locator_for(node)
    label = node.text or node.content_desc or node.resource_id
    return ClickPlan(
        request=request,
        target=target,
        locator_kind=locator_kind,
        locator_value=locator_value,
        description="点击元素：{}".format(label),
        risk_confirmation_required=any(word in request.casefold() for word in SENSITIVE_WORDS),
        follow_up_targets=tuple(targets[1:]),
    )


def plan_scroll_request(request: str) -> ScrollPlan:
    """Plan scrolling even when the next target is off-screen right now."""
    if not requests_scroll_to_bottom(request):
        raise ValueError("需求中未包含“向上滑动直到页面底部”")
    targets = requested_targets(request)
    return ScrollPlan(
        request=request,
        target=targets[0] if targets else None,
        risk_confirmation_required=any(word in request.casefold() for word in SENSITIVE_WORDS),
    )


def find_planned_node(tree: UiTree, plan: ClickPlan) -> Optional[UiNode]:
    if plan.locator_kind == "resource_id":
        return tree.find_by_resource_id(plan.locator_value)
    if plan.locator_kind == "text":
        return tree.find_by_text(plan.locator_value)
    return next((node for node in tree.nodes if node.content_desc == plan.locator_value), None)


def render_python(plan: ClickPlan, serial: str = "YOUR_DEVICE_SERIAL") -> str:
    if plan.locator_kind == "resource_id":
        finder = "tree.find_by_resource_id({!r})".format(plan.locator_value)
    elif plan.locator_kind == "text":
        finder = "tree.find_by_text({!r})".format(plan.locator_value)
    else:
        finder = "next((item for item in tree.nodes if item.content_desc == {!r}), None)".format(plan.locator_value)
    lines = [
        "from mobile_automation import AdbClient, UiTree",
        "",
        "client = AdbClient(serial={!r})".format(serial),
        "tree = UiTree.capture(client)",
        "node = {}".format(finder),
        "if node is None:",
        "    raise RuntimeError('未找到目标元素：{}')".format(plan.target),
        "UiTree.click(client, node)",
    ]
    for index, target in enumerate(plan.follow_up_targets, start=2):
        lines.extend([
            "",
            "# 第 {} 步：进入下一页后重新定位 {}".format(index, target),
            "tree = UiTree.capture(client)",
            "node = tree.find_by_text({!r})".format(target),
            "if node is None:",
            "    raise RuntimeError('未找到目标元素：{}')".format(target),
            "UiTree.click(client, node)",
        ])
    lines.append("# 每一步后均应补充目标页面元素断言。")
    return "\n".join(lines)


def render_scroll_python(plan: ScrollPlan, serial: str = "YOUR_DEVICE_SERIAL") -> str:
    lines = [
        "from mobile_automation import AdbClient, UiTree",
        "",
        "client = AdbClient(serial={!r})".format(serial),
        "",
        "def find_by_label(tree, label):",
        "    wanted = label.replace(' ', '').casefold()",
        "    for item in tree.nodes:",
        "        for value in (item.text, item.content_desc, item.resource_id):",
        "            compact = value.replace(' ', '').casefold()",
        "            if compact and (compact == wanted or wanted in compact):",
        "                return item",
        "    return None",
        "",
        "# 向上滑动，连续两次 UI 树不变时认为已到页面底部。",
        "for _ in range(12):",
        "    before = UiTree.capture(client)",
        "    width, height = before.screen_size",
        "    client.swipe(width // 2, int(height * 0.75), width // 2, int(height * 0.30), 350)",
        "    after = UiTree.capture(client)",
        "    if after.xml_text == before.xml_text:",
        "        break",
    ]
    if plan.resolved_click is not None:
        click_script = render_python(plan.resolved_click, serial).splitlines()
        # Keep a single client creation from the scroll preamble.
        start = click_script.index("tree = UiTree.capture(client)")
        lines.extend(["", "# 滑动后定位并点击目标元素"] + click_script[start:])
    elif plan.target:
        lines.extend([
            "",
            "# 滑动后请重新抓取 UI 树，定位目标元素：{}".format(plan.target),
            "tree = UiTree.capture(client)",
            "node = find_by_label(tree, {!r})".format(plan.target),
            "if node is None:",
            "    raise RuntimeError('滑动到底部后仍未找到目标元素：{}')".format(plan.target),
            "UiTree.click(client, node)",
        ])
    return "\n".join(lines)
