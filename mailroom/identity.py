"""Identity resolution: which From identity to use, and which SMTP route it takes.

Resolution rules are documented in examples/config.sample.toml. This module
turns a parsed ``AccountConfig`` plus context (an explicit From, a parent
email being replied to, etc.) into the concrete ``Identity`` and
``SmtpConfig`` that the SMTP transport will use.

Two failure modes are exposed as exceptions so the CLI can convert them into
clean exit-1 errors rather than tracebacks:

    IdentityNotFound: the explicit From address does not match any identity
        configured for the selected account.
    SmtpUnresolved: the resolved identity has no SMTP route (no
        identity.smtp, no account.default_smtp, and not a single ``[smtp.*]``
        block to fall back to).
"""

from dataclasses import replace
from typing import Any, Dict, List, Optional

from mailroom.config import AccountConfig, Identity, SmtpConfig


class IdentityNotFound(LookupError):
    """An explicit From address does not match any configured identity.

    Raised when ``--from EMAIL`` (compose/reply) or the From header of a draft
    being sent (send-draft) does not match any of the account's identities.
    The unmatched address and the list of available addresses are stored on
    the exception so the CLI can render them.
    """

    def __init__(self, from_addr: str, available: List[str]):
        self.from_addr = from_addr
        self.available = available
        super().__init__(
            f"From address '{from_addr}' is not configured for this account. "
            f"Configured identities: {available}"
        )


class SmtpUnresolved(LookupError):
    """An identity could not be paired with an SMTP block.

    Raised when none of the resolution rules (identity.smtp,
    account.default_smtp, lone-smtp fallback) produces an SMTP block for the
    identity at hand.
    """

    def __init__(self, account_name: str, identity_addr: str, available: List[str]):
        self.account_name = account_name
        self.identity_addr = identity_addr
        self.available = available
        super().__init__(
            f"Cannot resolve SMTP for identity '{identity_addr}' on account "
            f"'{account_name}'. Set 'smtp' on the identity, set 'default_smtp' "
            f"on the account, or define a single [smtp.*] block. "
            f"Available SMTP blocks: {available}"
        )


def list_identities(account: AccountConfig) -> List[Identity]:
    """Return the account's identities, synthesising one if none are configured.

    The synthesised identity has ``address = account.imap.username`` and an
    empty display name. Mailroom uses this for the bare-address From case
    where the user has not asked for send-as semantics.
    """
    if account.identities:
        return list(account.identities)
    return [Identity(address=account.imap.username, name="")]


def resolve_identity_for_send(
    account: AccountConfig, from_addr: Optional[str] = None
) -> Identity:
    """Pick the identity to send from.

    Args:
        account: The selected account.
        from_addr: Optional explicit From address (e.g. from ``--from``).

    Returns:
        The chosen ``Identity``. With *from_addr* None, returns the first
        identity (the synthesised one for accounts without ``[[identities]]``).

    Raises:
        IdentityNotFound: When *from_addr* is set but matches no identity.
    """
    identities = list_identities(account)
    if from_addr is None:
        return identities[0]
    target = from_addr.strip().lower()
    for ident in identities:
        if ident.address.lower() == target:
            return ident
    raise IdentityNotFound(
        from_addr=from_addr,
        available=[i.address for i in identities],
    )


def resolve_identity_for_reply(email_obj: Any, account: AccountConfig) -> Identity:
    """Pick the reply-from identity by matching the parent email's recipients.

    Walks ``email_obj.to`` then ``email_obj.cc``, returning the identity
    whose address matches any of those recipients. This is what tells
    mailroom "the user received this on alias X, so reply as X". Replaces
    the account-blind ``_find_reply_from_address`` in
    ``mailroom/smtp_client.py``.

    Args:
        email_obj: An ``Email`` model with ``.to`` and ``.cc`` lists of
            objects exposing ``.address``.
        account: The selected account.

    Returns:
        The matching ``Identity``, or the first identity if no recipient
        matches (the safe fallback so we never fail to compose a reply).
    """
    identities = list_identities(account)
    addr_to_identity = {i.address.lower(): i for i in identities}
    for recipient in (email_obj.to or []) + (email_obj.cc or []):
        addr = (getattr(recipient, "address", "") or "").lower()
        if addr and addr in addr_to_identity:
            return addr_to_identity[addr]
    return identities[0]


def resolve_smtp_for_identity(
    identity: Identity,
    account: AccountConfig,
    account_name: str,
    smtp_blocks: Dict[str, SmtpConfig],
) -> SmtpConfig:
    """Resolve the SMTP block for an identity, applying credential inheritance.

    Resolution order:
        1. ``identity.smtp`` if set.
        2. ``account.default_smtp`` if set.
        3. The lone ``[smtp.*]`` block when exactly one is defined.

    When the resolved SMTP block is a template (no username/password), this
    function returns a copy with credentials filled from the account's IMAP
    config. Concrete SMTP blocks (with their own creds) pass through
    unchanged.

    Raises:
        SmtpUnresolved: When none of the rules match.
    """
    name: Optional[str]
    if identity.smtp:
        name = identity.smtp
    elif account.default_smtp:
        name = account.default_smtp
    elif len(smtp_blocks) == 1:
        name = next(iter(smtp_blocks))
    else:
        raise SmtpUnresolved(
            account_name=account_name,
            identity_addr=identity.address,
            available=sorted(smtp_blocks),
        )
    smtp = smtp_blocks[name]
    if smtp.username and smtp.password:
        return smtp
    return replace(
        smtp,
        username=smtp.username or account.imap.username,
        password=smtp.password or account.imap.password,
    )
