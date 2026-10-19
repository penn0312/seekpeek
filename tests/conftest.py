from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx2
import pytest

from seekpeek.client import DeepSeekClient
from seekpeek.config import Settings

API_KEY = "sk-test-key"
BASE_URL = "https://api.deepseek.com"

SearchResultPayload = dict[str, Any]


@pytest.fixture
def settings() -> Settings:
    """Fast, retry-light settings; no artificial rate limiting."""
    return Settings(api_key=API_KEY, base_url=BASE_URL, timeout=5.0, max_retries=1, search_qps=0.0, fetch_qps=0.0)


def build_client(settings: Settings, handler: Callable[[httpx2.Request], httpx2.Response]) -> DeepSeekClient:
    """A real client wired to a fake transport, so payloads and parsing are exercised for real."""
    return DeepSeekClient(settings, transport=httpx2.MockTransport(handler))


def json_response(request: httpx2.Request, payload: Any, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, json=payload, request=request)


def error_response(request: httpx2.Request, status_code: int, message: str = "boom") -> httpx2.Response:
    return httpx2.Response(
        status_code,
        json={"error": {"message": message, "type": "invalid_request_error", "param": None, "code": status_code}},
        request=request,
    )


def search_payload(count: int = 1, *, scores: Sequence[float] = (2.4,)) -> dict[str, Any]:
    return {
        "results": [
            {
                "rerank_score": score,
                "title": f"Result {index}",
                "url": f"https://example.com/{index}",
                "snippet": f"snippet {index}",
                "summary": f"summary {index}",
                "content": f"content {index}",
                "site_name": "Example",
                "site_icon": "https://example.com/favicon.ico",
                "date": "2026-06-01T00:00:00+00:00",
                "authority": 3,
            }
            for index, score in enumerate(scores, start=1)
        ][:count]
    }


def request_body(request: httpx2.Request) -> dict[str, Any]:
    return json.loads(request.content)
