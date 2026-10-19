"""End-to-end test: a real stdio subprocess talking to a mock DeepSeek endpoint.

This is the only test that exercises the whole wire path — MCP handshake, tool
discovery, JSON-RPC framing, HTTP calls, and rendering — without touching the
network or spending a request.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

API_KEY = "sk-e2e-test"


class MockDeepSeek(BaseHTTPRequestHandler):
    """Minimal stand-in for api.deepseek.com."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        payload: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/v1/tools/search":
            self._json(
                {
                    "results": [
                        {
                            "rerank_score": 2.6,
                            "title": f"Hit for {payload['query']}",
                            "url": "https://example.com/hit",
                            "snippet": "snippet",
                            "summary": "summary text",
                            "content": "body text",
                            "site_name": "Example",
                            "date": "2026-06-01T00:00:00+00:00",
                            "authority": 4,
                        }
                    ]
                }
            )
        elif self.path == "/v1/tools/fetch":
            url = payload["url"]
            if url.endswith("/dead"):
                self._json({"error": {"message": "no content", "type": "invalid_request_error", "code": 404}}, 404)
            else:
                self._json({"title": "Mock page", "content": f"# Mock page\n\nbody of {url}"})
        else:
            self._json({"error": {"message": "not found"}}, 404)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        pass


@pytest.fixture
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockDeepSeek)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def server_params(base_url: str, **extra: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "seekpeek"],
        env={"DEEPSEEK_API_KEY": API_KEY, "DEEPSEEK_BASE_URL": base_url, **extra},
    )


async def test_stdio_server_serves_tools(base_url: str) -> None:
    async with Client(server_params(base_url), read_timeout_seconds=30) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {"search", "fetch", "fetch_many"}
        assert client.server_info is not None
        assert client.server_info.name == "seekpeek"

        resources = await client.list_resources()
        assert [str(resource.uri) for resource in resources.resources] == ["seekpeek://search-syntax"]

        prompts = await client.list_prompts()
        assert [prompt.name for prompt in prompts.prompts] == ["web-research"]


async def test_stdio_search_and_fetch_round_trip(base_url: str) -> None:
    async with Client(server_params(base_url), read_timeout_seconds=30) as client:
        search = await client.call_tool("search", {"query": "DeepSeek V4"})
        assert search.is_error is False
        text = search.content[0].text
        assert "Hit for DeepSeek V4" in text
        assert "https://example.com/hit" in text
        assert "2.6/3.0 (high-quality)" in text

        page = await client.call_tool("fetch", {"url": "example.com/doc"})
        assert "body of https://example.com/doc" in page.content[0].text

        batch = await client.call_tool(
            "fetch_many", {"urls": ["https://a.dev/live", "https://a.dev/dead"], "max_chars": 50}
        )
        batch_text = batch.content[0].text
        assert "fetch_many (1/2 succeeded)" in batch_text
        assert "Failed: No usable content for https://a.dev/dead" in batch_text


async def test_stdio_surfaces_bad_key_but_keeps_serving(base_url: str) -> None:
    params = server_params(base_url)
    params.env = {**(params.env or {}), "DEEPSEEK_API_KEY": ""}

    async with Client(params, read_timeout_seconds=30) as client:
        result = await client.call_tool("search", {"query": "anything"})

    assert result.is_error is True
    assert "DEEPSEEK_API_KEY is not set" in result.content[0].text


async def test_stdio_prompt_and_resource_content(base_url: str) -> None:
    async with Client(server_params(base_url), read_timeout_seconds=30) as client:
        resource = await client.read_resource("seekpeek://search-syntax")
        assert "| `after:` |" in resource.contents[0].text

        prompt = await client.get_prompt("web-research", {"topic": "MCP servers", "depth": "quick"})
        assert "MCP servers" in prompt.messages[0].content.text


@pytest.mark.skipif(not os.environ.get("SEEKPEEK_E2E_LIVE"), reason="set SEEKPEEK_E2E_LIVE=1 to hit the real API")
async def test_live_api_smoke() -> None:  # pragma: no cover - opt-in
    """`SEEKPEEK_E2E_LIVE=1 DEEPSEEK_API_KEY=sk-... uv run pytest -k live` — costs ~1 request."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "seekpeek"])
    async with Client(params, read_timeout_seconds=60) as client:
        result = await client.call_tool("search", {"query": "DeepSeek", "count": 1})
        assert result.is_error is False
        assert "http" in result.content[0].text
