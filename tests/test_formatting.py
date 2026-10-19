from __future__ import annotations

import json

import pytest

from seekpeek.formatting import (
    TRUNCATION_NOTE,
    fetch_outcomes_to_json,
    format_fetch_many_markdown,
    format_fetch_markdown,
    format_search_markdown,
    to_json,
    truncate,
)
from seekpeek.models import FetchOutcome, FetchResponse, SearchResponse

# --------------------------------------------------------------------- truncate


def test_truncate_leaves_short_text_alone() -> None:
    assert truncate("hello", 10) == "hello"


def test_truncate_marks_what_it_dropped() -> None:
    result = truncate("x" * 20, 5)

    assert result.startswith("xxxxx")
    assert TRUNCATION_NOTE.format(removed=15) in result


def test_truncate_zero_disables_the_cap() -> None:
    text = "y" * 100
    assert truncate(text, 0) == text


# ------------------------------------------------------------------ search output


def _search_response(**overrides) -> SearchResponse:
    payload = {
        "results": [
            {
                "rerank_score": 2.4,
                "title": "DeepSeek V4 模型发布",
                "url": "https://example.com/v4",
                "snippet": "短摘要",
                "summary": "长摘要，写给模型看的。",
                "content": "正文" * 20,
                "site_name": "Example News",
                "date": "2026-06-01T00:00:00+00:00",
                "authority": 3,
            }
        ]
    }
    payload.update(overrides)
    return SearchResponse.model_validate(payload)


def test_search_markdown_reports_scores_and_summary() -> None:
    markdown = format_search_markdown(_search_response(), query="DeepSeek V4")

    assert markdown.startswith("# seekpeek · search")
    assert "Query: `DeepSeek V4` — 1 result" in markdown
    assert "## 1. DeepSeek V4 模型发布" in markdown
    assert "Relevance: 2.4/3.0 (high-quality)" in markdown
    assert "Authority: 3/4 (authoritative)" in markdown
    assert "Published: 2026-06-01" in markdown
    assert "长摘要，写给模型看的。" in markdown
    assert "短摘要" not in markdown  # the model-oriented summary wins
    assert "正文" not in markdown  # unless need_content is set


def test_search_markdown_falls_back_to_snippet_and_marks_unknowns() -> None:
    response = _search_response(results=[{"title": "t", "url": "https://x.dev", "snippet": "only snippet"}])
    markdown = format_search_markdown(response, query="q")

    assert "only snippet" in markdown
    assert "Relevance:" not in markdown
    assert "Authority:" not in markdown


def test_search_markdown_includes_capped_content_on_request() -> None:
    markdown = format_search_markdown(_search_response(), query="q", need_content=True, max_content_chars=10)

    assert "**Page content**" in markdown
    assert TRUNCATION_NOTE.format(removed=30) in markdown
    assert "bodies capped at 10 chars each" in markdown


def test_search_markdown_explains_empty_results() -> None:
    markdown = format_search_markdown(SearchResponse(), query="nothing")

    assert "No results for `nothing`" in markdown
    assert "site:" in markdown  # hints at the usual cause


def test_search_json_is_parseable() -> None:
    payload = json.loads(to_json(_search_response()))

    assert payload["results"][0]["url"] == "https://example.com/v4"


def test_weak_results_are_not_called_high_quality() -> None:
    response = _search_response(results=[{"title": "t", "url": "https://x.dev", "rerank_score": 0.4, "authority": 1}])
    markdown = format_search_markdown(response, query="q")

    assert "Relevance: 0.4/3.0" in markdown
    assert "high-quality" not in markdown
    assert "Authority: 1/4" in markdown


# ------------------------------------------------------------------- fetch output


def test_fetch_markdown_includes_source_and_body() -> None:
    markdown = format_fetch_markdown(
        FetchResponse(title="Example", content="# Body\n\nText."), url="https://example.com/a"
    )

    assert markdown.startswith("# Example")
    assert "Source: https://example.com/a" in markdown
    assert "Text." in markdown


def test_fetch_markdown_handles_missing_title_and_body() -> None:
    markdown = format_fetch_markdown(FetchResponse(), url="https://example.com/a")

    assert "(untitled page)" in markdown
    assert "(the page had no text)" in markdown


def test_fetch_many_markdown_mixes_success_and_failure() -> None:
    outcomes = [
        FetchOutcome(url="https://a.dev", response=FetchResponse(title="A", content="alpha")),
        FetchOutcome(url="https://b.dev", error="No usable content (404)"),
    ]
    markdown = format_fetch_many_markdown(outcomes)

    assert "fetch_many (1/2 succeeded)" in markdown
    assert "## 1. A" in markdown
    assert "alpha" in markdown
    assert "## 2. https://b.dev" in markdown
    assert "Failed: No usable content (404)" in markdown


def test_fetch_many_json_lists_errors() -> None:
    outcomes = [
        FetchOutcome(url="https://a.dev", response=FetchResponse(title="A", content="alpha")),
        FetchOutcome(url="https://b.dev", error="boom"),
    ]
    payload = json.loads(fetch_outcomes_to_json(outcomes))

    assert payload["results"][0] == {"url": "https://a.dev", "ok": True, "title": "A", "content": "alpha"}
    assert payload["results"][1] == {"url": "https://b.dev", "ok": False, "error": "boom"}


@pytest.mark.parametrize("limit", [-1, 0])
def test_fetch_many_json_respects_zero_cap(limit: int) -> None:
    outcomes = [FetchOutcome(url="https://a.dev", response=FetchResponse(title="A", content="z" * 50))]
    payload = json.loads(fetch_outcomes_to_json(outcomes, max_chars=limit))

    assert payload["results"][0]["content"] == "z" * 50
