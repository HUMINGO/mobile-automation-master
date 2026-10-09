"""Page objects and centrally managed UI locators for Android test cases."""

from .me_page import MePage
from .home_page import HomePage
from .settings_page import SettingsPage
from .task_page import TaskPage
from .login_page import LoginPage

__all__ = ["MePage", "HomePage", "SettingsPage", "TaskPage", "LoginPage"]
