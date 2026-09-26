# Security (§94/§97-§100/§115/§136-§137)

* stdio: structured argv (no shell), command blocklist + allowlist, arg caps,
  allowlisted env — shell-injection tested (§115).
* HTTP: endpoint policy blocks loopback/private/link-local/metadata by default
  (`remote_public`); local MCP servers use `local_loopback` (§20). Cleartext
  http to remote hosts rejected in the default mode. Best-effort DNS check;
  hostname checks alone are NOT claimed as perfect SSRF defense (§21).
* Annotations (`readOnlyHint` etc.) are UNTRUSTED hints — preserved as
  metadata, never policy elevation; conservative tier descriptor for
  untrusted servers; SafetyEngine stays authoritative (§94/§100-tested).
* Tool descriptions/results are DATA (§99-tested): injection text cannot
  change policy fields, capabilities, or the manifest seat.
* Secrets: references only; catalog sync SCRUBS secret-shaped strings from
  metadata (§7/§115/§137-tested); no tokens in logs/events/context.
