"""Tests for core/api_client.py — GitHub API client."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from core.api_client import GitHubApiClient


class TestApiClientInit:
    """Test API client initialization."""

    def test_token_from_parameter(self):
        client = GitHubApiClient(token="test-token-123")
        assert client.has_token is True
        assert client.session.headers["Authorization"] == "token test-token-123"

    def test_no_token(self):
        with patch.dict("os.environ", {}, clear=True):
            client = GitHubApiClient(token="")
            assert client.has_token is False

    def test_default_headers(self):
        client = GitHubApiClient(token="t")
        assert "github" in client.session.headers["Accept"]
        assert "github-semantic-search" in client.session.headers["User-Agent"]


class TestApiClientGet:
    """Test GET request with retry and rate limiting."""

    def test_successful_get_returns_json(self):
        client = GitHubApiClient(token="t")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "ok"}
        mock_resp.headers = {"X-RateLimit-Remaining": "50", "X-RateLimit-Reset": "0"}

        with patch.object(client.session, "get", return_value=mock_resp):
            result = client.get("https://api.github.com/test")
            assert result == {"data": "ok"}

    def test_422_returns_none(self):
        client = GitHubApiClient(token="t")
        mock_resp = Mock()
        mock_resp.status_code = 422
        mock_resp.headers = {}

        with patch.object(client.session, "get", return_value=mock_resp):
            result = client.get("https://api.github.com/test")
            assert result is None

    def test_403_retries_with_backoff(self):
        client = GitHubApiClient(token="t")
        mock_403 = Mock()
        mock_403.status_code = 403
        mock_403.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}
        mock_200 = Mock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"ok": True}
        mock_200.headers = {"X-RateLimit-Remaining": "29", "X-RateLimit-Reset": "0"}

        with patch.object(client.session, "get", side_effect=[mock_403, mock_200]):
            with patch("core.api_client.time.sleep"):
                result = client.get("https://api.github.com/test")
                assert result == {"ok": True}

    def test_403_gives_up_after_max_retries(self):
        client = GitHubApiClient(token="t")
        mock_403 = Mock()
        mock_403.status_code = 403
        mock_403.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}

        with patch.object(client.session, "get", return_value=mock_403):
            with patch("core.api_client.time.sleep"):
                result = client.get("https://api.github.com/test")
                assert result is None

    def test_custom_headers_passed_through(self):
        client = GitHubApiClient(token="t")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.headers = {}

        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            custom_h = {"Accept": "application/vnd.github.text-match+json"}
            client.get("https://api.github.com/test", headers=custom_h)
            _, kwargs = mock_get.call_args
            assert kwargs["headers"] == custom_h

    def test_rate_limit_tracking(self):
        client = GitHubApiClient(token="t")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.headers = {"X-RateLimit-Remaining": "25", "X-RateLimit-Reset": "1700000000"}

        with patch.object(client.session, "get", return_value=mock_resp):
            client.get("https://api.github.com/test", api_type="search")
            assert client._search_rate_remaining == 25


class TestApiClientSearch:
    """Test paginated search."""

    def test_search_single_page(self):
        client = GitHubApiClient(token="t")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_count": 2,
            "items": [{"id": 1}, {"id": 2}]
        }
        mock_resp.headers = {"X-RateLimit-Remaining": "29", "X-RateLimit-Reset": "0"}

        with patch.object(client.session, "get", return_value=mock_resp):
            results = client.search("https://api.github.com/search/issues", "test query")
            assert len(results) == 2

    def test_search_passes_headers(self):
        client = GitHubApiClient(token="t")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total_count": 0, "items": []}
        mock_resp.headers = {}

        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            custom_h = {"Accept": "custom"}
            client.search("https://api.github.com/search/code", "q", headers=custom_h)
            _, kwargs = mock_get.call_args
            assert kwargs["headers"] == custom_h

    def test_search_empty_result(self):
        client = GitHubApiClient(token="t")

        with patch.object(client, "get", return_value=None):
            results = client.search("https://api.github.com/search/issues", "q")
            assert results == []
