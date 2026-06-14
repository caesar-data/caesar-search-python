from __future__ import annotations

import os
import re
import time
from types import TracebackType
from typing import Any

import httpx

from ._exceptions import APIConnectionError, APITimeoutError, status_error_from_response
from ._version import __version__
from .models import DocumentResponse, FeedbackResponse, SearchResponse

DEFAULT_BASE_URL = "https://alpha.api.trycaesar.com"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
_BASE_DELAY = 0.5
_MAX_DELAY = 8.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _resolve_key(api_key: str | None) -> str | None:
    return api_key or os.environ.get("CAESAR_API_KEY") or None


def _resolve_base_url(base_url: str | None) -> str:
    return (base_url or os.environ.get("CAESAR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Caesar-Client": f"python-sdk/{__version__}",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            seconds = float(retry_after)
            if seconds >= 0:
                return float(min(seconds, _MAX_DELAY))
        except ValueError:
            pass
    return float(min(_BASE_DELAY * (2**attempt), _MAX_DELAY))


def _search_body(
    query: str,
    *,
    mode: str | None,
    max_results: int | None,
    session_id: str | None,
    verbosity: str | None,
    max_chars_total: int | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "client_model": "python-sdk"}
    if mode is not None:
        body["mode"] = mode
    if max_results is not None:
        body["max_results"] = max_results
    if session_id is not None:
        body["session_id"] = session_id
    response_shape: dict[str, Any] = {}
    if verbosity is not None:
        response_shape["verbosity"] = verbosity
    if max_chars_total is not None:
        response_shape["budget"] = {"max_chars_total": max_chars_total}
    if response_shape:
        body["response"] = response_shape
    if extra_body:
        body.update(extra_body)
    return body


def _read_body(
    target: str | None,
    *,
    doc_id: str | None,
    url: str | None,
    query: str | None,
    max_chars: int | None,
    start_char: int | None,
    include: list[str] | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    if target is not None:
        if _UUID_PATTERN.match(target):
            doc_id = doc_id or target
        else:
            url = url or target
    if not doc_id and not url:
        raise ValueError("provide a doc_id or a url")

    content: dict[str, Any] = {
        "selection": "query_relevant" if query else "full_document",
        "format": "markdown",
    }
    if max_chars is not None:
        content["max_chars"] = max_chars
    if start_char:
        # Continuation reads address the raw document text so offsets stay
        # contiguous between calls.
        content["selection"] = "full_document"
        content["range"] = {"start_char": start_char}

    body: dict[str, Any] = {
        "include": include if include is not None else ["metadata", "content"],
        "content": content,
    }
    if doc_id:
        body["doc_id"] = doc_id
    elif url:
        body["canonical_url"] = url
    if query:
        body["query"] = query
    if extra_body:
        body.update(extra_body)
    return body


def _feedback_body(
    event_type: str,
    *,
    search_id: str | None,
    doc_id: str | None,
    passage_id: str | None,
    query: str | None,
    rank: int | None,
    notes: str | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "event_type": event_type,
        "agent_context": {"client_model": "python-sdk"},
    }
    if search_id is not None:
        body["search_id"] = search_id
    if doc_id is not None:
        body["doc_id"] = doc_id
    if passage_id is not None:
        body["passage_id"] = passage_id
    if query is not None:
        body["query"] = query
    if rank is not None:
        body["rank"] = rank
    if notes is not None:
        body["notes"] = notes
    if extra_body:
        body.update(extra_body)
    return body


class Caesar:
    """Synchronous client for the Caesar search API.

    Reads ``CAESAR_API_KEY`` and ``CAESAR_BASE_URL`` from the environment when
    not passed explicitly. Anonymous access works at a lower rate limit when
    the deployment allows it.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = _resolve_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        self._max_retries = max_retries
        self._client = http_client or httpx.Client(timeout=timeout)
        self.with_raw_response = _RawResponses(self)

    # -- public surface -------------------------------------------------

    def search(
        self,
        query: str,
        *,
        mode: str | None = None,
        max_results: int | None = None,
        session_id: str | None = None,
        verbosity: str | None = None,
        max_chars_total: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> SearchResponse:
        """Search the web. Returns ranked results with provenance handles."""
        body = _search_body(
            query,
            mode=mode,
            max_results=max_results,
            session_id=session_id,
            verbosity=verbosity,
            max_chars_total=max_chars_total,
            extra_body=extra_body,
        )
        return SearchResponse.model_validate(self._request("/v1/search", body).json())

    def read(
        self,
        target: str | None = None,
        *,
        doc_id: str | None = None,
        url: str | None = None,
        query: str | None = None,
        max_chars: int | None = None,
        start_char: int | None = None,
        include: list[str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> DocumentResponse:
        """Read a document as clean markdown by doc_id or URL.

        Truncated reads report ``content.start_char``/``char_count``; continue
        with ``start_char=start + count`` instead of retrying bigger.
        """
        body = _read_body(
            target,
            doc_id=doc_id,
            url=url,
            query=query,
            max_chars=max_chars,
            start_char=start_char,
            include=include,
            extra_body=extra_body,
        )
        return DocumentResponse.model_validate(self._request("/v1/document", body).json())

    def feedback(
        self,
        event_type: str,
        *,
        search_id: str | None = None,
        doc_id: str | None = None,
        passage_id: str | None = None,
        query: str | None = None,
        rank: int | None = None,
        notes: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> FeedbackResponse:
        """Send a feedback event about a search result or document."""
        body = _feedback_body(
            event_type,
            search_id=search_id,
            doc_id=doc_id,
            passage_id=passage_id,
            query=query,
            rank=rank,
            notes=notes,
            extra_body=extra_body,
        )
        return FeedbackResponse.model_validate(self._request("/v1/feedback", body).json())

    # -- plumbing ---------------------------------------------------------

    def _request(self, path: str, body: dict[str, Any]) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    f"{self._base_url}{path}",
                    json=body,
                    headers=_headers(self._api_key),
                )
            except httpx.TimeoutException as error:
                raise APITimeoutError(f"request timed out: {error}") from error
            except httpx.HTTPError as error:
                raise APIConnectionError(f"request failed: {error}") from error

            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                time.sleep(_retry_delay(attempt, response.headers.get("Retry-After")))
                last_response = response
                continue
            if response.is_success:
                return response
            raise status_error_from_response(response)

        raise status_error_from_response(last_response)  # type: ignore[arg-type]  # pragma: no cover

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Caesar:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class _RawResponses:
    """Escape hatch: the same methods, returning the raw httpx.Response."""

    def __init__(self, client: Caesar) -> None:
        self._client = client

    def search(self, query: str, **kwargs: Any) -> httpx.Response:
        extra_body = kwargs.pop("extra_body", None)
        body = _search_body(
            query,
            mode=kwargs.pop("mode", None),
            max_results=kwargs.pop("max_results", None),
            session_id=kwargs.pop("session_id", None),
            verbosity=kwargs.pop("verbosity", None),
            max_chars_total=kwargs.pop("max_chars_total", None),
            extra_body=extra_body,
        )
        return self._client._request("/v1/search", body)

    def read(self, target: str | None = None, **kwargs: Any) -> httpx.Response:
        body = _read_body(
            target,
            doc_id=kwargs.pop("doc_id", None),
            url=kwargs.pop("url", None),
            query=kwargs.pop("query", None),
            max_chars=kwargs.pop("max_chars", None),
            start_char=kwargs.pop("start_char", None),
            include=kwargs.pop("include", None),
            extra_body=kwargs.pop("extra_body", None),
        )
        return self._client._request("/v1/document", body)

    def feedback(self, event_type: str, **kwargs: Any) -> httpx.Response:
        body = _feedback_body(
            event_type,
            search_id=kwargs.pop("search_id", None),
            doc_id=kwargs.pop("doc_id", None),
            passage_id=kwargs.pop("passage_id", None),
            query=kwargs.pop("query", None),
            rank=kwargs.pop("rank", None),
            notes=kwargs.pop("notes", None),
            extra_body=kwargs.pop("extra_body", None),
        )
        return self._client._request("/v1/feedback", body)


class AsyncCaesar:
    """Asynchronous client for the Caesar search API. Mirrors :class:`Caesar`."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = _resolve_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        self._max_retries = max_retries
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    async def search(
        self,
        query: str,
        *,
        mode: str | None = None,
        max_results: int | None = None,
        session_id: str | None = None,
        verbosity: str | None = None,
        max_chars_total: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> SearchResponse:
        body = _search_body(
            query,
            mode=mode,
            max_results=max_results,
            session_id=session_id,
            verbosity=verbosity,
            max_chars_total=max_chars_total,
            extra_body=extra_body,
        )
        return SearchResponse.model_validate((await self._request("/v1/search", body)).json())

    async def read(
        self,
        target: str | None = None,
        *,
        doc_id: str | None = None,
        url: str | None = None,
        query: str | None = None,
        max_chars: int | None = None,
        start_char: int | None = None,
        include: list[str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> DocumentResponse:
        body = _read_body(
            target,
            doc_id=doc_id,
            url=url,
            query=query,
            max_chars=max_chars,
            start_char=start_char,
            include=include,
            extra_body=extra_body,
        )
        return DocumentResponse.model_validate((await self._request("/v1/document", body)).json())

    async def feedback(
        self,
        event_type: str,
        *,
        search_id: str | None = None,
        doc_id: str | None = None,
        passage_id: str | None = None,
        query: str | None = None,
        rank: int | None = None,
        notes: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> FeedbackResponse:
        body = _feedback_body(
            event_type,
            search_id=search_id,
            doc_id=doc_id,
            passage_id=passage_id,
            query=query,
            rank=rank,
            notes=notes,
            extra_body=extra_body,
        )
        return FeedbackResponse.model_validate((await self._request("/v1/feedback", body)).json())

    async def _request(self, path: str, body: dict[str, Any]) -> httpx.Response:
        import asyncio

        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}{path}",
                    json=body,
                    headers=_headers(self._api_key),
                )
            except httpx.TimeoutException as error:
                raise APITimeoutError(f"request timed out: {error}") from error
            except httpx.HTTPError as error:
                raise APIConnectionError(f"request failed: {error}") from error

            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                await asyncio.sleep(_retry_delay(attempt, response.headers.get("Retry-After")))
                last_response = response
                continue
            if response.is_success:
                return response
            raise status_error_from_response(response)

        raise status_error_from_response(last_response)  # type: ignore[arg-type]  # pragma: no cover

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncCaesar:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
