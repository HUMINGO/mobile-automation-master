from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mobile_automation import AdbClient
from config.test_settings import (
    ANDROID_DEVICE_SERIAL,
    APP_PACKAGE,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_PAGE_SWIPES,
    SLOW_PAGE_TIMEOUT_SECONDS,
)
from page_objects import MePage, HomePage, SettingsPage
from utils.android_actions import (
    dismiss_known_popups,
    generate_timestamps,
    restart_app,
    save_screenshot,
)


client = AdbClient(serial=ANDROID_DEVICE_SERIAL)


def test_setting_page():
    restart_app(client, APP_PACKAGE)
    dismiss_known_popups(client)

    # 页面元素与定位条件由 page_objects 管理；用例仅描述业务流程。
    home_page = HomePage(client)
    home_page.click(HomePage.ME_TAB, destination=MePage.TASKS, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    me_page = MePage(client)
    me_page.swipe_to(MePage.SETTINGS, max_swipes=MAX_PAGE_SWIPES)
    me_page.click(MePage.SETTINGS, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    save_screenshot(
        client,
        PROJECT_ROOT / "artifacts" / "test_cases" / f"{generate_timestamps()}.png",
    )


def test_change_profile():
    restart_app(client, APP_PACKAGE)
    dismiss_known_popups(client)

    # 页面元素与定位条件由 page_objects 管理；用例仅描述业务流程。
    home_page = HomePage(client)
    home_page.click(HomePage.ME_TAB, destination=MePage.TASKS, timeout_seconds=SLOW_PAGE_TIMEOUT_SECONDS)

    me_page = MePage(client)
    me_page.swipe_to(MePage.SETTINGS, max_swipes=MAX_PAGE_SWIPES)
    me_page.click(MePage.SETTINGS, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    # 点击 my profile
    setting_page = SettingsPage(client)
    setting_page.click(SettingsPage.MY_PROFILE, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

