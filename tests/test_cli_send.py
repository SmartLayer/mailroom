"""CLI tests for ``compose --send`` and ``reply --send`` under the
two-mode design.

Mode A: ``--identity NAME`` resolves From, display name, IMAP block, SMTP,
and sent_folder from a configured ``[identity.NAME]``.
Mode B: ``--smtp NAME --from EMAIL [--name N] [--fcc IMAP:FOLDER]`` sends
through a named SMTP block with a free-form From; no ``[identity.*]`` is
consulted. The SMTP block must carry its own credentials.

Drafting (no ``--send``) keeps the previous convenience defaults.
"""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mailroom.__main__ import app
from mailroom.config import (
    Identity,
    ImapBlock,
    MailroomConfig,
    SmtpConfig,
)

runner = CliRunner()


def _cfg() -> MailroomConfig:
    """Two-identity block on a Fastmail-style SMTP so FCC actually runs."""
    block = ImapBlock(
        host="imap.fastmail.com",
        port=993,
        username="login@x.com",
        password="p",
        default_smtp="fast",
    )
    return MailroomConfig(
        imap_blocks={"acct": block},
        _default_imap="acct",
        identities={
            "primary": Identity(imap="acct", address="primary@x.com", name="Primary"),
            "alias": Identity(imap="acct", address="alias@x.com", name="Alias"),
        },
        smtp_blocks={
            "fast": SmtpConfig(
                host="smtp.fastmail.com",
                port=587,
                username="login@x.com",
                password="p",
            )
        },
    )


def _cfg_with_relay() -> MailroomConfig:
    """Adds an SES-style relay block with its own credentials, plus a
    second IMAP block usable as an --fcc target.
    """
    cfg = _cfg()
    cfg.imap_blocks["work"] = ImapBlock(
        host="imap.work.com",
        port=993,
        username="login@work.com",
        password="p",
    )
    cfg.smtp_blocks["ses"] = SmtpConfig(
        host="email-smtp.eu-west-1.amazonaws.com",
        port=587,
        username="AKIA",
        password="ses-secret",
    )
    cfg.smtp_blocks["template"] = SmtpConfig(host="smtp.fastmail.com", port=587)
    return cfg


def _result() -> dict:
    return {
        "message_id_local": "<x@local>",
        "message_id_sent": "<x@local>",
        "smtp_response": "OK",
        "accepted_recipients": ["alice@y.com"],
    }


def _client() -> MagicMock:
    c = MagicMock()
    c._get_sent_folder.return_value = "Sent"
    c._get_drafts_folder.return_value = "Drafts"
    c.append_raw.return_value = 999
    c.save_draft_mime.return_value = 42
    return c


class TestComposeSendModeA:
    """``--identity NAME``: resolves everything from the configured block."""

    def test_send_with_identity(self):
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
                    "--identity",
                    "alias",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["identity"] == "alias@x.com"
        assert out["fcc_folder"] == "Sent"
        from_hdr = str(captured[0].get("From"))
        assert "alias@x.com" in from_hdr
        assert "Alias" in from_hdr  # identity's display name preserved

    def test_unknown_identity_errors(self):
        cfg = _cfg()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--identity",
                    "ghost",
                ],
            )
        assert result.exit_code == 1
        assert "ghost" in (result.output + (result.stderr or ""))
        send_mock.assert_not_called()

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
                    "--identity",
                    "primary",
                    "--no-save-sent",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["fcc_folder"] is None
        client.append_raw.assert_not_called()


class TestComposeSendModeB:
    """``--smtp NAME --from EMAIL``: free-form From through a named SMTP."""

    def test_send_with_smtp_and_from_no_fcc(self):
        cfg = _cfg_with_relay()
        captured: list = []

        def fake_send(msg, smtp_cfg, transport=None):
            captured.append((msg, smtp_cfg))
            return (msg.as_bytes(), _result())

        # No --fcc => no client should be opened.
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client") as make_client_mock,
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
                    "--send",
                    "--smtp",
                    "ses",
                    "--from",
                    "one-off@example.com",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["identity"] == "one-off@example.com"
        assert out["fcc_folder"] is None
        make_client_mock.assert_not_called()  # mode B + no --fcc => no IMAP
        # The SMTP block passed to send() is the SES one, with its own creds.
        assert captured[0][1].host == "email-smtp.eu-west-1.amazonaws.com"
        assert captured[0][1].username == "AKIA"

    def test_send_with_smtp_from_fcc_opens_named_block(self):
        cfg = _cfg_with_relay()
        client = _client()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client) as mc,
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
                    "--smtp",
                    "ses",
                    "--from",
                    "one-off@example.com",
                    "--fcc",
                    "work:Archive",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["fcc_folder"] == "Archive"
        # _make_client was called with imap_override="work" for FCC.
        mc.assert_called_once_with(imap_override="work")
        client.append_raw.assert_called_once()

    def test_send_with_name_override(self):
        cfg = _cfg_with_relay()
        captured: list = []

        def fake_send(msg, smtp_cfg, transport=None):
            captured.append(msg)
            return (msg.as_bytes(), _result())

        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
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
                    "--send",
                    "--smtp",
                    "ses",
                    "--from",
                    "one-off@example.com",
                    "--name",
                    "One Off",
                ],
            )
        assert result.exit_code == 0, result.output
        from_hdr = str(captured[0].get("From"))
        assert "one-off@example.com" in from_hdr
        assert "One Off" in from_hdr

    def test_smtp_without_from_errors(self):
        cfg = _cfg_with_relay()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--smtp",
                    "ses",
                ],
            )
        assert result.exit_code == 1
        send_mock.assert_not_called()

    def test_credential_less_smtp_rejected(self):
        cfg = _cfg_with_relay()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--smtp",
                    "template",
                    "--from",
                    "one-off@example.com",
                ],
            )
        assert result.exit_code == 1
        assert "template" in (result.output + (result.stderr or ""))
        send_mock.assert_not_called()

    def test_invalid_display_name_rejected(self):
        cfg = _cfg_with_relay()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--smtp",
                    "ses",
                    "--from",
                    "one-off@example.com",
                    "--name",
                    "Bad, Name",
                ],
            )
        assert result.exit_code == 1
        assert "RFC 5322" in (result.output + (result.stderr or ""))
        send_mock.assert_not_called()

    def test_fcc_unknown_imap_block_rejected(self):
        cfg = _cfg_with_relay()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--smtp",
                    "ses",
                    "--from",
                    "one-off@example.com",
                    "--fcc",
                    "ghost:Sent",
                ],
            )
        assert result.exit_code == 1
        assert "ghost" in (result.output + (result.stderr or ""))
        send_mock.assert_not_called()

    def test_fcc_missing_colon_rejected(self):
        cfg = _cfg_with_relay()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--smtp",
                    "ses",
                    "--from",
                    "one-off@example.com",
                    "--fcc",
                    "Sent",
                ],
            )
        assert result.exit_code == 1
        send_mock.assert_not_called()


class TestComposeSendNoRoute:
    def test_no_identity_no_smtp_errors(self):
        cfg = _cfg()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                ],
            )
        assert result.exit_code == 1
        msg = result.output + (result.stderr or "")
        assert "--identity" in msg and "--smtp" in msg
        send_mock.assert_not_called()

    def test_identity_and_smtp_mutually_exclusive(self):
        cfg = _cfg_with_relay()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.smtp_transport.send") as send_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "compose",
                    "--to",
                    "a@y.com",
                    "--body",
                    "x",
                    "--send",
                    "--identity",
                    "primary",
                    "--smtp",
                    "ses",
                ],
            )
        assert result.exit_code == 1
        assert "mutually exclusive" in (result.output + (result.stderr or ""))
        send_mock.assert_not_called()


class TestComposeNonSendIgnoresModeFlags:
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

    def test_default_save_draft_uses_identity_from(self):
        """Drafting still uses the legacy default-resolution path: the
        first identity on the [imap.NAME] block (--imap/-i) is the From.
        """
        cfg = _cfg()
        client = _client()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
        ):
            result = runner.invoke(
                app,
                ["compose", "--to", "alice@y.com", "--body", "hi"],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["identity"] == "primary@x.com"
        client.save_draft_mime.assert_called_once()

    def test_mode_flags_rejected_in_drafting(self):
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
                    "--identity",
                    "primary",
                ],
            )
        assert result.exit_code == 1
        assert "--send" in (result.output + (result.stderr or ""))


class TestReplySend:
    @staticmethod
    def _parent_alias():
        from mailroom.models import Email, EmailAddress, EmailContent

        return Email(
            uid=10,
            from_=EmailAddress(name="Sender", address="sender@y.com"),
            to=[EmailAddress(name="", address="alias@x.com")],
            subject="hi",
            content=EmailContent(text="body", html=None),
            message_id="<parent@y.com>",
        )

    @staticmethod
    def _parent_unrelated():
        from mailroom.models import Email, EmailAddress, EmailContent

        return Email(
            uid=10,
            from_=EmailAddress(name="Sender", address="sender@y.com"),
            to=[EmailAddress(name="", address="someone-else@y.com")],
            subject="hi",
            content=EmailContent(text="body", html=None),
            message_id="<parent@y.com>",
        )

    def test_reply_send_recipient_match_succeeds(self):
        cfg = _cfg()
        client = _client()
        client.fetch_email.return_value = self._parent_alias()
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
        assert out["identity"] == "alias@x.com"
        from_hdr = str(captured[0].get("From"))
        assert "alias@x.com" in from_hdr

    def test_reply_send_no_recipient_match_errors(self):
        """The silent-default closure: no recipient match plus no --identity
        and no --smtp must error rather than picking identities[0]."""
        cfg = _cfg()
        client = _client()
        client.fetch_email.return_value = self._parent_unrelated()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
            patch("mailroom.smtp_transport.send") as send_mock,
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
        assert result.exit_code == 1
        assert "no recipient" in (result.output + (result.stderr or "")).lower()
        send_mock.assert_not_called()

    def test_reply_send_with_explicit_identity(self):
        cfg = _cfg()
        client = _client()
        client.fetch_email.return_value = self._parent_alias()
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
                    "--send",
                    "--identity",
                    "primary",
                ],
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["identity"] == "primary@x.com"

    def test_reply_drafting_recipient_match_unchanged(self):
        """Drafting (no --send) keeps the legacy fallback semantics."""
        cfg = _cfg()
        client = _client()
        client.fetch_email.return_value = self._parent_unrelated()
        with (
            patch("mailroom.__main__.load_config", return_value=cfg),
            patch("mailroom.__main__._make_client", return_value=client),
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
                ],
            )
        # No --send: falls back to identities[0] just like the old path.
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["identity"] == "primary@x.com"
