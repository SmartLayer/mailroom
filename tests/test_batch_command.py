"""CLI integration tests for the ``batch`` subcommand and the new JSON output
shape produced by ``search`` and ``read``.

All tests use typer's CliRunner and mock IMAP connections — no network required.
"""

import json
import tempfile
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mailroom.__main__ import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _fake_search_result(source: str = "remote") -> dict:
    return {
        "results": [
            {
                "uid": 1,
                "folder": "INBOX",
                "from": "alice@example.com",
                "to": ["bob@example.com"],
                "subject": "Hello",
                "date": "2026-04-01T10:00:00+00:00",
                "flags": [],
                "has_attachments": False,
                "message_id": "<hello@example.com>",
            }
        ],
        "provenance": {"source": source, "indexed_at": None, "fell_back_reason": None},
    }


def _fake_empty_result() -> dict:
    return {
        "results": [],
        "provenance": {
            "source": "remote",
            "indexed_at": None,
            "fell_back_reason": None,
        },
    }


def _patch_search(result=None):
    """Return a context manager that patches _make_client_soft for search."""
    if result is None:
        result = _fake_search_result()

    def factory(name):
        client = MagicMock()
        client.search_emails.return_value = result
        return client

    return patch("mailroom.__main__._make_client_soft", side_effect=factory)


def _patch_config(account_name: str = "default"):
    from mailroom.config import AccountConfig, ImapConfig, MultiAccountConfig

    imap_cfg = ImapConfig(
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password="secret",
        use_ssl=True,
    )
    acct = AccountConfig(imap=imap_cfg)
    cfg = MultiAccountConfig(
        accounts={account_name: acct}, _default_account=account_name
    )
    return patch("mailroom.__main__.load_config", return_value=cfg)


# ---------------------------------------------------------------------------
# search command — new JSON shape
# ---------------------------------------------------------------------------


class TestSearchJsonShape:
    def test_single_search_has_op_key_outer(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["search", "from:alice"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        key = next(iter(data))
        assert key.startswith("search ")
        # inner key is account name
        assert "default" in data[key]
        assert "results" in data[key]["default"]

    def test_op_key_reflects_query(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["search", "from:alice"])
        data = json.loads(result.output)
        key = next(iter(data))
        assert "from:alice" in key

    def test_op_key_includes_folder_when_set(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["search", "-f", "INBOX", "from:alice"])
        data = json.loads(result.output)
        key = next(iter(data))
        assert "-f INBOX" in key

    def test_exit_code_1_on_no_results(self):
        with _patch_config(), _patch_search(_fake_empty_result()):
            result = runner.invoke(app, ["search", "from:nobody"])
        assert result.exit_code == 1

    def test_format_text_has_op_key_header(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["search", "--format", "text", "from:alice"])
        assert result.exit_code == 0
        assert "===" in result.output
        assert "from:alice" in result.output

    def test_format_oneline_op_key_first_column(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["search", "--format", "oneline", "from:alice"])
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        cols = first_line.split("\t")
        assert cols[0].startswith("search ")
        assert "from:alice" in cols[0]


# ---------------------------------------------------------------------------
# batch subcommand
# ---------------------------------------------------------------------------


class TestBatchSubcommand:
    def test_positional_args_two_search_ops(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(
                app, ["batch", "search from:alice", "search from:bob"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        keys = list(data.keys())
        assert any("from:alice" in k for k in keys)
        assert any("from:bob" in k for k in keys)

    def test_file_input(self):
        ops = ["search from:alice", "search from:bob"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(ops, f)
            fpath = f.name
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["batch", "--file", fpath])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

    def test_op_keys_echoed_exactly_as_submitted(self):
        op = "search  from:alice"  # double space intentional
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["batch", op])
        data = json.loads(result.output)
        assert op in data

    def test_unknown_subcommand_exits_1(self):
        with _patch_config():
            result = runner.invoke(app, ["batch", "move --uid 1"])
        assert result.exit_code == 1
        assert (
            "unsupported" in result.output.lower() or "error" in result.output.lower()
        )

    def test_format_text_has_section_header_per_op(self):
        ops = ["search from:alice", "search from:bob"]
        with _patch_config(), _patch_search():
            result = runner.invoke(app, ["batch", "--format", "text"] + ops)
        assert result.exit_code == 0
        assert "=== search from:alice ===" in result.output
        assert "=== search from:bob ===" in result.output

    def test_format_oneline_op_key_prepended(self):
        with _patch_config(), _patch_search():
            result = runner.invoke(
                app, ["batch", "--format", "oneline", "search from:alice"]
            )
        assert result.exit_code == 0
        first = result.output.strip().splitlines()[0]
        assert first.startswith("search from:alice\t")

    def test_exit_code_1_when_no_results(self):
        with _patch_config(), _patch_search(_fake_empty_result()):
            result = runner.invoke(app, ["batch", "search from:nobody"])
        assert result.exit_code == 1

    def test_no_args_no_stdin_exits_1(self):
        with _patch_config():
            result = runner.invoke(app, ["batch"])
        assert result.exit_code == 1

    def test_mixed_search_and_read_ops(self):
        """Batch can contain both search and read operations."""

        def factory(name):
            client = MagicMock()
            client.search_emails.return_value = _fake_search_result()
            email_obj = MagicMock()
            email_obj.from_ = MagicMock(__str__=lambda s: "alice@example.com")
            email_obj.to = []
            email_obj.subject = "Hello"
            email_obj.date = None
            email_obj.flags = []
            email_obj.message_id = "<hello@example.com>"
            email_obj.content.html = None
            email_obj.content.text = "body"
            email_obj.in_reply_to = None
            email_obj.references = None
            email_obj.cc = []
            email_obj.attachments = []
            client.fetch_email.return_value = email_obj
            return client

        with (
            _patch_config(),
            patch("mailroom.__main__._make_client_soft", side_effect=factory),
            patch(
                "mailroom.__main__._make_client",
                side_effect=lambda: factory("default"),
            ),
            patch(
                "mailroom.__main__._resolve_single_account_name", return_value="default"
            ),
        ):
            result = runner.invoke(
                app,
                ["batch", "search from:alice", "read -f INBOX --uid 1"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        search_key = next(k for k in data if k.startswith("search"))
        read_key = next(k for k in data if k.startswith("read"))
        assert "results" in data[search_key]["default"]
        assert "subject" in data[read_key]["default"]
