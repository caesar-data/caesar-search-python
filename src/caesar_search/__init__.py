"""caesar-search: official Python SDK for the Caesar search API."""

from . import models
from ._client import DEFAULT_BASE_URL, AsyncCaesar, Caesar, UploadResult
from ._exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    CaesarError,
    InsufficientBalanceError,
    MissingAPIKeyError,
    RateLimitError,
)
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
    "FileDeleteResponse",
    "FileIndexResponse",
    "FileIndexStatusResponse",
    "FileListResponse",
    "FilePresignResponse",
    "InsufficientBalanceError",
    "MissingAPIKeyError",
    "RateLimitError",
    "SearchResponse",
    "UploadResult",
    "__version__",
    "models",
]
