from __future__ import annotations

import asyncio

import httpx2
import pytest

from seekpeek.client import DeepSeekClient, normalize_url
from seekpeek.errors import APIError, NetworkError, RateLimitError, describe
from seekpeek.models import FetchResponse, SearchResponse
from tests.conftest import build_client, error_response, json_response, request_body


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record (and skip) the backoff waits instead of really sleeping."""
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


# ------------------------------------------------------------------ normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com/a", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("<https://example.com/a>", "https://example.com/a"),
        ("example.com", "https://example.com"),
        ("example.com/a?b=c", "https://example.com/a?b=c"),
        ("sub.example.co.uk:8443/path", "https://sub.example.co.uk:8443/path"),
    ],
)
def test_normalize_url_accepts(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a url", "ftp://example.com", "just text"])
def test_normalize_url_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match=r"HTTP/HTTPS|empty"):
        normalize_url(raw)


# ----------------------------------------------------------------------- search


async def test_search_sends_documented_payload(settings) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return json_response(request, {"results": []})

    client = build_client(settings, handler)
    async with client:
        await client.search("DeepSeek V4", count=3, ori_question="what is new", need_content=True)

    assert len(seen) == 1
    request = seen[0]
    assert request.url.path == "/v1/tools/search"
    assert request.headers["authorization"] == "Bearer sk-test-key"
    assert request_body(request) == {
        "query": "DeepSeek V4",
        "count": 3,
        "need_content": True,
        "ori_question": "what is new",
    }


async def test_search_omits_optional_empty_fields(settings) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return json_response(request, {"results": []})

    client = build_client(settings, handler)
    async with client:
        await client.search("q", ori_question="", localization=None)

    assert request_body(seen[0]) == {"query": "q", "count": 10, "need_content": False}


async def test_search_parses_results_and_ignores_unknown_fields(settings) -> None:
    payload = {
        "results": [{"title": "t", "url": "https://x.dev", "rerank_score": 2.5, "authority": 4, "future_field": 1}],
        "unexpected": "ignored",
    }
    client = build_client(settings, lambda request: json_response(request, payload))
    async with client:
        response = await client.search("q")

    assert isinstance(response, SearchResponse)
    assert response.results[0].rerank_score == 2.5
    assert response.results[0].published is None
    assert response.results[0].best_text == ""


# ----------------------------------------------------------------------- retries


async def test_search_retries_429_then_succeeds(settings, sleeps) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return error_response(request, 429, "rate limited")
        return json_response(request, {"results": [{"title": "ok", "url": "https://x.dev"}]})

    client = build_client(settings, handler)
    async with client:
        response = await client.search("q")

    assert calls == 2
    assert response.results[0].title == "ok"
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 0.75  # 0.5s base + up to 25% jitter


async def test_retry_after_header_is_honoured(settings, sleeps) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}})
        return json_response(request, {"results": []})

    client = build_client(settings, handler)
    async with client:
        await client.search("q")

    assert 3.0 <= sleeps[0] <= 3.75


async def test_retries_exhausted_raises_rate_limit_error(settings, sleeps) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return error_response(request, 429, "rate limited")

    client = build_client(settings, handler)
    async with client:
        with pytest.raises(RateLimitError) as excinfo:
            await client.search("q")

    error = excinfo.value
    assert error.status_code == 429
    assert error.attempts == 2  # one retry configured
    assert error.retryable
    assert "5 search and 5 fetch requests per second" in describe(error)


async def test_network_error_is_wrapped(settings) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host", request=request)

    client = build_client(settings, handler)
    async with client:
        with pytest.raises(NetworkError, match="ConnectError"):
            await client.search("q")


async def test_non_json_200_is_an_error(settings) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>hi</html>")

    client = build_client(settings, handler)
    async with client:
        with pytest.raises(NetworkError, match="not valid JSON"):
            await client.search("q")


# -------------------------------------------------------------------------- errors


async def test_bad_api_key_message_is_actionable(settings) -> None:
    client = build_client(settings, lambda request: error_response(request, 401, "Authentication Fails"))
    async with client:
        with pytest.raises(APIError) as excinfo:
            await client.search("q")

    message = describe(excinfo.value)
    assert "401" in message
    assert "DEEPSEEK_API_KEY" in message
    assert "Authentication Fails" in message


async def test_fetch_404_explains_the_url(settings) -> None:
    client = build_client(settings, lambda request: error_response(request, 404, "no content"))
    async with client:
        with pytest.raises(APIError) as excinfo:
            await client.fetch("https://gone.example.com/post")

    message = describe(excinfo.value, url="https://gone.example.com/post")
    assert "https://gone.example.com/post" in message
    assert "404" in message


async def test_insufficient_balance_points_at_top_up(settings) -> None:
    client = build_client(settings, lambda request: error_response(request, 402, "Insufficient Balance"))
    async with client:
        with pytest.raises(APIError) as excinfo:
            await client.search("q")

    assert "top_up" in describe(excinfo.value)


# --------------------------------------------------------------------------- fetch


async def test_fetch_returns_title_and_content(settings) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return json_response(request, {"title": "Example article", "content": "# Example article\n\nBody."})

    client = build_client(settings, handler)
    async with client:
        response = await client.fetch("example.com/post")

    assert request_body(seen[0]) == {"url": "https://example.com/post"}
    assert response == FetchResponse(title="Example article", content="# Example article\n\nBody.")


async def test_fetch_many_keeps_order_and_reports_failures(settings) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        url = request_body(request)["url"]
        if url.endswith("/ok"):
            return json_response(request, {"title": "OK", "content": "fine"})
        return error_response(request, 404, "no content")

    client = build_client(settings, handler)
    async with client:
        outcomes = await client.fetch_many(["https://x.dev/ok", "https://x.dev/dead", "https://x.dev/ok"])

    assert [outcome.url for outcome in outcomes] == ["https://x.dev/ok", "https://x.dev/dead", "https://x.dev/ok"]
    assert [outcome.ok for outcome in outcomes] == [True, False, True]
    assert outcomes[0].response is not None
    assert outcomes[0].response.title == "OK"
    assert outcomes[1].error is not None
    assert "https://x.dev/dead" in outcomes[1].error


async def test_fetch_many_reports_progress(settings) -> None:
    seen: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        seen.append((done, total))

    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(request, {"title": "t", "content": "c"})

    client = build_client(settings, handler)
    async with client:
        await client.fetch_many(["https://x.dev/1", "https://x.dev/2"], on_progress=on_progress)

    assert sorted(seen) == [(1, 2), (2, 2)]


async def test_fetch_many_bills_duplicates_once(settings) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request_body(request)["url"])
        return json_response(request, {"title": "t", "content": "c"})

    client = build_client(settings, handler)
    async with client:
        outcomes = await client.fetch_many(["https://x.dev/a", "https://x.dev/a", "https://x.dev/b"])

    assert seen == ["https://x.dev/a", "https://x.dev/b"]  # one request per unique URL
    assert [outcome.url for outcome in outcomes] == ["https://x.dev/a", "https://x.dev/a", "https://x.dev/b"]


async def test_client_requires_api_key() -> None:
    from seekpeek.config import ConfigError, Settings

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        DeepSeekClient(Settings(api_key=""))


async def test_client_is_closed_after_context(settings) -> None:
    client = DeepSeekClient(settings, transport=httpx2.MockTransport(lambda request: json_response(request, {})))
    async with client:
        pass
    assert client._http.is_closed
