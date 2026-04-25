"""Configuration handling for Mailroom."""

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file if it exists
load_dotenv()

# Keys that are global (top-level), not per-account
_GLOBAL_KEYS = {
    "default_account",
    "accounts",
    "idle_timeout",
    "verify_with_noop",
    "local_cache",
}

# Keys that signal OAuth2 auth (vs password auth)
_OAUTH2_KEYS = {
    "client_id",
    "client_secret",
    "refresh_token",
    "access_token",
    "token_expiry",
}


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
        """Create OAuth2 configuration from a flat account dictionary.

        Looks for client_id/client_secret directly in the dict, falling
        back to environment variables.
        """
        client_id = data.get("client_id") or os.environ.get("GMAIL_CLIENT_ID")
        client_secret = data.get("client_secret") or os.environ.get(
            "GMAIL_CLIENT_SECRET"
        )
        refresh_token = data.get("refresh_token") or os.environ.get(
            "GMAIL_REFRESH_TOKEN"
        )

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
    idle_timeout: int = (
        300  # seconds: 0 = close after each call, -1 = never, >0 = timeout
    )
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
    def from_dict(
        cls, data: Dict[str, Any], defaults: Dict[str, Any] | None = None
    ) -> "ImapConfig":
        """Create configuration from a flat account dictionary.

        Args:
            data: Flat account dictionary (host, username, password or
                  client_id/client_secret/refresh_token, etc.)
            defaults: Global defaults for idle_timeout, verify_with_noop, etc.
        """
        defaults = defaults or {}

        # OAuth2 if client_id is present
        oauth2_config = OAuth2Config.from_dict(data)

        # Password can be specified in environment variable
        password = data.get("password") or os.environ.get("IMAP_PASSWORD")

        host = data.get("host", "")
        is_gmail = host.endswith("gmail.com") or host.endswith("googlemail.com")

        if is_gmail and not oauth2_config and not password:
            raise ValueError(
                "Gmail requires either an app-specific password or OAuth2 credentials"
            )
        elif not is_gmail and not password and not oauth2_config:
            raise ValueError(
                "IMAP password must be specified in config or IMAP_PASSWORD environment variable"
            )

        use_ssl = data.get("use_ssl", True)

        return cls(
            host=data["host"],
            port=data.get("port", 993 if use_ssl else 143),
            username=data["username"],
            password=password,
            oauth2=oauth2_config,
            use_ssl=use_ssl,
            idle_timeout=data.get("idle_timeout", defaults.get("idle_timeout", 300)),
            verify_with_noop=data.get(
                "verify_with_noop", defaults.get("verify_with_noop", True)
            ),
        )


@dataclass
class LocalCacheConfig:
    """Configuration for the optional local-cache search backend.

    The presence of this block plus a per-account ``maildir`` opts an
    account into local-cache search.  Currently only mu is supported.

    Attributes:
        indexer: Backend identifier; only ``"mu"`` is accepted in v1.
        max_staleness_seconds: Maximum age of the index before the
            backend declines and the call falls back to IMAP.  Default
            4000 (~67 minutes), comfortably above an hourly index cron.
        mu_index: Optional explicit muhome path (the value passed to
            ``mu --muhome=…``).  If unset, the backend discovers it
            from ``mu info store`` on first use.
    """

    indexer: str = "mu"
    max_staleness_seconds: int = 4000
    mu_index: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LocalCacheConfig":
        """Create local-cache configuration from a flat dictionary."""
        return cls(
            indexer=data.get("indexer", "mu"),
            max_staleness_seconds=int(data.get("max_staleness_seconds", 4000)),
            mu_index=data.get("mu_index"),
        )


@dataclass
class AccountConfig:
    """Configuration for a single email account."""

    imap: ImapConfig
    allowed_folders: Optional[List[str]] = None
    maildir: Optional[str] = None

    @classmethod
    def from_dict(
        cls, data: Dict[str, Any], defaults: Dict[str, Any] | None = None
    ) -> "AccountConfig":
        """Create account configuration from a flat dictionary.

        Args:
            data: Flat account dictionary (host, username, ..., allowed_folders,
                maildir).
            defaults: Global defaults inherited from top level.
        """
        return cls(
            imap=ImapConfig.from_dict(data, defaults),
            allowed_folders=data.get("allowed_folders"),
            maildir=data.get("maildir"),
        )


@dataclass
class MultiAccountConfig:
    """Multi-account server configuration."""

    accounts: Dict[str, AccountConfig]
    _default_account: Optional[str] = None
    local_cache: Optional[LocalCacheConfig] = None

    @property
    def default_account(self) -> str:
        """Explicit default_account, or first account."""
        if self._default_account and self._default_account in self.accounts:
            return self._default_account
        return next(iter(self.accounts))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MultiAccountConfig":
        """Create multi-account configuration from dictionary."""
        # Extract global defaults
        defaults = {
            k: data[k] for k in ("idle_timeout", "verify_with_noop") if k in data
        }

        accounts = {}
        for name, account_data in data.get("accounts", {}).items():
            accounts[name] = AccountConfig.from_dict(account_data, defaults)

        if not accounts:
            raise ValueError("No accounts defined in configuration")

        local_cache_data = data.get("local_cache")
        local_cache = (
            LocalCacheConfig.from_dict(local_cache_data) if local_cache_data else None
        )

        return cls(
            accounts=accounts,
            _default_account=data.get("default_account"),
            local_cache=local_cache,
        )


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
        Path("~/.config/mailroom/config.toml"),
    ]

    config_data: Dict[str, Any] = {}
    if config_path:
        try:
            with open(config_path, "rb") as f:
                config_data = tomllib.load(f)
            logger.info(f"Loaded configuration from {config_path}")
        except FileNotFoundError:
            logger.warning(f"Configuration file not found: {config_path}")
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Invalid TOML in {config_path}: {e}") from e
    else:
        for path in default_locations:
            expanded_path = path.expanduser()
            if expanded_path.exists():
                try:
                    with open(expanded_path, "rb") as f:
                        config_data = tomllib.load(f)
                except tomllib.TOMLDecodeError as e:
                    raise ValueError(f"Invalid TOML in {expanded_path}: {e}") from e
                logger.info(f"Loaded configuration from {expanded_path}")
                break

    if not config_data:
        logger.info("No configuration file found, using environment variables")
        if not os.environ.get("IMAP_HOST"):
            raise ValueError(
                "No configuration file found and IMAP_HOST environment variable not set"
            )

        config_data = {
            "accounts": {
                "default": {
                    "host": os.environ.get("IMAP_HOST"),
                    "port": int(os.environ.get("IMAP_PORT", "993")),
                    "username": os.environ.get("IMAP_USERNAME"),
                    "password": os.environ.get("IMAP_PASSWORD"),
                    "use_ssl": os.environ.get("IMAP_USE_SSL", "true").lower() == "true",
                    "idle_timeout": int(os.environ.get("IMAP_IDLE_TIMEOUT", "300")),
                    "verify_with_noop": os.environ.get(
                        "IMAP_VERIFY_WITH_NOOP", "true"
                    ).lower()
                    == "true",
                }
            }
        }

        allowed_folders_env = os.environ.get("IMAP_ALLOWED_FOLDERS")
        if allowed_folders_env:
            config_data["accounts"]["default"]["allowed_folders"] = (
                allowed_folders_env.split(",")
            )

    return config_data


def load_config(config_path: Optional[str] = None) -> MultiAccountConfig:
    """Load configuration from file or environment variables.

    Args:
        config_path: Path to configuration file

    Returns:
        Multi-account server configuration

    Raises:
        ValueError: If configuration is invalid
    """
    config_data = _load_config_data(config_path)

    try:
        return MultiAccountConfig.from_dict(config_data)
    except KeyError as e:
        raise ValueError(f"Missing required configuration: {e}")
