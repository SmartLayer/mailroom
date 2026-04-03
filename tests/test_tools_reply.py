"""Tests for draft_reply_tool (MCP) and draft-reply (CLI)."""

import json
import os
import sys
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

from mcp.server.fastmcp import FastMCP, Context

from mailroom.models import Email, EmailAddress, EmailContent
from mailroom.tools import register_tools


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_email():
    return Email(
        message_id="<test123@example.com>",
        subject="Test Email",
        from_=EmailAddress(name="Sender", address="sender@example.com"),
        to=[EmailAddress(name="Recipient", address="recipient@example.com")],
        cc=[],
        date=datetime(2026, 1, 15, 10, 0, 0),
        content=EmailContent(text="Original body", html=None),
        attachments=[],
        flags=["\\Seen"],
        headers={"References": "<earlier@example.com>"},
        folder="INBOX",
        uid=42,
    )


def _register_and_extract_tools():
    """Register tools against a mock MCP and return the captured functions."""
    mcp = MagicMock(spec=FastMCP)
    stored = {}

    def mock_tool_decorator():
        def decorator(func):
            stored[func.__name__] = func
            return func
        return decorator

    mcp.tool = mock_tool_decorator
    imap_client = MagicMock()

    register_tools(mcp, imap_client)
    return stored, imap_client


# ---------------------------------------------------------------------------
# MCP tool tests
# ---------------------------------------------------------------------------

class TestDraftReplyTool:

    @pytest.fixture
    def tools(self):
        return _register_and_extract_tools()

    @pytest.fixture
    def ctx(self):
        return MagicMock(spec=Context)

    @pytest.mark.asyncio
    async def test_success_plain_text(self, tools, mock_email, ctx):
        stored, imap_client = tools
        draft_reply = stored["draft_reply_tool"]

        with patch("mailroom.tools.get_client_from_context") as gc:
            gc.return_value = imap_client
            imap_client.fetch_email.return_value = mock_email
            imap_client.save_draft_mime.return_value = 100
            imap_client._get_drafts_folder.return_value = "Drafts"

            with patch("mailroom.smtp_client.create_reply_mime") as mock_create:
                mime_msg = MagicMock()
                mock_create.return_value = mime_msg

                result = await draft_reply(
                    folder="INBOX", uid=42, reply_body="Thanks!", ctx=ctx,
                )

        assert result["status"] == "success"
        assert result["draft_uid"] == 100
        assert result["draft_folder"] == "Drafts"
        imap_client.fetch_email.assert_called_once_with(42, folder="INBOX")
        mock_create.assert_called_once()
        imap_client.save_draft_mime.assert_called_once_with(mime_msg)

    @pytest.mark.asyncio
    async def test_reply_all_with_cc(self, tools, mock_email, ctx):
        stored, imap_client = tools
        draft_reply = stored["draft_reply_tool"]

        with patch("mailroom.tools.get_client_from_context") as gc:
            gc.return_value = imap_client
            imap_client.fetch_email.return_value = mock_email
            imap_client.save_draft_mime.return_value = 101
            imap_client._get_drafts_folder.return_value = "Drafts"

            with patch("mailroom.smtp_client.create_reply_mime") as mock_create:
                mock_create.return_value = MagicMock()

                result = await draft_reply(
                    folder="INBOX", uid=42, reply_body="Noted",
                    ctx=ctx, reply_all=True,
                    cc=["extra@example.com"],
                )

        assert result["status"] == "success"
        # Verify create_reply_mime received reply_all=True and cc as EmailAddress list
        kw = mock_create.call_args
        assert kw.kwargs["reply_all"] is True
        assert len(kw.kwargs["cc"]) == 1
        assert kw.kwargs["cc"][0].address == "extra@example.com"

    @pytest.mark.asyncio
    async def test_html_body(self, tools, mock_email, ctx):
        stored, imap_client = tools
        draft_reply = stored["draft_reply_tool"]

        with patch("mailroom.tools.get_client_from_context") as gc:
            gc.return_value = imap_client
            imap_client.fetch_email.return_value = mock_email
            imap_client.save_draft_mime.return_value = 102
            imap_client._get_drafts_folder.return_value = "Drafts"

            with patch("mailroom.smtp_client.create_reply_mime") as mock_create:
                mock_create.return_value = MagicMock()

                result = await draft_reply(
                    folder="INBOX", uid=42, reply_body="plain",
                    ctx=ctx, body_html="<p>rich</p>",
                )

        assert result["status"] == "success"
        assert mock_create.call_args.kwargs["html_body"] == "<p>rich</p>"

    @pytest.mark.asyncio
    async def test_bcc_header_added(self, tools, mock_email, ctx):
        stored, imap_client = tools
        draft_reply = stored["draft_reply_tool"]

        with patch("mailroom.tools.get_client_from_context") as gc:
            gc.return_value = imap_client
            imap_client.fetch_email.return_value = mock_email
            imap_client.save_draft_mime.return_value = 103
            imap_client._get_drafts_folder.return_value = "Drafts"

            from email.message import EmailMessage
            mime_msg = EmailMessage()
            mime_msg.set_content("test")

            with patch("mailroom.smtp_client.create_reply_mime", return_value=mime_msg):
                result = await draft_reply(
                    folder="INBOX", uid=42, reply_body="Thanks", ctx=ctx,
                    bcc=["copy@example.com"],
                )

        assert result["status"] == "success"
        # Verify BCC header was added to the MIME message before saving
        saved_msg = imap_client.save_draft_mime.call_args[0][0]
        assert "copy@example.com" in saved_msg["Bcc"]

    @pytest.mark.asyncio
    async def test_email_not_found(self, tools, ctx):
        stored, imap_client = tools
        draft_reply = stored["draft_reply_tool"]

        with patch("mailroom.tools.get_client_from_context") as gc:
            gc.return_value = imap_client
            imap_client.fetch_email.return_value = None

            result = await draft_reply(
                folder="INBOX", uid=999, reply_body="Hi", ctx=ctx,
            )

        assert result["status"] == "error"
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_save_draft_failure(self, tools, mock_email, ctx):
        stored, imap_client = tools
        draft_reply = stored["draft_reply_tool"]

        with patch("mailroom.tools.get_client_from_context") as gc:
            gc.return_value = imap_client
            imap_client.fetch_email.return_value = mock_email
            imap_client.save_draft_mime.return_value = None

            with patch("mailroom.smtp_client.create_reply_mime") as mock_create:
                mock_create.return_value = MagicMock()

                result = await draft_reply(
                    folder="INBOX", uid=42, reply_body="Hi", ctx=ctx,
                )

        assert result["status"] == "error"
        assert "failed to save" in result["message"].lower()


# ---------------------------------------------------------------------------
# CLI draft-reply tests
# ---------------------------------------------------------------------------

class TestDraftReplyCLI:

    @pytest.fixture
    def mock_client(self, mock_email):
        client = MagicMock()
        client.fetch_email.return_value = mock_email
        client.config.username = "recipient@example.com"
        client.save_draft_mime.return_value = 200
        client._get_drafts_folder.return_value = "Drafts"
        return client

    def test_default_saves_draft(self, mock_client, mock_email, capsys):
        from mailroom.__main__ import app
        from typer.testing import CliRunner

        runner = CliRunner()

        with patch("mailroom.__main__._make_client", return_value=mock_client):
            with patch("mailroom.smtp_client.create_reply_mime") as mock_create:
                mock_create.return_value = MagicMock()
                result = runner.invoke(app, [
                    "--config", "dummy.yaml",
                    "draft-reply",
                    "-f", "INBOX", "--uid", "42", "--body", "Thanks",
                ])

        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["draft_uid"] == 200

    def test_output_to_file(self, mock_client, mock_email, tmp_path):
        from mailroom.__main__ import app
        from typer.testing import CliRunner
        from email.message import EmailMessage

        runner = CliRunner()
        out_path = str(tmp_path / "reply.eml")

        mime_msg = EmailMessage()
        mime_msg.set_content("Hello")
        mime_msg["Subject"] = "Re: Test"

        with patch("mailroom.__main__._make_client", return_value=mock_client):
            with patch("mailroom.smtp_client.create_reply_mime", return_value=mime_msg):
                result = runner.invoke(app, [
                    "--config", "dummy.yaml",
                    "draft-reply",
                    "-f", "INBOX", "--uid", "42", "--body", "Hello",
                    "-o", out_path,
                ])

        assert result.exit_code == 0
        assert os.path.exists(out_path)
        raw = open(out_path, "rb").read()
        assert b"Re: Test" in raw

    def test_output_to_stdout(self, mock_client, mock_email):
        from mailroom.__main__ import app
        from typer.testing import CliRunner
        from email.message import EmailMessage

        runner = CliRunner()

        mime_msg = EmailMessage()
        mime_msg.set_content("Stdout body")
        mime_msg["Subject"] = "Re: Stdout"

        with patch("mailroom.__main__._make_client", return_value=mock_client):
            with patch("mailroom.smtp_client.create_reply_mime", return_value=mime_msg):
                result = runner.invoke(app, [
                    "--config", "dummy.yaml",
                    "draft-reply",
                    "-f", "INBOX", "--uid", "42", "--body", "Stdout body",
                    "-o", "-",
                ])

        assert result.exit_code == 0
        # The raw RFC 822 message should appear on stdout
        assert "Re: Stdout" in result.output

    def test_bcc_in_raw_output(self, mock_client, mock_email, tmp_path):
        from mailroom.__main__ import app
        from typer.testing import CliRunner
        from email.message import EmailMessage

        runner = CliRunner()
        out_path = str(tmp_path / "reply_bcc.eml")

        mime_msg = EmailMessage()
        mime_msg.set_content("Body")
        mime_msg["Subject"] = "Re: Bcc test"

        with patch("mailroom.__main__._make_client", return_value=mock_client):
            with patch("mailroom.smtp_client.create_reply_mime", return_value=mime_msg):
                result = runner.invoke(app, [
                    "--config", "dummy.yaml",
                    "draft-reply",
                    "-f", "INBOX", "--uid", "42", "--body", "Body",
                    "--bcc", "copy@example.com",
                    "-o", out_path,
                ])

        assert result.exit_code == 0
        raw = open(out_path, "rb").read()
        assert b"Bcc: copy@example.com" in raw

    def test_email_not_found(self):
        from mailroom.__main__ import app
        from typer.testing import CliRunner

        runner = CliRunner()
        client = MagicMock()
        client.fetch_email.return_value = None

        with patch("mailroom.__main__._make_client", return_value=client):
            result = runner.invoke(app, [
                "--config", "dummy.yaml",
                "draft-reply",
                "-f", "INBOX", "--uid", "999", "--body", "Hi",
            ])

        assert result.exit_code != 0
