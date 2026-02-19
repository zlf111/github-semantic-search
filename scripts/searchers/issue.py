"""Issue searcher — searches GitHub issues via Search API."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from core.api_client import GitHubApiClient
from core.models import Issue, SearchConfig
from searchers.base import BaseSearcher

log = logging.getLogger("gss.searcher.issue")


class IssueSearcher(BaseSearcher):
    """Search GitHub issues with two-phase comment fetching."""

    name = "issues"

    def __init__(self, api_client: GitHubApiClient, repo: str):
        super().__init__(api_client, repo)
        self.results: dict[int, Issue] = {}

    def build_query(self, query_template: str, config: SearchConfig) -> str:
        """Adapt query template: replace {component}, add is:issue."""
        component = config.component
        if component:
            query = query_template.replace("{component}", component)
        else:
            query = query_template.replace("{component}", "").replace("  ", " ").strip()
        qualifiers = config.filter_qualifiers
        if qualifiers:
            query = f"{query} {qualifiers}"
        return query

    # Maximum unique results before stopping early (avoids runaway queries)
    MAX_COLLECT = 500

    def collect(self, config: SearchConfig):
        """Phase 1: Search title + body via GitHub Search API."""
        queries = config.queries
        component = config.component
        nq = len(queries)

        log.info("\n%s", "="*60)
        log.info(" 阶段 1: 搜索 title + body")
        log.info("%s", "="*60)
        log.info(" 仓库: %s", config.repo)
        log.info(" 组件: %s", component if component else "(不限)")
        log.info(" 主题: %s", config.topic)
        log.info(" 状态过滤: %s", config.state_filter if config.state_filter else "全部")
        if config.date_from or config.date_to:
            date_range = f"{config.date_from or '...'} ~ {config.date_to or '...'}"
            log.info(" 时间范围: %s", date_range)
        log.info(" 查询数: %d", nq)
        log.info("%s\n", "="*60)

        seen_queries = set()
        consecutive_zero = 0  # Track consecutive queries with 0 new results

        for i, query_template in enumerate(queries, 1):
            query = self.build_query(query_template, config)
            full_query = f"repo:{self.repo} is:issue {query}"

            query_normalized = " ".join(full_query.split())
            if query_normalized in seen_queries:
                log.info("  [%d/%d] 跳过 (重复): %s...", i, nq, query[:80])
                continue
            seen_queries.add(query_normalized)

            log.info("  [%d/%d] 搜索: %s...", i, nq, query[:80])

            items = self.api.search(
                "https://api.github.com/search/issues",
                full_query,
                max_pages=config.max_pages,
            )
            new_count = 0

            for item in items:
                num = item["number"]
                if num not in self.results:
                    new_count += 1
                    body_raw = item.get("body", "") or ""
                    body = body_raw[:50000] if len(body_raw) > 50000 else body_raw
                    self.results[num] = Issue(
                        number=num,
                        title=item["title"],
                        state=item["state"],
                        url=item["html_url"],
                        labels=[l["name"] for l in item.get("labels", [])],
                        created_at=item["created_at"][:10],
                        body=body,
                    )

            log.info("         -> %d 结果, %d 新 issues (累计 %d)",
                     len(items), new_count, len(self.results))

            # Early-stop: too many results already
            if len(self.results) >= self.MAX_COLLECT:
                log.warning("  已收集 %d 个 issues，提前停止剩余查询", len(self.results))
                break

            # Early-stop: 3 consecutive queries with 0 new results
            # Threshold raised from i>3 to i>max(5, nq//3) to prevent
            # premature stopping when high keywords are "descriptive phrases"
            # that rarely match verbatim. With R1/R3 interleaving, medium
            # queries now appear early enough to break the zero streak.
            _min_before_stop = max(5, nq // 3)
            if new_count == 0:
                consecutive_zero += 1
                if consecutive_zero >= 3 and i > _min_before_stop:
                    log.info("  连续 3 条查询无新结果（已执行 %d/%d），跳过剩余查询", i, nq)
                    break
            else:
                consecutive_zero = 0

            time.sleep(0.5)

        log.info("\n  阶段 1 完成: 收集 %d 个唯一 issues", len(self.results))

    def fetch_details(self, config: SearchConfig,
                      low_threshold: float = 3.0,
                      high_threshold: float = 8.0,
                      concurrency: int = 0):
        """Phase 2: Fetch comments for borderline issues."""
        borderline = [
            issue for issue in self.results.values()
            if low_threshold <= issue.relevance_score < high_threshold
            and not issue.comments_fetched
        ]

        if not borderline:
            log.info("\n  阶段 2: 无需搜索 comments (没有待搜索的中间分数段 issue)")
            return

        has_token = self.api.has_token
        if concurrency <= 0:
            concurrency = 4 if has_token else 1

        log.info("\n%s", "="*60)
        log.info(" 阶段 2: 搜索 comments")
        log.info("%s", "="*60)
        log.info(" 待搜索: %d 个 issues (score %s~%s)", len(borderline), low_threshold, high_threshold)
        log.info(" 并发数: %d", concurrency)
        log.info(" 跳过高分: %d 个", sum(1 for i in self.results.values() if i.relevance_score >= high_threshold))
        log.info(" 跳过低分: %d 个", sum(1 for i in self.results.values() if i.relevance_score < low_threshold))
        skipped_cached = sum(1 for i in self.results.values()
                             if low_threshold <= i.relevance_score < high_threshold
                             and i.comments_fetched)
        if skipped_cached:
            log.info(" 跳过已缓存: %d 个", skipped_cached)

        if not has_token and len(borderline) > 50:
            estimated_time = len(borderline) * 60 // max(concurrency, 1)
            log.warning("\n  [警告] 无 GITHUB_TOKEN，%d 个 issue 的评论搜索", len(borderline))
            log.warning("         预计需要 %d 分钟 (REST API 限制 60次/小时)", estimated_time // 60)
            log.warning("         强烈建议设置 GITHUB_TOKEN:")
            log.warning("           Windows: $env:GITHUB_TOKEN = 'ghp_xxxx'")
            log.warning("           Linux:   export GITHUB_TOKEN=ghp_xxxx")

        log.info("%s\n", "="*60)

        keywords = config.all_keywords
        print_lock = Lock()
        completed = [0]

        def _process_issue(issue: Issue) -> None:
            comments_text = self._fetch_comments(issue.number)
            issue.comments_text = comments_text
            issue.comments_fetched = True

            if comments_text:
                text_lower = comments_text.lower()
                for keyword in keywords:
                    if keyword.lower() in text_lower:
                        issue.matched_in_comments.add(keyword)

            with print_lock:
                completed[0] += 1
                idx = completed[0]
                if comments_text:
                    log.info("  [%d/%d] #%d: %s... -> %d 字, %d 关键词命中",
                             idx, len(borderline), issue.number, issue.title[:50],
                             len(comments_text), len(issue.matched_in_comments))
                else:
                    log.info("  [%d/%d] #%d: %s... -> 无评论",
                             idx, len(borderline), issue.number, issue.title[:50])

        if concurrency == 1:
            for issue in borderline:
                _process_issue(issue)
                time.sleep(0.3)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(_process_issue, issue): issue
                           for issue in borderline}
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc:
                        issue = futures[future]
                        with print_lock:
                            log.error("  [!] #%d 评论获取失败: %s", issue.number, exc)

        log.info("\n  阶段 2 完成: 搜索了 %d 个 issue 的评论", len(borderline))

    def _fetch_comments(self, issue_number: int) -> str:
        """Fetch all comments for a single issue."""
        url = f"https://api.github.com/repos/{self.repo}/issues/{issue_number}/comments"
        all_comments = []
        page = 1
        while True:
            data = self.api.get(url, params={"per_page": 100, "page": page}, api_type="core")
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            for comment in data:
                body = comment.get("body", "") or ""
                if body.strip():
                    all_comments.append(body)
            if len(data) < 100:
                break
            page += 1
        return "\n\n".join(all_comments)
