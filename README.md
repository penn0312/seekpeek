# seekpeek

> Give your AI agents eyes on the live web — search it, or peek at any page.

**seekpeek** is a [Model Context Protocol](https://modelcontextprotocol.io) server that wraps the
**DeepSeek Search & Fetch API** (`/v1/tools/search`, `/v1/tools/fetch`) into tools any MCP host can call:
Claude Code, Claude Desktop, Cursor, VS Code, Codex, Cline, Windsurf, and the rest.

[English](README.md) · [简体中文](README.zh-CN.md)

[![CI](https://github.com/penn0312/seekpeek/actions/workflows/ci.yml/badge.svg)](https://github.com/penn0312/seekpeek/actions/workflows/ci.yml)

---

## Why

Most agents are frozen at their training cutoff. DeepSeek's Search API is a good, cheap way to thaw them
out: a self-built index, structured results carrying a relevance **rerank score** and an **authority**
rating, a short `snippet` for humans and a long `summary` written for models.

`seekpeek` turns that into tools a model can actually use well, and handles the boring parts:

| The boring part | What seekpeek does |
| --- | --- |
| Context blowups | Caps page bodies with a visible `[truncated: …]` marker instead of silently dumping 100k characters |
| Random 429s | Client-side rate limiting (the account allows 5 QPS) plus exponential backoff with jitter on `408`/`429`/`5xx` |
| Cryptic failures | `401`/`402`/`403`/`404` become paragraphs the model can read and act on |
| N+1 round trips | `fetch_many` fetches up to ten pages concurrently behind a single tool call |

## Features

- 🔎 **`search`** — web search with the full DeepSeek query syntax (`site:`, `-site:`, `lang:`, `after:`, `before:`, phrases, exclusions)
- 📄 **`fetch`** — any URL → clean Markdown
- 📚 **`fetch_many`** — batch fetching, concurrency-limited, per-URL error reporting
- 🧠 **Model-friendly output** — Markdown by default, raw JSON on request; prefers the long `summary` over the human `snippet`
- 🧭 **Guided prompt + resource** — a `web-research` prompt and a [`seekpeek://search-syntax`](src/seekpeek/server.py) cheatsheet for hosts that support them
- 🖥️ **Two transports** — `stdio` for local hosts, `streamable-http` for shared or remote deployments
- 🧰 **Also a CLI** — `seekpeek search "…"`, `seekpeek fetch <url>`, `seekpeek doctor`

## Quick start

You need a DeepSeek API key: <https://platform.deepseek.com/api_keys>.

### 1. Add it to your MCP host

**Claude Code**

```bash
claude mcp add seekpeek --env DEEPSEEK_API_KEY=sk-your-key -- uvx seekpeek
```

**Any JSON-configured host** (Claude Desktop, Cursor, VS Code, Cline, Windsurf, …) — add this to the
`mcpServers` block:

```json
{
  "mcpServers": {
    "seekpeek": {
      "command": "uvx",
      "args": ["seekpeek"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-your-key"
      }
    }
  }
}
```

Not on PyPI yet? Install straight from Git instead:

```json
{
  "mcpServers": {
    "seekpeek": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/penn0312/seekpeek", "seekpeek"],
      "env": { "DEEPSEEK_API_KEY": "sk-your-key" }
    }
  }
}
```

### 2. Ask something that needs the web

> "Search for the latest DeepSeek model release and summarise what changed."

The model calls `seekpeek` → `search`, then `fetch` (or `fetch_many`) whichever pages it wants in full.

### Remote / HTTP deployment

```bash
DEEPSEEK_API_KEY=sk-... seekpeek --transport streamable-http --host 127.0.0.1 --port 8765
# MCP endpoint: http://127.0.0.1:8765/mcp
```

> Exposed beyond localhost? Put it behind a reverse proxy with authentication — seekpeek itself is
> unauthenticated and spends your DeepSeek quota.

## Tools

### `search`

```
search(
  query: str,                            # 1-128 chars, supports the operators below
  count: int = 10,                       # 1-50
  ori_question: str | None = None,       # the user's original question, sharpens intent matching
  need_content: bool = False,            # also return full page text
  localization: str | None = None,       # IP address used to prefer localised results
  max_content_chars: int = 12000,        # per-result body cap, 0 = unlimited
  response_format: "markdown" | "json" = "markdown",
) -> str
```

Real output (`need_content=true`, bodies capped at 200 chars for this sample):

````markdown
# seekpeek · search

Query: `DeepSeek 模型 site:github.com OR site:huggingface.co lang:zh` — 2 results, bodies capped at 200 chars each

## 1. DeepSeek V4 模型发布

- URL: https://example.com/deepseek-v4
- Relevance: 2.4/3.0 (high-quality) · Authority: 3/4 (authoritative) · Published: 2026-06-01 · Site: Example News

DeepSeek 发布了 V4 模型。此次发布改进了工具调用与长上下文能力，适合 Agent 工作流。

**Page content** (from `https://example.com/deepseek-v4`):

DeepSeek V4 现已正式上线……DeepSeek V4 现已正式上线……DeepSeek V4 现已正式上线……DeepSeek V4 现已正式上线……

… [truncated: 600 more characters]

## 2. Some older thread

- URL: https://forum.example.org/t/123
- Relevance: 0.8/3.0 · Authority: 1/4 · Published: 2024-11-02

A discussion from last year.
````

Every result is ranked. `rerank_score` ≥ 1.5 is flagged **high-quality**, `authority` ≥ 3 is flagged
**authoritative**, so the model can triage before spending context on a page. The long `summary` (written
for models) is used when available, `snippet` only as a fallback.

### `fetch`

```
fetch(url: str, max_chars: int = 12000, response_format: "markdown" | "json" = "markdown") -> str
```

Returns the page title plus its Markdown body. `max_chars=0` disables truncation. Bare hosts are accepted
(`example.com/doc`), and the URL is validated before it costs you a request.

### `fetch_many`

```
fetch_many(urls: list[str], max_chars: int = 12000, response_format: … ) -> str
```

Up to 10 URLs, fetched concurrently under the shared rate limiter. One dead link never fails the call:

```markdown
# seekpeek · fetch_many (1/2 succeeded)

## 1. Example article

- URL: https://example.com/article

# Example article

Article content.

## 2. https://broken.example.com/article

Failed: No usable content for https://broken.example.com/article (404). The page may be unreachable,
blocked by the site, served as an unsupported content type, or contain no extractable Markdown.
```

### Prompt & resource

- Prompt **`web-research`** — takes a `topic` and a `depth` (`quick` / `standard` / `deep`), and returns a
  research brief that teaches the model the query syntax and when to fetch.
- Resource **`seekpeek://search-syntax`** — the operator cheatsheet and result-field thresholds as Markdown.

## Search syntax

Passed straight through to DeepSeek; combine as many operators as you like in one query.

| Operator | Example | Effect |
| --- | --- | --- |
| `site:` | `DeepSeek site:github.com` | Restrict to a site. Several: `site:github.com OR site:huggingface.co` |
| `-site:` | `DeepSeek -site:example.com` | Exclude sites |
| `-` | `DeepSeek -广告` | Exclude pages containing a keyword |
| `-"…"` | `DeepSeek -"release notes"` | Exclude an exact phrase |
| `"…"` | `DeepSeek "精确短语"` | Exact phrase match |
| `lang:` | `DeepSeek lang:zh,en` | Restrict languages |
| `after:` | `DeepSeek after:2025-01-01` | Published after a date (`after:2025` works too) |
| `before:` | `DeepSeek before:2026-06` | Published before a date |

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | — | **Required.** Your DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API base URL (proxy or gateway) |
| `SEEKPEEK_TIMEOUT` | `60` | Per-request timeout in seconds |
| `SEEKPEEK_MAX_RETRIES` | `2` | Retries on `408` / `429` / `5xx` |
| `SEEKPEEK_SEARCH_QPS` | `5` | Client-side search rate limit |
| `SEEKPEEK_FETCH_QPS` | `5` | Client-side fetch rate limit |
| `SEEKPEEK_ENV_FILE` | — | Extra `.env` file to read, highest priority |

### Where to put the API key

Any of these works; the first one that is set wins:

| Where | Best for | Notes |
| --- | --- | --- |
| `~/.config/seekpeek/.env` | Everything | One file, picked up whatever directory the host launches from |
| `./.env` in your working directory | Local development | Searched from the current directory upwards |
| `SEEKPEEK_ENV_FILE=/path/to/file` | Multiple keys / CI | Explicit override |
| Your host's `env` block or `export` | Hosts that manage secrets | Real environment variables always win over `.env` |

```bash
# one-time setup
mkdir -p ~/.config/seekpeek
echo 'DEEPSEEK_API_KEY=sk-your-key' > ~/.config/seekpeek/.env
chmod 600 ~/.config/seekpeek/.env
seekpeek doctor
```

`seekpeek doctor` prints the effective configuration and probes the API:

```console
$ seekpeek doctor
seekpeek 0.1.0 — configuration
  DEEPSEEK_API_KEY      sk-abc…f123
  DEEPSEEK_BASE_URL     https://api.deepseek.com
  SEEKPEEK_TIMEOUT      60s
  SEEKPEEK_MAX_RETRIES  2
  SEEKPEEK_SEARCH_QPS   5
  SEEKPEEK_FETCH_QPS    5

• Probing https://api.deepseek.com/v1/tools/search …
✓ Search works — 1 result billed. Top hit: …
```

## CLI

```bash
uvx seekpeek                                  # stdio MCP server (what hosts launch)
uvx seekpeek --transport streamable-http      # HTTP MCP server on 127.0.0.1:8765/mcp
uvx seekpeek doctor [--offline]               # verify key, endpoint and limits
uvx seekpeek search "DeepSeek V4 site:github.com" --count 5 --content
uvx seekpeek search "latest MCP news" --json | jq '.results[].url'
uvx seekpeek fetch https://api-docs.deepseek.com/zh-cn/guides/search
uvx seekpeek fetch example.com/docs api-docs.deepseek.com   # several URLs at once
```

## Cost & limits

Search and Fetch are billed **per successful request** (¥5 per 1,000 requests at the time of writing) —
`count` and `need_content` do not change the price. Failed calls, empty result sets and timeouts are free.
Both endpoints allow **5 QPS per account**, which seekpeek mirrors client-side.

See the [official guide](https://api-docs.deepseek.com/guides/search) for the current numbers.

## Development

```bash
git clone https://github.com/penn0312/seekpeek && cd seekpeek
uv sync                        # install runtime + dev dependencies
cp .env.example .env           # add your key
uv run seekpeek doctor
uv run pytest -q               # 80 tests: unit, wire-protocol and stdio end-to-end
uv run ruff check . && uv run ruff format --check .
```

The test suite spins up a mock DeepSeek endpoint and drives the real server over stdio, so it needs no
network and no key. A live smoke test is available on request:

```bash
SEEKPEEK_E2E_LIVE=1 uv run pytest -k live   # spends one search request
```

Layout:

```
src/seekpeek/
├── cli.py         # argparse entry point: server, doctor, search, fetch
├── client.py      # DeepSeek HTTP client: auth, rate limiting, retries, URL handling
├── config.py      # environment-driven Settings
├── errors.py      # error types + the human/model-readable `describe()`
├── formatting.py  # Markdown / JSON renderers (pure functions)
├── models.py      # pydantic response models, FetchOutcome
└── server.py      # MCPServer: tools, prompt, resource, instructions
```

Pull requests are welcome — especially new hosts' config snippets, extra search-syntax examples, and
translations of the README.

## Disclaimer

seekpeek is an independent project and is not affiliated with DeepSeek. It reads public pages through the
DeepSeek API, so what you point it at is subject to DeepSeek's terms and to the target site's policies.

## License

[MIT](LICENSE)
