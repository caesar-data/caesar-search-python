# caesar-search (Python)

Official Python SDK for the Caesar search API — web search with provenance, built for agents.

## Quickstart

```python
# pip install caesar-search    (or: uv add caesar-search)
from caesar_search import Caesar

client = Caesar()  # requires CAESAR_API_KEY (get one at app.trycaesar.com)
results = client.search("rust async runtime comparison", max_results=5)
doc = client.read(results.results[0].doc_id, query="which runtime is fastest")
client.feedback("result_helpful", search_id=results.search_id, doc_id=doc.doc.doc_id)
```

## Clients

- `Caesar` — synchronous; `AsyncCaesar` — same surface with `async`/`await`. Both support context managers.
- Methods: `search()`, `read()` (doc_id **or** URL; `start_char=` continues truncated reads), `feedback()`, and the Files knowledge base: `upload_file()`, `list_files()`, `delete_file()`, `index_files()`, `file_index_status()`.
- Responses are typed pydantic v2 models generated from the public OpenAPI spec; provenance fields (`doc_id`, `search_id`, `capture_id`, canonical/source URLs, crawl dates) are preserved verbatim.
- `client.with_raw_response.search(...)` returns the raw `httpx.Response`.
- Retries: 429/5xx with capped exponential backoff honoring `Retry-After` (`max_retries=` to tune, `0` to disable).
- Config: `api_key=` / `CAESAR_API_KEY` is required for the public API; `base_url=` / `CAESAR_BASE_URL` may point at a self-hosted deployment.

## File uploads (workspace knowledge base)

Upload your organization's documents and search them alongside the web. `upload_file()` presigns, PUTs the bytes straight to storage (the API key never reaches storage), and by default triggers an incremental indexing run:

```python
upload = client.upload_file("report.pdf")  # path, or bytes with filename=
status = client.file_index_status(upload.sync_id)  # poll until completed
hits = client.search(
    "q3 revenue",
    extra_body={"scope": {"indexes": ["workspace"], "workspace_id": "<your-org-id>"}},
)

client.list_files()          # FileListResponse: name, size, last_modified
client.delete_file("report.pdf")
```

Batch several uploads with `index=False`, then call `index_files()` once. Supported types match the indexer (pdf, office documents, text, markdown, csv); one file may be up to the server's `max_object_bytes` (100 MB by default).

## Errors

`CaesarError` → `APIConnectionError` / `APITimeoutError` and `APIStatusError` (with `.status_code`, `.code`, `.message`, `.request_id`) → `AuthenticationError` (401/403), `MissingAPIKeyError` (local `missing_api_key` preflight), `RateLimitError` (429).

## License

[MIT](LICENSE)
