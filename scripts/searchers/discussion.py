"""Discussion searcher — searches GitHub Discussions via GraphQL API."""

import logging
import time

from core.api_client import GitHubApiClient
from core.models import DiscussionResult, SearchConfig
from searchers.base import BaseSearcher

log = logging.getLogger("gss.searcher.discussion")

# GraphQL query to search discussions by keyword in a repo
_SEARCH_QUERY = """
query($query: String!, $first: Int!, $after: String) {
  search(query: $query, type: DISCUSSION, first: $first, after: $after) {
    discussionCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on Discussion {
        number
        title
        url
        createdAt
        body
        category {
          name
        }
        answer {
          body
        }
        comments(first: 10) {
          nodes {
            body
          }
        }
      }
    }
  }
}
"""


class DiscussionSearcher(BaseSearcher):
    """Search GitHub Discussions via GraphQL API.

    Requires GITHUB_TOKEN (GraphQL API is not available without auth).
    """

    name = "discussions"

    def __init__(self, api_client: GitHubApiClient, repo: str):
        super().__init__(api_client, repo)
        self.results: dict[int, DiscussionResult] = {}

    def build_query(self, query_template: str, config: SearchConfig) -> str:
        component = config.component
        if component:
            query = query_template.replace("{component}", component)
        else:
            query = query_template.replace("{component}", "").replace("  ", " ").strip()
        return query

    def collect(self, config: SearchConfig):
        """Search Discussions via GraphQL."""
        queries = config.queries

        if not self.api.has_token:
            log.warning("\n  [!] Discussion 搜索需要 GITHUB_TOKEN，跳过")
            return

        nq = len(queries)
        log.info(f"\n{'='*60}")
        log.info(" Discussion 搜索 (GraphQL)")
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

            # Paginate GraphQL results
            after = None
            total_items = 0
            new_count = 0
            while True:
                variables = {
                    "query": full_query,
                    "first": 50,
                    "after": after,
                }
                data = self.api.graphql(_SEARCH_QUERY, variables)
                if not data:
                    break

                search_data = data.get("search", {})
                nodes = search_data.get("nodes", [])
                if not nodes:
                    break

                for node in nodes:
                    if not node or "number" not in node:
                        continue
                    num = node["number"]
                    if num not in self.results:
                        new_count += 1
                        body = (node.get("body", "") or "")[:50000]
                        answer = node.get("answer")
                        answer_body = (answer.get("body", "") or "") if answer else ""
                        comments = node.get("comments", {}).get("nodes", [])
                        comments_text = "\n\n".join(
                            c.get("body", "") for c in comments if c and c.get("body")
                        )
                        category = node.get("category", {})
                        cat_name = category.get("name", "") if category else ""

                        self.results[num] = DiscussionResult(
                            number=num,
                            title=node.get("title", ""),
                            url=node.get("url", ""),
                            category=cat_name,
                            created_at=node.get("createdAt", "")[:10],
                            body=body,
                            answer_body=answer_body,
                            comments_text=comments_text,
                        )

                total_items += len(nodes)

                page_info = search_data.get("pageInfo", {})
                if page_info.get("hasNextPage") and total_items < 200:
                    after = page_info.get("endCursor")
                    time.sleep(0.5)
                else:
                    break

            log.info("         -> %d 结果, %d 新 discussions", total_items, new_count)
            time.sleep(0.5)

        n = len(self.results)
        log.info("\n  Discussion 搜索完成: 找到 %d 个 discussions", n)

    def fetch_details(self, config: SearchConfig, **kwargs):
        """Discussions already include comments from initial query."""
        pass
