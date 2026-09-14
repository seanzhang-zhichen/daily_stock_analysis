from unittest.mock import patch

from src.search_service import SearchResponse, SearXNGSearchProvider, SearchService


def test_searxng_provider_uses_configured_self_hosted_timeout():
    provider = SearXNGSearchProvider(
        ["https://search.example.test"],
        self_hosted_timeout_seconds=17,
    )

    with patch.object(provider, "_do_search") as search_instance:
        search_instance.return_value = SearchResponse(
            query="query",
            results=[],
            provider="SearXNG",
            success=True,
        )
        provider.search("query")

    assert search_instance.call_args.kwargs["timeout"] == 17


def test_search_service_forwards_searxng_timeout():
    service = SearchService(
        searxng_base_urls=["https://search.example.test"],
        searxng_timeout_seconds=23,
    )
    provider = next(item for item in service._providers if item.name == "SearXNG")
    assert provider._self_hosted_timeout_seconds == 23
