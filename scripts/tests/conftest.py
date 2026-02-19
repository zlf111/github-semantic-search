"""Shared fixtures for all tests."""

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is on sys.path so 'core' and 'searchers' are importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.models import Issue, PullRequest, CodeResult, CommitResult, DiscussionResult, SearchConfig


# ---------- SearchConfig fixtures ----------

@pytest.fixture
def basic_config() -> SearchConfig:
    """Minimal config with a few keywords."""
    return SearchConfig(
        repo="TestOrg/test-repo",
        component="mylib",
        topic="memory leak",
        keywords_high=["memory leak", "out of memory"],
        keywords_medium=["oom", "heap overflow"],
        keywords_low=["allocation", "gc pressure"],
        queries=['mylib "memory leak"', 'mylib "oom"'],
    )


@pytest.fixture
def config_no_component() -> SearchConfig:
    """Config without component for whole-repo search."""
    return SearchConfig(
        repo="TestOrg/test-repo",
        component="",
        topic="segfault",
        keywords_high=["segmentation fault"],
        keywords_medium=["sigsegv"],
        keywords_low=["signal 11"],
        queries=['"segmentation fault"'],
    )


# ---------- Issue fixtures ----------

@pytest.fixture
def issue_high_relevance() -> Issue:
    """Issue that should score high — multiple high-weight keywords in title."""
    return Issue(
        number=100,
        title="memory leak in mylib allocator causes out of memory",
        body="We observed a severe memory leak when running mylib tests. "
             "The process eventually throws out of memory errors.",
        labels=["bug", "project: mylib"],
        state="open",
        url="https://github.com/TestOrg/test-repo/issues/100",
        created_at="2025-06-15",
    )


@pytest.fixture
def issue_medium_relevance() -> Issue:
    """Issue with only component label match + one medium keyword."""
    return Issue(
        number=200,
        title="mylib performance regression on gfx1100",
        body="After upgrade, OOM errors when batch size > 512.",
        labels=["project: mylib"],
        state="open",
        url="https://github.com/TestOrg/test-repo/issues/200",
        created_at="2025-08-01",
    )


@pytest.fixture
def issue_low_relevance() -> Issue:
    """Issue with only weak signals."""
    return Issue(
        number=300,
        title="Improve allocation strategy",
        body="The current allocation approach could be improved for throughput.",
        labels=["enhancement"],
        state="open",
        url="https://github.com/TestOrg/test-repo/issues/300",
        created_at="2025-09-01",
    )


# ---------- PR fixtures ----------

@pytest.fixture
def pr_merged_with_fix() -> PullRequest:
    """Merged PR that fixes a memory leak."""
    return PullRequest(
        number=400,
        title="Fix memory leak in mylib allocator",
        body="Resolves #100. The allocator was not freeing temporary buffers.",
        labels=["bug fix"],
        state="closed",
        url="https://github.com/TestOrg/test-repo/pull/400",
        created_at="2025-06-20",
        merged=True,
        changed_files=["projects/mylib/src/allocator.cpp", "projects/mylib/tests/test_alloc.py"],
        linked_issues=[100],
    )


# ---------- Code fixtures ----------

@pytest.fixture
def code_result_path_match() -> CodeResult:
    """Code result where component appears in path."""
    return CodeResult(
        path="projects/mylib/src/memory_manager.cpp",
        url="https://github.com/TestOrg/test-repo/blob/main/projects/mylib/src/memory_manager.cpp",
        repo="TestOrg/test-repo",
        sha="abc123",
        content_snippet="// Fix memory leak in allocator\nvoid free_buffer(Buffer* buf) {",
    )


# ---------- Commit fixtures ----------

@pytest.fixture
def commit_result() -> CommitResult:
    """Commit with keywords in summary line."""
    return CommitResult(
        sha="def456",
        message="Fix memory leak in batch allocator\n\nThe allocator was leaking when batch size exceeded threshold.",
        url="https://github.com/TestOrg/test-repo/commit/def456",
        author="dev",
        date="2025-06-20",
    )


# ---------- Discussion fixtures ----------

@pytest.fixture
def discussion_with_answer() -> DiscussionResult:
    """Discussion with an accepted answer containing keywords."""
    return DiscussionResult(
        number=500,
        title="How to debug memory leak in mylib?",
        url="https://github.com/TestOrg/test-repo/discussions/500",
        category="Q&A",
        created_at="2025-07-01",
        body="I'm seeing OOM errors after running mylib for extended periods.",
        answer_body="You should check the allocation pool. memory leak usually happens in the buffer cache.",
        comments_text="Try running with ASAN enabled to detect the leak.",
    )
