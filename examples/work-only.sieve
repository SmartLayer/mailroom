require ["mailroom-policy"];

# Example redact policy: hide every message where the work address
# is not a participant. Visibility = NOT (this rule), so personal
# correspondence sharing the same mailbox is replaced with a
# placeholder before reaching the agent or the model provider. The
# date and UID survive so the agent knows when redacted messages
# arrived; the subject, body, and every party address are blanked.
if not address :is ["from", "to", "cc"] "you@company.com" {
  redact;
}
