"""MCP tool registrations — thin wrappers only.

This module must contain **no domain logic**.  Each function's job is:
1. Obtain an ``ImapClient`` from the MCP context.
2. Delegate to a domain function (``imap_client``, ``models``, ``smtp_client``,
   or ``workflows``).
3. Return the result in an MCP-friendly format.

Any logic that could also be useful from the CLI or in tests belongs in one
of the domain modules listed above, not here.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP

from mailroom.imap_client import ImapClient
from mailroom.models import extract_links_batch
from mailroom.resources import get_client_from_context

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP, imap_client: ImapClient) -> None:
    """Register MCP tools.

    Args:
        mcp: MCP server
        imap_client: IMAP client
    """

    @mcp.tool()
    async def draft_reply_tool(
        folder: str, uid: int, reply_body: str, ctx: Context,
        reply_all: bool = False, cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        body_html: Optional[str] = None, account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a draft reply to an email and saves it to the drafts folder.

        Args:
            folder: Email folder name
            uid: Email UID
            reply_body: Reply text content
            ctx: MCP context
            reply_all: Whether to reply to all recipients
            cc: Optional CC recipients
            bcc: Optional BCC recipients
            body_html: Optional HTML version of the reply
            account: Account name (None for default account)

        Returns:
            Dictionary with status and the UID of the created draft
        """
        from mailroom.smtp_client import compose_and_save_reply_draft

        client = get_client_from_context(ctx, account)
        return compose_and_save_reply_draft(
            client, folder, uid, reply_body,
            reply_all=reply_all, cc=cc, bcc=bcc, body_html=body_html,
        )

    @mcp.tool()
    async def move_email(
        folder: str, uid: int, target_folder: str, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Move email to another folder.

        Args:
            folder: Source folder
            uid: Email UID
            target_folder: Target folder
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            success = client.move_email(uid, folder, target_folder)
            return f"Email moved from {folder} to {target_folder}" if success else "Failed to move email"
        except Exception as e:
            logger.error(f"Error moving email: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def mark_as_read(
        folder: str, uid: int, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Mark email as read.

        Args:
            folder: Folder name
            uid: Email UID
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            success = client.mark_email(uid, folder, r"\Seen", True)
            return "Email marked as read" if success else "Failed to mark email as read"
        except Exception as e:
            logger.error(f"Error marking email as read: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def mark_as_unread(
        folder: str, uid: int, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Mark email as unread.

        Args:
            folder: Folder name
            uid: Email UID
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            success = client.mark_email(uid, folder, r"\Seen", False)
            return "Email marked as unread" if success else "Failed to mark email as unread"
        except Exception as e:
            logger.error(f"Error marking email as unread: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def flag_email(
        folder: str, uid: int, ctx: Context,
        flag: bool = True, account: Optional[str] = None,
    ) -> str:
        """Flag or unflag email.

        Args:
            folder: Folder name
            uid: Email UID
            flag: True to flag, False to unflag
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            success = client.mark_email(uid, folder, r"\Flagged", flag)
            action = "flagged" if flag else "unflagged"
            return f"Email {action}" if success else f"Failed to {action.replace('ged','g').replace('ed','')} email"
        except Exception as e:
            logger.error(f"Error flagging email: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def delete_email(
        folder: str, uid: int, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Delete email.

        Args:
            folder: Folder name
            uid: Email UID
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            success = client.delete_email(uid, folder)
            return "Email deleted" if success else "Failed to delete email"
        except Exception as e:
            logger.error(f"Error deleting email: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def search_emails(
        query: Union[str, int], ctx: Context,
        folder: Optional[str] = None, criteria: str = "text",
        limit: int = 10, account: Optional[str] = None,
    ) -> str:
        """Search for emails.

        Args:
            query: Search query (numeric IDs are converted to strings).
                   For 'raw' criteria, provide the complete IMAP search expression.
            folder: Folder to search in (None for all folders)
            criteria: Search criteria (text, from, to, subject, all, unseen, seen, raw).
                     Use 'raw' for complex IMAP expressions with OR/AND/NOT operators.
            limit: Maximum number of results
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            JSON-formatted list of search results
        """
        client = get_client_from_context(ctx, account)
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(
                    client.search_emails, str(query), criteria,
                    folder=folder, limit=limit,
                ),
                timeout=30.0,
            )
            return json.dumps(results, indent=2, default=str)
        except asyncio.TimeoutError:
            error_msg = f"Email search timed out after 30 seconds (query={query}, criteria={criteria}, folder={folder})"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "results": []})
        except ValueError as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def process_email(
        folder: str, uid: int, action: str, ctx: Context,
        notes: Optional[str] = None, target_folder: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Process an email with specified action.

        This is a higher-level tool that combines multiple actions and records
        the decision for learning purposes.

        Args:
            folder: Folder name
            uid: Email UID
            action: Action to take (move, read, unread, flag, unflag, delete)
            notes: Optional notes about the decision
            target_folder: Target folder for move action
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message or error message
        """
        client = get_client_from_context(ctx, account)
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            return f"Email with UID {uid} not found in folder {folder}"
        try:
            return client.process_email_action(uid, folder, action, target_folder=target_folder)
        except ValueError as e:
            return str(e)
        except Exception as e:
            logger.error(f"Error processing email: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def process_meeting_invite(
        folder: str, uid: int, ctx: Context,
        availability_mode: str = "random", account: Optional[str] = None,
    ) -> dict:
        """Process a meeting invite email and create a draft reply.

        This tool orchestrates the full workflow:
        1. Identifies if the email is a meeting invite
        2. Checks calendar availability for the meeting time
        3. Generates an appropriate reply (accept/decline)
        4. Creates a MIME message for the reply
        5. Saves the reply as a draft

        Args:
            folder: Folder containing the invite email
            uid: UID of the invite email
            ctx: MCP context
            availability_mode: Mode for availability check (random, always_available,
                              always_busy, business_hours, weekdays)

        Returns:
            Dictionary with the processing result
        """
        from mailroom.workflows.meeting_reply import process_meeting_invite_workflow

        client = get_client_from_context(ctx, account)
        return process_meeting_invite_workflow(client, folder, uid, availability_mode)

    @mcp.tool()
    async def list_attachments(
        folder: str, uid: int, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """List attachments for a specific email.

        Args:
            folder: Folder name
            uid: Email UID
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            JSON-formatted list of attachments with metadata
        """
        client = get_client_from_context(ctx, account)
        try:
            email_obj = client.fetch_email(uid, folder)
            if not email_obj:
                return json.dumps({"error": f"Email with UID {uid} not found in folder {folder}"})
            return json.dumps(email_obj.attachment_summaries(), indent=2)
        except Exception as e:
            logger.error(f"Error listing attachments: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def download_attachment(
        folder: str, uid: int, identifier: str, save_path: str, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Download an attachment by filename or index.

        Args:
            folder: Folder name
            uid: Email UID
            identifier: Attachment filename or index (as string)
            save_path: Path where to save the attachment
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message with filename and size, or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            email_obj = client.fetch_email(uid, folder)
            if not email_obj:
                return f"Error: Email with UID {uid} not found in folder {folder}"
            result = email_obj.save_attachment(identifier, save_path)
            logger.info(f"Saved attachment '{result['filename']}' ({result['size']} bytes) to {result['saved']}")
            return f"Success: Saved '{result['filename']}' ({result['size']} bytes) to {result['saved']}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Error downloading attachment: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def export_email_html(
        folder: str, uid: int, save_path: str, ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Export email HTML content to a standalone file with embedded images.

        Args:
            folder: Folder name
            uid: Email UID
            save_path: Path where to save the HTML file
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            Success message with file path and size, or error message
        """
        client = get_client_from_context(ctx, account)
        try:
            email_obj = client.fetch_email(uid, folder)
            if not email_obj:
                return f"Error: Email with UID {uid} not found in folder {folder}"
            result = email_obj.export_html_to_file(save_path)
            logger.info(f"Exported HTML content ({result['size']} bytes) to {result['saved']}")
            return f"Success: Exported HTML content ({result['size']} bytes) to {result['saved']}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Error exporting HTML: {e}")
            return f"Error: {e}"

    @mcp.tool()
    async def extract_email_links(
        folder: str, uids: List[int], ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """Extract all links from email HTML content for multiple emails.

        This tool is useful for fraud detection and security analysis, allowing
        you to examine all URLs in multiple emails without downloading the full HTML content.
        Links are deduplicated per email (only first occurrence of each URL is kept per email).

        Args:
            folder: Folder name
            uids: List of email UIDs
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            JSON-formatted list of results, one per UID
        """
        client = get_client_from_context(ctx, account)
        results = extract_links_batch(client.fetch_email, folder, uids)
        return json.dumps(results, indent=2)
