# Usage Examples

## Example 1: Standard search (backward compatible)

User: "找 hipblaslt 中 page fault 相关的 issue"

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results.md -q
```

## Example 2: Multi-type parallel search

User: "全面搜索 hipblaslt 的 page fault 问题"

AI auto-selects `search_types: ["issues", "prs", "code", "commits", "discussions"]`.

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_full.json --output results.md -q
```

## Example 3: Smart search with AI review

User: "找 page fault 相关问题，要准确"

```bash
# Round 1: search + intermediate JSON
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results_r1.md \
  --intermediate-json intermediate.json \
  --cache-file .search_cache.json -q

# AI reads intermediate.json, writes overrides
# ...

# Round 2: apply AI overrides + optional new queries
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results_final.md \
  --cache-file .search_cache.json --resume \
  --score-overrides ai_overrides.json \
  --append-queries "\"new discovered term\"" -q
```

## Example 4: Issues + PRs with comments

User: "搜索 ROCm/rocm-libraries 中 OOM 相关的 issue 和 PR，需要搜索评论"

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_oom.json --search-comments --output oom_results.md -q
```

## Example 5: Cross-reference tracking

User: "这个 page fault 的 bug 被修过吗？找一下相关的 PR 和 commit"

AI auto-selects `search_types: ["issues", "prs", "commits"]`. Cross-reference engine auto-links.

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_pagefault.json --output results.md -q
```

Report output includes:
```
### 交叉引用
Issue #265 ← PR #3734 (fixes)
Issue #340 ← Commit abc1234 (ref)
```

## Example 6: Noise upgrade to Smart

User: "搜 page fault 的结果噪声太多，帮我过滤一下"

AI detects "噪声太多" → enables Smart mode:

```bash
# Round 1: generate intermediate JSON
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results_r1.md \
  --intermediate-json intermediate.json \
  --cache-file .search_cache.json -q

# AI reviews intermediate.json, writes overrides (ai_score: 0 for noise)
# ...

# Round 2: apply overrides
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_config.json --output results_clean.md \
  --cache-file .search_cache.json --resume \
  --score-overrides ai_overrides.json -q
```

## Example 7: Code-only search

User: "有没有处理 hipblaslt 分页错误的代码"

AI auto-selects `search_types: ["code"]`. Requires `GITHUB_TOKEN`.

```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config search_code.json --output code_results.md -q
```

## Example 8: Cross-topic correlation analysis

User: "我想了解 expert scheduling mode 和 page fault 的关联"

AI runs two separate searches, then cross-references the results to find shared Issues:

**Search 1** — ESM topic (issues + prs + commits):
```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config esm_config.json --output esm_report.md \
  --cache-file esm_cache.json -q
```

**Search 2** — page fault topic (issues + prs + commits):
```bash
python .cursor/skills/github-issue-search/scripts/search_github.py \
  --config pagefault_config.json --output pagefault_report.md \
  --cache-file pagefault_cache.json -q
```

**Cross-topic finding**: Issue #3211 appears in both reports — matched `expert scheduling mode` (7.6) in ESM search, matched `illegal memory access` (10.0) in page fault search. This reveals the causal chain:

```
Expert Scheduling Mode enabled (ROCm 7.0, hipblaslt)
  → AMD KFD lacks support on gfx12
    → "HIP error: an illegal memory access was encountered"
      → PR #3164 disables ESM for gfx12 (merged)
        → Issue #3211 tracks conditional re-enablement (open)
```

Each search independently finds the same issue from a different angle (symptom vs root cause), demonstrating how multi-topic searches can uncover deeper causal relationships.
