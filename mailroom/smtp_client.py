"""SMTP client implementation for sending emails."""

import email.utils
import logging
import mimetypes
import os
from datetime import datetime
from email import encoders
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Union

from mailroom.models import Email, EmailAddress

logger = logging.getLogger(__name__)


def _attach_file(container: MIMEMultipart, path: str) -> None:
    """Attach a file to a multipart/mixed container.

    Args:
        container: Outer ``multipart/mixed`` message to attach into.
        path: Filesystem path to the file.

    Raises:
        ValueError: If *path* is not a readable regular file.
    """
    if not os.path.isfile(path):
        raise ValueError(f"Attachment not found or not a regular file: {path}")

    ctype, encoding = mimetypes.guess_type(path)
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)

    with open(path, "rb") as fh:
        payload = fh.read()

    part = MIMEBase(maintype, subtype)
    part.set_payload(payload)
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        "attachment",
        filename=("utf-8", "", os.path.basename(path)),
    )
    container.attach(part)


def create_reply_mime(
    original_email: Email,
    reply_to: EmailAddress,
    body: str,
    subject: Optional[str] = None,
    cc: Optional[List[EmailAddress]] = None,
    reply_all: bool = False,
    html_body: Optional[str] = None,
    attachments: Optional[List[str]] = None,
) -> Union[EmailMessage, MIMEMultipart]:
    """Create a MIME message for replying to an email.

    Args:
        original_email: Original email to reply to
        reply_to: Address to send the reply from
        body: Plain text body of the reply
        subject: Subject for the reply (default: prepend "Re: " to original)
        cc: List of CC recipients (default: none)
        reply_all: Whether to reply to all recipients (default: False)
        html_body: Optional HTML version of the body
        attachments: Optional list of filesystem paths to attach. Duplicate
            basenames are accepted but may confuse some clients on save.

    Returns:
        MIME message ready for sending

    Raises:
        ValueError: If any attachment path is not a readable regular file.
    """
    multipart_mode = bool(html_body) or bool(attachments)
    msg: Union[EmailMessage, MIMEMultipart]
    if multipart_mode:
        msg = MIMEMultipart("mixed")
    else:
        msg = EmailMessage()

    # Set the From header
    msg["From"] = str(reply_to)

    # Set the To header
    to_recipients = [original_email.from_]
    if reply_all and original_email.to:
        # Add original recipients excluding the sender
        to_recipients.extend(
            [
                recipient
                for recipient in original_email.to
                if recipient.address != reply_to.address
            ]
        )

    msg["To"] = ", ".join(str(recipient) for recipient in to_recipients)

    # Set the CC header if applicable
    cc_recipients = []
    if cc:
        cc_recipients.extend(cc)
    elif reply_all and original_email.cc:
        cc_recipients.extend(
            [
                recipient
                for recipient in original_email.cc
                if recipient.address != reply_to.address
            ]
        )

    if cc_recipients:
        msg["Cc"] = ", ".join(str(recipient) for recipient in cc_recipients)

    # Set the subject
    if subject:
        msg["Subject"] = subject
    else:
        # Add "Re: " prefix if not already present
        # Unfold any MIME-folded subject (strip CR/LF and collapse whitespace)
        original_subject = " ".join(original_email.subject.split())
        if not original_subject.startswith("Re:"):
            msg["Subject"] = f"Re: {original_subject}"
        else:
            msg["Subject"] = original_subject

    # Set references for threading
    references = []
    if "References" in original_email.headers:
        # Unfold any wrapped header values (remove CR/LF/extra whitespace)
        refs_value = " ".join(original_email.headers["References"].split())
        references.append(refs_value)
    if original_email.message_id:
        msg_id = " ".join(original_email.message_id.split())
        references.append(msg_id)

    if references:
        msg["References"] = " ".join(references)

    # Set In-Reply-To header
    if original_email.message_id:
        msg["In-Reply-To"] = " ".join(original_email.message_id.split())

    # Prepare content
    if html_body:
        # Create multipart/alternative for text and HTML
        alternative = MIMEMultipart("alternative")

        # Add plain text part
        plain_text = body
        if original_email.content.text:
            # Quote original plain text
            quoted_original = "\n".join(
                f"> {line}" for line in original_email.content.text.split("\n")
            )
            plain_text += f"\n\nOn {email.utils.format_datetime(original_email.date or datetime.now())}, {original_email.from_} wrote:\n{quoted_original}"

        text_part = MIMEText(plain_text, "plain", "utf-8")
        alternative.attach(text_part)

        # Add HTML part
        html_content = html_body
        if original_email.content.html:
            # Add original HTML with a divider
            html_content += (
                f'\n<div style="border-top: 1px solid #ccc; margin-top: 20px; padding-top: 10px;">'
                f"\n<p>On {email.utils.format_datetime(original_email.date or datetime.now())}, {original_email.from_} wrote:</p>"
                f'\n<blockquote style="margin: 0 0 0 .8ex; border-left: 1px solid #ccc; padding-left: 1ex;">'
                f"\n{original_email.content.html}"
                f"\n</blockquote>"
                f"\n</div>"
            )
        else:
            # Convert plain text to HTML for quoting
            original_text = original_email.content.get_best_content()
            if original_text:
                escaped_text = (
                    original_text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                escaped_text = escaped_text.replace("\n", "<br>")
                html_content += (
                    f'\n<div style="border-top: 1px solid #ccc; margin-top: 20px; padding-top: 10px;">'
                    f"\n<p>On {email.utils.format_datetime(original_email.date or datetime.now())}, {original_email.from_} wrote:</p>"
                    f'\n<blockquote style="margin: 0 0 0 .8ex; border-left: 1px solid #ccc; padding-left: 1ex;">'
                    f"\n{escaped_text}"
                    f"\n</blockquote>"
                    f"\n</div>"
                )

        html_part = MIMEText(html_content, "html", "utf-8")
        alternative.attach(html_part)

        # Attach the alternative part to the message
        assert isinstance(msg, MIMEMultipart)
        msg.attach(alternative)
    else:
        # Plain text body (with or without attachments)
        plain_text = body
        if original_email.content.text:
            # Quote original plain text
            quoted_original = "\n".join(
                f"> {line}" for line in original_email.content.text.split("\n")
            )
            plain_text += f"\n\nOn {email.utils.format_datetime(original_email.date or datetime.now())}, {original_email.from_} wrote:\n{quoted_original}"

        if multipart_mode:
            assert isinstance(msg, MIMEMultipart)
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        else:
            assert isinstance(msg, EmailMessage)
            msg.set_content(plain_text)

    if attachments:
        assert isinstance(msg, MIMEMultipart)
        for path in attachments:
            _attach_file(msg, path)

    # Add Date header
    msg["Date"] = email.utils.formatdate(localtime=True)

    return msg


def _find_reply_from_address(email_obj: Email, my_address: str) -> EmailAddress:
    """Find the best reply-from address by matching the account address.

    Searches the To and CC fields of *email_obj* for an address matching
    *my_address* (case-insensitive).  Falls back to the first To recipient
    or, if there are none, constructs an ``EmailAddress`` from *my_address*.
    """
    my_lower = my_address.lower()
    for recipient in (email_obj.to or []) + (email_obj.cc or []):
        if recipient.address and recipient.address.lower() == my_lower:
            return recipient
    if email_obj.to:
        return email_obj.to[0]
    return EmailAddress(name="", address=my_address)


def compose_and_save_reply_draft(
    client: Any,
    folder: str,
    uid: int,
    reply_body: str,
    reply_all: bool = False,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    body_html: Optional[str] = None,
    attachments: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch an email, compose a reply, and save it as a draft.

    Args:
        client: An ``ImapClient`` instance (duck-typed to avoid circular import).
        folder: IMAP folder containing the original email.
        uid: UID of the original email.
        reply_body: Plain-text reply body.
        reply_all: Reply to all recipients.
        cc: Optional CC addresses as strings.
        bcc: Optional BCC addresses as strings.
        body_html: Optional HTML reply body.
        attachments: Optional list of filesystem paths to attach to the draft.

    Returns:
        Dict with keys ``status``, ``message``, ``draft_uid``, ``draft_folder``.
    """
    result: Dict[str, Any] = {
        "status": "error",
        "message": "",
        "draft_uid": None,
        "draft_folder": None,
    }

    try:
        email_obj = client.fetch_email(uid, folder=folder)
        if not email_obj:
            result["message"] = f"Email with UID {uid} not found in folder {folder}"
            return result

        reply_from = _find_reply_from_address(email_obj, client.config.username)

        cc_addresses = None
        if cc:
            cc_addresses = [EmailAddress.parse(addr) for addr in cc]

        mime_message = create_reply_mime(
            original_email=email_obj,
            reply_to=reply_from,
            body=reply_body,
            reply_all=reply_all,
            cc=cc_addresses,
            html_body=body_html,
            attachments=attachments,
        )

        if bcc:
            mime_message["Bcc"] = ", ".join(bcc)

        draft_uid = client.save_draft_mime(mime_message)
        if draft_uid:
            drafts_folder = client._get_drafts_folder()
            result["status"] = "success"
            result["message"] = "Draft reply saved"
            result["draft_uid"] = draft_uid
            result["draft_folder"] = drafts_folder
        else:
            result["message"] = "Failed to save draft"
    except Exception as e:
        logger.error(f"Error drafting reply: {e}")
        result["message"] = f"Error: {e}"

    return result
