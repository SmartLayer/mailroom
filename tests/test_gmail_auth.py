"""Tests for the Gmail authentication module."""

import argparse
import pytest
from unittest.mock import patch, MagicMock

from mailroom.gmail_auth import main


def test_parse_arguments():
    """Test argument parsing."""
    test_args = [
        "--client-id", "test_client_id",
        "--client-secret", "test_client_secret",
        "--credentials-file", "creds.json",
        "--port", "9000",
    ]

    with patch("sys.argv", ["gmail_auth.py"] + test_args), \
         patch("mailroom.gmail_auth.perform_oauth_flow") as mock_oauth_flow:

        mock_oauth_flow.return_value = {"refresh_token": "test_token"}

        main()

        mock_oauth_flow.assert_called_once_with(
            client_id="test_client_id",
            client_secret="test_client_secret",
            credentials_file="creds.json",
            port=9000,
        )
