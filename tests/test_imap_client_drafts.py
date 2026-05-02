"""Tests for IMAP client draft saving functionality."""

from datetime import datetime
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from mailroom.config import ImapBlock
from mailroom.imap_client import ImapClient


class TestDraftsFunctionality:
    """Tests for drafts folder functionality."""

    @pytest.fixture
    def mock_imap_client(self):
        """Create a mock IMAP client with the necessary methods."""
        config = ImapBlock(
            host="imap.example.com",
            port=993,
            use_ssl=True,
            username="test@example.com",
            password="password",
        )

        # Create a mock IMAP client
        client = ImapClient(config)
        client.client = MagicMock()
        client.connected = True
        client.last_activity = datetime.now()

        # Mock list_folders method
        client.list_folders = MagicMock()

        return client

    @pytest.fixture
    def sample_mime_message(self):
        """Create a sample MIME message for testing."""
        message = EmailMessage()
        message["From"] = "sender@example.com"
        message["To"] = "recipient@example.com"
        message["Subject"] = "Draft Email"
        message.set_content("This is a draft email")

        return message

    def test_get_drafts_folder_standard(self, mock_imap_client):
        """Test getting the drafts folder for standard IMAP server."""
        # Mock list_folders to return standard folders
        mock_imap_client.list_folders.return_value = [
            "INBOX",
            "Sent",
            "Drafts",
            "Trash",
        ]

        # Test drafts folder detection
        drafts_folder = mock_imap_client._get_drafts_folder()
        assert drafts_folder == "Drafts"

        # Test with different casing
        mock_imap_client.list_folders.return_value = [
            "INBOX",
            "Sent",
            "drafts",
            "Trash",
        ]
        drafts_folder = mock_imap_client._get_drafts_folder()
        assert drafts_folder == "drafts"

    def test_get_drafts_folder_gmail(self, mock_imap_client):
        """Test getting the drafts folder for Gmail."""
        # Configure as Gmail
        mock_imap_client.block.host = "imap.gmail.com"

        # Mock list_folders to return Gmail folders
        mock_imap_client.list_folders.return_value = [
            "INBOX",
            "[Gmail]/Sent Mail",
            "[Gmail]/Drafts",
            "[Gmail]/All Mail",
            "[Gmail]/Trash",
        ]

        # Test drafts folder detection
        drafts_folder = mock_imap_client._get_drafts_folder()
        assert drafts_folder == "[Gmail]/Drafts"

    def test_get_drafts_folder_fallback(self, mock_imap_client):
        """Test fallback behavior when no drafts folder is found."""
        # Mock list_folders to return folders without a drafts folder
        mock_imap_client.list_folders.return_value = ["INBOX", "Sent", "Trash"]

        # Test drafts folder detection with fallback
        drafts_folder = mock_imap_client._get_drafts_folder()
        assert drafts_folder == "INBOX"

    @patch("mailroom.imap_client.logger")
    def test_save_draft_mime_success(
        self, mock_logger, mock_imap_client, sample_mime_message
    ):
        """Test saving a draft MIME message successfully."""
        # Mock behavior
        mock_imap_client._get_drafts_folder = MagicMock(return_value="Drafts")
        mock_imap_client.client.append.return_value = b"[APPENDUID 1234 5678]"

        # Call save_draft_mime
        uid = mock_imap_client.save_draft_mime(sample_mime_message)

        # Verify behavior
        mock_imap_client.client.append.assert_called_once()
        assert uid == 5678
        mock_logger.debug.assert_called_with("Draft saved with UID: 5678")

    @patch("mailroom.imap_client.logger")
    def test_save_draft_mime_no_appenduid(
        self, mock_logger, mock_imap_client, sample_mime_message
    ):
        """Test saving a draft without APPENDUID in response."""
        # Mock behavior
        mock_imap_client._get_drafts_folder = MagicMock(return_value="Drafts")
        mock_imap_client.client.append.return_value = b"OK"

        # Call save_draft_mime
        uid = mock_imap_client.save_draft_mime(sample_mime_message)

        # Verify behavior
        mock_imap_client.client.append.assert_called_once()
        assert uid is None
        mock_logger.warning.assert_called_with(
            "Could not extract UID from append response: b'OK'"
        )

    @patch("mailroom.imap_client.logger")
    def test_save_draft_mime_error(
        self, mock_logger, mock_imap_client, sample_mime_message
    ):
        """Test error handling when saving a draft fails."""
        # Mock behavior
        mock_imap_client._get_drafts_folder = MagicMock(return_value="Drafts")
        mock_imap_client.client.append.side_effect = Exception("IMAP error")

        # Call save_draft_mime
        uid = mock_imap_client.save_draft_mime(sample_mime_message)

        # Verify behavior
        mock_imap_client.client.append.assert_called_once()
        assert uid is None
        mock_logger.error.assert_called()
