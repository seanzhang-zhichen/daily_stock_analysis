from unittest.mock import Mock, patch

from bot.commands.analyze import AnalyzeCommand


def test_bot_accepts_registered_index_and_forwards_structured_target():
    service = Mock()
    service.submit_analysis.return_value = {"success": True, "task_id": "task-index"}

    with patch("src.services.task_service.get_task_service", return_value=service):
        response = AnalyzeCommand().execute(Mock(), ["上证50"])

    assert "sh000016" in response.text
    kwargs = service.submit_analysis.call_args.kwargs
    assert kwargs["code"] == "sh000016"
    assert kwargs["analysis_target"].asset_type == "index"


def test_bot_accepts_csi_index_alias():
    service = Mock()
    service.submit_analysis.return_value = {"success": True, "task_id": "task-index"}

    with patch("src.services.task_service.get_task_service", return_value=service):
        AnalyzeCommand().execute(Mock(), ["930955.CSI"])

    kwargs = service.submit_analysis.call_args.kwargs
    assert kwargs["code"] == "csi930955"
    assert kwargs["analysis_target"].asset_type == "index"


def test_bot_stock_path_does_not_forward_structured_target():
    service = Mock()
    service.submit_analysis.return_value = {"success": True, "task_id": "task-stock"}

    with patch("src.services.task_service.get_task_service", return_value=service):
        AnalyzeCommand().execute(Mock(), ["AAPL"])

    kwargs = service.submit_analysis.call_args.kwargs
    assert kwargs["code"] == "AAPL"
    assert kwargs["analysis_target"] is None
