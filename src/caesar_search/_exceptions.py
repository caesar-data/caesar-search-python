from __future__ import annotations

import httpx


class CaesarError(Exception):
    """Base class for all caesar-search errors."""


class APIConnectionError(CaesarError):
    """The API could not be reached."""


class APITimeoutError(APIConnectionError):
    """The request timed out."""


class APIStatusError(CaesarError):
    """The API returned a non-2xx response."""

    def __init__(
        self, *, status_code: int, code: str, message: str, request_id: str | None, response: httpx.Response
    ):
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.response = response


class AuthenticationError(APIStatusError):
    """Missing or invalid API key (HTTP 401/403)."""


class InsufficientBalanceError(APIStatusError):
    """Prepaid balance is depleted (HTTP 402 insufficient_balance)."""


class RateLimitError(APIStatusError):
    """Rate limit exceeded (HTTP 429)."""


def status_error_from_response(response: httpx.Response) -> APIStatusError:
    code = f"http_{response.status_code}"
    message = f"API request failed with status {response.status_code}"
    request_id: str | None = None
    try:
        payload = response.json()
        error = payload.get("error") or {}
        code = error.get("code") or code
        message = error.get("message") or message
        request_id = payload.get("request_id")
    except Exception:  # noqa: BLE001 - non-JSON error bodies fall back to defaults
        pass

    error_class = APIStatusError
    if response.status_code in (401, 403):
        error_class = AuthenticationError
    elif response.status_code == 402 and code == "insufficient_balance":
        error_class = InsufficientBalanceError
    elif response.status_code == 429:
        error_class = RateLimitError
    return error_class(
        status_code=response.status_code,
        code=code,
        message=message,
        request_id=request_id,
        response=response,
    )
