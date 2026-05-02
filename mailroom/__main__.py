"""Mailroom — email toolkit for AI assistants and command-line scripting.

All CLI commands are subcommands of `mailroom`. The `mcp` subcommand starts
the MCP server; every other subcommand operates directly via IMAP without
importing the mcp package.
"""

import json
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import typer

from mailroom import __version__
from mailroom.config import (
    AccountConfig,
    MultiAccountConfig,
    SmtpConfig,
    load_config,
    load_config_with_warnings,
)
from mailroom.imap_client import ImapClient
from mailroom.models import extract_links_batch

if TYPE_CHECKING:
    from mailroom.local_cache import MuBackend


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mailroom {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="mailroom",
    help=(
        "Email toolkit for AI assistants and command-line scripting.\n\n"
        "Exit codes for data-returning commands (search, attachments, links): "
        "0 on success with results, 1 on success with zero results. "
        "This makes shell idioms work:\n\n"
        "  mailroom search 'from:alice@x' || mailroom search 'alice'\n"
        "  ( mailroom -a A search Q ; mailroom -a B search Q ) | jq -s add\n\n"
        "Multi-account search (-a A -a B or --all-accounts) makes the second "
        "idiom unnecessary in most cases."
    ),
    no_args_is_help=True,
)

# Module-level state set by the --config callback.
_config_path: Optional[str] = None
_account_names: List[str] = []
_all_accounts: bool = False

# Process-wide MuBackend instance, lazily built when the configured
# accounts opt into the local cache.  Shared across ImapClient instances
# so the muhome discovery (mu info store) runs at most once.
_mu_backend_singleton: Optional["MuBackend"] = None

logger = logging.getLogger(__name__)


def _get_mu_backend(cfg: MultiAccountConfig) -> Optional["MuBackend"]:
    """Return the shared MuBackend, building it on first use.

    Args:
        cfg: The loaded multi-account configuration.

    Returns:
        A ``MuBackend`` instance when ``cfg.local_cache`` is present;
        ``None`` when the local cache is not configured.
    """
    global _mu_backend_singleton
    if cfg.local_cache is None:
        return None
    if _mu_backend_singleton is None:
        from mailroom.local_cache import MuBackend

        _mu_backend_singleton = MuBackend(cfg.local_cache)
    return _mu_backend_singleton


@app.callback()
def _global_options(
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to TOML configuration file.",
        envvar="MAILROOM_CONFIG",
    ),
    accounts: List[str] = typer.Option(
        [],
        "--account",
        "-a",
        help=(
            "Account name (for multi-account configs). Uses default if omitted. "
            "Pass multiple times with 'search' to query several accounts."
        ),
    ),
    all_accounts: bool = typer.Option(
        False,
        "--all-accounts",
        "-A",
        help="Search every configured account. Only meaningful for 'search'.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging."
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    global _config_path, _account_names, _all_accounts
    _config_path = config
    _account_names = list(accounts)
    _all_accounts = all_accounts
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _resolve_accounts() -> List[str]:
    """Resolve module-level account flags to a deduplicated list of names.

    Returns the list of account names to search, validated against the
    config. Errors hard if a name is unknown or if ``--all-accounts`` is
    set with no accounts configured. Emits a stderr note when
    ``--all-accounts`` overrides explicit ``-a`` flags.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if _all_accounts:
        if not cfg.accounts:
            typer.echo("Error: no accounts configured", err=True)
            raise typer.Exit(1)
        if _account_names:
            typer.echo("note: --all-accounts overrides --account values", err=True)
        return list(cfg.accounts.keys())
    if not _account_names:
        return [cfg.default_account]
    seen: set = set()
    resolved: List[str] = []
    for name in _account_names:
        if name not in cfg.accounts:
            available = list(cfg.accounts.keys())
            typer.echo(
                f"Error: unknown account '{name}'. Available: {available}", err=True
            )
            raise typer.Exit(1)
        if name not in seen:
            resolved.append(name)
            seen.add(name)
    return resolved


def _make_client(account_override: Optional[str] = None) -> ImapClient:
    """Create and connect an ImapClient.

    Args:
        account_override: If given, use this account name instead of the
            global ``--account`` flag.

    Raises:
        typer.Exit: On config error, unknown account, multi-account misuse,
            or IMAP connect failure.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if account_override is None:
        if _all_accounts or len(_account_names) > 1:
            typer.echo(
                "Error: this command does not support multi-account flags "
                "(--all-accounts or repeated --account)",
                err=True,
            )
            raise typer.Exit(1)
        single = _account_names[0] if _account_names else None
        name = single or cfg.default_account
    else:
        name = account_override
    if name not in cfg.accounts:
        available = list(cfg.accounts.keys())
        typer.echo(f"Error: unknown account '{name}'. Available: {available}", err=True)
        raise typer.Exit(1)
    if not _account_names and account_override is None:
        typer.echo(f"Using account '{name}'", err=True)
    acct = cfg.accounts[name]
    client = ImapClient(
        acct.imap,
        acct.allowed_folders,
        local_cache=_get_mu_backend(cfg),
        account_cfg=acct,
    )
    try:
        client.connect()
    except Exception as exc:
        typer.echo(f"Error: failed to connect to IMAP server: {exc}", err=True)
        raise typer.Exit(1)
    return client


def _make_client_soft(name: str) -> Optional[ImapClient]:
    """Connect for one account, returning None on failure (with stderr warning).

    Used by the multi-account search loop so one unreachable account does
    not abort the whole command.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if name not in cfg.accounts:
        available = list(cfg.accounts.keys())
        typer.echo(f"Error: unknown account '{name}'. Available: {available}", err=True)
        raise typer.Exit(1)
    acct = cfg.accounts[name]
    client = ImapClient(
        acct.imap,
        acct.allowed_folders,
        local_cache=_get_mu_backend(cfg),
        account_cfg=acct,
    )
    try:
        client.connect()
    except Exception as exc:
        typer.echo(f"warning: connect failed for '{name}': {exc}", err=True)
        return None
    return client


def _resolve_single_account_name() -> str:
    """Return the single account name for commands that don't support multi-account.

    Raises:
        typer.Exit: On config error, unknown account, or multi-account flags.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if _all_accounts or len(_account_names) > 1:
        typer.echo(
            "Error: this command does not support multi-account flags "
            "(--all-accounts or repeated --account)",
            err=True,
        )
        raise typer.Exit(1)
    name = _account_names[0] if _account_names else cfg.default_account
    if name not in cfg.accounts:
        typer.echo(f"Error: unknown account '{name}'.", err=True)
        raise typer.Exit(1)
    return name


def _empty_result_for_subcmd(subcmd: str) -> Dict[str, Any]:
    """Return an appropriate placeholder result for a failed account connection."""
    if subcmd == "search":
        return _empty_search_result()
    return {"error": "connection failed"}


def _build_op_key(subcmd: str, **kwargs: Any) -> str:
    """Build a canonical operation key string from a subcommand and its arguments.

    Non-default arguments are included; defaults are omitted so the key
    remains compact. The key for a single-command run is used as the outer
    JSON key, matching the format expected by the batch subcommand.
    """
    parts = [subcmd]
    if subcmd == "search":
        folder = kwargs.get("folder")
        limit = kwargs.get("limit", 10)
        query = kwargs.get("query", "")
        if folder:
            parts += ["-f", folder]
        if limit != 10:
            parts += ["--limit", str(limit)]
        if query:
            parts.append(query)
    elif subcmd == "read":
        folder = kwargs.get("folder", "")
        uid = kwargs.get("uid", 0)
        parts += ["-f", folder, "--uid", str(uid)]
    return " ".join(parts)


def _fetch_email_result(client: ImapClient, folder: str, uid: int) -> Dict[str, Any]:
    """Fetch one email and return its JSON representation, or ``{"error": ...}``.

    Extracted so both the standalone ``read`` command and the batch executor
    share identical output structure.
    """
    email_obj = client.fetch_email(uid, folder)
    if not email_obj:
        return {"error": f"Email UID {uid} not found in {folder}"}
    result: Dict[str, Any] = {
        "uid": uid,
        "folder": folder,
        "from": str(email_obj.from_),
        "to": [str(to) for to in email_obj.to],
        "subject": email_obj.subject,
        "date": (email_obj.date.isoformat() if email_obj.date else None),
        "flags": email_obj.flags,
        "message_id": email_obj.message_id,
        "content_type": ("text/html" if email_obj.content.html else "text/plain"),
        "body": (
            str(email_obj.content.html)
            if email_obj.content.html
            else str(email_obj.content.text) if email_obj.content.text else None
        ),
    }
    if email_obj.in_reply_to:
        result["in_reply_to"] = email_obj.in_reply_to
    if email_obj.references:
        result["references"] = list(email_obj.references)
    if email_obj.cc:
        result["cc"] = [str(cc) for cc in email_obj.cc]
    if email_obj.attachments:
        result["attachments"] = [
            {
                "index": i,
                "filename": att.filename,
                "size": att.size,
                "content_type": att.content_type,
            }
            for i, att in enumerate(email_obj.attachments)
        ]
    return result


def _run_op(client: ImapClient, subcmd: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one operation against an already-connected IMAP client.

    Args:
        client: Connected ImapClient for the current account.
        subcmd: Subcommand name (``"search"`` or ``"read"``).
        kwargs: Parsed arguments for the subcommand.

    Returns:
        Per-account result dict suitable for inclusion in the batch output.
    """
    if subcmd == "search":
        return client.search_emails(
            kwargs["query"],
            folder=kwargs.get("folder"),
            limit=kwargs.get("limit", 10),
        )
    if subcmd == "read":
        return _fetch_email_result(client, kwargs["folder"], kwargs["uid"])
    return {"error": f"unknown subcommand '{subcmd}'"}


def _execute_batch(
    operations: List[Tuple[str, str, Dict[str, Any]]],
    names: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Execute multiple operations account-first over one IMAP connection per account.

    Opens one connection per account, runs every operation for that account,
    then closes before moving to the next. Accounts are processed sequentially
    to avoid server-side throttling.

    Args:
        operations: List of ``(op_key, subcmd, kwargs)`` tuples. ``op_key`` is
            echoed back as the outer JSON key.
        names: Account names to query, processed in order.

    Returns:
        ``{op_key: {account_name: result_dict}}`` — the batch JSON shape.

    Raises:
        ValueError: Re-raised from ``_run_op`` so the caller can map it to
            exit code 2 (invalid query syntax).
    """
    batch: Dict[str, Dict[str, Any]] = {key: {} for key, _, _ in operations}
    for name in names:
        client = _make_client_soft(name)
        if client is None:
            for key, subcmd, _ in operations:
                batch[key][name] = _empty_result_for_subcmd(subcmd)
            continue
        try:
            for key, subcmd, kwargs in operations:
                try:
                    batch[key][name] = _run_op(client, subcmd, kwargs)
                except ValueError:
                    raise
                except Exception as exc:
                    batch[key][name] = {"error": str(exc)}
        finally:
            client.disconnect()
    return batch


def _parse_search_args(tokens: List[str]) -> Dict[str, Any]:
    """Parse tokenised search arguments into a kwargs dict."""
    folder: Optional[str] = None
    limit = 10
    query_parts: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-f", "--folder") and i + 1 < len(tokens):
            folder = tokens[i + 1]
            i += 2
        elif tok.startswith("--folder="):
            folder = tok.split("=", 1)[1]
            i += 1
        elif tok in ("-n", "--limit") and i + 1 < len(tokens):
            limit = int(tokens[i + 1])
            i += 2
        elif tok.startswith("--limit="):
            limit = int(tok.split("=", 1)[1])
            i += 1
        elif not tok.startswith("-"):
            query_parts.append(tok)
            i += 1
        else:
            i += 1
    return {"query": " ".join(query_parts), "folder": folder, "limit": limit}


def _parse_read_args(tokens: List[str]) -> Dict[str, Any]:
    """Parse tokenised read arguments into a kwargs dict."""
    folder: Optional[str] = None
    uid: Optional[int] = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-f", "--folder") and i + 1 < len(tokens):
            folder = tokens[i + 1]
            i += 2
        elif tok.startswith("--folder="):
            folder = tok.split("=", 1)[1]
            i += 1
        elif tok in ("-u", "--uid") and i + 1 < len(tokens):
            uid = int(tokens[i + 1])
            i += 2
        elif tok.startswith("--uid="):
            uid = int(tok.split("=", 1)[1])
            i += 1
        else:
            i += 1
    if folder is None or uid is None:
        raise ValueError("read requires --folder (-f) and --uid (-u)")
    return {"folder": folder, "uid": uid}


def _parse_op_string(op_string: str) -> Tuple[str, str, Dict[str, Any]]:
    """Parse an operation string into ``(op_key, subcmd, kwargs)``.

    The ``op_key`` is the original string echoed back unchanged so the caller
    can correlate input operations with output results.

    Args:
        op_string: A subcommand string such as ``"search from:x"`` or
            ``"read -f INBOX --uid 42"``.

    Returns:
        ``(op_string, subcmd, kwargs)`` ready for ``_execute_batch``.

    Raises:
        ValueError: If the string is empty or names an unsupported subcommand.
    """
    tokens = shlex.split(op_string)
    if not tokens:
        raise ValueError("empty operation string")
    subcmd = tokens[0]
    args = tokens[1:]
    if subcmd == "search":
        kwargs: Dict[str, Any] = _parse_search_args(args)
    elif subcmd == "read":
        kwargs = _parse_read_args(args)
    else:
        raise ValueError(f"unsupported batch subcommand '{subcmd}'")
    return op_string, subcmd, kwargs


def _out(data: object) -> None:
    """Print data as JSON to stdout."""
    if isinstance(data, str):
        # Many tools return a JSON string; pass it through as-is.
        print(data)
    else:
        print(json.dumps(data, indent=2, default=str))


def _email_only(s: str) -> str:
    """Strip display name from ``Display Name <addr@host>``."""
    if "<" in s and ">" in s:
        return s.split("<", 1)[1].rsplit(">", 1)[0]
    return s


def _load_cfg_or_exit() -> MultiAccountConfig:
    """Load config or exit 1 with the usual error formatting."""
    try:
        return load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


def _perform_send(
    client: ImapClient,
    mime_message: Any,
    account: AccountConfig,
    account_name: str,
    smtp_blocks: Dict[str, SmtpConfig],
    identity: Any,
    save_sent_override: Optional[bool],
    sent_folder_override: Optional[str],
) -> Dict[str, Any]:
    """Resolve SMTP, transmit, and FCC. Used by compose/reply --send and send-draft.

    Args:
        client: Connected ImapClient (used for FCC and folder discovery).
        mime_message: Built MIME message ready to serialise.
        account: Selected account's config.
        account_name: Name of the selected account (for error messages).
        smtp_blocks: Top-level ``[smtp.*]`` map from the loaded config.
        identity: Resolved ``Identity`` driving From, SMTP route, sent_folder.
        save_sent_override: When set, overrides the SMTP block's
            ``save_sent`` resolution. None means "use the configured default".
        sent_folder_override: When set, overrides the identity's sent_folder
            (and the auto-discovered Sent folder). None means default.

    Returns:
        The standard send-result JSON shape (status, identity, message_ids,
        smtp_response, accepted_recipients, fcc_folder, fcc_uid).
    """
    from mailroom.identity import resolve_smtp_for_identity, SmtpUnresolved
    from mailroom.smtp_transport import send as smtp_send

    try:
        smtp = resolve_smtp_for_identity(identity, account, account_name, smtp_blocks)
    except SmtpUnresolved as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    try:
        fcc_bytes, send_result = smtp_send(mime_message, smtp)
    except Exception as exc:
        typer.echo(f"Error: SMTP send failed: {exc}", err=True)
        raise typer.Exit(1)

    do_fcc = (
        save_sent_override
        if save_sent_override is not None
        else smtp.resolve_save_sent()
    )
    fcc_folder: Optional[str] = None
    fcc_uid: Optional[int] = None
    if do_fcc:
        target = (
            sent_folder_override or identity.sent_folder or client._get_sent_folder()
        )
        fcc_folder = target
        try:
            fcc_uid = client.append_raw(target, fcc_bytes, flags=(r"\Seen",))
        except Exception as exc:
            typer.echo(f"warning: FCC to {target} failed: {exc}", err=True)

    return {
        "status": "success",
        "identity": identity.address,
        **send_result,
        "fcc_folder": fcc_folder,
        "fcc_uid": fcc_uid,
    }


def _print_eager_warnings_if_relevant() -> None:
    """Print config warnings to stderr when user is 'checking in' on mailroom.

    Detects no-args, ``--help``, or ``-h`` in argv (after stripping known
    global options like ``--config``/``--account``). Surfaces warnings before
    typer takes over so the user sees them above the help text.

    Safe-fails: any error in load (missing config, bad TOML) is swallowed
    here; the user will see the actual error from typer's normal flow if
    they then run a real command.
    """
    args = sys.argv[1:]
    globals_with_value = {"--config", "-c", "--account", "-a"}

    config_path: Optional[str] = None
    first_real: Optional[str] = None
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            break
        if tok in globals_with_value and i + 1 < len(args):
            if tok in ("--config", "-c"):
                config_path = args[i + 1]
            i += 2
            continue
        if "=" in tok and tok.split("=", 1)[0] in globals_with_value:
            if tok.startswith("--config="):
                config_path = tok.split("=", 1)[1]
            i += 1
            continue
        first_real = tok
        break

    is_help_or_noargs = first_real is None or first_real in ("--help", "-h")
    if not is_help_or_noargs:
        return

    try:
        _, warnings = load_config_with_warnings(config_path)
    except Exception:
        return
    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)


def _empty_search_result() -> Dict[str, Any]:
    """Return the wrapped result shape for an account that returned nothing.

    Used when client construction or connection fails, so the per-account
    output stays uniform with successful calls.
    """
    return {
        "results": [],
        "provenance": {
            "source": "remote",
            "indexed_at": None,
            "fell_back_reason": None,
        },
    }


def _format_provenance_line(provenance: Dict[str, Any]) -> str:
    """Format a one-line provenance summary for the text output mode."""
    source = provenance.get("source", "remote")
    indexed_at = provenance.get("indexed_at") or "-"
    reason = provenance.get("fell_back_reason")
    parts = [f"source={source}", f"indexed_at={indexed_at}"]
    if reason:
        parts.append(f"fell_back={reason}")
    return "# " + " ".join(parts)


def _format_batch_text(batch: Dict[str, Dict[str, Any]]) -> str:
    """Render a batch result as multi-line, prompt-friendly text."""
    sections: List[str] = []
    for op_key, accounts in batch.items():
        lines: List[str] = [f"=== {op_key} ==="]
        for account, value in accounts.items():
            hits: List[Dict[str, Any]] = value.get("results", []) or []
            provenance = value.get("provenance") or {}
            lines.append(f"== {account} ==")
            if "error" in value and not hits:
                lines.append(f"(error: {value['error']})")
            else:
                lines.append(_format_provenance_line(provenance))
                if not hits:
                    lines.append("(no results)")
                else:
                    for r in hits:
                        date = str(r.get("date", ""))[:10]
                        subject = r.get("subject", "")
                        from_ = r.get("from", "")
                        to_list = r.get("to") or [""]
                        to = to_list[0]
                        folder = r.get("folder", "")
                        message_id = r.get("message_id", "")
                        lines.append(f"{date}  {subject}")
                        lines.append(f"            from: {from_}")
                        lines.append(f"            to:   {to}")
                        lines.append(f"            folder: {folder}")
                        if message_id:
                            lines.append(f"            id:     {message_id}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _format_batch_oneline(batch: Dict[str, Dict[str, Any]]) -> str:
    """Render a batch result as one tab-separated line per result.

    Columns: op_key, account, date, subject, from → to, message_id.
    """
    lines: List[str] = []
    for op_key, accounts in batch.items():
        for account, value in accounts.items():
            hits: List[Dict[str, Any]] = value.get("results", []) or []
            if not hits:
                lines.append(f"{op_key}\t{account}\t(no results)")
                continue
            for r in hits:
                date = str(r.get("date", ""))[:10]
                subject = r.get("subject", "")
                from_addr = _email_only(r.get("from", ""))
                to_list = r.get("to") or [""]
                to_addr = _email_only(to_list[0]) if to_list[0] else ""
                message_id = r.get("message_id", "")
                lines.append(
                    f"{op_key}\t{account}\t{date}\t{subject}"
                    f"\t{from_addr} → {to_addr}\t{message_id}"
                )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# list-accounts
# ---------------------------------------------------------------------------


@app.command("config-check")
def config_check() -> None:
    """Validate the configuration file without invoking IMAP or SMTP.

    Reports hard errors on invalid TOML or bad cross-references (typo'd
    default_smtp, identity smtp referencing an undefined block, duplicate
    addresses, etc.) and lists non-fatal warnings (send-disabled accounts,
    shared credential-less SMTP on non-Gmail hosts, no smtp blocks).

    Exit codes:
        0  config is valid (warnings may still be present on stderr)
        1  config is invalid (errors on stderr)
    """
    try:
        _, warnings = load_config_with_warnings(_config_path)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)
    path = _config_path or "~/.config/mailroom/config.toml"
    print(f"config-check: OK ({path})")
    if warnings:
        print(f"  ({len(warnings)} warning(s) above)")


@app.command("list-accounts")
def list_accounts() -> None:
    """List configured email accounts."""
    cfg = _load_cfg_or_exit()
    accounts = []
    for name, acct in cfg.accounts.items():
        accounts.append(
            {
                "name": name,
                "default": name == cfg.default_account,
                "user": acct.imap.username,
                "host": acct.imap.host,
            }
        )
    _out(accounts)
    for w in cfg.warnings:
        print(f"warn: {w}", file=sys.stderr)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status() -> None:
    """Show IMAP server status and configuration."""
    cfg = _load_cfg_or_exit()
    accounts_map: Dict[str, Any] = {}
    status_data: Dict[str, Any] = {
        "server": "Mailroom",
        "version": __version__,
        "default_account": cfg.default_account,
        "accounts": accounts_map,
        "smtp_blocks": sorted(cfg.smtp_blocks),
    }
    for name, acct in cfg.accounts.items():
        status_data["accounts"][name] = {
            "imap_host": acct.imap.host,
            "imap_port": acct.imap.port,
            "imap_user": acct.imap.username,
            "imap_ssl": acct.imap.use_ssl,
            "default_smtp": acct.default_smtp,
            "identities": (
                [i.address for i in acct.identities] if acct.identities else None
            ),
            "allowed_folders": (
                list(acct.allowed_folders) if acct.allowed_folders else "all"
            ),
        }
    _out(status_data)
    for w in cfg.warnings:
        print(f"warn: {w}", file=sys.stderr)


# ---------------------------------------------------------------------------
# folders (NEW)
# ---------------------------------------------------------------------------


@app.command("folders")
def folders() -> None:
    """List available email folders."""
    client = _make_client()
    try:
        folder_list = client.list_folders()
        _out(folder_list)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command("search")
def search(
    query: str = typer.Argument(
        "",
        help=(
            "Gmail-style search query. Examples: "
            "'from:alice subject:invoice', 'is:unread after:2025-03-01', "
            "'meeting notes' (bare words search text), "
            "'imap:OR TEXT foo SUBJECT bar' (raw IMAP)."
        ),
    ),
    query_opt: Optional[str] = typer.Option(
        None, "--query", "-q", help="Alias for the positional query (overrides if set)."
    ),
    folder: Optional[str] = typer.Option(
        None, "--folder", "-f", help="Folder to search (default: all)."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of results."),
    output_format: str = typer.Option(
        "json",
        "--format",
        "-F",
        help="Output format: json (default), text, or oneline.",
    ),
) -> None:
    """Search for emails.

    Output is a JSON object keyed first by operation string, then by account
    name. Each per-account value is ``{"results": [...], "provenance": {...}}``
    where ``provenance`` reports whether the result came from IMAP
    (``source: "remote"``) or from a local mu cache (``source: "local"``).
    ``--limit`` is applied per account.

    ``--format text`` renders multi-line, prompt-friendly output grouped by
    operation and account; ``--format oneline`` renders one tab-separated line
    per result (op_key, account, date, subject, from→to, message_id).

    Exit code: 0 on hits, 1 when every account returned zero results, so
    shell fallback chains work: ``mailroom search 'from:x' || mailroom
    search 'x'``.
    """
    effective = query_opt if query_opt is not None else query
    op_key = _build_op_key("search", query=effective, folder=folder, limit=limit)
    names = _resolve_accounts()
    try:
        batch = _execute_batch(
            [
                (
                    op_key,
                    "search",
                    {"query": effective, "folder": folder, "limit": limit},
                )
            ],
            names,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if output_format == "json":
        _out(batch)
    elif output_format == "text":
        typer.echo(_format_batch_text(batch))
    elif output_format == "oneline":
        typer.echo(_format_batch_oneline(batch))
    else:
        typer.echo(
            f"Error: unknown --format '{output_format}'. Use json, text, or oneline.",
            err=True,
        )
        raise typer.Exit(2)
    has_results = any(
        v.get("results")
        for per_account in batch.values()
        for v in per_account.values()
        if isinstance(v, dict)
    )
    if not has_results:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


@app.command("batch")
def batch_cmd(
    operations: Optional[List[str]] = typer.Argument(
        None,
        help=(
            "Operation strings to execute, e.g. "
            "'search from:x' 'read -f INBOX --uid 1'."
        ),
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        help="JSON file containing an array of operation strings.",
    ),
    output_format: str = typer.Option(
        "json",
        "--format",
        "-F",
        help="Output format: json (default), text, or oneline.",
    ),
) -> None:
    """Execute multiple operations in one IMAP connection per account.

    Supply operations as positional arguments, via ``--file``, or as a JSON
    array on stdin. Each operation is a subcommand string identical to what
    you would pass on the command line (e.g. ``"search from:x"``).

    All operations for a given account are executed over a single connection
    before moving to the next account, which avoids per-query reconnections
    and server-side throttling.

    Output JSON is keyed by operation string, then by account name — the same
    shape as a single-command run but with multiple outer keys.

    Exit code: 0 if any search operation returned results, 1 otherwise.
    """
    op_strings: List[str] = []
    if file is not None:
        try:
            op_strings = json.loads(file.read_text())
        except Exception as exc:
            typer.echo(f"Error reading --file: {exc}", err=True)
            raise typer.Exit(1)
    elif operations:
        op_strings = list(operations)
    elif not sys.stdin.isatty():
        try:
            op_strings = json.loads(sys.stdin.read())
        except Exception as exc:
            typer.echo(f"Error reading stdin: {exc}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo(
            "Error: provide operations as arguments, via --file, or on stdin.",
            err=True,
        )
        raise typer.Exit(1)

    parsed: List[Tuple[str, str, Dict[str, Any]]] = []
    for s in op_strings:
        try:
            parsed.append(_parse_op_string(s))
        except ValueError as exc:
            typer.echo(f"Error parsing operation {s!r}: {exc}", err=True)
            raise typer.Exit(1)

    names = _resolve_accounts()
    try:
        batch = _execute_batch(parsed, names)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)

    if output_format == "json":
        _out(batch)
    elif output_format == "text":
        typer.echo(_format_batch_text(batch))
    elif output_format == "oneline":
        typer.echo(_format_batch_oneline(batch))
    else:
        typer.echo(
            f"Error: unknown --format '{output_format}'. Use json, text, or oneline.",
            err=True,
        )
        raise typer.Exit(2)
    has_results = any(
        v.get("results")
        for per_account in batch.values()
        for v in per_account.values()
        if isinstance(v, dict)
    )
    if not has_results:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# read (NEW)
# ---------------------------------------------------------------------------


@app.command("read")
def read(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Read an email's content.

    Output is a JSON object keyed by operation string, then by account name,
    consistent with the batch output format.
    """
    name = _resolve_single_account_name()
    client = _make_client()
    try:
        result = _fetch_email_result(client, folder, uid)
        if "error" in result:
            typer.echo(result["error"], err=True)
            raise typer.Exit(1)
        op_key = _build_op_key("read", folder=folder, uid=uid)
        _out({op_key: {name: result}})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


@app.command("move")
def move(
    folder: str = typer.Option(..., "--folder", "-f", help="Source folder."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    target: str = typer.Option(..., "--target", "-t", help="Destination folder."),
) -> None:
    """Move an email to another folder."""
    client = _make_client()
    try:
        success = client.move_email(uid, folder, target)
        _out(
            {
                "success": success,
                "message": (
                    f"Moved from {folder} to {target}"
                    if success
                    else "Failed to move email"
                ),
            }
        )
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


@app.command("copy")
def copy_cmd(
    from_account: str = typer.Option(
        ..., "--from-account", help="Source account name."
    ),
    from_folder: str = typer.Option(..., "--from-folder", help="Source folder."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID in the source folder."),
    to_folder: str = typer.Option(
        "INBOX", "--to-folder", "-t", help="Destination folder."
    ),
    move_flag: bool = typer.Option(
        False, "--move", help="Delete from source after copy."
    ),
    preserve_flags: bool = typer.Option(
        False, "--preserve-flags", help="Copy original flags to destination."
    ),
) -> None:
    """Copy an email from one account into another.

    The global --account/-a selects the destination account.
    Fetches the raw RFC 822 message from the source and APPENDs it to the
    destination, preserving the message byte-for-byte and its original date.
    """
    from mailroom.imap_client import copy_email_between_accounts

    source = _make_client(account_override=from_account)
    dest = _make_client()
    try:
        result = copy_email_between_accounts(
            source,
            dest,
            uid,
            from_folder,
            to_folder=to_folder,
            move=move_flag,
            preserve_flags=preserve_flags,
        )
        if not result["success"]:
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)
        _out(
            {
                "success": True,
                "subject": result["subject"],
                "source": f"{from_account}/{from_folder}/{uid}",
                "destination": to_folder,
                "new_uid": result["new_uid"],
                "moved": result["moved"],
            }
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    finally:
        source.disconnect()
        dest.disconnect()


# ---------------------------------------------------------------------------
# mark-read / mark-unread
# ---------------------------------------------------------------------------


@app.command("mark-read")
def mark_read(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Mark an email as read."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Seen", True)
        _out({"success": success})
    finally:
        client.disconnect()


@app.command("mark-unread")
def mark_unread(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Mark an email as unread."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Seen", False)
        _out({"success": success})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# flag
# ---------------------------------------------------------------------------


@app.command("flag")
def flag(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    unflag: bool = typer.Option(
        False, "--unflag", help="Remove the flag instead of setting it."
    ),
) -> None:
    """Flag (star) an email, or unflag it with --unflag."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Flagged", not unflag)
        _out({"success": success, "flagged": not unflag})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@app.command("delete")
def delete(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Delete an email."""
    client = _make_client()
    try:
        success = client.delete_email(uid, folder)
        _out({"success": success})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------


@app.command("triage")
def triage(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    action: str = typer.Argument(
        ..., help="Action: move, read, unread, flag, unflag, delete."
    ),
    target_folder: Optional[str] = typer.Option(
        None, "--target-folder", "-t", help="Target folder (for move)."
    ),
    notes: Optional[str] = typer.Option(None, "--notes", help="Optional notes."),
) -> None:
    """Triage an email with a given action."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        try:
            message = client.process_email_action(
                uid, folder, action, target_folder=target_folder
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        _out({"message": message})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# attachments
# ---------------------------------------------------------------------------


@app.command("attachments")
def attachments(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """List attachments for an email.

    Exit code: 0 if at least one attachment is found, 1 if the email has
    none. Shell idiom: ``mailroom attachments -f INBOX -u 1 || echo none``.
    """
    client = _make_client()
    empty = False
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            _out({"error": f"Email UID {uid} not found in {folder}"})
            raise typer.Exit(1)
        result = []
        for i, att in enumerate(email_obj.attachments):
            entry = {
                "index": i,
                "filename": att.filename,
                "size": att.size,
                "content_type": att.content_type,
            }
            if att.content_id:
                entry["content_id"] = att.content_id
            result.append(entry)
        _out(result)
        empty = not result
    finally:
        client.disconnect()
    if empty:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


@app.command("save")
def save(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    identifier: str = typer.Option(
        ..., "--identifier", "-i", help="Attachment filename or numeric index."
    ),
    save_path: str = typer.Option(
        ..., "--save-path", "-o", help="Path to save the attachment."
    ),
) -> None:
    """Download an attachment from an email."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        result = email_obj.save_attachment(identifier, save_path)
        _out(result)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _export_raw(client: ImapClient, folder: str, uid: int, save_path: str) -> None:
    """Export raw RFC 822 bytes to *save_path* (or stdout if ``-``)."""
    fetched = client.fetch_raw(uid, folder)
    if not fetched:
        typer.echo(f"Email UID {uid} not found in {folder}", err=True)
        raise typer.Exit(1)

    raw_bytes = fetched["raw"]
    if save_path == "-":
        sys.stdout.buffer.write(raw_bytes)
        return

    dir_part = os.path.dirname(save_path)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    with open(save_path, "wb") as fh:
        fh.write(raw_bytes)
    _out(
        {
            "success": True,
            "save_path": save_path,
            "size": len(raw_bytes),
            "subject": fetched.get("subject"),
        }
    )


def _export_html(client: ImapClient, folder: str, uid: int, save_path: str) -> None:
    """Export HTML with embedded images to *save_path*."""
    email_obj = client.fetch_email(uid, folder)
    if not email_obj:
        typer.echo(f"Email UID {uid} not found in {folder}", err=True)
        raise typer.Exit(1)
    _out(email_obj.export_html_to_file(save_path))


@app.command("export")
def export(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    save_path: str = typer.Option(
        ...,
        "--save-path",
        "-o",
        help="Path to save to. Use '-' with --raw to stream raw RFC 822 to stdout.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Export the raw RFC 822 message bytes instead of HTML.",
    ),
) -> None:
    """Export email content to a standalone file.

    Default: HTML with embedded images.
    With --raw: the raw RFC 822 message as stored on the IMAP server.
    """
    client = _make_client()
    try:
        if raw:
            _export_raw(client, folder, uid, save_path)
        else:
            _export_html(client, folder, uid, save_path)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------


@app.command("links")
def links(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uids: List[int] = typer.Option(..., "--uid", "-u", help="One or more email UIDs."),
) -> None:
    """Extract all links from email HTML content."""
    client = _make_client()
    try:
        results = extract_links_batch(client.fetch_email, folder, uids)
        _out(results)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------


def _write_raw_output(mime_message: Any, output: str) -> None:
    """Serialise *mime_message* and write to *output* path (``-`` is stdout)."""
    if hasattr(mime_message, "as_bytes"):
        raw = mime_message.as_bytes()
    else:
        raw = mime_message.as_string().encode("utf-8")
    if output == "-":
        sys.stdout.buffer.write(raw)
        return
    dir_part = os.path.dirname(output)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    with open(output, "wb") as fh:
        fh.write(raw)
    typer.echo(f"Wrote {len(raw)} bytes to {output}", err=True)


@app.command("compose")
def compose(
    to: List[str] = typer.Option(
        ..., "--to", help="Recipient email address. Repeatable."
    ),
    body: str = typer.Option(..., "--body", "-b", help="Plain-text body."),
    subject: str = typer.Option("", "--subject", "-s", help="Subject line."),
    cc: Optional[List[str]] = typer.Option(None, "--cc", help="CC recipients."),
    bcc: Optional[List[str]] = typer.Option(
        None,
        "--bcc",
        help="BCC recipients (added to raw message; stripped by sending agents).",
    ),
    body_html: Optional[str] = typer.Option(
        None, "--body-html", help="HTML version of body."
    ),
    attach: Optional[List[str]] = typer.Option(
        None, "--attach", help="Path to a file to attach. Repeatable."
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for raw RFC 822 message. Use '-' for stdout (suitable for piping).",
    ),
    send_flag: bool = typer.Option(
        False,
        "--send",
        help="Transmit via SMTP instead of saving as a draft. Mutually exclusive with --output.",
    ),
    from_email: Optional[str] = typer.Option(
        None,
        "--from",
        help="Identity address to send as. Defaults to the account's first identity.",
    ),
    save_sent: Optional[bool] = typer.Option(
        None,
        "--save-sent/--no-save-sent",
        help="Override the SMTP block's save_sent default for this send.",
    ),
    sent_folder: Optional[str] = typer.Option(
        None,
        "--sent-folder",
        help="Override the identity's sent_folder for the FCC step.",
    ),
) -> None:
    """Compose a new email.

    Default: saves to the IMAP drafts folder.
    --output: writes raw RFC 822 to a file or stdout.
    --send: transmits via SMTP (resolved per identity), then optionally FCCs
    to Sent.
    """
    import email.utils

    from mailroom.identity import IdentityNotFound, resolve_identity_for_send
    from mailroom.models import EmailAddress
    from mailroom.smtp_client import create_mime

    if send_flag and output is not None:
        typer.echo("Error: --send and --output are mutually exclusive", err=True)
        raise typer.Exit(2)

    cfg = _load_cfg_or_exit()
    name = _resolve_single_account_name()
    account = cfg.accounts[name]

    try:
        identity = resolve_identity_for_send(account, from_addr=from_email)
    except IdentityNotFound as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    client = _make_client()
    try:
        from_addr = EmailAddress(name=identity.name, address=identity.address)
        to_addrs = [EmailAddress.parse(a) for a in to]
        cc_addrs = [EmailAddress.parse(a) for a in cc] if cc else None
        bcc_addrs = [EmailAddress.parse(a) for a in bcc] if bcc else None

        mime_message = create_mime(
            from_addr=from_addr,
            body=body,
            to=to_addrs,
            subject=subject,
            cc=cc_addrs,
            bcc=bcc_addrs,
            html_body=body_html,
            attachments=attach,
        )
        if not mime_message.get("Message-ID"):
            mime_message["Message-ID"] = email.utils.make_msgid()

        if send_flag:
            _out(
                _perform_send(
                    client,
                    mime_message,
                    account,
                    name,
                    cfg.smtp_blocks,
                    identity,
                    save_sent,
                    sent_folder,
                )
            )
        elif output is not None:
            _write_raw_output(mime_message, output)
        else:
            draft_uid = client.save_draft_mime(mime_message)
            if draft_uid is None:
                typer.echo("Failed to save draft", err=True)
                raise typer.Exit(1)
            _out(
                {
                    "status": "success",
                    "message": "Draft saved",
                    "identity": identity.address,
                    "draft_uid": draft_uid,
                    "draft_folder": client._get_drafts_folder(),
                }
            )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# reply
# ---------------------------------------------------------------------------


@app.command("reply")
def reply(
    folder: str = typer.Option(
        ..., "--folder", "-f", help="Folder containing the email."
    ),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID to reply to."),
    body: str = typer.Option(..., "--body", "-b", help="Reply body text."),
    reply_all: bool = typer.Option(
        False, "--reply-all", help="Reply to all recipients."
    ),
    cc: Optional[List[str]] = typer.Option(None, "--cc", help="CC recipients."),
    bcc: Optional[List[str]] = typer.Option(
        None,
        "--bcc",
        help="BCC recipients (added to raw message; stripped by sending agents).",
    ),
    body_html: Optional[str] = typer.Option(
        None, "--body-html", help="HTML version of reply body."
    ),
    attach: Optional[List[str]] = typer.Option(
        None,
        "--attach",
        help="Path to a file to attach. Repeatable.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for raw RFC 822 message. Use '-' for stdout (suitable for piping).",
    ),
    send_flag: bool = typer.Option(
        False,
        "--send",
        help="Transmit via SMTP instead of saving as a draft. Mutually exclusive with --output.",
    ),
    from_email: Optional[str] = typer.Option(
        None,
        "--from",
        help="Identity address to reply as. Defaults to whichever identity matches the parent's recipients.",
    ),
    save_sent: Optional[bool] = typer.Option(
        None,
        "--save-sent/--no-save-sent",
        help="Override the SMTP block's save_sent default for this send.",
    ),
    sent_folder: Optional[str] = typer.Option(
        None,
        "--sent-folder",
        help="Override the identity's sent_folder for the FCC step.",
    ),
) -> None:
    """Draft or send a reply to an email.

    Default: saves to drafts.
    --output: writes raw RFC 822.
    --send: transmits via SMTP and FCCs to Sent.
    Reply identity defaults to whichever account identity matches the
    parent's recipients (so a reply to alias X is sent as alias X).
    """
    import email.utils

    from mailroom.identity import (
        IdentityNotFound,
        resolve_identity_for_reply,
        resolve_identity_for_send,
    )
    from mailroom.models import EmailAddress
    from mailroom.smtp_client import create_mime

    if send_flag and output is not None:
        typer.echo("Error: --send and --output are mutually exclusive", err=True)
        raise typer.Exit(2)

    cfg = _load_cfg_or_exit()
    name = _resolve_single_account_name()
    account = cfg.accounts[name]

    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)

        try:
            if from_email is not None:
                identity = resolve_identity_for_send(account, from_addr=from_email)
            else:
                identity = resolve_identity_for_reply(email_obj, account)
        except IdentityNotFound as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        from_addr = EmailAddress(name=identity.name, address=identity.address)
        cc_addresses = [EmailAddress.parse(addr) for addr in cc] if cc else None
        bcc_addresses = [EmailAddress.parse(addr) for addr in bcc] if bcc else None

        mime_message = create_mime(
            original_email=email_obj,
            from_addr=from_addr,
            body=body,
            reply_all=reply_all,
            cc=cc_addresses,
            bcc=bcc_addresses,
            html_body=body_html,
            attachments=attach,
        )
        if not mime_message.get("Message-ID"):
            mime_message["Message-ID"] = email.utils.make_msgid()

        if send_flag:
            _out(
                _perform_send(
                    client,
                    mime_message,
                    account,
                    name,
                    cfg.smtp_blocks,
                    identity,
                    save_sent,
                    sent_folder,
                )
            )
        elif output is not None:
            _write_raw_output(mime_message, output)
        else:
            draft_uid = client.save_draft_mime(mime_message)
            if draft_uid is None:
                typer.echo("Failed to save reply draft", err=True)
                raise typer.Exit(1)
            _out(
                {
                    "status": "success",
                    "message": "Draft reply saved",
                    "identity": identity.address,
                    "draft_uid": draft_uid,
                    "draft_folder": client._get_drafts_folder(),
                }
            )
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# send-draft
# ---------------------------------------------------------------------------


@app.command("send-draft")
def send_draft(
    folder: str = typer.Option(
        ..., "--folder", "-f", help="Folder containing the draft."
    ),
    uid: int = typer.Option(..., "--uid", "-u", help="Draft UID to send."),
    keep_draft: bool = typer.Option(
        False,
        "--keep-draft",
        help="Leave the draft in place after sending. Default deletes it.",
    ),
    save_sent: Optional[bool] = typer.Option(
        None,
        "--save-sent/--no-save-sent",
        help="Override the SMTP block's save_sent default for this send.",
    ),
    sent_folder: Optional[str] = typer.Option(
        None,
        "--sent-folder",
        help="Override the identity's sent_folder for the FCC step.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Connect to SMTP and authenticate, but stop before MAIL FROM. Useful for validating creds.",
    ),
    bcc: Optional[List[str]] = typer.Option(
        None,
        "--bcc",
        help="Add envelope-time BCC recipients without rewriting the draft body.",
    ),
) -> None:
    """Send an existing draft as-is.

    The draft's From header is parsed and matched against the selected
    account's identities. An unknown From is a hard error (no silent
    fallback) so that an AI accidentally drafting from the wrong identity
    cannot send through the wrong SMTP route.

    On success the draft is deleted from the source folder unless
    --keep-draft is set, and the message is FCC'd to Sent unless
    --no-save-sent or the SMTP block's save_sent resolution says otherwise.
    """
    from email.parser import BytesParser

    from mailroom.identity import (
        IdentityNotFound,
        SmtpUnresolved,
        resolve_identity_for_send,
        resolve_smtp_for_identity,
    )
    from mailroom.smtp_transport import _pick_default_transport

    cfg = _load_cfg_or_exit()
    name = _resolve_single_account_name()
    account = cfg.accounts[name]

    client = _make_client()
    try:
        fetched = client.fetch_raw(uid, folder)
        if not fetched:
            typer.echo(f"Error: draft UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)

        msg = BytesParser().parsebytes(fetched["raw"])

        from_raw = str(msg.get("From", "") or "").strip()
        from_addr_only = from_raw
        if "<" in from_raw and ">" in from_raw:
            from_addr_only = from_raw.split("<", 1)[1].rsplit(">", 1)[0].strip()
        if not from_addr_only:
            typer.echo("Error: draft has no From header", err=True)
            raise typer.Exit(1)

        try:
            identity = resolve_identity_for_send(account, from_addr=from_addr_only)
        except IdentityNotFound as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        try:
            smtp = resolve_smtp_for_identity(identity, account, name, cfg.smtp_blocks)
        except SmtpUnresolved as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        if bcc:
            existing = str(msg.get("Bcc", "") or "").strip()
            merged = ", ".join([existing] + list(bcc)) if existing else ", ".join(bcc)
            if "Bcc" in msg:
                del msg["Bcc"]
            msg["Bcc"] = merged

        if dry_run:
            factory = _pick_default_transport(smtp.port)
            try:
                conn = factory(smtp.host, smtp.port)
                conn.ehlo()
                if smtp.port in (587, 2587):
                    conn.starttls()
                    conn.ehlo()
                if smtp.username and smtp.password:
                    conn.login(smtp.username, smtp.password)
                conn.quit()
            except Exception as exc:
                typer.echo(f"Error: dry-run failed: {exc}", err=True)
                raise typer.Exit(1)
            _out(
                {
                    "dry_run": True,
                    "identity": identity.address,
                    "smtp": {"host": smtp.host, "port": smtp.port},
                }
            )
            return

        result = _perform_send(
            client,
            msg,
            account,
            name,
            cfg.smtp_blocks,
            identity,
            save_sent,
            sent_folder,
        )

        draft_removed = False
        if not keep_draft:
            try:
                client.delete_email(uid, folder)
                draft_removed = True
            except Exception as exc:
                typer.echo(f"warning: draft delete failed: {exc}", err=True)

        result["draft_removed"] = draft_removed
        _out(result)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# accept-invite
# ---------------------------------------------------------------------------


@app.command("accept-invite")
def accept_invite(
    folder: str = typer.Option(
        ..., "--folder", "-f", help="Folder containing the invite email."
    ),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    availability_mode: str = typer.Option(
        "random",
        "--availability-mode",
        help="Availability mode: random, always_available, always_busy, business_hours, weekdays.",
    ),
) -> None:
    """Process a meeting invite and create a draft reply."""
    from mailroom.workflows.meeting_reply import process_meeting_invite_workflow

    client = _make_client()
    try:
        result = process_meeting_invite_workflow(client, folder, uid, availability_mode)
        _out(result)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# mcp (start MCP server)
# ---------------------------------------------------------------------------


@app.command("mcp")
def mcp_serve(
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to TOML configuration file.",
        envvar="MAILROOM_CONFIG",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging."),
    dev: bool = typer.Option(False, "--dev", help="Enable development mode."),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    """Start the MCP server (Model Context Protocol)."""
    if version:
        print(f"Mailroom MCP server version {__version__}")
        raise typer.Exit()
    from mailroom.mcp_server import create_server

    server = create_server(config, debug)
    server.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_SEARCH_ALIASES = {"search-email", "search_email", "email-search", "email_search"}


def _rewrite_argv(argv: List[str]) -> List[str]:
    """Rewrite argv for AI-friendly invocation.

    Two transformations:
    1. If the subcommand is one of the known search aliases
       (``email_search``, ``email-search``, ``search_email``, ``search-email``),
       rewrite it to ``search`` and emit a note to stderr.
    2. If ``-a``/``--account`` appears after the subcommand, hoist it to
       before the subcommand so Typer's global callback sees it.

    Skips when ``_MAILROOM_COMPLETE`` is set so shell-completion is undisturbed.
    """
    if os.environ.get("_MAILROOM_COMPLETE"):
        return argv
    out = list(argv)
    globals_with_value = {"--config", "-c", "--account", "-a"}
    sub_idx: Optional[int] = None
    i = 0
    while i < len(out):
        tok = out[i]
        if tok == "--":
            break
        if tok in globals_with_value:
            i += 2
            continue
        if (
            tok.startswith("--")
            and "=" in tok
            and tok.split("=", 1)[0] in globals_with_value
        ):
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        sub_idx = i
        break
    if sub_idx is None:
        return out
    sub = out[sub_idx]
    if sub in _SEARCH_ALIASES:
        sys.stderr.write(f"note: no such subcommand {sub!r}; running 'search'\n")
        out[sub_idx] = "search"
    accounts: List[str] = []
    tail: List[str] = []
    j = sub_idx + 1
    while j < len(out):
        tok = out[j]
        if tok in ("--account", "-a") and j + 1 < len(out):
            accounts.append(out[j + 1])
            j += 2
            continue
        if tok.startswith("--account=") or tok.startswith("-a="):
            accounts.append(tok.split("=", 1)[1])
            j += 1
            continue
        tail.append(tok)
        j += 1
    if accounts:
        flat: List[str] = []
        for a in accounts:
            flat.extend(["--account", a])
        return out[:sub_idx] + flat + [out[sub_idx]] + tail
    return out


def main() -> None:
    sys.argv[1:] = _rewrite_argv(sys.argv[1:])
    _print_eager_warnings_if_relevant()
    app()


if __name__ == "__main__":
    main()
