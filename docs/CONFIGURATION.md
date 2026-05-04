# Multi-Account and Send-As Routing

This document covers the parts of the mailroom config that deal with more than one mailbox or more than one From address: multiple `[imap.*]` blocks, multiple `[smtp.*]` endpoints, and how identities select between them on send.

For the small single-account config, see the [Configuration section in the README](../README.md#configuration).

## Multiple `[imap.*]`, `[smtp.*]`, `[identity.*]` blocks

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

## Selecting an `[imap.*]` block with `-i`

```bash
mailroom -i work search "is:unread"
```

## One mailbox, several identities

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

## Picking a send identity (`--send` mode)

`compose --send`, `reply --send`, and `send-draft` require the route to be named explicitly. There are two forms.

**Mode A: `--identity NAME`.** Names a configured `[identity.NAME]` block; resolves From, display name, the `[imap.*]` block, the SMTP route, and the Sent folder.

```bash
mailroom compose --send --identity director \
  --to client@example.com -b "..."
```

**Mode B: `--smtp NAME --from EMAIL [--name N] [--fcc IMAP:FOLDER]`.** Sends a free-form `--from` through a named SMTP block, without consulting any `[identity.*]`. The SMTP block must carry its own username and password (no inheritance from an `[imap.*]` block, since none is in scope). Useful for relays like SES that are authorised to carry many addresses. With no `--fcc`, no copy is saved; with `--fcc work:Sent`, mailroom appends the message to the named folder on `[imap.work]` after a successful send.

```bash
mailroom compose --send --smtp ses-syd \
  --from "noreply@example.org" --name "Example Org" \
  --fcc director:Sent \
  --to client@example.com -b "..."
```

**Reply** has one extra path: when neither flag is given, mailroom matches the parent's recipients against identities on the selected `[imap.*]` block and uses the match. If no recipient matches, `reply --send` errors rather than silently picking an arbitrary identity. The drafting path (no `--send`) keeps the older fallback behaviour.

**`send-draft`** by default uses the draft's own From header and refuses to send if it does not match a configured identity. `--identity` or `--smtp/--from` override the draft's From for that send.

Drafting (no `--send`) keeps the previous convenience defaults: the first identity on the selected `[imap.*]` block is the From, and `--from EMAIL` selects a different identity by address.
