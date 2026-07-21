from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx

from ._exceptions import APIConnectionError, APITimeoutError, MissingAPIKeyError, status_error_from_response
from ._version import __version__
from .models import (
    DocumentResponse,
    FeedbackResponse,
    FileDeleteResponse,
    FileIndexResponse,
    FileIndexStatusResponse,
    FileListResponse,
    FilePresignResponse,
    SearchResponse,
)

DEFAULT_BASE_URL = "https://alpha.api.trycaesar.com"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
_BASE_DELAY = 0.5
_MAX_DELAY = 8.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# Stripped from every presigned storage PUT: the URL's signature is the
# authorization, and client credentials must never reach the storage host.
_CREDENTIAL_HEADERS = ("Authorization", "Proxy-Authorization", "Cookie")
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _resolve_key(api_key: str | None) -> str | None:
    return api_key or os.environ.get("CAESAR_API_KEY") or None


def _resolve_base_url(base_url: str | None) -> str:
    return (base_url or os.environ.get("CAESAR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _is_public_base_url(base_url: str) -> bool:
    return base_url.rstrip("/") == DEFAULT_BASE_URL


def _require_key_for_public_api(api_key: str | None, base_url: str) -> None:
    if not api_key and _is_public_base_url(base_url):
        raise MissingAPIKeyError(base_url=base_url)


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
    max_results: int | None,
    session_id: str | None,
    verbosity: str | None,
    max_chars_total: int | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "client_model": "python-sdk"}
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
        "selection": "full_document",
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


@dataclass(frozen=True)
class UploadResult:
    """Outcome of :meth:`Caesar.upload_file` (snake_case, matching the API)."""

    name: str
    """Stored filename, as listed by ``list_files`` / used by ``delete_file``."""

    sync_id: str | None = None
    """Indexing run id (poll with ``file_index_status``); None when ``index=False``."""

    index_state: str | None = None
    """Initial indexing run state; None when ``index=False``."""


def _file_payload(file: str | os.PathLike[str] | bytes, filename: str | None) -> tuple[bytes, str]:
    """Resolve upload input to (bytes, filename). Paths default to their basename."""
    if isinstance(file, bytes):
        if not filename:
            raise ValueError("filename is required when uploading bytes")
        return file, filename
    path = Path(file)
    return path.read_bytes(), filename or path.name


def _presign_body(filename: str, size: int, content_type: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"filename": filename, "size": size}
    if content_type is not None:
        body["content_type"] = content_type
    return body


class Caesar:
    """Synchronous client for the Caesar search API.

    Reads ``CAESAR_API_KEY`` and ``CAESAR_BASE_URL`` from the environment when
    not passed explicitly. The public Caesar API requires an API key; custom
    ``base_url`` deployments decide their own authentication policy.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
        storage_http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = _resolve_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        _require_key_for_public_api(self._api_key, self._base_url)
        self._max_retries = max_retries
        self._timeout = timeout
        self._client = http_client or httpx.Client(timeout=timeout)
        # Presigned storage PUTs never run on ``http_client``: its default
        # headers (Authorization, cookies) must not reach storage. Pass
        # ``storage_http_client`` for transport concerns (proxy, custom CA);
        # credential headers are stripped from PUTs regardless.
        self._storage_client = storage_http_client
        self.with_raw_response = _RawResponses(self)

    # -- public surface -------------------------------------------------

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        session_id: str | None = None,
        verbosity: str | None = None,
        max_chars_total: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> SearchResponse:
        """Search the web. Returns ranked results with provenance handles."""
        body = _search_body(
            query,
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

    def upload_file(
        self,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        index: bool = True,
    ) -> UploadResult:
        """Upload one file to the organization's Files knowledge base.

        Presigns, PUTs the bytes straight to storage (the API key is never
        sent there), then triggers an incremental indexing run so the file
        becomes searchable (``index=False`` to batch uploads and call
        :meth:`index_files` once).
        """
        data, resolved_name = _file_payload(file, filename)
        presigned = self.presign_upload(resolved_name, len(data), content_type=content_type)
        self._put_presigned(str(presigned.url), data, content_type)
        if not index:
            return UploadResult(name=str(presigned.name))
        started = self.index_files(mode="incremental")
        return UploadResult(
            name=str(presigned.name), sync_id=str(started.sync_id), index_state=str(started.state)
        )

    def presign_upload(
        self, filename: str, size: int, *, content_type: str | None = None
    ) -> FilePresignResponse:
        """Create a presigned upload URL. PUT exactly ``size`` bytes to it, no auth header."""
        body = _presign_body(filename, size, content_type)
        return FilePresignResponse.model_validate(self._request("/v1/files/presign", body).json())

    def list_files(self) -> FileListResponse:
        """List the organization's uploaded files."""
        return FileListResponse.model_validate(self._request("/v1/files", None, method="GET").json())

    def delete_file(self, name: str) -> FileDeleteResponse:
        """Delete one uploaded file by name (as returned by :meth:`list_files`)."""
        path = f"/v1/files/{quote(name, safe='')}"
        return FileDeleteResponse.model_validate(self._request(path, None, method="DELETE").json())

    def index_files(self, *, mode: str = "incremental") -> FileIndexResponse:
        """Start an indexing run over uploaded files. Poll with :meth:`file_index_status`."""
        return FileIndexResponse.model_validate(self._request("/v1/files/index", {"mode": mode}).json())

    def file_index_status(self, sync_id: str) -> FileIndexStatusResponse:
        """Progress and outcome of one files indexing run."""
        path = f"/v1/files/index/{quote(sync_id, safe='')}"
        return FileIndexStatusResponse.model_validate(self._request(path, None, method="GET").json())

    # -- plumbing ---------------------------------------------------------

    def _request(self, path: str, body: dict[str, Any] | None, *, method: str = "POST") -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(
                    method,
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

    def _put_presigned(self, url: str, data: bytes, content_type: str | None) -> None:
        """PUT bytes to a presigned storage URL.

        Runs on a dedicated client — never ``self._client`` — so defaults from
        a caller-supplied ``http_client`` (Authorization, cookies) cannot reach
        storage: the URL is pre-authorized by its signature and the API key
        must never be sent there. The body must be exactly the presigned size.
        Transient storage errors retry like :meth:`_request`; resending the
        same presigned bytes is safe.
        """
        headers = {"Content-Type": content_type} if content_type else {}
        if self._storage_client is not None:
            self._put_with(self._storage_client, url, data, headers)
            return
        with httpx.Client(timeout=self._timeout) as storage:
            self._put_with(storage, url, data, headers)

    def _put_with(self, client: httpx.Client, url: str, data: bytes, headers: dict[str, str]) -> None:
        for attempt in range(self._max_retries + 1):
            request = client.build_request("PUT", url, content=data, headers=headers)
            for header in _CREDENTIAL_HEADERS:
                request.headers.pop(header, None)
            try:
                response = client.send(request)
            except httpx.TimeoutException as error:
                raise APITimeoutError(f"upload timed out: {error}") from error
            except httpx.HTTPError as error:
                raise APIConnectionError(f"upload failed: {error}") from error
            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                time.sleep(_retry_delay(attempt, response.headers.get("Retry-After")))
                continue
            if response.is_success:
                return
            raise status_error_from_response(response)

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

    def upload_file(
        self,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        index: bool = True,
    ) -> httpx.Response:
        """Run the full upload flow (presign, PUT, index).

        Returns the final raw API response: the index call, or the presign
        response when ``index=False``. The storage PUT is internal — its
        response is checked, not returned.
        """
        data, resolved_name = _file_payload(file, filename)
        presign_response = self._client._request(
            "/v1/files/presign", _presign_body(resolved_name, len(data), content_type)
        )
        self._client._put_presigned(str(presign_response.json()["url"]), data, content_type)
        if not index:
            return presign_response
        return self._client._request("/v1/files/index", {"mode": "incremental"})

    def presign_upload(self, filename: str, size: int, **kwargs: Any) -> httpx.Response:
        body = _presign_body(filename, size, kwargs.pop("content_type", None))
        return self._client._request("/v1/files/presign", body)

    def list_files(self) -> httpx.Response:
        return self._client._request("/v1/files", None, method="GET")

    def delete_file(self, name: str) -> httpx.Response:
        return self._client._request(f"/v1/files/{quote(name, safe='')}", None, method="DELETE")

    def index_files(self, **kwargs: Any) -> httpx.Response:
        return self._client._request("/v1/files/index", {"mode": kwargs.pop("mode", "incremental")})

    def file_index_status(self, sync_id: str) -> httpx.Response:
        return self._client._request(f"/v1/files/index/{quote(sync_id, safe='')}", None, method="GET")


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
        storage_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = _resolve_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        _require_key_for_public_api(self._api_key, self._base_url)
        self._max_retries = max_retries
        self._timeout = timeout
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        # See Caesar: storage PUTs never run on ``http_client``.
        self._storage_client = storage_http_client

    async def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        session_id: str | None = None,
        verbosity: str | None = None,
        max_chars_total: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> SearchResponse:
        body = _search_body(
            query,
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

    async def upload_file(
        self,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        index: bool = True,
    ) -> UploadResult:
        """Upload one file to the organization's Files knowledge base.

        Presigns, PUTs the bytes straight to storage (the API key is never
        sent there), then triggers an incremental indexing run so the file
        becomes searchable (``index=False`` to batch uploads and call
        :meth:`index_files` once).
        """
        import asyncio

        # Path reads happen in a worker thread so a large or slow file does
        # not block the event loop.
        data, resolved_name = await asyncio.to_thread(_file_payload, file, filename)
        presigned = await self.presign_upload(resolved_name, len(data), content_type=content_type)
        await self._put_presigned(str(presigned.url), data, content_type)
        if not index:
            return UploadResult(name=str(presigned.name))
        started = await self.index_files(mode="incremental")
        return UploadResult(
            name=str(presigned.name), sync_id=str(started.sync_id), index_state=str(started.state)
        )

    async def presign_upload(
        self, filename: str, size: int, *, content_type: str | None = None
    ) -> FilePresignResponse:
        """Create a presigned upload URL. PUT exactly ``size`` bytes to it, no auth header."""
        body = _presign_body(filename, size, content_type)
        return FilePresignResponse.model_validate((await self._request("/v1/files/presign", body)).json())

    async def list_files(self) -> FileListResponse:
        """List the organization's uploaded files."""
        return FileListResponse.model_validate((await self._request("/v1/files", None, method="GET")).json())

    async def delete_file(self, name: str) -> FileDeleteResponse:
        """Delete one uploaded file by name (as returned by :meth:`list_files`)."""
        path = f"/v1/files/{quote(name, safe='')}"
        return FileDeleteResponse.model_validate((await self._request(path, None, method="DELETE")).json())

    async def index_files(self, *, mode: str = "incremental") -> FileIndexResponse:
        """Start an indexing run over uploaded files. Poll with :meth:`file_index_status`."""
        return FileIndexResponse.model_validate(
            (await self._request("/v1/files/index", {"mode": mode})).json()
        )

    async def file_index_status(self, sync_id: str) -> FileIndexStatusResponse:
        """Progress and outcome of one files indexing run."""
        path = f"/v1/files/index/{quote(sync_id, safe='')}"
        return FileIndexStatusResponse.model_validate((await self._request(path, None, method="GET")).json())

    async def _request(
        self, path: str, body: dict[str, Any] | None, *, method: str = "POST"
    ) -> httpx.Response:
        import asyncio

        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
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

    async def _put_presigned(self, url: str, data: bytes, content_type: str | None) -> None:
        """PUT bytes to a presigned storage URL (dedicated client, retried; see Caesar)."""
        headers = {"Content-Type": content_type} if content_type else {}
        if self._storage_client is not None:
            await self._put_with(self._storage_client, url, data, headers)
            return
        async with httpx.AsyncClient(timeout=self._timeout) as storage:
            await self._put_with(storage, url, data, headers)

    async def _put_with(
        self, client: httpx.AsyncClient, url: str, data: bytes, headers: dict[str, str]
    ) -> None:
        import asyncio

        for attempt in range(self._max_retries + 1):
            request = client.build_request("PUT", url, content=data, headers=headers)
            for header in _CREDENTIAL_HEADERS:
                request.headers.pop(header, None)
            try:
                response = await client.send(request)
            except httpx.TimeoutException as error:
                raise APITimeoutError(f"upload timed out: {error}") from error
            except httpx.HTTPError as error:
                raise APIConnectionError(f"upload failed: {error}") from error
            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                await asyncio.sleep(_retry_delay(attempt, response.headers.get("Retry-After")))
                continue
            if response.is_success:
                return
            raise status_error_from_response(response)

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
