# Advanced Reference

## Architecture

```
scripts/
├── search_github.py            ← Unified entry point (v6: parallel + smart)
├── search_github_issues.py     ← Backward-compatible wrapper (v4)
├── core/
│   ├── api_client.py           ← GitHub API client (REST + GraphQL, rate limit, retry)
│   ├── models.py               ← Data models (Issue, PR, Code, Commit, Discussion, SearchConfig)
│   ├── scorer.py               ← Keyword relevance scoring engine (5 content types)
│   ├── cache.py                ← JSON cache for incremental search (all 5 types, thread-safe)
│   ├── report.py               ← Unified report generation (Markdown + JSON, all types)
│   ├── cross_ref.py            ← Cross-reference engine (Issue↔PR↔Commit links + graph)
│   └── query_builder.py        ← Auto query builder + seed synonym merger (v6.1)
├── data/
│   └── seed_synonyms.json      ← 种子同义词库 (12 个通用主题, ~150 个关键词)
├── searchers/
│   ├── base.py                 ← Abstract base searcher
│   ├── issue.py                ← Issue searcher (2-phase: search + comments)
│   ├── pr.py                   ← PR searcher (review comments + changed files)
│   ├── code.py                 ← Code searcher (text-match header for snippets)
│   ├── commit.py               ← Commit searcher (cloak-preview header)
│   └── discussion.py           ← Discussion searcher (via api_client.graphql())
└── tests/                      ← 100+ unit tests
```

## Query Auto-Build (v6.1)

When `config.queries` is empty or absent, the script auto-generates search queries from keywords via `core/query_builder.py`.

### Algorithm (5 rounds, interleaved)

| Round | Strategy | With component | Without component | Cap |
|-------|----------|---------------|-------------------|-----|
| R1 | Each **high** keyword individually | `{component} "page fault"` | `"page fault"` | 5 |
| R3 | **Medium** keyword pairs | `{component} sigsegv OR sigbus` | `sigsegv OR sigbus` | 4 |
| R2 | **High** OR pairs (broader sweep) | *(skip — same as R1)* | `"page fault" OR "segfault"` | 3 |
| R4 | **Cross-tier** high + medium | `"page fault" OR sigsegv OR segfault` | (same) | 2 |
| R5 | **Low** keyword groups of 3 | `{component} "core dump" OR coredump OR abort` | (same) | ∞ |

Total ≤ 15 queries. Auto-deduplication. R2 only generated when `component` is non-empty (otherwise duplicates R1).

**Interleaving** (v6.2): R1 and R3 are interleaved (zip) rather than sequential:

```
旧顺序: R1 R1 R1 R1 R1 | R3 R3 R3 R3 | R2 R4 R5
新顺序: R1 R3 R1 R3 R1 R3 R1 R3 R1 | R2 | R4 | R5
```

**Why**: If high keywords are "descriptive phrases" that don't match verbatim in issues (e.g., `"tuning no effect"`), R1 queries all return 0. With sequential ordering, this triggers early stopping before R3 gets a chance. With interleaving, R3 queries break the consecutive-zero streak at Q2, Q4, Q6 etc., keeping the search alive.

**Early stopping** (v6.2): Threshold raised from `i > 3` to `i > max(5, nq // 3)`:
- For 15 queries: earliest stop at Q6 (was Q4)
- For 9 queries: earliest stop at Q6 (was Q4)
- Combined with interleaving, medium keywords always get at least 2-3 chances before early stopping can trigger.

### Backward compatibility

If config contains `queries`, they are used as-is. The auto-builder only activates when `queries` is empty/missing.

## Seed Synonym Database (v6.1)

`scripts/data/seed_synonyms.json` — a lightweight (~5KB) curated synonym database covering 12 common topics:

| Topic ID | Trigger examples | Keywords |
|----------|-----------------|----------|
| `memory_fault` | page fault, segfault, illegal memory | 18 |
| `oom` | out of memory, oom, allocation fail | 13 |
| `memory_leak` | memory leak, asan, valgrind | 12 |
| `crash` | crash, core dump, abort | 10 |
| `hang_deadlock` | hang, deadlock, timeout, freeze | 13 |
| `build_error` | build fail, compile error, cmake error | 13 |
| `performance` | performance regression, slowdown | 14 |
| `race_condition` | race condition, data race, tsan | 11 |
| `gpu_error` | gpu hang, gpu reset, device lost | 12 |
| `hip_cuda_error` | hip error, cuda error | 11 |
| `test_failure` | test fail, flaky test | 11 |
| `compatibility` | incompatible, breaking change | 10 |

**Merge logic**: At startup, `config.topic` is matched against each topic's trigger list (substring match, case-insensitive). All matching topics' keywords are merged into `config.keywords_*` (union, no duplicates). Multiple topics can match simultaneously.

**Example**: User provides topic "page fault" with 5 keywords. Seed database matches `memory_fault` and adds 14 more → 19 total keywords.

**Not a replacement for AI**: The seed database provides a reliable baseline. AI synonym expansion (Phase 2) can still add domain-specific, context-sensitive terms that the database doesn't cover.

## Parallel Search (v6)

When `search_types` contains 2+ types, searchers execute in parallel via `ThreadPoolExecutor`. Each searcher uses the shared `GitHubApiClient` (thread-safe). Cache writes are serialized with a lock.

Disable with `--no-parallel` for debugging.

## Smart Multi-Round Search (v6)

The `--intermediate-json` + `--score-overrides` + `--append-queries` flags enable AI-in-the-loop search:

1. **Round 1**: Standard keyword search → intermediate JSON
2. **AI Review**: Read intermediate, filter noise, identify patterns, generate new queries
3. **Round 2**: `--resume --append-queries "new terms"` → refined results with AI overrides

This loop can repeat, but typically 1-2 rounds suffice.

### Intermediate JSON Structure

```json
{
  "version": "v6-intermediate",
  "repo": "owner/repo",
  "component": "hipblaslt",
  "topic": "page fault",
  "instructions": "Review each item. Set ai_score (0-30), ai_label (relevant/noise/borderline).",
  "types": {
    "issues": {
      "total": 42,
      "top": [
        {
          "number": 265, "title": "hipblaslt page fault on MI300X",
          "score": 23.9, "matched_keywords": ["page fault", "sigsegv"],
          "state": "open", "url": "https://...",
          "body_snippet": "First 300 chars of issue body..."
        }
      ],
      "borderline": [
        {"number": 999, "title": "...", "score": 2.5, "matched_keywords": [...], "body_snippet": "..."}
      ]
    },
    "code": {
      "total": 15,
      "top": [
        {"path": "src/hipblaslt/fault_handler.cpp", "score": 8.0, "matched_keywords": [...], "url": "..."}
      ]
    },
    "commits": {
      "total": 8,
      "top": [
        {"sha": "abc1234567", "message": "First 200 chars...", "score": 6.5, "matched_keywords": [...]}
      ]
    }
  }
}
```

**Key fields per item type**:
- Issues/PRs: `number`, `title`, `state`, `url`, `body_snippet` (300 chars)
- Code: `path`, `url` (no body_snippet)
- Commits: `sha` (10 chars), `message` (200 chars)
- All types: `score`, `matched_keywords`
- `top`: sorted by score descending, max 30 items
- `borderline`: score between 1.0 and `--min-score`, max 20 items

## Cross-Reference Engine (v6)

Automatically detects links between Issues, PRs, and Commits across search results.

**Trigger condition**: Auto-runs when user requests ≥2 linkable types in `search_types` (i.e., at least 2 of `issues`, `prs`, `commits`). Based on user config, not result data — even if one type returns 0 results, the engine still runs. No extra API calls — works on already-fetched data.

**Detection patterns**:

| Source | Target | Pattern | Relation |
|--------|--------|---------|----------|
| PR body | Issue | `fixes/closes/resolves #N` | `fixes` (strong) |
| PR body | Issue | `#N` reference | `refs` (weak) |
| PR body/title | PR | `#N` referencing another PR in results | `refs` (PR→PR) |
| PR | Issue | `linked_issues` pre-extracted field | `fixes` |
| Commit msg | Issue | `#N` referencing issue in results | `refs` |
| Commit msg | PR | `(#N)` referencing PR in results | `refs` |
| Issue body | PR | `#N` referencing PR in results | `refs` |

Only links to items that appear in the current search results (avoids false positives).

**Output**: A "交叉引用" section in the Markdown report with multiple tables:

```markdown
### 交叉引用

**统计**: 58 条引用 (Issue↔PR: 3, PR↔PR: 5, Commit→*: 50)

#### Issue ↔ PR 关联
| Issue | 关联 PR | 类型 |
|-------|---------|------|
| #265  | #3734   | fixes |
| #340  | #3801   | ref   |

#### PR → PR 关联
| 来源 PR | 引用 PR | 类型 |
|---------|---------|------|
| #2017   | #1892   | ref  |
| #2647   | #2472   | ref  |

#### Commit 引用
| Commit  | 引用对象     | 类型 |
|---------|-------------|------|
| 2b290f0 | PR #1892    | ref  |
| 3bbf4dd | PR #2393    | ref  |

![交叉引用关系图](cross_ref_graph.png)
```

**Graph visualization** (`cross_ref_graph.png`):
- Auto-generated alongside the report as a PNG file
- **Hub-node filtering**: only shows nodes with ≥2 connections (degree ≥ 2) plus their neighbors, filtering out 1:1 simple links
- **Smart column layout**: source nodes (left) → target nodes (center) → commits (right). PR→PR edges cross columns for visibility.
- **Color coding**: Issue = red/pink, PR source = orange, PR target = green, Commit = blue
- Max 35 nodes for readability; excess nodes are filtered by degree
- CJK font auto-detection for Chinese/Japanese text in labels

## Cache & Incremental Search

`--cache-file` + `--resume`: saves/restores all search type results to a single JSON file. Each type is stored in its own section (issues, prs, code, commits, discussions). Incremental search only fetches new data. Cache is keyed by repo name for validation.

## Logging (v6)

All modules use Python `logging` (logger hierarchy: `gss.*`). Three verbosity levels:
- `--verbose` / `-v`: DEBUG — all API calls, retry details, scoring steps
- *(default)*: INFO — progress, results counts, cache operations
- `--quiet` / `-q`: WARNING — only errors and rate-limit waits

Recommended: use `--quiet` when AI invokes the script to minimize output noise.

## Concurrent Comment Fetching

`--concurrency N`: control parallelism for comment fetching (default auto: 4 with token, 1 without). Applies to both Issue comments and PR review comments.

## Comment Fetch Thresholds

`--comments-low` and `--comments-high` control which items get comment fetching:
- Items scoring **above** `--comments-high` (default 8.0) are skipped (already high relevance)
- Items scoring **below** `--comments-low` (default 3.0) are skipped (too low to bother)
- Only items in between are fetched — this saves API quota

## Backward Compatibility

- `search_github_issues.py` still works (thin wrapper calling `search_github.py`)
- Old config JSON without `search_types` defaults to `["issues"]`
- All v4/v5 CLI args are preserved
- Smart features are opt-in (no flags = v5-equivalent behavior)

## Error Handling

| Error | Symptom | Action |
|-------|---------|--------|
| Rate limit (403) | `API rate limit exceeded` in output | Wait for reset (logged automatically). Suggest user set `GITHUB_TOKEN`. |
| No token for Code/Discussions | `code` or `discussions` returns 0 results or 401 | Inform user: Code and Discussions search requires `GITHUB_TOKEN`. |
| Network timeout | `ConnectionError` or `Timeout` | Retry up to 3 times (built-in). If persistent, check connectivity. |
| Invalid config JSON | `json.JSONDecodeError` | Re-check the config file syntax. Common issue: trailing commas. |
| Empty results | 0 results for all queries | Broaden keywords, reduce filters, check repo name spelling. |
| Cache mismatch | `Cache repo mismatch` warning | Delete old cache file or use a different `--cache-file` path. |

**General rule**: If the script exits with non-zero code, read the error output, fix the issue, and re-run. Do NOT silently skip failures.
