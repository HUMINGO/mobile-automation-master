"""Shared page-object primitives for stable Android UI test locators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mobile_automation import AdbClient, UiNode, UiTree
from utils.android_actions import (
    swipe_until_element_visible,
    wait_for_element_visible,
    wait_for_page_ready,
)


@dataclass(frozen=True)
class PageElement:
    """One centrally managed locator on a page.

    Prefer ``content_desc`` or ``resource_id`` when the app exposes either;
    ``text`` is reserved for elements without a stable accessibility label.
    """

    name: str
    text: Optional[str] = None
    resource_id: Optional[str] = None
    content_desc: Optional[str] = None

    def locator_kwargs(self) -> dict:
        if not any((self.text, self.resource_id, self.content_desc)):
            raise ValueError("页面元素 {} 没有配置定位条件".format(self.name))
        return {
            key: value
            for key, value in (
                ("text", self.text),
                ("resource_id", self.resource_id),
                ("content_desc", self.content_desc),
            )
            if value is not None
        }


class BasePage:
    """Base class that keeps test cases free of raw UI locator details."""

    def __init__(self, client: AdbClient) -> None:
        self.client = client

    def wait_for(self, element: PageElement, *, timeout_seconds: float = 12) -> UiNode:
        return wait_for_element_visible(
            self.client, timeout_seconds=timeout_seconds, **element.locator_kwargs()
        )

    def swipe_to(
        self,
        element: PageElement,
        *,
        max_swipes: int = 10,
        direction: str = "up",
    ) -> UiNode:
        return swipe_until_element_visible(
            self.client,
            max_swipes=max_swipes,
            direction=direction,
            **element.locator_kwargs()
        )

    def click(
        self,
        element: PageElement,
        *,
        destination: Optional[PageElement] = None,
        timeout_seconds: float = 8,
    ) -> UiTree:
        """Wait for, click, and verify the page transition in one operation."""
        node = self.wait_for(element, timeout_seconds=timeout_seconds)
        before_navigation = UiTree.capture(self.client)
        UiTree.click(self.client, node)
        destination_kwargs = destination.locator_kwargs() if destination else {}
        return wait_for_page_ready(
            self.client,
            before_navigation,
            timeout_seconds=timeout_seconds,
            **destination_kwargs
        )
