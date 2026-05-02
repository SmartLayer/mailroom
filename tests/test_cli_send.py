"""CLI tests for ``compose --send`` and ``reply --send``.

The existing test_tools_compose.py / test_tools_reply.py cover the MCP tools
layer (compose_and_save_draft etc.), which the CLI no longer goes through.
This file covers the new identity-aware CLI handlers and the --send / --from
/ --save-sent / --no-save-sent / --sent-folder flags.
"""

import json
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mailroom.__main__ import app
from mailroom.config import (
    AccountConfig,
    Identity,
    ImapConfig,
    MultiAccountConfig,
    SmtpConfig,
)
from mailroom.smtp_transport import SendResult

runner = CliRunner()


def _cfg() -> MultiAccountConfig:
    """Two-identity account on a Fastmail-style SMTP so FCC actually runs."""
    imap = ImapConfig(
        host="imap.fastmail.com",
        port=993,
        username="login@x.com",
        password="p",
    )
    acct = AccountConfig(
        imap=imap,
        default_smtp="fast",
        identities=[
            Identity(address="primary@x.com", name="Primary"),
            Identity(address="alias@x.com", name="Alias"),
        ],
    )
    return MultiAccountConfig(
        accounts={"acct": acct},
        _default_account="acct",
        smtp_blocks={
            "fast": SmtpConfig(
                host="smtp.fastmail.com",
                port=587,
                username="login@x.com",
                password="p",
            )
        },
    )


def _result() -> SendResult:
    return SendResult(
        message_id_local="<x@local>",
        message_id_sent="<x@local>",
        smtp_response="OK",
        accepted_recipients=["alice@y.com"],
    )


def _client() -> MagicMock:
    c = MagicMock()
    c._get_sent_folder.return_value = "Sent"
    c._get_drafts_folder.return_value = "Drafts"
    c.append_raw.return_value = 999
    c.save_draft_mime.return_value = 42
    return c


class TestComposeSend:
    def test_send_uses_first_identity_by_default(self):
        cfg = _cfg()
        client = _client()
        captured: list = []

        def fake_send(msg, smtp_cfg, transport=None):
            captured.append(msg)
            return (msg.as_bytes(), _result())

        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch("mailroom.smtp_transport.send", side_effect=fake_send),
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "alice@y.com",
                    "--body",
                    "hi",
                    "--subject",
                    "T",
                    "--send",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["identity"] == "primary@x.com"
        assert out["fcc_folder"] == "Sent"
        # The MIME's From header carries the chosen identity.
        from_hdr = str(captured[0].get("From"))
        assert "primary@x.com" in from_hdr
        assert "Primary" in from_hdr  # display name preserved

    def test_send_with_explicit_from(self):
        cfg = _cfg()
        client = _client()
        captured: list = []

        def fake_send(msg, smtp_cfg, transport=None):
            captured.append(msg)
            return (msg.as_bytes(), _result())

        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch("mailroom.smtp_transport.send", side_effect=fake_send),
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "alice@y.com",
                    "--body",
                    "hi",
                    "--from",
                    "alias@x.com",
                    "--send",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["identity"] == "alias@x.com"
        from_hdr = str(captured[0].get("From"))
        assert "alias@x.com" in from_hdr

    def test_send_with_unknown_from_errors(self):
        cfg = _cfg()
        client = _client()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "alice@y.com",
                    "--body",
                    "hi",
                    "--from",
                    "impostor@evil.com",
                    "--send",
                ],
            )
        assert result.exit_code == 1
        send_mock.assert_not_called()

    def test_send_and_output_mutually_exclusive(self):
        cfg = _cfg()
        with patch("mailroom.__main__.load_config", return_value=cfg):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "alice@y.com",
                    "--body",
                    "hi",
                    "--send",
                    "-o",
                    "-",
                ],
            )
        assert result.exit_code == 2
        assert "mutually exclusive" in (result.output or "") + (result.stderr or "")

    def test_no_save_sent_skips_fcc(self):
        cfg = _cfg()
        client = _client()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch(
                "mailroom.smtp_transport.send",
                return_value=(b"raw", _result()),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "alice@y.com",
                    "--body",
                    "hi",
                    "--send",
                    "--no-save-sent",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["fcc_folder"] is None
        client.append_raw.assert_not_called()

    def test_default_save_draft_uses_identity_from(self):
        """Backward-compat: no --send and no --output still saves draft, and
        the From header now uses the resolved identity (improvement over the
        old account-blind client.config.username)."""
        cfg = _cfg()
        client = _client()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "alice@y.com",
                    "--body",
                    "hi",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["identity"] == "primary@x.com"
        client.save_draft_mime.assert_called_once()
        # Confirm the saved MIME's From carried the identity.
        saved_msg = client.save_draft_mime.call_args[0][0]
        assert "primary@x.com" in str(saved_msg.get("From"))


class TestReplySend:
    def test_reply_picks_identity_matching_parent_recipient(self):
        cfg = _cfg()
        client = _client()

        # Build a parent where alias@x.com is in To, so reply uses that identity.
        from mailroom.models import Email, EmailAddress, EmailContent

        parent = Email(
            uid=10,
            from_=EmailAddress(name="Sender", address="sender@y.com"),
            to=[EmailAddress(name="", address="alias@x.com")],
            subject="hi",
            content=EmailContent(text="body", html=None),
            message_id="<parent@y.com>",
        )
        client.fetch_email.return_value = parent

        captured: list = []

        def fake_send(msg, smtp_cfg, transport=None):
            captured.append(msg)
            return (msg.as_bytes(), _result())

        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch("mailroom.smtp_transport.send", side_effect=fake_send),
        ):
            result = runner.invoke(
                app,
                [
                    "reply",
                    "-f",
                    "INBOX",
                    "-u",
                    "10",
                    "--body",
                    "thanks",
                    "--send",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        # Auto-picked alias because parent's To matched alias.
        assert out["identity"] == "alias@x.com"
        from_hdr = str(captured[0].get("From"))
        assert "alias@x.com" in from_hdr

    def test_reply_with_explicit_from_overrides_match(self):
        cfg = _cfg()
        client = _client()
        from mailroom.models import Email, EmailAddress, EmailContent

        parent = Email(
            uid=10,
            from_=EmailAddress(name="Sender", address="sender@y.com"),
            to=[EmailAddress(name="", address="alias@x.com")],
            subject="hi",
            content=EmailContent(text="body", html=None),
            message_id="<parent@y.com>",
        )
        client.fetch_email.return_value = parent

        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch(
                "mailroom.smtp_transport.send",
                return_value=(b"raw", _result()),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "reply",
                    "-f",
                    "INBOX",
                    "-u",
                    "10",
                    "--body",
                    "thanks",
                    "--from",
                    "primary@x.com",
                    "--send",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["identity"] == "primary@x.com"
