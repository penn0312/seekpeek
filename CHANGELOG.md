# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-10-19

First public release.

### Added

- MCP server (`stdio`, `streamable-http`, `sse`) exposing three tools on top of the DeepSeek
  Search & Fetch API:
  - `search` — ranked web results with the full DeepSeek query syntax, optional page bodies;
  - `fetch` — one page as Markdown, with a configurable character cap;
  - `fetch_many` — up to 10 pages concurrently, per-URL error reporting, progress notifications.
- Markdown rendering tuned for models: relevance/authority thresholds surfaced inline, the long
  `summary` preferred over the human-facing `snippet`, truncation always announced.
- `web-research` prompt and the `seekpeek://search-syntax` resource.
- Async DeepSeek client with bearer auth, client-side QPS limiting, exponential backoff with jitter
  on `408`/`429`/`5xx` (`Retry-After` honoured), and actionable messages for `400`/`401`/`402`/`403`/`404`.
- CLI: serve the server, `doctor` diagnostics, and direct `search` / `fetch` subcommands.
- Configuration through `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `SEEKPEEK_TIMEOUT`,
  `SEEKPEEK_MAX_RETRIES`, `SEEKPEEK_SEARCH_QPS`, `SEEKPEEK_FETCH_QPS`.
- Test suite (80 tests) covering the client, formatters, config, tools, and a full stdio end-to-end
  run against a mock DeepSeek endpoint; plus an opt-in live smoke test.

[Unreleased]: https://github.com/penn0312/seekpeek/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/penn0312/seekpeek/releases/tag/v0.1.0
