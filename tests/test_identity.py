"""Tests for identity and SMTP resolution.

Covers the rules documented in examples/config.sample.toml: synthesised
identity for accounts without [[identities]], explicit --from matching, the
hard-error path for unknown From (the AI-safety win), and the SMTP
resolution chain (identity.smtp -> account.default_smtp -> lone smtp).
"""

import pytest

from mailroom.config import AccountConfig, Identity, ImapConfig, SmtpConfig
from mailroom.identity import (
    IdentityNotFound,
    SmtpUnresolved,
    list_identities,
    resolve_identity_for_reply,
    resolve_identity_for_send,
    resolve_smtp_for_identity,
)


def _imap() -> ImapConfig:
    return ImapConfig(
        host="imap.gmail.com",
        port=993,
        username="login@gmail.com",
        password="p",
    )


class _RecipientStub:
    """Minimal stand-in for an EmailAddress in unit tests."""

    def __init__(self, address: str):
        self.address = address


class _EmailStub:
    """Minimal stand-in for the Email model with .to/.cc lists."""

    def __init__(self, to=None, cc=None):
        self.to = [_RecipientStub(a) for a in (to or [])]
        self.cc = [_RecipientStub(a) for a in (cc or [])]


class TestListIdentities:
    def test_synthesised_when_no_identities(self):
        acct = AccountConfig(imap=_imap())
        ids = list_identities(acct)
        assert len(ids) == 1
        assert ids[0].address == "login@gmail.com"
        assert ids[0].name == ""

    def test_explicit_identities_passed_through(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[
                Identity(address="a@x.com"),
                Identity(address="b@x.com"),
            ],
        )
        ids = list_identities(acct)
        assert [i.address for i in ids] == ["a@x.com", "b@x.com"]


class TestResolveIdentityForSend:
    def test_default_first_identity(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[
                Identity(address="primary@x.com"),
                Identity(address="other@x.com"),
            ],
        )
        ident = resolve_identity_for_send(acct)
        assert ident.address == "primary@x.com"

    def test_explicit_from_match(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[
                Identity(address="primary@x.com"),
                Identity(address="other@x.com"),
            ],
        )
        ident = resolve_identity_for_send(acct, from_addr="other@x.com")
        assert ident.address == "other@x.com"

    def test_explicit_from_case_insensitive(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[Identity(address="alice@example.com")],
        )
        ident = resolve_identity_for_send(acct, from_addr="ALICE@EXAMPLE.COM")
        assert ident.address == "alice@example.com"

    def test_unknown_from_raises(self):
        """The AI-safety hard error: unrecognised From cannot fall through."""
        acct = AccountConfig(
            imap=_imap(),
            identities=[Identity(address="a@x.com"), Identity(address="b@x.com")],
        )
        with pytest.raises(IdentityNotFound) as excinfo:
            resolve_identity_for_send(acct, from_addr="impostor@x.com")
        assert excinfo.value.from_addr == "impostor@x.com"
        assert excinfo.value.available == ["a@x.com", "b@x.com"]

    def test_unknown_from_with_synthesised_identity(self):
        """Even synthesised-only accounts must reject unknown From."""
        acct = AccountConfig(imap=_imap())
        with pytest.raises(IdentityNotFound):
            resolve_identity_for_send(acct, from_addr="impostor@x.com")


class TestResolveIdentityForReply:
    def test_matches_to(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[
                Identity(address="a@x.com"),
                Identity(address="b@x.com"),
            ],
        )
        parent = _EmailStub(to=["b@x.com"], cc=[])
        assert resolve_identity_for_reply(parent, acct).address == "b@x.com"

    def test_matches_cc(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[
                Identity(address="a@x.com"),
                Identity(address="b@x.com"),
            ],
        )
        parent = _EmailStub(to=["other@x.com"], cc=["a@x.com"])
        assert resolve_identity_for_reply(parent, acct).address == "a@x.com"

    def test_falls_back_to_first(self):
        acct = AccountConfig(
            imap=_imap(),
            identities=[
                Identity(address="a@x.com"),
                Identity(address="b@x.com"),
            ],
        )
        parent = _EmailStub(to=["unrelated@x.com"])
        # No match -> first identity (safe fallback so reply never fails to compose).
        assert resolve_identity_for_reply(parent, acct).address == "a@x.com"

    def test_uses_synthesised_when_no_identities(self):
        acct = AccountConfig(imap=_imap())
        parent = _EmailStub(to=["login@gmail.com"])
        assert resolve_identity_for_reply(parent, acct).address == "login@gmail.com"


class TestResolveSmtpForIdentity:
    def test_identity_smtp_wins(self):
        smtps = {
            "gmail": SmtpConfig(host="smtp.gmail.com"),
            "ses": SmtpConfig(
                host="email-smtp.example.com",
                username="AKIA",
                password="x",
            ),
        }
        acct = AccountConfig(imap=_imap(), default_smtp="gmail")
        ident = Identity(address="a@x.com", smtp="ses")
        smtp = resolve_smtp_for_identity(ident, acct, "acct", smtps)
        assert smtp.host == "email-smtp.example.com"
        assert smtp.username == "AKIA"  # concrete creds preserved

    def test_account_default_smtp_when_identity_omits(self):
        smtps = {
            "gmail": SmtpConfig(host="smtp.gmail.com"),
            "ses": SmtpConfig(host="email-smtp.example.com"),
        }
        acct = AccountConfig(imap=_imap(), default_smtp="gmail")
        ident = Identity(address="a@x.com")
        smtp = resolve_smtp_for_identity(ident, acct, "acct", smtps)
        assert smtp.host == "smtp.gmail.com"

    def test_lone_smtp_fallback(self):
        smtps = {"gmail": SmtpConfig(host="smtp.gmail.com")}
        acct = AccountConfig(imap=_imap())
        ident = Identity(address="a@x.com")
        smtp = resolve_smtp_for_identity(ident, acct, "acct", smtps)
        assert smtp.host == "smtp.gmail.com"

    def test_template_inherits_creds_from_account(self):
        smtps = {"gmail": SmtpConfig(host="smtp.gmail.com")}
        acct = AccountConfig(imap=_imap())  # imap has username login@gmail.com / pwd p
        smtp = resolve_smtp_for_identity(
            Identity(address="x@y.com"), acct, "acct", smtps
        )
        assert smtp.username == "login@gmail.com"
        assert smtp.password == "p"

    def test_concrete_smtp_preserves_its_own_creds(self):
        smtps = {
            "ses": SmtpConfig(
                host="email-smtp.example.com",
                username="AKIA",
                password="ses-secret",
            )
        }
        acct = AccountConfig(imap=_imap())
        smtp = resolve_smtp_for_identity(
            Identity(address="x@y.com"), acct, "acct", smtps
        )
        assert smtp.username == "AKIA"
        assert smtp.password == "ses-secret"  # account creds NOT inherited

    def test_no_resolution_path_raises(self):
        smtps = {
            "a": SmtpConfig(host="smtp.a.com"),
            "b": SmtpConfig(host="smtp.b.com"),
        }
        acct = AccountConfig(imap=_imap())  # no default_smtp
        with pytest.raises(SmtpUnresolved) as excinfo:
            resolve_smtp_for_identity(
                Identity(address="x@y.com"), acct, "myacct", smtps
            )
        assert excinfo.value.account_name == "myacct"
        assert sorted(excinfo.value.available) == ["a", "b"]
