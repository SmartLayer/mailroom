"""Tests for MCP tools implementation."""

import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from mcp.server.fastmcp import FastMCP, Context

from imap_mcp.imap_client import ImapClient
from imap_mcp.models import Email, EmailAddress, EmailContent
from imap_mcp.tools import register_tools


# Patch the get_client_from_context function to use our mock client
@pytest.fixture(autouse=True)
def patch_get_client():
    with patch('imap_mcp.tools.get_client_from_context') as mock_get_client:
        yield mock_get_client


class TestTools:
    """Test class for MCP tools."""

    @pytest.fixture
    def mock_email(self):
        """Create a mock email object."""
        email = Email(
            message_id="<test123@example.com>",
            subject="Test Email",
            from_=EmailAddress(name="Sender", address="sender@example.com"),
            to=[EmailAddress(name="Recipient", address="recipient@example.com")],
            cc=[],
            bcc=[],
            date=datetime.now(),
            content=EmailContent(text="Test content", html="<p>Test content</p>"),
            attachments=[],
            flags=["\\Seen"],
            headers={},
            folder="INBOX",
            uid=1
        )
        return email

    @pytest.fixture
    def mock_client(self, mock_email):
        """Create a mock IMAP client."""
        client = MagicMock(spec=ImapClient)
        # Configure default return values
        client.move_email.return_value = True
        client.mark_email.return_value = True
        client.delete_email.return_value = True
        client.list_folders.return_value = ["INBOX", "Sent", "Archive", "Trash"]
        client.search.return_value = [1, 2, 3]
        client.fetch_emails.return_value = {1: mock_email, 2: mock_email, 3: mock_email}
        client.fetch_email.return_value = mock_email
        return client

    @pytest.fixture
    def tools(self, mock_client):
        """Set up tools for testing."""
        # Create a mock MCP server
        mcp = MagicMock(spec=FastMCP)
        
        # Make tool decorator store and return the decorated function
        stored_tools = {}
        
        def mock_tool_decorator():
            def decorator(func):
                stored_tools[func.__name__] = func
                return func
            return decorator
        
        mcp.tool = mock_tool_decorator
        
        # Register tools with our mock
        register_tools(mcp, mock_client)
        
        # Return the tools dictionary
        return stored_tools

    @pytest.fixture
    def mock_context(self, mock_client, patch_get_client):
        """Create a mock context and configure get_client_from_context."""
        context = MagicMock(spec=Context)
        patch_get_client.return_value = mock_client
        return context

    @pytest.mark.asyncio
    async def test_move_email(self, tools, mock_client, mock_context):
        """Test moving an email from one folder to another."""
        # Get the move_email function
        move_email = tools["move_email"]
        
        # Call the move_email function
        result = await move_email("INBOX", 123, "Archive", mock_context)
        
        # Check the client was called correctly
        mock_client.move_email.assert_called_once_with(123, "INBOX", "Archive")
        
        # Check the result
        assert "Email moved from INBOX to Archive" in result

        # Test error handling
        mock_client.move_email.side_effect = Exception("Connection error")
        result = await move_email("INBOX", 123, "Archive", mock_context)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_mark_as_read(self, tools, mock_client, mock_context):
        """Test marking an email as read."""
        # Get the mark_as_read function
        mark_as_read = tools["mark_as_read"]
        
        # Call the function
        result = await mark_as_read("INBOX", 123, mock_context)
        
        # Check the client was called correctly
        mock_client.mark_email.assert_called_once_with(123, "INBOX", "\\Seen", True)
        
        # Check the result
        assert "Email marked as read" in result
        
        # Test failure case
        mock_client.mark_email.return_value = False
        result = await mark_as_read("INBOX", 123, mock_context)
        assert "Failed to mark email as read" in result

    @pytest.mark.asyncio
    async def test_mark_as_unread(self, tools, mock_client, mock_context):
        """Test marking an email as unread."""
        # Get the mark_as_unread function
        mark_as_unread = tools["mark_as_unread"]
        
        # Reset mock for this test
        mock_client.mark_email.reset_mock()
        mock_client.mark_email.return_value = True
        
        # Call the function
        result = await mark_as_unread("INBOX", 123, mock_context)
        
        # Check the client was called correctly
        mock_client.mark_email.assert_called_once_with(123, "INBOX", "\\Seen", False)
        
        # Check the result
        assert "Email marked as unread" in result
        
        # Test error handling
        mock_client.mark_email.side_effect = Exception("Server error")
        result = await mark_as_unread("INBOX", 123, mock_context)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_flag_email(self, tools, mock_client, mock_context):
        """Test flagging and unflagging an email."""
        # Get the flag_email function
        flag_email = tools["flag_email"]
        
        # Reset mock for this test
        mock_client.mark_email.reset_mock()
        mock_client.mark_email.return_value = True
        
        # Test flagging
        result = await flag_email("INBOX", 123, mock_context, True)
        mock_client.mark_email.assert_called_once_with(123, "INBOX", "\\Flagged", True)
        assert "Email flagged" in result
        
        # Reset mock
        mock_client.mark_email.reset_mock()
        
        # Test unflagging
        result = await flag_email("INBOX", 123, mock_context, False)
        mock_client.mark_email.assert_called_once_with(123, "INBOX", "\\Flagged", False)
        assert "Email unflagged" in result

    @pytest.mark.asyncio
    async def test_delete_email(self, tools, mock_client, mock_context):
        """Test deleting an email."""
        # Get the delete_email function
        delete_email = tools["delete_email"]
        
        # Call the function
        result = await delete_email("INBOX", 123, mock_context)
        
        # Check the client was called correctly
        mock_client.delete_email.assert_called_once_with(123, "INBOX")
        
        # Check the result
        assert "Email deleted" in result
        
        # Test failure case
        mock_client.delete_email.return_value = False
        result = await delete_email("INBOX", 123, mock_context)
        assert "Failed to delete" in result
        
        # Test error handling
        mock_client.delete_email.side_effect = Exception("Permission denied")
        result = await delete_email("INBOX", 123, mock_context)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_search_emails(self, tools, mock_client, mock_context, mock_email):
        """Test searching for emails via the MCP tool wrapper."""
        search_emails = tools["search_emails"]

        # Configure client.search_emails to return sample results
        sample_results = [
            {"uid": 1, "folder": "INBOX", "from": "sender@example.com",
             "to": ["recipient@example.com"], "subject": "Test Email",
             "date": "2025-04-01T10:00:00", "flags": ["\\Seen"],
             "has_attachments": False},
        ]
        mock_client.search_emails.return_value = sample_results

        # Test default parameters
        result = await search_emails("test query", mock_context)
        result_data = json.loads(result)
        assert isinstance(result_data, list)
        assert len(result_data) == 1
        assert result_data[0]["subject"] == "Test Email"
        mock_client.search_emails.assert_called_once_with(
            "test query", "text", folder=None, limit=10,
        )

        # Test with specific folder and criteria
        mock_client.search_emails.reset_mock()
        result = await search_emails("test query", mock_context, folder="INBOX", criteria="from")
        mock_client.search_emails.assert_called_once_with(
            "test query", "from", folder="INBOX", limit=10,
        )

        # Test with invalid criteria — client.search_emails raises ValueError
        mock_client.search_emails.reset_mock()
        mock_client.search_emails.side_effect = ValueError("Invalid search criteria: invalid")
        result = await search_emails("test query", mock_context, criteria="invalid")
        assert "Invalid search criteria" in result
        mock_client.search_emails.side_effect = None

        # Test numeric query is coerced to string
        mock_client.search_emails.reset_mock()
        mock_client.search_emails.return_value = sample_results
        result = await search_emails(69172700, mock_context, folder="INBOX")
        mock_client.search_emails.assert_called_once_with(
            "69172700", "text", folder="INBOX", limit=10,
        )

    @pytest.mark.asyncio
    async def test_search_emails_raw_criteria(self, tools, mock_client, mock_context, mock_email):
        """Test searching with raw IMAP criteria delegates to client.search_emails."""
        search_emails = tools["search_emails"]

        sample_results = [
            {"uid": 1, "folder": "INBOX", "from": "sender@example.com",
             "to": ["recipient@example.com"], "subject": "Edinburgh trip",
             "date": "2025-04-01T10:00:00", "flags": [], "has_attachments": False},
        ]
        mock_client.search_emails.return_value = sample_results

        result = await search_emails("TEXT Edinburgh", mock_context, folder="INBOX", criteria="raw")
        result_data = json.loads(result)
        assert isinstance(result_data, list)
        mock_client.search_emails.assert_called_once_with(
            "TEXT Edinburgh", "raw", folder="INBOX", limit=10,
        )

    @pytest.mark.asyncio
    async def test_process_email(self, tools, mock_client, mock_context):
        """Test processing an email with multiple actions."""
        process_email = tools["process_email"]

        # Test move action — delegates to process_email_action
        mock_client.process_email_action.return_value = "Email moved from INBOX to Archive"
        result = await process_email(
            "INBOX", 123, "move", mock_context, target_folder="Archive"
        )
        mock_client.process_email_action.assert_called_with(
            123, "INBOX", "move", target_folder="Archive"
        )
        assert "Email moved" in result

        # Test read action
        mock_client.process_email_action.return_value = "Email marked as read"
        result = await process_email("INBOX", 123, "read", mock_context)
        mock_client.process_email_action.assert_called_with(
            123, "INBOX", "read", target_folder=None
        )
        assert "Email marked as read" in result

        # Test move without target folder — ValueError from domain
        mock_client.process_email_action.side_effect = ValueError(
            "target_folder is required for move action"
        )
        result = await process_email("INBOX", 123, "move", mock_context)
        assert "target_folder" in result
        mock_client.process_email_action.side_effect = None

        # Test invalid action — ValueError from domain
        mock_client.process_email_action.side_effect = ValueError(
            "Unknown action 'invalid_action'"
        )
        result = await process_email("INBOX", 123, "invalid_action", mock_context)
        assert "Unknown action" in result
        mock_client.process_email_action.side_effect = None

        # Test email not found
        mock_client.fetch_email.return_value = None
        result = await process_email("INBOX", 123, "read", mock_context)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_tool_error_handling(self, tools, mock_client, mock_context):
        """Test error handling in tools."""
        # Get tools to test
        move_email = tools["move_email"]
        mark_as_read = tools["mark_as_read"]
        search_emails = tools["search_emails"]
        
        # Test move_email error handling
        mock_client.move_email.side_effect = Exception("Network error")
        result = await move_email("INBOX", 123, "Archive", mock_context)
        assert "Error" in result
        
        # Test mark_as_read error handling
        mock_client.mark_email.side_effect = Exception("Server timeout")
        result = await mark_as_read("INBOX", 123, mock_context)
        assert "Error" in result
        
        # Test search_emails error handling — client.search_emails raises ValueError
        mock_client.search_emails.side_effect = ValueError("Search failed")
        result = await search_emails("test", mock_context)
        assert "Search failed" in result
        mock_client.search_emails.side_effect = None

    @pytest.mark.asyncio
    async def test_tool_parameter_validation(self, tools, mock_client, mock_context):
        """Test parameter validation in tools."""
        # Get tools to test
        search_emails = tools["search_emails"]
        process_email = tools["process_email"]
        
        # Test search_emails with invalid criteria — client raises ValueError
        mock_client.search_emails.side_effect = ValueError("Invalid search criteria: invalid_criteria")
        result = await search_emails("test", mock_context, criteria="invalid_criteria")
        assert "Invalid search criteria" in result
        mock_client.search_emails.side_effect = None
        
        # Test process_email with missing target folder for move action
        mock_client.process_email_action.side_effect = ValueError("target_folder is required for move action")
        result = await process_email("INBOX", 123, "move", ctx=mock_context)
        assert "target_folder" in result

        # Test process_email with invalid action
        mock_client.process_email_action.side_effect = ValueError("Unknown action 'nonexistent_action'")
        result = await process_email("INBOX", 123, "nonexistent_action", ctx=mock_context)
        assert "Unknown action" in result
        mock_client.process_email_action.side_effect = None


class TestRawImapCriteriaParsing:
    """Test the ImapClient.parse_raw_criteria helper function."""
    
    def test_parse_simple_single_keyword(self):
        """Test parsing simple single-keyword queries."""
        assert ImapClient.parse_raw_criteria("ALL") == "ALL"
        assert ImapClient.parse_raw_criteria("UNSEEN") == "UNSEEN"
        assert ImapClient.parse_raw_criteria("SEEN") == "SEEN"
    
    def test_parse_simple_text_search(self):
        """Test parsing simple TEXT searches."""
        result = ImapClient.parse_raw_criteria("TEXT Edinburgh")
        assert result == ["TEXT", "Edinburgh"]
        
        result = ImapClient.parse_raw_criteria('TEXT "booking confirmation"')
        assert result == ["TEXT", "booking confirmation"]
    
    def test_parse_simple_or_expression(self):
        """Test parsing simple OR expressions."""
        result = ImapClient.parse_raw_criteria('OR TEXT "Edinburgh" TEXT "Berlin"')
        assert result == ["OR", "TEXT", "Edinburgh", "TEXT", "Berlin"]
    
    def test_parse_nested_or_expression(self):
        """Test parsing nested OR expressions."""
        result = ImapClient.parse_raw_criteria('OR TEXT "Edinburgh" OR TEXT "Berlin" TEXT "Munich"')
        assert result == ["OR", "TEXT", "Edinburgh", "OR", "TEXT", "Berlin", "TEXT", "Munich"]
    
    def test_parse_complex_travel_query(self):
        """Test parsing the complex travel booking query from the example."""
        query = 'OR TEXT "Edinburgh" OR TEXT "Berlin" OR TEXT "Munich" OR TEXT "Vienna" OR TEXT "Warsaw" OR TEXT "itinerary" OR TEXT "booking confirmation" OR TEXT "e-ticket" OR TEXT "reservation" OR TEXT "receipt" OR TEXT "ticket" TEXT "order"'
        result = ImapClient.parse_raw_criteria(query)
        
        # Verify it's a list
        assert isinstance(result, list)
        
        # Verify key elements are present
        assert "OR" in result
        assert "TEXT" in result
        assert "Edinburgh" in result
        assert "Berlin" in result
        assert "booking confirmation" in result
        assert "order" in result
    
    def test_parse_from_subject_criteria(self):
        """Test parsing FROM and SUBJECT criteria."""
        result = ImapClient.parse_raw_criteria('FROM "john@example.com"')
        assert result == ["FROM", "john@example.com"]
        
        result = ImapClient.parse_raw_criteria('SUBJECT "meeting"')
        assert result == ["SUBJECT", "meeting"]
    
    def test_parse_combined_criteria(self):
        """Test parsing combined criteria without OR."""
        result = ImapClient.parse_raw_criteria('SEEN FROM gmail')
        assert result == ["SEEN", "FROM", "gmail"]
