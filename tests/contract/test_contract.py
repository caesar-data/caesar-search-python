"""Contract tests replaying search -> read -> feedback against a live deployment.

Gated so unit runs stay hermetic:
  CAESAR_CONTRACT=1 CAESAR_BASE_URL=... [CAESAR_API_KEY=...] uv run pytest tests/contract
"""

from __future__ import annotations

import os

import pytest

from caesar_search import Caesar

pytestmark = pytest.mark.skipif(
    os.environ.get("CAESAR_CONTRACT") != "1",
    reason="contract tests run only with CAESAR_CONTRACT=1",
)


def test_search_read_feedback_flow() -> None:
    with Caesar() as client:
        search = client.search("agent search api", max_results=3)
        assert search.results is not None and len(search.results) > 0
        doc_id = search.results[0].doc_id
        assert doc_id

        document = client.read(doc_id, max_chars=2000)
        assert document.doc.doc_id == doc_id

        feedback = client.feedback(
            "result_helpful",
            search_id=search.search_id,
            doc_id=doc_id,
            query="agent search api",
        )
        assert feedback.accepted is True
