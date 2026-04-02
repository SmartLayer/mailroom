"""Main server implementation for IMAP MCP."""

import argparse
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional

from mcp.server.fastmcp import FastMCP

from imap_mcp.config import MultiAccountConfig, load_config
from imap_mcp.imap_client import ImapClient
from imap_mcp.resources import register_resources
from imap_mcp.tools import register_tools
from imap_mcp.mcp_protocol import extend_server

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("imap_mcp")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict]:
    """Server lifespan manager to handle IMAP client lifecycle.

    Creates one ``ImapClient`` per configured account and yields them as a
    dict keyed by account name.

    Args:
        server: MCP server instance

    Yields:
        Context dictionary with ``imap_clients`` dict and ``default_account``
    """
    config: MultiAccountConfig = getattr(server, "_config", None)
    if not config:
        config = load_config()

    if not isinstance(config, MultiAccountConfig):
        raise TypeError("Invalid server configuration")

    clients: Dict[str, ImapClient] = {}
    try:
        for name, acct in config.accounts.items():
            logger.info(f"Connecting to IMAP server for account '{name}'...")
            client = ImapClient(acct.imap, acct.allowed_folders)
            client.connect()
            clients[name] = client

        yield {
            "imap_clients": clients,
            "default_account": config.default_account,
        }
    finally:
        for name, client in clients.items():
            logger.info(f"Disconnecting from IMAP server for account '{name}'...")
            client.disconnect()


def create_server(config_path: Optional[str] = None, debug: bool = False) -> FastMCP:
    """Create and configure the MCP server.

    Args:
        config_path: Path to configuration file
        debug: Enable debug mode

    Returns:
        Configured MCP server instance
    """
    if debug:
        logger.setLevel(logging.DEBUG)

    config = load_config(config_path)

    server = FastMCP(
        "IMAP",
        description="IMAP Model Context Protocol server for email processing",
        version="0.1.0",
        lifespan=server_lifespan,
    )

    # Store config for access in the lifespan
    server._config = config

    # Create a throwaway client for tool/resource registration (not used at runtime)
    first_acct = config.accounts[config.default_account]
    imap_client = ImapClient(first_acct.imap, first_acct.allowed_folders)

    register_resources(server, imap_client)
    register_tools(server, imap_client)

    @server.tool()
    def server_status() -> str:
        """Get server status and configuration info."""
        lines = [
            f"server: IMAP MCP",
            f"version: 0.1.0",
            f"default_account: {config.default_account}",
            f"accounts: {', '.join(config.accounts.keys())}",
        ]
        for name, acct in config.accounts.items():
            lines.append(f"  [{name}] {acct.imap.username}@{acct.imap.host}:{acct.imap.port}")
            if acct.allowed_folders:
                lines.append(f"    allowed_folders: {acct.allowed_folders}")
        return "\n".join(lines)

    server = extend_server(server)

    return server


def main() -> None:
    """Run the IMAP MCP server."""
    parser = argparse.ArgumentParser(description="IMAP MCP Server")
    parser.add_argument(
        "--config", 
        help="Path to configuration file",
        default=os.environ.get("IMAP_MCP_CONFIG"),
    )
    parser.add_argument(
        "--dev", 
        action="store_true", 
        help="Enable development mode",
    )
    parser.add_argument(
        "--debug", 
        action="store_true", 
        help="Enable debug logging",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version information and exit",
    )
    args = parser.parse_args()
    
    if args.version:
        print("IMAP MCP Server version 0.1.0")
        return
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    server = create_server(args.config, args.debug)
    
    # Start the server
    logger.info("Starting server{}...".format(" in development mode" if args.dev else ""))
    server.run()
    
    
if __name__ == "__main__":
    main()
