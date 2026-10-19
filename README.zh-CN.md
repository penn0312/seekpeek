# seekpeek

> 给你的 AI Agent 装上眼睛：搜得到全网，也看得进单页。

**seekpeek** 是一个 [Model Context Protocol](https://modelcontextprotocol.io) 服务器，把
**DeepSeek 网页搜索与抓取 API**（`/v1/tools/search`、`/v1/tools/fetch`）包装成任意 MCP 宿主都能调用的工具：
Claude Code、Claude Desktop、Cursor、VS Code、Codex、Cline、Windsurf 等等。

[English](README.md) · [简体中文](README.zh-CN.md)

[![CI](https://github.com/penn0312/seekpeek/actions/workflows/ci.yml/badge.svg)](https://github.com/penn0312/seekpeek/actions/workflows/ci.yml)

---

## 为什么需要它

大多数 Agent 被困在训练截止日期里。DeepSeek 的搜索接口是性价比很高的解冻方案：自研索引、结构化结果，
每条结果都带**重排序分数**和**权威性评级**，既有给人看的 `snippet`，也有专门写给模型读的 `summary`。

seekpeek 把这些变成一个模型真正用得好的工具，顺手处理掉所有琐事：

| 琐事 | seekpeek 的做法 |
| --- | --- |
| 上下文被撑爆 | 正文超长时截断，并明确标注 `… [truncated: …]`，而不是悄悄塞进十万字 |
| 时不时撞上 429 | 客户端限速（账号额度 5 QPS）+ `408`/`429`/`5xx` 指数退避重试（带抖动） |
| 报错看不懂 | `401`/`402`/`403`/`404` 都翻译成模型能读懂、能据以行动的一段话 |
| 一次抓一页太慢 | `fetch_many` 一次并发抓最多 10 页 |

## 特性

- 🔎 **`search`** — 支持 DeepSeek 全部检索语法（`site:`、`-site:`、`lang:`、`after:`、`before:`、精确短语、排除词）
- 📄 **`fetch`** — 任意 URL → 干净的 Markdown
- 📚 **`fetch_many`** — 批量抓取，并发受控，逐条报告失败原因
- 🧠 **对模型友好** — 默认输出 Markdown，需要时可要 JSON；优先用写给模型的 `summary` 而非 `snippet`
- 🧭 **prompt + resource** — 提供 `web-research` 提示词和 `seekpeek://search-syntax` 语法速查
- 🖥️ **两种传输** — 本地宿主用 `stdio`，共享/远程部署用 `streamable-http`
- 🧰 **同时是命令行工具** — `seekpeek search "…"`、`seekpeek fetch <url>`、`seekpeek doctor`

## 快速开始

需要一个 DeepSeek API Key：<https://platform.deepseek.com/api_keys>。

### 1. 接入你的 MCP 宿主

**Claude Code**

```bash
claude mcp add seekpeek --env DEEPSEEK_API_KEY=sk-your-key -- uvx seekpeek
```

**其他用 JSON 配置的宿主**（Claude Desktop、Cursor、VS Code、Cline、Windsurf……）— 在 `mcpServers` 里加上：

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

还没发布到 PyPI？直接装 Git 版本：

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

### 2. 提一个需要联网的问题

> “搜一下 DeepSeek 最新发布，总结有哪些变化。”

模型会先调 `search`，再用 `fetch`（或 `fetch_many`）把需要的页面读全。

### 远程 / HTTP 部署

```bash
DEEPSEEK_API_KEY=sk-... seekpeek --transport streamable-http --host 127.0.0.1 --port 8765
# MCP 端点：http://127.0.0.1:8765/mcp
```

> 如果不止本机访问，请在反向代理层加上鉴权——seekpeek 自身不带认证，而它花的是你的 DeepSeek 额度。

## 工具

### `search`

```
search(
  query: str,                            # 1-128 字符，支持下面的检索语法
  count: int = 10,                       # 1-50
  ori_question: str | None = None,       # 用户原始问题，帮助理解搜索意图
  need_content: bool = False,            # 同时返回网页正文
  localization: str | None = None,       # 用于优先返回本地化结果的 IP 地址
  max_content_chars: int = 12000,        # 单条正文上限，0 表示不限
  response_format: "markdown" | "json" = "markdown",
) -> str
```

真实输出（`need_content=true`，示例中把正文压到 200 字符）：

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

每条结果都经过重排序。`rerank_score` ≥ 1.5 会标注 **high-quality**，`authority` ≥ 3 会标注
**authoritative**，模型可以先筛一遍再决定要不要花上下文去读网页。优先使用写给模型的 `summary`，
只有缺失时才退回 `snippet`。

### `fetch`

```
fetch(url: str, max_chars: int = 12000, response_format: "markdown" | "json" = "markdown") -> str
```

返回页面标题和 Markdown 正文。`max_chars=0` 关闭截断。支持省略协议的写法（`example.com/doc`），
URL 会先做校验，不会白白花掉一次请求。

### `fetch_many`

```
fetch_many(urls: list[str], max_chars: int = 12000, response_format: … ) -> str
```

最多 10 个 URL，在统一限速下并发抓取。某条链接挂掉不会让整次调用失败：

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

### Prompt 与 Resource

- Prompt **`web-research`** — 传入 `topic` 和 `depth`（`quick` / `standard` / `deep`），返回一份研究提纲，
  顺便把检索语法和抓取时机教给模型。
- Resource **`seekpeek://search-syntax`** — 操作符速查表 + 结果字段阈值说明（Markdown）。

## 检索语法

原样透传给 DeepSeek，一个 query 里可以自由组合多个操作符。

| 操作符 | 示例 | 作用 |
| --- | --- | --- |
| `site:` | `DeepSeek site:github.com` | 限定站点，多个用 `site:github.com OR site:huggingface.co` |
| `-site:` | `DeepSeek -site:example.com` | 排除站点 |
| `-` | `DeepSeek -广告` | 排除含指定关键词的网页 |
| `-"…"` | `DeepSeek -"release notes"` | 排除指定短语 |
| `"…"` | `DeepSeek "精确短语"` | 精确匹配短语 |
| `lang:` | `DeepSeek lang:zh,en` | 限定语言 |
| `after:` | `DeepSeek after:2025-01-01` | 发布时间晚于该日期（`after:2025` 也可以） |
| `before:` | `DeepSeek before:2026-06` | 发布时间早于该日期 |

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | — | **必填**，你的 DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址（走代理或网关时改这里） |
| `SEEKPEEK_TIMEOUT` | `60` | 单次请求超时（秒） |
| `SEEKPEEK_MAX_RETRIES` | `2` | `408` / `429` / `5xx` 的重试次数 |
| `SEEKPEEK_SEARCH_QPS` | `5` | 客户端搜索限速 |
| `SEEKPEEK_FETCH_QPS` | `5` | 客户端抓取限速 |

`seekpeek doctor` 会打印生效配置并探活一次：

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

## 命令行

```bash
uvx seekpeek                                  # stdio MCP 服务（宿主启动用的就是它）
uvx seekpeek --transport streamable-http      # HTTP MCP 服务，监听 127.0.0.1:8765/mcp
uvx seekpeek doctor [--offline]               # 检查 Key、地址和限速配置
uvx seekpeek search "DeepSeek V4 site:github.com" --count 5 --content
uvx seekpeek search "最新 MCP 动态" --json | jq '.results[].url'
uvx seekpeek fetch https://api-docs.deepseek.com/zh-cn/guides/search
uvx seekpeek fetch example.com/docs api-docs.deepseek.com   # 一次抓多个
```

## 计费与限速

Search 与 Fetch 都按**成功请求次数**计费（撰写时为每 1,000 次 5 元），`count` 和 `need_content` 都不影响单价。
请求失败、搜索无结果、超时都不计费。两者各自限速 **5 QPS（账号级）**，seekpeek 在客户端做了同样的限制。

最新数字请看[官方指南](https://api-docs.deepseek.com/zh-cn/guides/search)。

## 开发

```bash
git clone https://github.com/penn0312/seekpeek && cd seekpeek
uv sync                        # 安装运行时与开发依赖
cp .env.example .env           # 填入你的 Key
uv run seekpeek doctor
uv run pytest -q               # 80 个测试：单元、协议、stdio 端到端
uv run ruff check . && uv run ruff format --check .
```

测试套件会自己起一个假的 DeepSeek 端点，并通过 stdio 驱动真实的服务器进程，因此不联网、不需要 Key。
想做真实调用时可以开一次真机冒烟测试：

```bash
SEEKPEEK_E2E_LIVE=1 uv run pytest -k live   # 会消耗一次搜索请求
```

目录结构：

```
src/seekpeek/
├── cli.py         # argparse 入口：服务、doctor、search、fetch
├── client.py      # DeepSeek HTTP 客户端：鉴权、限速、重试、URL 处理
├── config.py      # 基于环境变量的 Settings
├── errors.py      # 异常类型 + 面向人和模型的 describe()
├── formatting.py  # Markdown / JSON 渲染（纯函数）
├── models.py      # pydantic 响应模型、FetchOutcome
└── server.py      # MCPServer：工具、prompt、resource、instructions
```

欢迎提 PR——尤其是各家宿主的配置片段、更多检索语法示例，以及 README 的其他语言版本。

## 免责声明

seekpeek 是独立项目，与 DeepSeek 官方无关。它通过 DeepSeek API 读取公开网页，因此你指向的内容受
DeepSeek 服务条款与目标站点策略的约束。

## 许可证

[MIT](LICENSE)
