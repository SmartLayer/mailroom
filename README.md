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
- [Multi-block and send-as](#multi-block-and-send-as)
- [Local cache (mu)](#local-cache-mu)
- [Connection handling](#connection-handling)
- [Security](#security)
- [License](#license)

Mailroom connects to your existing mailbox on Gmail, Outlook, Fastmail, or any IMAP provider. It does not create new email addresses or route mail through a third-party service.

Commandline users, script authors, and AI assistants can search, read, download, reply, send, and organize email. Two interfaces serve different environments: a CLI that outputs JSON (for terminal-based agents, scripts, and automation) and an MCP server (for web-based AI chats and MCP clients). Both expose the same operations.

## What your AI can do with it

- Find a booking confirmation buried in your inbox
- Download the PDF attachment from an invoice
- Check all the links in a suspicious email
- Reply with attachments, in the right thread, saved to drafts or sent
- Compose and send a new message as the right alias
- Move, flag, or archive messages
- Search across all folders at once
- Handle a meeting invite (check availability, draft a response)

## Installation

See [INSTALLATION.md](docs/INSTALLATION.md) for Homebrew, Debian/Ubuntu (.deb), Fedora/RHEL (.rpm), and source installs.

## Configuration

Copy the sample and fill in your credentials:

```bash
cp examples/config.sample.toml ~/.config/mailroom/config.toml
```

A minimal config has three top-level named-entity tables: an `[imap.NAME]` mailbox, an `[smtp.NAME]` outgoing endpoint, and an `[identity.NAME]` describing one sendable address pointing at the IMAP block:

```toml
[smtp.gmail]
host = "smtp.gmail.com"
port = 587

[imap.personal]
host = "imap.gmail.com"
port = 993
username = "you@gmail.com"
password = "abcdefghijklmnop"
default_smtp = "gmail"

[identity.personal]
imap = "personal"
address = "you@gmail.com"
```

Gmail OAuth2 uses dedicated keys on the same `[imap.NAME]` block:

```toml
[imap.personal]
host = "imap.gmail.com"
port = 993
username = "you@gmail.com"
client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
refresh_token = "YOUR_REFRESH_TOKEN"
default_smtp = "gmail"
```

`[smtp.NAME]` blocks declare named SMTP endpoints. When the block omits credentials, mailroom inherits them from the `[imap.NAME]` block in scope at send time, the right shape for Gmail and Fastmail where IMAP and SMTP share one credential. When the block carries its own `username` and `password`, mailroom uses those, the right shape for AWS SES and similar smarthosts where one IAM SMTP user serves many From addresses.

Sending requires at least one `[identity.NAME]` block pointing at the `[imap.NAME]`. A block with no identities is read-only for sending; drafting and reading still work. This is a valid state, not an error. Declaring identities explicitly avoids the registration-handle hazard, where an IMAP login (e.g. a Gmail handle that is not an intended sender) could otherwise become a sendable identity by accident.

`mailroom config-check` validates the config (cross-references, identity addresses, send-route resolution) without performing any IMAP or SMTP traffic. The same warnings surface on `mailroom`, `mailroom --help`, `mailroom status`, and `mailroom list`.

Gmail OAuth2 setup requires a Google Cloud project with the Gmail API enabled. See [GMAIL_SETUP.md](docs/GMAIL_SETUP.md) for the full walkthrough.

## Quick test

With uv (any platform):

```bash
uvx mailroom search "subject:invoice"
```

No installation step; `uvx` runs it directly. To install permanently:

```bash
uv tool install mailroom
```

On Ubuntu 25.04 or later, the CLI dependencies are in the standard repositories. Install them, then run directly from a clone:

```bash
sudo apt-get install python3-typer python3-dotenv python3-imapclient python3-requests
python3 -m mailroom search "subject:invoice"
```

Mailroom looks for a config file at `~/.config/mailroom/config.toml`. Use `--config /path/to/config.toml` to point to a different location.

The MCP server (`mailroom mcp`) requires the `mcp` Python package, which is not in apt. Use `uv` or `pip` for that. Many users prefer the CLI to MCP because the latter loads 80+ tools into every conversation; the CLI is the lighter footprint.

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
mailroom export -f INBOX -u 4523 -o /tmp/email.eml --raw

# Extract all links from several emails
mailroom links -f INBOX -u 4523 -u 4524 -u 4525

# Reply, saved to drafts by default; --send transmits via SMTP
mailroom reply -f INBOX -u 4523 -b "Thanks, confirmed."
mailroom reply -f INBOX -u 4523 -b "Invoice attached." --attach /tmp/invoice.pdf
mailroom reply -f INBOX -u 4523 -b "Thanks, confirmed." --send

# Compose a new message
mailroom compose --to alice@example.com --subject "Meeting" -b "See attached." --send

# Send a draft
mailroom send-draft -f Drafts -u 4530

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

Because every command returns JSON and uses non-zero exit codes on failure, Mailroom works as a building block in pipelines and cron jobs.

```bash
# Forward all emails from a sender to another folder
mailroom search "from:sender@example.com" --folder INBOX \
  | jq -r '.[].uid' \
  | xargs -I{} mailroom move -f INBOX -u {} -t Forwarded

# Daily digest: save today's unread subjects to a file
mailroom search "is:unread" --folder INBOX \
  | jq -r '.[].subject' > ~/daily-digest.txt

# Auto-acknowledge incoming invoices
mailroom search "is:unread subject:invoice" --folder INBOX \
  | jq -r '.[].uid' \
  | xargs -I{} mailroom reply -f INBOX -u {} -b "Received, processing." --send
```

AI agents with skill/hook systems call Mailroom the same way: define a skill that runs a shell command and parses the JSON output.

## Multi-block and send-as

A single config holds multiple `[imap.*]` blocks, `[smtp.*]` endpoints, and `[identity.*]` blocks. Each identity declares which `[imap.NAME]` block it routes through:

```toml
default_imap = "personal"

[smtp.gmail]
host = "smtp.gmail.com"
port = 587

[smtp.ses-syd]
host = "email-smtp.ap-southeast-2.amazonaws.com"
port = 587
username = "AKIA..."
password = "BPa+..."

[imap.personal]
host = "imap.gmail.com"
username = "you@gmail.com"
password = "personal-app-password"
default_smtp = "gmail"

[identity.personal]
imap = "personal"
address = "you@gmail.com"

[imap.work]
host = "outlook.office365.com"
username = "you@company.com"
password = "work-app-password"

[identity.work]
imap = "work"
address = "you@company.com"
```

Select an `[imap.NAME]` block with `-i`:

```bash
mailroom -i work search "is:unread"
```

A single `[imap.NAME]` block can have several identities, useful when one Gmail mailbox handles personal mail and an organisational alias routed through SES:

```toml
[imap.director]
host = "imap.gmail.com"
username = "alias-host@gmail.com"
password = "gmail-app-password"
default_smtp = "gmail"

[identity.director]
imap = "director"
address = "director@example.org"
name = "Director Name"
smtp = "ses-syd"
sent_folder = "[Gmail]/Sent Mail"

[identity.director-alias]
imap = "director"
address = "alias-host@gmail.com"
```

Pick the From identity at send time with `--from`:

```bash
mailroom -i director compose --to client@example.com --from director@example.org -b "..." --send
```

`mailroom reply` defaults to the identity that received the parent email, so a reply to alias X is sent as X. `mailroom send-draft` reads the From header on the draft and refuses to send if it does not match a configured identity, which prevents a script or AI from accidentally sending through the wrong route.

## Local cache (mu)

If you already have your maildir indexed by [mu](https://www.djcbsoftware.nl/code/mu/), `search` can be served from the local Xapian index instead of IMAP, orders of magnitude faster. Opt in by adding a `[local_cache]` block plus a per-block `maildir`:

```toml
[local_cache]
indexer = "mu"
max_staleness_seconds = 4000

[imap.gmail]
host = "imap.gmail.com"
username = "you@gmail.com"
password = "..."
maildir = "/var/local/mail/you-gmail-com"

[identity.gmail]
imap = "gmail"
address = "you@gmail.com"
```

The contract is "a maildir exists and mu indexes it"; mailroom does not run `mbsync`, `offlineimap`, or `mu index`. When the index is stale, the query is untranslatable, the call is folder-scoped, mu is missing, or any error occurs, the search falls back to IMAP transparently. Every `search` response carries a `provenance` field reporting `source` (`"local"` or `"remote"`), the index `indexed_at` timestamp, and a `fell_back_reason` tag when applicable.

## Connection handling

IMAP servers drop idle connections after 10-30 minutes. AI assistants work in bursts: a flurry of operations, then thinking time. Mailroom tracks connection age and reconnects transparently before operations fail. The default idle timeout is 300 seconds; set `idle_timeout` in the config to adjust.

## Security

Mailroom accesses your email account. Store credentials outside your repository (environment variables, a secrets manager, or a config file in `.gitignore`). Use app-specific passwords or OAuth2 rather than your main account password. Restrict `allowed_folders` in the config to limit what the tool can see.

## License

MIT. See [LICENSE](LICENSE).
