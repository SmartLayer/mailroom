"""IMAP client implementation."""

import email
import logging
import re
import shlex
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

import imapclient  # type: ignore[import-untyped]

from mailroom.config import AccountConfig, ImapConfig
from mailroom.models import Email
from mailroom.oauth2 import get_access_token
from mailroom.query_parser import parse_query

if TYPE_CHECKING:
    from mailroom.local_cache import MuBackend

logger = logging.getLogger(__name__)


class ImapClient:
    """IMAP client for interacting with email servers."""

    def __init__(
        self,
        config: ImapConfig,
        allowed_folders: Optional[List[str]] = None,
        local_cache: Optional["MuBackend"] = None,
        account_cfg: Optional[AccountConfig] = None,
    ):
        """Initialize IMAP client.

        Args:
            config: IMAP configuration
            allowed_folders: List of allowed folders (None means all folders)
            local_cache: Optional ``MuBackend`` for serving search calls
                from a local mu index.  When ``None``, all searches are
                served by IMAP.
            account_cfg: Optional account configuration carrying the
                per-account ``maildir`` opt-in for the local cache.
                When ``None`` or its ``maildir`` is unset, the local
                cache is bypassed even if ``local_cache`` is provided.
        """
        self.config = config
        self.allowed_folders = set(allowed_folders) if allowed_folders else None
        self.local_cache = local_cache
        self.account_cfg = account_cfg
        self.client: Optional[imapclient.IMAPClient] = None
        self.folder_cache: Dict[str, List[str]] = {}
        self.connected = False
        self.count_cache: Dict[str, Dict[str, Tuple[int, datetime]]] = (
            {}
        )  # Cache for message counts
        self.current_folder: Optional[str] = None  # Store the currently selected folder
        self.folder_message_counts: Dict[str, int] = (
            {}
        )  # Cache for folder message counts
        self.last_activity: Optional[datetime] = (
            None  # Track last successful IMAP operation
        )

    def _client_or_raise(self) -> imapclient.IMAPClient:
        """Return the underlying IMAPClient, raising if not connected."""
        if self.client is None:
            raise ConnectionError("Not connected to IMAP server")
        return self.client

    def connect(self) -> None:
        """Connect to IMAP server.

        Raises:
            ConnectionError: If connection fails
        """
        try:
            self.client = imapclient.IMAPClient(
                self.config.host,
                port=self.config.port,
                ssl=self.config.use_ssl,
                timeout=10,  # 10 second connection timeout
            )

            # Use OAuth2 for Gmail if configured
            if self.config.requires_oauth2:
                logger.info(f"Using OAuth2 authentication for {self.config.host}")

                # Get fresh access token
                if not self.config.oauth2:
                    raise ValueError("OAuth2 configuration is required for Gmail")

                access_token, _ = get_access_token(self.config.oauth2)

                # Authenticate with XOAUTH2
                # Use the oauth_login method which properly formats the XOAUTH2 string
                self.client.oauth2_login(self.config.username, access_token)
            else:
                # Standard password authentication
                if not self.config.password:
                    raise ValueError("Password is required for authentication")

                self.client.login(self.config.username, self.config.password)

            self.connected = True
            self.last_activity = datetime.now()  # Track connection time
            logger.info(f"Connected to IMAP server {self.config.host}")
        except Exception as e:
            self.connected = False
            logger.error(f"Failed to connect to IMAP server: {e}")
            raise ConnectionError(f"Failed to connect to IMAP server: {e}")

    def disconnect(self) -> None:
        """Disconnect from IMAP server."""
        if self.client:
            try:
                self.client.logout()
            except Exception as e:
                logger.warning(f"Error during IMAP logout: {e}")
            finally:
                self.client = None
                self.connected = False
                self.last_activity = None  # Reset activity tracking
                logger.info("Disconnected from IMAP server")

    def _is_connection_stale(self) -> bool:
        """Check if connection is likely stale based on idle timeout.

        Returns:
            True if connection should be considered stale
        """
        idle_timeout = self.config.idle_timeout

        # -1 means never consider stale (legacy behaviour)
        if idle_timeout < 0:
            return False

        # 0 means always stale (close after each operation)
        if idle_timeout == 0:
            return True

        # Check actual idle time
        if self.last_activity is None:
            return True

        idle_seconds = (datetime.now() - self.last_activity).total_seconds()
        return idle_seconds > idle_timeout

    def _verify_connection(self) -> bool:
        """Verify connection is alive using NOOP command.

        Returns:
            True if connection is alive, False otherwise
        """
        if not self.client or not self.connected:
            return False

        try:
            self.client.noop()
            return True
        except Exception as e:
            logger.warning(f"Connection verification failed: {e}")
            return False

    def _update_activity(self) -> None:
        """Update last activity timestamp after successful operation."""
        self.last_activity = datetime.now()

    def ensure_connected(self) -> None:
        """Ensure connection is available and healthy.

        This method implements the connection lifecycle strategy:
        - idle_timeout = 0: Reconnect before every operation (stateless mode)
        - idle_timeout > 0: Reconnect if idle longer than timeout
        - idle_timeout = -1: Never proactively reconnect (legacy mode)

        Raises:
            ConnectionError: If connection cannot be established
        """
        idle_timeout = self.config.idle_timeout

        # Case 1: Not connected at all - must connect
        if not self.connected or not self.client:
            self.connect()
            return

        # Case 2: Stateless mode (idle_timeout = 0) - always reconnect
        if idle_timeout == 0:
            logger.debug("Stateless mode: reconnecting for operation")
            self.disconnect()
            self.connect()
            return

        # Case 3: Legacy mode (idle_timeout = -1) - never proactively reconnect
        if idle_timeout < 0:
            return

        # Case 4: Connection might be stale - check and reconnect if needed
        if self._is_connection_stale():
            logger.info(f"Connection idle for >{idle_timeout}s, reconnecting...")
            self.disconnect()
            self.connect()
            return

        # Case 5: Connection within timeout - optionally verify with NOOP
        if self.config.verify_with_noop:
            if not self._verify_connection():
                logger.warning("Connection verification failed, reconnecting...")
                self.disconnect()
                self.connect()

    def get_capabilities(self) -> List[str]:
        """Get IMAP server capabilities.

        Returns:
            List of server capabilities

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()
        raw_capabilities = self._client_or_raise().capabilities()

        # Convert byte strings to regular strings and normalize case
        capabilities = []
        for cap in raw_capabilities:
            if isinstance(cap, bytes):
                cap = cap.decode("utf-8")
            capabilities.append(cap.upper())

        self._update_activity()
        return capabilities

    def list_folders(self, refresh: bool = False) -> List[str]:
        """List available folders.

        Args:
            refresh: Force refresh folder list cache

        Returns:
            List of folder names

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()

        # Check cache first
        if not refresh and self.folder_cache:
            return list(self.folder_cache.keys())

        # Get folders from server
        folders = []
        for flags, delimiter, name in self._client_or_raise().list_folders():
            if isinstance(name, bytes):
                # Convert bytes to string if necessary
                name = name.decode("utf-8")

            # Skip non-selectable folders (e.g. Gmail's '[Gmail]' parent has
            # \Noselect; SELECTing it returns NONEXISTENT).
            if b"\\Noselect" in flags or b"\\NonExistent" in flags:
                continue

            # Filter folders if allowed_folders is set
            if self.allowed_folders is not None and name not in self.allowed_folders:
                continue

            folders.append(name)
            self.folder_cache[name] = flags

        self._update_activity()
        logger.debug(f"Listed {len(folders)} folders")
        return folders

    def find_special_use_folder(self, role: bytes) -> Optional[str]:
        """Return the folder marked with the given SPECIAL-USE flag.

        IMAP SPECIAL-USE (RFC 6154) advertises folders by role:
        ``\\All``, ``\\Sent``, ``\\Drafts``, ``\\Trash``, ``\\Junk``,
        ``\\Flagged``, ``\\Important``. Gmail tags ``[Gmail]/All Mail`` with
        ``\\All``; Fastmail uses ``Archive``; etc.

        Args:
            role: The SPECIAL-USE flag as bytes, e.g. ``b'\\\\All'``.

        Returns:
            The folder name, or ``None`` if no folder advertises that role.
        """
        if not self.folder_cache:
            self.list_folders()
        for name, flags in self.folder_cache.items():
            if role in flags:
                return name
        return None

    def _is_folder_allowed(self, folder: str) -> bool:
        """Check if a folder is allowed.

        Args:
            folder: Folder to check

        Returns:
            True if folder is allowed, False otherwise
        """
        # If no allowed_folders specified, all folders are allowed
        if self.allowed_folders is None:
            return True

        # If allowed_folders is specified, check if folder is in it
        return folder in self.allowed_folders

    def select_folder(self, folder: str, readonly: bool = False) -> Dict:
        """Select folder on IMAP server.

        Args:
            folder: Folder to select
            readonly: If True, select folder in read-only mode

        Returns:
            Dictionary with folder information

        Raises:
            ValueError: If folder is not allowed
            ConnectionError: If connection error occurs
        """
        # Make sure the folder is allowed
        if not self._is_folder_allowed(folder):
            raise ValueError(f"Folder '{folder}' is not allowed")

        self.ensure_connected()

        try:
            result: Dict[Any, Any] = self._client_or_raise().select_folder(
                folder, readonly=readonly
            )
            self.current_folder = folder
            self._update_activity()
            logger.debug(f"Selected folder '{folder}'")
            return result
        except imapclient.IMAPClient.Error as e:
            logger.error(f"Error selecting folder {folder}: {e}")
            raise ConnectionError(f"Failed to select folder {folder}: {e}")

    def search(
        self,
        criteria: Union[str, List[Any], Tuple[Any, ...]],
        folder: str = "INBOX",
        charset: Optional[str] = None,
    ) -> List[int]:
        """Search for messages.

        Args:
            criteria: Search criteria
            folder: Folder to search in
            charset: Character set for search criteria

        Returns:
            List of message UIDs

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()
        self.select_folder(folder, readonly=True)

        resolved_criteria: Union[str, List[Any], Tuple[Any, ...]] = criteria
        if isinstance(criteria, str):
            # Predefined criteria strings
            criteria_map: Dict[str, Union[str, List[Any]]] = {
                "all": "ALL",
                "unseen": "UNSEEN",
                "seen": "SEEN",
                "answered": "ANSWERED",
                "unanswered": "UNANSWERED",
                "deleted": "DELETED",
                "undeleted": "UNDELETED",
                "flagged": "FLAGGED",
                "unflagged": "UNFLAGGED",
                "recent": "RECENT",
                "today": ["SINCE", datetime.now().date()],
                "yesterday": [
                    "SINCE",
                    (datetime.now() - timedelta(days=1)).date(),
                    "BEFORE",
                    datetime.now().date(),
                ],
                "week": ["SINCE", (datetime.now() - timedelta(days=7)).date()],
                "month": ["SINCE", (datetime.now() - timedelta(days=30)).date()],
            }

            if criteria.lower() in criteria_map:
                resolved_criteria = criteria_map[criteria.lower()]

        results = self._client_or_raise().search(resolved_criteria, charset=charset)
        self._update_activity()
        logger.debug(f"Search returned {len(results)} results")
        return list(results)

    def fetch_email(self, uid: int, folder: str = "INBOX") -> Optional[Email]:
        """Fetch a single email by UID.

        Args:
            uid: Email UID
            folder: Folder to fetch from

        Returns:
            Email object or None if not found

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()
        self.select_folder(folder, readonly=True)

        # Fetch message data with BODY.PEEK[] to get all parts including headers
        # Using BODY.PEEK[] instead of RFC822 to avoid setting the \Seen flag
        result = self._client_or_raise().fetch([uid], ["BODY.PEEK[]", "FLAGS"])

        if not result or uid not in result:
            logger.warning(f"Message with UID {uid} not found in folder {folder}")
            return None

        # Parse message
        message_data = result[uid]
        raw_message = message_data[b"BODY[]"]
        flags = message_data[b"FLAGS"]

        # Convert flags to strings
        str_flags = [f.decode("utf-8") if isinstance(f, bytes) else f for f in flags]

        # Parse email
        message = email.message_from_bytes(raw_message)
        email_obj = Email.from_message(message, uid=uid, folder=folder)
        email_obj.flags = str_flags

        self._update_activity()
        return email_obj

    def fetch_emails(
        self,
        uids: List[int],
        folder: str = "INBOX",
        limit: Optional[int] = None,
    ) -> Dict[int, Email]:
        """Fetch multiple emails by UIDs.

        Args:
            uids: List of email UIDs
            folder: Folder to fetch from
            limit: Maximum number of emails to fetch

        Returns:
            Dictionary mapping UIDs to Email objects

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()
        self.select_folder(folder, readonly=True)

        # Apply limit if specified
        if limit is not None and limit > 0:
            uids = uids[:limit]

        # Fetch message data
        if not uids:
            return {}

        # Use BODY.PEEK[] to get full message including all parts and headers
        result = self._client_or_raise().fetch(uids, ["BODY.PEEK[]", "FLAGS"])

        # Parse emails
        emails = {}
        for uid, message_data in result.items():
            raw_message = message_data[b"BODY[]"]
            flags = message_data[b"FLAGS"]

            # Convert flags to strings
            str_flags = [
                f.decode("utf-8") if isinstance(f, bytes) else f for f in flags
            ]

            # Parse email
            message = email.message_from_bytes(raw_message)
            email_obj = Email.from_message(message, uid=uid, folder=folder)
            email_obj.flags = str_flags

            emails[uid] = email_obj

        self._update_activity()
        return emails

    def fetch_thread(self, uid: int, folder: str = "INBOX") -> List[Email]:
        """Fetch all emails in a thread.

        This method retrieves the initial email identified by the UID, and then
        searches for all related emails that belong to the same thread using
        Message-ID, In-Reply-To, References headers, and Subject matching as a fallback.

        Args:
            uid: UID of any email in the thread
            folder: Folder to fetch from

        Returns:
            List of Email objects in the thread, sorted chronologically

        Raises:
            ConnectionError: If not connected and connection fails
            ValueError: If the initial email cannot be found
        """
        self.ensure_connected()
        self.select_folder(folder, readonly=True)

        # Fetch the initial email
        initial_email = self.fetch_email(uid, folder)
        if not initial_email:
            raise ValueError(
                f"Initial email with UID {uid} not found in folder {folder}"
            )

        # Get thread identifiers from the initial email
        message_id = initial_email.headers.get("Message-ID", "")
        subject = initial_email.subject

        # Strip "Re:", "Fwd:", etc. from the subject for better matching
        clean_subject = re.sub(
            r"^(?:Re|Fwd|Fw|FWD|RE|FW):\s*", "", subject, flags=re.IGNORECASE
        )

        # Set to store all UIDs that belong to the thread
        thread_uids = {uid}

        # Search for emails with this Message-ID in the References or In-Reply-To headers
        if message_id:
            # Look for emails that reference this message ID
            references_query = f'HEADER References "{message_id}"'
            try:
                references_results = self.search(references_query, folder)
                thread_uids.update(references_results)
            except Exception as e:
                logger.warning(f"Error searching for References: {e}")

            # Look for direct replies to this message
            inreplyto_query = f'HEADER In-Reply-To "{message_id}"'
            try:
                inreplyto_results = self.search(inreplyto_query, folder)
                thread_uids.update(inreplyto_results)
            except Exception as e:
                logger.warning(f"Error searching for In-Reply-To: {e}")

            # If the initial email has References or In-Reply-To, fetch those messages too
            initial_references = initial_email.headers.get("References", "")
            initial_inreplyto = initial_email.headers.get("In-Reply-To", "")

            # Extract all message IDs from the References header
            if initial_references:
                for ref_id in re.findall(r"<[^>]+>", initial_references):
                    query = f'HEADER Message-ID "{ref_id}"'
                    try:
                        results = self.search(query, folder)
                        thread_uids.update(results)
                    except Exception as e:
                        logger.warning(
                            f"Error searching for Referenced message {ref_id}: {e}"
                        )

            # Look for the message that this is a reply to
            if initial_inreplyto:
                query = f'HEADER Message-ID "{initial_inreplyto}"'
                try:
                    results = self.search(query, folder)
                    thread_uids.update(results)
                except Exception as e:
                    logger.warning(f"Error searching for In-Reply-To message: {e}")

        # If we still have only the initial email or a small thread, try subject-based matching
        if len(thread_uids) <= 2 and clean_subject:
            # Look for emails with the same or related subject (Re: Subject)
            # This is a fallback for email clients that don't properly use References/In-Reply-To
            subject_query = f'SUBJECT "{clean_subject}"'
            try:
                subject_results = self.search(subject_query, folder)

                # Filter out emails that are unlikely to be part of the thread
                # For example, avoid including all emails with a common subject like "Hello"
                if len(subject_results) < 20:  # Set a reasonable limit
                    thread_uids.update(subject_results)
                else:
                    # If there are too many results, try a more strict approach
                    # Look for exact subject match or common Re: pattern
                    strict_matches = []
                    strict_subjects = [
                        clean_subject,
                        f"Re: {clean_subject}",
                        f"RE: {clean_subject}",
                        f"Fwd: {clean_subject}",
                        f"FWD: {clean_subject}",
                        f"Fw: {clean_subject}",
                        f"FW: {clean_subject}",
                    ]

                    # Fetch subjects for all candidate emails
                    candidate_emails = self.fetch_emails(subject_results, folder)
                    for candidate_uid, candidate_email in candidate_emails.items():
                        if candidate_email.subject in strict_subjects:
                            strict_matches.append(candidate_uid)

                    thread_uids.update(strict_matches)
            except Exception as e:
                logger.warning(f"Error searching by subject: {e}")

        # Fetch all discovered thread emails
        thread_emails = self.fetch_emails(list(thread_uids), folder)

        # Sort emails by date (chronologically)
        sorted_emails = sorted(
            thread_emails.values(), key=lambda e: e.date if e.date else datetime.min
        )

        self._update_activity()
        return sorted_emails

    def mark_email(
        self,
        uid: int,
        folder: str,
        flag: str,
        value: bool = True,
    ) -> bool:
        """Mark email with flag.

        Args:
            uid: Email UID
            folder: Folder containing the email
            flag: Flag to set or remove
            value: True to set, False to remove

        Returns:
            True if successful

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()
        self.select_folder(folder)

        try:
            client = self._client_or_raise()
            if value:
                client.add_flags([uid], flag)
                logger.debug(f"Added flag {flag} to message {uid}")
            else:
                client.remove_flags([uid], flag)
                logger.debug(f"Removed flag {flag} from message {uid}")
            self._update_activity()
            return True
        except Exception as e:
            logger.error(f"Failed to mark email: {e}")
            return False

    def move_email(self, uid: int, source_folder: str, target_folder: str) -> bool:
        """Move email to another folder.

        Args:
            uid: Email UID
            source_folder: Source folder
            target_folder: Target folder

        Returns:
            True if successful

        Raises:
            ConnectionError: If not connected and connection fails
            ValueError: If folder is not allowed
        """
        self.ensure_connected()

        # Check if folders are allowed
        if self.allowed_folders is not None:
            if source_folder not in self.allowed_folders:
                raise ValueError(f"Source folder '{source_folder}' is not allowed")
            if target_folder not in self.allowed_folders:
                raise ValueError(f"Target folder '{target_folder}' is not allowed")

        # Select source folder
        self.select_folder(source_folder)

        try:
            # Move email (copy + delete)
            client = self._client_or_raise()
            client.copy([uid], target_folder)
            client.add_flags([uid], r"\Deleted")
            client.expunge()
            self._update_activity()
            logger.debug(f"Moved message {uid} from {source_folder} to {target_folder}")
            return True
        except Exception as e:
            logger.error(f"Failed to move email: {e}")
            return False

    def delete_email(self, uid: int, folder: str) -> bool:
        """Delete email.

        Args:
            uid: Email UID
            folder: Folder containing the email

        Returns:
            True if successful

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()
        self.select_folder(folder)

        try:
            client = self._client_or_raise()
            client.add_flags([uid], r"\Deleted")
            client.expunge()
            self._update_activity()
            logger.debug(f"Deleted message {uid} from {folder}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete email: {e}")
            return False

    def process_email_action(
        self,
        uid: int,
        folder: str,
        action: str,
        target_folder: Optional[str] = None,
    ) -> str:
        """Execute a high-level email action by name.

        Args:
            uid: Email UID
            folder: Folder containing the email
            action: One of move, read, unread, flag, unflag, delete
            target_folder: Required when *action* is ``move``

        Returns:
            Human-readable result message

        Raises:
            ValueError: If *action* is unknown or *target_folder* missing for move
        """
        action_l = action.lower()
        if action_l == "move":
            if not target_folder:
                raise ValueError("target_folder is required for move action")
            self.move_email(uid, folder, target_folder)
            return f"Email moved from {folder} to {target_folder}"
        elif action_l == "read":
            self.mark_email(uid, folder, r"\Seen", True)
            return "Email marked as read"
        elif action_l == "unread":
            self.mark_email(uid, folder, r"\Seen", False)
            return "Email marked as unread"
        elif action_l == "flag":
            self.mark_email(uid, folder, r"\Flagged", True)
            return "Email flagged"
        elif action_l == "unflag":
            self.mark_email(uid, folder, r"\Flagged", False)
            return "Email unflagged"
        elif action_l == "delete":
            self.delete_email(uid, folder)
            return "Email deleted"
        else:
            raise ValueError(
                f"Unknown action '{action}'. "
                "Valid: move, read, unread, flag, unflag, delete"
            )

    def _get_drafts_folder(self) -> str:
        """Get the drafts folder name for the current server.

        Returns:
            The name of the drafts folder, or "INBOX" as fallback
        """
        self.ensure_connected()
        folders = self.list_folders(refresh=True)

        # Check for Gmail's special folders structure
        if self.config.host and "gmail" in self.config.host.lower():
            gmail_drafts = [f for f in folders if f.lower().endswith("/drafts")]
            if gmail_drafts:
                logger.debug(f"Using Gmail drafts folder: {gmail_drafts[0]}")
                return gmail_drafts[0]

        # Look for standard drafts folder names (case-insensitive)
        drafts_folder_names = [
            "Drafts",
            "Draft",
            "Brouillons",
            "Borradores",
            "Entwürfe",
        ]
        for folder in folders:
            if folder.lower() in [name.lower() for name in drafts_folder_names]:
                logger.debug(f"Using drafts folder: {folder}")
                return folder

        # Fallback to INBOX if no drafts folder found
        logger.warning("No drafts folder found, using INBOX as fallback")
        return "INBOX"

    def save_draft_mime(self, message: Any) -> Optional[int]:
        """Save a MIME message as a draft.

        Args:
            message: email.message.Message object to save as draft

        Returns:
            UID of the saved draft if available, None otherwise

        Raises:
            ConnectionError: If not connected and connection fails
        """
        self.ensure_connected()

        # Get the drafts folder
        drafts_folder = self._get_drafts_folder()

        try:
            # Convert message to bytes if it's not already
            if hasattr(message, "as_bytes"):
                message_bytes = message.as_bytes()
            else:
                message_bytes = message.as_string().encode("utf-8")

            # Save the draft with Draft flag
            response = self._client_or_raise().append(
                drafts_folder, message_bytes, flags=(r"\Draft",)
            )

            # Try to extract the UID from the response
            uid = None
            if isinstance(response, bytes) and b"APPENDUID" in response:
                # Parse the APPENDUID response (format: [APPENDUID <uidvalidity> <uid>])
                try:
                    # Use a more robust parsing approach
                    match = re.search(rb"APPENDUID\s+\d+\s+(\d+)", response)
                    if match:
                        uid = int(match.group(1))
                        logger.debug(f"Draft saved with UID: {uid}")
                except (IndexError, ValueError) as e:
                    logger.warning(f"Could not parse UID from response: {e}")

            if uid is None:
                logger.warning(
                    f"Could not extract UID from append response: {response}"
                )

            self._update_activity()
            return uid

        except Exception as e:
            logger.error(f"Failed to save draft: {e}")
            return None

    def fetch_raw(
        self,
        uid: int,
        folder: str = "INBOX",
    ) -> Optional[Dict[str, Any]]:
        """Fetch raw RFC 822 bytes, flags, and INTERNALDATE for a message.

        Args:
            uid: Email UID
            folder: Folder containing the email

        Returns:
            Dict with keys 'raw' (bytes), 'flags' (tuple), 'date' (datetime),
            'subject' (str) or None if not found.
        """
        self.ensure_connected()
        self.select_folder(folder, readonly=True)

        result = self._client_or_raise().fetch(
            [uid], ["BODY.PEEK[]", "FLAGS", "INTERNALDATE"]
        )

        if not result or uid not in result:
            logger.warning(f"Message with UID {uid} not found in folder {folder}")
            return None

        data = result[uid]
        raw_message = data[b"BODY[]"]
        flags = data[b"FLAGS"]
        internal_date = data.get(b"INTERNALDATE")

        # Extract subject for logging/display
        msg = email.message_from_bytes(raw_message)
        subject = msg.get("Subject", "(no subject)")

        self._update_activity()
        return {
            "raw": raw_message,
            "flags": flags,
            "date": internal_date,
            "subject": subject,
        }

    def append_raw(
        self,
        folder: str,
        raw_message: bytes,
        flags: tuple = (),
        msg_time: Optional[datetime] = None,
    ) -> Optional[int]:
        """Append raw RFC 822 bytes to a folder.

        Args:
            folder: Target folder.
            raw_message: Complete RFC 822 message as bytes.
            flags: IMAP flags to set (e.g. (r'\\Seen', r'\\Flagged')).
            msg_time: INTERNALDATE for the message. If None, server uses
                current time.

        Returns:
            UID of the appended message if server supports APPENDUID,
            else None.
        """
        self.ensure_connected()

        try:
            response = self._client_or_raise().append(
                folder, raw_message, flags=flags, msg_time=msg_time
            )

            uid = None
            if isinstance(response, bytes) and b"APPENDUID" in response:
                try:
                    match = re.search(rb"APPENDUID\s+\d+\s+(\d+)", response)
                    if match:
                        uid = int(match.group(1))
                        logger.debug(f"Message appended to {folder} with UID: {uid}")
                except (IndexError, ValueError) as e:
                    logger.warning(f"Could not parse UID from response: {e}")

            if uid is None:
                logger.warning(
                    f"Could not extract UID from append response: {response}"
                )

            self._update_activity()
            return uid

        except Exception as e:
            logger.error(f"Failed to append message to {folder}: {e}")
            raise

    # Header search prefixes whose presence triggers the Gmail X-GM-RAW
    # dispatch.  Standard IMAP SEARCH FROM/TO/CC/BCC against Gmail's All
    # Mail empirically does not filter by header content for values that
    # contain "@"/"."; X-GM-RAW evaluates the query the way Gmail's web UI
    # does and produces the expected filter (issue #17).
    _GMAIL_RAW_TRIGGER_PREFIXES = ("from:", "to:", "cc:", "bcc:")

    def _build_search_spec(self, query: str) -> Union[str, List[Any]]:
        """Translate a user query into IMAP search criteria.

        For Gmail accounts the function returns ``[b"X-GM-RAW", query]`` when
        the query contains a header search prefix, so Gmail evaluates the
        query with web-UI semantics.  All other queries (and the ``imap:``
        raw escape) go through the standard ``parse_query`` emitter.

        Args:
            query: Raw user query string.

        Returns:
            A criteria value suitable for ``imapclient.IMAPClient.search``.

        Raises:
            ValueError: Propagated from ``parse_query`` on malformed queries.
        """
        if self._should_use_gmail_raw(query):
            return [b"X-GM-RAW", query.strip()]
        return parse_query(query)

    def _should_use_gmail_raw(self, query: str) -> bool:
        """Decide whether a query should be sent via ``X-GM-RAW``.

        Returns ``True`` only when the server is Gmail, the query is not a
        raw IMAP escape, and at least one whitespace-separated token starts
        with a header search prefix (``from:``/``to:``/``cc:``/``bcc:``).
        Pure flag/date queries continue to use standard IMAP search so
        non-Gmail capability assumptions (no ``X-GM-EXT-1`` requirement)
        and existing tests remain unchanged.

        Args:
            query: Raw user query string.

        Returns:
            ``True`` when the Gmail X-GM-RAW dispatch should be used.
        """
        host = (self.config.host or "").lower()
        if "gmail" not in host:
            return False
        stripped = query.strip()
        if stripped.lower().startswith("imap:"):
            return False
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            tokens = stripped.split()
        for tok in tokens:
            tok_lower = tok.lower()
            if any(tok_lower.startswith(p) for p in self._GMAIL_RAW_TRIGGER_PREFIXES):
                return True
            # Also catch negated prefixes like -to:foo.
            if tok_lower.startswith("-") and any(
                tok_lower[1:].startswith(p) for p in self._GMAIL_RAW_TRIGGER_PREFIXES
            ):
                return True
        return False

    def search_emails(
        self,
        query: str,
        folder: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """High-level email search across one or all folders.

        Uses Gmail-style query syntax::

            from:alice subject:invoice is:unread after:2025-03-01
            meeting notes                     # bare words → TEXT search
            imap:OR TEXT foo SUBJECT bar       # raw IMAP passthrough

        On Gmail accounts (host contains ``gmail``), the query is dispatched
        through Gmail's ``X-GM-RAW`` extension whenever it contains a header
        search prefix (``from:``/``to:``/``cc:``/``bcc:``).  Standard IMAP
        ``SEARCH TO foo@example.com`` against Gmail's All Mail folder
        empirically matches every recent message rather than filtering by
        the To header (issue #17); ``X-GM-RAW`` evaluates the query with
        the same semantics as Gmail's web UI and filters correctly.
        Queries without header prefixes (pure flag/date searches) and the
        ``imap:`` raw escape continue to use standard IMAP search.

        When the client was constructed with a ``local_cache`` backend
        and an opted-in ``account_cfg``, eligible calls are served from
        the local mu index instead of IMAP.  Folder-scoped searches
        always go to IMAP (see ``fell_back_reason="folder_scope"``).

        Args:
            query: Gmail-style search query string.
            folder: Folder to search (``None`` searches all folders).
            limit: Maximum number of results.

        Returns:
            A dict ``{"results": [...], "provenance": {...}}``.  Each
            result carries either an ``uid`` (IMAP) or ``message_id``
            and ``path`` (local cache); both shapes share ``folder``,
            ``from``, ``to``, ``subject``, ``date``, ``flags``, and
            ``has_attachments``.  ``provenance`` carries ``source``
            (``"local"`` or ``"remote"``), ``indexed_at`` (ISO 8601 or
            ``None``), and ``fell_back_reason`` (``None`` or one of
            ``"folder_scope"``, ``"mu_missing"``, ``"db_missing"``,
            ``"stale"``, ``"untranslatable"``, ``"exception"``).

        Raises:
            ValueError: On malformed queries.
        """
        local_results, fell_back_reason = self._try_local_cache_search(
            query, folder, limit
        )
        if local_results is not None:
            return {
                "results": local_results,
                "provenance": {
                    "source": "local",
                    "indexed_at": (
                        self.local_cache.index_mtime_iso()
                        if self.local_cache is not None
                        else None
                    ),
                    "fell_back_reason": None,
                },
            }
        return {
            "results": self._search_emails_imap(query, folder, limit),
            "provenance": {
                "source": "remote",
                "indexed_at": None,
                "fell_back_reason": fell_back_reason,
            },
        }

    def _try_local_cache_search(
        self, query: str, folder: Optional[str], limit: int
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Attempt to serve a search from the local cache.

        Returns:
            ``(results, None)`` on a successful local-cache hit, or
            ``(None, reason)`` when the local cache cannot serve the
            call.  ``reason`` is ``None`` when the account is not opted
            into the local cache (the wrapped shape still applies, but
            no fallback is reported); otherwise it is one of the tags
            from the ``provenance.fell_back_reason`` vocabulary.
        """
        # Late import to avoid a circular dependency.
        from mailroom.local_cache import MuFailure
        from mailroom.query_parser import UntranslatableQuery

        if self.local_cache is None or self.account_cfg is None:
            return None, None
        if not self.account_cfg.maildir:
            return None, None
        if folder is not None:
            return None, "folder_scope"
        eligibility = self.local_cache.is_eligible(self.account_cfg)
        if not eligibility.eligible:
            return None, eligibility.reason
        try:
            results = self.local_cache.search(self.account_cfg, query, limit)
        except UntranslatableQuery:
            return None, "untranslatable"
        except (MuFailure, ValueError) as e:
            logger.warning(f"Local cache search failed, falling back to IMAP: {e}")
            return None, "exception"
        return results, None

    def _search_emails_imap(
        self,
        query: str,
        folder: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Run a search against the IMAP server (no local-cache attempt).

        Args:
            query: Gmail-style search query string.
            folder: Folder to search (``None`` searches all folders).
            limit: Maximum number of results.

        Returns:
            List of result dicts sorted by date descending, each with
            keys: ``uid``, ``folder``, ``from``, ``to``, ``subject``,
            ``date``, ``flags``, ``has_attachments``, ``message_id``.
            ``message_id`` matches the field already emitted by the
            local-cache path in ``local_cache.py``.

        Raises:
            ValueError: On malformed queries.
        """
        search_spec = self._build_search_spec(query)

        if folder:
            folders_to_search = [folder]
        else:
            # Prefer the SPECIAL-USE \All folder when the server advertises one
            # (Gmail's [Gmail]/All Mail, Fastmail's Archive, etc.) — one SELECT
            # instead of iterating every folder. Falls back to all selectable.
            all_mail = self.find_special_use_folder(b"\\All")
            folders_to_search = [all_mail] if all_mail else self.list_folders()

        # Pass 1: collect (uid, folder, date) using a lightweight fetch
        candidates: List[tuple] = []
        for current_folder in folders_to_search:
            try:
                uids = self.search(search_spec, folder=current_folder)
                if not uids:
                    continue
                self.select_folder(current_folder, readonly=True)
                date_data = self._client_or_raise().fetch(uids, ["INTERNALDATE"])
                for uid, data in date_data.items():
                    dt = data.get(b"INTERNALDATE")
                    iso = dt.isoformat() if dt else "0"
                    candidates.append((iso, uid, current_folder))
            except Exception as e:
                logger.warning(f"Error searching folder {current_folder}: {e}")

        # Sort globally by date and keep only the top `limit`
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:limit]

        # Pass 2: full-fetch only the messages we will return
        # Group by folder to minimise SELECT commands
        by_folder: Dict[str, List[int]] = {}
        for _date, uid, fldr in top:
            by_folder.setdefault(fldr, []).append(uid)

        results: List[Dict[str, Any]] = []
        for current_folder, uid_list in by_folder.items():
            try:
                emails = self.fetch_emails(uid_list, folder=current_folder)
                for uid, email_obj in emails.items():
                    results.append(
                        {
                            "uid": uid,
                            "folder": current_folder,
                            "from": str(email_obj.from_),
                            "to": [str(t) for t in email_obj.to],
                            "subject": email_obj.subject,
                            "date": (
                                email_obj.date.isoformat() if email_obj.date else None
                            ),
                            "flags": email_obj.flags,
                            "has_attachments": len(email_obj.attachments) > 0,
                            "message_id": email_obj.message_id,
                        }
                    )
            except Exception as e:
                logger.warning(f"Error fetching from folder {current_folder}: {e}")

        results.sort(key=lambda x: x.get("date") or "0", reverse=True)
        return results


def copy_email_between_accounts(
    source: "ImapClient",
    dest: "ImapClient",
    uid: int,
    from_folder: str,
    to_folder: str = "INBOX",
    move: bool = False,
    preserve_flags: bool = False,
) -> Dict[str, Any]:
    """Copy (or move) an email from one IMAP account to another.

    Fetches the raw RFC 822 message from *source*, applies optional flag
    filtering, and APPENDs it to *dest*.  The original INTERNALDATE is
    always preserved.  If *move* is True the source message is deleted
    after a successful append.

    Args:
        source: IMAP client connected to the source account.
        dest: IMAP client connected to the destination account.
        uid: UID of the email in the source folder.
        from_folder: Folder in the source account containing the email.
        to_folder: Destination folder (default: INBOX).
        move: If True, delete the email from the source after copy.
        preserve_flags: If True, copy original flags (excluding \\Recent)
            to the destination.  If False, no flags are set.

    Returns:
        Dict with keys: success (bool), subject (str), new_uid (int | None),
        moved (bool), error (str | None).
    """
    raw_data = source.fetch_raw(uid, from_folder)
    if raw_data is None:
        return {
            "success": False,
            "subject": "",
            "new_uid": None,
            "moved": False,
            "error": f"UID {uid} not found in {from_folder}",
        }

    flags: tuple = ()
    if preserve_flags:
        raw_flags = raw_data["flags"]
        flags = tuple(
            f.decode("utf-8") if isinstance(f, bytes) else f
            for f in raw_flags
            if f not in (b"\\Recent", "\\Recent")
        )

    new_uid = dest.append_raw(
        to_folder,
        raw_data["raw"],
        flags=flags,
        msg_time=raw_data["date"],
    )

    if move:
        source.delete_email(uid, from_folder)

    return {
        "success": True,
        "subject": raw_data["subject"],
        "new_uid": new_uid,
        "moved": move,
        "error": None,
    }
