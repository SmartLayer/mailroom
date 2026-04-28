"""Unit tests for the batch output format and core batch execution primitives.

All tests run without a network connection — IMAP clients are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from mailroom.__main__ import (
    _build_op_key,
    _empty_result_for_subcmd,
    _execute_batch,
    _parse_op_string,
    _parse_read_args,
    _parse_search_args,
)

# ---------------------------------------------------------------------------
# _build_op_key
# ---------------------------------------------------------------------------


class TestBuildOpKey:
    def test_search_query_only(self):
        assert _build_op_key("search", query="from:foo") == "search from:foo"

    def test_search_with_folder(self):
        assert _build_op_key("search", query="x", folder="INBOX") == "search -f INBOX x"

    def test_search_default_limit_omitted(self):
        key = _build_op_key("search", query="x", limit=10)
        assert "--limit" not in key

    def test_search_non_default_limit_included(self):
        key = _build_op_key("search", query="x", limit=20)
        assert "--limit 20" in key

    def test_search_empty_query(self):
        key = _build_op_key("search", query="", folder="INBOX")
        assert key == "search -f INBOX"

    def test_read_key(self):
        assert _build_op_key("read", folder="INBOX", uid=42) == "read -f INBOX --uid 42"

    def test_unknown_subcmd_returns_bare_name(self):
        key = _build_op_key("folders")
        assert key == "folders"


# ---------------------------------------------------------------------------
# _empty_result_for_subcmd
# ---------------------------------------------------------------------------


class TestEmptyResultForSubcmd:
    def test_search_returns_search_shape(self):
        r = _empty_result_for_subcmd("search")
        assert "results" in r
        assert "provenance" in r
        assert r["results"] == []

    def test_read_returns_error_dict(self):
        r = _empty_result_for_subcmd("read")
        assert "error" in r


# ---------------------------------------------------------------------------
# _parse_search_args / _parse_read_args / _parse_op_string
# ---------------------------------------------------------------------------


class TestParseSearchArgs:
    def test_bare_query(self):
        r = _parse_search_args(["from:foo"])
        assert r["query"] == "from:foo"
        assert r["folder"] is None
        assert r["limit"] == 10

    def test_folder_short(self):
        r = _parse_search_args(["-f", "INBOX", "from:foo"])
        assert r["folder"] == "INBOX"

    def test_folder_long(self):
        r = _parse_search_args(["--folder=Sent", "x"])
        assert r["folder"] == "Sent"

    def test_limit_short(self):
        r = _parse_search_args(["-n", "5", "x"])
        assert r["limit"] == 5

    def test_limit_long_equals(self):
        r = _parse_search_args(["--limit=20", "x"])
        assert r["limit"] == 20

    def test_multi_word_query(self):
        r = _parse_search_args(["hello", "world"])
        assert r["query"] == "hello world"

    def test_unknown_flags_ignored(self):
        r = _parse_search_args(["--unknown", "from:foo"])
        assert r["query"] == "from:foo"


class TestParseReadArgs:
    def test_basic(self):
        r = _parse_read_args(["-f", "INBOX", "--uid", "42"])
        assert r["folder"] == "INBOX"
        assert r["uid"] == 42

    def test_equals_forms(self):
        r = _parse_read_args(["--folder=Sent", "--uid=7"])
        assert r["folder"] == "Sent"
        assert r["uid"] == 7

    def test_missing_folder_raises(self):
        with pytest.raises(ValueError, match="--folder"):
            _parse_read_args(["--uid", "1"])

    def test_missing_uid_raises(self):
        with pytest.raises(ValueError, match="--uid"):
            _parse_read_args(["-f", "INBOX"])


class TestParseOpString:
    def test_search_op(self):
        key, subcmd, kwargs = _parse_op_string("search from:foo")
        assert key == "search from:foo"
        assert subcmd == "search"
        assert kwargs["query"] == "from:foo"

    def test_read_op(self):
        key, subcmd, kwargs = _parse_op_string("read -f INBOX --uid 42")
        assert key == "read -f INBOX --uid 42"
        assert subcmd == "read"
        assert kwargs["folder"] == "INBOX"
        assert kwargs["uid"] == 42

    def test_key_is_echoed_exactly(self):
        op = "search  -f INBOX  from:foo"
        key, _, _ = _parse_op_string(op)
        assert key == op

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _parse_op_string("")

    def test_unsupported_subcommand_raises(self):
        with pytest.raises(ValueError, match="unsupported"):
            _parse_op_string("move -f INBOX --uid 1 --target Archive")


# ---------------------------------------------------------------------------
# _execute_batch
# ---------------------------------------------------------------------------


def _fake_search_result(query: str) -> dict:
    return {
        "results": [{"subject": f"result for {query}", "from": "x@y.com"}],
        "provenance": {
            "source": "remote",
            "indexed_at": None,
            "fell_back_reason": None,
        },
    }


class TestExecuteBatch:
    def _make_client(self, search_side_effect=None):
        client = MagicMock()
        if search_side_effect:
            client.search_emails.side_effect = search_side_effect
        else:
            client.search_emails.return_value = _fake_search_result("q")
        return client

    @patch("mailroom.__main__._make_client_soft")
    def test_single_search_wraps_in_op_key(self, mock_soft):
        client = self._make_client()
        mock_soft.return_value = client
        ops = [
            (
                "search from:foo",
                "search",
                {"query": "from:foo", "folder": None, "limit": 10},
            )
        ]
        result = _execute_batch(ops, ["acct1"])
        assert "search from:foo" in result
        assert "acct1" in result["search from:foo"]
        assert "results" in result["search from:foo"]["acct1"]

    @patch("mailroom.__main__._make_client_soft")
    def test_two_ops_produce_two_outer_keys(self, mock_soft):
        client = self._make_client()
        mock_soft.return_value = client
        ops = [
            (
                "search from:a",
                "search",
                {"query": "from:a", "folder": None, "limit": 10},
            ),
            (
                "search from:b",
                "search",
                {"query": "from:b", "folder": None, "limit": 10},
            ),
        ]
        result = _execute_batch(ops, ["acct1"])
        assert set(result.keys()) == {"search from:a", "search from:b"}

    @patch("mailroom.__main__._make_client_soft")
    def test_one_connection_per_account_for_multiple_ops(self, mock_soft):
        client = self._make_client()
        mock_soft.return_value = client
        ops = [
            (
                "search from:a",
                "search",
                {"query": "from:a", "folder": None, "limit": 10},
            ),
            (
                "search from:b",
                "search",
                {"query": "from:b", "folder": None, "limit": 10},
            ),
        ]
        _execute_batch(ops, ["acct1"])
        assert mock_soft.call_count == 1
        assert client.disconnect.call_count == 1

    @patch("mailroom.__main__._make_client_soft")
    def test_sequential_accounts_two_connections(self, mock_soft):
        client = self._make_client()
        mock_soft.return_value = client
        ops = [("search q", "search", {"query": "q", "folder": None, "limit": 10})]
        _execute_batch(ops, ["acct1", "acct2"])
        assert mock_soft.call_count == 2

    @patch("mailroom.__main__._make_client_soft")
    def test_failed_connection_produces_error_not_omission(self, mock_soft):
        mock_soft.return_value = None  # simulate connection failure
        ops = [("search q", "search", {"query": "q", "folder": None, "limit": 10})]
        result = _execute_batch(ops, ["acct1"])
        assert "acct1" in result["search q"]
        assert "results" in result["search q"]["acct1"]  # _empty_search_result shape

    @patch("mailroom.__main__._make_client_soft")
    def test_runtime_error_produces_error_dict(self, mock_soft):
        client = MagicMock()
        client.search_emails.side_effect = RuntimeError("timeout")
        mock_soft.return_value = client
        ops = [("search q", "search", {"query": "q", "folder": None, "limit": 10})]
        result = _execute_batch(ops, ["acct1"])
        assert "error" in result["search q"]["acct1"]
        assert "timeout" in result["search q"]["acct1"]["error"]

    @patch("mailroom.__main__._make_client_soft")
    def test_valueerror_propagates(self, mock_soft):
        client = MagicMock()
        client.search_emails.side_effect = ValueError("bad query")
        mock_soft.return_value = client
        ops = [("search q", "search", {"query": "q", "folder": None, "limit": 10})]
        with pytest.raises(ValueError, match="bad query"):
            _execute_batch(ops, ["acct1"])

    @patch("mailroom.__main__._make_client_soft")
    def test_multi_account_results_keyed_by_account(self, mock_soft):
        def make_client_for(name):
            c = MagicMock()
            c.search_emails.return_value = _fake_search_result(name)
            return c

        mock_soft.side_effect = lambda n: make_client_for(n)
        ops = [("search q", "search", {"query": "q", "folder": None, "limit": 10})]
        result = _execute_batch(ops, ["acct1", "acct2"])
        assert "acct1" in result["search q"]
        assert "acct2" in result["search q"]
