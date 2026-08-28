"""Feishu custom-bot notifications backed by a local Codex config file."""

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import time
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path("~/.codex/feishu/feishu_bot_config.json").expanduser()


class FeishuError(RuntimeError):
    """Raised when Feishu configuration or message delivery fails."""


@dataclass(frozen=True)
class FeishuConfig:
    webhook: str
    secret: str


def load_feishu_config(config_path: Path = DEFAULT_CONFIG_PATH) -> FeishuConfig:
    """Load bot credentials without exposing their values in error messages."""
    path = Path(config_path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeishuError("找不到飞书配置文件：{}".format(path)) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise FeishuError("无法读取飞书配置文件：{}".format(path)) from exc

    if not isinstance(data, dict):
        raise FeishuError("飞书配置必须是 JSON 对象")
    webhook = data.get("webhook")
    secret = data.get("secret")
    if not isinstance(webhook, str) or not webhook.strip():
        raise FeishuError("飞书配置缺少有效的 webhook")
    if not isinstance(secret, str) or not secret.strip():
        raise FeishuError("飞书配置缺少有效的 secret")
    if not webhook.startswith("https://"):
        raise FeishuError("飞书 webhook 必须使用 HTTPS")
    return FeishuConfig(webhook=webhook.strip(), secret=secret.strip())


def build_feishu_signature(timestamp: str, secret: str) -> str:
    """Create the signature required by a signed Feishu custom bot."""
    string_to_sign = "{}\n{}".format(timestamp, secret)
    digest = hmac.new(
        string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def send_feishu_text(
    text: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
    timeout: float = 10.0,
    timestamp: Optional[int] = None,
    opener: Callable = urlopen,
):
    """Send one text message through the configured Feishu custom bot."""
    message = text.strip()
    if not message:
        raise FeishuError("飞书通知内容不能为空")
    config = load_feishu_config(config_path)
    timestamp_text = str(int(time.time() if timestamp is None else timestamp))
    payload = {
        "timestamp": timestamp_text,
        "sign": build_feishu_signature(timestamp_text, config.secret),
        "msg_type": "text",
        "content": {"text": message},
    }
    request = Request(
        config.webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise FeishuError("飞书通知请求失败：HTTP {}".format(exc.code)) from exc
    except (OSError, URLError) as exc:
        raise FeishuError("飞书通知请求失败：网络连接异常") from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FeishuError("飞书返回了无法解析的响应") from exc
    code = result.get("code", result.get("StatusCode", 0))
    if code not in (0, "0", None):
        raise FeishuError("飞书拒绝了通知请求，错误码：{}".format(code))
    return result
