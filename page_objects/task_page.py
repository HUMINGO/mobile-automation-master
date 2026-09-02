"""UI elements on the Tasks page."""

from .base import BasePage, PageElement


class TaskPage(BasePage):
    JOIN_AGENCY = PageElement("Join agency 按钮", content_desc="Join agency")
