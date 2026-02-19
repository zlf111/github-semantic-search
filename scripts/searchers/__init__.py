"""GitHub Semantic Search — Searcher plugins."""

from searchers.base import BaseSearcher
from searchers.issue import IssueSearcher
from searchers.pr import PRSearcher
from searchers.code import CodeSearcher
from searchers.commit import CommitSearcher
from searchers.discussion import DiscussionSearcher

__all__ = [
    "BaseSearcher", "IssueSearcher", "PRSearcher",
    "CodeSearcher", "CommitSearcher", "DiscussionSearcher",
]
