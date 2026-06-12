# caesar-search (Python)

Official Python SDK for the Caesar search API — web search with provenance, built for agents.

## Quickstart

```python
# pip install caesar-search    (or: uv add caesar-search)
from caesar_search import Caesar

client = Caesar()  # reads CAESAR_API_KEY; anonymous tier works without a key
results = client.search("rust async runtime comparison", max_results=5)
doc = client.read(results.results[0].doc_id, query="which runtime is fastest")
client.feedback("result_helpful", search_id=results.search_id, doc_id=doc.doc.doc_id)
```

## Clients

- `Caesar` — synchronous; `AsyncCaesar` — same surface with `async`/`await`. Both support context managers.
- Methods: `search()`, `read()` (doc_id **or** URL; `start_char=` continues truncated reads), `feedback()`.
- Responses are typed pydantic v2 models generated from the public OpenAPI spec; provenance fields (`doc_id`, `search_id`, `capture_id`, canonical/source URLs, crawl dates) are preserved verbatim.
- `client.with_raw_response.search(...)` returns the raw `httpx.Response`.
- Retries: 429/5xx with capped exponential backoff honoring `Retry-After` (`max_retries=` to tune, `0` to disable).
- Config: `api_key=` / `CAESAR_API_KEY`; `base_url=` / `CAESAR_BASE_URL`.

## Errors

`CaesarError` → `APIConnectionError` / `APITimeoutError` and `APIStatusError` (with `.status_code`, `.code`, `.message`, `.request_id`) → `AuthenticationError` (401/403), `RateLimitError` (429).

## License

[MIT](LICENSE)
