"""The MCP server: three tools, one prompt, one resource.

`build_server()` is the single entry point; both the CLI and the tests use it.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from seekpeek import __version__
from seekpeek.client import DeepSeekClient, close_client, get_client, normalize_url
from seekpeek.config import MISSING_KEY_HINT, ConfigError
from seekpeek.errors import SeekpeekError, describe
from seekpeek.formatting import (
    fetch_outcomes_to_json,
    format_fetch_many_markdown,
    format_fetch_markdown,
    format_search_markdown,
    to_json,
    truncate,
)

DEFAULT_MAX_CHARS = 12000
MAX_BATCH_URLS = 10

ResponseFormat = Literal["markdown", "json"]

SEARCH_SYNTAX = """\
# DeepSeek search syntax

Operators are passed through to the search engine and can be combined freely in one query.

| Operator | Example | Effect |
| --- | --- | --- |
| `site:` | `DeepSeek site:github.com` | Restrict to a site. Several: `site:github.com OR site:huggingface.co` |
| `-site:` | `DeepSeek -site:example.com` | Exclude sites |
| `-` | `DeepSeek -广告` | Exclude pages containing a keyword |
| `-"..."` | `DeepSeek -"release notes"` | Exclude an exact phrase |
| `"..."` | `DeepSeek "精确短语"` | Exact phrase match |
| `lang:` | `DeepSeek lang:zh,en` | Restrict languages, comma separated |
| `after:` | `DeepSeek after:2025-01-01` | Published after a date (`after:2025` also works) |
| `before:` | `DeepSeek before:2026-06` | Published before a date |

Reading results:

- `rerank_score` — 0–3, blends relevance, authority and freshness. **≥ 1.5 is a strong hit**; below
  that, treat the result as a hint and verify it elsewhere.
- `authority` — 0–4 site authority. **≥ 3 is highly authoritative**; `-1` means unknown.
- `summary` — long, model-oriented digest (600–800 chars). `snippet` — short, human-oriented preview.
- `content` — full page text, only when `need_content=true` (each result is still billed once).
"""

INSTRUCTIONS = """\
seekpeek gives you live web access through the DeepSeek Search & Fetch API.

Tools:
- `search(query, ...)` — ranked web results. Use it whenever the answer depends on anything after your
  training cutoff, on current prices/versions/events, or on a URL the user mentioned.
- `fetch(url, ...)` — one page as clean Markdown, when a summary is not enough or the user hands you a link.
- `fetch_many(urls, ...)` — several pages in a single call (max 10). Prefer it over repeated `fetch` calls.

Search better:
- Operators work inline: `site:github.com`, `-site:example.com`, `"exact phrase"`, `-word`, `lang:zh,en`,
  `after:2026-01-01`, `before:2026-06`. Read the `seekpeek://search-syntax` resource for the full table.
- Pass the user's own wording as `ori_question` when it differs from `query` — it sharpens intent matching.
- Results are reranked: trust `rerank_score` >= 1.5 and `authority` >= 3 first, and prefer recent dates
  for time-sensitive questions.
- Page bodies are capped (`max_content_chars`, default 12000) to protect your context; raise it or set 0
  only when you truly need the whole page. Ask for `need_content=true` only when summaries are not enough.

Cite the `url` of every result you rely on, so the user can verify it.
"""

SEARCH_DESCRIPTION = """\
Search the live web (DeepSeek Search API) and get ranked, structured results.

Each result carries a rerank score (0-3; >=1.5 is high-quality), site authority (0-4; >=3 is highly
authoritative), publication date, a short snippet and a long model-oriented summary. Set
`need_content=true` to also receive the page text. Supports inline operators: `site:`, `-site:`,
`"exact phrase"`, `-word`, `lang:`, `after:`, `before:`.

Use it for anything after your training cutoff, current events, versions, prices or APIs.
"""

FETCH_DESCRIPTION = """\
Fetch one web page (DeepSeek Fetch API) and return its title plus the full text as Markdown.

Use it on a `url` returned by `search` when the summary is not enough, or on any URL the user provides.
Returns an explanatory error when the page is unreachable, blocked, or has no extractable content.
"""

FETCH_MANY_DESCRIPTION = """\
Fetch up to 10 web pages in one call and return each as Markdown.

Fetches run concurrently under the account rate limit, and one failing URL never fails the call — the
error is reported in place. Prefer this over several `fetch` calls when you already know which URLs
you need (for example the top three results of a search).
"""


def _client_error(exc: Exception, *, url: str | None = None) -> ToolError:
    if isinstance(exc, ConfigError):
        return ToolError(str(exc) or MISSING_KEY_HINT)
    if isinstance(exc, SeekpeekError):
        return ToolError(describe(exc, url=url))
    if isinstance(exc, ValueError):
        return ToolError(str(exc))
    return ToolError(f"{type(exc).__name__}: {exc}")


async def _report_progress(ctx: Context | None, done: int, total: int) -> None:
    """Report progress when a wire request exists to report to.

    In-process callers (tests, embedding, the CLI) have a `Context` without a request
    context, and the SDK raises there — progress is a nicety, never a failure.
    """
    if ctx is None:
        return
    with contextlib.suppress(RuntimeError, ValueError):
        await ctx.report_progress(done, total, message=f"fetched {done}/{total}")


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[None]:
    """Close the shared HTTP client when the server shuts down."""
    try:
        yield
    finally:
        await close_client()


def build_server() -> MCPServer:
    """Create the seekpeek MCP server."""
    server: MCPServer = MCPServer(
        name="seekpeek",
        title="seekpeek — live web access",
        description="Web search and page fetching for agents, powered by the DeepSeek Search & Fetch API.",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=app_lifespan,
    )

    # ------------------------------------------------------------------- tools

    @server.tool(
        title="Web search",
        description=SEARCH_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=False),
        structured_output=False,
    )
    async def search(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=128,
                description=(
                    'Search query, 1-128 characters. Supports `site:`, `-site:`, `"exact phrase"`, `-word`, '
                    "`lang:zh,en`, `after:2025-01-01`, `before:2026-06`."
                ),
            ),
        ],
        count: Annotated[int, Field(ge=1, le=50, description="How many results to return (1-50).")] = 10,
        ori_question: Annotated[
            str | None,
            Field(
                max_length=128,
                description=(
                    "The user's original question when it differs from `query` — improves intent matching. "
                    "Max 128 characters."
                ),
            ),
        ] = None,
        need_content: Annotated[
            bool,
            Field(
                description=(
                    "Also return each page's full text in `content` (larger output; one billed request either way)."
                )
            ),
        ] = False,
        localization: Annotated[
            str | None,
            Field(
                description=(
                    "IP address used to prefer locally relevant results, e.g. `203.0.113.7`. "
                    "Leave unset for global results."
                )
            ),
        ] = None,
        max_content_chars: Annotated[
            int,
            Field(ge=0, description="Per-result cap for page bodies when `need_content=true`. 0 disables the cap."),
        ] = DEFAULT_MAX_CHARS,
        response_format: Annotated[
            Literal["markdown", "json"],
            Field(description="`markdown` for reading, `json` for programmatic use."),
        ] = "markdown",
    ) -> str:
        """Search the live web and get ranked results with scores, dates and summaries."""
        try:
            client = await get_client()
            response = await client.search(
                query,
                count=count,
                ori_question=ori_question,
                localization=localization,
                need_content=need_content,
            )
        except Exception as exc:
            raise _client_error(exc) from exc

        if response_format == "json":
            return to_json(response)
        return format_search_markdown(
            response,
            query=query,
            need_content=need_content,
            max_content_chars=max_content_chars,
        )

    @server.tool(
        title="Fetch a page",
        description=FETCH_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=False),
        structured_output=False,
    )
    async def fetch(
        url: Annotated[
            str,
            Field(
                min_length=1,
                max_length=8192,
                description="Absolute http(s) URL. A bare host such as `example.com/post` is accepted too.",
            ),
        ],
        max_chars: Annotated[int, Field(ge=0, description="Cap on returned characters. 0 disables the cap.")] = (
            DEFAULT_MAX_CHARS
        ),
        response_format: Annotated[
            Literal["markdown", "json"],
            Field(description="`markdown` for reading, `json` for tooling."),
        ] = "markdown",
    ) -> str:
        """Fetch a web page and return its Markdown text."""
        target: str | None = None
        try:
            target = normalize_url(url)
            client = await get_client()
            response = await client.fetch(target)
        except Exception as exc:
            raise _client_error(exc, url=target) from exc

        if response_format == "json":
            return to_json({"url": target, "title": response.title, "content": truncate(response.content, max_chars)})
        return format_fetch_markdown(response, url=target, max_chars=max_chars)

    @server.tool(
        title="Fetch several pages",
        description=FETCH_MANY_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=False),
        structured_output=False,
    )
    async def fetch_many(
        urls: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=MAX_BATCH_URLS,
                description="1-10 absolute http(s) URLs (bare hosts are accepted).",
            ),
        ],
        max_chars: Annotated[
            int,
            Field(ge=0, description="Per-page cap on returned characters. 0 disables the cap."),
        ] = DEFAULT_MAX_CHARS,
        response_format: Annotated[
            Literal["markdown", "json"],
            Field(description="`markdown` for reading, `json` for tooling."),
        ] = "markdown",
        ctx: Context | None = None,
    ) -> str:
        """Fetch several pages at once, reporting per-URL failures inline."""
        try:
            client = await get_client()
        except Exception as exc:
            raise _client_error(exc) from exc

        async def on_progress(done: int, total: int) -> None:
            await _report_progress(ctx, done, total)

        try:
            targets = [normalize_url(url) for url in urls]
            outcomes = await client.fetch_many(targets, on_progress=on_progress)
        except Exception as exc:
            raise _client_error(exc) from exc

        if response_format == "json":
            return fetch_outcomes_to_json(outcomes, max_chars=max_chars)
        return format_fetch_many_markdown(outcomes, max_chars=max_chars)

    # ----------------------------------------------------------- prompt & docs

    @server.prompt(
        name="web-research",
        title="Web research brief",
        description="Turn a topic into a research brief that uses seekpeek's search operators well.",
    )
    def web_research(
        topic: Annotated[str, Field(min_length=1, description="What to research.")],
        depth: Annotated[
            Literal["quick", "standard", "deep"],
            Field(description="How many sources to gather."),
        ] = "standard",
    ) -> str:
        """Build a research brief for a topic."""
        plans = {
            "quick": "One `search` call (count 5) plus at most one `fetch` for the strongest result.",
            "standard": (
                "Two or three `search` calls from different angles (main query, `site:`-restricted, and "
                "recency-filtered with `after:`), then `fetch_many` on the 3-5 most authoritative URLs."
            ),
            "deep": (
                "Four or more `search` calls, deliberately varying wording, language (`lang:zh,en`), sites "
                "and date windows so the results overlap as little as possible. Then `fetch_many` in batches "
                "of 10 over everything promising, and finish with a second pass over gaps you noticed."
            ),
        }
        return (
            f"Research this with the `seekpeek` tools: {topic}\n\n"
            f"Depth: {depth} — {plans[depth]}\n\n"
            "Method:\n"
            "1. Restate what you need to find out, including any date, site or language constraints.\n"
            "2. Search with operators rather than long sentences. Keep the user's own wording in "
            "`ori_question` when it adds intent.\n"
            "3. Judge results by `rerank_score` (>=1.5), `authority` (>=3) and freshness before fetching.\n"
            "4. Cross-check any claim that changes the user's decision against a second, independent source.\n"
            "5. Answer with inline links to every source you used, and say plainly which parts are uncertain."
        )

    @server.resource(
        "seekpeek://search-syntax",
        name="search-syntax",
        title="DeepSeek search syntax & result fields",
        description="Search operators, rerank/authority thresholds and result field meanings.",
        mime_type="text/markdown",
    )
    def search_syntax() -> str:
        """Search-operators cheatsheet."""
        return SEARCH_SYNTAX

    return server


__all__ = ["DeepSeekClient", "build_server", "close_client"]
