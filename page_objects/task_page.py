"""UI elements on the Tasks page."""

from .base import BasePage, PageElement


class TaskPage(BasePage):
    JOIN_AGENCY = PageElement("Join agency 按钮", content_desc="Join agency")
    # UIAutomator exposes the placeholder as ``text``; its content-desc is
    # empty, so it must not be located as an accessibility description.
    JOIN_AGENCY_INPUT = PageElement("Join agency 输入框", text="Enter Agent ID")
