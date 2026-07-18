from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

SAMPLE_SEARCH_RESPONSE: dict[str, Any] = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "search_id": "22222222-2222-4222-8222-222222222222",
    "session_id": "33333333-3333-4333-8333-333333333333",
    "access": {
        "rate_limit": {"limit_rps": 100, "remaining": 99, "reset_at": "2026-06-12T00:00:00Z"},
    },
    "ranking": {"mode": "standard", "ranker_version": "reranked_v1", "score_scope": "response_local"},
    "results": [
        {
            "rank": 1,
            "doc_id": "44444444-4444-4444-8444-444444444444",
            "canonical_url": "https://example.com/one",
            "source_url": "https://example.com/one?utm=x",
            "title": "Example One",
            "snippet": "First snippet.",
            "score": {"value": 0.91},
            "metadata": {"published_at": "2026-06-01T00:00:00Z", "last_crawled_at": "2026-06-12T00:00:00Z"},
            "passages": [
                {
                    "passage_id": "55555555-5555-4555-8555-555555555555",
                    "doc_id": "44444444-4444-4444-8444-444444444444",
                    "ordinal": 1,
                    "text": "Passage text.",
                }
            ],
        }
    ],
    "usage": {"requests": 1, "bytes_returned": 1000},
}

SAMPLE_DOCUMENT_RESPONSE: dict[str, Any] = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "session_id": "33333333-3333-4333-8333-333333333333",
    "access": {
        "rate_limit": {"limit_rps": 100, "remaining": 98, "reset_at": "2026-06-12T00:00:00Z"},
    },
    "doc": {
        "doc_id": "44444444-4444-4444-8444-444444444444",
        "canonical_url": "https://example.com/one",
        "source_url": "https://example.com/one?utm=x",
        "title": "Example One",
        "first_seen_at": "2026-06-01T00:00:00Z",
        "last_seen_at": "2026-06-12T00:00:00Z",
    },
    "content": {
        "selection": "full_document",
        "format": "markdown",
        "text": "# Example\n\nBody text.",
        "truncated": False,
        "char_count": 21,
    },
    "usage": {"requests": 1, "bytes_returned": 800},
}

SAMPLE_FEEDBACK_RESPONSE: dict[str, Any] = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "feedback_id": "66666666-6666-4666-8666-666666666666",
    "session_id": "33333333-3333-4333-8333-333333333333",
    "access": {
        "rate_limit": {"limit_rps": 100, "remaining": 97, "reset_at": "2026-06-12T00:00:00Z"},
    },
    "accepted": True,
    "usage": {"requests": 1, "bytes_returned": 300},
}


class RecordingTransport(httpx.MockTransport):
    """MockTransport that records every request it serves."""

    def __init__(self, handler: Any) -> None:
        self.requests: list[httpx.Request] = []

        def recording_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return cast(httpx.Response, handler(request, len(self.requests) - 1))

        super().__init__(recording_handler)

    def body(self, index: int = 0) -> dict[str, Any]:
        return cast("dict[str, Any]", json.loads(self.requests[index].content))


@pytest.fixture()
def search_transport() -> RecordingTransport:
    return RecordingTransport(lambda _request, _index: httpx.Response(200, json=SAMPLE_SEARCH_RESPONSE))
