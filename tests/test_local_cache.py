"""Tests for the optional local-cache search backend (mu)."""

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict
from unittest.mock import patch

import pytest

from mailroom.config import AccountConfig, ImapConfig, LocalCacheConfig
from mailroom.local_cache import MuBackend, MuFailure
from mailroom.query_parser import UntranslatableQuery


def _make_account_cfg(
    maildir: str = "/var/local/mail/director-rivermill-au",
) -> AccountConfig:
    """Build an AccountConfig with a configured maildir for tests."""
    imap = ImapConfig(
        host="imap.example.com",
        port=993,
        username="director@rivermill.au",
        password="password",
        use_ssl=True,
    )
    return AccountConfig(imap=imap, maildir=maildir)


def _make_xapian_dir(tmp_path) -> str:
    """Create a fake mu store layout under tmp_path and return muhome."""
    muhome = tmp_path / "muhome"
    xapian = muhome / "xapian"
    xapian.mkdir(parents=True)
    return str(muhome)


class TestMuBackendIsEligible:
    """Eligibility check mirrors provenance.fell_back_reason vocabulary."""

    def test_mu_missing(self, tmp_path, monkeypatch):
        """When the mu binary is not on PATH the backend declines with
        reason ``mu_missing``."""
        muhome = _make_xapian_dir(tmp_path)
        cfg = LocalCacheConfig(mu_index=muhome)
        backend = MuBackend(cfg)
        monkeypatch.setattr("mailroom.local_cache.shutil.which", lambda _: None)

        result = backend.is_eligible(_make_account_cfg())

        assert result.eligible is False
        assert result.reason == "mu_missing"

    def test_db_missing(self, tmp_path, monkeypatch):
        """When the xapian directory is absent the backend declines with
        reason ``db_missing``."""
        muhome = tmp_path / "muhome"
        muhome.mkdir()
        # No xapian subdir created.
        cfg = LocalCacheConfig(mu_index=str(muhome))
        backend = MuBackend(cfg)
        monkeypatch.setattr(
            "mailroom.local_cache.shutil.which", lambda _: "/usr/bin/mu"
        )

        result = backend.is_eligible(_make_account_cfg())

        assert result.eligible is False
        assert result.reason == "db_missing"

    def test_stale(self, tmp_path, monkeypatch):
        """A xapian dir whose mtime is older than max_staleness_seconds
        triggers a stale fallback."""
        muhome = _make_xapian_dir(tmp_path)
        xapian = os.path.join(muhome, "xapian")
        # Backdate xapian mtime to two hours ago.
        old_ts = datetime.now().timestamp() - 7200
        os.utime(xapian, (old_ts, old_ts))
        cfg = LocalCacheConfig(mu_index=muhome, max_staleness_seconds=3600)
        backend = MuBackend(cfg)
        monkeypatch.setattr(
            "mailroom.local_cache.shutil.which", lambda _: "/usr/bin/mu"
        )

        result = backend.is_eligible(_make_account_cfg())

        assert result.eligible is False
        assert result.reason == "stale"

    def test_eligible(self, tmp_path, monkeypatch):
        """A fresh xapian dir plus mu on PATH yields eligibility."""
        muhome = _make_xapian_dir(tmp_path)
        cfg = LocalCacheConfig(mu_index=muhome, max_staleness_seconds=86400)
        backend = MuBackend(cfg)
        monkeypatch.setattr(
            "mailroom.local_cache.shutil.which", lambda _: "/usr/bin/mu"
        )

        result = backend.is_eligible(_make_account_cfg())

        assert result.eligible is True
        assert result.reason is None


class TestMuBackendSearch:
    """search() invokes mu, parses output, and surfaces failures."""

    def _backend(self, tmp_path) -> MuBackend:
        """Build a MuBackend with a real (empty) xapian dir at tmp_path."""
        muhome = _make_xapian_dir(tmp_path)
        cfg = LocalCacheConfig(mu_index=muhome, max_staleness_seconds=86400)
        return MuBackend(cfg)

    def test_invokes_mu_with_correct_argv(self, tmp_path):
        """Argv must include muhome, find, --format=json, sort/limit/scope."""
        backend = self._backend(tmp_path)
        muhome = backend.muhome
        account_cfg = _make_account_cfg("/tmp/foo/director-rivermill-au")

        captured: Dict[str, Any] = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="[]", stderr=""
            )

        with patch("mailroom.local_cache.subprocess.run", side_effect=fake_run):
            backend.search(account_cfg, "from:alice", limit=10)

        argv = captured["argv"]
        assert argv[0] == "mu"
        assert "find" in argv
        # --muhome is a subcommand flag and must follow ``find`` in the
        # argv, not precede it; mu's outer driver rejects it otherwise.
        assert argv.index(f"--muhome={muhome}") > argv.index("find")
        assert "--format=json" in argv
        assert "--maxnum" in argv
        assert "10" in argv
        assert "--sortfield" in argv
        assert "date" in argv
        assert "--reverse" in argv
        # The scoped query must AND the translated query with the maildir.
        assert argv[-1] == "(from:alice) AND maildir:/director-rivermill-au/"

    def test_parses_mu_json_output(self, tmp_path):
        """A single mu json record round-trips into the mailroom result shape."""
        backend = self._backend(tmp_path)
        account_cfg = _make_account_cfg("/var/local/mail/director-rivermill-au")
        sample = [
            {
                ":path": "/var/local/mail/director-rivermill-au/cur/123",
                "size": 174217,
                ":from": [{":email": "a@b.com", ":name": "Alice"}],
                ":to": [{":email": "c@d.com"}],
                ":subject": "Hi",
                ":date-unix": 1700000000,
                ":flags": ["seen", "attach"],
                ":message-id": "<m@x>",
                ":maildir": "/director-rivermill-au",
            }
        ]

        with patch(
            "mailroom.local_cache.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(sample),
                stderr="",
            ),
        ):
            results = backend.search(account_cfg, "from:alice", limit=5)

        assert len(results) == 1
        rec = results[0]
        assert rec["message_id"] == "<m@x>"
        assert rec["path"] == "/var/local/mail/director-rivermill-au/cur/123"
        assert rec["folder"] == "INBOX"
        assert rec["from"] == "Alice <a@b.com>"
        assert rec["to"] == ["c@d.com"]
        assert rec["subject"] == "Hi"
        assert rec["date"] == "2023-11-14T22:13:20+00:00"
        assert rec["flags"] == ["seen", "attach"]
        assert rec["has_attachments"] is True

    def test_exit_code_2_returns_empty(self, tmp_path):
        """mu's exit code 2 (no matches) is not an error — return []."""
        backend = self._backend(tmp_path)
        account_cfg = _make_account_cfg()

        with patch(
            "mailroom.local_cache.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=2, stdout="", stderr="no matches\n"
            ),
        ):
            results = backend.search(account_cfg, "from:nobody", limit=10)

        assert results == []

    def test_timeout_raises_mufailure(self, tmp_path):
        """A subprocess timeout becomes a MuFailure for the caller to fall back."""
        backend = self._backend(tmp_path)
        account_cfg = _make_account_cfg()

        def boom(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=30)

        with patch("mailroom.local_cache.subprocess.run", side_effect=boom):
            with pytest.raises(MuFailure, match="timed out"):
                backend.search(account_cfg, "from:alice", limit=10)

    def test_nonzero_exit_other_than_2_raises(self, tmp_path):
        """Any non-zero exit other than 2 is a real failure."""
        backend = self._backend(tmp_path)
        account_cfg = _make_account_cfg()

        with patch(
            "mailroom.local_cache.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="permission denied"
            ),
        ):
            with pytest.raises(MuFailure, match="exited 1"):
                backend.search(account_cfg, "from:alice", limit=10)

    def test_malformed_json_raises_mufailure(self, tmp_path):
        """Garbage stdout becomes a MuFailure rather than a JSONDecodeError."""
        backend = self._backend(tmp_path)
        account_cfg = _make_account_cfg()

        with patch(
            "mailroom.local_cache.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="not json", stderr=""
            ),
        ):
            with pytest.raises(MuFailure, match="decode"):
                backend.search(account_cfg, "from:alice", limit=10)

    def test_imap_prefix_raises_untranslatable(self, tmp_path):
        """An imap: token in the query surfaces UntranslatableQuery to the
        caller (re-raised from parse_query_to_mu)."""
        backend = self._backend(tmp_path)
        account_cfg = _make_account_cfg()

        # subprocess.run should never be called for an untranslatable query.
        with patch("mailroom.local_cache.subprocess.run") as mock_run:
            with pytest.raises(UntranslatableQuery):
                backend.search(account_cfg, "imap:UNSEEN", limit=10)
            mock_run.assert_not_called()

    def test_no_maildir_raises_value_error(self, tmp_path):
        """An account without maildir cannot be scoped; ValueError protects us."""
        backend = self._backend(tmp_path)
        imap = ImapConfig(
            host="imap.example.com",
            port=993,
            username="x@example.com",
            password="password",
            use_ssl=True,
        )
        account_cfg = AccountConfig(imap=imap, maildir=None)

        with pytest.raises(ValueError, match="maildir"):
            backend.search(account_cfg, "from:alice", limit=10)
