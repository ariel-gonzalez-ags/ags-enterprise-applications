# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security reports.**

Email **security@agisphire.example** (replace with the real address before
going public) with:

- A description of the issue and its potential impact
- Steps to reproduce or a proof of concept
- Affected component (`web/`, `api/`, infra config)

You can expect an acknowledgment within **3 business days**. We will keep you
informed as we investigate and will credit you in the fix notes unless you
prefer otherwise.

## Scope

This repository contains a marketing site and its auth backend. In scope:

- The OAuth2/OIDC flow (`api/app/routers/auth.py`) — PKCE, state, session cookies
- Session signing (`api/app/session.py`)
- The nginx proxy configuration (`nginx.conf`)
- Anything that could expose user data, session tokens, or credentials

Out of scope: denial of service against the demo deployment, issues in
third-party dependencies already tracked by Dependabot, and reports requiring
physical access or social engineering.

## Secrets & credentials

No secrets are stored in this repository by design (`.env.local` and
`secrets/` are gitignored; push protection is enabled). If you find a
credential committed anywhere in the history, report it immediately — the
credential will be rotated and the history scrubbed.
