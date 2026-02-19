"""Base searcher interface for all content types."""

from abc import ABC, abstractmethod
from typing import Any

from core.api_client import GitHubApiClient
from core.models import SearchConfig


class BaseSearcher(ABC):
    """Abstract base class for GitHub content type searchers.

    Each searcher handles one content type (issues, PRs, code, commits, etc.)
    and implements the search → collect → fetch_details pipeline.
    """

    name: str = "base"

    def __init__(self, api_client: GitHubApiClient, repo: str):
        self.api = api_client
        self.repo = repo
        self.results: dict[int, Any] = {}

    @abstractmethod
    def build_query(self, query_template: str, config: SearchConfig) -> str:
        """Adapt a generic query template for this content type's API."""
        ...

    @abstractmethod
    def collect(self, config: SearchConfig):
        """Execute search and populate self.results."""
        ...

    @abstractmethod
    def fetch_details(self, config: SearchConfig, **kwargs):
        """Fetch additional details (comments, diffs, etc.) for results."""
        ...
