# Security Policy

## Supported versions

Only the latest release (the `latest` image tag) receives security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report it privately through GitHub instead: open the repository's **Security** tab and click
**Report a vulnerability** (GitHub private vulnerability reporting).

Please include:

- a description of the issue and its impact,
- steps to reproduce (version / image tag, configuration, requests),
- a suggested fix, if you have one.

You can expect a first reply within 7 days. Please give us a reasonable amount of time to release
a fix before you disclose the issue publicly.

## Hardening notes for operators

- Do not expose Last Call directly to the internet. Put it behind a reverse proxy with HTTPS
  (and optionally forward auth such as Authentik), then set `LASTCALL_SECURE_COOKIES=1`.
- API keys and the SMTP password are stored in the SQLite database in `/data`. Protect that volume
  and its backups.
- Use a Jellyfin API key dedicated to Last Call, so you can revoke it independently.
- The Plex token is your server owner's token. Treat it like a password; Last Call removes it from
  error messages and logs.
