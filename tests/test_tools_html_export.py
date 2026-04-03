"""Tests for HTML export MCP tool."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from mcp.server.fastmcp import FastMCP, Context
from mailroom.imap_client import ImapClient
from mailroom.models import Email, EmailAddress, EmailAttachment, EmailContent
from mailroom.tools import register_tools


# Patch the get_client_from_context function to use our mock client
@pytest.fixture(autouse=True)
def patch_get_client():
    with patch('mailroom.tools.get_client_from_context') as mock_get_client:
        yield mock_get_client


@pytest.fixture
def email_with_html_and_images():
    """Create a test email with HTML content and inline images."""
    html_content = """
    <html>
    <head><title>Test Email</title></head>
    <body>
        <h1>Crypto got everything it wanted. Now it's sinking</h1>
        <p>This is a test email with inline images.</p>
        <img src="cid:image1@example.com" alt="Logo">
        <img src="cid:image2@example.com" alt="Chart">
        <p>End of email.</p>
    </body>
    </html>
    """
    
    email = Email(
        uid=123,
        folder="INBOX",
        from_=EmailAddress(name="Sender", address="sender@example.com"),
        to=[EmailAddress(name="Recipient", address="recipient@example.com")],
        subject="Crypto got everything it wanted. Now it's sinking",
        date=None,
        message_id="<test@example.com>",
        in_reply_to=None,
        headers={},
        content=EmailContent(text="Plain text version", html=html_content),
        attachments=[
            EmailAttachment(
                filename="logo.png",
                content_type="image/png",
                size=100,
                content_id="image1@example.com",
                content=b"PNG_IMAGE_DATA_HERE",
            ),
            EmailAttachment(
                filename="chart.jpg",
                content_type="image/jpeg",
                size=200,
                content_id="image2@example.com",
                content=b"JPEG_IMAGE_DATA_HERE",
            ),
        ],
        flags=[],
    )
    return email


@pytest.fixture
def email_with_html_no_images():
    """Create a test email with HTML content but no inline images."""
    html_content = """
    <html>
    <head><title>Simple Email</title></head>
    <body>
        <h1>Newsletter</h1>
        <p>This is a simple HTML email without images.</p>
    </body>
    </html>
    """
    
    email = Email(
        uid=456,
        folder="INBOX",
        from_=EmailAddress(name="Sender", address="sender@example.com"),
        to=[EmailAddress(name="Recipient", address="recipient@example.com")],
        subject="Simple Newsletter",
        date=None,
        message_id="<test2@example.com>",
        in_reply_to=None,
        headers={},
        content=EmailContent(text="Plain text version", html=html_content),
        attachments=[],
        flags=[],
    )
    return email


@pytest.fixture
def email_plaintext_only():
    """Create a test email with only plain text content."""
    email = Email(
        uid=789,
        folder="INBOX",
        from_=EmailAddress(name="Sender", address="sender@example.com"),
        to=[EmailAddress(name="Recipient", address="recipient@example.com")],
        subject="Plain text only",
        date=None,
        message_id="<test3@example.com>",
        in_reply_to=None,
        headers={},
        content=EmailContent(text="This is plain text only", html=None),
        attachments=[],
        flags=[],
    )
    return email


def _make_email(html, attachments=None):
    """Helper to build an Email with given HTML and attachments."""
    return Email(
        message_id="<test@example.com>",
        subject="Test",
        from_=EmailAddress(name="S", address="s@x.com"),
        to=[EmailAddress(name="R", address="r@x.com")],
        content=EmailContent(html=html),
        attachments=attachments or [],
    )


class TestEmbedInlineImages:
    """Tests for Email.html_with_embedded_images method."""

    def test_embed_single_image(self):
        """Test embedding a single inline image."""
        email_obj = _make_email(
            '<img src="cid:test123" alt="Test">',
            [EmailAttachment(filename="test.png", content_type="image/png",
                             size=10, content_id="test123", content=b"TESTDATA")],
        )
        result = email_obj.html_with_embedded_images()
        assert "cid:test123" not in result
        assert "data:image/png;base64," in result
        assert "VEVTVERB" in result  # Base64 of "TESTDATA"

    def test_embed_multiple_images(self):
        """Test embedding multiple inline images."""
        email_obj = _make_email(
            '<img src="cid:img1" alt="First"><img src="cid:img2" alt="Second">',
            [
                EmailAttachment(filename="img1.png", content_type="image/png",
                                size=5, content_id="img1", content=b"IMG1"),
                EmailAttachment(filename="img2.jpg", content_type="image/jpeg",
                                size=5, content_id="img2", content=b"IMG2"),
            ],
        )
        result = email_obj.html_with_embedded_images()
        assert "cid:img1" not in result
        assert "cid:img2" not in result
        assert "data:image/png;base64," in result
        assert "data:image/jpeg;base64," in result

    def test_embed_with_angle_brackets_in_cid(self):
        """Test embedding images when content_id has angle brackets."""
        email_obj = _make_email(
            '<img src="cid:test123" alt="Test">',
            [EmailAttachment(filename="test.png", content_type="image/png",
                             size=10, content_id="<test123>", content=b"TESTDATA")],
        )
        result = email_obj.html_with_embedded_images()
        assert "cid:test123" not in result
        assert "data:image/png;base64," in result

    def test_embed_with_single_quotes(self):
        """Test embedding images with single-quoted src attributes."""
        email_obj = _make_email(
            "<img src='cid:test123' alt='Test'>",
            [EmailAttachment(filename="test.png", content_type="image/png",
                             size=10, content_id="test123", content=b"TESTDATA")],
        )
        result = email_obj.html_with_embedded_images()
        assert "cid:test123" not in result
        assert "data:image/png;base64," in result

    def test_embed_missing_attachment(self):
        """Test handling of missing attachment for cid reference."""
        email_obj = _make_email(
            '<img src="cid:missing" alt="Test">',
            [EmailAttachment(filename="other.png", content_type="image/png",
                             size=10, content_id="other", content=b"TESTDATA")],
        )
        result = email_obj.html_with_embedded_images()
        assert "cid:missing" in result

    def test_embed_no_attachments(self):
        """Test with no attachments."""
        html = '<img src="cid:test123" alt="Test">'
        email_obj = _make_email(html)
        result = email_obj.html_with_embedded_images()
        assert result == html

    def test_embed_no_cid_references(self):
        """Test HTML with no cid: references."""
        html = '<img src="http://example.com/image.png" alt="Test">'
        email_obj = _make_email(
            html,
            [EmailAttachment(filename="test.png", content_type="image/png",
                             size=10, content_id="test123", content=b"TESTDATA")],
        )
        result = email_obj.html_with_embedded_images()
        assert result == html


class TestExportEmailHtml:
    """Tests for export_email_html tool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock IMAP client."""
        client = MagicMock(spec=ImapClient)
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
    async def test_export_html_with_inline_images(
        self, tools, mock_client, mock_context, email_with_html_and_images
    ):
        """Test exporting HTML email with inline images."""
        # Setup
        mock_client.fetch_email.return_value = email_with_html_and_images

        # Get the tool
        export_tool = tools["export_email_html"]

        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".html") as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Call the tool
            result = await export_tool(
                folder="INBOX",
                uid=123,
                save_path=tmp_path,
                ctx=mock_context,
            )

            # Assertions
            assert "Success" in result
            assert tmp_path in result
            assert "bytes" in result

            # Verify file was written
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Check that cid: references were replaced with base64 data URIs
            assert "cid:image1@example.com" not in content
            assert "cid:image2@example.com" not in content
            assert "data:image/png;base64," in content
            assert "data:image/jpeg;base64," in content
            
            # Check that original HTML structure is preserved
            assert "<h1>Crypto got everything it wanted. Now it's sinking</h1>" in content

        finally:
            # Cleanup
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @pytest.mark.asyncio
    async def test_export_html_without_images(
        self, tools, mock_client, mock_context, email_with_html_no_images
    ):
        """Test exporting HTML email without inline images."""
        # Setup
        mock_client.fetch_email.return_value = email_with_html_no_images

        # Get the tool
        export_tool = tools["export_email_html"]

        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".html") as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Call the tool
            result = await export_tool(
                folder="INBOX",
                uid=456,
                save_path=tmp_path,
                ctx=mock_context,
            )

            # Assertions
            assert "Success" in result

            # Verify file was written
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Check that HTML content is present
            assert "<h1>Newsletter</h1>" in content
            assert "simple HTML email" in content

        finally:
            # Cleanup
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @pytest.mark.asyncio
    async def test_export_plaintext_only_email(
        self, tools, mock_client, mock_context, email_plaintext_only
    ):
        """Test exporting email with no HTML content (error case)."""
        # Setup
        mock_client.fetch_email.return_value = email_plaintext_only

        # Get the tool
        export_tool = tools["export_email_html"]

        # Call the tool
        result = await export_tool(
            folder="INBOX",
            uid=789,
            save_path="/tmp/test.html",
            ctx=mock_context,
        )

        # Assertions
        assert "Error" in result
        assert "no HTML content" in result

    @pytest.mark.asyncio
    async def test_export_email_not_found(
        self, tools, mock_client, mock_context
    ):
        """Test exporting non-existent email."""
        # Setup
        mock_client.fetch_email.return_value = None

        # Get the tool
        export_tool = tools["export_email_html"]

        # Call the tool
        result = await export_tool(
            folder="INBOX",
            uid=999,
            save_path="/tmp/test.html",
            ctx=mock_context,
        )

        # Assertions
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_export_path_traversal_prevention(
        self, tools, mock_client, mock_context, email_with_html_no_images
    ):
        """Test that path traversal attempts are sanitized."""
        # Setup
        mock_client.fetch_email.return_value = email_with_html_no_images

        # Get the tool
        export_tool = tools["export_email_html"]

        # Create a temporary directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Try to use path traversal
            malicious_path = os.path.join(tmp_dir, "../../../evil.html")

            # Call the tool
            result = await export_tool(
                folder="INBOX",
                uid=456,
                save_path=malicious_path,
                ctx=mock_context,
            )

            # The function should sanitize the path and succeed
            assert "Success" in result

            # Verify that the file was NOT written outside the tmp_dir
            assert not os.path.exists("/evil.html")
            assert not os.path.exists("/../evil.html")

    @pytest.mark.asyncio
    async def test_export_with_exception(
        self, tools, mock_client, mock_context
    ):
        """Test exporting when an exception occurs."""
        # Setup
        mock_client.fetch_email.side_effect = Exception("Connection error")

        # Get the tool
        export_tool = tools["export_email_html"]

        # Call the tool
        result = await export_tool(
            folder="INBOX",
            uid=123,
            save_path="/tmp/test.html",
            ctx=mock_context,
        )

        # Assertions
        assert "Error" in result
        assert "Connection error" in result

    @pytest.mark.asyncio
    async def test_export_creates_directories(
        self, tools, mock_client, mock_context, email_with_html_no_images
    ):
        """Test that export creates necessary directories."""
        # Setup
        mock_client.fetch_email.return_value = email_with_html_no_images

        # Get the tool
        export_tool = tools["export_email_html"]

        # Create a temporary directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Use a path with nested directories that don't exist yet
            nested_path = os.path.join(tmp_dir, "subdir1", "subdir2", "email.html")

            # Call the tool
            result = await export_tool(
                folder="INBOX",
                uid=456,
                save_path=nested_path,
                ctx=mock_context,
            )

            # Assertions
            assert "Success" in result

            # Verify the file was created and directories were made
            assert os.path.exists(nested_path)
            with open(nested_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "<h1>Newsletter</h1>" in content

