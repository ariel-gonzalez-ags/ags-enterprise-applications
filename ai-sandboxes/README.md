# Agisphire

**Agisphire** — AI-executed cloud work, sandbox-tested and delivered with proof.
Static Astro frontend (nginx) + FastAPI backend (Google OAuth, product API).

## Run it

```bash
./scripts/dev.sh     # frontend hot-reload → http://localhost:4321
./scripts/prod.sh    # full stack in Docker → http://localhost:8090
```

## Pages & endpoints

- `/` — landing page (hero, workloads, how-it-works, sandbox proof, capabilities, stats, CTA)
- `/app` — future product console (protected; prompts Google sign-in)
- `/api/auth/*` — login / callback / me / logout (Google OAuth + PKCE)
- `/api/healthz` — API health + whether OAuth is configured

## Signing in

Auth needs a Google OAuth client (one-time setup):
[console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
→ Create OAuth client ID (Web application) → redirect URI
`http://localhost:8090/api/auth/callback`. Then:

```bash
cp .env.example .env.local   # fill in GOOGLE_CLIENT_ID/SECRET + SESSION_SECRET
docker compose up -d
```

Without credentials the site works fine; login returns 503 until configured.

## Contributing

Read **[AGENTS.md](AGENTS.md)** first — it defines the architecture, the
golden rules (copy in `content/`, tokens for colors, one section per
component, backend conventions), the brand system, and how changes are
verified.
