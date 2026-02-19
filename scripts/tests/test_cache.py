"""Tests for core/cache.py — JSON caching with atomic write."""

import json
import pytest
from core.cache import save_cache, load_cache
from core.models import Issue


@pytest.fixture
def sample_issues():
    """Create a dict of sample issues for caching."""
    return {
        1: Issue(number=1, title="Bug A", body="Description A",
                 labels=["bug"], state="open", url="u1", created_at="2025-01-01",
                 relevance_score=10.0, matched_keywords={"keyword1"}),
        2: Issue(number=2, title="Bug B", body="Description B",
                 labels=["bug", "p1"], state="closed", url="u2", created_at="2025-02-01",
                 relevance_score=5.0, matched_keywords={"keyword2", "keyword3"},
                 comments_text="some comment text", comments_fetched=True),
    }


class TestSaveCache:
    """Test save_cache writes valid JSON."""

    def test_save_creates_file(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)
        assert (tmp_path / "cache.json").exists()

    def test_saved_file_is_valid_json(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)
        data = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
        assert "repo" in data
        assert "issues" in data
        assert len(data["issues"]) == 2

    def test_saved_data_preserves_fields(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)
        data = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
        issue_data = list(data["issues"].values())[0]
        assert "number" in issue_data
        assert "title" in issue_data
        assert "body" in issue_data
        assert "labels" in issue_data


class TestLoadCache:
    """Test load_cache restores issues correctly."""

    def test_load_restores_issues(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)

        loaded = {}
        result = load_cache(path, "TestOrg/test-repo", loaded)
        assert result is True
        assert len(loaded) == 2
        assert 1 in loaded
        assert 2 in loaded

    def test_loaded_issue_has_correct_fields(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)

        loaded = {}
        load_cache(path, "TestOrg/test-repo", loaded)
        issue = loaded[1]
        assert issue.title == "Bug A"
        assert issue.state == "open"
        assert issue.number == 1

    def test_load_nonexistent_file_returns_false(self, tmp_path):
        loaded = {}
        result = load_cache(str(tmp_path / "missing.json"), "org/repo", loaded)
        assert result is False
        assert len(loaded) == 0

    def test_load_wrong_repo_returns_false(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)

        loaded = {}
        result = load_cache(path, "DifferentOrg/other-repo", loaded)
        assert result is False

    def test_load_corrupted_json_returns_false(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("{invalid json", encoding="utf-8")

        loaded = {}
        result = load_cache(str(path), "org/repo", loaded)
        assert result is False


class TestCacheRoundTrip:
    """Test save -> load preserves data integrity."""

    def test_roundtrip_preserves_comments(self, tmp_path, sample_issues):
        path = str(tmp_path / "cache.json")
        save_cache(sample_issues, "TestOrg/test-repo", path)

        loaded = {}
        load_cache(path, "TestOrg/test-repo", loaded)
        assert loaded[2].comments_text == "some comment text"
        assert loaded[2].comments_fetched is True

    def test_roundtrip_with_empty_dict(self, tmp_path):
        path = str(tmp_path / "cache.json")
        save_cache({}, "org/repo", path)

        loaded = {}
        result = load_cache(path, "org/repo", loaded)
        assert result is True
        assert len(loaded) == 0
