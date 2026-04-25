"""Mailroom — email toolkit for AI assistants and command-line scripting.

All CLI commands are subcommands of `mailroom`. The `mcp` subcommand starts
the MCP server; every other subcommand operates directly via IMAP without
importing the mcp package.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import typer

from mailroom import __version__
from mailroom.config import load_config
from mailroom.imap_client import ImapClient
from mailroom.models import extract_links_batch


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mailroom {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="mailroom",
    help="Email toolkit for AI assistants and command-line scripting.",
    no_args_is_help=True,
)

# Module-level state set by the --config callback.
_config_path: Optional[str] = None
_account_names: List[str] = []
_all_accounts: bool = False

logger = logging.getLogger(__name__)


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
    client = ImapClient(acct.imap, acct.allowed_folders)
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
    client = ImapClient(acct.imap, acct.allowed_folders)
    try:
        client.connect()
    except Exception as exc:
        typer.echo(f"warning: connect failed for '{name}': {exc}", err=True)
        return None
    return client


def _out(data: object) -> None:
    """Print data as JSON to stdout."""
    if isinstance(data, str):
        # Many tools return a JSON string; pass it through as-is.
        print(data)
    else:
        print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# list-accounts
# ---------------------------------------------------------------------------


@app.command("list-accounts")
def list_accounts() -> None:
    """List configured email accounts."""
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
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


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status() -> None:
    """Show IMAP server status and configuration."""
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    accounts_map: Dict[str, Any] = {}
    status_data: Dict[str, Any] = {
        "server": "Mailroom",
        "version": "1.0.3",
        "default_account": cfg.default_account,
        "accounts": accounts_map,
    }
    for name, acct in cfg.accounts.items():
        status_data["accounts"][name] = {
            "imap_host": acct.imap.host,
            "imap_port": acct.imap.port,
            "imap_user": acct.imap.username,
            "imap_ssl": acct.imap.use_ssl,
            "allowed_folders": (
                list(acct.allowed_folders) if acct.allowed_folders else "all"
            ),
        }
    _out(status_data)


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
) -> None:
    """Search for emails. Returns a dict keyed by account name.

    Single-account searches return ``{"acct-name": [...]}``; multi-account
    searches (``-a A -a B`` or ``--all-accounts``) return one key per
    account searched. ``--limit`` is applied per account.
    """
    effective = query_opt if query_opt is not None else query
    names = _resolve_accounts()
    results: Dict[str, List[Dict[str, Any]]] = {}
    for name in names:
        client = _make_client_soft(name)
        if client is None:
            results[name] = []
            continue
        try:
            results[name] = client.search_emails(effective, folder=folder, limit=limit)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        except Exception as exc:
            typer.echo(f"warning: search failed for '{name}': {exc}", err=True)
            results[name] = []
        finally:
            client.disconnect()
    _out(results)


# ---------------------------------------------------------------------------
# read (NEW)
# ---------------------------------------------------------------------------


@app.command("read")
def read(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Read an email's content."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        result: Dict[str, Any] = {
            "uid": uid,
            "folder": folder,
            "from": str(email_obj.from_),
            "to": [str(to) for to in email_obj.to],
            "subject": email_obj.subject,
            "date": (email_obj.date.isoformat() if email_obj.date else None),
            "flags": email_obj.flags,
            "content_type": ("text/html" if email_obj.content.html else "text/plain"),
            "body": (
                str(email_obj.content.html)
                if email_obj.content.html
                else str(email_obj.content.text) if email_obj.content.text else None
            ),
        }
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
        _out(result)
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
    """List attachments for an email."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            _out({"error": f"Email UID {uid} not found in {folder}"})
            return
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
    finally:
        client.disconnect()


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
) -> None:
    """Compose a new email.

    By default, saves the draft to the IMAP drafts folder.
    With -o, writes the raw RFC 822 message to a file or stdout instead.
    """
    from mailroom.models import EmailAddress
    from mailroom.smtp_client import compose_and_save_draft, create_mime

    client = _make_client()
    try:
        if output is not None:
            from_addr = EmailAddress(name="", address=client.config.username)
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
            _write_raw_output(mime_message, output)
        else:
            result = compose_and_save_draft(
                client,
                to,
                subject,
                body,
                cc=cc,
                bcc=bcc,
                body_html=body_html,
                attachments=attach,
            )
            if result["status"] == "success":
                _out(result)
            else:
                typer.echo(result.get("message", "Failed to save draft"), err=True)
                raise typer.Exit(1)
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
) -> None:
    """Draft a reply to an email.

    By default, saves the draft to the IMAP drafts folder.
    With -o, writes the raw RFC 822 message to a file or stdout.
    """
    client = _make_client()
    try:
        if output is not None:
            # --output path: build MIME locally and write raw bytes
            from mailroom.models import EmailAddress
            from mailroom.smtp_client import _find_reply_from_address, create_mime

            email_obj = client.fetch_email(uid, folder)
            if not email_obj:
                typer.echo(f"Email UID {uid} not found in {folder}", err=True)
                raise typer.Exit(1)

            reply_from = _find_reply_from_address(email_obj, client.config.username)
            cc_addresses = [EmailAddress.parse(addr) for addr in cc] if cc else None
            bcc_addresses = [EmailAddress.parse(addr) for addr in bcc] if bcc else None

            mime_message = create_mime(
                original_email=email_obj,
                from_addr=reply_from,
                body=body,
                reply_all=reply_all,
                cc=cc_addresses,
                bcc=bcc_addresses,
                html_body=body_html,
                attachments=attach,
            )
            _write_raw_output(mime_message, output)
        else:
            # Default path: save as draft via domain function
            from mailroom.smtp_client import compose_and_save_reply_draft

            result = compose_and_save_reply_draft(
                client,
                folder,
                uid,
                body,
                reply_all=reply_all,
                cc=cc,
                bcc=bcc,
                body_html=body_html,
                attachments=attach,
            )
            if result["status"] == "success":
                _out(result)
            else:
                typer.echo(result.get("message", "Failed to save draft"), err=True)
                raise typer.Exit(1)
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
        print("Mailroom MCP server version 1.0.3")
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
    app()


if __name__ == "__main__":
    main()
