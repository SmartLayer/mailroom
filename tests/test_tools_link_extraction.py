"""Tests for link extraction from email HTML."""

import json
from unittest.mock import MagicMock

import pytest

from mailroom.models import Email, EmailAddress, EmailContent


def _make_email(html):
    """Helper to build an Email with given HTML."""
    return Email(
        message_id="<test@example.com>",
        subject="Test",
        from_=EmailAddress(name="S", address="s@x.com"),
        to=[EmailAddress(name="R", address="r@x.com")],
        content=EmailContent(html=html),
    )


class TestExtractLinksFromHtml:
    """Test the Email.extract_links method."""

    def test_extract_simple_links(self):
        """Test extracting simple links from HTML."""
        links = _make_email("""
        <html>
            <body>
                <a href="https://example.com">Example Site</a>
                <a href="https://google.com">Google</a>
            </body>
        </html>
        """).extract_links()

        assert len(links) == 2
        assert links[0] == {
            "url": "https://example.com",
            "anchor": "Example Site",
            "position": 1,
        }
        assert links[1] == {
            "url": "https://google.com",
            "anchor": "Google",
            "position": 2,
        }

    def test_extract_multiline_link(self):
        """Test extracting links that span multiple lines."""
        links = _make_email("""
        <a
            href="https://example.com/verify"
            class="button"
            style="color: blue;"
        >
            Click here to
            verify your account
        </a>
        """).extract_links()

        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/verify"
        assert links[0]["anchor"] == "Click here to verify your account"
        assert links[0]["position"] == 1

    def test_deduplicate_repeated_urls(self):
        """Test that repeated URLs are deduplicated (first occurrence kept)."""
        links = _make_email("""
        <a href="https://example.com">First Link</a>
        <a href="https://google.com">Google</a>
        <a href="https://example.com">Duplicate Link</a>
        <a href="https://example.com">Another Duplicate</a>
        """).extract_links()

        assert len(links) == 2
        assert links[0]["url"] == "https://example.com"
        assert links[0]["anchor"] == "First Link"
        assert links[1]["url"] == "https://google.com"

    def test_links_with_no_anchor_text(self):
        """Test links with no anchor text (e.g., image links)."""
        links = _make_email("""
        <a href="https://example.com"></a>
        <a href="https://tracking.com/pixel.gif"><img src="pixel.gif"></a>
        """).extract_links()

        assert len(links) == 2
        assert links[0]["url"] == "https://example.com"
        assert links[0]["anchor"] == ""
        assert links[1]["url"] == "https://tracking.com/pixel.gif"
        assert links[1]["anchor"] == ""

    def test_links_with_html_entities(self):
        """Test links with HTML entities in anchor text."""
        links = _make_email("""
        <a href="https://example.com">Click &amp; Verify</a>
        <a href="https://test.com">Test &lt;Company&gt;</a>
        <a href="https://quote.com">&quot;Quoted Text&quot;</a>
        """).extract_links()

        assert len(links) == 3
        assert links[0]["anchor"] == "Click & Verify"
        assert links[1]["anchor"] == "Test <Company>"
        assert links[2]["anchor"] == '"Quoted Text"'

    def test_links_with_nested_html(self):
        """Test links with nested HTML elements."""
        links = _make_email("""
        <a href="https://example.com">
            <span style="color: red;">Important</span>
            <strong>Action Required</strong>
        </a>
        """).extract_links()

        assert len(links) == 1
        assert links[0]["anchor"] == "Important Action Required"

    def test_empty_html(self):
        """Test with empty HTML."""
        assert _make_email("").extract_links() == []
        assert _make_email(None).extract_links() == []

    def test_html_with_no_links(self):
        """Test HTML content with no links."""
        links = _make_email("""
        <html>
            <body>
                <p>This is a paragraph with no links.</p>
                <div>Just some text content.</div>
            </body>
        </html>
        """).extract_links()
        assert links == []

    def test_case_insensitive_tags(self):
        """Test that link extraction is case-insensitive."""
        links = _make_email("""
        <A HREF="https://example.com">Upper Case</A>
        <a HREF="https://test.com">Mixed Case</a>
        """).extract_links()

        assert len(links) == 2
        assert links[0]["url"] == "https://example.com"
        assert links[1]["url"] == "https://test.com"

    def test_single_vs_double_quotes(self):
        """Test links with single and double quotes."""
        links = _make_email("""
        <a href="https://example.com">Double Quotes</a>
        <a href='https://test.com'>Single Quotes</a>
        """).extract_links()

        assert len(links) == 2
        assert links[0]["url"] == "https://example.com"
        assert links[1]["url"] == "https://test.com"

    def test_attributes_before_and_after_href(self):
        """Test links with attributes before and after href."""
        links = _make_email("""
        <a class="btn" href="https://example.com" id="link1">Before and After</a>
        <a href="https://test.com" target="_blank" rel="noopener">After Only</a>
        """).extract_links()

        assert len(links) == 2
        assert links[0]["url"] == "https://example.com"
        assert links[1]["url"] == "https://test.com"

    def test_relative_and_absolute_urls(self):
        """Test extraction of both relative and absolute URLs."""
        links = _make_email("""
        <a href="https://example.com/page">Absolute URL</a>
        <a href="/relative/path">Relative Path</a>
        <a href="#anchor">Anchor Link</a>
        <a href="mailto:test@example.com">Email Link</a>
        """).extract_links()

        assert len(links) == 4
        assert links[0]["url"] == "https://example.com/page"
        assert links[1]["url"] == "/relative/path"
        assert links[2]["url"] == "#anchor"
        assert links[3]["url"] == "mailto:test@example.com"

    def test_position_tracking(self):
        """Test that position is correctly tracked for unique URLs."""
        links = _make_email("""
        <a href="https://example.com">Link 1</a>
        <a href="https://test.com">Link 2</a>
        <a href="https://example.com">Duplicate</a>
        <a href="https://third.com">Link 3</a>
        """).extract_links()

        assert len(links) == 3
        assert links[0]["position"] == 1
        assert links[1]["position"] == 2
        assert links[2]["position"] == 3


@pytest.mark.asyncio
class TestLinksTool:
    """Test the links MCP tool."""

    async def test_extract_links_from_email(self, mock_context):
        """Test extracting links from an email with HTML content."""
        from mcp.server.fastmcp import FastMCP

        from mailroom.tools import register_tools

        # Create mock email with HTML content
        html_content = """
        <html>
            <body>
                <p>Welcome to our service!</p>
                <a href="https://example.com/verify">Verify your account</a>
                <p>Or contact us:</p>
                <a href="mailto:support@example.com">Email Support</a>
            </body>
        </html>
        """

        mock_email = Email(
            message_id="<test@example.com>",
            subject="Test Email",
            from_=EmailAddress("sender@example.com", "Sender"),
            to=[EmailAddress("recipient@example.com", "Recipient")],
            content=EmailContent(text="Plain text version", html=html_content),
        )

        # Set up mock client
        mock_client = MagicMock()
        mock_client.fetch_email.return_value = mock_email
        mock_context.request_context.lifespan_context = {"imap_client": mock_client}

        # Create MCP server and register tools
        mcp = FastMCP("test")
        register_tools(mcp, mock_client)

        # Get the tool function
        extract_tool = None
        for tool_func in mcp._tool_manager._tools.values():
            if tool_func.fn.__name__ == "links":
                extract_tool = tool_func.fn
                break

        assert extract_tool is not None, "links tool not found"

        # Call the tool with a list of UIDs
        result = await extract_tool("INBOX", [123], mock_context)

        # Parse result
        results = json.loads(result)

        assert len(results) == 1
        assert results[0]["uid"] == 123
        assert len(results[0]["links"]) == 2
        assert results[0]["links"][0]["url"] == "https://example.com/verify"
        assert results[0]["links"][0]["anchor"] == "Verify your account"
        assert results[0]["links"][1]["url"] == "mailto:support@example.com"
        assert results[0]["links"][1]["anchor"] == "Email Support"

    async def test_extract_links_multiple_emails(self, mock_context):
        """Test extracting links from multiple emails."""
        from mcp.server.fastmcp import FastMCP

        from mailroom.tools import register_tools

        # Create two mock emails
        html1 = '<html><body><a href="https://example.com">Link 1</a></body></html>'
        html2 = '<html><body><a href="https://test.com">Link 2</a></body></html>'

        email1 = Email(
            message_id="<test1@example.com>",
            subject="Email 1",
            from_=EmailAddress("sender@example.com", "Sender"),
            to=[EmailAddress("recipient@example.com", "Recipient")],
            content=EmailContent(html=html1),
        )

        email2 = Email(
            message_id="<test2@example.com>",
            subject="Email 2",
            from_=EmailAddress("sender@example.com", "Sender"),
            to=[EmailAddress("recipient@example.com", "Recipient")],
            content=EmailContent(html=html2),
        )

        # Set up mock client to return different emails for different UIDs
        mock_client = MagicMock()

        def fetch_side_effect(uid, folder):
            if uid == 100:
                return email1
            elif uid == 200:
                return email2
            return None

        mock_client.fetch_email.side_effect = fetch_side_effect
        mock_context.request_context.lifespan_context = {"imap_client": mock_client}

        # Create MCP server and register tools
        mcp = FastMCP("test")
        register_tools(mcp, mock_client)

        # Get the tool function
        extract_tool = None
        for tool_func in mcp._tool_manager._tools.values():
            if tool_func.fn.__name__ == "links":
                extract_tool = tool_func.fn
                break

        assert extract_tool is not None

        # Call the tool with multiple UIDs
        result = await extract_tool("INBOX", [100, 200], mock_context)

        # Parse result
        results = json.loads(result)

        assert len(results) == 2
        assert results[0]["uid"] == 100
        assert len(results[0]["links"]) == 1
        assert results[0]["links"][0]["url"] == "https://example.com"
        assert results[1]["uid"] == 200
        assert len(results[1]["links"]) == 1
        assert results[1]["links"][0]["url"] == "https://test.com"

    async def test_extract_links_email_not_found(self, mock_context):
        """Test error when email is not found."""
        from mcp.server.fastmcp import FastMCP

        from mailroom.tools import register_tools

        # Set up mock client that returns None
        mock_client = MagicMock()
        mock_client.fetch_email.return_value = None
        mock_context.request_context.lifespan_context = {"imap_client": mock_client}

        # Create MCP server and register tools
        mcp = FastMCP("test")
        register_tools(mcp, mock_client)

        # Get the tool function
        extract_tool = None
        for tool_func in mcp._tool_manager._tools.values():
            if tool_func.fn.__name__ == "links":
                extract_tool = tool_func.fn
                break

        assert extract_tool is not None

        # Call the tool
        result = await extract_tool("INBOX", [999], mock_context)

        # Parse result
        results = json.loads(result)
        assert len(results) == 1
        assert results[0]["uid"] == 999
        assert "error" in results[0]
        assert "not found" in results[0]["error"]

    async def test_extract_links_no_html_content(self, mock_context):
        """Test error when email has no HTML content."""
        from mcp.server.fastmcp import FastMCP

        from mailroom.tools import register_tools

        # Create mock email with only plain text
        mock_email = Email(
            message_id="<test@example.com>",
            subject="Test Email",
            from_=EmailAddress("sender@example.com", "Sender"),
            to=[EmailAddress("recipient@example.com", "Recipient")],
            content=EmailContent(text="Plain text only, no HTML"),
        )

        # Set up mock client
        mock_client = MagicMock()
        mock_client.fetch_email.return_value = mock_email
        mock_context.request_context.lifespan_context = {"imap_client": mock_client}

        # Create MCP server and register tools
        mcp = FastMCP("test")
        register_tools(mcp, mock_client)

        # Get the tool function
        extract_tool = None
        for tool_func in mcp._tool_manager._tools.values():
            if tool_func.fn.__name__ == "links":
                extract_tool = tool_func.fn
                break

        assert extract_tool is not None

        # Call the tool
        result = await extract_tool("INBOX", [123], mock_context)

        # Parse result
        results = json.loads(result)
        assert len(results) == 1
        assert results[0]["uid"] == 123
        assert "error" in results[0]
        assert "no HTML content" in results[0]["error"]

    async def test_extract_links_html_with_no_links(self, mock_context):
        """Test with HTML content that has no links."""
        from mcp.server.fastmcp import FastMCP

        from mailroom.tools import register_tools

        # Create mock email with HTML but no links
        mock_email = Email(
            message_id="<test@example.com>",
            subject="Test Email",
            from_=EmailAddress("sender@example.com", "Sender"),
            to=[EmailAddress("recipient@example.com", "Recipient")],
            content=EmailContent(
                text="Plain text",
                html="<html><body><p>Just plain text, no links.</p></body></html>",
            ),
        )

        # Set up mock client
        mock_client = MagicMock()
        mock_client.fetch_email.return_value = mock_email
        mock_context.request_context.lifespan_context = {"imap_client": mock_client}

        # Create MCP server and register tools
        mcp = FastMCP("test")
        register_tools(mcp, mock_client)

        # Get the tool function
        extract_tool = None
        for tool_func in mcp._tool_manager._tools.values():
            if tool_func.fn.__name__ == "links":
                extract_tool = tool_func.fn
                break

        assert extract_tool is not None

        # Call the tool
        result = await extract_tool("INBOX", [123], mock_context)

        # Parse result
        results = json.loads(result)
        assert len(results) == 1
        assert results[0]["uid"] == 123
        assert results[0]["links"] == []

    async def test_extract_links_deduplication(self, mock_context):
        """Test that duplicate URLs are deduplicated per email."""
        from mcp.server.fastmcp import FastMCP

        from mailroom.tools import register_tools

        # Create mock email with duplicate links
        html_content = """
        <html>
            <body>
                <a href="https://example.com">First Link</a>
                <a href="https://example.com">Duplicate Link</a>
                <a href="https://test.com">Different Link</a>
            </body>
        </html>
        """

        mock_email = Email(
            message_id="<test@example.com>",
            subject="Test Email",
            from_=EmailAddress("sender@example.com", "Sender"),
            to=[EmailAddress("recipient@example.com", "Recipient")],
            content=EmailContent(html=html_content),
        )

        # Set up mock client
        mock_client = MagicMock()
        mock_client.fetch_email.return_value = mock_email
        mock_context.request_context.lifespan_context = {"imap_client": mock_client}

        # Create MCP server and register tools
        mcp = FastMCP("test")
        register_tools(mcp, mock_client)

        # Get the tool function
        extract_tool = None
        for tool_func in mcp._tool_manager._tools.values():
            if tool_func.fn.__name__ == "links":
                extract_tool = tool_func.fn
                break

        assert extract_tool is not None

        # Call the tool
        result = await extract_tool("INBOX", [123], mock_context)

        # Parse result
        results = json.loads(result)

        # Should only have 2 unique URLs
        assert len(results) == 1
        assert results[0]["uid"] == 123
        assert len(results[0]["links"]) == 2
        assert results[0]["links"][0]["url"] == "https://example.com"
        assert results[0]["links"][0]["anchor"] == "First Link"  # First occurrence kept
        assert results[0]["links"][1]["url"] == "https://test.com"


@pytest.fixture
def mock_context():
    """Create a mock MCP context."""
    context = MagicMock()
    context.request_context = MagicMock()
    context.request_context.lifespan_context = {}
    return context
