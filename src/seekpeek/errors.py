"""Error types plus human/model readable explanations.

Kept free of MCP imports so the client layer stays reusable (the CLI uses it too).
"""

from __future__ import annotations

DOCS_URL = "https://api-docs.deepseek.com/guides/search"
KEYS_URL = "https://platform.deepseek.com/api_keys"
BILLING_URL = "https://platform.deepseek.com/top_up"

MISSING_KEY_HINT = (
    "DEEPSEEK_API_KEY is not set. Create a key at https://platform.deepseek.com/api_keys, "
    "then pass it to seekpeek through your MCP host's `env` block, e.g.\n"
    '  "env": { "DEEPSEEK_API_KEY": "sk-..." }'
)


class SeekpeekError(Exception):
    """Base class for every error seekpeek raises."""


class ConfigError(SeekpeekError):
    """The environment is not usable; the message already says what to do."""


class NetworkError(SeekpeekError):
    """The request never produced an HTTP response (DNS, TLS, connect, read timeout)."""


class APIError(SeekpeekError):
    """DeepSeek answered with a non-200 status."""

    def __init__(self, status_code: int, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.attempts = attempts

    @property
    def retryable(self) -> bool:
        return self.status_code in {408, 429, 500, 502, 503, 504}


class RateLimitError(APIError):
    """HTTP 429 — account-level QPS exceeded."""


def _suffix(attempts: int) -> str:
    return "" if attempts <= 1 else f" (after {attempts} attempts)"


def describe(exc: SeekpeekError, *, url: str | None = None) -> str:
    """Turn an exception into one actionable paragraph for the model or the user."""
    if isinstance(exc, ConfigError):
        return str(exc) or MISSING_KEY_HINT

    if isinstance(exc, NetworkError):
        return (
            f"Could not reach the DeepSeek API: {exc}. "
            "Check your network connection (and DEEPSEEK_BASE_URL if you use a proxy), then retry."
        )

    if isinstance(exc, RateLimitError):
        return (
            f"DeepSeek rate limit hit{_suffix(exc.attempts)}: the account allows 5 search and 5 fetch requests "
            "per second. Wait a moment before retrying, and avoid issuing many searches in parallel."
        )

    if not isinstance(exc, APIError):  # pragma: no cover - defensive
        return str(exc)

    attempts = _suffix(exc.attempts)
    status = exc.status_code
    detail = f" DeepSeek said: {exc.message}" if exc.message else ""

    if status == 400:
        return f"DeepSeek rejected the request{attempts}.{detail}"
    if status == 401:
        return (
            f"DeepSeek rejected the API key (401){attempts}."
            f" Check DEEPSEEK_API_KEY — it must be an active key from {KEYS_URL}.{detail}"
        )
    if status == 402:
        return f"The DeepSeek account has insufficient balance (402){attempts}. Top up at {BILLING_URL}, then retry."
    if status == 403:
        return (
            f"DeepSeek refused this fetch for security-policy reasons (403){attempts}."
            " The page is not retrievable through the API; try another source."
        )
    if status == 404:
        target = url or "the URL"
        return (
            f"No usable content for {target} (404){attempts}."
            " The page may be unreachable, blocked by the site, served as an unsupported content type, "
            "or contain no extractable Markdown. Try the site homepage, an AMP/print variant, or a cache."
        )
    if status == 408:
        return f"DeepSeek timed out while reading the request body (408){attempts}. Retry — this is usually transient."
    if status == 413:
        return f"The request body is too large (413){attempts}. Shorten the URL or the query and retry.{detail}"
    if status >= 500:
        return (
            f"DeepSeek had a server-side problem ({status}){attempts}. This is usually transient — "
            f"retry in a few seconds.{detail}"
        )
    return f"DeepSeek returned HTTP {status}{attempts}.{detail} See {DOCS_URL} for the error table."
