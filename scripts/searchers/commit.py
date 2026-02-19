"""Commit searcher — searches GitHub commits via Search API."""

import logging
import time

from core.api_client import GitHubApiClient
from core.models import CommitResult, SearchConfig
from searchers.base import BaseSearcher

log = logging.getLogger("gss.searcher.commit")


class CommitSearcher(BaseSearcher):
    """Search commits in a GitHub repository by message content."""

    name = "commits"
    # Commit search API header (cloak-preview is legacy but harmless)
    COMMIT_SEARCH_HEADERS = {
        "Accept": "application/vnd.github.cloak-preview+json"
    }

    def __init__(self, api_client: GitHubApiClient, repo: str):
        super().__init__(api_client, repo)
        self.results: dict[str, CommitResult] = {}  # keyed by SHA

    def build_query(self, query_template: str, config: SearchConfig) -> str:
        """Adapt query template for commit search."""
        component = config.component
        if component:
            query = query_template.replace("{component}", component)
        else:
            query = query_template.replace("{component}", "").replace("  ", " ").strip()
        # Add date filters if applicable
        qualifiers = config.filter_qualifiers
        # Convert issue-style filters to commit-style (author-date vs created)
        # GitHub commit search uses "author-date" instead of "created"
        if qualifiers:
            qualifiers = qualifiers.replace("created:", "author-date:")
            query = f"{query} {qualifiers}"
        return query

    def collect(self, config: SearchConfig):
        """Search commits matching keywords."""
        queries = config.queries

        nq = len(queries)
        log.info(f"\n{'='*60}")
        log.info(" Commit 搜索")
        log.info(f"{'='*60}")
        log.info(f" 仓库: {config.repo}")
        log.info(f" 查询数: {nq}")
        log.info(f"{'='*60}\n")

        seen_queries = set()

        for i, query_template in enumerate(queries, 1):
            query = self.build_query(query_template, config)
            full_query = f"repo:{self.repo} {query}"

            query_normalized = " ".join(full_query.split())
            if query_normalized in seen_queries:
                log.info("  [%d/%d] 跳过 (重复)", i, nq)
                continue
            seen_queries.add(query_normalized)

            log.info("  [%d/%d] 搜索: %s...", i, nq, query[:80])

            items = self.api.search(
                "https://api.github.com/search/commits",
                full_query,
                headers=self.COMMIT_SEARCH_HEADERS,
                max_pages=config.max_pages,
            )
            new_count = 0

            for item in items:
                sha = item.get("sha", "")
                if sha and sha not in self.results:
                    new_count += 1
                    commit = item.get("commit", {})
                    author = commit.get("author", {})
                    self.results[sha] = CommitResult(
                        sha=sha,
                        message=commit.get("message", "")[:1000],
                        url=item.get("html_url", ""),
                        author=author.get("name", "unknown"),
                        date=author.get("date", "")[:10],
                    )

            log.info("         -> %d 结果, %d 新 commits", len(items), new_count)
            time.sleep(0.5)

        n = len(self.results)
        log.info("\n  Commit 搜索完成: 找到 %d 个 commits", n)

    def fetch_details(self, config: SearchConfig, **kwargs):
        """Optionally fetch changed files for top commits."""
        # For now, skip — changed files are expensive and rarely needed
        pass
