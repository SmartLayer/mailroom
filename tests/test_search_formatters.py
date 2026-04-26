"""Tests for `--format text` and `--format oneline` rendering of the
`message_id` field in search results. The dispatcher prefetch in aesop reads
the text format directly into the SPAR-A prompt, so the Message-ID has to
appear there for the agent to thread a reply onto the parent."""

from mailroom.__main__ import _format_search_oneline, _format_search_text


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


def _wrap(hits: list, account: str = "acct1") -> dict:
    return {
        account: {
            "results": hits,
            "provenance": {
                "source": "remote",
                "indexed_at": None,
                "fell_back_reason": None,
            },
        }
    }


class TestFormatSearchText:

    def test_renders_message_id_when_present(self):
        out = _format_search_text(_wrap([_hit("<thread-1@example.com>")]))
        assert "<thread-1@example.com>" in out
        # The label should sit alongside the existing date/from/to/folder lines.
        assert "id:" in out

    def test_omits_id_line_when_missing(self):
        # Backwards-compat: results from older mailroom versions or local
        # caches without message_id should still render cleanly.
        h = _hit()
        del h["message_id"]
        out = _format_search_text(_wrap([h]))
        assert "id:" not in out


class TestFormatSearchOneline:

    def test_appends_message_id_column(self):
        out = _format_search_oneline(_wrap([_hit("<thread-1@example.com>")]))
        assert "<thread-1@example.com>" in out
        # Fields are tab-separated; message_id should be the trailing column.
        first = out.splitlines()[0]
        cols = first.split("\t")
        assert cols[-1] == "<thread-1@example.com>"

    def test_blank_when_message_id_missing(self):
        h = _hit()
        del h["message_id"]
        out = _format_search_oneline(_wrap([h]))
        first = out.splitlines()[0]
        cols = first.split("\t")
        # Trailing blank column rather than crash.
        assert cols[-1] == ""
