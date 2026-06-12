from __future__ import annotations

import httpx
import pytest

from caesar_search import (
    APIStatusError,
    APITimeoutError,
    AsyncCaesar,
    AuthenticationError,
    Caesar,
    RateLimitError,
)

from .conftest import (
    SAMPLE_DOCUMENT_RESPONSE,
    SAMPLE_FEEDBACK_RESPONSE,
    SAMPLE_SEARCH_RESPONSE,
    RecordingTransport,
)


def make_client(transport: RecordingTransport, **kwargs: object) -> Caesar:
    kwargs.setdefault("api_key", "test-key")
    return Caesar(http_client=httpx.Client(transport=transport), **kwargs)  # type: ignore[arg-type]


def test_search_returns_typed_response_with_provenance(search_transport: RecordingTransport) -> None:
    client = make_client(search_transport)
    response = client.search("test query", max_results=5, verbosity="compact", max_chars_total=4000)

    assert response.search_id == SAMPLE_SEARCH_RESPONSE["search_id"]
    assert response.results is not None
    result = response.results[0]
    assert result.doc_id == "44444444-4444-4444-8444-444444444444"
    assert result.canonical_url == "https://example.com/one"
    assert result.source_url == "https://example.com/one?utm=x"

    body = search_transport.body()
    assert body["query"] == "test query"
    assert body["max_results"] == 5
    assert body["response"] == {"verbosity": "compact", "budget": {"max_chars_total": 4000}}
    assert body["client_model"] == "python-sdk"


def test_headers_carry_auth_and_attribution(search_transport: RecordingTransport) -> None:
    client = make_client(search_transport)
    client.search("q")
    request = search_transport.requests[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["X-Caesar-Client"].startswith("python-sdk/")


def test_api_key_argument_beats_environment(
    search_transport: RecordingTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAESAR_API_KEY", "env-key")
    client = make_client(search_transport, api_key="arg-key")
    client.search("q")
    assert search_transport.requests[0].headers["Authorization"] == "Bearer arg-key"


def test_read_maps_doc_id_url_and_range() -> None:
    transport = RecordingTransport(lambda _r, _i: httpx.Response(200, json=SAMPLE_DOCUMENT_RESPONSE))
    client = make_client(transport)

    client.read("44444444-4444-4444-8444-444444444444", max_chars=500)
    client.read("https://example.com/page", query="what is it")
    client.read(doc_id="44444444-4444-4444-8444-444444444444", start_char=100)

    by_doc = transport.body(0)
    assert by_doc["doc_id"] == "44444444-4444-4444-8444-444444444444"
    assert by_doc["content"]["selection"] == "full_document"
    assert by_doc["content"]["max_chars"] == 500

    by_url = transport.body(1)
    assert by_url["canonical_url"] == "https://example.com/page"
    assert by_url["query"] == "what is it"
    assert by_url["content"]["selection"] == "query_relevant"

    by_range = transport.body(2)
    assert by_range["content"]["range"] == {"start_char": 100}
    assert by_range["content"]["selection"] == "full_document"


def test_read_requires_a_target() -> None:
    transport = RecordingTransport(lambda _r, _i: httpx.Response(200, json=SAMPLE_DOCUMENT_RESPONSE))
    client = make_client(transport)
    with pytest.raises(ValueError):
        client.read()


def test_feedback_maps_fields() -> None:
    transport = RecordingTransport(lambda _r, _i: httpx.Response(200, json=SAMPLE_FEEDBACK_RESPONSE))
    client = make_client(transport)
    response = client.feedback("result_helpful", search_id="s1", doc_id="d1", rank=2)
    assert response.accepted is True
    body = transport.body()
    assert body["event_type"] == "result_helpful"
    assert body["search_id"] == "s1"
    assert body["rank"] == 2
    assert body["agent_context"] == {"client_model": "python-sdk"}


def test_retries_429_honoring_retry_after_then_succeeds() -> None:
    def handler(_request: httpx.Request, index: int) -> httpx.Response:
        if index == 0:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "rate_limited"}})
        return httpx.Response(200, json=SAMPLE_SEARCH_RESPONSE)

    transport = RecordingTransport(handler)
    client = make_client(transport)
    response = client.search("q")
    assert response.search_id
    assert len(transport.requests) == 2


def test_retry_exhaustion_raises_rate_limit_error() -> None:
    transport = RecordingTransport(
        lambda _r, _i: httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"error": {"code": "rate_limited", "message": "slow down"}},
        )
    )
    client = make_client(transport, max_retries=1)
    with pytest.raises(RateLimitError) as excinfo:
        client.search("q")
    assert excinfo.value.code == "rate_limited"
    assert len(transport.requests) == 2


def test_max_retries_zero_disables_retries() -> None:
    transport = RecordingTransport(
        lambda _r, _i: httpx.Response(500, json={"error": {"code": "internal_error"}})
    )
    client = make_client(transport, max_retries=0)
    with pytest.raises(APIStatusError):
        client.search("q")
    assert len(transport.requests) == 1


def test_auth_error_maps_to_authentication_error() -> None:
    transport = RecordingTransport(
        lambda _r, _i: httpx.Response(
            401,
            json={"request_id": "req-1", "error": {"code": "missing_api_key", "message": "missing API key"}},
        )
    )
    client = make_client(transport)
    with pytest.raises(AuthenticationError) as excinfo:
        client.search("q")
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "missing_api_key"
    assert excinfo.value.request_id == "req-1"


def test_timeout_maps_to_api_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    client = Caesar(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(APITimeoutError):
        client.search("q")


def test_with_raw_response_returns_httpx_response(search_transport: RecordingTransport) -> None:
    client = make_client(search_transport)
    raw = client.with_raw_response.search("q", verbosity="ids_only")
    assert isinstance(raw, httpx.Response)
    assert raw.status_code == 200
    assert search_transport.body()["response"] == {"verbosity": "ids_only"}


def test_context_manager_closes() -> None:
    transport = RecordingTransport(lambda _r, _i: httpx.Response(200, json=SAMPLE_SEARCH_RESPONSE))
    with make_client(transport) as client:
        client.search("q")


async def test_async_client_mirrors_sync() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=SAMPLE_SEARCH_RESPONSE)

    async with AsyncCaesar(
        api_key="test-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ) as client:
        response = await client.search("async query", max_results=3)
    assert response.results is not None
    assert response.results[0].doc_id == "44444444-4444-4444-8444-444444444444"
    assert calls[0].headers["X-Caesar-Client"].startswith("python-sdk/")


async def test_async_retries() -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json=SAMPLE_SEARCH_RESPONSE)

    async with AsyncCaesar(
        api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ) as client:
        await client.search("q")
    assert count == 2
