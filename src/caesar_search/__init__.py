"""caesar-search: official Python SDK for the Caesar search API."""

from . import models
from ._client import DEFAULT_BASE_URL, AsyncCaesar, Caesar
from ._exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    CaesarError,
    RateLimitError,
)
from ._version import __version__
from .models import DocumentResponse, FeedbackResponse, SearchResponse

__all__ = [
    "DEFAULT_BASE_URL",
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "AsyncCaesar",
    "AuthenticationError",
    "Caesar",
    "CaesarError",
    "DocumentResponse",
    "FeedbackResponse",
    "RateLimitError",
    "SearchResponse",
    "__version__",
    "models",
]
