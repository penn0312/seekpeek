from __future__ import annotations

import json

import httpx2
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from seekpeek.client import DeepSeekClient
from seekpeek.config import ConfigError, Settings
from seekpeek.server import build_server
from tests.conftest import build_client, error_response, json_response, search_payload


@pytest.fixture
def server() -> MCPServer:
    return build_server()


@pytest.fixture
def install_client(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    """Route the server's client through a fake transport; returns the client to manage."""

    def install(handler) -> DeepSeekClient:
        client = build_client(settings, handler)
        monkeypatch.setattr("seekpeek.server.get_client", _returning(client))
        return client

    return install


def _returning(client: DeepSeekClient):
    async def fake_get_client() -> DeepSeekClient:
        return client

    return fake_get_client


def text_of(result) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


# ------------------------------------------------------------------------ surface


async def test_tool_inventory(server: MCPServer) -> None:
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert set(tools) == {"search", "fetch", "fetch_many"}
    for tool in tools.values():
        assert tool.description
        assert len(tool.description) > 60
        assert tool.output_schema is None  # text-only tools: no structured output to validate
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True

    search = tools["search"]
    assert search.input_schema["required"] == ["query"]
    properties = search.input_schema["properties"]
    assert properties["count"]["maximum"] == 50
    assert properties["count"]["default"] == 10
    assert "site:" in properties["query"]["description"]
    assert properties["response_format"]["enum"] == ["markdown", "json"]
    assert tools["fetch_many"].input_schema["properties"]["urls"]["maxItems"] == 10


async def test_server_identity_and_instructions(server: MCPServer) -> None:
    assert server.name == "seekpeek"
    assert server.instructions
    assert "rerank_score" in server.instructions


async def test_resource_serves_the_syntax_cheatsheet(server: MCPServer) -> None:
    contents = await server.read_resource("seekpeek://search-syntax")

    assert contents[0].mime_type == "text/markdown"
    assert "| `site:` |" in contents[0].content
    assert "after:2025-01-01" in contents[0].content


async def test_prompt_adapts_to_depth(server: MCPServer) -> None:
    prompts = {prompt.name for prompt in await server.list_prompts()}
    assert "web-research" in prompts

    quick = await server.get_prompt("web-research", {"topic": "MCP", "depth": "quick"})
    deep = await server.get_prompt("web-research", {"topic": "MCP", "depth": "deep"})

    assert "MCP" in str(quick)
    assert "Four or more" in str(deep)
    assert str(quick) != str(deep)


# --------------------------------------------------------------------- search tool


async def test_search_tool_renders_markdown(server: MCPServer, install_client) -> None:
    client = install_client(lambda request: json_response(request, search_payload()))
    async with client:
        result = await server.call_tool("search", {"query": "DeepSeek V4", "count": 1})

    text = text_of(result)
    assert result.is_error is False
    assert "Query: `DeepSeek V4` — 1 result" in text
    assert "Result 1" in text


async def test_search_tool_can_return_json(server: MCPServer, install_client) -> None:
    client = install_client(lambda request: json_response(request, search_payload()))
    async with client:
        result = await server.call_tool("search", {"query": "q", "response_format": "json"})

    payload = json.loads(text_of(result))
    assert payload["results"][0]["url"] == "https://example.com/1"


async def test_search_tool_forwards_operators_untouched(server: MCPServer, install_client) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return json_response(request, {"results": []})

    client = install_client(handler)
    async with client:
        await server.call_tool("search", {"query": "DeepSeek site:github.com lang:zh after:2025", "count": 5})

    body = json.loads(seen[0].content)
    assert body["query"] == "DeepSeek site:github.com lang:zh after:2025"
    assert body["count"] == 5


async def test_search_tool_requires_a_query(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="query"):
        await server.call_tool("search", {})


async def test_search_tool_rejects_out_of_range_count(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="less than or equal to 50"):
        await server.call_tool("search", {"query": "q", "count": 999})


async def test_missing_api_key_is_explained_to_the_model(server: MCPServer, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_get_client():
        raise ConfigError("DEEPSEEK_API_KEY is not set. Create a key at https://platform.deepseek.com/api_keys")

    monkeypatch.setattr("seekpeek.server.get_client", broken_get_client)
    with pytest.raises(ToolError, match="DEEPSEEK_API_KEY"):
        await server.call_tool("search", {"query": "q"})


# ---------------------------------------------------------------------- fetch tool


async def test_fetch_tool_returns_page_markdown(server: MCPServer, install_client) -> None:
    handler = lambda request: json_response(request, {"title": "Doc", "content": "# Doc\n\nHello."})  # noqa: E731
    client = install_client(handler)
    async with client:
        result = await server.call_tool("fetch", {"url": "https://example.com/doc"})

    text = text_of(result)
    assert text.startswith("# Doc")
    assert "Source: https://example.com/doc" in text


async def test_fetch_tool_explains_a_404_with_the_url(server: MCPServer, install_client) -> None:
    client = install_client(lambda request: error_response(request, 404, "no usable content"))
    async with client:
        with pytest.raises(ToolError) as excinfo:
            await server.call_tool("fetch", {"url": "https://example.com/missing"})

    message = str(excinfo.value)
    assert "https://example.com/missing" in message
    assert "404" in message


async def test_fetch_tool_rejects_nonsense_urls(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="HTTP/HTTPS"):
        await server.call_tool("fetch", {"url": "not a url"})


# ----------------------------------------------------------------- fetch_many tool


async def test_fetch_many_reports_each_url(server: MCPServer, install_client) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        url = json.loads(request.content)["url"]
        if url.endswith("/dead"):
            return error_response(request, 404, "no content")
        return json_response(request, {"title": url, "content": f"body of {url}"})

    client = install_client(handler)
    async with client:
        result = await server.call_tool("fetch_many", {"urls": ["https://a.dev/live", "https://a.dev/dead"]})

    text = text_of(result)
    assert "fetch_many (1/2 succeeded)" in text
    assert "body of https://a.dev/live" in text
    assert "Failed: No usable content for https://a.dev/dead" in text


async def test_fetch_many_caps_the_batch(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="at most 10 items"):
        await server.call_tool("fetch_many", {"urls": [f"https://a.dev/{i}" for i in range(11)]})


async def test_fetch_many_accepts_bare_hosts(server: MCPServer, install_client) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content)["url"])
        return json_response(request, {"title": "t", "content": "c"})

    client = install_client(handler)
    async with client:
        await server.call_tool("fetch_many", {"urls": ["example.com/a"]})

    assert seen == ["https://example.com/a"]
