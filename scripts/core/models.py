"""Data models for GitHub Semantic Search."""

import json
import re
from dataclasses import dataclass, field

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ============================================================================
# Result types
# ============================================================================

@dataclass
class Issue:
    """Single GitHub issue with scoring metadata."""
    number: int
    title: str
    state: str
    url: str
    labels: list
    created_at: str
    body: str = ""
    comments_text: str = ""
    comments_fetched: bool = False
    matched_keywords: set = field(default_factory=set)
    matched_in_comments: set = field(default_factory=set)
    relevance_score: float = 0.0


@dataclass
class PullRequest:
    """Single GitHub PR with scoring metadata."""
    number: int
    title: str
    state: str
    merged: bool
    url: str
    labels: list
    created_at: str
    body: str = ""
    review_comments_text: str = ""
    comments_fetched: bool = False
    linked_issues: list = field(default_factory=list)
    changed_files: list = field(default_factory=list)
    matched_keywords: set = field(default_factory=set)
    matched_in_comments: set = field(default_factory=set)
    relevance_score: float = 0.0


@dataclass
class CodeResult:
    """Single code search result."""
    path: str
    url: str
    repo: str
    sha: str = ""
    content_snippet: str = ""
    matched_keywords: set = field(default_factory=set)
    relevance_score: float = 0.0


@dataclass
class CommitResult:
    """Single commit search result."""
    sha: str
    message: str
    url: str
    author: str
    date: str
    changed_files: list = field(default_factory=list)
    matched_keywords: set = field(default_factory=set)
    relevance_score: float = 0.0


@dataclass
class DiscussionResult:
    """Single GitHub Discussion result (from GraphQL)."""
    number: int
    title: str
    url: str
    category: str
    created_at: str
    body: str = ""
    answer_body: str = ""
    comments_text: str = ""
    matched_keywords: set = field(default_factory=set)
    matched_in_comments: set = field(default_factory=set)
    relevance_score: float = 0.0


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class SearchConfig:
    """搜索配置，可从 JSON 文件或命令行参数构建。"""
    repo: str = "ROCm/rocm-libraries"
    component: str = ""
    topic: str = "page fault"
    state_filter: str = ""
    date_from: str = ""
    date_to: str = ""
    exclude_issues: list = field(default_factory=list)
    search_types: list = field(default_factory=lambda: ["issues"])
    keywords_high: list = field(default_factory=list)
    keywords_medium: list = field(default_factory=list)
    keywords_low: list = field(default_factory=list)
    queries: list = field(default_factory=list)
    max_pages: int = 3  # Max pages per search query (3 = 300 items max)

    @property
    def all_keywords(self) -> frozenset:
        cache_key = "_all_keywords_cache"
        if not hasattr(self, cache_key):
            object.__setattr__(self, cache_key,
                frozenset(self.keywords_high + self.keywords_medium + self.keywords_low))
        return getattr(self, cache_key)

    @property
    def keyword_weight_map(self) -> dict:
        cache_key = "_kw_weight_cache"
        if not hasattr(self, cache_key):
            mapping = {}
            for kw in self.keywords_high:
                mapping[kw.lower()] = 5.0
            for kw in self.keywords_medium:
                mapping[kw.lower()] = 3.0
            for kw in self.keywords_low:
                mapping[kw.lower()] = 1.0
            object.__setattr__(self, cache_key, mapping)
        return getattr(self, cache_key)

    @property
    def has_component(self) -> bool:
        return bool(self.component and self.component.strip())

    def validate(self) -> list[str]:
        errors = []
        for field_name, value in [("date_from", self.date_from), ("date_to", self.date_to)]:
            if value and not _ISO_DATE_RE.match(value):
                errors.append(f"{field_name}={value!r} 格式错误，应为 YYYY-MM-DD")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            errors.append(f"date_from ({self.date_from}) 晚于 date_to ({self.date_to})")
        if self.state_filter and self.state_filter not in ("open", "closed"):
            errors.append(f"state_filter={self.state_filter!r} 无效")
        valid_types = {"issues", "prs", "code", "commits", "discussions"}
        for st in self.search_types:
            if st not in valid_types:
                errors.append(f"search_types 包含无效值: {st!r}")
        return errors

    @property
    def filter_qualifiers(self) -> str:
        parts = []
        if self.state_filter in ("open", "closed"):
            parts.append(f"is:{self.state_filter}")
        if self.date_from and self.date_to:
            parts.append(f"created:{self.date_from}..{self.date_to}")
        elif self.date_from:
            parts.append(f"created:>={self.date_from}")
        elif self.date_to:
            parts.append(f"created:<={self.date_to}")
        return " ".join(parts)

    @classmethod
    def from_json(cls, path: str) -> "SearchConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = cls()
        cfg.repo = data.get("repo", cfg.repo)
        cfg.component = data.get("component", "") or ""
        cfg.topic = data.get("topic", cfg.topic)
        filters = data.get("filters", {})
        cfg.state_filter = filters.get("state", "") or ""
        cfg.date_from = filters.get("date_from", "") or ""
        cfg.date_to = filters.get("date_to", "") or ""
        cfg.exclude_issues = [int(x) for x in data.get("exclude_issues", [])]
        cfg.search_types = data.get("search_types", ["issues"])
        kw = data.get("keywords", {})
        cfg.keywords_high = kw.get("high", [])
        cfg.keywords_medium = kw.get("medium", [])
        cfg.keywords_low = kw.get("low", [])
        cfg.queries = data.get("queries", [])
        return cfg

    def to_json(self, path: str):
        data = {
            "repo": self.repo,
            "component": self.component,
            "topic": self.topic,
            "search_types": self.search_types,
            "filters": {
                "state": self.state_filter,
                "date_from": self.date_from,
                "date_to": self.date_to,
            },
            "exclude_issues": self.exclude_issues,
            "keywords": {
                "high": self.keywords_high,
                "medium": self.keywords_medium,
                "low": self.keywords_low,
            },
            "queries": self.queries,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
