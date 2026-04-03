"""Tests for the config module."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mailroom.config import ImapConfig, MultiAccountConfig, AccountConfig, load_config


class TestImapConfig:
    """Test cases for the ImapConfig class."""

    def test_init(self):
        """Test ImapConfig initialization."""
        config = ImapConfig(
            host="imap.example.com",
            port=993,
            username="test@example.com",
            password="password"
        )

        assert config.host == "imap.example.com"
        assert config.port == 993
        assert config.username == "test@example.com"
        assert config.password == "password"
        assert config.use_ssl is True

        config = ImapConfig(
            host="imap.example.com",
            port=143,
            username="test@example.com",
            password="password",
            use_ssl=False
        )
        assert config.use_ssl is False

    def test_from_dict(self):
        """Test creating ImapConfig from a flat dictionary."""
        data = {
            "host": "imap.example.com",
            "port": 993,
            "username": "test@example.com",
            "password": "password",
            "use_ssl": True
        }

        config = ImapConfig.from_dict(data)
        assert config.host == "imap.example.com"
        assert config.port == 993
        assert config.password == "password"
        assert config.use_ssl is True
        assert config.oauth2 is None

    def test_from_dict_oauth2(self):
        """Test creating ImapConfig with OAuth2 from a flat dictionary."""
        data = {
            "host": "imap.gmail.com",
            "username": "test@gmail.com",
            "client_id": "my_id",
            "client_secret": "my_secret",
            "refresh_token": "my_token",
        }

        config = ImapConfig.from_dict(data)
        assert config.host == "imap.gmail.com"
        assert config.oauth2 is not None
        assert config.oauth2.client_id == "my_id"
        assert config.password is None

    def test_from_dict_defaults(self):
        """Test that port defaults to 993 for SSL, 143 for non-SSL."""
        ssl_data = {"host": "imap.example.com", "username": "u", "password": "p"}
        assert ImapConfig.from_dict(ssl_data).port == 993

        non_ssl_data = {"host": "imap.example.com", "username": "u", "password": "p", "use_ssl": False}
        assert ImapConfig.from_dict(non_ssl_data).port == 143

    def test_from_dict_global_defaults(self):
        """Test that global defaults are inherited."""
        data = {"host": "imap.example.com", "username": "u", "password": "p"}
        defaults = {"idle_timeout": 600, "verify_with_noop": False}

        config = ImapConfig.from_dict(data, defaults)
        assert config.idle_timeout == 600
        assert config.verify_with_noop is False

    def test_from_dict_account_overrides_global(self):
        """Test that per-account values override global defaults."""
        data = {"host": "imap.example.com", "username": "u", "password": "p", "idle_timeout": 60}
        defaults = {"idle_timeout": 600}

        config = ImapConfig.from_dict(data, defaults)
        assert config.idle_timeout == 60

    def test_from_dict_with_env_password(self, monkeypatch):
        """Test creating ImapConfig with password from environment variable."""
        monkeypatch.setenv("IMAP_PASSWORD", "env_password")

        data = {"host": "imap.example.com", "username": "test@example.com"}
        config = ImapConfig.from_dict(data)
        assert config.password == "env_password"

        data_with_password = {"host": "imap.example.com", "username": "test@example.com", "password": "dict_password"}
        config = ImapConfig.from_dict(data_with_password)
        assert config.password == "dict_password"

    def test_from_dict_missing_password(self, monkeypatch):
        """Test error when password is missing from both dict and environment."""
        monkeypatch.delenv("IMAP_PASSWORD", raising=False)

        data = {"host": "imap.example.com", "username": "test@example.com"}

        with pytest.raises(ValueError, match="IMAP password must be specified"):
            ImapConfig.from_dict(data)

    def test_from_dict_missing_required_fields(self):
        """Test error when required fields are missing."""
        with pytest.raises(KeyError):
            ImapConfig.from_dict({"username": "test@example.com", "password": "password"})

        with pytest.raises(KeyError):
            ImapConfig.from_dict({"host": "imap.example.com", "password": "password"})


class TestAccountConfig:
    """Test cases for AccountConfig."""

    def test_from_flat_dict(self):
        """Test creating AccountConfig from a flat dictionary."""
        data = {
            "host": "imap.example.com",
            "port": 993,
            "username": "test@example.com",
            "password": "password",
            "allowed_folders": ["INBOX", "Sent"],
        }

        acct = AccountConfig.from_dict(data)
        assert acct.imap.host == "imap.example.com"
        assert acct.imap.password == "password"
        assert acct.allowed_folders == ["INBOX", "Sent"]


class TestMultiAccountConfig:
    """Test cases for MultiAccountConfig."""

    def test_default_account_explicit(self):
        """Test explicit default_account."""
        imap = ImapConfig(host="h", port=993, username="u", password="p")
        cfg = MultiAccountConfig(
            accounts={"a": AccountConfig(imap=imap), "b": AccountConfig(imap=imap)},
            _default_account="b",
        )
        assert cfg.default_account == "b"

    def test_default_account_fallback(self):
        """Test default_account falls back to first account."""
        imap = ImapConfig(host="h", port=993, username="u", password="p")
        cfg = MultiAccountConfig(accounts={"first": AccountConfig(imap=imap)})
        assert cfg.default_account == "first"


class TestLoadConfig:
    """Test cases for the load_config function."""

    def test_load_flat_accounts(self):
        """Test loading the new flat account format."""
        toml_content = """\
default_account = "work"

[accounts.personal]
host = "imap.gmail.com"
username = "me@gmail.com"
client_id = "cid"
client_secret = "csec"
refresh_token = "rtok"

[accounts.work]
host = "imap.fastmail.com"
username = "me@company.com"
password = "secret"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb") as f:
            f.write(toml_content.encode())
            f.flush()

            config = load_config(f.name)

            assert isinstance(config, MultiAccountConfig)
            assert config.default_account == "work"
            assert "personal" in config.accounts
            assert "work" in config.accounts

            personal = config.accounts["personal"]
            assert personal.imap.host == "imap.gmail.com"
            assert personal.imap.oauth2 is not None
            assert personal.imap.oauth2.client_id == "cid"

            work = config.accounts["work"]
            assert work.imap.host == "imap.fastmail.com"
            assert work.imap.password == "secret"
            assert work.imap.oauth2 is None

    def test_load_global_defaults(self):
        """Test that global idle_timeout is inherited by accounts."""
        toml_content = """\
idle_timeout = 600

[accounts.test]
host = "imap.example.com"
username = "u"
password = "p"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb") as f:
            f.write(toml_content.encode())
            f.flush()

            config = load_config(f.name)
            assert config.accounts["test"].imap.idle_timeout == 600

    def test_load_from_default_locations(self, monkeypatch, tmp_path):
        """Test loading configuration from default locations."""
        for env_var in ["IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
                        "IMAP_USE_SSL", "IMAP_ALLOWED_FOLDERS"]:
            monkeypatch.delenv(env_var, raising=False)

        toml_content = """\
[accounts.test]
host = "imap.example.com"
username = "test@example.com"
password = "password"
"""
        temp_dir = tmp_path / ".config" / "mailroom"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / "config.toml"
        temp_file.write_bytes(toml_content.encode())

        original_expanduser = Path.expanduser
        def mock_expanduser(self):
            if str(self) == "~/.config/mailroom/config.toml":
                return temp_file
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", mock_expanduser)

        def mock_exists(path):
            return path == temp_file

        monkeypatch.setattr(Path, "exists", mock_exists)

        config = load_config()
        assert config.accounts["test"].imap.host == "imap.example.com"

    def test_load_from_env_variables(self, monkeypatch):
        """Test loading configuration from environment variables."""
        monkeypatch.setenv("IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("IMAP_PORT", "993")
        monkeypatch.setenv("IMAP_USERNAME", "test@example.com")
        monkeypatch.setenv("IMAP_PASSWORD", "env_password")
        monkeypatch.setenv("IMAP_USE_SSL", "true")
        monkeypatch.setenv("IMAP_ALLOWED_FOLDERS", "INBOX,Sent,Archive")

        original_open = open
        def mock_open(*args, **kwargs):
            if args[0] == "nonexistent_file.toml":
                raise FileNotFoundError(f"No such file: {args[0]}")
            return original_open(*args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            config = load_config("nonexistent_file.toml")

            acct = config.accounts["default"]
            assert acct.imap.host == "imap.example.com"
            assert acct.imap.password == "env_password"
            assert acct.allowed_folders == ["INBOX", "Sent", "Archive"]

    def test_load_missing_required_env(self, monkeypatch):
        """Test error when required environment variables are missing."""
        monkeypatch.delenv("IMAP_HOST", raising=False)

        original_open = open
        def mock_open(*args, **kwargs):
            if args[0] == "nonexistent_file.toml":
                raise FileNotFoundError(f"No such file: {args[0]}")
            return original_open(*args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            with pytest.raises(ValueError, match="IMAP_HOST"):
                load_config("nonexistent_file.toml")

    def test_invalid_config_missing_host(self):
        """Test error when config is missing required host."""
        toml_content = """\
[accounts.test]
username = "test@example.com"
password = "password"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb") as f:
            f.write(toml_content.encode())
            f.flush()

            with pytest.raises(ValueError, match="Missing required configuration"):
                load_config(f.name)

    def test_no_accounts(self):
        """Test error when no accounts are defined."""
        toml_content = """\
idle_timeout = 300
"""
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb") as f:
            f.write(toml_content.encode())
            f.flush()

            with pytest.raises(ValueError, match="No accounts defined"):
                load_config(f.name)
