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
    MAX_PAGE_SWIPES,
    SLOW_PAGE_TIMEOUT_SECONDS,
)
from page_objects import MePage, HomePage, SettingsPage
from utils.android_actions import (
    dismiss_known_popups,
    generate_timestamps,
    restart_app,
    save_screenshot,
    swipe_page,
    screen_contains_text,
)


client = AdbClient(serial=ANDROID_DEVICE_SERIAL)

from page_objects import LoginPage
from utils.android_actions import PageTransitionTimeout


def test_login():
    # Restart the app
    restart_app(client, APP_PACKAGE)
    time.sleep(8)
    # 点击手机号登录按钮
    client.tap(274, 1283)
    loginPage = LoginPage(client)
    loginPage.input_text(LoginPage.phone_num_input, "13686868866")
    try:
        loginPage.click(
            LoginPage.next_button,
            destination=LoginPage.verification_prompt,
            timeout_seconds=20,
        )
    except PageTransitionTimeout:
        # 动态背景可能让 UIAutomator dump 失败；仅在截图 OCR 确认提示可见时继续。
        if not screen_contains_text(client, "Verification code has been sent to"):
            raise
        print("验证码页面已由截图 OCR 确认。")
    # 验证码格子没有 UI 树定位信息：点击第一格后一次输入完整验证码。
    client.tap(125, 885)
    client.input_text("1234")



if __name__ == '__main__':
    test_login()
