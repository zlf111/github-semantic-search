"""Comprehensive tests for all 5 searcher classes with mocked API responses."""

import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest

# Ensure scripts/ is on sys.path so 'core' and 'searchers' are importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.api_client import GitHubApiClient
from core.models import SearchConfig
from searchers.issue import IssueSearcher
from searchers.pr import PRSearcher
from searchers.code import CodeSearcher
from searchers.commit import CommitSearcher
from searchers.discussion import DiscussionSearcher


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_api_client():
    """Mock GitHubApiClient that returns fake API responses."""
    api = MagicMock(spec=GitHubApiClient)
    api.has_token = True
    api.repo = "TestOrg/test-repo"
    return api


@pytest.fixture
def basic_config():
    """Basic SearchConfig for testing."""
    return SearchConfig(
        repo="TestOrg/test-repo",
        component="mylib",
        topic="memory leak",
        keywords_high=["memory leak", "out of memory"],
        keywords_medium=["oom"],
        keywords_low=["allocation"],
        queries=['mylib "memory leak"', 'mylib "oom"'],
    )


@pytest.fixture
def config_no_component():
    """SearchConfig without component."""
    return SearchConfig(
        repo="TestOrg/test-repo",
        component="",
        topic="bug",
        keywords_high=["bug"],
        keywords_medium=[],
        keywords_low=[],
        queries=['"bug"'],
    )


# ============================================================================
# IssueSearcher Tests
# ============================================================================

class TestIssueSearcher:
    """Tests for IssueSearcher."""

    def test_issue_collect_parses_results(self, mock_api_client, basic_config):
        """Test that collect() parses API results and populates results dict."""
        # Mock API response with fake issue items
        fake_items = [
            {
                "number": 100,
                "title": "Memory leak in mylib allocator",
                "state": "open",
                "html_url": "https://github.com/TestOrg/test-repo/issues/100",
                "labels": [{"name": "bug"}, {"name": "project: mylib"}],
                "created_at": "2025-06-15T10:00:00Z",
                "body": "We observed a severe memory leak when running mylib tests.",
            },
            {
                "number": 200,
                "title": "OOM error in mylib",
                "state": "closed",
                "html_url": "https://github.com/TestOrg/test-repo/issues/200",
                "labels": [{"name": "project: mylib"}],
                "created_at": "2025-08-01T10:00:00Z",
                "body": "Out of memory errors when batch size > 512.",
            },
        ]
        mock_api_client.search.return_value = fake_items

        searcher = IssueSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        # Verify results dict is populated
        assert len(searcher.results) == 2
        assert 100 in searcher.results
        assert 200 in searcher.results

        # Verify Issue objects are correctly created
        issue_100 = searcher.results[100]
        assert issue_100.number == 100
        assert issue_100.title == "Memory leak in mylib allocator"
        assert issue_100.state == "open"
        assert issue_100.url == "https://github.com/TestOrg/test-repo/issues/100"
        assert "bug" in issue_100.labels
        assert "project: mylib" in issue_100.labels
        assert issue_100.created_at == "2025-06-15"
        assert issue_100.body == "We observed a severe memory leak when running mylib tests."

        issue_200 = searcher.results[200]
        assert issue_200.number == 200
        assert issue_200.state == "closed"

        # Verify API was called with correct query
        assert mock_api_client.search.called
        call_args = mock_api_client.search.call_args
        assert "https://api.github.com/search/issues" in call_args[0]
        assert "repo:TestOrg/test-repo is:issue" in call_args[0][1]

    def test_issue_dedup(self, mock_api_client, basic_config):
        """Test that same issue from multiple queries appears only once."""
        # Same issue returned from multiple queries
        fake_item = {
            "number": 100,
            "title": "Memory leak",
            "state": "open",
            "html_url": "https://github.com/TestOrg/test-repo/issues/100",
            "labels": [],
            "created_at": "2025-06-15T10:00:00Z",
            "body": "Test body",
        }
        mock_api_client.search.return_value = [fake_item]

        searcher = IssueSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        # Should only have one issue despite multiple queries
        assert len(searcher.results) == 1
        assert 100 in searcher.results

    def test_issue_build_query_with_component(self, mock_api_client, basic_config):
        """Test query building with component."""
        searcher = IssueSearcher(mock_api_client, "TestOrg/test-repo")
        query = searcher.build_query('mylib "memory leak"', basic_config)
        assert query == 'mylib "memory leak"'

    def test_issue_build_query_without_component(self, mock_api_client, config_no_component):
        """Test query building without component."""
        searcher = IssueSearcher(mock_api_client, "TestOrg/test-repo")
        query = searcher.build_query('{component} "bug"', config_no_component)
        # Should remove {component} placeholder and clean up spaces
        assert "{component}" not in query
        assert query == '"bug"'


# ============================================================================
# PRSearcher Tests
# ============================================================================

class TestPRSearcher:
    """Tests for PRSearcher."""

    def test_pr_collect_parses_results(self, mock_api_client, basic_config):
        """Test that collect() parses PR results including merged status."""
        fake_items = [
            {
                "number": 400,
                "title": "Fix memory leak in mylib allocator",
                "state": "closed",
                "html_url": "https://github.com/TestOrg/test-repo/pull/400",
                "labels": [{"name": "bug fix"}],
                "created_at": "2025-06-20T10:00:00Z",
                "body": "Resolves #100. The allocator was not freeing temporary buffers.",
                "pull_request": {
                    "merged_at": "2025-06-21T10:00:00Z",
                },
            },
            {
                "number": 500,
                "title": "Add OOM handling",
                "state": "open",
                "html_url": "https://github.com/TestOrg/test-repo/pull/500",
                "labels": [],
                "created_at": "2025-08-01T10:00:00Z",
                "body": "Add better OOM error handling.",
                "pull_request": {
                    "merged_at": None,
                },
            },
        ]
        mock_api_client.search.return_value = fake_items

        searcher = PRSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        # Verify results dict is populated
        assert len(searcher.results) == 2
        assert 400 in searcher.results
        assert 500 in searcher.results

        # Verify merged status is correctly parsed
        pr_400 = searcher.results[400]
        assert pr_400.number == 400
        assert pr_400.merged is True
        assert pr_400.state == "closed"
        assert pr_400.title == "Fix memory leak in mylib allocator"

        pr_500 = searcher.results[500]
        assert pr_500.merged is False
        assert pr_500.state == "open"

    def test_pr_linked_issues_extraction(self, mock_api_client, basic_config):
        """Test that linked issues are extracted from PR body."""
        fake_items = [
            {
                "number": 400,
                "title": "Fix memory leak",
                "state": "closed",
                "html_url": "https://github.com/TestOrg/test-repo/pull/400",
                "labels": [],
                "created_at": "2025-06-20T10:00:00Z",
                "body": "This fixes #100 and closes #200. Also resolves #300.",
                "pull_request": {"merged_at": None},
            },
        ]
        mock_api_client.search.return_value = fake_items

        searcher = PRSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        pr_400 = searcher.results[400]
        # Should extract linked issue numbers from body
        assert 100 in pr_400.linked_issues
        assert 200 in pr_400.linked_issues
        assert 300 in pr_400.linked_issues
        assert len(pr_400.linked_issues) == 3

    def test_pr_build_query(self, mock_api_client, basic_config):
        """Test PR query building."""
        searcher = PRSearcher(mock_api_client, "TestOrg/test-repo")
        query = searcher.build_query('mylib "memory leak"', basic_config)
        assert query == 'mylib "memory leak"'


# ============================================================================
# CodeSearcher Tests
# ============================================================================

class TestCodeSearcher:
    """Tests for CodeSearcher."""

    def test_code_collect_parses_results(self, mock_api_client, basic_config):
        """Test that collect() parses code results including path and snippet."""
        fake_items = [
            {
                "path": "projects/mylib/src/memory_manager.cpp",
                "html_url": "https://github.com/TestOrg/test-repo/blob/main/projects/mylib/src/memory_manager.cpp",
                "sha": "abc123def456",
                "text_matches": [
                    {
                        "fragment": "// Fix memory leak in allocator\nvoid free_buffer(Buffer* buf) {",
                    }
                ],
            },
            {
                "path": "projects/mylib/tests/test_alloc.py",
                "html_url": "https://github.com/TestOrg/test-repo/blob/main/projects/mylib/tests/test_alloc.py",
                "sha": "def456ghi789",
                "text_matches": [],
            },
        ]
        mock_api_client.search.return_value = fake_items

        searcher = CodeSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        # Verify results dict is populated (keyed by path)
        assert len(searcher.results) == 2
        assert "projects/mylib/src/memory_manager.cpp" in searcher.results
        assert "projects/mylib/tests/test_alloc.py" in searcher.results

        # Verify CodeResult objects are correctly created
        code_1 = searcher.results["projects/mylib/src/memory_manager.cpp"]
        assert code_1.path == "projects/mylib/src/memory_manager.cpp"
        assert code_1.url == "https://github.com/TestOrg/test-repo/blob/main/projects/mylib/src/memory_manager.cpp"
        assert code_1.repo == "TestOrg/test-repo"
        assert code_1.sha == "abc123def456"
        # Should extract snippet from text_matches
        assert "Fix memory leak" in code_1.content_snippet

        code_2 = searcher.results["projects/mylib/tests/test_alloc.py"]
        assert code_2.path == "projects/mylib/tests/test_alloc.py"
        # No text_matches, so snippet should be empty
        assert code_2.content_snippet == ""

        # Verify API was called with correct headers
        assert mock_api_client.search.called
        call_args = mock_api_client.search.call_args
        assert "https://api.github.com/search/code" in call_args[0]
        assert call_args[1]["headers"]["Accept"] == "application/vnd.github.text-match+json"

    def test_code_build_query(self, mock_api_client, basic_config):
        """Test code query building."""
        searcher = CodeSearcher(mock_api_client, "TestOrg/test-repo")
        query = searcher.build_query('mylib "memory leak"', basic_config)
        assert query == 'mylib "memory leak"'


# ============================================================================
# CommitSearcher Tests
# ============================================================================

class TestCommitSearcher:
    """Tests for CommitSearcher."""

    def test_commit_collect_parses_results(self, mock_api_client, basic_config):
        """Test that collect() parses commit results including sha, message, author."""
        fake_items = [
            {
                "sha": "def456abc123",
                "html_url": "https://github.com/TestOrg/test-repo/commit/def456abc123",
                "commit": {
                    "message": "Fix memory leak in batch allocator\n\nThe allocator was leaking when batch size exceeded threshold.",
                    "author": {
                        "name": "John Doe",
                        "date": "2025-06-20T10:00:00Z",
                    },
                },
            },
            {
                "sha": "ghi789jkl012",
                "html_url": "https://github.com/TestOrg/test-repo/commit/ghi789jkl012",
                "commit": {
                    "message": "Add OOM handling",
                    "author": {
                        "name": "Jane Smith",
                        "date": "2025-08-01T10:00:00Z",
                    },
                },
            },
        ]
        mock_api_client.search.return_value = fake_items

        searcher = CommitSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        # Verify results dict is populated (keyed by SHA)
        assert len(searcher.results) == 2
        assert "def456abc123" in searcher.results
        assert "ghi789jkl012" in searcher.results

        # Verify CommitResult objects are correctly created
        commit_1 = searcher.results["def456abc123"]
        assert commit_1.sha == "def456abc123"
        assert commit_1.message == "Fix memory leak in batch allocator\n\nThe allocator was leaking when batch size exceeded threshold."
        assert commit_1.url == "https://github.com/TestOrg/test-repo/commit/def456abc123"
        assert commit_1.author == "John Doe"
        assert commit_1.date == "2025-06-20"

        commit_2 = searcher.results["ghi789jkl012"]
        assert commit_2.author == "Jane Smith"
        assert commit_2.date == "2025-08-01"

        # Verify API was called with correct headers
        assert mock_api_client.search.called
        call_args = mock_api_client.search.call_args
        assert "https://api.github.com/search/commits" in call_args[0]
        assert call_args[1]["headers"]["Accept"] == "application/vnd.github.cloak-preview+json"

    def test_commit_build_query(self, mock_api_client, basic_config):
        """Test commit query building."""
        searcher = CommitSearcher(mock_api_client, "TestOrg/test-repo")
        query = searcher.build_query('mylib "memory leak"', basic_config)
        assert query == 'mylib "memory leak"'


# ============================================================================
# DiscussionSearcher Tests
# ============================================================================

class TestDiscussionSearcher:
    """Tests for DiscussionSearcher."""

    def test_discussion_collect_parses_results(self, mock_api_client, basic_config):
        """Test that collect() parses GraphQL discussion results."""
        # Mock GraphQL response
        fake_graphql_response = {
            "search": {
                "discussionCount": 1,
                "pageInfo": {
                    "hasNextPage": False,
                    "endCursor": None,
                },
                "nodes": [
                    {
                        "number": 500,
                        "title": "How to debug memory leak in mylib?",
                        "url": "https://github.com/TestOrg/test-repo/discussions/500",
                        "createdAt": "2025-07-01T10:00:00Z",
                        "body": "I'm seeing OOM errors after running mylib for extended periods.",
                        "category": {
                            "name": "Q&A",
                        },
                        "answer": {
                            "body": "You should check the allocation pool. memory leak usually happens in the buffer cache.",
                        },
                        "comments": {
                            "nodes": [
                                {"body": "Try running with ASAN enabled to detect the leak."},
                                {"body": "Another helpful comment."},
                            ],
                        },
                    },
                ],
            },
        }
        mock_api_client.graphql.return_value = fake_graphql_response

        searcher = DiscussionSearcher(mock_api_client, "TestOrg/test-repo")
        searcher.collect(basic_config)

        # Verify results dict is populated
        assert len(searcher.results) == 1
        assert 500 in searcher.results

        # Verify DiscussionResult object is correctly created
        discussion = searcher.results[500]
        assert discussion.number == 500
        assert discussion.title == "How to debug memory leak in mylib?"
        assert discussion.url == "https://github.com/TestOrg/test-repo/discussions/500"
        assert discussion.category == "Q&A"
        assert discussion.created_at == "2025-07-01"
        assert "OOM errors" in discussion.body
        assert "allocation pool" in discussion.answer_body
        assert "ASAN enabled" in discussion.comments_text
        assert "Another helpful comment" in discussion.comments_text

        # Verify GraphQL was called
        assert mock_api_client.graphql.called

    def test_discussion_build_query(self, mock_api_client, basic_config):
        """Test discussion query building."""
        searcher = DiscussionSearcher(mock_api_client, "TestOrg/test-repo")
        query = searcher.build_query('mylib "memory leak"', basic_config)
        assert query == 'mylib "memory leak"'
