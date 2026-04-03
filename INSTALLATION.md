# Mailroom Server Installation Guide

This document provides detailed instructions for installing and setting up the Mailroom Server.

## Prerequisites

- Python 3.11 or higher
- An IMAP-enabled email account
- [uv](https://docs.astral.sh/uv/) - Python package installer (required for installation)
- Claude Desktop or another MCP-compatible client

## Installation Steps

### 1. Install the uv tool

The MCP server installation requires the uv tool from Astral. Install it according to the official documentation:
https://docs.astral.sh/uv/

### 2. Clone the repository

```bash
git clone https://github.com/SmartLayer/mailroom.git
cd mailroom
```

### 3. Install the package and dependencies

```bash
pip install -e .
```

For development, install with additional development dependencies:

```bash
pip install -e ".[dev]"
```

### 4. Create a configuration file

```bash
cp config.sample.toml config.toml
```

Edit the `config.toml` file with your email settings:

```toml
[imap]
host = "imap.example.com"
port = 993
username = "your.email@example.com"
# password = "your_password"  # or set IMAP_PASSWORD environment variable
use_ssl = true

# Optional: restrict access to specific folders
# allowed_folders = ["INBOX", "Sent", "Archive", "Important"]
```

For security, it's recommended to use environment variables for sensitive information:

```bash
export IMAP_PASSWORD="your_secure_password"
```

### 5. Running the server

#### Basic usage:

```bash
mailroom mcp
```

#### With specific config file:

```bash
mailroom mcp --config /path/to/config.toml
```

#### For development mode (with inspector):

```bash
mailroom mcp --config /path/to/config.toml --dev
```

#### For debugging:

```bash
mailroom mcp --config /path/to/config.toml --debug
```

## Integrating with Claude Desktop

Add the following to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "mailroom": {
      "command": "mailroom",
      "args": ["mcp", "--config", "/path/to/config.toml"],
      "env": {
        "IMAP_PASSWORD": "your_secure_password"
      }
    }
  }
}
```

## Troubleshooting

If you encounter issues with the installation or running the server:

1. Ensure all prerequisites are installed correctly
2. Verify your IMAP server settings are correct
3. Check that your email provider allows IMAP access
4. For authentication issues, try using an app-specific password if available
5. Enable debug mode (`--debug`) for more detailed logs