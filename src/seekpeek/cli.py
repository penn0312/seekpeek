"""Command line interface: run the MCP server, or use seekpeek directly.

seekpeek                          # stdio MCP server (what hosts launch)
seekpeek --transport streamable-http
seekpeek doctor                   # check key, endpoint and limits
seekpeek search "query" --content
seekpeek fetch https://example.com
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Literal

from seekpeek import __version__
from seekpeek.client import DeepSeekClient
from seekpeek.config import Settings
from seekpeek.errors import SeekpeekError, describe
from seekpeek.formatting import (
    fetch_outcomes_to_json,
    format_fetch_many_markdown,
    format_fetch_markdown,
    format_search_markdown,
    to_json,
)

PROGRAM = "seekpeek"

LOOKS_UNCONFIGURED = (
    "{program}: DEEPSEEK_API_KEY is not set — every tool call will explain this until you provide one.\n"
    "           Create a key at https://platform.deepseek.com/api_keys and add it to your host's env block.\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Live web search and page fetching for AI agents, over the DeepSeek Search & Fetch API.",
        epilog="With no subcommand, launches the MCP server on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="MCP transport to serve (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--json-response", action="store_true", help="Answer HTTP requests with plain JSON")

    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="check the API key, endpoint and rate limits")
    doctor.add_argument("--offline", action="store_true", help="skip the live search check")

    search = sub.add_parser("search", help="run a search without an MCP host")
    search.add_argument("query", help="query, with operators such as site: / lang:zh / after:2025")
    search.add_argument("--count", type=int, default=10, help="number of results, 1-50 (default: 10)")
    search.add_argument("--ori-question", default=None, help="the user's original question, for intent matching")
    search.add_argument("--content", action="store_true", help="include each page's full text")
    search.add_argument("--localization", default=None, help="IP address used to prefer localised results")
    search.add_argument("--max-content-chars", type=int, default=12000, help="per-page cap, 0 disables it")
    search.add_argument("--json", dest="as_json", action="store_true", help="print raw JSON")

    fetch = sub.add_parser("fetch", help="fetch one or more pages without an MCP host")
    fetch.add_argument("urls", nargs="+", help="one or more http(s) URLs")
    fetch.add_argument("--max-chars", type=int, default=12000, help="per-page cap, 0 disables it")
    fetch.add_argument("--json", dest="as_json", action="store_true", help="print raw JSON")

    return parser


def _fail(message: str) -> int:
    print(f"{PROGRAM}: {message}", file=sys.stderr)
    return 1


async def _run_search(settings: Settings, args: argparse.Namespace) -> int:
    async with DeepSeekClient(settings) as client:
        response = await client.search(
            args.query,
            count=args.count,
            ori_question=args.ori_question,
            localization=args.localization,
            need_content=args.content,
        )
    if args.as_json:
        print(to_json(response))
    else:
        print(
            format_search_markdown(
                response,
                query=args.query,
                need_content=args.content,
                max_content_chars=args.max_content_chars,
            )
        )
    return 0


async def _run_fetch(settings: Settings, args: argparse.Namespace) -> int:
    async with DeepSeekClient(settings) as client:
        if len(args.urls) == 1:
            url = args.urls[0]
            response = await client.fetch(url)
            print(
                to_json({"url": url, "title": response.title, "content": response.content})
                if args.as_json
                else format_fetch_markdown(response, url=url, max_chars=args.max_chars)
            )
            return 0
        outcomes = await client.fetch_many(args.urls)
    print(
        fetch_outcomes_to_json(outcomes, max_chars=args.max_chars)
        if args.as_json
        else format_fetch_many_markdown(outcomes, max_chars=args.max_chars)
    )
    return 0 if all(outcome.ok for outcome in outcomes) else 1


async def _run_doctor(settings: Settings, args: argparse.Namespace) -> int:
    width = max(len(label) for label, _ in settings.describe())
    print(f"{PROGRAM} {__version__} — configuration")
    for label, value in settings.describe():
        print(f"  {label.ljust(width)}  {value}")

    if not settings.api_key:
        print("\n✗ No API key. Set DEEPSEEK_API_KEY (https://platform.deepseek.com/api_keys).")
        return 1

    if args.offline:
        print("\n• Offline mode: skipped the live search check.")
        return 0

    print(f"\n• Probing {settings.base_url}/v1/tools/search …")
    async with DeepSeekClient(settings) as client:
        response = await client.search("DeepSeek", count=1)
    if not response.results:
        print("✓ Endpoint reachable, but the probe returned no results (unusual, but not fatal).")
        return 0

    top = response.results[0]
    print(f"✓ Search works — 1 result billed. Top hit: {top.title or top.url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except Exception as exc:  # ConfigError
        return _fail(str(exc))

    if args.command is None:
        from seekpeek.server import build_server

        if not settings.api_key:
            print(LOOKS_UNCONFIGURED.format(program=PROGRAM), file=sys.stderr)
        server = build_server()
        transport: Literal["stdio", "sse", "streamable-http"] = args.transport
        if transport == "stdio":
            server.run(transport="stdio")
        else:
            server.run(
                transport=transport,
                host=args.host,
                port=args.port,
                json_response=args.json_response,
            )
        return 0

    runner = {"doctor": _run_doctor, "search": _run_search, "fetch": _run_fetch}[args.command]
    try:
        return asyncio.run(runner(settings, args))
    except ValueError as exc:
        return _fail(str(exc))
    except SeekpeekError as exc:
        url = args.urls[0] if getattr(args, "urls", None) else None
        return _fail(describe(exc, url=url))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
