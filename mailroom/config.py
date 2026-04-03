"""Configuration handling for Mailroom."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file if it exists
load_dotenv()


@dataclass
class OAuth2Config:
    """OAuth2 configuration for IMAP authentication."""
    
    client_id: str
    client_secret: str
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None
    token_expiry: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["OAuth2Config"]:
        """Create OAuth2 configuration from dictionary."""
        if not data:
            return None
            
        # OAuth2 credentials can be specified in environment variables
        client_id = data.get("client_id") or os.environ.get("GMAIL_CLIENT_ID")
        client_secret = data.get("client_secret") or os.environ.get("GMAIL_CLIENT_SECRET")
        refresh_token = data.get("refresh_token") or os.environ.get("GMAIL_REFRESH_TOKEN")
        
        if not client_id or not client_secret:
            return None
            
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            access_token=data.get("access_token"),
            token_expiry=data.get("token_expiry"),
        )


@dataclass
class ImapConfig:
    """IMAP server configuration."""
    
    host: str
    port: int
    username: str
    password: Optional[str] = None
    oauth2: Optional[OAuth2Config] = None
    use_ssl: bool = True
    idle_timeout: int = 300  # seconds: 0 = close after each call, -1 = never, >0 = timeout
    verify_with_noop: bool = True  # send NOOP to verify connection health
    
    @property
    def is_gmail(self) -> bool:
        """Check if this is a Gmail configuration."""
        return self.host.endswith("gmail.com") or self.host.endswith("googlemail.com")
    
    @property
    def requires_oauth2(self) -> bool:
        """Check if this configuration requires OAuth2."""
        return self.is_gmail and self.oauth2 is not None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImapConfig":
        """Create configuration from dictionary."""
        # Create OAuth2 config if present
        oauth2_config = OAuth2Config.from_dict(data.get("oauth2", {}))
        
        # Password can be specified in environment variable
        password = data.get("password") or os.environ.get("IMAP_PASSWORD")
        
        # For Gmail, we need either password (for app-specific password) or OAuth2 credentials
        host = data.get("host", "")
        is_gmail = host.endswith("gmail.com") or host.endswith("googlemail.com")
        
        if is_gmail and not oauth2_config and not password:
            raise ValueError(
                "Gmail requires either an app-specific password or OAuth2 credentials"
            )
        elif not is_gmail and not password:
            raise ValueError(
                "IMAP password must be specified in config or IMAP_PASSWORD environment variable"
            )
        
        return cls(
            host=data["host"],
            port=data.get("port", 993 if data.get("use_ssl", True) else 143),
            username=data["username"],
            password=password,
            oauth2=oauth2_config,
            use_ssl=data.get("use_ssl", True),
            idle_timeout=data.get("idle_timeout", 300),
            verify_with_noop=data.get("verify_with_noop", True),
        )


@dataclass
class ServerConfig:
    """MCP server configuration (single-account, kept for backward compat)."""

    imap: ImapConfig
    allowed_folders: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerConfig":
        """Create configuration from dictionary."""
        return cls(
            imap=ImapConfig.from_dict(data.get("imap", {})),
            allowed_folders=data.get("allowed_folders"),
        )


@dataclass
class AccountConfig:
    """Configuration for a single email account."""

    imap: ImapConfig
    allowed_folders: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountConfig":
        """Create account configuration from dictionary."""
        return cls(
            imap=ImapConfig.from_dict(data.get("imap", {})),
            allowed_folders=data.get("allowed_folders"),
        )


@dataclass
class MultiAccountConfig:
    """Multi-account MCP server configuration."""

    accounts: Dict[str, AccountConfig]

    @property
    def default_account(self) -> str:
        """First account is the default."""
        return next(iter(self.accounts))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MultiAccountConfig":
        """Create multi-account configuration from dictionary."""
        accounts = {}
        for name, account_data in data.get("accounts", {}).items():
            accounts[name] = AccountConfig.from_dict(account_data)

        if not accounts:
            raise ValueError("No accounts defined in multi-account configuration")

        return cls(accounts=accounts)

    @classmethod
    def from_single(cls, config: ServerConfig) -> "MultiAccountConfig":
        """Wrap a single-account ServerConfig into MultiAccountConfig."""
        # Derive account name from username (e.g. "admin@rivermill.au" -> "admin")
        name = config.imap.username.split("@")[0] if config.imap.username else "default"
        account = AccountConfig(imap=config.imap, allowed_folders=config.allowed_folders)
        return cls(accounts={name: account})


def _load_config_data(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load raw configuration data from file or environment variables.

    Args:
        config_path: Path to configuration file

    Returns:
        Raw configuration dictionary

    Raises:
        ValueError: If no configuration source is available
    """
    default_locations = [
        Path("config.yaml"),
        Path("config.yml"),
        Path("~/.config/mailroom/config.yaml"),
        Path("/etc/mailroom/config.yaml"),
    ]

    config_data: Dict[str, Any] = {}
    if config_path:
        try:
            with open(config_path, "r") as f:
                config_data = yaml.safe_load(f) or {}
            logger.info(f"Loaded configuration from {config_path}")
        except FileNotFoundError:
            logger.warning(f"Configuration file not found: {config_path}")
    else:
        for path in default_locations:
            expanded_path = path.expanduser()
            if expanded_path.exists():
                with open(expanded_path, "r") as f:
                    config_data = yaml.safe_load(f) or {}
                logger.info(f"Loaded configuration from {expanded_path}")
                break

    if not config_data:
        logger.info("No configuration file found, using environment variables")
        if not os.environ.get("IMAP_HOST"):
            raise ValueError(
                "No configuration file found and IMAP_HOST environment variable not set"
            )

        config_data = {
            "imap": {
                "host": os.environ.get("IMAP_HOST"),
                "port": int(os.environ.get("IMAP_PORT", "993")),
                "username": os.environ.get("IMAP_USERNAME"),
                "password": os.environ.get("IMAP_PASSWORD"),
                "use_ssl": os.environ.get("IMAP_USE_SSL", "true").lower() == "true",
                "idle_timeout": int(os.environ.get("IMAP_IDLE_TIMEOUT", "300")),
                "verify_with_noop": os.environ.get("IMAP_VERIFY_WITH_NOOP", "true").lower() == "true",
            }
        }

        if os.environ.get("IMAP_ALLOWED_FOLDERS"):
            config_data["allowed_folders"] = os.environ.get("IMAP_ALLOWED_FOLDERS").split(",")

    return config_data


def load_config(config_path: Optional[str] = None) -> MultiAccountConfig:
    """Load configuration from file or environment variables.

    Supports both single-account (``imap:`` top-level key) and multi-account
    (``accounts:`` top-level key) formats.  Single-account configs are
    automatically wrapped into a one-entry ``MultiAccountConfig``.

    Args:
        config_path: Path to configuration file

    Returns:
        Multi-account server configuration

    Raises:
        ValueError: If configuration is invalid
    """
    config_data = _load_config_data(config_path)

    try:
        if "accounts" in config_data:
            return MultiAccountConfig.from_dict(config_data)
        # Single-account format — wrap into MultiAccountConfig
        server_config = ServerConfig.from_dict(config_data)
        return MultiAccountConfig.from_single(server_config)
    except KeyError as e:
        raise ValueError(f"Missing required configuration: {e}")
