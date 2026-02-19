"""PR searcher — searches GitHub Pull Requests via Search API."""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from core.api_client import GitHubApiClient
from core.models import PullRequest, SearchConfig
from searchers.base import BaseSearcher

log = logging.getLogger("gss.searcher.pr")

# Pattern to detect linked issues in PR body: "fixes #123", "closes #456", etc.
_LINKED_ISSUE_RE = re.compile(
    r"(?:fix(?:es|ed)?|close(?:s|d)?|resolve(?:s|d)?)\s+#(\d+)",
    re.IGNORECASE,
)


class PRSearcher(BaseSearcher):
    """Search GitHub PRs with review comment fetching."""

    name = "prs"

    def __init__(self, api_client: GitHubApiClient, repo: str):
        super().__init__(api_client, repo)
        self.results: dict[int, PullRequest] = {}

    def build_query(self, query_template: str, config: SearchConfig) -> str:
        """Adapt query template for PR search: replace {component}, add is:pr."""
        component = config.component
        if component:
            query = query_template.replace("{component}", component)
        else:
            query = query_template.replace("{component}", "").replace("  ", " ").strip()
        qualifiers = config.filter_qualifiers
        if qualifiers:
            query = f"{query} {qualifiers}"
        return query

    # Maximum unique results before stopping early
    MAX_COLLECT = 500

    def collect(self, config: SearchConfig):
        """Phase 1: Search PR title + body via GitHub Search API."""
        queries = config.queries
        component = config.component

        log.info("\n" + "="*60)
        log.info(" PR 搜索: 阶段 1 — 搜索 title + body")
        log.info("="*60)
        log.info(" 仓库: %s", config.repo)
        comp_display = component if component else "(不限)"
        log.info(" 组件: %s", comp_display)
        log.info(" 主题: %s", config.topic)
        nq = len(queries)
        log.info(" 查询数: %d", nq)
        log.info("="*60 + "\n")

        seen_queries = set()
        consecutive_zero = 0

        for i, query_template in enumerate(queries, 1):
            query = self.build_query(query_template, config)
            full_query = f"repo:{self.repo} is:pr {query}"

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

                    # Detect linked issues from body
                    linked = [int(m) for m in _LINKED_ISSUE_RE.findall(body)]

                    # Check merged status from pull_request field
                    pr_info = item.get("pull_request", {})
                    merged = pr_info.get("merged_at") is not None

                    self.results[num] = PullRequest(
                        number=num,
                        title=item["title"],
                        state=item["state"],
                        merged=merged,
                        url=item["html_url"],
                        labels=[l["name"] for l in item.get("labels", [])],
                        created_at=item["created_at"][:10],
                        body=body,
                        linked_issues=linked,
                    )

            log.info("         -> %d 结果, %d 新 PRs (累计 %d)",
                     len(items), new_count, len(self.results))

            # Early-stop: too many results
            if len(self.results) >= self.MAX_COLLECT:
                log.warning("  已收集 %d 个 PRs，提前停止剩余查询", len(self.results))
                break

            # Early-stop: consecutive queries with 0 new results
            # Threshold raised from i>3 to i>max(5, nq//3) to prevent
            # premature stopping when high keywords are "descriptive phrases"
            _min_before_stop = max(5, nq // 3)
            if new_count == 0:
                consecutive_zero += 1
                if consecutive_zero >= 3 and i > _min_before_stop:
                    log.info("  连续 3 条查询无新结果（已执行 %d/%d），跳过剩余查询", i, nq)
                    break
            else:
                consecutive_zero = 0

            time.sleep(0.5)

        n = len(self.results)
        log.info("\n  PR 阶段 1 完成: 收集 %d 个唯一 PRs", n)

    def fetch_details(self, config: SearchConfig,
                      low_threshold: float = 3.0,
                      high_threshold: float = 8.0,
                      concurrency: int = 0):
        """Phase 2: Fetch review comments + changed files for borderline PRs."""
        borderline = [
            pr for pr in self.results.values()
            if low_threshold <= pr.relevance_score < high_threshold
            and not pr.comments_fetched
        ]

        if not borderline:
            log.info("\n  PR 阶段 2: 无需获取详情")
            return

        has_token = self.api.has_token
        if concurrency <= 0:
            concurrency = 4 if has_token else 1

        n = len(borderline)
        log.info("\n" + "="*60)
        log.info(" PR 搜索: 阶段 2 — 获取 review comments + changed files")
        log.info("="*60)
        log.info(" 待获取: %d 个 PRs (score %s~%s)", n, low_threshold, high_threshold)
        log.info(" 并发数: %d", concurrency)
        log.info("="*60 + "\n")

        keywords = config.all_keywords
        print_lock = Lock()
        completed = [0]

        def _process_pr(pr: PullRequest) -> None:
            # Fetch review comments
            review_text = self._fetch_review_comments(pr.number)
            pr.review_comments_text = review_text
            pr.comments_fetched = True

            # Fetch changed files (lightweight, only file names)
            files = self._fetch_changed_files(pr.number)
            pr.changed_files = files

            # Match keywords in review comments
            if review_text:
                text_lower = review_text.lower()
                for keyword in keywords:
                    if keyword.lower() in text_lower:
                        pr.matched_in_comments.add(keyword)

            with print_lock:
                completed[0] += 1
                idx = completed[0]
                n_files = len(files)
                n_kw = len(pr.matched_in_comments)
                log.info("  [%d/%d] #%d: %s... -> %d files, %d kw",
                         idx, n, pr.number, pr.title[:50], n_files, n_kw)

        if concurrency == 1:
            for pr in borderline:
                _process_pr(pr)
                time.sleep(0.3)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(_process_pr, pr): pr for pr in borderline}
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc:
                        pr = futures[future]
                        with print_lock:
                            log.warning("  [!] #%d 详情获取失败: %s", pr.number, exc)

        log.info("\n  PR 阶段 2 完成: 获取了 %d 个 PR 的详情", n)

    def _fetch_review_comments(self, pr_number: int) -> str:
        """Fetch all review comments for a PR."""
        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/comments"
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
        # Also fetch issue-style comments (general PR discussion)
        url2 = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"
        page = 1
        while True:
            data = self.api.get(url2, params={"per_page": 100, "page": page}, api_type="core")
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

    def _fetch_changed_files(self, pr_number: int) -> list[str]:
        """Fetch list of changed file paths for a PR."""
        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/files"
        files = []
        page = 1
        while True:
            data = self.api.get(url, params={"per_page": 100, "page": page}, api_type="core")
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            for f in data:
                filename = f.get("filename", "")
                if filename:
                    files.append(filename)
            if len(data) < 100:
                break
            page += 1
        return files
