"""Report generation for GitHub Semantic Search v5.

Generates unified Markdown reports with:
- Executive summary across all search types
- Consistent metadata headers per section
- Keyword-matched vs component-only separation
- Top-N limiting for large result sets
- Summary tables for keyword-matched items only
"""

import json
import time

from core.models import (
    Issue, PullRequest, CodeResult, CommitResult,
    DiscussionResult, SearchConfig,
)


# ============================================================================
# Shared helpers
# ============================================================================

def _date_display(config: SearchConfig) -> str:
    if config.date_from and config.date_to:
        return f"{config.date_from} ~ {config.date_to}"
    elif config.date_from:
        return f">= {config.date_from}"
    elif config.date_to:
        return f"<= {config.date_to}"
    return "不限"


def _keywords_block(config: SearchConfig) -> list[str]:
    """Shared keywords display block."""
    kw_high = ", ".join(f"`{k}`" for k in config.keywords_high[:10]) or "无"
    kw_med = ", ".join(f"`{k}`" for k in config.keywords_medium[:10]) or "无"
    kw_low = ", ".join(f"`{k}`" for k in config.keywords_low[:10]) or "无"
    n = len(config.keywords_high) + len(config.keywords_medium) + len(config.keywords_low)
    return [
        f"### 关键词 ({n} 个)",
        "",
        f"- **高权重** (+5): {kw_high}",
        f"- **中权重** (+3): {kw_med}",
        f"- **低权重** (+1): {kw_low}",
    ]


def _metadata_block(config: SearchConfig, searched_comments: bool = False,
                    timestamp: str = None) -> list[str]:
    """Shared metadata block for executive summary header."""
    ts = timestamp or time.strftime('%Y-%m-%d %H:%M:%S')
    comp = config.component if config.has_component else "(不限)"
    state = config.state_filter if config.state_filter else "全部"
    lines = [
        f"- **仓库**: [{config.repo}](https://github.com/{config.repo})",
        f"- **组件**: {comp}",
        f"- **主题**: {config.topic}",
        f"- **状态过滤**: {state}",
        f"- **时间范围**: {_date_display(config)}",
        f"- **搜索时间**: {ts}",
    ]
    if searched_comments:
        lines.append("- **搜索 comments**: 是")
    return lines


# ============================================================================
# Result splitting
# ============================================================================

def _split_results(items, min_score: float):
    """Split results into keyword-matched and component-only groups.

    Returns: (keyword_matched, component_only) – both sorted by desc score.
    """
    ranked = [item for item in items if item.relevance_score >= min_score]
    ranked.sort(key=lambda x: -x.relevance_score)
    kw_matched = [r for r in ranked if r.matched_keywords]
    comp_only = [r for r in ranked if not r.matched_keywords]
    return kw_matched, comp_only


def _section_stats(type_label, total_searched, kw_matched, comp_only):
    """Build stats dict for executive summary row."""
    all_ranked = kw_matched + comp_only
    top = max((r.relevance_score for r in all_ranked), default=0)
    return {
        "type_label": type_label,
        "total_searched": total_searched,
        "kw_matched": len(kw_matched),
        "component_only": len(comp_only),
        "top_score": top,
    }


# ============================================================================
# Executive summary
# ============================================================================

def format_executive_summary(sections: list[dict], config: SearchConfig,
                             searched_comments: bool = False) -> str:
    """Generate top-level executive summary.

    *sections*: list of dicts from ``_section_stats``.
    """
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    lines = [
        "# GitHub 语义搜索报告 v5",
        "",
        "## 执行摘要",
        "",
        "| 类型 | 搜索总数 | 关键词命中 | 仅组件匹配 | 最高分 |",
        "|------|---------|-----------|-----------|--------|",
    ]
    for s in sections:
        top = f"{s['top_score']:.1f}" if s['top_score'] > 0 else "-"
        lines.append(
            f"| {s['type_label']} | {s['total_searched']} | "
            f"{s['kw_matched']} | {s['component_only']} | {top} |"
        )
    lines.append("")
    lines.extend(_metadata_block(config, searched_comments, ts))
    lines.append("")
    lines.extend(_keywords_block(config))
    lines.extend(["", "---", ""])
    return "\n".join(lines)


# ============================================================================
# Snippet extraction (shared)
# ============================================================================

def _extract_snippets(body: str, comments: str, keywords: set,
                      context_chars: int = 120) -> list[str]:
    """Extract keyword context snippets from body and comments."""
    if not body and not comments:
        return []
    snippets = []
    seen = set()
    for source_label, text in [("[body]", body), ("[comments]", comments)]:
        if not text:
            continue
        text_lower = text.lower()
        for keyword in sorted(keywords, key=len, reverse=True):
            kw_lower = keyword.lower()
            pos = text_lower.find(kw_lower)
            while pos != -1:
                bucket = (source_label, pos // context_chars)
                if bucket not in seen:
                    seen.add(bucket)
                    start = max(0, pos - 40)
                    end = min(len(text), pos + len(keyword) + context_chars - 40)
                    snippet = text[start:end].replace("\n", " ").strip()
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(text):
                        snippet = snippet + "..."
                    snippets.append(f"{source_label} {snippet}")
                    if len(snippets) >= 5:
                        return snippets
                pos = text_lower.find(kw_lower, pos + 1)
    return snippets


# ============================================================================
# Issue formatting
# ============================================================================

def _format_issue_detail(issue: Issue) -> str:
    """Format one issue with full detail (keyword-matched)."""
    kws_body = issue.matched_keywords - issue.matched_in_comments
    kws_comments = issue.matched_in_comments
    icon = "\U0001f7e2" if issue.state == "open" else "\U0001f534"
    labels_str = ", ".join(f"`{l}`" for l in issue.labels) if issue.labels else "\u65e0"

    parts = []
    if kws_body:
        parts.append("body: " + ", ".join(f"`{k}`" for k in sorted(kws_body)))
    if kws_comments:
        parts.append("comments: " + ", ".join(f"`{k}`" for k in sorted(kws_comments)))
    kws_str = " | ".join(parts) if parts else "\u65e0"

    snippets = _extract_snippets(issue.body, issue.comments_text,
                                 issue.matched_keywords)
    snippet_text = ""
    if snippets:
        snippet_text = "\n" + "\n".join(f"  > {s}" for s in snippets[:3]) + "\n"

    return (
        f"### {icon} [#{issue.number}]({issue.url}) "
        f"(score: {issue.relevance_score:.1f})\n"
        f"**{issue.title}**\n"
        f"- \u72b6\u6001: {issue.state} | \u65e5\u671f: {issue.created_at}"
        f" | Labels: {labels_str}\n"
        f"- \u5339\u914d: {kws_str}\n"
        f"{snippet_text}\n"
    )


def format_issue_section(issues: dict, config: SearchConfig, min_score: float,
                         searched_comments: bool = False,
                         max_component: int = 10) -> tuple:
    """Format Issue section with keyword/component split.

    Returns ``(markdown_str, stats_dict)``.
    """
    exclude = set(config.exclude_issues) if config.exclude_issues else set()
    pool = {k: v for k, v in issues.items() if k not in exclude}
    if config.state_filter:
        pool = {k: v for k, v in pool.items() if v.state == config.state_filter}
    if config.date_from:
        pool = {k: v for k, v in pool.items() if v.created_at >= config.date_from}
    if config.date_to:
        pool = {k: v for k, v in pool.items() if v.created_at <= config.date_to}

    kw_matched, comp_only = _split_results(pool.values(), min_score)
    stats = _section_stats("Issues", len(issues), kw_matched, comp_only)

    n_kw = len(kw_matched)
    n_comp = len(comp_only)
    n_total = n_kw + n_comp

    lines = [f"# Issues ({n_kw} \u4e2a\u5173\u952e\u8bcd\u547d\u4e2d"
             f" / {n_total} \u4e2a\u603b\u76f8\u5173)", ""]

    if not kw_matched and not comp_only:
        lines.append("\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684 issues\u3002\n")
        return "\n".join(lines), stats

    # ---- keyword matched (full detail) ----
    if kw_matched:
        lines.append(f"## \u5173\u952e\u8bcd\u5339\u914d ({n_kw} \u4e2a)\n")
        for issue in kw_matched:
            lines.append(_format_issue_detail(issue))

    # ---- component only (compact table) ----
    if comp_only and max_component > 0:
        shown = comp_only[:max_component]
        lines.append(f"## \u4ec5\u7ec4\u4ef6\u5339\u914d ({n_comp} \u4e2a)\n")
        note_parts = []
        if config.has_component:
            note_parts.append(
                f"\u4ee5\u4e0b issues \u56e0\u5305\u542b\u7ec4\u4ef6\u540d"
                f" `{config.component}` \u88ab\u68c0\u7d22\uff0c"
                f"\u4f46\u672a\u5339\u914d\u4efb\u4f55\u641c\u7d22\u5173\u952e\u8bcd\u3002"
            )
        if n_comp > max_component:
            note_parts.append(
                f"\u4ec5\u663e\u793a\u524d {max_component} \u4e2a"
                f"\uff08\u5171 {n_comp} \u4e2a\uff09\u3002"
            )
        if note_parts:
            lines.append("> " + " ".join(note_parts) + "\n")
        lines.append("| # | Issue | \u72b6\u6001 | \u5206\u6570"
                     " | \u65e5\u671f | \u6807\u9898 |")
        lines.append("|---|-------|------|------|------|------|")
        for i, issue in enumerate(shown, 1):
            ic = "\U0001f7e2" if issue.state == "open" else "\U0001f534"
            t = issue.title[:60] + ("..." if len(issue.title) > 60 else "")
            lines.append(
                f"| {i} | [#{issue.number}]({issue.url}) | "
                f"{ic} {issue.state} | {issue.relevance_score:.1f} | "
                f"{issue.created_at} | {t} |"
            )
        lines.append("")

    # ---- summary table (keyword matched only) ----
    if kw_matched:
        lines.append("## \u6c47\u603b\u8868\u683c\n")
        lines.append("| # | Issue | \u72b6\u6001 | \u5206\u6570"
                     " | \u5339\u914d\u5173\u952e\u8bcd | \u6765\u6e90 |")
        lines.append("|---|-------|------|------|-----------|------|")
        for i, issue in enumerate(kw_matched, 1):
            kws = ", ".join(sorted(issue.matched_keywords)[:5])
            ic = "\U0001f7e2" if issue.state == "open" else "\U0001f534"
            src = "body"
            if issue.matched_in_comments:
                if issue.matched_keywords - issue.matched_in_comments:
                    src = "body+comments"
                else:
                    src = "comments"
            lines.append(
                f"| {i} | [#{issue.number}]({issue.url}) | "
                f"{ic} {issue.state} | {issue.relevance_score:.1f} | "
                f"{kws} | {src} |"
            )
        lines.append("")

    lines.extend(["---", ""])
    return "\n".join(lines), stats


# ============================================================================
# PR formatting
# ============================================================================

def _format_pr_detail(pr: PullRequest) -> str:
    """Format one PR with full detail (keyword-matched)."""
    icon = "\u2705" if pr.merged else (
        "\U0001f7e2" if pr.state == "open" else "\U0001f534")
    status = "merged" if pr.merged else pr.state
    labels = ", ".join(f"`{l}`" for l in pr.labels) if pr.labels else "\u65e0"
    kws = ", ".join(f"`{k}`" for k in sorted(pr.matched_keywords)[:8])
    linked = ", ".join(f"#{n}" for n in pr.linked_issues) \
        if pr.linked_issues else "\u65e0"
    files = ", ".join(f"`{f}`" for f in pr.changed_files[:5]) \
        if pr.changed_files else "\u672a\u83b7\u53d6"
    if len(pr.changed_files) > 5:
        files += f" +{len(pr.changed_files) - 5} more"

    return (
        f"### {icon} [#{pr.number}]({pr.url}) "
        f"(score: {pr.relevance_score:.1f})\n"
        f"**{pr.title}**\n"
        f"- \u72b6\u6001: {status} | \u65e5\u671f: {pr.created_at}"
        f" | Labels: {labels}\n"
        f"- \u5173\u8054 Issues: {linked}\n"
        f"- \u53d8\u66f4\u6587\u4ef6: {files}\n"
        f"- \u5339\u914d: {kws}\n\n"
    )


def format_pr_section(prs: dict, config: SearchConfig, min_score: float,
                      max_component: int = 10) -> tuple:
    """Format PR section with keyword/component split.

    Returns ``(markdown_str, stats_dict)``.
    """
    kw_matched, comp_only = _split_results(prs.values(), min_score)
    stats = _section_stats("Pull Requests", len(prs), kw_matched, comp_only)

    n_kw = len(kw_matched)
    n_comp = len(comp_only)
    n_total = n_kw + n_comp

    lines = [f"# Pull Requests ({n_kw} \u4e2a\u5173\u952e\u8bcd\u547d\u4e2d"
             f" / {n_total} \u4e2a\u603b\u76f8\u5173)", ""]

    if not kw_matched and not comp_only:
        lines.append("\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684 PRs\u3002\n")
        return "\n".join(lines), stats

    # ---- keyword matched (full detail) ----
    if kw_matched:
        lines.append(f"## \u5173\u952e\u8bcd\u5339\u914d ({n_kw} \u4e2a)\n")
        for pr in kw_matched:
            lines.append(_format_pr_detail(pr))

    # ---- component only (compact table) ----
    if comp_only and max_component > 0:
        shown = comp_only[:max_component]
        lines.append(f"## \u4ec5\u7ec4\u4ef6\u5339\u914d ({n_comp} \u4e2a)\n")
        note_parts = []
        if config.has_component:
            note_parts.append(
                f"\u4ee5\u4e0b PRs \u56e0\u5305\u542b\u7ec4\u4ef6\u540d"
                f" `{config.component}` \u88ab\u68c0\u7d22\uff0c"
                f"\u4f46\u672a\u5339\u914d\u4efb\u4f55\u641c\u7d22\u5173\u952e\u8bcd\u3002"
            )
        if n_comp > max_component:
            note_parts.append(
                f"\u4ec5\u663e\u793a\u524d {max_component} \u4e2a"
                f"\uff08\u5171 {n_comp} \u4e2a\uff09\u3002"
            )
        if note_parts:
            lines.append("> " + " ".join(note_parts) + "\n")
        lines.append("| # | PR | \u72b6\u6001 | \u5206\u6570"
                     " | \u65e5\u671f | \u6807\u9898 |")
        lines.append("|---|-----|------|------|------|------|")
        for i, pr in enumerate(shown, 1):
            ic = "\u2705" if pr.merged else (
                "\U0001f7e2" if pr.state == "open" else "\U0001f534")
            st = "merged" if pr.merged else pr.state
            t = pr.title[:60] + ("..." if len(pr.title) > 60 else "")
            lines.append(
                f"| {i} | [#{pr.number}]({pr.url}) | "
                f"{ic} {st} | {pr.relevance_score:.1f} | "
                f"{pr.created_at} | {t} |"
            )
        lines.append("")

    # ---- summary table (keyword matched only) ----
    if kw_matched:
        lines.append("## \u6c47\u603b\u8868\u683c\n")
        lines.append("| # | PR | \u72b6\u6001 | \u5206\u6570"
                     " | \u5339\u914d\u5173\u952e\u8bcd | \u5173\u8054 Issue |")
        lines.append("|---|-----|------|------|-----------|-----------|")
        for i, pr in enumerate(kw_matched, 1):
            kws = ", ".join(sorted(pr.matched_keywords)[:5])
            ic = "\u2705" if pr.merged else (
                "\U0001f7e2" if pr.state == "open" else "\U0001f534")
            st = "merged" if pr.merged else pr.state
            linked = ", ".join(f"#{n}" for n in pr.linked_issues[:3]) \
                if pr.linked_issues else "-"
            lines.append(
                f"| {i} | [#{pr.number}]({pr.url}) | "
                f"{ic} {st} | {pr.relevance_score:.1f} | "
                f"{kws} | {linked} |"
            )
        lines.append("")

    lines.extend(["---", ""])
    return "\n".join(lines), stats


# ============================================================================
# Code formatting
# ============================================================================

def format_code_section(results: dict, config: SearchConfig,
                        min_score: float) -> tuple:
    """Format Code section.

    Returns ``(markdown_str, stats_dict)``.
    """
    kw_matched, comp_only = _split_results(results.values(), min_score)
    stats = _section_stats("Code", len(results), kw_matched, comp_only)
    all_ranked = kw_matched + comp_only
    all_ranked.sort(key=lambda x: -x.relevance_score)
    n = len(all_ranked)

    lines = [f"# Code ({n} \u4e2a\u76f8\u5173\u6587\u4ef6)", ""]

    if not all_ranked:
        lines.append("\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684"
                     "\u4ee3\u7801\u6587\u4ef6\u3002\n")
        return "\n".join(lines), stats

    for r in all_ranked[:30]:
        kws = ", ".join(f"`{k}`" for k in sorted(r.matched_keywords)[:5]) \
            or "\u65e0"
        snippet = r.content_snippet[:200].replace("\n", " ") \
            if r.content_snippet else ""
        lines.append(
            f"### [`{r.path}`]({r.url}) (score: {r.relevance_score:.1f})\n"
            f"- \u5339\u914d: {kws}\n"
        )
        if snippet:
            lines.append(f"  > {snippet}...\n")

    lines.extend(["---", ""])
    return "\n".join(lines), stats


# ============================================================================
# Commit formatting
# ============================================================================

def format_commit_section(results: dict, config: SearchConfig,
                          min_score: float) -> tuple:
    """Format Commit section.

    Returns ``(markdown_str, stats_dict)``.
    """
    kw_matched, comp_only = _split_results(results.values(), min_score)
    stats = _section_stats("Commits", len(results), kw_matched, comp_only)
    all_ranked = kw_matched + comp_only
    all_ranked.sort(key=lambda x: -x.relevance_score)
    n = len(all_ranked)

    lines = [f"# Commits ({n} \u4e2a\u76f8\u5173 commits)", ""]

    if not all_ranked:
        lines.append("\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684"
                     " commits\u3002\n")
        return "\n".join(lines), stats

    for r in all_ranked[:30]:
        kws = ", ".join(f"`{k}`" for k in sorted(r.matched_keywords)[:5]) \
            or "\u65e0"
        msg = r.message.split("\n")[0][:100] if r.message else ""
        sha_short = r.sha[:7]
        lines.append(
            f"### [`{sha_short}`]({r.url}) (score: {r.relevance_score:.1f})\n"
            f"**{msg}**\n"
            f"- \u4f5c\u8005: {r.author} | \u65e5\u671f: {r.date}\n"
            f"- \u5339\u914d: {kws}\n\n"
        )

    lines.extend(["---", ""])
    return "\n".join(lines), stats


# ============================================================================
# Discussion formatting
# ============================================================================

def format_discussion_section(results: dict, config: SearchConfig,
                              min_score: float,
                              max_component: int = 10) -> tuple:
    """Format Discussion section with keyword/component split.

    Returns ``(markdown_str, stats_dict)``.
    """
    kw_matched, comp_only = _split_results(results.values(), min_score)
    stats = _section_stats("Discussions", len(results), kw_matched, comp_only)

    n_kw = len(kw_matched)
    n_comp = len(comp_only)
    n_total = n_kw + n_comp

    lines = [f"# Discussions ({n_kw} \u4e2a\u5173\u952e\u8bcd\u547d\u4e2d"
             f" / {n_total} \u4e2a\u603b\u76f8\u5173)", ""]

    if not kw_matched and not comp_only:
        lines.append("\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684"
                     " discussions\u3002\n")
        return "\n".join(lines), stats

    # ---- keyword matched ----
    if kw_matched:
        lines.append(f"## \u5173\u952e\u8bcd\u5339\u914d"
                     f" ({n_kw} \u4e2a)\n")
        for r in kw_matched[:30]:
            kws = ", ".join(f"`{k}`" for k in sorted(r.matched_keywords)[:5])
            cat = r.category or "\u672a\u5206\u7c7b"
            ans = "\u2705 \u5df2\u56de\u7b54" if r.answer_body \
                else "\u2753 \u672a\u56de\u7b54"
            lines.append(
                f"### [#{r.number}]({r.url})"
                f" (score: {r.relevance_score:.1f})\n"
                f"**{r.title}**\n"
                f"- \u5206\u7c7b: {cat} | \u65e5\u671f: {r.created_at}"
                f" | {ans}\n"
                f"- \u5339\u914d: {kws}\n\n"
            )

    # ---- component only ----
    if comp_only and max_component > 0:
        shown = comp_only[:max_component]
        lines.append(f"## \u4ec5\u7ec4\u4ef6\u5339\u914d"
                     f" ({n_comp} \u4e2a)\n")
        if n_comp > max_component:
            lines.append(
                f"> \u4ec5\u663e\u793a\u524d {max_component} \u4e2a"
                f"\uff08\u5171 {n_comp} \u4e2a\uff09\u3002\n"
            )
        lines.append("| # | Discussion | \u5206\u6570 | \u5206\u7c7b"
                     " | \u65e5\u671f | \u6807\u9898 |")
        lines.append("|---|-----------|------|------|------|------|")
        for i, r in enumerate(shown, 1):
            cat = r.category or "-"
            t = r.title[:60] + ("..." if len(r.title) > 60 else "")
            lines.append(
                f"| {i} | [#{r.number}]({r.url}) | "
                f"{r.relevance_score:.1f} | {cat} | "
                f"{r.created_at} | {t} |"
            )
        lines.append("")

    lines.extend(["---", ""])
    return "\n".join(lines), stats


# ============================================================================
# Full report generator
# ============================================================================

def format_full_report(
    config: SearchConfig,
    min_score: float,
    searched_comments: bool = False,
    issue_results: dict = None,
    pr_results: dict = None,
    code_results: dict = None,
    commit_results: dict = None,
    disc_results: dict = None,
    max_component: int = 10,
) -> str:
    """Generate complete unified report with executive summary.

    Accepts result dicts from each searcher (``None`` = skipped).
    Returns full Markdown string.
    """
    section_stats = []
    section_texts = []

    if issue_results is not None:
        text, st = format_issue_section(
            issue_results, config, min_score, searched_comments, max_component)
        section_stats.append(st)
        section_texts.append(text)

    if pr_results is not None:
        text, st = format_pr_section(
            pr_results, config, min_score, max_component)
        section_stats.append(st)
        section_texts.append(text)

    if code_results is not None:
        text, st = format_code_section(code_results, config, min_score)
        section_stats.append(st)
        section_texts.append(text)

    if commit_results is not None:
        text, st = format_commit_section(commit_results, config, min_score)
        section_stats.append(st)
        section_texts.append(text)

    if disc_results is not None:
        text, st = format_discussion_section(
            disc_results, config, min_score, max_component)
        section_stats.append(st)
        section_texts.append(text)

    summary = format_executive_summary(section_stats, config, searched_comments)
    body = "\n".join(section_texts)
    footer = "*Generated by search_github.py v5*"
    return f"{summary}{body}\n{footer}\n"


# ============================================================================
# Backward-compatible functions
# ============================================================================

def get_ranked_results(issues: dict, config: SearchConfig,
                       min_score: float = 3.0) -> list:
    """Return filtered and sorted Issue results (backward compat)."""
    exclude = set(config.exclude_issues) if config.exclude_issues else set()
    out = []
    for issue in issues.values():
        if issue.number in exclude:
            continue
        if issue.relevance_score < min_score:
            continue
        if config.state_filter and issue.state != config.state_filter:
            continue
        if config.date_from and issue.created_at < config.date_from:
            continue
        if config.date_to and issue.created_at > config.date_to:
            continue
        out.append(issue)
    return sorted(out, key=lambda x: -x.relevance_score)


def format_markdown(issues: dict, config: SearchConfig,
                    min_score: float = 3.0,
                    searched_comments: bool = False) -> str:
    """Issue-only Markdown report (v4 backward compat)."""
    text, _ = format_issue_section(issues, config, min_score, searched_comments)
    return text


def format_json(issues: dict, config: SearchConfig,
                min_score: float = 3.0,
                searched_comments: bool = False,
                resumed: bool = False) -> str:
    """Generate JSON report (Issue-only, v4 backward compat)."""
    ranked = get_ranked_results(issues, config, min_score)
    result = {
        "repo": config.repo,
        "component": config.component,
        "topic": config.topic,
        "filters": {
            "state": config.state_filter or "all",
            "date_from": config.date_from or None,
            "date_to": config.date_to or None,
        },
        "searched_comments": searched_comments,
        "resumed_from_cache": resumed,
        "total_searched": len(issues),
        "total_relevant": len(ranked),
        "issues": [
            {
                "number": i.number,
                "title": i.title,
                "state": i.state,
                "url": i.url,
                "labels": i.labels,
                "created_at": i.created_at,
                "relevance_score": i.relevance_score,
                "matched_keywords": sorted(i.matched_keywords),
                "matched_in_comments": sorted(i.matched_in_comments),
            }
            for i in ranked
        ],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================================
# Full JSON report (all types)
# ============================================================================

def _ranked_list(items_dict: dict, min_score: float) -> list:
    """Filter and sort items by score descending."""
    ranked = [item for item in items_dict.values()
              if item.relevance_score >= min_score]
    ranked.sort(key=lambda x: -x.relevance_score)
    return ranked


def format_full_json(
    config: SearchConfig,
    min_score: float = 3.0,
    searched_comments: bool = False,
    issue_results: dict = None,
    pr_results: dict = None,
    code_results: dict = None,
    commit_results: dict = None,
    disc_results: dict = None,
) -> str:
    """Generate unified JSON report covering all search types.

    Mirrors ``format_full_report`` but outputs structured JSON.
    """
    result = {
        "version": "v5",
        "repo": config.repo,
        "component": config.component,
        "topic": config.topic,
        "search_types": config.search_types,
        "filters": {
            "state": config.state_filter or "all",
            "date_from": config.date_from or None,
            "date_to": config.date_to or None,
        },
        "searched_comments": searched_comments,
        "generated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
    }

    if issue_results is not None:
        ranked = _ranked_list(issue_results, min_score)
        result["issues"] = {
            "total_searched": len(issue_results),
            "total_relevant": len(ranked),
            "items": [
                {
                    "number": i.number,
                    "title": i.title,
                    "state": i.state,
                    "url": i.url,
                    "labels": i.labels,
                    "created_at": i.created_at,
                    "relevance_score": i.relevance_score,
                    "matched_keywords": sorted(i.matched_keywords),
                    "matched_in_comments": sorted(i.matched_in_comments),
                }
                for i in ranked
            ],
        }

    if pr_results is not None:
        ranked = _ranked_list(pr_results, min_score)
        result["pull_requests"] = {
            "total_searched": len(pr_results),
            "total_relevant": len(ranked),
            "items": [
                {
                    "number": p.number,
                    "title": p.title,
                    "state": p.state,
                    "merged": p.merged,
                    "url": p.url,
                    "labels": p.labels,
                    "created_at": p.created_at,
                    "relevance_score": p.relevance_score,
                    "matched_keywords": sorted(p.matched_keywords),
                    "linked_issues": p.linked_issues,
                    "changed_files": p.changed_files[:10],
                }
                for p in ranked
            ],
        }

    if code_results is not None:
        ranked = _ranked_list(code_results, min_score)
        result["code"] = {
            "total_searched": len(code_results),
            "total_relevant": len(ranked),
            "items": [
                {
                    "path": r.path,
                    "url": r.url,
                    "repo": r.repo,
                    "relevance_score": r.relevance_score,
                    "matched_keywords": sorted(r.matched_keywords),
                }
                for r in ranked
            ],
        }

    if commit_results is not None:
        ranked = _ranked_list(commit_results, min_score)
        result["commits"] = {
            "total_searched": len(commit_results),
            "total_relevant": len(ranked),
            "items": [
                {
                    "sha": r.sha,
                    "message": r.message.split("\n")[0][:200],
                    "author": r.author,
                    "date": r.date,
                    "url": r.url,
                    "relevance_score": r.relevance_score,
                    "matched_keywords": sorted(r.matched_keywords),
                }
                for r in ranked
            ],
        }

    if disc_results is not None:
        ranked = _ranked_list(disc_results, min_score)
        result["discussions"] = {
            "total_searched": len(disc_results),
            "total_relevant": len(ranked),
            "items": [
                {
                    "number": r.number,
                    "title": r.title,
                    "url": r.url,
                    "category": r.category,
                    "created_at": r.created_at,
                    "has_answer": bool(r.answer_body),
                    "relevance_score": r.relevance_score,
                    "matched_keywords": sorted(r.matched_keywords),
                }
                for r in ranked
            ],
        }

    return json.dumps(result, ensure_ascii=False, indent=2)
