"""Tests for the `mailroom status` connection-probe table.

The CLI ``status`` command runs an IMAP login per [imap.NAME] and an
EHLO + optional auth per [smtp.NAME], then prints a short table.
Probes are mocked here so the tests do not require live servers.
"""

from unittest.mock import MagicMock, patch

from mailroom.__main__ import (
    _print_status_table,
    _probe_all,
    _probe_imap,
    _probe_smtp,
)
from mailroom.config import ImapBlock, MailroomConfig, SmtpConfig


def _imap_block(name: str = "acc") -> ImapBlock:
    return ImapBlock(
        host=f"imap.{name}.example.com",
        port=993,
        username=f"user@{name}.example.com",
        password="p",
        use_ssl=True,
    )


def _smtp_block(
    name: str = "out",
    *,
    username: str = "",
    password: str = "",
    port: int = 587,
) -> SmtpConfig:
    return SmtpConfig(
        host=f"smtp.{name}.example.com",
        port=port,
        username=username or None,
        password=password or None,
    )


class TestProbeImap:
    """`_probe_imap` returns 'ok' on success and 'FAIL: ...' on connect error."""

    def test_ok_when_connect_succeeds(self):
        with patch("mailroom.__main__.ImapClient") as mock_cls:
            instance = MagicMock()
            mock_cls.return_value = instance
            assert _probe_imap(_imap_block()) == "ok"
            instance.connect.assert_called_once()
            instance.disconnect.assert_called_once()

    def test_fail_message_carries_exception_text(self):
        with patch("mailroom.__main__.ImapClient") as mock_cls:
            instance = MagicMock()
            instance.connect.side_effect = ConnectionError("login refused")
            mock_cls.return_value = instance
            assert _probe_imap(_imap_block()) == "FAIL: login refused"


class TestProbeSmtp:
    """`_probe_smtp` covers the three blocks the table must distinguish.

    A block with credentials authenticates ('ok'); a credential-less
    template stops at EHLO+STARTTLS ('ok (template, no auth)'); a
    block whose server is unreachable surfaces 'FAIL: ...'.
    """

    def test_creds_block_authenticates(self):
        with patch("smtplib.SMTP") as mock_cls:
            conn = MagicMock()
            mock_cls.return_value = conn
            assert _probe_smtp(_smtp_block(username="u", password="p")) == "ok"
            conn.starttls.assert_called_once()
            conn.login.assert_called_once_with("u", "p")
            conn.quit.assert_called_once()

    def test_template_block_stops_at_starttls(self):
        with patch("smtplib.SMTP") as mock_cls:
            conn = MagicMock()
            mock_cls.return_value = conn
            assert _probe_smtp(_smtp_block()) == "ok (template, no auth)"
            conn.starttls.assert_called_once()
            conn.login.assert_not_called()

    def test_smtps_uses_ssl_factory(self):
        """Port 465 picks SMTP_SSL; STARTTLS is not issued."""
        with patch("smtplib.SMTP_SSL") as mock_cls:
            conn = MagicMock()
            mock_cls.return_value = conn
            assert _probe_smtp(_smtp_block(port=465)) == "ok (template, no auth)"
            conn.starttls.assert_not_called()

    def test_connect_failure_surfaces(self):
        with patch("smtplib.SMTP", side_effect=OSError("network unreachable")):
            assert "FAIL" in _probe_smtp(_smtp_block())


class TestProbeAll:
    """`_probe_all` orders rows IMAP-then-SMTP and runs probes in parallel."""

    def test_orders_imap_then_smtp(self):
        cfg = MailroomConfig(
            imap_blocks={"a": _imap_block("a"), "b": _imap_block("b")},
            smtp_blocks={"out": _smtp_block("out")},
        )
        with (
            patch("mailroom.__main__._probe_imap", return_value="ok"),
            patch("mailroom.__main__._probe_smtp", return_value="ok"),
        ):
            rows = _probe_all(cfg)
        kinds = [r[1] for r in rows]
        assert kinds == ["imap", "imap", "smtp"]
        names = [r[0] for r in rows]
        assert names == ["a", "b", "out"]


class TestPrintStatusTable:
    """Output is a header line plus aligned rows; empty config has its own line."""

    def test_renders_aligned_columns(self, capsys):
        rows = [
            ("acc1", "imap", "imap.example.com:993", "ok"),
            ("acc-with-long-name", "imap", "imap.example.com:993", "FAIL: x"),
        ]
        _print_status_table(rows)
        out = capsys.readouterr().out.splitlines()
        # First line is the version stamp; second is the header; rest are rows.
        assert out[0].startswith("mailroom ")
        assert "NAME" in out[1] and "STATUS" in out[1]
        # Each row carries every field, and every row has the same total
        # width as the header (the fixed-width fmt string pads the last
        # column out, so column boundaries line up).
        header_width = len(out[1])
        for row, expected in zip(out[2:], rows):
            assert len(row) == header_width
            for field in expected:
                assert field in row

    def test_empty_config_prints_marker(self, capsys):
        _print_status_table([])
        out = capsys.readouterr().out
        assert "no [imap.*] or [smtp.*] blocks configured" in out
