"""GitHub Semantic Search — Core modules."""

import logging

from core.models import Issue, SearchConfig
from core.api_client import GitHubApiClient
from core.scorer import KeywordScorer
from core.cache import save_cache, load_cache
from core.report import format_markdown, format_json
from core.query_builder import build_queries, merge_seed_synonyms

__all__ = [
    "Issue", "SearchConfig",
    "GitHubApiClient",
    "KeywordScorer",
    "save_cache", "load_cache",
    "format_markdown", "format_json",
    "build_queries", "merge_seed_synonyms",
]

# Package-level logger — all submodules use logging.getLogger(__name__)
# which resolves to e.g. "core.api_client", "core.scorer", etc.
# The root "core" logger can be configured once in the entry point.
logger = logging.getLogger("gss")  # gss = GitHub Semantic Search
