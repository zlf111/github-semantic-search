"""GitHub API client with rate limiting and retry logic."""

import logging
import os
import time
from typing import Optional

try:
    import requests
except ImportError:
    raise ImportError("requests library required. Install with: pip install requests")

log = logging.getLogger("gss.api")


class GitHubApiClient:
    """Unified GitHub API client (REST + GraphQL).

    Manages authentication, rate limiting, and retry with exponential backoff.
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.session = requests.Session()
        if self.token:
            self.session.headers["Authorization"] = f"token {self.token}"
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "github-semantic-search/5.0"
        # Rate limit tracking
        self._search_rate_remaining = 30
        self._core_rate_remaining = 5000
        self._search_rate_reset = 0
        self._core_rate_reset = 0

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    def check_core_budget(self) -> int:
        """Query GitHub /rate_limit to get current Core API remaining quota.

        Returns remaining count (0 if request fails or no token).
        """
        if not self.token:
            return 60  # unauthenticated default
        try:
            resp = self.session.get(
                "https://api.github.com/rate_limit", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                core = data.get("resources", {}).get("core", {})
                remaining = core.get("remaining", 0)
                self._core_rate_remaining = remaining
                reset_ts = core.get("reset", 0)
                if reset_ts:
                    self._core_rate_reset = int(reset_ts)
                return remaining
        except Exception:
            pass
        return self._core_rate_remaining

    def _wait_if_needed(self, api_type: str = "core"):
        """Check rate limit and wait if necessary.

        After waiting, does NOT blindly reset counters — lets the next
        API response update them via response headers.  Sets remaining
        to a small positive value (3) so we don't immediately re-wait,
        but still trigger another wait quickly if the reset didn't
        actually happen yet.
        """
        now = int(time.time())
        if api_type == "search" and self._search_rate_remaining < 3:
            wait = max(self._search_rate_reset - now, 5)
            wait = min(wait, 65)
            # Always print rate-limit waits (visible even in -q mode)
            msg = (f"Search API 配额不足 (剩余 {self._search_rate_remaining})，"
                   f"等待 {wait}s...")
            log.warning(msg)
            print(f"  ⏳ {msg}", flush=True)
            time.sleep(wait)
            # Don't assume full recovery — use conservative value;
            # the real count will be updated from response headers.
            self._search_rate_remaining = 3
        elif api_type == "core" and self._core_rate_remaining < 5:
            wait = max(self._core_rate_reset - now, 5)
            wait = min(wait, 65)
            msg = (f"REST API 配额不足 (剩余 {self._core_rate_remaining})，"
                   f"等待 {wait}s...")
            log.warning(msg)
            print(f"  ⏳ {msg}", flush=True)
            time.sleep(wait)
            self._core_rate_remaining = 5

    def get(self, url: str, params: dict = None,
            api_type: str = "core", headers: dict = None,
            _retries: int = 0, _max_retries: int = 3) -> Optional[dict | list]:
        """Send GET request with rate limiting, retry, and error handling.

        Args:
            headers: Extra headers to merge with session defaults for this request.
        """
        self._wait_if_needed(api_type)
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=30)

            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset_ts = resp.headers.get("X-RateLimit-Reset")
            if remaining is not None:
                remaining = int(remaining)
                reset_val = int(reset_ts) if reset_ts else 0
                if api_type == "search":
                    self._search_rate_remaining = remaining
                    self._search_rate_reset = reset_val
                else:
                    self._core_rate_remaining = remaining
                    self._core_rate_reset = reset_val

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 403:
                if _retries >= _max_retries:
                    log.error("速率限制: 已重试 %d 次仍失败，跳过", _max_retries)
                    return None
                reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(reset_time - int(time.time()), 10)
                wait = min(wait, 120)
                log.warning("速率限制 (HTTP 403)，等待 %ds... (重试 %d/%d)",
                            wait, _retries + 1, _max_retries)
                time.sleep(wait)
                return self.get(url, params, api_type, headers=headers,
                                _retries=_retries + 1, _max_retries=_max_retries)
            elif resp.status_code == 422:
                log.warning("查询格式错误，跳过")
                return None
            elif resp.status_code >= 500 and _retries < _max_retries:
                wait = 2 ** _retries * 2
                log.warning("服务端错误 %d，%ds 后重试...",
                            resp.status_code, wait)
                time.sleep(wait)
                return self.get(url, params, api_type, headers=headers,
                                _retries=_retries + 1, _max_retries=_max_retries)
            else:
                log.error("API 错误 %d: %s",
                          resp.status_code, resp.text[:200])
                return None
        except requests.ConnectionError as e:
            if _retries < _max_retries:
                wait = 2 ** _retries * 2
                log.warning("连接失败，%ds 后重试... (%s)", wait, e)
                time.sleep(wait)
                return self.get(url, params, api_type, headers=headers,
                                _retries=_retries + 1, _max_retries=_max_retries)
            log.error("请求失败 (已重试 %d 次): %s", _max_retries, e)
            return None
        except requests.RequestException as e:
            log.error("请求失败: %s", e)
            return None

    def search(self, endpoint: str, query: str, per_page: int = 100,
               headers: dict = None, max_pages: int = 3) -> list[dict]:
        """Execute a GitHub Search API query with pagination.

        Args:
            endpoint: Search endpoint (e.g. "https://api.github.com/search/issues")
            query: Full search query string
            per_page: Results per page (max 100)
            headers: Extra headers for this request (e.g. text-match media type)
            max_pages: Maximum pages to fetch per query (default 3 = 300 items).
                       Prevents runaway pagination on broad queries.

        Returns:
            List of result items from all pages
        """
        all_items = []
        page = 1
        warned_limit = False

        while page <= max_pages:
            data = self.get(
                endpoint,
                params={"q": query, "per_page": per_page, "page": page},
                api_type="search",
                headers=headers,
            )
            if not data or not isinstance(data, dict) or "items" not in data:
                break

            items = data["items"]
            all_items.extend(items)
            total = data.get("total_count", 0)

            if total > 1000 and not warned_limit:
                warned_limit = True
                log.warning("此查询匹配 %d 个结果，GitHub API 上限 1000 条"
                            "（本次最多取 %d 页 = %d 条）",
                            total, max_pages, max_pages * per_page)

            if len(all_items) >= total or len(items) < per_page or len(all_items) >= 1000:
                break
            page += 1
            time.sleep(0.5)

        return all_items

    # ------------------------------------------------------------------
    # GraphQL
    # ------------------------------------------------------------------

    def graphql(self, query: str, variables: dict = None,
                _retries: int = 0, _max_retries: int = 3) -> Optional[dict]:
        """Send a GraphQL request to GitHub API with retry and rate-limit handling.

        Args:
            query: GraphQL query string.
            variables: Variables dict for the query.
            _retries: Current retry attempt (internal).
            _max_retries: Maximum number of retries.

        Returns:
            The ``data`` field of the response, or ``None`` on failure.
        """
        url = "https://api.github.com/graphql"
        payload = {"query": query, "variables": variables or {}}

        self._wait_if_needed("core")  # GraphQL shares the core rate pool

        try:
            resp = self.session.post(url, json=payload, timeout=30)

            # Update rate-limit tracking from response headers
            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset_ts = resp.headers.get("X-RateLimit-Reset")
            if remaining is not None:
                self._core_rate_remaining = int(remaining)
                self._core_rate_reset = int(reset_ts) if reset_ts else 0

            if resp.status_code == 200:
                data = resp.json()
                if "errors" in data:
                    for err in data["errors"][:3]:
                        log.error("GraphQL error: %s", err.get("message", "?"))
                    return None
                return data.get("data")

            elif resp.status_code == 401:
                log.error("GraphQL 需要认证 (GITHUB_TOKEN)")
                return None

            elif resp.status_code == 403:
                if _retries >= _max_retries:
                    log.error("GraphQL 速率限制: 已重试 %d 次仍失败，跳过",
                              _max_retries)
                    return None
                reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(reset_time - int(time.time()), 10)
                wait = min(wait, 120)
                log.warning("GraphQL 速率限制 (HTTP 403)，等待 %ds... "
                            "(重试 %d/%d)", wait, _retries + 1, _max_retries)
                time.sleep(wait)
                return self.graphql(query, variables,
                                    _retries=_retries + 1, _max_retries=_max_retries)

            elif resp.status_code >= 500 and _retries < _max_retries:
                wait = 2 ** _retries * 2
                log.warning("GraphQL 服务端错误 %d，%ds 后重试...",
                            resp.status_code, wait)
                time.sleep(wait)
                return self.graphql(query, variables,
                                    _retries=_retries + 1, _max_retries=_max_retries)
            else:
                log.error("GraphQL 错误 %d: %s",
                          resp.status_code, resp.text[:200])
                return None

        except requests.ConnectionError as e:
            if _retries < _max_retries:
                wait = 2 ** _retries * 2
                log.warning("GraphQL 连接失败，%ds 后重试... (%s)", wait, e)
                time.sleep(wait)
                return self.graphql(query, variables,
                                    _retries=_retries + 1, _max_retries=_max_retries)
            log.error("GraphQL 请求失败 (已重试 %d 次): %s", _max_retries, e)
            return None
        except requests.RequestException as e:
            log.error("GraphQL 请求失败: %s", e)
            return None
