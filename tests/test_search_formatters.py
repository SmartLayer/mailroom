"""Tests for ``--format text`` and ``--format oneline`` rendering.

The dispatcher prefetch in aesop reads the text format directly into the
SPAR-A prompt, so the Message-ID has to appear there for the agent to thread
a reply onto the parent.
"""

from mailroom.__main__ import _format_chain_oneline, _format_chain_text


def _hit(message_id: str = "<m@example.com>") -> dict:
    return {
        "uid": 42,
        "folder": "INBOX",
        "from": "Alice <alice@example.com>",
        "to": ["Bob <bob@example.com>"],
        "subject": "Hello",
        "date": "2026-04-01T10:00:00+00:00",
        "flags": [],
        "has_attachments": False,
        "message_id": message_id,
    }


def _wrap(hits: list, account: str = "acct1", op_key: str = "search q") -> dict:
    return {
        op_key: {
            account: {
                "results": hits,
                "provenance": {
                    "source": "remote",
                    "indexed_at": None,
                    "fell_back_reason": None,
                },
            }
        }
    }


class TestFormatChainText:

    def test_renders_message_id_when_present(self):
        out = _format_chain_text(_wrap([_hit("<thread-1@example.com>")]))
        assert "<thread-1@example.com>" in out
        assert "id:" in out

    def test_omits_id_line_when_missing(self):
        h = _hit()
        del h["message_id"]
        out = _format_chain_text(_wrap([h]))
        assert "id:" not in out

    def test_op_key_header_present(self):
        out = _format_chain_text(_wrap([_hit()], op_key="search from:alice"))
        assert "=== search from:alice ===" in out

    def test_account_header_present(self):
        out = _format_chain_text(_wrap([_hit()], account="work"))
        assert "== work ==" in out

    def test_multiple_op_keys(self):
        wrapped = {
            "search from:a": {
                "acct1": {
                    "results": [_hit("<id1>")],
                    "provenance": {
                        "source": "remote",
                        "indexed_at": None,
                        "fell_back_reason": None,
                    },
                }
            },
            "search from:b": {
                "acct1": {
                    "results": [_hit("<id2>")],
                    "provenance": {
                        "source": "remote",
                        "indexed_at": None,
                        "fell_back_reason": None,
                    },
                }
            },
        }
        out = _format_chain_text(wrapped)
        assert "=== search from:a ===" in out
        assert "=== search from:b ===" in out
        assert "<id1>" in out
        assert "<id2>" in out

    def test_error_value_renders_without_crash(self):
        wrapped = {"search from:x": {"acct1": {"error": "connection failed"}}}
        out = _format_chain_text(wrapped)
        assert "error: connection failed" in out


class TestFormatChainOneline:

    def test_appends_message_id_column(self):
        out = _format_chain_oneline(_wrap([_hit("<thread-1@example.com>")]))
        assert "<thread-1@example.com>" in out
        first = out.splitlines()[0]
        cols = first.split("\t")
        assert cols[-1] == "<thread-1@example.com>"

    def test_blank_when_message_id_missing(self):
        h = _hit()
        del h["message_id"]
        out = _format_chain_oneline(_wrap([h]))
        first = out.splitlines()[0]
        cols = first.split("\t")
        assert cols[-1] == ""

    def test_op_key_is_first_column(self):
        out = _format_chain_oneline(_wrap([_hit()], op_key="search from:alice"))
        first = out.splitlines()[0]
        cols = first.split("\t")
        assert cols[0] == "search from:alice"

    def test_account_is_second_column(self):
        out = _format_chain_oneline(_wrap([_hit()], account="work", op_key="search q"))
        first = out.splitlines()[0]
        cols = first.split("\t")
        assert cols[1] == "work"

    def test_no_results_line(self):
        wrapped = {
            "search from:x": {
                "acct1": {
                    "results": [],
                    "provenance": {
                        "source": "remote",
                        "indexed_at": None,
                        "fell_back_reason": None,
                    },
                }
            }
        }
        out = _format_chain_oneline(wrapped)
        assert "(no results)" in out
        first = out.splitlines()[0]
        assert first.startswith("search from:x\tacct1\t")
