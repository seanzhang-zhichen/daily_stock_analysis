from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.notification_sender.dingtalk_sender import DingtalkSender


def test_dingtalk_sends_byte_safe_markdown_chunks():
    sender = DingtalkSender(SimpleNamespace(
        dingtalk_webhook_url="https://oapi.dingtalk.com/robot/send?access_token=test",
        dingtalk_secret=None,
    ))
    response = Mock()
    response.json.return_value = {"errcode": 0}
    with patch("src.notification_sender.dingtalk_sender.requests.post", return_value=response) as post:
        assert sender.send_to_dingtalk("中" * 8000, title="A股")
    assert post.call_count == 2
    for call in post.call_args_list:
        assert call.kwargs["json"]["msgtype"] == "markdown"


def test_dingtalk_signs_webhook_when_secret_is_configured():
    sender = DingtalkSender(SimpleNamespace(
        dingtalk_webhook_url="https://oapi.dingtalk.com/robot/send?access_token=test",
        dingtalk_secret="SECexample",
    ))
    with patch("src.notification_sender.dingtalk_sender.time.time", return_value=1.0):
        signed = sender._signed_url()
    assert "timestamp=1000" in signed
    assert "sign=" in signed
    assert "SECexample" not in signed
