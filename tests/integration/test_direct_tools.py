"""Integration tests for direct tool usage with the Mailroom client.

These tests directly import and use the IMAP tool functions to test their functionality
with a real Gmail account. This approach bypasses the server API and CLI interfaces
to focus on testing the core email search functionality.
"""

import json
import logging

import pytest

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from mailroom.config import load_config  # noqa: E402
from mailroom.imap_client import ImapClient  # noqa: E402

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration

try:
    from mailroom.tools import search as search_tool
except ImportError:
    search_tool = None


class TestDirectToolsIntegration:
    """Test direct usage of Mailroom tools without going through the server or CLI."""

    @pytest.fixture(scope="class")
    async def imap_client(self):
        """Create and yield an IMAP client connected to Gmail."""
        config = load_config()
        acct = config.accounts[config.default_account]
        client = ImapClient(acct.imap, acct.allowed_folders)
        client.connect()
        try:
            yield client
        finally:
            client.disconnect()

    @pytest.mark.asyncio
    async def test_list_folders(self, imap_client):
        """Test listing folders directly from the IMAP client."""
        # Get list of folders
        folders = imap_client.list_folders()

        # Check that we got some folders
        assert len(folders) > 0, "No folders returned from IMAP server"

        # Check that INBOX is present
        assert "INBOX" in folders, "INBOX not found in folder list"

        # Log the folders for reference
        logger.info(f"Found {len(folders)} folders: {folders}")

    @pytest.mark.asyncio
    async def test_search_unread_emails(self, imap_client):
        """Test searching for unread emails using the search tool directly."""
        results = await search_tool(query="is:unread", folder="INBOX", limit=10)

        try:
            results_dict = json.loads(results)
            logger.info(f"Search results: {json.dumps(results_dict, indent=2)}")

            assert isinstance(results_dict, list), "Expected list of results"
            logger.info(f"Found {len(results_dict)} unread emails in INBOX")

            if results_dict:
                first_email = results_dict[0]
                expected_fields = ["uid", "folder", "from", "subject", "date"]
                for field in expected_fields:
                    assert (
                        field in first_email
                    ), f"Field '{field}' missing from email result"

                assert "\\Seen" not in first_email.get(
                    "flags", []
                ), "Email should be unread (no \\Seen flag)"

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse search results: {e}")
            logger.error(f"Raw results: {results}")
            pytest.fail(f"Invalid JSON returned from search tool: {e}")

    @pytest.mark.asyncio
    async def test_search_with_different_queries(self, imap_client):
        """Test searching with different Gmail-style queries."""
        test_cases = [
            ("all", "all emails"),
            ("today", "emails from today"),
            ("subject:test", "emails with 'test' in subject"),
        ]

        for query, description in test_cases:
            logger.info(f"Testing search for {description}")

            results = await search_tool(query=query, folder="INBOX", limit=5)

            try:
                results_dict = json.loads(results)
                logger.info(f"Found {len(results_dict)} {description}")
                assert isinstance(
                    results_dict, list
                ), f"Expected list of results for {description}"

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse search results for {description}: {e}")
                logger.error(f"Raw results: {results}")
                pytest.fail(
                    f"Invalid JSON returned from search tool for {description}: {e}"
                )


if __name__ == "__main__":
    # Enable running the tests directly
    pytest.main(["-xvs", __file__])
