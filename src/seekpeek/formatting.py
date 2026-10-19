"""Render API responses as Markdown (for models) or JSON (for machines).

Pure functions — no I/O, no MCP — so they are cheap to unit-test.
"""

from __future__ import annotations

import json
from typing import Any

from seekpeek.models import FetchOutcome, FetchResponse, SearchResponse, SearchResult

TRUNCATION_NOTE = "… [truncated: {removed} more characters]"

HIGH_QUALITY_SCORE = 1.5
AUTHORITATIVE_LEVEL = 3
HIGHLY_AUTHORITATIVE_LEVEL = 4


def truncate(text: str, limit: int) -> str:
    """Cut `text` to `limit` characters, announcing what was dropped. `limit<=0` keeps everything."""
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n\n{TRUNCATION_NOTE.format(removed=len(text) - limit)}"


def _meta_line(result: SearchResult) -> str:
    parts: list[str] = []
    if result.rerank_score is not None:
        score = f"{result.rerank_score:.1f}/3.0"
        if result.rerank_score >= HIGH_QUALITY_SCORE:
            score += " (high-quality)"
        parts.append(f"Relevance: {score}")
    if result.authority >= 0:
        authority = f"{result.authority}/4"
        if result.authority >= HIGHLY_AUTHORITATIVE_LEVEL:
            authority += " (highly authoritative)"
        elif result.authority >= AUTHORITATIVE_LEVEL:
            authority += " (authoritative)"
        parts.append(f"Authority: {authority}")
    if result.published:
        parts.append(f"Published: {result.published}")
    if result.site_name:
        parts.append(f"Site: {result.site_name}")
    return " · ".join(parts)


def format_search_markdown(
    response: SearchResponse,
    *,
    query: str,
    need_content: bool = False,
    max_content_chars: int = 0,
) -> str:
    """Markdown report of a search."""
    count = len(response.results)
    if count == 0:
        return (
            f"# seekpeek · search\n\nNo results for `{query}`.\n\n"
            "Try fewer operators, drop quotes, or use broader wording. "
            "`site:`/`lang:`/`after:` filters are the usual culprits."
        )

    noun = "result" if count == 1 else "results"

    if need_content:
        content_note = (
            f", bodies capped at {max_content_chars} chars each" if max_content_chars > 0 else "with full bodies"
        )
    else:
        content_note = ""
    lines = ["# seekpeek · search", "", f"Query: `{query}` — {count} {noun}{content_note}"]

    for index, result in enumerate(response.results, start=1):
        lines += ["", f"## {index}. {result.title or '(untitled)'}", "", f"- URL: {result.url}"]
        if meta := _meta_line(result):
            lines.append(f"- {meta}")
        if text := result.best_text:
            lines += ["", text]
        if need_content and result.content:
            body = truncate(result.content, max_content_chars)
            lines += ["", f"**Page content** (from `{result.url}`):", "", body]
        elif need_content:
            lines += ["", "_Page content was requested but the API returned none for this result._"]

    return "\n".join(lines)


def format_fetch_markdown(
    response: FetchResponse,
    *,
    url: str,
    max_chars: int = 0,
    heading: str | None = None,
) -> str:
    """Markdown report of a single fetched page."""
    title = response.title.strip() or "(untitled page)"
    lines = [f"# {title}", "", f"Source: {url}", "", "---", ""]
    lines.append(truncate(response.content, max_chars).strip() if response.content else "(the page had no text)")
    rendered = "\n".join(lines)
    return f"# {heading}\n\n{rendered}" if heading else rendered


def format_fetch_many_markdown(outcomes: list[FetchOutcome], *, max_chars: int = 0) -> str:
    """Markdown report for a batch fetch, successes and failures side by side."""
    total = len(outcomes)
    ok = sum(1 for outcome in outcomes if outcome.ok)
    lines = [f"# seekpeek · fetch_many ({ok}/{total} succeeded)", ""]

    for index, outcome in enumerate(outcomes, start=1):
        if outcome.response is not None:
            title = outcome.response.title.strip() or outcome.url
            body = truncate(outcome.response.content, max_chars).strip() if outcome.response.content else "(no text)"
            lines += [f"## {index}. {title}", "", f"- URL: {outcome.url}", "", body, ""]
        else:
            lines += [f"## {index}. {outcome.url}", "", f"Failed: {outcome.error}", ""]

    return "\n".join(lines).rstrip()


def fetch_outcomes_to_json(outcomes: list[FetchOutcome], *, max_chars: int = 0) -> str:
    """JSON view of a batch fetch."""
    payload: list[dict[str, Any]] = []
    for outcome in outcomes:
        entry: dict[str, Any] = {"url": outcome.url, "ok": outcome.ok}
        if outcome.response is not None:
            entry["title"] = outcome.response.title
            entry["content"] = truncate(outcome.response.content, max_chars)
        else:
            entry["error"] = outcome.error
        payload.append(entry)
    return to_json({"results": payload})


def to_json(payload: Any) -> str:
    """Pretty JSON with real Unicode, for `response_format="json"`."""
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    return json.dumps(payload, ensure_ascii=False, indent=2)
