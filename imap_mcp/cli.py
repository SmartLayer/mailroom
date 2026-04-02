"""Command-line interface for imap-mcp tools.

Exposes the same operations available via the MCP server as direct CLI commands,
taking a --config path to the YAML configuration file.
"""

import asyncio
import json
import logging
import sys
from typing import List, Optional

import typer

from imap_mcp.config import load_config
from imap_mcp.imap_client import ImapClient
from imap_mcp.models import extract_links_batch, sanitize_and_save

app = typer.Typer(
    name="imap-mcp-cli",
    help="Command-line interface for imap-mcp email operations.",
    no_args_is_help=True,
)

# Module-level state set by the --config callback.
_config_path: Optional[str] = None
_account_name: Optional[str] = None

logger = logging.getLogger(__name__)


@app.callback()
def _global_options(
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to YAML configuration file.",
        envvar="IMAP_MCP_CONFIG",
    ),
    account: Optional[str] = typer.Option(
        None,
        "--account",
        "-a",
        help="Account name (for multi-account configs). Uses default if omitted.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging."),
) -> None:
    global _config_path, _account_name
    _config_path = config
    _account_name = account
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _make_client() -> ImapClient:
    cfg = load_config(_config_path)
    name = _account_name or cfg.default_account
    if name not in cfg.accounts:
        available = list(cfg.accounts.keys())
        raise typer.BadParameter(f"Unknown account '{name}'. Available: {available}")
    acct = cfg.accounts[name]
    client = ImapClient(acct.imap, acct.allowed_folders)
    client.connect()
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
    cfg = load_config(_config_path)
    accounts = []
    for name, acct in cfg.accounts.items():
        accounts.append({
            "name": name,
            "default": name == cfg.default_account,
            "user": acct.imap.username,
            "host": acct.imap.host,
        })
    _out(accounts)


# ---------------------------------------------------------------------------
# server-status
# ---------------------------------------------------------------------------

@app.command("server-status")
def server_status() -> None:
    """Show IMAP server status and configuration."""
    cfg = load_config(_config_path)
    status = {
        "server": "IMAP MCP",
        "version": "0.1.0",
        "default_account": cfg.default_account,
        "accounts": {},
    }
    for name, acct in cfg.accounts.items():
        status["accounts"][name] = {
            "imap_host": acct.imap.host,
            "imap_port": acct.imap.port,
            "imap_user": acct.imap.username,
            "imap_ssl": acct.imap.use_ssl,
            "allowed_folders": list(acct.allowed_folders) if acct.allowed_folders else "all",
        }
    _out(status)


# ---------------------------------------------------------------------------
# search-emails
# ---------------------------------------------------------------------------

@app.command("search-emails")
def search_emails(
    query: str = typer.Argument(..., help="Search query string."),
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Folder to search (default: all)."),
    criteria: str = typer.Option("subject", "--criteria", "-C",
        help="Search criteria: text, from, to, subject, all, unseen, seen, today, week, month, raw."),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of results."),
) -> None:
    """Search for emails."""
    client = _make_client()
    try:
        results = client.search_emails(query, criteria, folder=folder, limit=limit)
        _out(results)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# move-email
# ---------------------------------------------------------------------------

@app.command("move-email")
def move_email(
    folder: str = typer.Argument(..., help="Source folder."),
    uid: int = typer.Argument(..., help="Email UID."),
    target_folder: str = typer.Argument(..., help="Destination folder."),
) -> None:
    """Move an email to another folder."""
    client = _make_client()
    try:
        success = client.move_email(uid, folder, target_folder)
        _out({"success": success, "message": f"Moved from {folder} to {target_folder}" if success else "Failed to move email"})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# mark-as-read / mark-as-unread
# ---------------------------------------------------------------------------

@app.command("mark-as-read")
def mark_as_read(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
) -> None:
    """Mark an email as read."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Seen", True)
        _out({"success": success})
    finally:
        client.disconnect()


@app.command("mark-as-unread")
def mark_as_unread(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
) -> None:
    """Mark an email as unread."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Seen", False)
        _out({"success": success})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# flag-email
# ---------------------------------------------------------------------------

@app.command("flag-email")
def flag_email(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
    unflag: bool = typer.Option(False, "--unflag", help="Remove the flag instead of setting it."),
) -> None:
    """Flag (star) an email, or unflag it with --unflag."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Flagged", not unflag)
        _out({"success": success, "flagged": not unflag})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# delete-email
# ---------------------------------------------------------------------------

@app.command("delete-email")
def delete_email(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
) -> None:
    """Delete an email."""
    client = _make_client()
    try:
        success = client.delete_email(uid, folder)
        _out({"success": success})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# process-email
# ---------------------------------------------------------------------------

@app.command("process-email")
def process_email(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
    action: str = typer.Argument(..., help="Action: move, read, unread, flag, unflag, delete."),
    target_folder: Optional[str] = typer.Option(None, "--target-folder", "-t", help="Target folder (for move)."),
    notes: Optional[str] = typer.Option(None, "--notes", help="Optional notes."),
) -> None:
    """Process an email with a given action."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        try:
            message = client.process_email_action(uid, folder, action, target_folder=target_folder)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        _out({"message": message})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# list-attachments
# ---------------------------------------------------------------------------

@app.command("list-attachments")
def list_attachments(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
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
            entry = {"index": i, "filename": att.filename, "size": att.size, "content_type": att.content_type}
            if att.content_id:
                entry["content_id"] = att.content_id
            result.append(entry)
        _out(result)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# download-attachment
# ---------------------------------------------------------------------------

@app.command("download-attachment")
def download_attachment(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
    identifier: str = typer.Argument(..., help="Attachment filename or numeric index."),
    save_path: str = typer.Argument(..., help="Path to save the attachment."),
) -> None:
    """Download an attachment from an email."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        if not email_obj.attachments:
            typer.echo("Email has no attachments", err=True)
            raise typer.Exit(1)

        attachment = email_obj.find_attachment(identifier)
        if attachment is None:
            typer.echo(f"Attachment '{identifier}' not found. Use filename or numeric index (0-{len(email_obj.attachments)-1}).", err=True)
            raise typer.Exit(1)
        if attachment.content is None:
            typer.echo(f"Attachment '{attachment.filename}' has no content", err=True)
            raise typer.Exit(1)

        saved = sanitize_and_save(attachment.content, save_path, mode="wb")
        _out({"saved": saved, "filename": attachment.filename, "size": attachment.size})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# export-email-html
# ---------------------------------------------------------------------------

@app.command("export-email-html")
def export_email_html(
    folder: str = typer.Argument(..., help="Folder name."),
    uid: int = typer.Argument(..., help="Email UID."),
    save_path: str = typer.Argument(..., help="Path to save the HTML file."),
) -> None:
    """Export email HTML content to a standalone file with embedded images."""
    import os

    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        if not email_obj.content.html:
            typer.echo("Email has no HTML content", err=True)
            raise typer.Exit(1)

        html_content = email_obj.html_with_embedded_images()
        saved = sanitize_and_save(html_content, save_path, mode="w")
        _out({"saved": saved, "size": os.path.getsize(saved)})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# extract-email-links
# ---------------------------------------------------------------------------

@app.command("extract-email-links")
def extract_email_links(
    folder: str = typer.Argument(..., help="Folder name."),
    uids: List[int] = typer.Argument(..., help="One or more email UIDs."),
) -> None:
    """Extract all links from email HTML content."""
    client = _make_client()
    try:
        results = extract_links_batch(client.fetch_email, folder, uids)
        _out(results)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# draft-reply
# ---------------------------------------------------------------------------

@app.command("draft-reply")
def draft_reply(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder containing the email."),
    uid: int = typer.Option(..., "--uid", help="Email UID to reply to."),
    body: str = typer.Option(..., "--body", "-b", help="Reply body text."),
    reply_all: bool = typer.Option(False, "--reply-all", help="Reply to all recipients."),
    cc: Optional[List[str]] = typer.Option(None, "--cc", help="CC recipients."),
    bcc: Optional[List[str]] = typer.Option(None, "--bcc", help="BCC recipients (added to raw message; stripped by sending agents)."),
    body_html: Optional[str] = typer.Option(None, "--body-html", help="HTML version of reply body."),
    output: Optional[str] = typer.Option(None, "--output", "-o",
        help="Output path for raw RFC 822 message. Use '-' for stdout (suitable for piping)."),
) -> None:
    """Draft a reply to an email.

    By default, saves the draft to the IMAP drafts folder.
    With -o, writes the raw RFC 822 message to a file or stdout.
    """
    client = _make_client()
    try:
        if output is not None:
            # --output path: build MIME locally and write raw bytes
            from imap_mcp.smtp_client import create_reply_mime, _find_reply_from_address
            from imap_mcp.models import EmailAddress

            email_obj = client.fetch_email(uid, folder)
            if not email_obj:
                typer.echo(f"Email UID {uid} not found in {folder}", err=True)
                raise typer.Exit(1)

            reply_from = _find_reply_from_address(email_obj, client.config.username)
            cc_addresses = [EmailAddress.parse(addr) for addr in cc] if cc else None

            mime_message = create_reply_mime(
                original_email=email_obj,
                reply_to=reply_from,
                body=body,
                reply_all=reply_all,
                cc=cc_addresses,
                html_body=body_html,
            )
            if bcc:
                mime_message["Bcc"] = ", ".join(bcc)

            if hasattr(mime_message, "as_bytes"):
                raw = mime_message.as_bytes()
            else:
                raw = mime_message.as_string().encode("utf-8")

            if output == "-":
                sys.stdout.buffer.write(raw)
            else:
                import os
                os.makedirs(os.path.dirname(output) if os.path.dirname(output) else ".", exist_ok=True)
                with open(output, "wb") as fh:
                    fh.write(raw)
                typer.echo(f"Wrote {len(raw)} bytes to {output}", err=True)
        else:
            # Default path: save as draft via domain function
            from imap_mcp.smtp_client import compose_and_save_reply_draft

            result = compose_and_save_reply_draft(
                client, folder, uid, body,
                reply_all=reply_all, cc=cc, bcc=bcc, body_html=body_html,
            )
            if result["status"] == "success":
                _out(result)
            else:
                typer.echo(result.get("message", "Failed to save draft"), err=True)
                raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# process-meeting-invite
# ---------------------------------------------------------------------------

@app.command("process-meeting-invite")
def process_meeting_invite(
    folder: str = typer.Argument(..., help="Folder containing the invite email."),
    uid: int = typer.Argument(..., help="Email UID."),
    availability_mode: str = typer.Option(
        "random", "--availability-mode", "-a",
        help="Availability mode: random, always_available, always_busy, business_hours, weekdays.",
    ),
) -> None:
    """Process a meeting invite and create a draft reply."""
    from imap_mcp.workflows.meeting_reply import process_meeting_invite_workflow

    client = _make_client()
    try:
        result = process_meeting_invite_workflow(client, folder, uid, availability_mode)
        _out(result)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
