"""Async client for the DeepSeek Search & Fetch endpoints.

Handles the tedious parts: bearer auth, account-level rate limiting, retries with
backoff, and turning HTTP failures into exceptions that carry an actionable message.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx2

from seekpeek import __version__
from seekpeek.config import Settings
from seekpeek.errors import APIError, NetworkError, RateLimitError, SeekpeekError, describe
from seekpeek.models import FetchOutcome, FetchResponse, SearchResponse

logger = logging.getLogger("seekpeek.client")

SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_BACKOFF = 20.0
MAX_RETRY_AFTER = 30.0
MAX_FETCH_CONCURRENCY = 5

_BARE_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?::\d{1,5})?(?:[/?#]\S*)?\Z")


class RateLimiter:
    """Spaces calls so a burst never trips the account-level QPS limit."""

    def __init__(self, qps: float) -> None:
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._ready_at = 0.0

    async def acquire(self) -> None:
        if self.interval <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            wait = self._ready_at - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._ready_at = max(loop.time(), self._ready_at) + self.interval


def normalize_url(url: str) -> str:
    """Trim whitespace and accept bare hostnames such as `example.com/page`."""
    cleaned = url.strip().strip("<>\"'")
    if not cleaned:
        raise ValueError("The URL is empty.")
    if "://" not in cleaned:
        if not _BARE_HOST.fullmatch(cleaned):
            raise ValueError(f"{url!r} is not an HTTP/HTTPS URL — pass a full address such as https://example.com/page")
        cleaned = f"https://{cleaned}"

    parts = urlsplit(cleaned)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{url!r} is not an HTTP/HTTPS URL — pass a full address such as https://example.com/page")
    if parts.username or parts.password:
        raise ValueError("The DeepSeek Fetch API rejects URLs that carry a username or password.")
    return cleaned


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def _error_message(response: httpx2.Response) -> str:
    """Dig the message out of the OpenAI-style error envelope, with a fallback."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:500]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return ""


def _raise_for_status(status: int, message: str, attempts: int) -> APIError:
    factory = RateLimitError if status == 429 else APIError
    return factory(status, message, attempts=attempts)


class DeepSeekClient:
    """Thin, typed wrapper over `/v1/tools/search` and `/v1/tools/fetch`."""

    def __init__(self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._http = httpx2.AsyncClient(
            base_url=settings.base_url,
            headers={
                "Authorization": f"Bearer {settings.require_api_key()}",
                "Content-Type": "application/json",
                "User-Agent": f"seekpeek/{__version__}",
            },
            timeout=httpx2.Timeout(settings.timeout, connect=min(10.0, settings.timeout)),
            follow_redirects=True,
            transport=transport,
        )
        self._search_limiter = RateLimiter(settings.search_qps)
        self._fetch_limiter = RateLimiter(settings.fetch_qps)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> DeepSeekClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ request

    async def _post(self, path: str, payload: dict[str, Any], limiter: RateLimiter) -> dict[str, Any]:
        attempts = self.settings.max_retries + 1
        last_error: APIError | None = None

        for attempt in range(1, attempts + 1):
            await limiter.acquire()
            try:
                response = await self._http.post(path, json=payload)
            except httpx2.RequestError as exc:
                if attempt < attempts:
                    await self._pause(attempt, None, f"{type(exc).__name__}: {exc}")
                    continue
                raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise NetworkError("DeepSeek returned a 200 response that was not valid JSON.") from exc
                if not isinstance(body, dict):
                    raise NetworkError("DeepSeek returned a 200 response with an unexpected body shape.")
                return body

            last_error = _raise_for_status(response.status_code, _error_message(response), attempt)
            if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                await self._pause(
                    attempt,
                    _parse_retry_after(response.headers.get("retry-after")),
                    f"HTTP {response.status_code}",
                )
                continue
            raise last_error

        # Unreachable: the loop either returns or raises on the final attempt.
        raise last_error or NetworkError("DeepSeek request failed for an unknown reason.")

    async def _pause(self, attempt: int, retry_after: float | None, reason: str) -> None:
        delay = retry_after if retry_after is not None else min(0.5 * 2 ** (attempt - 1), MAX_BACKOFF)
        delay += random.uniform(0, delay / 4)  # jitter, so parallel callers don't sync up
        logger.warning("DeepSeek %s on attempt %d — retrying in %.1fs", reason, attempt, delay)
        await asyncio.sleep(delay)

    # ------------------------------------------------------------------- public

    async def search(
        self,
        query: str,
        *,
        count: int = 10,
        ori_question: str | None = None,
        localization: str | None = None,
        need_content: bool = False,
    ) -> SearchResponse:
        """`POST /v1/tools/search`."""
        payload: dict[str, Any] = {"query": query, "count": count, "need_content": need_content}
        if ori_question:
            payload["ori_question"] = ori_question
        if localization:
            payload["localization"] = localization
        body = await self._post(SEARCH_PATH, payload, self._search_limiter)
        return SearchResponse.model_validate(body)

    async def fetch(self, url: str) -> FetchResponse:
        """`POST /v1/tools/fetch`."""
        body = await self._post(FETCH_PATH, {"url": normalize_url(url)}, self._fetch_limiter)
        return FetchResponse.model_validate(body)

    async def fetch_many(
        self,
        urls: Sequence[str],
        *,
        concurrency: int = MAX_FETCH_CONCURRENCY,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> list[FetchOutcome]:
        """Fetch several pages concurrently, never raising: failures come back per URL.

        Duplicate URLs are fetched once — DeepSeek bills per request — and the shared
        rate limiter keeps the burst inside the account's QPS budget.
        """
        targets = [normalize_url(url) for url in urls]
        unique = list(dict.fromkeys(targets))
        outcomes: dict[str, FetchOutcome] = {}
        semaphore = asyncio.Semaphore(max(1, concurrency))
        done = 0

        async def run(target: str) -> None:
            nonlocal done
            async with semaphore:
                try:
                    outcomes[target] = FetchOutcome(url=target, response=await self.fetch(target))
                except SeekpeekError as exc:
                    outcomes[target] = FetchOutcome(url=target, error=describe(exc, url=target))
                except Exception as exc:
                    outcomes[target] = FetchOutcome(url=target, error=f"{type(exc).__name__}: {exc}")
            done += 1
            if on_progress is not None:
                await on_progress(done, len(unique))

        await asyncio.gather(*(run(target) for target in unique))
        return [outcomes[target] for target in targets]


_client: DeepSeekClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> DeepSeekClient:
    """Process-wide client, created on first use so startup never blocks on config."""
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = DeepSeekClient(Settings.from_env())
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
