import base64
import hashlib
import hmac
import json

import pytest

from mobile_automation.feishu import (
    FeishuError,
    build_feishu_signature,
    load_feishu_config,
    send_feishu_text,
)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


def write_config(path, webhook="https://example.test/hook", secret="private"):
    path.write_text(
        json.dumps({"webhook": webhook, "secret": secret}), encoding="utf-8"
    )


def test_load_feishu_config(tmp_path):
    path = tmp_path / "config.json"
    write_config(path)

    config = load_feishu_config(path)

    assert config.webhook == "https://example.test/hook"
    assert config.secret == "private"


def test_missing_secret_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"webhook": "https://example.test/hook"}))

    with pytest.raises(FeishuError, match="secret"):
        load_feishu_config(path)


def test_signature_matches_feishu_algorithm():
    timestamp = "1700000000"
    expected = base64.b64encode(
        hmac.new(
            (timestamp + "\nprivate").encode("utf-8"),
            b"",
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    assert build_feishu_signature(timestamp, "private") == expected


def test_send_feishu_text_posts_signed_payload(tmp_path):
    path = tmp_path / "config.json"
    write_config(path)
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(b'{"code": 0, "msg": "success"}')

    result = send_feishu_text(
        "测试通知", config_path=path, timestamp=1700000000, opener=opener
    )
    payload = json.loads(captured["request"].data.decode("utf-8"))

    assert result["code"] == 0
    assert captured["timeout"] == 10.0
    assert payload["timestamp"] == "1700000000"
    assert payload["msg_type"] == "text"
    assert payload["content"] == {"text": "测试通知"}
    assert payload["sign"] == build_feishu_signature("1700000000", "private")
