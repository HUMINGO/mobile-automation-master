import time
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
    TEST_AGENT_ID,
)
from page_objects import MePage, HomePage, TaskPage
from utils.android_actions import dismiss_known_popups, restart_app


client = AdbClient(serial=ANDROID_DEVICE_SERIAL)


def test_enter_task_page():
    restart_app(client, APP_PACKAGE)
    dismiss_known_popups(client)

    home_page = HomePage(client)
    home_page.click(HomePage.ME_TAB, destination=MePage.TASKS, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    me_page = MePage(client)
    me_page.click(MePage.TASKS, destination=TaskPage.JOIN_AGENCY, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    task_page = TaskPage(client)
    task_page.click(TaskPage.JOIN_AGENCY, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)


def test_input():
    restart_app(client, APP_PACKAGE)
    dismiss_known_popups(client)

    home_page = HomePage(client)
    home_page.click(HomePage.ME_TAB, destination=MePage.TASKS, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    me_page = MePage(client)
    me_page.click(MePage.TASKS, destination=TaskPage.JOIN_AGENCY, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    task_page = TaskPage(client)
    task_page.click(TaskPage.JOIN_AGENCY, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)
    task_page.input_text(TaskPage.JOIN_AGENCY_INPUT, TEST_AGENT_ID)
    task_page.clear_text(TaskPage.JOIN_AGENCY_INPUT)



if __name__ == '__main__':
    test_input()
