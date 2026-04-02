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
from imap_mcp.tools import _parse_raw_imap_criteria

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
    search_criteria_map = {
        "text": ["TEXT", query],
        "from": ["FROM", query],
        "to": ["TO", query],
        "subject": ["SUBJECT", query],
        "all": "ALL",
        "unseen": "UNSEEN",
        "seen": "SEEN",
        "today": "today",
        "week": "week",
        "month": "month",
        "raw": "raw",
    }

    if criteria.lower() not in search_criteria_map:
        typer.echo(f"Invalid criteria '{criteria}'. Valid: {', '.join(search_criteria_map)}", err=True)
        raise typer.Exit(1)

    if criteria.lower() == "raw":
        try:
            search_spec = _parse_raw_imap_criteria(query)
        except Exception as exc:
            typer.echo(f"Failed to parse raw IMAP criteria: {exc}", err=True)
            raise typer.Exit(1)
    else:
        search_spec = search_criteria_map[criteria.lower()]

    client = _make_client()
    try:
        folders_to_search = [folder] if folder else client.list_folders()
        results = []

        for current_folder in folders_to_search:
            try:
                uids = client.search(search_spec, folder=current_folder)
                uids = sorted(uids, reverse=True)[:limit]
                if uids:
                    emails = client.fetch_emails(uids, folder=current_folder)
                    for uid, email_obj in emails.items():
                        results.append({
                            "uid": uid,
                            "folder": current_folder,
                            "from": str(email_obj.from_),
                            "to": [str(t) for t in email_obj.to],
                            "subject": email_obj.subject,
                            "date": email_obj.date.isoformat() if email_obj.date else None,
                            "flags": email_obj.flags,
                            "has_attachments": len(email_obj.attachments) > 0,
                        })
            except Exception as exc:
                logger.warning("Error searching folder %s: %s", current_folder, exc)

        results.sort(key=lambda x: x.get("date") or "0", reverse=True)
        results = results[:limit]
        _out(results)
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

        action_l = action.lower()
        if action_l == "move":
            if not target_folder:
                typer.echo("--target-folder is required for move action", err=True)
                raise typer.Exit(1)
            client.move_email(uid, folder, target_folder)
            _out({"message": f"Moved from {folder} to {target_folder}"})
        elif action_l == "read":
            client.mark_email(uid, folder, r"\Seen", True)
            _out({"message": "Marked as read"})
        elif action_l == "unread":
            client.mark_email(uid, folder, r"\Seen", False)
            _out({"message": "Marked as unread"})
        elif action_l == "flag":
            client.mark_email(uid, folder, r"\Flagged", True)
            _out({"message": "Flagged"})
        elif action_l == "unflag":
            client.mark_email(uid, folder, r"\Flagged", False)
            _out({"message": "Unflagged"})
        elif action_l == "delete":
            client.delete_email(uid, folder)
            _out({"message": "Deleted"})
        else:
            typer.echo(f"Unknown action '{action}'. Valid: move, read, unread, flag, unflag, delete", err=True)
            raise typer.Exit(1)
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
    import os

    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        if not email_obj.attachments:
            typer.echo("Email has no attachments", err=True)
            raise typer.Exit(1)

        attachment = None
        for att in email_obj.attachments:
            if att.filename == identifier:
                attachment = att
                break
        if attachment is None:
            try:
                idx = int(identifier)
                if 0 <= idx < len(email_obj.attachments):
                    attachment = email_obj.attachments[idx]
                else:
                    typer.echo(f"Index {idx} out of range (0–{len(email_obj.attachments)-1})", err=True)
                    raise typer.Exit(1)
            except ValueError:
                typer.echo(f"Attachment '{identifier}' not found", err=True)
                raise typer.Exit(1)

        if attachment.content is None:
            typer.echo(f"Attachment '{attachment.filename}' has no content", err=True)
            raise typer.Exit(1)

        sanitized = save_path.replace("../", "").replace("..\\", "")
        os.makedirs(os.path.dirname(sanitized) if os.path.dirname(sanitized) else ".", exist_ok=True)
        with open(sanitized, "wb") as fh:
            fh.write(attachment.content)
        _out({"saved": sanitized, "filename": attachment.filename, "size": attachment.size})
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
    from imap_mcp.tools import _embed_inline_images

    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        if not email_obj.content.html:
            typer.echo("Email has no HTML content", err=True)
            raise typer.Exit(1)

        html_content = _embed_inline_images(email_obj.content.html, email_obj.attachments)
        sanitized = save_path.replace("../", "").replace("..\\", "")
        os.makedirs(os.path.dirname(sanitized) if os.path.dirname(sanitized) else ".", exist_ok=True)
        with open(sanitized, "w", encoding="utf-8") as fh:
            fh.write(html_content)
        _out({"saved": sanitized, "size": os.path.getsize(sanitized)})
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
    from imap_mcp.tools import _extract_links_from_html

    client = _make_client()
    try:
        results = []
        for uid in uids:
            try:
                email_obj = client.fetch_email(uid, folder)
                if not email_obj:
                    results.append({"uid": uid, "error": f"UID {uid} not found", "links": []})
                    continue
                if not email_obj.content.html:
                    results.append({"uid": uid, "error": "No HTML content", "links": []})
                    continue
                links = _extract_links_from_html(email_obj.content.html)
                results.append({"uid": uid, "links": links})
            except Exception as exc:
                results.append({"uid": uid, "error": str(exc), "links": []})
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
    from imap_mcp.smtp_client import create_reply_mime
    from imap_mcp.models import EmailAddress

    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)

        # Find the recipient that matches this account's address
        reply_from = None
        my_addr = client.config.username.lower()
        for recipient in (email_obj.to or []) + (email_obj.cc or []):
            if recipient.address and recipient.address.lower() == my_addr:
                reply_from = recipient
                break
        if reply_from is None:
            reply_from = (email_obj.to[0] if email_obj.to
                          else EmailAddress(name="", address=client.config.username))

        cc_addresses = None
        if cc:
            cc_addresses = [EmailAddress.parse(addr) for addr in cc]

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

        if output is not None:
            # Output raw RFC 822 message
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
            # Save as draft in IMAP
            draft_uid = client.save_draft_mime(mime_message)
            if draft_uid:
                drafts_folder = client._get_drafts_folder()
                _out({"status": "success", "draft_uid": draft_uid, "draft_folder": drafts_folder})
            else:
                typer.echo("Failed to save draft", err=True)
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
    from imap_mcp.models import EmailAddress
    from imap_mcp.workflows.invite_parser import identify_meeting_invite_details
    from imap_mcp.workflows.calendar_mock import check_mock_availability
    from imap_mcp.workflows.meeting_reply import generate_meeting_reply_content
    from imap_mcp.smtp_client import create_reply_mime

    client = _make_client()
    result: dict = {
        "status": "error",
        "message": "An error occurred",
        "draft_uid": None,
        "draft_folder": None,
        "availability": None,
    }
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            result["message"] = f"Email UID {uid} not found in {folder}"
            _out(result)
            return

        invite_result = identify_meeting_invite_details(email_obj)
        if not invite_result["is_invite"]:
            result["status"] = "not_invite"
            result["message"] = "Not a meeting invite"
            _out(result)
            return

        invite_details = invite_result["details"]
        avail_result = check_mock_availability(
            invite_details.get("start_time"),
            invite_details.get("end_time"),
            availability_mode,
        )
        result["availability"] = avail_result["available"]
        reply_content = generate_meeting_reply_content(invite_details, avail_result)

        reply_from = email_obj.to[0] if email_obj.to else EmailAddress(
            name="Me", address=client.config.username
        )
        mime_message = create_reply_mime(
            original_email=email_obj,
            reply_to=reply_from,
            body=reply_content["reply_body"],
            subject=reply_content["reply_subject"],
            reply_all=False,
        )
        draft_uid = client.save_draft_mime(mime_message)
        if draft_uid:
            drafts_folder = client._get_drafts_folder()
            result["status"] = "success"
            result["message"] = f"Draft created: {reply_content['reply_type']}"
            result["draft_uid"] = draft_uid
            result["draft_folder"] = drafts_folder
        else:
            result["message"] = "Failed to save draft"
    except Exception as exc:
        result["message"] = f"Error: {exc}"
    finally:
        client.disconnect()

    _out(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
