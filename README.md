# ![Mailroom](docs/logo.png)

[![CI](https://github.com/SmartLayer/mailroom/actions/workflows/code_checks.yml/badge.svg)](https://github.com/SmartLayer/mailroom/actions/workflows/code_checks.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Give your script or AI assistant access to your email.

## Contents

- [What your AI can do with it](#what-your-ai-can-do-with-it)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick test](#quick-test)
- [CLI usage](#cli-usage)
- [MCP server](#mcp-server)
- [Scripting and automation](#scripting-and-automation)
- [Multi-account](#multi-account)
- [Connection handling](#connection-handling)
- [Security](#security)
- [License](#license)

Mailroom lets commandline users, script authors and AI assistants search, read, download, reply to, and organize email. It works with any IMAP provider (Gmail, Outlook, Fastmail, self-hosted). Two interfaces serve different environments: a CLI that outputs JSON (for terminal-based agents, scripts, and automation) and an MCP server (for web-based AI chats and MCP clients). Both expose the same operations.

## What your AI can do with it

- Find a booking confirmation buried in your inbox
- Download the PDF attachment from an invoice
- Check all the links in a suspicious email
- Draft a reply that lands in the right thread
- Move, flag, or archive messages
- Search across all folders at once
- Handle a meeting invite — check availability, draft a response

## Installation

**uv** (any platform, no install step):

```bash
uvx mailroom search "subject:invoice"
```

To install permanently: `uv tool install mailroom`

**Homebrew** (macOS / Linux):

```bash
brew install --formula https://raw.githubusercontent.com/SmartLayer/mailroom/main/formula/mailroom.rb
```

**Debian / Ubuntu** (.deb):

Download `mailroom_<version>_all.deb` from the [Releases](https://github.com/SmartLayer/mailroom/releases) page, then:

```bash
sudo dpkg -i mailroom_*_all.deb
sudo apt-get install -f
```

**Fedora / RHEL** (.rpm):

Download `mailroom-<version>.noarch.rpm` from the [Releases](https://github.com/SmartLayer/mailroom/releases) page, then:

```bash
sudo rpm -i mailroom-*.noarch.rpm
```

**From source**:

```bash
git clone https://github.com/SmartLayer/mailroom.git
cd mailroom
uv pip install -e .
```

## Configuration

Copy the sample and fill in your credentials:

```bash
cp examples/config.sample.toml ~/.config/mailroom/config.toml
```

For Gmail with OAuth2:

```toml
[imap]
host = "imap.gmail.com"
port = 993
username = "your-email@gmail.com"
use_ssl = true

[imap.oauth2]
client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
refresh_token = "YOUR_REFRESH_TOKEN"
```

For other providers, use a password or app-specific password:

```toml
[imap]
host = "imap.your-provider.com"
port = 993
username = "your-email@provider.com"
use_ssl = true
password = "YOUR_APP_PASSWORD"
```

Gmail OAuth2 setup requires a Google Cloud project with the Gmail API enabled. See [GMAIL_SETUP.md](docs/GMAIL_SETUP.md) for the full walkthrough.

## Quick test

With uv (any platform):

```bash
uvx mailroom search "subject:invoice"
```

No installation step — `uvx` runs it directly. To install permanently:

```bash
uv tool install mailroom
```

On Ubuntu 25.04 or later, the CLI dependencies are in the standard repositories. Install them, then run directly from a clone:

```bash
sudo apt-get install python3-typer python3-dotenv python3-imapclient python3-requests
```
Then you can run it directly without uv

```bash
python3 -m mailroom search "subject:invoice"
```

Mailroom looks for a config file at `~/.config/mailroom/config.toml`. Use `--config /path/to/config.toml` to point to a different location.

The MCP server (`mailroom mcp`) requires the `mcp` Python package, which is not in apt. Use `uv` or `pip` for that. Manuy people prefer to use cli instead of mcp as the latter loads 80+ tools into every conversation, in that case no need to install mcp package.

## CLI usage

Every command outputs JSON to stdout. Errors go to stderr. This makes Mailroom composable with `jq`, shell scripts, and AI agent skill definitions.

```bash
# What's unread?
mailroom search "is:unread" --folder INBOX --limit 10

# Search by subject across all folders
mailroom search 'subject:"hotel booking"'

# Read an email
mailroom read -f INBOX -u 4523

# List and download attachments
mailroom attachments -f INBOX -u 4523
mailroom save -f INBOX -u 4523 -i itinerary.pdf -o /tmp/itinerary.pdf

# Export an HTML email as a standalone file (images embedded)
mailroom export -f INBOX -u 4523 -o /tmp/email.html

# Extract all links from several emails (useful for phishing checks)
mailroom links -f INBOX -u 4523 -u 4524 -u 4525

# Draft a threaded reply
mailroom reply -f INBOX -u 4523 -b "Thanks, confirmed."

# Organize
mailroom move -f INBOX -u 4523 -t Archive
mailroom mark-read -f INBOX -u 4524
mailroom flag -f INBOX -u 4525
```

Run `mailroom --help` for the full command list.

## MCP server

For AI environments that cannot run shell commands (Claude web, Cursor, or any MCP client):

```bash
mailroom mcp
```

This starts an MCP server exposing the same operations as tools. The MCP package is only imported when this subcommand runs, so the CLI stays lightweight.

## Scripting and automation

Because every command returns JSON and uses non-zero exit codes on failure, Mailroom works as a building block in pipelines and cron jobs. A few patterns:

```bash
# Forward all emails from a sender to another folder
mailroom search "from:sender@example.com" --folder INBOX \
  | jq -r '.[].uid' \
  | xargs -I{} mailroom move -f INBOX -u {} -t Forwarded

# Daily digest: save today's unread subjects to a file
mailroom search "is:unread" --folder INBOX \
  | jq -r '.[].subject' > ~/daily-digest.txt
```

AI agents with skill/hook systems can call Mailroom the same way — define a skill that runs a shell command and parses the JSON output.

## Multi-account

A single config file can hold multiple accounts:

```toml
default_account = "personal"

[accounts.personal]
host = "imap.gmail.com"
username = "you@gmail.com"
client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
refresh_token = "YOUR_REFRESH_TOKEN"

[accounts.work]
host = "outlook.office365.com"
username = "you@company.com"
password = "YOUR_APP_PASSWORD"
```

Select an account with `-a`:

```bash
mailroom -a work search "is:unread"
```

## Connection handling

IMAP servers drop idle connections after 10-30 minutes. AI assistants work in bursts — a flurry of operations, then thinking time. Mailroom tracks connection age and reconnects transparently before operations fail. The default idle timeout is 300 seconds; set `idle_timeout` in the config to adjust.

## Security

Mailroom accesses your email account. Store credentials outside your repository (environment variables, a secrets manager, or a config file in `.gitignore`). Use app-specific passwords or OAuth2 rather than your main account password. Restrict `allowed_folders` in the config to limit what the tool can see.

## License

MIT. See [LICENSE](LICENSE).
