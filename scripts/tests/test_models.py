"""Tests for core/models.py — data models and SearchConfig."""

import json
import pytest
from core.models import Issue, PullRequest, CodeResult, CommitResult, DiscussionResult, SearchConfig


class TestSearchConfigValidation:
    """Test SearchConfig.validate() catches invalid configurations."""

    def test_invalid_date_format_returns_error(self):
        config = SearchConfig(date_from="not-a-date")
        errors = config.validate()
        assert any("date_from" in e for e in errors)

    def test_date_range_reversed_returns_error(self):
        config = SearchConfig(date_from="2025-12-31", date_to="2025-01-01")
        errors = config.validate()
        assert any("date_from" in e for e in errors)

    def test_invalid_state_returns_error(self):
        config = SearchConfig(state_filter="invalid")
        errors = config.validate()
        assert any("state_filter" in e for e in errors)

    def test_invalid_search_type_returns_error(self):
        config = SearchConfig(search_types=["issues", "invalid_type"])
        errors = config.validate()
        assert any("search_type" in e.lower() for e in errors)

    def test_valid_config_returns_no_errors(self, basic_config):
        errors = basic_config.validate()
        assert len(errors) == 0

    def test_valid_dates_no_error(self):
        config = SearchConfig(date_from="2024-01-01", date_to="2025-12-31")
        errors = config.validate()
        assert len(errors) == 0


class TestSearchConfigProperties:
    """Test computed properties of SearchConfig."""

    def test_all_keywords(self, basic_config):
        kws = basic_config.all_keywords
        assert "memory leak" in kws
        assert "out of memory" in kws
        assert "oom" in kws
        assert "allocation" in kws

    def test_keyword_weight_map(self, basic_config):
        wm = basic_config.keyword_weight_map
        assert wm["memory leak"] == 5
        assert wm["oom"] == 3
        assert wm["allocation"] == 1

    def test_has_component(self, basic_config, config_no_component):
        assert basic_config.has_component is True
        assert config_no_component.has_component is False

    def test_filter_qualifiers_with_state(self):
        config = SearchConfig(state_filter="open")
        assert "is:open" in config.filter_qualifiers

    def test_filter_qualifiers_with_both_dates(self):
        config = SearchConfig(date_from="2024-01-01", date_to="2025-12-31")
        q = config.filter_qualifiers
        assert "created:2024-01-01..2025-12-31" in q

    def test_filter_qualifiers_with_only_date_from(self):
        config = SearchConfig(date_from="2024-01-01")
        q = config.filter_qualifiers
        assert "created:>=2024-01-01" in q

    def test_filter_qualifiers_with_only_date_to(self):
        config = SearchConfig(date_to="2025-12-31")
        q = config.filter_qualifiers
        assert "created:<=2025-12-31" in q


class TestSearchConfigFromJson:
    """Test JSON deserialization."""

    def test_from_json_basic(self, tmp_path):
        data = {
            "repo": "org/repo",
            "component": "mylib",
            "topic": "crash",
            "keywords": {
                "high": ["crash"],
                "medium": ["error"],
                "low": ["warning"]
            },
            "queries": ["mylib crash"],
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(data))
        config = SearchConfig.from_json(str(path))
        assert config.repo == "org/repo"
        assert config.component == "mylib"
        assert "crash" in config.keywords_high

    def test_from_json_with_search_types(self, tmp_path):
        data = {
            "repo": "org/repo",
            "topic": "test",
            "search_types": ["issues", "prs", "code"],
            "keywords": {"high": ["bug"]},
            "queries": ["bug"],
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(data))
        config = SearchConfig.from_json(str(path))
        assert config.search_types == ["issues", "prs", "code"]

    def test_from_json_default_search_types(self, tmp_path):
        data = {
            "repo": "org/repo",
            "topic": "test",
            "keywords": {"high": ["bug"]},
            "queries": ["bug"],
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(data))
        config = SearchConfig.from_json(str(path))
        assert config.search_types == ["issues"]

    def test_from_json_with_filters(self, tmp_path):
        data = {
            "repo": "org/repo",
            "topic": "test",
            "filters": {
                "state": "open",
                "date_from": "2024-01-01",
                "date_to": "2025-12-31",
            },
            "keywords": {"high": ["bug"]},
            "queries": ["bug"],
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(data))
        config = SearchConfig.from_json(str(path))
        assert config.state_filter == "open"
        assert config.date_from == "2024-01-01"
        assert config.date_to == "2025-12-31"


class TestIssueDataclass:
    """Test Issue dataclass basics."""

    def test_issue_defaults(self):
        issue = Issue(number=1, title="t", body="b", labels=[],
                      state="open", url="u", created_at="2025-01-01")
        assert issue.relevance_score == 0.0
        assert issue.matched_keywords == set()
        assert issue.comments_text == ""

    def test_issue_fields(self, issue_high_relevance):
        assert issue_high_relevance.number == 100
        assert issue_high_relevance.state == "open"
        assert "memory leak" in issue_high_relevance.title


class TestCodeResultDataclass:
    """Test CodeResult dataclass."""

    def test_code_result_defaults(self):
        cr = CodeResult(path="a.py", url="u", repo="r")
        assert cr.sha == ""
        assert cr.content_snippet == ""
        assert cr.relevance_score == 0.0
