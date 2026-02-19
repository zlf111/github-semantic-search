"""Code searcher — searches GitHub code via Search API."""

import logging
import time

from core.api_client import GitHubApiClient
from core.models import CodeResult, SearchConfig
from searchers.base import BaseSearcher

log = logging.getLogger("gss.searcher.code")


class CodeSearcher(BaseSearcher):
    """Search code files in a GitHub repository.

    Note: GitHub Code Search API has special requirements:
    - Must be authenticated (requires token)
    - Only indexes files < 384 KB
    - Only indexes repos with activity in the last year
    - Returns up to 100 results per query (1000 total with pagination)
    - Requires Accept: application/vnd.github.text-match+json for text_matches
    """

    name = "code"
    # text-match media type enables text_matches with code snippets
    TEXT_MATCH_HEADERS = {
        "Accept": "application/vnd.github.text-match+json"
    }

    def __init__(self, api_client: GitHubApiClient, repo: str):
        super().__init__(api_client, repo)
        self.results: dict[str, CodeResult] = {}  # keyed by file path

    def build_query(self, query_template: str, config: SearchConfig) -> str:
        """Adapt query template for code search."""
        component = config.component
        if component:
            query = query_template.replace("{component}", component)
        else:
            query = query_template.replace("{component}", "").replace("  ", " ").strip()
        # Code search doesn't support date/state filters
        return query

    def collect(self, config: SearchConfig):
        """Search code files matching keywords."""
        queries = config.queries
        component = config.component

        if not self.api.has_token:
            log.warning("\n  [!] Code 搜索需要 GITHUB_TOKEN，跳过")
            return

        nq = len(queries)
        log.info(f"\n{'='*60}")
        log.info(" Code 搜索")
        log.info(f"{'='*60}")
        log.info(f" 仓库: {config.repo}")
        comp_display = component if component else "(不限)"
        log.info(f" 组件: {comp_display}")
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
                "https://api.github.com/search/code",
                full_query,
                per_page=50,  # Code search returns smaller pages
                headers=self.TEXT_MATCH_HEADERS,
                max_pages=config.max_pages,
            )
            new_count = 0

            for item in items:
                path = item.get("path", "")
                if path and path not in self.results:
                    new_count += 1
                    # Extract text fragment from text_matches if available
                    snippet = ""
                    for tm in item.get("text_matches", []):
                        frag = tm.get("fragment", "")
                        if frag:
                            snippet = frag[:500]
                            break

                    self.results[path] = CodeResult(
                        path=path,
                        url=item.get("html_url", ""),
                        repo=self.repo,
                        sha=item.get("sha", ""),
                        content_snippet=snippet,
                    )

            log.info("         -> %d 结果, %d 新文件", len(items), new_count)
            time.sleep(1)  # Code search has stricter rate limits

        n = len(self.results)
        log.info("\n  Code 搜索完成: 找到 %d 个文件", n)

    def fetch_details(self, config: SearchConfig, **kwargs):
        """Code search doesn't need extra detail fetching."""
        pass
