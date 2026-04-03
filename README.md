# Mailroom

Scriptable email access for humans and AI assistants.

## Overview

Mailroom is a command-line tool and Python library for working with IMAP email. It handles the things that raw IMAP makes painful: searching across folders, decoding MIME and base64, downloading attachments, exporting HTML emails with embedded images, extracting links, processing meeting invites, and drafting replies with proper threading headers. All commands output JSON, making them easy to pipe into `jq`, wrap in scripts, or call from an AI agent.

For environments where the assistant cannot execute local commands (e.g. Claude web interface), Mailroom also runs as an MCP server via `mailroom mcp`. The MCP mode exposes the same operations as MCP tools. The `mcp` package is not imported unless this subcommand is used.

### Capabilities

- Email browsing and searching
- Email organization (moving, tagging, marking)
- Email composition and replies
- Interactive email processing and learning user preferences
- Automated email summarization and categorization
- Support for multiple IMAP providers

## Features

- **Email Authentication**: Secure access to IMAP servers with various authentication methods
- **Email Browsing**: List folders and messages with filtering options 
- **Email Content**: Read message contents including text, HTML, and attachments with download capability 
- **Email Actions**: Move, delete, mark as read/unread, flag messages 
- **Email Composition**: Draft and save replies to messages with proper formatting
  - Support for plain text and HTML replies
  - Reply-all functionality with CC support
  - Proper threading with In-Reply-To and References headers
  - Save drafts to appropriate folders
- **Search**: Advanced search capabilities with support for complex IMAP expressions
  - Simple criteria: text, from, to, subject, all, unseen, seen
  - Date-based searches: today, week, month
  - Raw IMAP expressions: Full support for OR/AND/NOT operators in Polish notation
  - Complex queries for finding specific email patterns (e.g., travel bookings, receipts) 
- **Connection Management**: Intelligent connection lifecycle management with configurable idle timeout and automatic reconnection
- **Interaction Patterns**: Structured patterns for processing emails and learning preferences (planned)
- **Learning Layer**: Record and analyze user decisions to predict future actions (planned)

## Usage

```bash
# Using the console_script entry point (requires pip/uv install):
mailroom --config path/to/config.yaml COMMAND [ARGS]

# Using python3 -m (works without installing the entry point):
python3 -m mailroom --config path/to/config.yaml COMMAND [ARGS]
```

The `--config` option (or the `MAILROOM_CONFIG` environment variable) selects the YAML configuration file. Add `--verbose` / `-v` for debug logging.

### Commands

| Command | Description |
|---------|-------------|
| `server-status` | Show IMAP server configuration (no connection needed) |
| `search-emails QUERY` | Search emails; `--criteria` selects field (subject, from, to, text, all, unseen, seen, raw), `--folder` limits to one folder, `--limit` caps results |
| `move-email FOLDER UID TARGET` | Move an email to another folder |
| `mark-as-read FOLDER UID` | Mark an email as read |
| `mark-as-unread FOLDER UID` | Mark an email as unread |
| `flag-email FOLDER UID` | Flag (star) an email; `--unflag` removes the flag |
| `delete-email FOLDER UID` | Delete an email |
| `process-email FOLDER UID ACTION` | Higher-level action dispatch: move, read, unread, flag, unflag, delete |
| `list-attachments FOLDER UID` | List attachment metadata for an email |
| `download-attachment FOLDER UID IDENTIFIER SAVE_PATH` | Download attachment by filename or index |
| `export-email-html FOLDER UID SAVE_PATH` | Export HTML email to a standalone file with embedded images |
| `extract-email-links FOLDER UID [UID...]` | Extract all hyperlinks from one or more emails |
| `process-meeting-invite FOLDER UID` | Identify a meeting invite, check availability, and save a draft reply |
| `mcp` | Start the MCP server (for environments that cannot run CLI commands) |

### Examples

```bash
# Show what account is configured
mailroom --config config.yaml server-status

# Find recent unread mail in the inbox
mailroom --config config.yaml search-emails "" --criteria unseen --folder INBOX --limit 10

# Search by subject across all folders
mailroom --config config.yaml search-emails "invoice" --criteria subject

# List attachments on a specific message
mailroom --config config.yaml list-attachments INBOX 12345

# Save an attachment to disk
mailroom --config config.yaml download-attachment INBOX 12345 report.pdf /tmp/report.pdf

# Export a HTML email for browser viewing
mailroom --config config.yaml export-email-html INBOX 12345 /tmp/email.html

# Extract all links from several messages
mailroom --config config.yaml extract-email-links INBOX 12345 12346 12347

# Move a message to a folder
mailroom --config config.yaml move-email INBOX 12345 Archive
```

All commands output JSON to stdout.

## Connection Management

IMAP servers typically drop idle connections after 10-30 minutes. Since AI assistants have bursty usage patterns (quick operations followed by thinking time and user interaction), the server includes intelligent connection lifecycle management to handle this gracefully.

### Configuration

Add connection settings to the `imap` section of the config file:

```yaml
imap:
  host: imap.gmail.com
  port: 993
  username: your-email@gmail.com
  use_ssl: true
  idle_timeout: 300      # seconds before reconnecting (default: 300)
  verify_with_noop: true # verify connection health with NOOP command
```

### idle_timeout Options

| Value | Behaviour | Use Case |
|-------|-----------|----------|
| `0` | Close connection after each operation | Testing, debugging, strict resource control |
| `300` (default) | Reconnect if idle > 5 minutes | Normal AI assistant usage |
| `600` | Reconnect if idle > 10 minutes | Low-latency environments |
| `-1` | Never proactively reconnect | Legacy behaviour |

### How It Works

1. **Activity Tracking**: Each successful IMAP operation updates an internal timestamp
2. **Staleness Check**: Before operations, the client checks if idle time exceeds `idle_timeout`
3. **NOOP Verification**: If enabled, sends a NOOP command to verify the connection is still alive
4. **Automatic Reconnection**: Stale or dead connections are transparently reconnected

This ensures reliable operation even when there are long gaps between MCP tool calls.

## Current Project Structure

The project is currently organized as follows:

```
.
├── examples/              # Example configurations
│   └── config.yaml.example
├── mailroom/              # Source code
│   ├── __main__.py        # CLI entry point and all commands
│   ├── __init__.py
│   ├── config.py          # Configuration handling
│   ├── imap_client.py     # IMAP client implementation
│   ├── models.py          # Data models
│   ├── mcp_server.py      # MCP server (only loaded by `mailroom mcp`)
│   ├── resources.py       # MCP resources
│   ├── tools.py           # MCP tool wrappers
├── tests/                 # Test suite
│   ├── __init__.py
│   └── test_models.py
├── INSTALLATION.md        # Detailed installation guide
├── pyproject.toml         # Project configuration
└── README.md              # This file
```

## Getting Started

### Prerequisites

- Python 3.8 or higher
- An IMAP-enabled email account (Gmail recommended)
- [uv](https://docs.astral.sh/uv/) for package management and running Python scripts

### Installation

1. Install uv if you haven't already:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Clone and install the package:
   ```bash
   git clone https://github.com/non-dirty/imap-mcp.git
   cd imap-mcp
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   uv pip install -e ".[dev]"
   ```

### Gmail Configuration

1. Create a config file:
   ```bash
   cp config.sample.yaml config.yaml
   ```

2. Set up Gmail OAuth2 credentials:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing one
   - Enable the Gmail API
   - Create OAuth2 credentials (Desktop Application type)
   - Download the client configuration

3. Update `config.yaml` with your Gmail settings:
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

### Usage

#### Starting the MCP Server

To start the MCP server (for use with Claude Desktop or other MCP clients):
```bash
mailroom mcp --config config.yaml
```

For development mode with debugging:
```bash
mailroom mcp --config config.yaml --dev --debug
```

#### Managing OAuth2 Tokens

To refresh your OAuth2 token:
```bash
python3 -m mailroom.auth_setup refresh-token --config config.yaml
```

To generate a new OAuth2 token:
```bash
python3 -m mailroom.auth_setup generate-token --config config.yaml
```

#### Advanced Email Search

The server supports complex IMAP search expressions for powerful email filtering:

**Simple searches:**
```python
# Using the search_emails tool
search_emails(query="important", criteria="text")      # Search for text
search_emails(query="john@example.com", criteria="from")  # From specific sender
search_emails(query="", criteria="unseen")              # Unread emails
search_emails(query="", criteria="today")               # Today's emails
```

**Complex searches with raw IMAP expressions:**
```python
# Simple OR: Find emails from Edinburgh or Berlin
search_emails(
    query='OR TEXT "Edinburgh" TEXT "Berlin"',
    criteria="raw"
)

# Complex nested OR: Find travel-related emails
search_emails(
    query='OR TEXT "Edinburgh" OR TEXT "Berlin" OR TEXT "Munich" OR TEXT "itinerary" OR TEXT "booking confirmation" TEXT "e-ticket"',
    criteria="raw"
)

# Combined criteria: Find unread emails from specific sender
search_emails(
    query='UNSEEN FROM "john@example.com"',
    criteria="raw"
)
```

**Note:** Raw IMAP expressions use Polish (prefix) notation where operators come before their operands. Each `OR` operator combines exactly two search criteria. For multiple OR operations, nest them properly as shown above.

#### Using MCP Resources

The server exposes email data through MCP resources that can be accessed via `fetch_mcp_resource`:

- `email://folders` - List all email folders
- `email://{folder}/list` - List emails in a folder (max 50 recent)
- `email://search/{query}` - Search emails across folders
- `email://{folder}/{uid}` - Get specific email content (returns HTML when available)

These resources are designed to work with MCP-compatible clients like Claude Desktop or other AI assistants that support the Model Context Protocol.

## Development

### Setting Up Development Environment

```bash
# Set up virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

## Security Considerations

Mailroom requires access to your email account, which contains sensitive personal information. Please be aware of the following security considerations:

- Store email credentials securely using environment variables or secure credential storage
- Consider using app-specific passwords instead of your main account password
- Limit folder access to only what's necessary for your use case
- Review the permissions granted to the server in your email provider's settings

## Project Roadmap

- [x] Project initialization and repository setup
- [x] Basic IMAP integration
- [x] Email resource implementation
- [x] Email tool implementation
- [x] Email reply and draft functionality
- [x] Connection lifecycle management
- [x] Advanced search capabilities with complex IMAP expressions
- [ ] User preference learning implementation
- [ ] Multi-account support
- [ ] Integration with major email providers

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Model Context Protocol](https://modelcontextprotocol.io/) for providing the framework
- [Anthropic](https://www.anthropic.com/) for developing Claude
- Various Python IMAP libraries that make this project possible
