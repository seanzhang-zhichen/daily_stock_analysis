from types import SimpleNamespace

from src.services.data_capability_service import DataCapabilityService


def test_public_capabilities_hide_provider_details():
    service = DataCapabilityService(config=SimpleNamespace(), fetcher_manager=SimpleNamespace(_fetchers=[]))
    result = service.get_overview(include_provider_details=False)
    assert result["providers"] == []
    assert any(item["market"] == "us" and item["dataset"] == "financial.snapshot" for item in result["datasets"])


def test_admin_overview_contains_no_credentials():
    config = SimpleNamespace(futu_opend_host="127.0.0.1", longbridge_app_key="secret", finnhub_api_key=None, alphavantage_api_key=None)
    result = DataCapabilityService(config=config, fetcher_manager=SimpleNamespace(_fetchers=[])).get_overview()
    serialized = str(result).lower()
    assert "secret" not in serialized
    assert next(item for item in result["providers"] if item["name"] == "futu")["configured"] is True
