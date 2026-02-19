"""Tests for multi-type cache support in core/cache.py."""

import json
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import (
    Issue, PullRequest, CodeResult, CommitResult, DiscussionResult,
)
from core.cache import save_cache, load_cache


# ============================================================================
# PR cache tests
# ============================================================================

class TestPRCache:
    def test_save_and_load_pr(self, tmp_path):
        path = str(tmp_path / "cache.json")
        prs = {
            10: PullRequest(
                number=10, title="Fix bug", state="closed", merged=True,
                url="https://github.com/org/repo/pull/10",
                labels=["bugfix"], created_at="2025-01-10",
                body="Fix the crash", linked_issues=[5],
                changed_files=["src/main.cpp"],
                matched_keywords={"crash"}, relevance_score=12.0,
            ),
        }
        save_cache(prs, "org/repo", path, type_key="prs")

        loaded = {}
        result = load_cache(path, "org/repo", loaded, type_key="prs")
        assert result is True
        assert 10 in loaded
        pr = loaded[10]
        assert pr.title == "Fix bug"
        assert pr.merged is True
        assert pr.linked_issues == [5]
        assert "crash" in pr.matched_keywords
        assert pr.relevance_score == 12.0

    def test_pr_fields_roundtrip(self, tmp_path):
        path = str(tmp_path / "cache.json")
        prs = {
            20: PullRequest(
                number=20, title="Add feature", state="open", merged=False,
                url="https://github.com/org/repo/pull/20",
                labels=["enhancement"], created_at="2025-02-01",
                body="New feature", review_comments_text="looks good",
                comments_fetched=True,
                changed_files=["a.py", "b.py"],
                matched_keywords={"feature"}, matched_in_comments={"feature"},
                relevance_score=7.5,
            ),
        }
        save_cache(prs, "org/repo", path, type_key="prs")
        loaded = {}
        load_cache(path, "org/repo", loaded, type_key="prs")
        pr = loaded[20]
        assert pr.review_comments_text == "looks good"
        assert pr.comments_fetched is True
        assert "feature" in pr.matched_in_comments


# ============================================================================
# Code cache tests
# ============================================================================

class TestCodeCache:
    def test_save_and_load_code(self, tmp_path):
        path = str(tmp_path / "cache.json")
        code = {
            "src/lib.cpp": CodeResult(
                path="src/lib.cpp",
                url="https://github.com/org/repo/blob/main/src/lib.cpp",
                repo="org/repo", sha="def456",
                content_snippet="void handle_fault()",
                matched_keywords={"fault"}, relevance_score=8.0,
            ),
        }
        save_cache(code, "org/repo", path, type_key="code")

        loaded = {}
        result = load_cache(path, "org/repo", loaded, type_key="code")
        assert result is True
        assert "src/lib.cpp" in loaded
        assert loaded["src/lib.cpp"].sha == "def456"


# ============================================================================
# Commit cache tests
# ============================================================================

class TestCommitCache:
    def test_save_and_load_commit(self, tmp_path):
        path = str(tmp_path / "cache.json")
        commits = {
            "aaa1111": CommitResult(
                sha="aaa1111222233334444",
                message="fix: handle page fault gracefully",
                url="https://github.com/org/repo/commit/aaa1111",
                author="dev1", date="2025-03-01",
                changed_files=["src/handler.cpp"],
                matched_keywords={"page fault"}, relevance_score=11.0,
            ),
        }
        save_cache(commits, "org/repo", path, type_key="commits")

        loaded = {}
        result = load_cache(path, "org/repo", loaded, type_key="commits")
        assert result is True
        assert "aaa1111222233334444" in loaded
        c = loaded["aaa1111222233334444"]
        assert c.author == "dev1"
        assert "page fault" in c.matched_keywords


# ============================================================================
# Discussion cache tests
# ============================================================================

class TestDiscussionCache:
    def test_save_and_load_discussion(self, tmp_path):
        path = str(tmp_path / "cache.json")
        discs = {
            50: DiscussionResult(
                number=50, title="GPU segfault help",
                url="https://github.com/org/repo/discussions/50",
                category="Q&A", created_at="2025-04-01",
                body="Need help with segfault",
                answer_body="Try disabling feature X",
                comments_text="I also have this",
                matched_keywords={"segfault"},
                matched_in_comments=set(),
                relevance_score=9.0,
            ),
        }
        save_cache(discs, "org/repo", path, type_key="discussions")

        loaded = {}
        result = load_cache(path, "org/repo", loaded, type_key="discussions")
        assert result is True
        assert 50 in loaded
        d = loaded[50]
        assert d.category == "Q&A"
        assert d.answer_body == "Try disabling feature X"
        assert "segfault" in d.matched_keywords


# ============================================================================
# Multi-type in one file
# ============================================================================

class TestMultiTypeCache:
    def test_multiple_types_coexist(self, tmp_path):
        """Save issues then prs to the same file, both should load."""
        path = str(tmp_path / "cache.json")

        issues = {
            1: Issue(number=1, title="Bug", state="open",
                     url="u", labels=[], created_at="2025-01-01",
                     matched_keywords={"bug"}, relevance_score=5.0),
        }
        prs = {
            10: PullRequest(number=10, title="Fix", state="closed",
                            merged=True, url="u", labels=[],
                            created_at="2025-01-02",
                            matched_keywords={"fix"}, relevance_score=6.0),
        }

        save_cache(issues, "org/repo", path, type_key="issues")
        save_cache(prs, "org/repo", path, type_key="prs")

        # Verify both sections exist in JSON
        with open(path) as f:
            data = json.load(f)
        assert "issues" in data
        assert "prs" in data

        # Load each type independently
        loaded_issues = {}
        load_cache(path, "org/repo", loaded_issues, type_key="issues")
        assert 1 in loaded_issues

        loaded_prs = {}
        load_cache(path, "org/repo", loaded_prs, type_key="prs")
        assert 10 in loaded_prs

    def test_missing_type_returns_false(self, tmp_path):
        """Loading a type that wasn't cached returns False."""
        path = str(tmp_path / "cache.json")
        issues = {
            1: Issue(number=1, title="Bug", state="open",
                     url="u", labels=[], created_at="2025-01-01"),
        }
        save_cache(issues, "org/repo", path, type_key="issues")

        loaded = {}
        result = load_cache(path, "org/repo", loaded, type_key="code")
        assert result is False
        assert len(loaded) == 0
