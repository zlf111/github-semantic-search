---
name: github-semantic-search
description: >
  Search GitHub content (Issues, PRs, Code, Commits, Discussions) with AI-powered synonym expansion,
  multi-round search, semantic scoring, parallel execution, cross-reference linking, and optional AI re-ranking.
  Use when the user asks to find, search, or collect GitHub content related to a topic, bug, or error.
  Triggers on: "find issues about X", "search PRs related to Y", "find code about Z", "全面搜索 X",
  "这个 bug 被修过吗", "噪声太多 / 误报太多" (Smart mode), "相关 PR / commit 有哪些" (cross-reference),
  "搜一下 X 的讨论", "X error 的解决方案", "search X in repo Y".
---

# GitHub Semantic Search v6

AI-driven GitHub search: synonym expansion → keyword search → relevance scoring → optional AI re-ranking.
5 content types: **Issues, PRs, Code, Commits, Discussions**. Multi-type searches run in parallel.

## Workflow

### Phase 1: Understand & Plan

Extract from user's message:
- **repo**: `owner/repo`. **If not specified, ask the user.** Do not assume a default.
- **component** *(optional)*: Software component (e.g., `hipblaslt`). Default `""` for whole-repo.
- **topic**: What they're looking for (e.g., `page fault`, `memory overflow`)

Auto-select `search_types` (do NOT ask unless ambiguous):

| User intent | search_types | Notes |
|------------|-------------|-------|
| "找 X 的 issue" | `["issues"]` | Default |
| "X 怎么解决的" / "X 的修复" | `["issues", "prs", "commits"]` | Cross-ref auto-links |
| "X 的 fix PR" / "被修过吗" | `["issues", "prs"]` | Cross-ref highlights |
| "有没有处理 X 的代码" | `["code"]` | Requires GITHUB_TOKEN |
| "搜一下 X 的讨论" | `["discussions"]` | Requires GITHUB_TOKEN (GraphQL) |
| "全面搜索 X" / "调研 X" | all 5 types | Parallel by default |
| "噪声太多" / "要准确" | *(keep types)* + Smart | Enable `--intermediate-json` |
| Unspecified | `["issues"]` | Default |

**Smart mode triggers** — enable `--intermediate-json` when:
- User explicitly asks for precision ("要准确", "过滤噪声")
- Multi-type search with 3+ types
- Previous round returned >50% noise
- User says "再精确一下" after initial results

### Phase 2: Expand Synonyms

Generate synonyms along these axes:
1. **Exact synonyms** across terminology systems
2. **Error codes and signals** (e.g., SIGSEGV, signal 11)
3. **Consequences** (page fault → GPU hang, OOM → process killed)
4. **Platform-specific terms** (AMD ROCm vs NVIDIA CUDA)
5. **Abbreviations** (OOM, SEGV, segfault)

Organize into three keyword tiers:

```json
{
  "keywords": {
    "high": ["exact synonyms, +5 each"],
    "medium": ["related terms, +3 each"],
    "low": ["loose associations, +1 each"]
  }
}
```

> **关键词分层原则**：high 关键词决定搜索引擎执行前几条查询的内容。因早停机制的存在，若 high 全 miss，后续 medium/low 查询可能被跳过。按话题类型选择 high 关键词策略：
>
> | 话题类型 | high 放什么 | low 放什么 | 典型例子 |
> |---------|-----------|-----------|---------|
> | **错误消息型** | 错误消息本身、信号名、错误码 | 宽泛后果描述 | page fault → high: `"page fault"`, `"sigsegv"` |
> | **行为描述型** | 涉及的技术术语、配置名、文件名 | 英语行为描述短语 | tuning 无效 → high: `"tuning file"`, `"logic yaml"` ; low: `"tuning no effect"` |
>
> **原则**：high 关键词应是"在 issue 正文中大概率原样出现的字符串"，而非"用户描述问题的自然语言"。

> **种子词库自动补充**：脚本启动时会根据 `topic` 匹配内置种子词库（`scripts/data/seed_synonyms.json`，覆盖 12 个常见主题），自动补充 AI 可能遗漏的基线同义词。AI 无需手动做到面面俱到——尽力扩展即可，种子词库兜底。

For detailed expansion patterns: read `references/synonyms.md`.

### Phase 3: Query Auto-Build (Code-driven)

**查询由代码自动构建**，AI 只需提供关键词，不必手写 `queries`。

脚本在 config 不含 `queries`（或为空）时，自动按 5 轮策略生成查询，**R1 与 R3 交叉编排**：

| 轮次 | 策略 | 示例 |
|------|------|------|
| R1 | 每个 high 关键词独立查询 (含 component) | `hipblaslt "page fault"` |
| R3 | medium 关键词两两 OR 对 (含 component) | `hipblaslt sigsegv OR segfault` |
| R2 | high 关键词 OR 对 (无 component，广撒网) | `"page fault" OR "segmentation fault"` |
| R4 | 跨 tier 组合 high + medium (广覆盖) | `"page fault" OR sigsegv OR segfault` |
| R5 | low 关键词三个一组 OR (含 component) | `hipblaslt "core dump" OR coredump OR abort` |

实际执行顺序：`R1[0] R3[0] R1[1] R3[1] R1[2] R3[2] ... R2 R4 R5`。交叉编排确保即使 high 关键词全 miss，medium 查询仍在前 6 条内得到执行，避免早停误杀。

总查询数 ≤15，自动去重。如需手动查询，仍可在 config 中写 `queries` 字段（向后兼容）。

### Phase 3.5: Assemble Config File

Write `search_config.json` to disk before running.**`queries` 字段可以省略**——脚本会自动从关键词构建。

```json
{
  "repo": "owner/repo",
  "component": "hipblaslt",
  "topic": "page fault",
  "search_types": ["issues", "prs"],
  "filters": {"state": "", "date_from": "", "date_to": ""},
  "exclude_issues": [],
  "keywords": {
    "high": ["page fault", "memory access fault"],
    "medium": ["sigsegv", "segmentation fault"],
    "low": ["gpu hang"]
  }
}
```

Verify: `repo` correct, `search_types` matches intent, all 3 keyword tiers present, `component` is `""` if unspecified. `queries` 仅在需要手动控制查询时提供。

### Phase 4: Run Search

```bash
# Standard search
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results.md -q

# Multi-type + Smart (with intermediate JSON for AI review)
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results.md \
  --intermediate-json intermediate.json \
  --cache-file .search_cache.json -q
```

Key flags:
- `-q` — always when AI runs (suppress progress noise)
- `--intermediate-json` — enable Smart AI review (Phase 5)
- `--cache-file` + `--resume` — incremental/second-round search (Phase 6)
- `--search-comments` — fetch comments for borderline items (recommended for Issues + PRs)
- `--dry-run` — preview queries without executing (user wants to review first)
- `-v` / `--verbose` — debug scoring and API details (when user asks "why wasn't X found")

Full CLI reference with all flags and defaults: read `references/cli.md`.

### Phase 5: AI Review (Smart — optional)

**When to use**: 3+ types, user wants precision, or high noise. **Skip**: simple search with clear results.
**Cost**: ~500-1000 extra tokens per cycle. Typically 1-2 cycles.

Read `intermediate.json`. Each type has `top[]` (max 30, by score) and `borderline[]` (max 20). Items contain `score`, `matched_keywords`, and type-specific fields. For full structure: read `references/advanced.md`.

Review each item:
1. **Filter noise**: keyword matched out of context → `ai_score: 0`
2. **Boost missed relevance**: low score but clearly relevant → increase `ai_score`
3. **Identify patterns**: recurring themes, versions, modules
4. **Discover new keywords**: note for Phase 6

Write overrides and re-generate:

```json
{"overrides": {"issues": {"123": {"ai_score": 18, "ai_label": "relevant"}, "456": {"ai_score": 0, "ai_label": "noise"}}}}
```

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results_smart.md \
  --cache-file .search_cache.json --resume \
  --score-overrides ai_overrides.json -q
```

### Phase 6: Second Round (optional)

**When**: first round missed results or new keywords discovered.

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results_v2.md \
  --cache-file .search_cache.json --resume \
  --append-queries "\"new keyword\"" "\"discovered term\" OR \"variant\"" -q
```

`--resume` loads cached first-round results. `--append-queries` adds new queries without re-running old ones.

### Phase 7: Analyze & Report

Read final output and provide:
1. **Executive Summary** table — keyword hits vs component-only counts per type
2. **Cross-reference links** (v6): "Issue #N → fixed by PR #M → merged in commit abc123"
3. **Cross-reference graph** (v6): auto-generated PNG with hub-node filtering (see below)
4. **Keyword-matched results**: full detail with snippets per type
5. **Patterns**: common versions, recurring modules, temporal clusters
6. **Recommendations**: what to look at first, likely duplicates
7. Component-only results: mention briefly only if relevant

**Cross-reference** — auto-triggers when user requests ≥2 linkable types (`issues`, `prs`, `commits`):

| Link type | Detection | Meaning |
|-----------|-----------|---------|
| PR → Issue (fixes) | `fixes/closes/resolves #N` in PR body | PR explicitly fixes Issue. High confidence. |
| PR → Issue (ref) | `#N` in PR body referencing known Issue | PR mentions Issue, no fix claim. Verify. |
| PR → PR (ref) | `#N` in PR body referencing another PR | One PR references another (e.g., revert, follow-up). |
| Commit → PR/Issue | `(#N)` in commit message | Commit references a PR or Issue in results. |
| Issue → PR (ref) | `#N` in Issue body referencing a PR | Less common; Issue author links to related PR. |

A **directed graph PNG** is auto-generated alongside the report, using hub-node filtering (only nodes with ≥2 connections are shown). Three-column layout: source nodes → target nodes → commits.

## Config Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `repo` | string | yes | `owner/repo` |
| `component` | string | no | Component filter (empty = whole repo) |
| `topic` | string | yes | Human-readable topic |
| `search_types` | list | no | Default `["issues"]`. Options: `issues prs code commits discussions` |
| `filters.state` | string | no | `"open"`, `"closed"`, or `""` (all) |
| `filters.date_from` | string | no | `YYYY-MM-DD` |
| `filters.date_to` | string | no | `YYYY-MM-DD` |
| `exclude_issues` | list | no | Issue numbers to exclude |
| `keywords.high` | list | yes | Exact synonyms (+5 each) |
| `keywords.medium` | list | yes | Related terms (+3 each) |
| `keywords.low` | list | no | Loose associations (+1 each) |
| `queries` | list | no | Search API query templates. **省略时自动从 keywords 构建** |

### search_types options

| Type | API | Token Required | Notes |
|------|-----|---------------|-------|
| `issues` | REST | No | 2-phase with optional comment fetching |
| `prs` | REST | No | Detects merge status + linked issues |
| `code` | REST | **Yes** | File paths + text snippets |
| `commits` | REST | No | Searches commit messages |
| `discussions` | GraphQL | **Yes** | Includes answers + comments |

## Scoring (Summary)

Score = keyword tier weight + positional bonuses + type-specific bonuses.

- **Keyword tiers**: high +5, medium +3, low +1. Title bonus +2/kw. Frequency +0.3/extra (cap +2).
- **Partial match**: For 3+ word keywords, if the first N-1 words match (e.g., "memory access" from "memory access fault"), score = tier weight × 0.6, title bonus reduced to +1.
- **Component**: body +2, label +3 (Issues/PRs); path +3 (Code).
- **Type bonuses**: PR merged +2, linked issue +1.5; Code path +1/kw; Commit summary +1.5; Discussion answer +1/kw.
- **AI Override (Smart)**: `--score-overrides` replaces machine scores (0-30).

Full scoring rules: read `references/scoring.md`.

## Token Reminder

If rate limiting occurs, remind the user:

> **GITHUB_TOKEN 可以大幅提速 (免费)**
> - 生成: https://github.com/settings/tokens (不需要勾选权限)
> - 设置: `$env:GITHUB_TOKEN = 'ghp_xxx'` (Win) / `export GITHUB_TOKEN=ghp_xxx` (Linux)
> - 效果: Search 10→30/min, REST 60→5000/hr

## References

Load on-demand when needed. All files are in `references/` under the skill directory.

- **`references/cli.md`** — Full CLI flag tables (7 categories). Read when you need exact flag names, defaults, or less common options.
- **`references/scoring.md`** — Detailed scoring rules per content type. Read when user asks about scoring or you need to verify values.
- **`references/examples.md`** — 8 usage examples with trigger phrases. Read for pattern-matching on unusual requests (includes cross-topic correlation analysis).
- **`references/advanced.md`** — Parallel search, Smart details, cross-ref engine, cache, logging, error handling, architecture. Read when troubleshooting or user asks about internals.
- **`references/synonyms.md`** — Synonym expansion guide with worked examples (page fault, perf regression, build failure). Read for complex synonym generation.
