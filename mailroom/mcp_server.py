"""Mailroom MCP server — exposes email operations as MCP tools."""

import argparse
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional

from mcp.server.fastmcp import FastMCP

from mailroom import __version__
from mailroom.config import MultiAccountConfig, load_config
from mailroom.imap_client import ImapClient
from mailroom.local_cache import MuBackend
from mailroom.mcp_protocol import extend_server
from mailroom.resources import register_resources
from mailroom.tools import register_tools

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mailroom")


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
    config_attr = getattr(server, "_config", None)
    config: MultiAccountConfig
    if config_attr is None:
        config = load_config()
    elif isinstance(config_attr, MultiAccountConfig):
        config = config_attr
    else:
        raise TypeError("Invalid server configuration")

    mu_backend = MuBackend(config.local_cache) if config.local_cache else None

    clients: Dict[str, ImapClient] = {}
    try:
        for name, acct in config.accounts.items():
            logger.info(f"Connecting to IMAP server for account '{name}'...")
            client = ImapClient(
                acct.imap,
                acct.allowed_folders,
                local_cache=mu_backend,
                account_cfg=acct,
            )
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
        "Mailroom",
        instructions="Email toolkit for AI assistants",
        lifespan=server_lifespan,
    )

    # Store config for access in the lifespan
    server._config = config  # type: ignore[attr-defined]

    # Create a throwaway client for tool/resource registration (not used at runtime)
    first_acct = config.accounts[config.default_account]
    mu_backend = MuBackend(config.local_cache) if config.local_cache else None
    imap_client = ImapClient(
        first_acct.imap,
        first_acct.allowed_folders,
        local_cache=mu_backend,
        account_cfg=first_acct,
    )

    register_resources(server, imap_client)
    register_tools(server, imap_client)

    @server.tool(name="status")
    def status() -> str:
        """Get server status and configuration info."""
        lines = [
            "server: Mailroom",
            f"version: {__version__}",
            f"default_account: {config.default_account}",
            f"accounts: {', '.join(config.accounts.keys())}",
        ]
        for name, acct in config.accounts.items():
            lines.append(
                f"  [{name}] {acct.imap.username}@{acct.imap.host}:{acct.imap.port}"
            )
            if acct.allowed_folders:
                lines.append(f"    allowed_folders: {acct.allowed_folders}")
        return "\n".join(lines)

    server = extend_server(server)

    return server


def main() -> None:
    """Run the Mailroom MCP server."""
    parser = argparse.ArgumentParser(description="Mailroom MCP Server")
    parser.add_argument(
        "--config",
        help="Path to configuration file",
        default=os.environ.get("MAILROOM_CONFIG"),
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
        print(f"Mailroom MCP server version {__version__}")
        return

    if args.debug:
        logger.setLevel(logging.DEBUG)

    server = create_server(args.config, args.debug)

    # Start the server
    logger.info(
        "Starting server{}...".format(" in development mode" if args.dev else "")
    )
    server.run()


if __name__ == "__main__":
    main()
