"""Utilities for capturing, inspecting, and interacting with Android UI trees."""

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from .adb import AdbClient
from .reporting import record_device_step


BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def parse(cls, value: str) -> "Bounds":
        match = BOUNDS_PATTERN.fullmatch(value)
        if not match:
            raise ValueError("无法解析控件 bounds：{}".format(value))
        return cls(*(int(part) for part in match.groups()))

    @property
    def center(self) -> Tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True)
class UiNode:
    element: ET.Element

    @property
    def text(self) -> str:
        return self.element.attrib.get("text", "")

    @property
    def resource_id(self) -> str:
        return self.element.attrib.get("resource-id", "")

    @property
    def content_desc(self) -> str:
        return self.element.attrib.get("content-desc", "")

    @property
    def class_name(self) -> str:
        return self.element.attrib.get("class", "")

    @property
    def clickable(self) -> bool:
        return self.element.attrib.get("clickable") == "true"

    @property
    def enabled(self) -> bool:
        return self.element.attrib.get("enabled", "true") == "true"

    @property
    def visible_to_user(self) -> bool:
        """Whether UIAutomator explicitly marks this node as visible."""
        return self.element.attrib.get("visible-to-user", "true") != "false"

    @property
    def bounds(self) -> Optional[Bounds]:
        value = self.element.attrib.get("bounds", "")
        if not value:
            return None
        try:
            return Bounds.parse(value)
        except ValueError:
            return None

    def describe(self) -> Dict[str, object]:
        bounds = self.bounds
        return {
            "text": self.text,
            "resource_id": self.resource_id,
            "content_desc": self.content_desc,
            "class": self.class_name,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "visible_to_user": self.visible_to_user,
            "bounds": self.element.attrib.get("bounds", ""),
            "center": list(bounds.center) if bounds else None,
        }


class UiTree:
    def __init__(self, xml_text: str) -> None:
        self.xml_text = xml_text
        self.root = ET.fromstring(xml_text)
        self.nodes = [UiNode(element) for element in self.root.iter("node")]

    @classmethod
    def capture(
        cls, client: AdbClient, save_to: Optional[Path] = None
    ) -> "UiTree":
        xml_text = client.dump_ui()
        if save_to is not None:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_text(xml_text, encoding="utf-8")
        return cls(xml_text)

    def find_by_resource_id(self, resource_id: str) -> Optional[UiNode]:
        return next((node for node in self.nodes if node.resource_id == resource_id), None)

    def find_by_text(self, text: str) -> Optional[UiNode]:
        return next((node for node in self.nodes if node.text == text), None)

    def find_xpath(self, xpath: str) -> Optional[UiNode]:
        # ElementTree uses `.//` for a descendant search while UIAutomator XPath
        # is commonly written as `//`. This accepts both forms.
        normalized = ".{}".format(xpath) if xpath.startswith("//") else xpath
        element = self.root.find(normalized)
        return UiNode(element) if element is not None else None

    @property
    def screen_size(self) -> Tuple[int, int]:
        bounds = [node.bounds for node in self.nodes if node.bounds is not None]
        if not bounds:
            return 0, 0
        return max(item.right for item in bounds), max(item.bottom for item in bounds)

    def nodes_at(self, x: int, y: int, clickable_only: bool = False) -> List[UiNode]:
        matches = [
            node
            for node in self.nodes
            if node.bounds is not None
            and node.bounds.contains(x, y)
            and (node.clickable or not clickable_only)
        ]
        return sorted(matches, key=lambda node: node.bounds.area)

    def center_candidates(
        self, clickable_only: bool = True, limit: int = 10
    ) -> List[UiNode]:
        width, height = self.screen_size
        center_x, center_y = width / 2.0, height / 2.0
        candidates = [
            node
            for node in self.nodes
            if node.bounds is not None and (node.clickable or not clickable_only)
        ]
        candidates.sort(
            key=lambda node: math.hypot(
                node.bounds.center[0] - center_x,
                node.bounds.center[1] - center_y,
            )
        )
        return candidates[:limit]

    @staticmethod
    def click(client: AdbClient, node: Optional[UiNode]) -> Tuple[int, int]:
        if node is None:
            raise ValueError("目标节点为空：请先确认元素已成功定位，再执行点击")
        if not node.enabled:
            raise ValueError("目标节点当前不可用")
        if node.bounds is None:
            raise ValueError("目标节点没有可点击坐标")
        x, y = node.bounds.center
        client.tap(x, y)
        label = node.text or node.content_desc or node.resource_id or node.class_name
        record_device_step(client, "点击元素", "{}，坐标 ({}, {})".format(label, x, y))
        return x, y
