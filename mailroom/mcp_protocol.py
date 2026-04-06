"""MCP Protocol Implementation for Mailroom.

This module implements the required MCP protocol methods that are not directly
supported by FastMCP but needed for Claude desktop compatibility.
"""

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def extend_server(server: FastMCP) -> FastMCP:
    """Extend a FastMCP server with additional MCP protocol methods.

    Args:
        server: The FastMCP server instance to extend

    Returns:
        The extended server instance
    """

    # Register resources methods
    @server.resource("email://folders")
    def email_folders() -> str:
        """List all available email folders."""
        logger.info("Accessing email folders resource")

        # Get the IMAP client from the server's context if available
        if hasattr(server, "_lifespan_context") and server._lifespan_context:
            imap_client = server._lifespan_context.get("imap_client")
            if imap_client:
                folders = imap_client.list_folders()
                return "\n".join(folders)

        return "No email folders available"

    # Register prompts
    @server.prompt(name="search")
    def search(query: str) -> str:
        """Create prompt for searching emails.

        Args:
            query: Search query for emails

        Returns:
            Formatted prompt string
        """
        return f"Search for emails that match: {query}"

    @server.prompt()
    def compose_email(to: str, subject: str = "", body: str = "") -> str:
        """Create prompt for composing a new email.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body content

        Returns:
            Formatted prompt string
        """
        return f"Compose an email to: {to}\nSubject: {subject}\n\n{body}"

    # Add Claude Desktop compatibility methods
    # These are added using direct registration since they may not fit
    # the standard FastMCP pattern, but are needed for Claude desktop

    # Low-level method registration if FastMCP doesn't have built-in support
    # for some methods needed by Claude Desktop
    if hasattr(server, "_low_level_server"):
        low_level = server._low_level_server

        # Register any additional methods that aren't directly supported by FastMCP
        # Only do this if the standard FastMCP decorators don't cover the method
        if not hasattr(low_level, "has_method") or not low_level.has_method(
            "sampling/createMessage"
        ):
            logger.info(
                "Registering additional low-level methods for Claude desktop compatibility"
            )

            # Note: This is a fallback approach that should rarely be needed
            # as FastMCP should handle most standard MCP methods

            # Implement any additional methods if needed here

    return server
