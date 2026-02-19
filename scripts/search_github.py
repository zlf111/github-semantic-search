#!/usr/bin/env python3
"""
GitHub Semantic Search v6
=========================

在指定 GitHub 仓库中搜索与特定主题相关的内容（Issues, PRs, Code, Commits, Discussions）。
支持 AI 同义词扩展 + 多轮关键词搜索 + 相关度评分 + 并行搜索。

用法:
    # Issues 搜索 (默认，兼容 v4/v5)
    python search_github.py --config search_config.json --output results.md

    # Issues + PR 搜索 (并行)
    python search_github.py --config search_config.json --search-types issues prs

    # 全类型并行搜索
    python search_github.py --config config.json --search-types issues prs code commits discussions

    # 禁用并行 (调试用)
    python search_github.py --config config.json --search-types issues prs --no-parallel

    # 增量搜索 (从缓存恢复)
    python search_github.py --config config_v2.json --cache-file cache.json --resume

环境变量:
    GITHUB_TOKEN: GitHub Personal Access Token (免费, 无需权限)
"""

import argparse
import concurrent.futures
import logging
import os
import sys
import threading
import time as _time

# Add parent directory to path for package imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.models import SearchConfig
from core.api_client import GitHubApiClient
from core.scorer import KeywordScorer
from core.cache import save_cache, load_cache
from core.report import (
    format_markdown, format_json, format_full_report, format_full_json,
    get_ranked_results,
)
from core.cross_ref import build_cross_references, format_cross_ref_summary
from core.query_builder import build_queries, merge_seed_synonyms
from searchers.issue import IssueSearcher
from searchers.pr import PRSearcher
from searchers.code import CodeSearcher
from searchers.commit import CommitSearcher

log = logging.getLogger("gss")


def _setup_logging(verbose: bool = False, quiet: bool = False):
    """Configure the gss logger hierarchy.

    --verbose: DEBUG level (all details)
    --quiet:   WARNING only (errors and rate-limit waits)
    default:   INFO (progress + results)
    """
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "  %(message)s"  # clean prefix, matches original print style
    ))
    root = logging.getLogger("gss")
    root.setLevel(level)
    root.addHandler(handler)
    # Prevent duplicate logs if called multiple times
    root.propagate = False
from searchers.discussion import DiscussionSearcher


def print_token_hint():
    """Print GITHUB_TOKEN setup instructions."""
    print("┌─────────────────────────────────────────────────┐")
    print("│  [提示] 未检测到 GITHUB_TOKEN                   │")
    print("│                                                  │")
    print("│  当前限制: Search 10次/分钟, REST 60次/小时     │")
    print("│  设置后:   Search 30次/分钟, REST 5000次/小时   │")
    print("│                                                  │")
    print("│  免费生成: https://github.com/settings/tokens    │")
    print("│  无需勾选任何权限                                │")
    print("│                                                  │")
    print("│  设置方法:                                       │")
    print("│    Windows: $env:GITHUB_TOKEN = 'ghp_xxx'        │")
    print("│    Linux:   export GITHUB_TOKEN=ghp_xxx          │")
    print("└─────────────────────────────────────────────────┘")
    print()


def print_dry_run(config: SearchConfig):
    """Display dry-run preview of queries and filters."""
    component = config.component
    qualifiers = config.filter_qualifiers
    component_display = component if component else "(不限)"
    state_display = config.state_filter or "全部"
    print(f"\n{'='*60}")
    print(" DRY-RUN 预览 (不执行搜索)")
    print(f"{'='*60}")
    print(f" 仓库: {config.repo}")
    print(f" 组件: {component_display}")
    print(f" 主题: {config.topic}")
    print(f" 状态: {state_display}")
    if config.date_from or config.date_to:
        d_from = config.date_from or "..."
        d_to = config.date_to or "..."
        print(f" 时间: {d_from} ~ {d_to}")
    if config.exclude_issues:
        print(f" 排除: {config.exclude_issues}")
    kh = len(config.keywords_high)
    km = len(config.keywords_medium)
    kl = len(config.keywords_low)
    print(f"\n 关键词 ({kh}H + {km}M + {kl}L):")
    if config.keywords_high:
        print(f"   高: {', '.join(config.keywords_high)}")
    if config.keywords_medium:
        print(f"   中: {', '.join(config.keywords_medium)}")
    if config.keywords_low:
        print(f"   低: {', '.join(config.keywords_low)}")
    nq = len(config.queries)
    print(f"\n 将发送的查询 ({nq} 条):")
    seen = set()
    for i, qt in enumerate(config.queries, 1):
        if component:
            q = qt.replace("{component}", component)
        else:
            q = qt.replace("{component}", "").replace("  ", " ").strip()
        if qualifiers:
            q = f"{q} {qualifiers}"
        full = f"repo:{config.repo} is:issue {q}"
        q_norm = " ".join(full.split())
        dup = " (重复)" if q_norm in seen else ""
        seen.add(q_norm)
        print(f"   [{i}] {full}{dup}")
    print(f"\n{'='*60}")
    print(" 使用 --dry-run 确认后，去掉该参数即可执行搜索")
    print(f"{'='*60}")


def _write_intermediate_json(searchers: dict, config, args):
    """Output intermediate scored results as JSON for AI review.

    Emits top-N results + borderline items per type, with enough context
    for an AI to judge relevance and adjust scores.
    """
    import json

    intermediate = {
        "version": "v6-intermediate",
        "repo": config.repo,
        "component": config.component,
        "topic": config.topic,
        "instructions": (
            "Review each item. Set 'ai_score' to your assessed relevance "
            "(0-30). Set 'ai_label' to 'relevant', 'noise', or 'borderline'. "
            "Save as JSON and pass back with --score-overrides."
        ),
        "types": {},
    }

    for type_key, searcher in searchers.items():
        results = searcher.results
        if not results:
            continue

        items = sorted(results.values(),
                       key=lambda x: -x.relevance_score)

        # Top 30 + borderline (score 1.0 ~ min_score)
        top = items[:30]
        borderline = [r for r in items[30:]
                      if 1.0 <= r.relevance_score < args.min_score][:20]

        def _item_summary(item):
            """Extract compact summary for AI review."""
            d = {"score": round(item.relevance_score, 1),
                 "matched_keywords": sorted(item.matched_keywords)}
            if hasattr(item, "number"):
                d["number"] = item.number
                d["title"] = item.title
                d["url"] = item.url
            if hasattr(item, "state"):
                d["state"] = getattr(item, "state", "")
            if hasattr(item, "path"):
                d["path"] = item.path
                d["url"] = item.url
            if hasattr(item, "sha") and not hasattr(item, "number"):
                d["sha"] = item.sha[:10]
                d["message"] = item.message[:200] if hasattr(item, "message") else ""
            # Body snippet for context (first 300 chars)
            body = getattr(item, "body", "") or ""
            if body:
                d["body_snippet"] = body[:300]
            return d

        section = {
            "total": len(results),
            "top": [_item_summary(r) for r in top],
        }
        if borderline:
            section["borderline"] = [_item_summary(r) for r in borderline]

        intermediate["types"][type_key] = section

    path = args.intermediate_json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(intermediate, f, ensure_ascii=False, indent=2)
    log.info("[Smart] 中间结果已保存到 %s (%d 种类型)",
             path, len(intermediate["types"]))


def _apply_score_overrides(searchers: dict, overrides_path: str):
    """Apply AI-reviewed score overrides to search results.

    Expected JSON format:
    {
      "overrides": {
        "issues": { "123": {"ai_score": 15.0, "ai_label": "relevant"}, ... },
        "prs":    { "456": {"ai_score": 0, "ai_label": "noise"}, ... },
        "code":   { "path/to/file.py": {"ai_score": 8.0}, ... },
        ...
      }
    }
    """
    import json

    if not os.path.exists(overrides_path):
        log.warning("[Smart] 分数修正文件不存在: %s", overrides_path)
        return

    with open(overrides_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    overrides = data.get("overrides", {})
    total_applied = 0

    for type_key, type_overrides in overrides.items():
        if type_key not in searchers:
            continue
        results = searchers[type_key].results

        for key_str, override in type_overrides.items():
            # Convert key to the right type (int for issues/prs/discussions, str for code/commits)
            if type_key in ("issues", "prs", "discussions"):
                try:
                    key = int(key_str)
                except ValueError:
                    continue
            else:
                key = key_str

            if key not in results:
                continue

            item = results[key]
            if "ai_score" in override:
                old = item.relevance_score
                item.relevance_score = float(override["ai_score"])
                log.debug("[Smart] %s #%s: %.1f → %.1f",
                          type_key, key_str, old, item.relevance_score)
                total_applied += 1

    log.info("[Smart] 已应用 %d 条分数修正 (来自 %s)",
             total_applied, overrides_path)


def main():
    parser = argparse.ArgumentParser(
        description="GitHub Semantic Search v6",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Basic
    parser.add_argument("--config", "-c", help="JSON 配置文件路径")
    parser.add_argument("--repo", default="ROCm/rocm-libraries")
    parser.add_argument("--component", default="")
    parser.add_argument("--topic", default="page fault")
    # Search types
    parser.add_argument("--search-types", nargs="*", default=None,
                        help="搜索类型: issues prs code commits discussions")
    # Filters
    parser.add_argument("--state", default="", choices=["open", "closed", ""])
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    # Search options
    parser.add_argument("--keywords", nargs="*")
    parser.add_argument("--queries", nargs="*")
    parser.add_argument("--search-comments", action="store_true",
                        help="强制启用 comments 搜索 (默认: 有 token 时自动启用)")
    parser.add_argument("--no-comments", action="store_true",
                        help="禁用 comments 搜索 (即使有 token)")
    parser.add_argument("--comments-low", type=float, default=3.0)
    parser.add_argument("--comments-high", type=float, default=8.0)
    parser.add_argument("--concurrency", type=int, default=0)
    # Cache
    parser.add_argument("--cache-file", default="")
    parser.add_argument("--resume", action="store_true")
    # Output
    parser.add_argument("--min-score", type=float, default=3.0)
    parser.add_argument("--max-component", type=int, default=10,
                        help="仅组件匹配的最大显示数 (default: 10)")
    parser.add_argument("--output", "-o", help="输出文件路径")
    # Smart features
    parser.add_argument("--intermediate-json", default="",
                        help="输出中间结果 JSON (供 AI 二次审查)")
    parser.add_argument("--score-overrides", default="",
                        help="AI 分数修正 JSON 文件 (覆盖机器评分)")
    parser.add_argument("--append-queries", nargs="*", default=None,
                        help="追加查询 (多轮搜索, 与 --resume 配合)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    # Parallel
    parser.add_argument("--no-parallel", action="store_true",
                        help="禁用并行搜索 (调试用, 默认多类型自动并行)")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="每条查询最大分页数 (默认 3 = 300 条/查询, "
                             "防止宽泛查询耗尽配额)")
    # Verbosity
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", "-v", action="store_true",
                           help="详细输出 (DEBUG 级别)")
    verbosity.add_argument("--quiet", "-q", action="store_true",
                           help="安静模式 (仅警告和错误)")
    args = parser.parse_args()

    # Configure logging before any other work
    _setup_logging(verbose=args.verbose, quiet=args.quiet)

    # Build config
    if args.config:
        config = SearchConfig.from_json(args.config)
    else:
        config = SearchConfig(repo=args.repo, component=args.component, topic=args.topic)
        if args.keywords:
            config.keywords_high = args.keywords
        if args.queries:
            config.queries = args.queries

    # CLI overrides
    if args.state:
        config.state_filter = args.state
    if args.date_from:
        config.date_from = args.date_from
    if args.date_to:
        config.date_to = args.date_to
    if args.search_types:
        config.search_types = args.search_types

    # Max pages per query
    config.max_pages = args.max_pages

    # Append queries (multi-round search)
    if args.append_queries:
        config.queries = list(config.queries) + list(args.append_queries)
        log.info("追加 %d 条查询 (总计 %d 条)",
                 len(args.append_queries), len(config.queries))

    # Validate
    errors = config.validate()
    if errors:
        for err in errors:
            print(f"[错误] {err}")
        sys.exit(1)

    # ========== Phase 2.5: Seed synonym merge (before keyword cache) ==========
    n_seed = merge_seed_synonyms(config)
    if n_seed:
        print(f"\n🌱 种子词库补充了 {n_seed} 个关键词")
        print(f"   当前: H={len(config.keywords_high)}, "
              f"M={len(config.keywords_medium)}, L={len(config.keywords_low)}")

    # ========== Phase 3: Auto-build queries (if not provided) ==========
    if not config.queries:
        config.queries = build_queries(config)
        if config.queries:
            print(f"\n🔧 自动构建了 {len(config.queries)} 条查询 (来自 "
                  f"{len(config.all_keywords)} 个关键词)")
        else:
            print("[错误] 未提供搜索查询，且无法从关键词自动生成。"
                  "请提供关键词或查询。")
            sys.exit(1)

    if not config.all_keywords:
        log.warning("未提供评分关键词。")
    else:
        total_kw = len(config.all_keywords)
        if total_kw < 10:
            print(f"\n⚠️  关键词数量偏少 ({total_kw} 个)，建议至少 10 个。")
            print("   提示: 请参考 references/synonyms.md 进行同义词扩展 (Phase 2)。")
            print(f"   当前: H={len(config.keywords_high)}, "
                  f"M={len(config.keywords_medium)}, L={len(config.keywords_low)}")
            print()

    # Dry-run
    if args.dry_run:
        print_dry_run(config)
        sys.exit(0)

    # Token check
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print_token_hint()
        if args.search_comments:
            log.warning("无 token 时 comments 搜索可能非常慢 (REST API 60次/小时)")

    # Initialize
    api_client = GitHubApiClient(token=token)

    # --- Smart auto-comments ---
    # Default: enable comment search when token exists, unless --no-comments
    if args.no_comments:
        args.search_comments = False
    elif not args.search_comments and token:
        # Auto-detect: check Core API budget before enabling
        core_remaining = api_client.check_core_budget()
        if core_remaining >= 50:
            args.search_comments = True
            log.info("[auto-comments] Core API 余量充足 (%d)，自动启用 comments 搜索",
                     core_remaining)
        else:
            log.warning("[auto-comments] Core API 余量不足 (%d < 50)，跳过 comments 搜索"
                        "（可用 --search-comments 强制启用）", core_remaining)
    scorer = KeywordScorer()
    search_types = config.search_types

    # Searcher registry: (type_key, SearcherClass, score_method, supports_comments)
    _SEARCHER_REGISTRY = [
        ("issues",      IssueSearcher,      scorer.score_issues,      True),
        ("prs",         PRSearcher,          scorer.score_prs,         True),
        ("code",        CodeSearcher,        scorer.score_code,        False),
        ("commits",     CommitSearcher,      scorer.score_commits,     False),
        ("discussions", DiscussionSearcher,  scorer.score_discussions, False),
    ]

    # Filter to active types only
    active_registry = [(tk, sc, sf, sup)
                       for tk, sc, sf, sup in _SEARCHER_REGISTRY
                       if tk in search_types]

    # Thread lock for cache file writes (read-modify-write is not atomic)
    _cache_lock = threading.Lock()

    def _run_searcher(type_key, SearcherClass, score_fn, supports_comments):
        """Execute one searcher: cache-load → collect → score → details → cache-save.

        Returns (type_key, searcher, was_resumed, elapsed_seconds).
        """
        t0 = _time.monotonic()
        searcher = SearcherClass(api_client, config.repo)
        was_resumed = False

        # Resume from cache
        if args.resume and args.cache_file:
            loaded = load_cache(args.cache_file, config.repo,
                                searcher.results, type_key=type_key)
            if loaded:
                n = len(searcher.results)
                log.info("[%s] 已加载 %d 个缓存结果", type_key, n)
                was_resumed = True

        # Phase 1: collect + score
        searcher.collect(config)
        score_fn(searcher.results, config)

        # Phase 2: fetch details (comments) if requested and supported
        if args.search_comments and supports_comments:
            searcher.fetch_details(
                config,
                low_threshold=args.comments_low,
                high_threshold=args.comments_high,
                concurrency=args.concurrency,
            )
            score_fn(searcher.results, config)

        # Save cache (lock for thread safety: read-modify-write on shared file)
        if args.cache_file:
            with _cache_lock:
                save_cache(searcher.results, config.repo,
                           args.cache_file, type_key=type_key)

        elapsed = _time.monotonic() - t0
        return type_key, searcher, was_resumed, elapsed

    # ========== Execute searchers ==========
    searchers: dict[str, object] = {}
    resumed = False
    use_parallel = len(active_registry) > 1 and not args.no_parallel

    if use_parallel:
        log.info("[并行模式] 同时搜索 %d 种类型: %s",
                 len(active_registry),
                 ", ".join(tk for tk, *_ in active_registry))
        t_total = _time.monotonic()

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(active_registry)) as pool:
            futures = {
                pool.submit(_run_searcher, tk, sc, sf, sup): tk
                for tk, sc, sf, sup in active_registry
            }
            for future in concurrent.futures.as_completed(futures):
                tk = futures[future]
                try:
                    type_key, searcher, was_resumed, elapsed = future.result()
                    searchers[type_key] = searcher
                    if was_resumed:
                        resumed = True
                    log.info("[%s] 完成 (%.1fs)", type_key, elapsed)
                except Exception as exc:
                    log.error("[%s] 搜索失败: %s", tk, exc)

        wall = _time.monotonic() - t_total
        serial_sum = sum(
            getattr(s, '_elapsed', 0)
            for s in searchers.values()
        )
        log.info("[并行模式] 全部完成: %.1fs 实际 / %d 种类型",
                 wall, len(searchers))
    else:
        # Sequential execution (single type or --no-parallel)
        for type_key, SearcherClass, score_fn, supports_comments in active_registry:
            _, searcher, was_resumed, elapsed = _run_searcher(
                type_key, SearcherClass, score_fn, supports_comments)
            searchers[type_key] = searcher
            if was_resumed:
                resumed = True

    # ========== Smart: Intermediate JSON (for AI review) ==========
    if args.intermediate_json:
        _write_intermediate_json(searchers, config, args)

    # ========== Smart: Score overrides (from AI review) ==========
    if args.score_overrides:
        _apply_score_overrides(searchers, args.score_overrides)

    # ========== Cross-references ==========
    # Only run cross-reference analysis when 2+ content types have results
    # Cross-reference: triggered when user requested 2+ search types
    _xref_types_requested = len([t for t in config.search_types
                                 if t in ("issues", "prs", "commits")])
    if _xref_types_requested >= 2:
        xref = build_cross_references(
            issue_results=searchers["issues"].results if "issues" in searchers else None,
            pr_results=searchers["prs"].results if "prs" in searchers else None,
            commit_results=searchers["commits"].results if "commits" in searchers else None,
        )
    else:
        log.info("[交叉引用] 用户仅请求 %d 种可关联类型，跳过 (需要 ≥2 种: issues/prs/commits)",
                 _xref_types_requested)
        xref = {"edges": [], "stats": {"total_edges": 0, "issue_pr_links": 0,
                                        "pr_pr_links": 0, "commit_refs": 0}}

    # ========== Output ==========
    result_kwargs = dict(
        config=config,
        min_score=args.min_score,
        searched_comments=args.search_comments,
        issue_results=searchers["issues"].results if "issues" in searchers else None,
        pr_results=searchers["prs"].results if "prs" in searchers else None,
        code_results=searchers["code"].results if "code" in searchers else None,
        commit_results=searchers["commits"].results if "commits" in searchers else None,
        disc_results=searchers["discussions"].results if "discussions" in searchers else None,
    )

    if args.json:
        output = format_full_json(**result_kwargs)
    else:
        output = format_full_report(**result_kwargs, max_component=args.max_component)
        # Append cross-reference section if there are links
        # Place graph PNG next to the output file
        _img_dir = os.path.dirname(os.path.abspath(args.output)) if args.output else ""
        xref_section = format_cross_ref_summary(
            xref,
            issue_results=searchers["issues"].results if "issues" in searchers else None,
            pr_results=searchers["prs"].results if "prs" in searchers else None,
            commit_results=searchers["commits"].results if "commits" in searchers else None,
            repo=config.repo,
            output_dir=_img_dir,
        )
        if xref_section:
            # Insert cross-reference before footer line ("*Generated by ...")
            _footer_marker = "*Generated by search_github.py"
            _footer_idx = output.rfind(_footer_marker)
            if _footer_idx > 0:
                output = (output[:_footer_idx].rstrip()
                          + "\n\n" + xref_section + "\n\n"
                          + output[_footer_idx:])
            else:
                output = output.rstrip() + "\n\n" + xref_section

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\n结果已保存到: {args.output}")
    else:
        print("\n" + output)

    # ========== Summary ==========
    _TYPE_LABELS = {
        "issues": "Issues", "prs": "PRs", "code": "Code",
        "commits": "Commits", "discussions": "Discussions",
    }

    print(f"\n{'='*60}")
    print(" 搜索完成!")

    if resumed:
        print("   模式: 增量搜索 (从缓存恢复)")

    exclude = set(config.exclude_issues) if config.exclude_issues else set()

    for type_key, searcher in searchers.items():
        results = searcher.results

        # Apply the same filters used in report generation so console
        # summary matches the Markdown output.
        if type_key in ("issues", "prs"):
            pool = {k: v for k, v in results.items() if k not in exclude}
            if config.state_filter:
                pool = {k: v for k, v in pool.items()
                        if v.state == config.state_filter}
            if config.date_from:
                pool = {k: v for k, v in pool.items()
                        if v.created_at >= config.date_from}
        else:
            pool = results

        n_total = len(pool)
        ranked = [r for r in pool.values() if r.relevance_score >= args.min_score]
        n_ranked = len(ranked)
        n_high = len([r for r in ranked if r.relevance_score >= 8.0])
        label = _TYPE_LABELS.get(type_key, type_key)

        if type_key == "code":
            print(f"   {label}: 搜索 {n_total} 文件, 相关 {n_ranked}")
        else:
            print(f"   {label}: 搜索 {n_total}, 相关 {n_ranked}, 高度相关 {n_high}")

    # Issue-specific extras
    if "issues" in searchers and args.search_comments:
        issue_results = searchers["issues"].results
        pool_comments = {k: v for k, v in issue_results.items() if k not in exclude}
        if config.state_filter:
            pool_comments = {k: v for k, v in pool_comments.items()
                             if v.state == config.state_filter}
        n_from_comments = len([i for i in pool_comments.values()
                               if i.matched_in_comments and i.relevance_score >= args.min_score])
        print(f"   通过 comments 发现: {n_from_comments} 个")

    if args.cache_file and searchers:
        print(f"   缓存已保存: {args.cache_file}")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
