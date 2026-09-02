"""UI elements on the Settings page.

Keep new Settings locators here as the page evolves; test cases must not add
raw text/resource-id/content-desc values inline.
"""

from .base import BasePage, PageElement


class SettingsPage(BasePage):
    # Existing product flow uses this entry.  It is not asserted by the
    # current navigation-only test, but is managed here for later cases.
    TRADE_PASSWORD = PageElement("Trade Password", text="Trade Password")
    MY_PROFILE = PageElement("My Profile", text="My Profile")
