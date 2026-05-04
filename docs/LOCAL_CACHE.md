# Local Cache: offlineimap + mu

For AI agents searching across years of mail, IMAP is slow: every query round-trips to the server. Mailroom can answer `search` from a local Xapian index instead, orders of magnitude faster, and falls back to IMAP transparently when the index can't serve the query.

This is opt-in. Without a `[local_cache]` block in the config, every `search` goes to IMAP exactly as before.

## How the pieces fit

Three components, each owned by a separate project:

- [offlineimap](https://github.com/OfflineIMAP/offlineimap) (or [mbsync](https://isync.sourceforge.io/)) keeps a maildir on disk in sync with your IMAP server.
- [mu](https://www.djcbsoftware.nl/code/mu/) indexes the maildir into a Xapian database and answers queries.
- mailroom reads `mu`'s index for `search` when the index is fresh and the query is translatable, otherwise it falls back to IMAP.

Mailroom does not run `offlineimap`, `mbsync`, or `mu index`. The contract is "a maildir exists and `mu` indexes it"; how the maildir gets populated and how often `mu` re-indexes is your decision and runs outside mailroom.

## Prerequisites

Install offlineimap (or mbsync) and mu through your package manager, and set up syncing and indexing per their upstream documentation:

- offlineimap: https://github.com/OfflineIMAP/offlineimap (configuration in `~/.offlineimaprc`)
- mu: https://www.djcbsoftware.nl/code/mu/ (`mu init --maildir=/path/to/maildir`, then `mu index`)

A working setup ends with a maildir on disk and `mu find subject:hello` returning hits. Once that holds, mailroom can use it.

## Wiring mailroom

Add a `[local_cache]` block and a `maildir` field on the `[imap.*]` block whose mail you indexed:

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

`max_staleness_seconds` is the threshold past which mailroom considers the index stale and falls back to IMAP for that query. Pick a value matching how often your sync job runs (e.g. if you run offlineimap every hour, `max_staleness_seconds = 4000` lets a slightly delayed sync still serve the query).

## Contract and fallback

When the index is stale, the query is untranslatable, the call is folder-scoped, `mu` is missing, or any error occurs, the search falls back to IMAP transparently. Every `search` response carries a `provenance` field reporting `source` (`"local"` or `"remote"`), the index `indexed_at` timestamp, and a `fell_back_reason` tag when applicable. The caller can therefore detect when local served the query and when it did not.
