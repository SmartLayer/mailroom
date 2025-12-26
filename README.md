# IMAP MCP Server

A Model Context Protocol (MCP) server that enables AI assistants to check email, process messages, and learn user preferences through interaction.

## Overview

This project implements an MCP server that interfaces with IMAP email servers to provide the following capabilities:

- Email browsing and searching
- Email organization (moving, tagging, marking)
- Email composition and replies
- Interactive email processing and learning user preferences
- Automated email summarization and categorization
- Support for multiple IMAP providers

The IMAP MCP server is designed to work with Claude or any other MCP-compatible assistant, allowing them to act as intelligent email assistants that learn your preferences over time.

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
├── imap_mcp/              # Source code
│   ├── __init__.py
│   ├── config.py          # Configuration handling
│   ├── imap_client.py     # IMAP client implementation
│   ├── models.py          # Data models
│   ├── resources.py       # MCP resources implementation
│   ├── server.py          # Main server implementation
│   └── tools.py           # MCP tools implementation
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

#### Checking Email

To list emails in your inbox:
```bash
uv run list_inbox.py --config config.yaml --folder INBOX --limit 10
```

Available options:
- `--folder`: Specify which folder to check (default: INBOX)
- `--limit`: Maximum number of emails to display (default: 10)
- `--verbose`: Enable detailed logging output

#### Starting the MCP Server

To start the IMAP MCP server:
```bash
uv run imap-mcp --config config.yaml
```

For development mode with debugging:
```bash
uv run imap-mcp --dev
```

#### Managing OAuth2 Tokens

To refresh your OAuth2 token:
```bash
uv run imap_mcp.auth_setup refresh-token --config config.yaml
```

To generate a new OAuth2 token:
```bash
uv run imap_mcp.auth_setup generate-token --config config.yaml
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

This MCP server requires access to your email account, which contains sensitive personal information. Please be aware of the following security considerations:

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
