#!/usr/bin/env python3
"""
Gmail OAuth2 Token Refresher Utility

This script helps refresh OAuth2 tokens for Gmail integration testing by:
1. Reading credentials from config.toml
2. Opening a browser for authentication
3. Printing new tokens for the user to update manually

Usage:
    python refresh_oauth2_token.py [--config CONFIG_PATH]

Requirements:
    pip install google-auth google-auth-oauthlib
"""

import argparse
import json
import logging
import sys
import tomllib
import webbrowser
from typing import Dict

from google_auth_oauthlib.flow import InstalledAppFlow

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Gmail OAuth2 scopes
SCOPES = ["https://mail.google.com/"]


def load_config(config_path: str) -> Dict:
    """Load configuration from TOML file.

    Args:
        config_path: Path to the config file

    Returns:
        Dictionary with configuration
    """
    try:
        with open(config_path, "rb") as file:
            return tomllib.load(file)
    except Exception as e:
        logger.error(f"Failed to load config from {config_path}: {e}")
        sys.exit(1)


def extract_oauth2_credentials(config: Dict) -> Dict:
    """Extract OAuth2 credentials from configuration.

    Args:
        config: Configuration dictionary

    Returns:
        Dictionary with client_id and client_secret
    """
    imap_config = config.get("imap", {})
    oauth2_config = imap_config.get("oauth2", {})

    client_id = oauth2_config.get("client_id")
    client_secret = oauth2_config.get("client_secret")

    if not client_id or not client_secret:
        logger.error("OAuth2 credentials not found in config")
        sys.exit(1)

    return {"client_id": client_id, "client_secret": client_secret}


def refresh_token(credentials: Dict) -> Dict:
    """Refresh OAuth2 token using Google's OAuth2 flow.

    Args:
        credentials: Dictionary with client_id and client_secret

    Returns:
        Dictionary with new tokens
    """
    client_config = {
        "installed": {
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    try:
        flow = InstalledAppFlow.from_client_config(
            client_config, scopes=SCOPES, redirect_uri="http://localhost:8080"
        )

        flow.oauth2session.scope = SCOPES
        authorization_url, _ = flow.authorization_url(
            access_type="offline", prompt="consent"
        )

        logger.info("Opening browser for authentication...")
        webbrowser.open(authorization_url)

        flow.run_local_server(port=8080)

        creds = flow.credentials
        return {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
            "access_token": creds.token,
            "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
    except Exception as e:
        logger.error(f"Failed to refresh token: {e}")
        sys.exit(1)


def main():
    """Main function to refresh OAuth2 token."""
    parser = argparse.ArgumentParser(
        description="Refresh Gmail OAuth2 tokens for integration testing"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.toml",
        help="Path to config.toml file (default: config.toml)",
    )
    args = parser.parse_args()

    logger.info(f"Loading configuration from {args.config}")
    config = load_config(args.config)

    logger.info("Extracting OAuth2 credentials")
    credentials = extract_oauth2_credentials(config)

    logger.info("Starting OAuth2 authentication flow")
    new_tokens = refresh_token(credentials)

    logger.info("New OAuth2 tokens obtained:")
    logger.info(
        f"Refresh Token: {new_tokens['refresh_token'][:10]}...{new_tokens['refresh_token'][-10:]}"
    )

    print("\nUpdate your config.toml [imap.oauth2] section with:\n")
    print(f'refresh_token = "{new_tokens["refresh_token"]}"')
    print()
    print("Full token details:")
    print(json.dumps(new_tokens, indent=2))


if __name__ == "__main__":
    main()
