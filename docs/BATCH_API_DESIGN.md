# Batch API Design

## Problem

The CLI currently opens a new IMAP connection per invocation. Scripts that need to
check many addresses (e.g. `update-campaign-progress.py`) call `mailroom search`
in a loop, creating one connection per query. With many contacts this causes IMAP
server throttling and linear wall-time growth.

## Output format

The batch response is a JSON object (not an array — array indices are integers and
cannot carry operation strings as keys). The outer key is the full subcommand
invocation string submitted by the caller. The inner key is the account name,
consistent with the existing single-operation JSON shape.

```json
{
  "search -f INBOX from:foo@bar.com": {
    "accountA": { "results": [...], "provenance": {...} },
    "accountB": { "results": [...], "provenance": {...} }
  },
  "search -f Sent to:baz@qux.com": {
    "accountA": { "results": [...], "provenance": {...} }
  },
  "read <message_id>": {
    "accountA": { "subject": "...", "body": "...", "headers": {...} }
  }
}
```

Operations are not limited to `search`. Any subcommand (`read`, `attachments`,
etc.) may appear in the same batch; the subcommand string plus its arguments form
the key.

### Key normalisation

The response echoes each key exactly as submitted. Canonicalisation is the
caller's responsibility. Two strings that differ only in whitespace are treated as
distinct operations.

### Partial failure

If an operation fails for a given account, the inner value for that account is
`{"error": "<message>"}`. A missing inner key means the operation was not
attempted for that account; an explicit error key means it was attempted and
failed. This distinction lets callers tell "no results" from "not reached".

## Execution model

Although the output is indexed by operation, IMAP connections are per-account.
Executing operation-first would open and close a connection for every
`(account, operation)` pair. The correct execution traversal is account-first:

```
for each account:
    open one IMAP connection
    execute every operation in the batch that applies to this account
    close the connection
```

This reduces connections from `accounts × operations` to `accounts`, regardless
of how many operations the batch contains.

### Concurrency default

By default accounts are processed sequentially — one account fully resolved
before the next connection is opened. This avoids triggering server-side
throttling on multi-account setups. Parallel execution across accounts is
reserved for a future release flag and requires no format change.
