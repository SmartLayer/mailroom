"""IMAP client implementation."""

import email
import logging
import re
import shlex
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

import imapclient

from mailroom.config import ImapConfig
from mailroom.models import Email
from mailroom.oauth2 import get_access_token

logger = logging.getLogger(__name__)


class ImapClient:
    """IMAP client for interacting with email servers."""
    
    def __init__(self, config: ImapConfig, allowed_folders: Optional[List[str]] = None):
        """Initialize IMAP client.
        
        Args:
            config: IMAP configuration
            allowed_folders: List of allowed folders (None means all folders)
        """
        self.config = config
        self.allowed_folders = set(allowed_folders) if allowed_folders else None
        self.client = None
        self.folder_cache: Dict[str, List[str]] = {}
        self.connected = False
        self.count_cache: Dict[str, Dict[str, Tuple[int, datetime]]] = {}  # Cache for message counts
        self.current_folder = None  # Store the currently selected folder
        self.folder_message_counts = {}  # Cache for folder message counts
        self.last_activity: Optional[datetime] = None  # Track last successful IMAP operation
    
    @staticmethod
    def parse_raw_criteria(raw_query: str) -> Union[str, List]:
        """Parse a raw IMAP search query string into imapclient format.

        Converts e.g. ``"OR TEXT 'foo' SUBJECT 'bar'"`` into
        ``['OR', 'TEXT', 'foo', 'SUBJECT', 'bar']``.

        Returns a plain string for single-keyword queries (``"ALL"``),
        or a list for multi-token queries.
        """
        try:
            tokens = shlex.split(raw_query)
        except ValueError as e:
            logger.warning(
                f"Failed to parse raw IMAP query with shlex: {e}. "
                "Falling back to simple split."
            )
            tokens = raw_query.split()

        if len(tokens) == 1:
            return tokens[0]
        return tokens

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
        raw_capabilities = self.client.capabilities()
        
        # Convert byte strings to regular strings and normalize case
        capabilities = []
        for cap in raw_capabilities:
            if isinstance(cap, bytes):
                cap = cap.decode('utf-8')
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
        for flags, delimiter, name in self.client.list_folders():
            if isinstance(name, bytes):
                # Convert bytes to string if necessary
                name = name.decode("utf-8")
            
            # Filter folders if allowed_folders is set
            if self.allowed_folders is not None and name not in self.allowed_folders:
                continue
            
            folders.append(name)
            self.folder_cache[name] = flags
        
        self._update_activity()
        logger.debug(f"Listed {len(folders)} folders")
        return folders
    
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
            result = self.client.select_folder(folder, readonly=readonly)
            self.current_folder = folder
            self._update_activity()
            logger.debug(f"Selected folder '{folder}'")
            return result
        except imapclient.IMAPClient.Error as e:
            logger.error(f"Error selecting folder {folder}: {e}")
            raise ConnectionError(f"Failed to select folder {folder}: {e}")
    
    def search(
        self, 
        criteria: Union[str, List, Tuple],
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
        
        if isinstance(criteria, str):
            # Predefined criteria strings
            criteria_map = {
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
                criteria = criteria_map[criteria.lower()]
        
        results = self.client.search(criteria, charset=charset)
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
        result = self.client.fetch([uid], ["BODY.PEEK[]", "FLAGS"])
        
        if not result or uid not in result:
            logger.warning(f"Message with UID {uid} not found in folder {folder}")
            return None
        
        # Parse message
        message_data = result[uid]
        raw_message = message_data[b"BODY[]"]
        flags = message_data[b"FLAGS"]
        
        # Convert flags to strings
        str_flags = [
            f.decode("utf-8") if isinstance(f, bytes) else f 
            for f in flags
        ]
        
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
        result = self.client.fetch(uids, ["BODY.PEEK[]", "FLAGS"])
        
        # Parse emails
        emails = {}
        for uid, message_data in result.items():
            raw_message = message_data[b"BODY[]"]
            flags = message_data[b"FLAGS"]
            
            # Convert flags to strings
            str_flags = [
                f.decode("utf-8") if isinstance(f, bytes) else f 
                for f in flags
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
            raise ValueError(f"Initial email with UID {uid} not found in folder {folder}")
        
        # Get thread identifiers from the initial email
        message_id = initial_email.headers.get("Message-ID", "")
        subject = initial_email.subject
        
        # Strip "Re:", "Fwd:", etc. from the subject for better matching
        clean_subject = re.sub(r"^(?:Re|Fwd|Fw|FWD|RE|FW):\s*", "", subject, flags=re.IGNORECASE)
        
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
                for ref_id in re.findall(r'<[^>]+>', initial_references):
                    query = f'HEADER Message-ID "{ref_id}"'
                    try:
                        results = self.search(query, folder)
                        thread_uids.update(results)
                    except Exception as e:
                        logger.warning(f"Error searching for Referenced message {ref_id}: {e}")
            
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
                        f"FW: {clean_subject}"
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
            thread_emails.values(), 
            key=lambda e: e.date if e.date else datetime.min
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
            if value:
                self.client.add_flags([uid], flag)
                logger.debug(f"Added flag {flag} to message {uid}")
            else:
                self.client.remove_flags([uid], flag)
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
            self.client.copy([uid], target_folder)
            self.client.add_flags([uid], r"\Deleted")
            self.client.expunge()
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
            self.client.add_flags([uid], r"\Deleted")
            self.client.expunge()
            self._update_activity()
            logger.debug(f"Deleted message {uid} from {folder}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete email: {e}")
            return False
    
    def process_email_action(
        self, uid: int, folder: str, action: str,
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
        drafts_folder_names = ["Drafts", "Draft", "Brouillons", "Borradores", "Entwürfe"]
        for folder in folders:
            if folder.lower() in [name.lower() for name in drafts_folder_names]:
                logger.debug(f"Using drafts folder: {folder}")
                return folder
        
        # Fallback to INBOX if no drafts folder found
        logger.warning("No drafts folder found, using INBOX as fallback")
        return "INBOX"
    
    def save_draft_mime(self, message) -> Optional[int]:
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
            response = self.client.append(
                drafts_folder, 
                message_bytes,
                flags=(r"\Draft",)
            )
            
            # Try to extract the UID from the response
            uid = None
            if isinstance(response, bytes) and b"APPENDUID" in response:
                # Parse the APPENDUID response (format: [APPENDUID <uidvalidity> <uid>])
                try:
                    # Use a more robust parsing approach
                    match = re.search(rb'APPENDUID\s+\d+\s+(\d+)', response)
                    if match:
                        uid = int(match.group(1))
                        logger.debug(f"Draft saved with UID: {uid}")
                except (IndexError, ValueError) as e:
                    logger.warning(f"Could not parse UID from response: {e}")
            
            if uid is None:
                logger.warning(f"Could not extract UID from append response: {response}")
            
            self._update_activity()
            return uid
            
        except Exception as e:
            logger.error(f"Failed to save draft: {e}")
            return None

    def search_emails(
        self,
        query: str,
        criteria: str = "text",
        folder: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """High-level email search across one or all folders.

        Args:
            query: Search query string. For ``raw`` criteria this is the full
                   IMAP search expression.
            criteria: One of text, from, to, subject, all, unseen, seen,
                      today, week, month, raw.
            folder: Folder to search (``None`` searches all folders).
            limit: Maximum number of results.

        Returns:
            List of result dicts sorted by date descending, each with keys:
            uid, folder, from, to, subject, date, flags, has_attachments.

        Raises:
            ValueError: If *criteria* is not recognised.
        """
        criteria_map = {
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

        key = criteria.lower()
        if key not in criteria_map:
            raise ValueError(
                f"Invalid search criteria: {criteria}. "
                f"Valid options: {', '.join(criteria_map)}"
            )

        if key == "raw":
            search_spec = self.parse_raw_criteria(query)
        else:
            search_spec = criteria_map[key]

        folders_to_search = [folder] if folder else self.list_folders()
        results: List[Dict[str, Any]] = []

        for current_folder in folders_to_search:
            try:
                uids = self.search(search_spec, folder=current_folder)
                uids = sorted(uids, reverse=True)[:limit]
                if uids:
                    emails = self.fetch_emails(uids, folder=current_folder)
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
            except Exception as e:
                logger.warning(f"Error searching folder {current_folder}: {e}")

        results.sort(key=lambda x: x.get("date") or "0", reverse=True)
        return results[:limit]
