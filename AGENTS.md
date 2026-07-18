# AGENTS.md

Guidance for AI agents using and maintaining `caesar-search` (Python).

## Using the SDK

- The loop: `search()` → pick `doc_id` → `read()` → optionally `feedback()`. Thread provenance handles (`doc_id`, `search_id`) between calls.
- `read()` accepts a doc_id or URL positionally. A truncated read sets `content.truncated`; continue with `start_char=content.start_char + content.char_count` — do not retry with a bigger `max_chars`.
- `search(verbosity=...)` controls payload shape: `ids_only` (handles only), `compact`, `standard` (default), `full` (adds provenance). `max_chars_total=` sets a hard response budget.
- Set `CAESAR_API_KEY`; never hardcode keys. Exceptions: catch `MissingAPIKeyError`/`AuthenticationError`/`RateLimitError`/`APIStatusError`.

## Common mistakes

| Mistake | Correction |
|---|---|
| `client.search(query="...", limit=5)` | The parameter is `max_results` |
| `client.document(...)` / `client.get_document(...)` | The method is `read()` |
| Retrying truncated reads with bigger `max_chars` | Use `start_char` continuation |
| Expecting camelCase fields | Models are snake_case, matching the API |
| Hand-editing `models/_models.py` | Generated from `spec/openapi-public.json`; run the generator instead |

## Maintaining this repo

- `spec/openapi-public.json` is the vendored contract; `uv run datamodel-codegen` regenerates `src/caesar_search/models/_models.py`. CI fails if the generated file is dirty against the spec.
- The spec-sync workflow polls the live public spec, regenerates, classifies the diff with oasdiff, and updates stable review branches. Merging a non-breaking version bump into `main` publishes the release automatically.
- `uv run pytest` (hermetic, httpx MockTransport) must pass; contract tests are gated behind `CAESAR_CONTRACT=1`.
- `uv run mypy` strict on the veneer; `uv run ruff check`.
- Never name upstream search/inference providers in code, docs, or errors.
