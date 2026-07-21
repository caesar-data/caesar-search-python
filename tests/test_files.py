from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from caesar_search import APIStatusError, AsyncCaesar, Caesar, UploadResult

from .conftest import RecordingTransport

STORAGE_URL = "https://storage.example/bucket/org/notes.txt"

PRESIGN_RESPONSE: dict[str, Any] = {
    "url": STORAGE_URL,
    "name": "notes.txt",
    "expires_in_seconds": 900,
    "max_object_bytes": 104857600,
}

INDEX_RESPONSE: dict[str, Any] = {"sync_id": "sync-1", "state": "queued"}

STATUS_RESPONSE: dict[str, Any] = {
    "sync_id": "sync-1",
    "state": "completed",
    "stats": {
        "enumerated": 1,
        "fetched": 1,
        "indexed": 1,
        "failed": 0,
        "skipped_unsupported": 0,
        "deleted": 0,
        "bytes": 20,
    },
    "error": None,
    "started_at": None,
    "completed_at": None,
}

LIST_RESPONSE: dict[str, Any] = {
    "files": [{"name": "notes.txt", "size": 20, "last_modified": "2026-01-01T00:00:00Z"}]
}


def files_handler(put_status: int = 200) -> Any:
    def handler(request: httpx.Request, _index: int) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        if request.url.host == "storage.example":
            return httpx.Response(put_status, text="" if put_status == 200 else "denied")
        routes = {
            "POST /v1/files/presign": (200, PRESIGN_RESPONSE),
            "POST /v1/files/index": (202, INDEX_RESPONSE),
            "GET /v1/files": (200, LIST_RESPONSE),
            # request.url.path is decoded; the wire target stays URL-encoded.
            "DELETE /v1/files/My Report.pdf": (200, {"deleted": True}),
            "GET /v1/files/index/sync-1": (200, STATUS_RESPONSE),
        }
        status, body = routes[key]
        return httpx.Response(status, json=body)

    return handler


def make_client(transport: RecordingTransport) -> Caesar:
    # storage_http_client shares the transport so PUTs are recorded in order;
    # the client itself would otherwise create a real (non-mocked) one per PUT.
    return Caesar(
        api_key="test-key",
        http_client=httpx.Client(transport=transport),
        storage_http_client=httpx.Client(transport=transport),
    )


def test_upload_file_presigns_puts_and_indexes() -> None:
    transport = RecordingTransport(files_handler())
    client = make_client(transport)

    result = client.upload_file(b"hello knowledge base", filename="notes.txt", content_type="text/plain")

    assert result == UploadResult(name="notes.txt", sync_id="sync-1", index_state="queued")

    presign = transport.requests[0]
    assert json.loads(presign.content) == {"filename": "notes.txt", "size": 20, "content_type": "text/plain"}
    assert presign.headers["Authorization"] == "Bearer test-key"

    put = transport.requests[1]
    assert put.method == "PUT"
    assert put.url == STORAGE_URL
    assert put.content == b"hello knowledge base"
    assert put.headers["Content-Type"] == "text/plain"
    # The presigned URL is pre-authorized; the API key must never reach storage.
    assert "Authorization" not in put.headers

    index = transport.requests[2]
    assert index.url.path == "/v1/files/index"
    assert json.loads(index.content) == {"mode": "incremental"}


def test_upload_file_reads_paths_and_defaults_filename(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_bytes(b"hello knowledge base")
    transport = RecordingTransport(files_handler())
    client = make_client(transport)

    result = client.upload_file(source)

    assert result.name == "notes.txt"
    assert json.loads(transport.requests[0].content)["filename"] == "notes.txt"


def test_upload_file_index_false_skips_indexing() -> None:
    transport = RecordingTransport(files_handler())
    client = make_client(transport)

    result = client.upload_file(b"hello knowledge base", filename="notes.txt", index=False)

    assert result == UploadResult(name="notes.txt")
    assert len(transport.requests) == 2  # presign + PUT only


def test_upload_file_bytes_require_filename() -> None:
    client = make_client(RecordingTransport(files_handler()))
    with pytest.raises(ValueError, match="filename is required"):
        client.upload_file(b"data")


def test_upload_file_surfaces_failed_put() -> None:
    transport = RecordingTransport(files_handler(put_status=403))
    client = make_client(transport)
    with pytest.raises(APIStatusError):
        client.upload_file(b"data", filename="notes.txt")


def test_list_delete_index_status_routes_and_auth() -> None:
    transport = RecordingTransport(files_handler())
    client = make_client(transport)

    listed = client.list_files()
    assert listed.files is not None and str(listed.files[0].name) == "notes.txt"

    deleted = client.delete_file("My Report.pdf")
    assert deleted.deleted is True
    assert transport.requests[1].url.raw_path.endswith(b"/v1/files/My%20Report.pdf")

    indexed = client.index_files(mode="incremental")
    assert str(indexed.sync_id) == "sync-1"

    status = client.file_index_status("sync-1")
    assert str(status.state) == "completed"
    assert status.stats is not None and status.stats.indexed == 1

    for request in transport.requests:
        assert request.headers["Authorization"] == "Bearer test-key"


def test_with_raw_response_files_methods() -> None:
    transport = RecordingTransport(files_handler())
    client = make_client(transport)
    response = client.with_raw_response.list_files()
    assert isinstance(response, httpx.Response)
    assert response.status_code == 200


def test_upload_file_does_not_inherit_http_client_headers() -> None:
    """Credential defaults on either caller-supplied client must never reach storage."""
    ambient = {"Authorization": "Bearer ambient-credential", "Cookie": "session=1"}
    transport = RecordingTransport(files_handler())
    client = Caesar(
        api_key="test-key",
        http_client=httpx.Client(transport=transport, headers=ambient),
        # Even the dedicated storage client gets credential headers stripped.
        storage_http_client=httpx.Client(transport=transport, headers=ambient),
    )

    client.upload_file(b"hello knowledge base", filename="notes.txt")

    put = next(r for r in transport.requests if r.method == "PUT")
    assert "Authorization" not in put.headers
    assert "Cookie" not in put.headers
    # API calls still authenticate with the SDK key, not the ambient default.
    assert transport.requests[0].headers["Authorization"] == "Bearer test-key"


def test_upload_file_retries_transient_storage_errors() -> None:
    """A 503 from storage retries the PUT — resending presigned bytes is safe."""
    put_attempts = 0

    def handler(request: httpx.Request, _index: int) -> httpx.Response:
        nonlocal put_attempts
        if request.url.host == "storage.example":
            put_attempts += 1
            if put_attempts == 1:
                return httpx.Response(503, text="slow down", headers={"Retry-After": "0"})
            return httpx.Response(200)
        response: httpx.Response = files_handler()(request, _index)
        return response

    transport = RecordingTransport(handler)
    client = make_client(transport)

    result = client.upload_file(b"hello knowledge base", filename="notes.txt")

    assert result.name == "notes.txt"
    assert put_attempts == 2


def test_with_raw_response_upload_file() -> None:
    transport = RecordingTransport(files_handler())
    client = make_client(transport)

    response = client.with_raw_response.upload_file(b"hello knowledge base", filename="notes.txt")
    assert isinstance(response, httpx.Response)
    assert response.status_code == 202  # the index call is the final response

    presign_only = client.with_raw_response.upload_file(
        b"hello knowledge base", filename="notes.txt", index=False
    )
    assert presign_only.status_code == 200
    assert presign_only.json()["url"] == STORAGE_URL


async def test_async_upload_file_and_list() -> None:
    transport = RecordingTransport(files_handler())
    client = AsyncCaesar(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=transport),
        storage_http_client=httpx.AsyncClient(transport=transport),
    )

    result = await client.upload_file(b"hello knowledge base", filename="notes.txt")
    assert result == UploadResult(name="notes.txt", sync_id="sync-1", index_state="queued")

    put = transport.requests[1]
    assert put.method == "PUT"
    assert "Authorization" not in put.headers

    listed = await client.list_files()
    assert listed.files is not None and str(listed.files[0].name) == "notes.txt"
    await client.aclose()
