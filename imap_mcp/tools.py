"""MCP tools implementation for email operations.

Thin wrappers that wire MCP context to domain functions in
``imap_client``, ``smtp_client``, and ``workflows``.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Union

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from imap_mcp.imap_client import ImapClient
from imap_mcp.resources import get_client_from_context

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP, imap_client: ImapClient) -> None:
    """Register MCP tools.

    Args:
        mcp: MCP server
        imap_client: IMAP client
    """

    @mcp.tool()
    async def draft_reply_tool(folder: str, uid: int, reply_body: str, ctx: Context,
                           reply_all: bool = False, cc: Optional[List[str]] = None,
                           bcc: Optional[List[str]] = None,
                           body_html: Optional[str] = None, account: Optional[str] = None) -> Dict[str, Any]:
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
        from imap_mcp.smtp_client import compose_and_save_reply_draft

        client = get_client_from_context(ctx, account)
        result = compose_and_save_reply_draft(
            client, folder, uid, reply_body,
            reply_all=reply_all, cc=cc, bcc=bcc, body_html=body_html,
        )
        return result

    # Move email to a different folder
    @mcp.tool()
    async def move_email(
        folder: str,
        uid: int,
        target_folder: str,
        ctx: Context,
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
            if success:
                return f"Email moved from {folder} to {target_folder}"
            else:
                return "Failed to move email"
        except Exception as e:
            logger.error(f"Error moving email: {e}")
            return f"Error: {e}"

    # Mark email as read
    @mcp.tool()
    async def mark_as_read(
        folder: str,
        uid: int,
        ctx: Context,
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
            if success:
                return "Email marked as read"
            else:
                return "Failed to mark email as read"
        except Exception as e:
            logger.error(f"Error marking email as read: {e}")
            return f"Error: {e}"

    # Mark email as unread
    @mcp.tool()
    async def mark_as_unread(
        folder: str,
        uid: int,
        ctx: Context,
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
            if success:
                return "Email marked as unread"
            else:
                return "Failed to mark email as unread"
        except Exception as e:
            logger.error(f"Error marking email as unread: {e}")
            return f"Error: {e}"

    # Flag email (important/starred)
    @mcp.tool()
    async def flag_email(
        folder: str,
        uid: int,
        ctx: Context,
        flag: bool = True,
        account: Optional[str] = None,
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
            if success:
                return f"Email {'flagged' if flag else 'unflagged'}"
            else:
                return f"Failed to {'flag' if flag else 'unflag'} email"
        except Exception as e:
            logger.error(f"Error flagging email: {e}")
            return f"Error: {e}"

    # Delete email
    @mcp.tool()
    async def delete_email(
        folder: str,
        uid: int,
        ctx: Context,
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
            if success:
                return "Email deleted"
            else:
                return "Failed to delete email"
        except Exception as e:
            logger.error(f"Error deleting email: {e}")
            return f"Error: {e}"

    # Search for emails
    @mcp.tool()
    async def search_emails(
        query: Union[str, int],
        ctx: Context,
        folder: Optional[str] = None,
        criteria: str = "text",
        limit: int = 10,
        account: Optional[str] = None,
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

    # Process email interactive session
    @mcp.tool()
    async def process_email(
        folder: str,
        uid: int,
        action: str,
        ctx: Context,
        notes: Optional[str] = None,
        target_folder: Optional[str] = None,
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

        # Fetch the email first to have context for learning
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            return f"Email with UID {uid} not found in folder {folder}"

        # Process the action
        try:
            if action.lower() == "move":
                if not target_folder:
                    return "Target folder must be specified for move action"
                client.move_email(uid, folder, target_folder)
                return f"Email moved from {folder} to {target_folder}"
            elif action.lower() == "read":
                client.mark_email(uid, folder, r"\Seen", True)
                return "Email marked as read"
            elif action.lower() == "unread":
                client.mark_email(uid, folder, r"\Seen", False)
                return "Email marked as unread"
            elif action.lower() == "flag":
                client.mark_email(uid, folder, r"\Flagged", True)
                return "Email flagged"
            elif action.lower() == "unflag":
                client.mark_email(uid, folder, r"\Flagged", False)
                return "Email unflagged"
            elif action.lower() == "delete":
                client.delete_email(uid, folder)
                return "Email deleted"
            else:
                return f"Invalid action: {action}"
        except Exception as e:
            logger.error(f"Error processing email: {e}")
            return f"Error: {e}"

    # Process meeting invite and generate a draft reply
    @mcp.tool()
    async def process_meeting_invite(
        folder: str,
        uid: int,
        ctx: Context,
        availability_mode: str = "random",
        account: Optional[str] = None,
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
            Dictionary with the processing result:
              - status: "success", "not_invite", or "error"
              - message: Description of the result
              - draft_uid: UID of the saved draft (if successful)
              - draft_folder: Folder where the draft was saved (if successful)
              - availability: Whether the time slot was available
        """
        from imap_mcp.workflows.meeting_reply import process_meeting_invite_workflow

        client = get_client_from_context(ctx, account)
        return process_meeting_invite_workflow(client, folder, uid, availability_mode)

    # List attachments for an email
    @mcp.tool()
    async def list_attachments(
        folder: str,
        uid: int,
        ctx: Context,
        account: Optional[str] = None,
    ) -> str:
        """List attachments for a specific email.

        Args:
            folder: Folder name
            uid: Email UID
            ctx: MCP context
            account: Account name (None for default account)

        Returns:
            JSON-formatted list of attachments with metadata (index, filename, size, content_type, content_id)
        """
        client = get_client_from_context(ctx, account)

        try:
            # Fetch the email
            email_obj = client.fetch_email(uid, folder)

            if not email_obj:
                return json.dumps({"error": f"Email with UID {uid} not found in folder {folder}"})

            # Extract attachment metadata
            attachments_list = []
            for index, attachment in enumerate(email_obj.attachments):
                attachment_info = {
                    "index": index,
                    "filename": attachment.filename,
                    "size": attachment.size,
                    "content_type": attachment.content_type,
                }

                # Include content_id if present
                if attachment.content_id:
                    attachment_info["content_id"] = attachment.content_id

                attachments_list.append(attachment_info)

            return json.dumps(attachments_list, indent=2)

        except Exception as e:
            logger.error(f"Error listing attachments: {e}")
            return json.dumps({"error": str(e)})

    # Download an attachment
    @mcp.tool()
    async def download_attachment(
        folder: str,
        uid: int,
        identifier: str,
        save_path: str,
        ctx: Context,
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
            # Fetch the email
            email_obj = client.fetch_email(uid, folder)

            if not email_obj:
                return f"Error: Email with UID {uid} not found in folder {folder}"

            if not email_obj.attachments:
                return "Error: Email has no attachments"

            # Find the attachment by identifier
            attachment = None

            # First, try to match by exact filename
            for att in email_obj.attachments:
                if att.filename == identifier:
                    attachment = att
                    break

            # If not found, try to parse as index
            if attachment is None:
                try:
                    index = int(identifier)
                    if 0 <= index < len(email_obj.attachments):
                        attachment = email_obj.attachments[index]
                    else:
                        return f"Error: Invalid attachment index {index}. Valid range: 0-{len(email_obj.attachments) - 1}"
                except ValueError:
                    return f"Error: Attachment '{identifier}' not found. Use filename or numeric index."

            if attachment is None:
                return f"Error: Attachment '{identifier}' not found"

            # Sanitize the save_path to prevent path traversal
            sanitized_path = save_path.replace("../", "").replace("..\\", "")

            # Check if attachment has content
            if attachment.content is None:
                return f"Error: Attachment '{attachment.filename}' has no content"

            # Write the attachment to disk
            import os

            os.makedirs(os.path.dirname(sanitized_path) if os.path.dirname(sanitized_path) else ".", exist_ok=True)

            with open(sanitized_path, "wb") as f:
                f.write(attachment.content)

            logger.info(f"Saved attachment '{attachment.filename}' ({attachment.size} bytes) to {sanitized_path}")
            return f"Success: Saved '{attachment.filename}' ({attachment.size} bytes) to {sanitized_path}"

        except Exception as e:
            logger.error(f"Error downloading attachment: {e}")
            return f"Error: {e}"

    # Export email HTML to file
    @mcp.tool()
    async def export_email_html(
        folder: str,
        uid: int,
        save_path: str,
        ctx: Context,
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
            # Fetch the email
            email_obj = client.fetch_email(uid, folder)

            if not email_obj:
                return f"Error: Email with UID {uid} not found in folder {folder}"

            # Check if email has HTML content
            if not email_obj.content.html:
                return "Error: Email has no HTML content"

            # Process HTML to embed inline images
            html_content = email_obj.html_with_embedded_images()

            # Sanitize the save_path to prevent path traversal
            sanitized_path = save_path.replace("../", "").replace("..\\", "")

            # Create directories if they don't exist
            import os

            os.makedirs(os.path.dirname(sanitized_path) if os.path.dirname(sanitized_path) else ".", exist_ok=True)

            # Write the HTML to disk
            with open(sanitized_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            # Get file size
            file_size = os.path.getsize(sanitized_path)

            logger.info(f"Exported HTML content ({file_size} bytes) to {sanitized_path}")
            return f"Success: Exported HTML content ({file_size} bytes) to {sanitized_path}"

        except Exception as e:
            logger.error(f"Error exporting HTML: {e}")
            return f"Error: {e}"

    # Extract links from email HTML
    @mcp.tool()
    async def extract_email_links(
        folder: str,
        uids: List[int],
        ctx: Context,
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
            JSON-formatted list of results, one per UID. Each result contains:
            - uid: The email UID
            - links: List of link objects with url, anchor, and position (or empty list)
            - error: Error message if email not found or has no HTML content (optional)
        """
        client = get_client_from_context(ctx, account)
        results = []

        for uid in uids:
            try:
                # Fetch the email
                email_obj = client.fetch_email(uid, folder)

                if not email_obj:
                    results.append({
                        "uid": uid,
                        "error": f"Email with UID {uid} not found in folder {folder}",
                        "links": []
                    })
                    continue

                # Check if email has HTML content
                if not email_obj.content.html:
                    results.append({
                        "uid": uid,
                        "error": "Email has no HTML content",
                        "links": []
                    })
                    continue

                # Extract links from HTML
                links = email_obj.extract_links()

                results.append({
                    "uid": uid,
                    "links": links
                })

                logger.info(f"Extracted {len(links)} unique links from email UID {uid} in folder {folder}")

            except Exception as e:
                logger.error(f"Error extracting links from UID {uid}: {e}")
                results.append({
                    "uid": uid,
                    "error": str(e),
                    "links": []
                })

        return json.dumps(results, indent=2)
