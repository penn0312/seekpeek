"""Response models for the DeepSeek Search and Fetch endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class SearchResult(BaseModel):
    """One row of `/v1/tools/search`."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    url: str = ""
    snippet: str = ""
    summary: str = ""
    content: str = ""
    site_name: str | None = None
    site_icon: str | None = None
    date: str | None = None
    rerank_score: float | None = Field(default=None, description="0–3 relevance/quality score; ≥1.5 is a strong hit")
    authority: int = Field(default=-1, description="0–4 site authority; -1 unknown, ≥3 highly authoritative")

    @property
    def best_text(self) -> str:
        """The most useful description we have: long summary, else snippet."""
        return (self.summary or self.snippet).strip()

    @property
    def published(self) -> str | None:
        """Just the date part of the RFC 3339 timestamp."""
        if not self.date:
            return None
        return self.date.split("T", 1)[0] if "T" in self.date else self.date


class SearchResponse(BaseModel):
    """Body of a successful search."""

    model_config = ConfigDict(extra="ignore")

    results: list[SearchResult] = Field(default_factory=list)


class FetchResponse(BaseModel):
    """Body of a successful fetch."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    content: str = ""


@dataclass(slots=True)
class FetchOutcome:
    """One page's fate inside a batch fetch: either a response or a readable error."""

    url: str
    response: FetchResponse | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.response is not None
