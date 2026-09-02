"""UI elements on the Me/profile page."""

from .base import BasePage, PageElement


class MePage(BasePage):
    # These entries belong only to the profile page, even when they require a
    # vertical swipe before becoming visible.
    TASKS = PageElement("Tasks 入口", content_desc="Tasks")
    SETTINGS = PageElement("Settings 入口", content_desc="Settings")
