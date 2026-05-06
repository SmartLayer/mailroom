---
name: mailroom
description: Search, read, and look up information from the user's IMAP mailboxes via the mailroom CLI, and send mail when asked. Trigger when the user asks to find or look something up in their email, recall what someone has said in mail, summarise correspondence with a person, search the inbox for a topic, or check replies. Phrases like "tell me about X from emails", "what did X email me about", "find Y in my mail", "search my inbox for Z", or "show recent messages from W" all route here. Use this rather than guessing an email address from a name.
---

# Email workflow via mailroom

## Process

0. Before drafting, note today's date; whether this is a reply; and if so, how long the delay was.
1. Show from / identity, to, cc, bcc, subject, body for review.
2. Wait for user approval.
3. Send via `mailroom`.
4. After sending, report `message_id_sent` from the JSON output.

Do not save a draft if told to send.

## One-shot principle

Each mailroom invocation should be the final call - no pre-flight `list`, `config-check`, or `folders` before a send the user already approved. If the command needs context (which identity, which folder), ask the user, not mailroom. mailroom errors are explicit and name the exact corrective flag, so a wrong invocation surfaces what to fix without a probe phase.

Skip `mailroom config-check` unless the user reports config-related trouble. Skip `mailroom list` unless the user explicitly asks "what identities do I have".

## Sending: pick a route

`compose --send`, `reply --send`, and `send-draft` all require an explicit route. Two forms:

- **Mode A (preferred when the user names an identity)**: `--identity NAME` resolves From address, display name, IMAP block, SMTP block, and sent_folder from the configured `[identity.NAME]` block. One flag, no other identity-related flags valid.
- **Mode B (relay-style sends without a configured identity)**: `--smtp NAME --from EMAIL [--name "Display Name"] [--fcc IMAP_NAME:FOLDER]`. The named `[smtp.NAME]` block must carry its own username/password. Use this for SES-style relays authorised to carry many addresses.

If the user names an identity ("send as partnerships"), use `--identity partnerships` directly. If the user gives only an address, ask whether they have an `[identity.NAME]` for it before falling back to mode B.

## Cowardly refusal: --allow-no-copy

mailroom refuses to send when no copy will be retained. A copy is retained iff FCC will run, OR the BCC list includes the sender's own address. A BCC addressed only to a third party (e.g. an auditor) does not count. Pass `--allow-no-copy` only when the user explicitly opts into a no-record send (e.g. throwaway sends through a relay that archives independently).

## Identity-level bcc (send-only identities)

`[identity.NAME]` may declare a `bcc = "self@x"` (string or list). On every send from this identity, those addresses are appended to BCC automatically. When `bcc` is set, the identity may omit `imap` entirely - it becomes send-only, and the self-BCC takes the place of FCC. Such identities cannot fetch, save drafts, or reply to a parent; they only do `compose --send`.

## New emails

```bash
mailroom compose \
  --to recipient@example.com \
  --subject "..." \
  --body "..." \
  --send --identity NAME
```

For HTML, add `--body-html "<p>...</p>"`. With both `--body` and `--body-html`, the message goes as `multipart/alternative`. Repeat `--attach <path>` for attachments. Repeat `--bcc <addr>` for envelope-only recipients.

The JSON output includes `message_id_sent` (recipient-visible Message-ID, differs from `message_id_local` when the smarthost rewrites it, e.g. SES) and `accepted_recipients`.

## Replies

```bash
mailroom -i <imap> reply -f <folder> -u <uid> --body "..." --send --identity NAME
```

Without `--identity`, mailroom matches the parent's recipients against the imap block's identities and uses the matching one. A miss errors rather than guessing. Threading headers (`In-Reply-To`, `References`) are filled from the parent automatically. `--reply-all`, `--cc`, `--body-html`, `--bcc`, `--attach` work the same as on `compose`. Drop `--send` to save a draft instead.

## Sending an existing draft

```bash
mailroom -i <imap> send-draft -f Drafts -u <uid>
```

Reads the draft, parses its From, matches it against the imap block's identities, transmits, and removes the draft on success. `--keep-draft` retains it. `--dry-run` connects and authenticates without sending. `--bcc` adds envelope-time recipients without rewriting the draft body. `--identity NAME` overrides the draft's From with a configured identity; `--smtp NAME --from EMAIL` is mode B.

## Top-level flags

- `-i, --imap NAME`: select a configured `[imap.NAME]` block. Omit to use `default_imap`. Repeat with `search` to query multiple blocks.
- `-A, --all-imap`: query every imap block (search only).
- `-c, --config PATH`: alternate config file.

There is no `-a` flag. The old `-a <account>` is now `-i <imap>`. The old `[[identities]]` table is now `[identity.NAME]` blocks.

## After a successful send

mailroom FCCs (IMAP-APPENDs) the wire-form bytes to the identity's Sent folder, with `Bcc:` stripped and `Message-ID:` rewritten to match the recipient-visible form, so threading later works because the FCC bytes carry the same Message-ID the recipient sees. Configure the Sent folder per identity via `sent_folder = "..."`; without it, mailroom auto-detects via SPECIAL-USE `\Sent` and falls back to `Sent`. When the SMTP block has `save_sent = false` (or `"auto"` resolving to false on Gmail, where the server auto-files), the FCC step is skipped.

## Looking up a person by name

When the user names a person to look up ("tell me about Alice Doe, from emails"), search by the name itself rather than constructing an address from it. AI often invents plausible-looking addresses such as `alicedoe@gmail.com` from the name; the actual address commonly has no surface relation to it, so a guessed address returns nothing. Issue a name-based query first, read a hit to learn the real address, then narrow.

## Lookups

`mailroom search` chains multiple keywords in one invocation. Each keyword becomes its own operation in the result, keyed by the operation string, so each message comes labelled with which keyword matched it:

```bash
mailroom -A search "sergio" search "panedas" search "sergiopanedas"
```

Output is JSON: `{op_key: {imap_name: {results: [...], provenance: {...}}}}`. Different limits per term, separate provenance per term, and per-term hit attribution all come for free. Use this pattern for any name-with-variants lookup, an event covered by several keywords, or related topics in one pass. With `[local_cache]` configured the queries run against a local index orders of magnitude faster than IMAP; without it the same calls hit IMAP. Every per-term response carries a `provenance` field reporting `source` (`"local"` or `"remote"`) and any fall-back reason.

`from:alice OR from:bob` is also a valid Gmail-style query. The IMAP server returns one mixed result set with no per-keyword attribution, so reach for it only when the union genuinely is what you want.

`mailroom -A` runs the chain against every imap block; `-i NAME` (repeatable) selects specific blocks. Verbs mix in one chain: `mailroom search foo read -f INBOX -u 42` runs the search and the fetch over one connection per block.

Read, list and extract attachments, or export the verbatim `.eml` for a hit:

```bash
mailroom -i <imap> read -f <folder> -u <uid>
mailroom -i <imap> attachments -f <folder> -u <uid>
mailroom -i <imap> save -f <folder> -u <uid> -i <name> -o <path>
mailroom -i <imap> export -f <folder> -u <uid> --raw -o /tmp/msg.eml
```

`mailroom list` enumerates configured blocks/identities/SMTP. Run it only when the user explicitly asks.
