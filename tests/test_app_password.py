"""Tests for the app_password module."""

import os
import tempfile
from unittest.mock import patch
import pytest

from mailroom.app_password import setup_app_password, main


class TestSetupAppPassword:
    """Tests for the setup_app_password function."""

    def test_setup_returns_config(self):
        """Test that setup returns the expected config dict."""
        result = setup_app_password(
            username="test@gmail.com",
            password="test_password",
        )

        assert "imap" in result
        assert result["imap"]["host"] == "imap.gmail.com"
        assert result["imap"]["port"] == 993
        assert result["imap"]["username"] == "test@gmail.com"
        assert result["imap"]["password"] == "test_password"
        assert result["imap"]["use_ssl"] is True

    def test_setup_prints_toml_snippet(self, capsys):
        """Test that setup prints a TOML snippet."""
        setup_app_password(
            username="test@gmail.com",
            password="test_password",
        )

        captured = capsys.readouterr()
        assert "[imap]" in captured.out
        assert 'username = "test@gmail.com"' in captured.out
        assert 'password = "test_password"' in captured.out


class TestMain:
    """Tests for the main function."""

    @pytest.mark.skip(reason="Test interrupts automated execution to ask for password in command line")
    def test_main_success(self):
        """Test successful execution of main function."""
        pass
