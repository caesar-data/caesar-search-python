from __future__ import annotations

from caesar_search.models import DocumentResponse, FeedbackResponse, SearchResponse

from .conftest import SAMPLE_DOCUMENT_RESPONSE, SAMPLE_FEEDBACK_RESPONSE, SAMPLE_SEARCH_RESPONSE


def test_search_response_golden_round_trip() -> None:
    model = SearchResponse.model_validate(SAMPLE_SEARCH_RESPONSE)
    assert model.request_id == SAMPLE_SEARCH_RESPONSE["request_id"]
    assert model.ranking is not None
    assert model.ranking.ranker_version == "reranked_v1"
    assert model.ranking.score_scope == "response_local"
    assert model.results is not None
    result = model.results[0]
    assert result.rank == 1
    assert result.score is not None and result.score.value == 0.91
    assert result.passages is not None
    assert result.passages[0].passage_id == "55555555-5555-4555-8555-555555555555"
    assert model.usage is not None and model.usage.bytes_returned == 1000

    # Round-trip must preserve provenance fields verbatim.
    dumped = model.model_dump(exclude_none=True)
    assert dumped["results"][0]["doc_id"] == SAMPLE_SEARCH_RESPONSE["results"][0]["doc_id"]
    assert dumped["results"][0]["canonical_url"] == SAMPLE_SEARCH_RESPONSE["results"][0]["canonical_url"]
    assert dumped["results"][0]["source_url"] == SAMPLE_SEARCH_RESPONSE["results"][0]["source_url"]


def test_document_response_golden() -> None:
    model = DocumentResponse.model_validate(SAMPLE_DOCUMENT_RESPONSE)
    assert model.doc.doc_id == "44444444-4444-4444-8444-444444444444"
    assert model.content is not None
    assert model.content.char_count == 21
    assert model.content.truncated is False


def test_feedback_response_golden() -> None:
    model = FeedbackResponse.model_validate(SAMPLE_FEEDBACK_RESPONSE)
    assert model.accepted is True
    assert model.feedback_id == "66666666-6666-4666-8666-666666666666"


def test_unknown_fields_are_tolerated() -> None:
    payload = dict(SAMPLE_SEARCH_RESPONSE)
    payload["future_field"] = {"nested": True}
    model = SearchResponse.model_validate(payload)
    assert model.search_id == SAMPLE_SEARCH_RESPONSE["search_id"]
