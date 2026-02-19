import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import Issue, PullRequest, CommitResult
from core.cross_ref import build_cross_references, format_cross_ref_summary


def test_pr_to_issue_link_from_body():
    """PR body contains 'fixes #123', verify issue_to_prs and pr_to_issues maps."""
    pr = PullRequest(
        number=10,
        title="Fix bug",
        state="closed",
        merged=True,
        url="https://github.com/test/repo/pull/10",
        labels=[],
        created_at="2024-01-01",
        body="This fixes #123"
    )
    
    issue = Issue(
        number=123,
        title="Bug report",
        state="closed",
        url="https://github.com/test/repo/issues/123",
        labels=[],
        created_at="2024-01-01"
    )
    
    xref = build_cross_references(
        issue_results={123: issue},
        pr_results={10: pr},
        commit_results=None
    )
    
    assert xref["pr_to_issues"][10] == [123]
    assert xref["issue_to_prs"][123] == [10]
    assert xref["stats"]["issue_pr_links"] >= 1
    assert xref["stats"]["total_edges"] >= 1


def test_pr_to_issue_link_from_linked_issues():
    """PR has pre-extracted linked_issues=[123], verify mapping."""
    pr = PullRequest(
        number=20,
        title="Feature PR",
        state="open",
        merged=False,
        url="https://github.com/test/repo/pull/20",
        labels=[],
        created_at="2024-01-02",
        body="Some description",
        linked_issues=[123]
    )
    
    issue = Issue(
        number=123,
        title="Feature request",
        state="open",
        url="https://github.com/test/repo/issues/123",
        labels=[],
        created_at="2024-01-01"
    )
    
    xref = build_cross_references(
        issue_results={123: issue},
        pr_results={20: pr},
        commit_results=None
    )
    
    # linked_issues=[123] should create a pr→issue edge
    assert 123 in xref["pr_to_issues"].get(20, [])
    assert 20 in xref["issue_to_prs"].get(123, [])


def test_commit_to_issue_link():
    """Commit message contains '#456', verify issue_to_commits."""
    commit = CommitResult(
        sha="abcdef1234567890abcdef1234567890abcdef12",
        message="Fix issue #456",
        url="https://github.com/test/repo/commit/abcdef12",
        author="testuser",
        date="2024-01-03"
    )
    
    issue = Issue(
        number=456,
        title="Another issue",
        state="open",
        url="https://github.com/test/repo/issues/456",
        labels=[],
        created_at="2024-01-01"
    )
    
    xref = build_cross_references(
        issue_results={456: issue},
        pr_results=None,
        commit_results={"abcdef1234567890abcdef1234567890abcdef12": commit}
    )
    
    assert xref["issue_to_commits"][456] == ["abcdef1234"]
    assert xref["stats"]["commit_refs"] >= 1


def test_no_results_returns_empty():
    """All None inputs, verify empty maps."""
    xref = build_cross_references(
        issue_results=None,
        pr_results=None,
        commit_results=None
    )
    
    assert xref["issue_to_prs"] == {}
    assert xref["pr_to_issues"] == {}
    assert xref["issue_to_commits"] == {}
    assert xref["stats"]["total_edges"] == 0
    assert xref["stats"]["issue_pr_links"] == 0
    assert xref["stats"]["pr_pr_links"] == 0
    assert xref["stats"]["commit_refs"] == 0


def test_dedup_links():
    """Same issue referenced by multiple PRs, verify no duplicates."""
    pr1 = PullRequest(
        number=30,
        title="PR 1",
        state="closed",
        merged=True,
        url="https://github.com/test/repo/pull/30",
        labels=[],
        created_at="2024-01-01",
        body="Fixes #100"
    )
    
    pr2 = PullRequest(
        number=31,
        title="PR 2",
        state="closed",
        merged=True,
        url="https://github.com/test/repo/pull/31",
        labels=[],
        created_at="2024-01-02",
        body="Closes #100"
    )
    
    issue = Issue(
        number=100,
        title="Issue",
        state="closed",
        url="https://github.com/test/repo/issues/100",
        labels=[],
        created_at="2024-01-01"
    )
    
    xref = build_cross_references(
        issue_results={100: issue},
        pr_results={30: pr1, 31: pr2},
        commit_results=None
    )
    
    assert xref["issue_to_prs"][100] == [30, 31]
    assert xref["pr_to_issues"][30] == [100]
    assert xref["pr_to_issues"][31] == [100]
    # Verify no duplicates
    assert len(xref["issue_to_prs"][100]) == 2
    assert len(set(xref["issue_to_prs"][100])) == 2


def test_filter_to_found_issues():
    """Only link to issues that were actually found in issue_results."""
    pr = PullRequest(
        number=40,
        title="PR",
        state="open",
        merged=False,
        url="https://github.com/test/repo/pull/40",
        labels=[],
        created_at="2024-01-01",
        body="Fixes #200 and #201"
    )
    
    # Only issue 200 exists in results, 201 does not
    issue200 = Issue(
        number=200,
        title="Issue 200",
        state="open",
        url="https://github.com/test/repo/issues/200",
        labels=[],
        created_at="2024-01-01"
    )
    
    xref = build_cross_references(
        issue_results={200: issue200},  # Only 200, not 201
        pr_results={40: pr},
        commit_results=None
    )
    
    # Should only link to 200, not 201
    assert xref["pr_to_issues"][40] == [200]
    assert 201 not in xref["pr_to_issues"][40]
    assert xref["issue_to_prs"][200] == [40]
    assert 201 not in xref["issue_to_prs"]


def test_format_cross_ref_summary():
    """Verify Markdown output format."""
    issue = Issue(
        number=500,
        title="Issue",
        state="open",
        url="https://github.com/test/repo/issues/500",
        labels=[],
        created_at="2024-01-01"
    )
    
    pr = PullRequest(
        number=50,
        title="PR",
        state="open",
        merged=False,
        url="https://github.com/test/repo/pull/50",
        labels=[],
        created_at="2024-01-01",
        body="Fixes #500"
    )
    
    commit = CommitResult(
        sha="1234567890abcdef1234567890abcdef12345678",
        message="Reference #500",
        url="https://github.com/test/repo/commit/12345678",
        author="testuser",
        date="2024-01-01"
    )
    
    xref = build_cross_references(
        issue_results={500: issue},
        pr_results={50: pr},
        commit_results={"1234567890abcdef1234567890abcdef12345678": commit}
    )
    
    summary = format_cross_ref_summary(
        xref,
        issue_results={500: issue},
        pr_results={50: pr},
        commit_results={"1234567890abcdef1234567890abcdef12345678": commit},
        repo="test/repo",
        output_dir="",
    )
    
    assert "## 交叉引用" in summary
    assert "### Issue ↔ PR 关联" in summary
    # Rich table headers
    assert "| Issue | 标题 |" in summary
    assert "#500" in summary
    assert "#50" in summary
    # Commit reference section
    assert "### Commit 引用" in summary
    assert "1234567890" in summary
    # PNG graph image reference
    assert "cross_ref_graph.png" in summary
    assert "---" in summary


def test_format_cross_ref_empty():
    """No links, verify empty string returned."""
    xref = build_cross_references(
        issue_results=None,
        pr_results=None,
        commit_results=None
    )
    
    summary = format_cross_ref_summary(xref)
    
    assert summary == ""
