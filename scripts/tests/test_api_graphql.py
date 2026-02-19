"""Tests for api_client.py GraphQL method."""

import pytest
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.api_client import GitHubApiClient


class TestGraphQL:
    @patch("core.api_client.requests.Session")
    def test_successful_graphql_returns_data(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"search": {"nodes": [{"number": 1}]}}
        }
        mock_resp.headers = {"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "0"}
        mock_session.post.return_value = mock_resp

        client = GitHubApiClient(token="test-token")
        result = client.graphql("query { viewer { login } }")

        assert result is not None
        assert "search" in result
        mock_session.post.assert_called_once()

    @patch("core.api_client.requests.Session")
    def test_graphql_with_errors_returns_none(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "errors": [{"message": "Some error"}]
        }
        mock_resp.headers = {}
        mock_session.post.return_value = mock_resp

        client = GitHubApiClient(token="test-token")
        result = client.graphql("query { bad }")
        assert result is None

    @patch("core.api_client.requests.Session")
    def test_graphql_401_returns_none(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.headers = {}
        mock_session.post.return_value = mock_resp

        client = GitHubApiClient(token="")
        result = client.graphql("query { viewer { login } }")
        assert result is None

    @patch("core.api_client.time.sleep")
    @patch("core.api_client.requests.Session")
    def test_graphql_403_retries(self, mock_session_cls, mock_sleep):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # First call: 403, second call: success
        resp_403 = MagicMock()
        resp_403.status_code = 403
        resp_403.headers = {"X-RateLimit-Reset": "0", "X-RateLimit-Remaining": "0"}
        resp_403.text = "rate limited"

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"data": {"viewer": {"login": "test"}}}
        resp_ok.headers = {"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "0"}

        mock_session.post.side_effect = [resp_403, resp_ok]

        client = GitHubApiClient(token="test-token")
        result = client.graphql("query { viewer { login } }")

        assert result is not None
        assert mock_session.post.call_count == 2

    @patch("core.api_client.requests.Session")
    def test_graphql_403_gives_up_after_max_retries(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        resp_403 = MagicMock()
        resp_403.status_code = 403
        resp_403.headers = {"X-RateLimit-Reset": "0", "X-RateLimit-Remaining": "0"}
        resp_403.text = "rate limited"
        mock_session.post.return_value = resp_403

        client = GitHubApiClient(token="test-token")
        with patch("core.api_client.time.sleep"):
            result = client.graphql("query { viewer { login } }",
                                    _max_retries=2)

        assert result is None
        assert mock_session.post.call_count == 3  # initial + 2 retries

    @patch("core.api_client.requests.Session")
    def test_graphql_passes_variables(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"result": True}}
        mock_resp.headers = {}
        mock_session.post.return_value = mock_resp

        client = GitHubApiClient(token="test-token")
        client.graphql("query($q: String!) { search(query: $q) }",
                       variables={"q": "test"})

        call_kwargs = mock_session.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["variables"] == {"q": "test"}

    @patch("core.api_client.requests.Session")
    def test_graphql_updates_rate_limit(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {}}
        mock_resp.headers = {
            "X-RateLimit-Remaining": "4500",
            "X-RateLimit-Reset": "1700000000",
        }
        mock_session.post.return_value = mock_resp

        client = GitHubApiClient(token="test-token")
        client.graphql("query {}")

        assert client._core_rate_remaining == 4500
        assert client._core_rate_reset == 1700000000
