"""Tests for the server module."""

import argparse
import logging
from contextlib import AsyncExitStack
from unittest import mock

import pytest
from mcp.server.fastmcp import FastMCP

from mailroom.config import AccountConfig, ImapConfig, MultiAccountConfig
from mailroom.mcp_server import create_server, main, server_lifespan


class TestServer:
    """Tests for the server module."""

    def test_create_server(self, monkeypatch):
        """Test server creation with default configuration."""
        mock_config = MultiAccountConfig(
            accounts={
                "test": AccountConfig(
                    imap=ImapConfig(
                        host="imap.example.com",
                        port=993,
                        username="test@example.com",
                        password="password",
                        use_ssl=True,
                    ),
                    allowed_folders=["INBOX", "Sent"],
                )
            },
        )

        with mock.patch("mailroom.mcp_server.load_config", return_value=mock_config):
            server = create_server()

            assert isinstance(server, FastMCP)
            assert server.name == "Mailroom"
            assert server._config == mock_config

            with mock.patch(
                "mailroom.mcp_server.register_resources"
            ) as mock_register_resources:
                with mock.patch(
                    "mailroom.mcp_server.register_tools"
                ) as mock_register_tools:
                    create_server()
                    assert mock_register_resources.called
                    assert mock_register_tools.called

    def test_create_server_with_debug(self):
        """Test server creation with debug mode enabled."""
        mock_config = MultiAccountConfig(
            accounts={
                "test": AccountConfig(
                    imap=ImapConfig(
                        host="localhost", port=993, username="test", password="pw"
                    ),
                )
            },
        )
        with mock.patch("mailroom.mcp_server.load_config", return_value=mock_config):
            with mock.patch("mailroom.mcp_server.logger") as mock_logger:
                create_server(debug=True)
                mock_logger.setLevel.assert_called_with(logging.DEBUG)

    def test_create_server_with_config_path(self):
        """Test server creation with a specific config path."""
        config_path = "test_config.toml"

        with mock.patch("mailroom.mcp_server.load_config") as mock_load_config:
            create_server(config_path=config_path)
            mock_load_config.assert_called_with(config_path)

    @pytest.mark.asyncio
    async def test_server_lifespan(self):
        """Test server lifespan context manager."""
        mock_server = mock.MagicMock()
        imap_cfg = ImapConfig(
            host="imap.example.com",
            port=993,
            username="test@example.com",
            password="password",
            use_ssl=True,
        )
        mock_config = MultiAccountConfig(
            accounts={"test": AccountConfig(imap=imap_cfg)},
        )
        mock_server._config = mock_config

        with mock.patch("mailroom.mcp_server.ImapClient") as MockImapClient:
            mock_client = MockImapClient.return_value

            async with AsyncExitStack() as stack:
                context = await stack.enter_async_context(server_lifespan(mock_server))

                MockImapClient.assert_called_once_with(imap_cfg, None)
                mock_client.connect.assert_called_once()
                assert context["imap_clients"]["test"] == mock_client
                assert context["default_account"] == "test"

            mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_server_lifespan_fallback_config(self):
        """Test server lifespan with fallback config loading."""
        mock_server = mock.MagicMock()
        mock_server._config = None

        mock_config = MultiAccountConfig(
            accounts={
                "test": AccountConfig(
                    imap=ImapConfig(
                        host="imap.example.com",
                        port=993,
                        username="test@example.com",
                        password="password",
                    ),
                )
            },
        )

        with mock.patch(
            "mailroom.mcp_server.load_config", return_value=mock_config
        ) as mock_load_config:
            with mock.patch("mailroom.mcp_server.ImapClient"):
                async with AsyncExitStack() as stack:
                    await stack.enter_async_context(server_lifespan(mock_server))
                    mock_load_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_server_lifespan_invalid_config(self):
        """Test server lifespan with invalid config."""
        # Create mock server with invalid config
        mock_server = mock.MagicMock()
        mock_server._config = "not a MultiAccountConfig object"

        # Verify TypeError is raised
        with pytest.raises(TypeError, match="Invalid server configuration"):
            async with server_lifespan(mock_server):
                pass

    def test_status_tool(self):
        """Test the status tool."""
        mock_config = MultiAccountConfig(
            accounts={
                "test": AccountConfig(
                    imap=ImapConfig(
                        host="imap.example.com",
                        port=993,
                        username="test@example.com",
                        password="password",
                        use_ssl=True,
                    ),
                    allowed_folders=["INBOX", "Sent"],
                )
            },
        )

        with mock.patch("mailroom.mcp_server.load_config", return_value=mock_config):
            server = create_server()
            assert server is not None

    def test_main_function(self):
        """Test the main function."""
        test_args = ["--config", "test_config.toml", "--debug", "--dev"]

        with mock.patch("sys.argv", ["server.py"] + test_args):
            with mock.patch("mailroom.mcp_server.create_server") as mock_create_server:
                with mock.patch(
                    "mailroom.mcp_server.argparse.ArgumentParser.parse_args"
                ) as mock_parse_args:
                    mock_args = argparse.Namespace(
                        config="test_config.toml",
                        debug=True,
                        dev=True,
                        version=False,
                    )
                    mock_parse_args.return_value = mock_args

                    mock_server = mock.MagicMock()
                    mock_create_server.return_value = mock_server

                    with mock.patch("mailroom.mcp_server.logger") as mock_logger:
                        main()

                        mock_create_server.assert_called_once_with(
                            "test_config.toml", True
                        )
                        mock_server.run.assert_called_once()
                        mock_logger.setLevel.assert_called_with(logging.DEBUG)

    def test_main_env_config(self, monkeypatch):
        """Test main function with config from environment variable."""
        monkeypatch.setenv("MAILROOM_CONFIG", "env_config.toml")

        with mock.patch("sys.argv", ["server.py"]):
            with mock.patch("mailroom.mcp_server.create_server") as mock_create_server:
                with mock.patch(
                    "mailroom.mcp_server.argparse.ArgumentParser.parse_args"
                ) as mock_parse_args:
                    mock_args = argparse.Namespace(
                        config="env_config.toml",
                        debug=False,
                        dev=False,
                        version=False,
                    )
                    mock_parse_args.return_value = mock_args

                    main()
                    mock_create_server.assert_called_once_with("env_config.toml", False)
