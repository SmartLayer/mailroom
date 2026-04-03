# Mailroom

Give your script or AI assistant access to your email.

Mailroom lets AI assistants search, read, download, reply to, and organize email. It works with any IMAP provider (Gmail, Outlook, Fastmail, self-hosted). Two interfaces serve different environments: a CLI that outputs JSON (for terminal-based agents, scripts, and automation) and an MCP server (for web-based AI chats and MCP clients). Both expose the same operations.

## What your AI can do with it

- Find a booking confirmation buried in your inbox
- Download the PDF attachment from an invoice
- Check all the links in a suspicious email
- Draft a reply that lands in the right thread
- Move, flag, or archive messages
- Search across all folders at once
- Handle a meeting invite — check availability, draft a response

## Configuration

Copy the sample and fill in your credentials:

```bash
cp config.sample.yaml config.yaml
```

For Gmail with OAuth2:

```yaml
imap:
  host: imap.gmail.com
  port: 993
  username: your-email@gmail.com
  use_ssl: true
  oauth2:
    client_id: YOUR_CLIENT_ID
    client_secret: YOUR_CLIENT_SECRET
    refresh_token: YOUR_REFRESH_TOKEN
```

For other providers, use a password or app-specific password:

```yaml
imap:
  host: imap.your-provider.com
  port: 993
  username: your-email@provider.com
  use_ssl: true
  password: YOUR_APP_PASSWORD
```

Gmail OAuth2 setup requires a Google Cloud project with the Gmail API enabled. See [GMAIL_SETUP.md](GMAIL_SETUP.md) for the full walkthrough.

## Quick test

With uv (any platform):

```bash
uvx mailroom --config config.yaml search-emails "invoice" --criteria subject
```

No installation step — `uvx` runs it directly. To install permanently:

```bash
uv tool install mailroom
```

On Ubuntu 25.10 or later, the CLI dependencies are in the standard repositories. Install them, then run directly from a clone:

```bash
sudo apt-get install python3-typer python3-yaml python3-dotenv python3-imapclient python3-requests
```
Then you can run it directly without uv

```bash
python3 -m mailroom --config config.yaml search-emails "invoice" --criteria subject
```

The MCP server (`mailroom mcp`) requires the `mcp` Python package, which is not in apt. Use `uv` or `pip` for that. Manuy people prefer to use cli instead of mcp as the latter loads 80+ tools into every conversation, in that case no need to install mcp package.

## CLI usage

Every command outputs JSON to stdout. Errors go to stderr. This makes Mailroom composable with `jq`, shell scripts, and AI agent skill definitions.

```bash
# What's unread?
mailroom -c config.yaml search-emails "" --criteria unseen --folder INBOX --limit 10

# Search by subject across all folders
mailroom -c config.yaml search-emails "hotel booking" --criteria subject

# Read an email's attachments, then download one
mailroom -c config.yaml list-attachments INBOX 4523
mailroom -c config.yaml download-attachment INBOX 4523 itinerary.pdf /tmp/itinerary.pdf

# Export an HTML email as a standalone file (images embedded)
mailroom -c config.yaml export-email-html INBOX 4523 /tmp/email.html

# Extract all links from several emails (useful for phishing checks)
mailroom -c config.yaml extract-email-links INBOX 4523 4524 4525

# Draft a threaded reply
mailroom -c config.yaml draft-reply INBOX 4523 "Thanks, confirmed."

# Organize
mailroom -c config.yaml move-email INBOX 4523 Archive
mailroom -c config.yaml mark-as-read INBOX 4524
mailroom -c config.yaml flag-email INBOX 4525
```

Run `mailroom --help` for the full command list.

## MCP server

For AI environments that cannot run shell commands (Claude web, Cursor, or any MCP client):

```bash
mailroom mcp --config config.yaml
```

This starts an MCP server exposing the same operations as tools. The MCP package is only imported when this subcommand runs, so the CLI stays lightweight.

## Scripting and automation

Because every command returns JSON and uses non-zero exit codes on failure, Mailroom works as a building block in pipelines and cron jobs. A few patterns:

```bash
# Forward all unread emails from a sender to another address
mailroom -c config.yaml search-emails "sender@example.com" --criteria from --folder INBOX \
  | jq -r '.[].uid' \
  | xargs -I{} mailroom -c config.yaml move-email INBOX {} Forwarded

# Daily digest: save today's unread subjects to a file
mailroom -c config.yaml search-emails "" --criteria unseen --folder INBOX \
  | jq -r '.[].subject' > ~/daily-digest.txt
```

AI agents with skill/hook systems can call Mailroom the same way — define a skill that runs a shell command and parses the JSON output.

## Multi-account

A single config file can hold multiple accounts:

```yaml
default_account: personal
accounts:
  personal:
    imap:
      host: imap.gmail.com
      # ...
  work:
    imap:
      host: outlook.office365.com
      # ...
```

Select an account with `-a`:

```bash
mailroom -c config.yaml -a work search-emails "" --criteria unseen
```

## Connection handling

IMAP servers drop idle connections after 10-30 minutes. AI assistants work in bursts — a flurry of operations, then thinking time. Mailroom tracks connection age and reconnects transparently before operations fail. The default idle timeout is 300 seconds; set `idle_timeout` in the config to adjust.

## Security

Mailroom accesses your email account. Store credentials outside your repository (environment variables, a secrets manager, or a config file in `.gitignore`). Use app-specific passwords or OAuth2 rather than your main account password. Restrict `allowed_folders` in the config to limit what the tool can see.

## License

MIT. See [LICENSE](LICENSE).
