"""UI elements on the Party/home page."""

from .base import BasePage, PageElement


class HomePage(BasePage):
    # Android UI tree verified: android.widget.Button, content-desc="Me".
    ME_TAB = PageElement("底部导航：Me", content_desc="Me")
