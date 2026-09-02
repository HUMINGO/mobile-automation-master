"""Shared, editable settings for the Android UI test cases.

Keep device- and environment-specific values here so test cases only describe
their business flow.  Update this file when switching the connected device or
the app under test.
"""

# Android device selected by ADB for the current test environment.
ANDROID_DEVICE_SERIAL = "9XRWMBROZXFIZD45"

# Application package restarted before each independent test case.
APP_PACKAGE = "com.boloup.pro.beta"

# Common timing and interaction settings used by the page-object test cases.
DEFAULT_TIMEOUT_SECONDS = 12
SLOW_PAGE_TIMEOUT_SECONDS = 20
MAX_PAGE_SWIPES = 10

# Non-sensitive test data.  Do not put passwords, verification codes or other
# secrets in this source-controlled file.
TEST_AGENT_ID = "5000103"
