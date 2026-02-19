"""Tests for core/report.py — report generation module."""

import json
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import (
    Issue, PullRequest, CodeResult, CommitResult,
    DiscussionResult, SearchConfig,
)
from core.report import (
    format_issue_section, format_pr_section, format_code_section,
    format_commit_section, format_discussion_section,
    format_full_report, format_full_json, format_executive_summary,
    _split_results, _extract_snippets,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def config():
    return SearchConfig(
        repo="org/repo",
        component="mylib",
        topic="test topic",
        keywords_high=["crash", "segfault"],
        keywords_medium=["hang"],
        keywords_low=["slow"],
    )


@pytest.fixture
def sample_issues():
    """Dict of issues: 2 keyword-matched, 1 component-only."""
    return {
        101: Issue(
            number=101, title="Crash in mylib", state="open",
            url="https://github.com/org/repo/issues/101",
            labels=["project: mylib"], created_at="2025-01-15",
            body="segfault when running on GPU",
            matched_keywords={"crash", "segfault"}, relevance_score=15.0,
        ),
        102: Issue(
            number=102, title="Hang on startup", state="closed",
            url="https://github.com/org/repo/issues/102",
            labels=["bug"], created_at="2025-02-01",
            body="Process hangs in mylib init",
            matched_keywords={"hang"}, relevance_score=8.0,
        ),
        103: Issue(
            number=103, title="mylib build issue", state="open",
            url="https://github.com/org/repo/issues/103",
            labels=["project: mylib"], created_at="2025-03-01",
            body="Build fails on Windows",
            matched_keywords=set(), relevance_score=5.0,
        ),
    }


@pytest.fixture
def sample_prs():
    return {
        201: PullRequest(
            number=201, title="Fix crash in mylib", state="closed",
            merged=True, url="https://github.com/org/repo/pull/201",
            labels=["bugfix"], created_at="2025-01-20",
            body="Fixes segfault", matched_keywords={"crash", "segfault"},
            relevance_score=17.0, linked_issues=[101],
            changed_files=["src/mylib/core.cpp"],
        ),
        202: PullRequest(
            number=202, title="Update mylib docs", state="closed",
            merged=True, url="https://github.com/org/repo/pull/202",
            labels=[], created_at="2025-02-15",
            body="Documentation update",
            matched_keywords=set(), relevance_score=4.0,
        ),
    }


@pytest.fixture
def sample_code():
    return {
        "src/mylib/fault.cpp": CodeResult(
            path="src/mylib/fault.cpp",
            url="https://github.com/org/repo/blob/main/src/mylib/fault.cpp",
            repo="org/repo", sha="abc123",
            content_snippet="// handle segfault in GPU kernel",
            matched_keywords={"segfault"}, relevance_score=10.0,
        ),
    }


@pytest.fixture
def sample_commits():
    return {
        "abc1234": CommitResult(
            sha="abc1234567890", message="fix: resolve crash in mylib allocator\n\nDetails here",
            url="https://github.com/org/repo/commit/abc1234567890",
            author="dev1", date="2025-01-18",
            matched_keywords={"crash"}, relevance_score=9.5,
        ),
    }


@pytest.fixture
def sample_discussions():
    return {
        301: DiscussionResult(
            number=301, title="Segfault on GPU after mylib update",
            url="https://github.com/org/repo/discussions/301",
            category="Q&A", created_at="2025-03-10",
            body="Getting segfault after update", answer_body="Try version 2.1",
            matched_keywords={"segfault"}, relevance_score=12.0,
        ),
    }


# ============================================================================
# _split_results tests
# ============================================================================

class TestSplitResults:
    def test_splits_keyword_and_component_only(self, sample_issues):
        kw, comp = _split_results(sample_issues.values(), min_score=3.0)
        assert len(kw) == 2  # issues 101, 102
        assert len(comp) == 1  # issue 103

    def test_respects_min_score(self, sample_issues):
        kw, comp = _split_results(sample_issues.values(), min_score=10.0)
        assert len(kw) == 1  # only issue 101 (15.0)
        assert len(comp) == 0

    def test_sorted_descending(self, sample_issues):
        kw, comp = _split_results(sample_issues.values(), min_score=3.0)
        assert kw[0].relevance_score >= kw[1].relevance_score

    def test_empty_input(self):
        kw, comp = _split_results([], min_score=3.0)
        assert kw == []
        assert comp == []


# ============================================================================
# _extract_snippets tests
# ============================================================================

class TestExtractSnippets:
    def test_extracts_from_body(self):
        snippets = _extract_snippets(
            body="This has a segfault error in the code",
            comments="", keywords={"segfault"})
        assert len(snippets) >= 1
        assert "[body]" in snippets[0]

    def test_extracts_from_comments(self):
        snippets = _extract_snippets(
            body="no match here",
            comments="Found a segfault in logs",
            keywords={"segfault"})
        assert len(snippets) >= 1
        assert "[comments]" in snippets[0]

    def test_empty_inputs(self):
        snippets = _extract_snippets("", "", {"crash"})
        assert snippets == []

    def test_max_5_snippets(self):
        body = " ".join(["crash happens"] * 100)
        snippets = _extract_snippets(body, "", {"crash"})
        assert len(snippets) <= 5


# ============================================================================
# Section formatting tests
# ============================================================================

class TestFormatIssueSection:
    def test_returns_markdown_and_stats(self, sample_issues, config):
        md, stats = format_issue_section(sample_issues, config, min_score=3.0)
        assert isinstance(md, str)
        assert isinstance(stats, dict)
        assert "Issues" in md
        assert stats["type_label"] == "Issues"

    def test_keyword_matched_count(self, sample_issues, config):
        md, stats = format_issue_section(sample_issues, config, min_score=3.0)
        assert stats["kw_matched"] == 2
        assert stats["component_only"] == 1

    def test_max_component_limits_display(self, sample_issues, config):
        md, _ = format_issue_section(sample_issues, config, min_score=3.0,
                                     max_component=0)
        # With max_component=0, component-only table should not appear
        assert "仅组件匹配" not in md

    def test_empty_issues(self, config):
        md, stats = format_issue_section({}, config, min_score=3.0)
        assert stats["kw_matched"] == 0
        assert "未找到" in md


class TestFormatPRSection:
    def test_returns_markdown_and_stats(self, sample_prs, config):
        md, stats = format_pr_section(sample_prs, config, min_score=3.0)
        assert "Pull Requests" in md
        assert stats["kw_matched"] == 1
        assert stats["component_only"] == 1

    def test_merged_indicator(self, sample_prs, config):
        md, _ = format_pr_section(sample_prs, config, min_score=3.0)
        assert "merged" in md


class TestFormatCodeSection:
    def test_returns_markdown_and_stats(self, sample_code, config):
        md, stats = format_code_section(sample_code, config, min_score=3.0)
        assert "Code" in md
        assert stats["kw_matched"] == 1


class TestFormatCommitSection:
    def test_returns_markdown_and_stats(self, sample_commits, config):
        md, stats = format_commit_section(sample_commits, config, min_score=3.0)
        assert "Commits" in md
        assert stats["kw_matched"] == 1
        assert "abc1234" in md


class TestFormatDiscussionSection:
    def test_returns_markdown_and_stats(self, sample_discussions, config):
        md, stats = format_discussion_section(
            sample_discussions, config, min_score=3.0)
        assert "Discussions" in md
        assert stats["kw_matched"] == 1
        assert "已回答" in md


# ============================================================================
# Full report tests
# ============================================================================

class TestFormatFullReport:
    def test_contains_executive_summary(self, config, sample_issues, sample_prs):
        report = format_full_report(
            config=config, min_score=3.0,
            issue_results=sample_issues, pr_results=sample_prs)
        assert "执行摘要" in report
        assert "Issues" in report
        assert "Pull Requests" in report

    def test_skips_none_types(self, config, sample_issues):
        report = format_full_report(
            config=config, min_score=3.0,
            issue_results=sample_issues)
        assert "Issues" in report
        assert "Pull Requests" not in report

    def test_footer(self, config, sample_issues):
        report = format_full_report(
            config=config, min_score=3.0,
            issue_results=sample_issues)
        assert "Generated by search_github.py v5" in report


# ============================================================================
# Full JSON tests
# ============================================================================

class TestFormatFullJson:
    def test_valid_json(self, config, sample_issues, sample_prs,
                        sample_code, sample_commits, sample_discussions):
        output = format_full_json(
            config=config, min_score=3.0,
            issue_results=sample_issues, pr_results=sample_prs,
            code_results=sample_code, commit_results=sample_commits,
            disc_results=sample_discussions)
        data = json.loads(output)
        assert data["version"] == "v5"
        assert "issues" in data
        assert "pull_requests" in data
        assert "code" in data
        assert "commits" in data
        assert "discussions" in data

    def test_issue_items_filtered_by_min_score(self, config, sample_issues):
        output = format_full_json(
            config=config, min_score=10.0,
            issue_results=sample_issues)
        data = json.loads(output)
        # Only issue 101 (score 15.0) should pass
        assert data["issues"]["total_relevant"] == 1
        assert data["issues"]["items"][0]["number"] == 101

    def test_skips_none_types(self, config, sample_issues):
        output = format_full_json(
            config=config, min_score=3.0,
            issue_results=sample_issues)
        data = json.loads(output)
        assert "issues" in data
        assert "pull_requests" not in data

    def test_pr_fields(self, config, sample_prs):
        output = format_full_json(
            config=config, min_score=3.0,
            pr_results=sample_prs)
        data = json.loads(output)
        pr_item = data["pull_requests"]["items"][0]
        assert "merged" in pr_item
        assert "linked_issues" in pr_item
        assert "changed_files" in pr_item

    def test_discussion_has_answer_field(self, config, sample_discussions):
        output = format_full_json(
            config=config, min_score=3.0,
            disc_results=sample_discussions)
        data = json.loads(output)
        disc_item = data["discussions"]["items"][0]
        assert disc_item["has_answer"] is True
