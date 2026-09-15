# Agisphire — Agent Guide

You are working on **Agisphire**: a marketing site that is becoming a product.
Static [Astro](https://astro.build) frontend (nginx) + FastAPI backend (auth,
future product API). This file is the single source of truth for how the
project is structured and how to change it. Read it fully before editing
anything.

## What this is

Two services, orchestrated by `docker-compose.yml`:

- **`web/`** — static Astro site, compiled to plain HTML at build time, served
  by nginx-unprivileged. No UI framework, no CSS framework, and only one
  sanctioned piece of client JS (`public/js/auth.js`, see rule 8).
- **`api/`** — FastAPI service. Today: Google OAuth (authorization-code flow
  with PKCE, signed session cookie) and a health endpoint. Tomorrow: the
  product API (task submission, sandbox status). Python files live under
  `api/app/`, routers in `api/app/routers/`.

The browser only talks to nginx (container :8080, host :8090). nginx serves
static files directly and proxies `/api/*` to the api container.

**Phase note.** Auth landed in phase 2 (Google sign-in + protected `/app`
placeholder). Task submission and live status are next — when a requirement
crosses new architectural ground (database, background workers, websockets),
**stop and flag it to the user before implementing**. See "Evolution path".

## Stack decisions (already made — don't revisit without a reason)

- **Astro 7**, static output. Zero JavaScript ships to the browser beyond
  `public/js/auth.js`. Note: Astro 7 minifies inlined CSS (lowercase hex,
  no spaces) — smoke tests must match tolerantly, not by exact bytes.
  Astro 7 also **drops elements with `style="display:none"`** at build
  time — use the `hidden` attribute instead (gate overlay in ConsoleShell).
- **No UI framework** (no React/Vue). Components are `.astro` files.
- **No CSS framework** (no Tailwind). Hand-written CSS custom properties
  ("tokens") in `web/src/styles/`.
- **Content lives in plain JS data files** in `web/src/content/`. Pages and
  components map over that data. Copy edits never touch markup.
- **FastAPI** for the backend; **itsdangerous-signed session cookies** (no
  server-side session store yet); **Google OAuth** with PKCE (OIDC
  `openid email profile` scopes).
- **Docker**: web = multi-stage (node → nginx-unprivileged), container ports
  8080/8081; api = python:3.14-slim + uvicorn on 8000 (internal only).
  Host port 8090. All base images pinned by digest.
- **Node 26** for frontend builds; the web image contains no Node at runtime.
- **Secrets via env only**: `GOOGLE_CLIENT_ID/SECRET`, `SESSION_SECRET`,
  `BASE_URL`, `COOKIE_SECURE` — template in `.env.example`, real values in
  gitignored `.env.local`, injected by compose. Never in code or images.

## Directory map

```
.
├── AGENTS.md             ← this file
├── Dockerfile            ← web image: astro build → smoke tests → nginx
├── docker-compose.yml    ← web + api, host port 8090
├── nginx.conf            ← static + /assets + /api proxy; /healthz on 8081
├── .env.example          ← env template (copy to .env.local, gitignored)
├── scripts/
│   ├── dev.sh            ← frontend hot-reload (no docker), :4321
│   ├── prod.sh           ← docker compose up --build (production-like)
│   └── test_auth_flow.py ← e2e auth test with Google mocked (run in venv)
├── api/
│   ├── Dockerfile        ← python:3.14-slim + uvicorn, non-root
│   ├── requirements.txt  ← pinned majors (fastapi, uvicorn, httpx, itsdangerous)
│   └── app/
│       ├── main.py       ← create_app: CORS, /api/healthz, router mount
│       ├── config.py     ← env-driven settings (no secrets in code)
│       ├── session.py    ← signed session cookie create/read/clear
│       └── routers/auth.py ← Google OAuth: login/callback/me/logout
└── web/
    ├── astro.config.mjs
    ├── package.json
    ├── public/
    │   ├── assets/       ← static brand files (logo SVGs = favicon)
    │   └── js/auth.js    ← ONLY client JS: nav auth state via /api/auth/me
    ├── src/
    │   ├── content/      ← ALL copy as JS data (home.js)
    │   ├── layouts/      ← Base.astro: <head>, fonts, global CSS, auth prop
    │   ├── styles/       ← tokens.css.astro (palette) + base.css.astro (primitives)
    │   ├── components/   ← one UI section per file, scoped styles
    │   └── pages/        ← index.astro + app.astro (composition only)
    └── tests/checks.mjs  ← smoke tests against web/dist output
```

## The golden rules

1. **Keep every source file under ~250 lines.** When a file grows past that,
   split it: one section/component per file, shared copy into `content/`.
2. **Copy lives in `web/src/content/*.js`, never inline in pages.** To change
   text, edit the data file. Components receive content via props or imports.
3. **One component = one UI section.** Each `.astro` component carries its own
   `<style>` block (Astro scopes it automatically). Reusable primitives
   (`.wrap`, `.btn`, `.sec-*`) live in `styles/base.css.astro` only.
4. **Use design tokens, never hardcode colors.** The palette is defined once in
   `styles/tokens.css.astro` (`--accent`, `--panel`, …). Reference `var(--x)`.
5. **Icons come from `components/Icon.astro`.** Content files reference icons
   by name. Add new icon paths to that one file — never paste SVGs into pages.
6. **Pages are composition only.** `pages/index.astro` should read like a list
   of sections. If logic or copy appears in a page, move it to `content/` or a
   component.
7. **The logo mark is sacred.** Defined once in `components/Logo.astro` and as
   static files in `public/assets/`. Never redraw or restyle it elsewhere. See
   "Brand system" below.
8. **Client-side JS is an explicit exception, not a pattern.** The only
   sanctioned script is `public/js/auth.js` (nav auth state) plus the gate
   script in `pages/app.astro`. Both are vanilla, IIFE/scoped, and only call
   `/api/auth/*`. Any new client JS needs the user's sign-off.
9. **Backend rules (api/):** routes under `routers/` (one file per domain),
   config only via `config.py`, sessions only via `session.py`. FastAPI docs
   endpoints stay disabled (`docs_url=None`). Cookie values must be
   alphanumeric-safe — for structured data, sign with itsdangerous rather
   than hand-rolling separators.

## Brand system

**The mark** (C4g-6 twin-arc ring + A1 counter A), 100×100 viewBox:

- **Ring**: two arcs `M14 50 A36 36 0 0 1 86 50` (top) and sweep-0 (bottom),
  `stroke #F05623`, `stroke-width 6`, round caps; bottom arc at 45% opacity.
- **A**: single evenodd path `M50 29 L67 70 L55.5 70 L53 61.5 L47 61.5 L44.5
  70 L33 70 Z M50 43 L53.8 54 L46.2 54 Z` — the open counter is what keeps it
  legible at 16px. Never fill it.
- **Colors**: A is `#F2EDE8` on dark, `#060504` on light (`<Logo light />`).
  The ring is always ember — never recolor it, never add a background disc.
- **Clear space** = one arc-cap width on all sides.

**Palette** (tokens in `styles/tokens.css.astro` — reference these, don't copy):

| Token | Hex | Use |
|-------|-----|-----|
| `--accent` | `#F05623` | ember — CTAs, highlights, ring |
| `--text` | `#F2EDE8` | off-white — headings, the A |
| `--bg` | `#060504` | page background |
| `--panel` / `--panel2` | `#0C0A08` / `#12100D` | cards, raised surfaces |
| `--line` / `--line2` | `#1E1812` / `#2B2118` | borders |
| `--muted` / `--muted2` | `#98897E` / `#655950` | secondary text |
| `--warn` | `#FFB184` | pale ember accents |
| `--red` | `#E5484D` | errors only |
| `--deep-red` | `#A82405` | brand-only secondary, rarely used |

**Typography**: Inter for UI (400–700), JetBrains Mono for code/eyebrows/labels.
Eyebrows are uppercase, `letter-spacing .08em`, mono, `--accent` colored.

## Evolution path (as the product grows)

The design system (`styles/tokens`, `components/Logo`, `components/Icon`) and
the content-as-data pattern are stack-agnostic — carry them forward whatever
happens. Auth (phase 2) is done; the expected growth from here:

1. **More pages / blog / docs** → stays static, just add pages. No flag needed.
2. **Product features** (task submission, sandbox status) → new routers in
   `api/app/routers/`, gated by `session.get_session`. The session is
   identity-provider-agnostic: enterprise SSO (SAML/OIDC via Keycloak or
   Entra ID) later is a config-level change, not a rewrite.
3. **Persistence** (task history, user records) → flag to the user, then add
   a database as a third compose service. Do not start persisting state in
   cookies or in-memory dicts.
4. **Heavy product UI** (live console) → reconsider client interactivity per
   feature (Astro islands), not as a blanket rewrite.

Never bolt a backend onto the static nginx image; the api service exists for
that. Marketing pages stay prerendered (static) regardless.

## Workflow

```bash
# Develop frontend with hot reload (Node 22+ on host):
./scripts/dev.sh            # → http://localhost:4321

# Full stack (what CI/CD runs):
./scripts/prod.sh           # docker build both images + smoke tests → :8090
```

The web `Dockerfile` runs `web/tests/checks.mjs` against the built output
during `docker build`. **A failing smoke test fails the image build.** When
you add a section or page, add matching checks to `web/tests/checks.mjs`.

### Auth setup (one-time, human task)

Google OAuth credentials cannot be generated by an agent. Create them in the
[Google Cloud console](https://console.cloud.google.com/apis/credentials)
(APIs & Services → Credentials → Create OAuth client ID → Web application):

- Authorized JavaScript origin: `http://localhost:8090`
- Authorized redirect URI: `http://localhost:8090/api/auth/callback`

Then `cp .env.example .env.local`, fill in the values (generate
`SESSION_SECRET` with `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`),
and restart: `docker compose up -d`. Without credentials the site still works;
`/api/auth/login` returns 503 and `/api/healthz` reports
`oauth_configured: false`.

### Testing auth without Google credentials

`scripts/test_auth_flow.py` exercises the full OAuth dance with Google's
token/userinfo endpoints mocked (PKCE, state enforcement, session creation,
tampering rejection):

```bash
python3 -m venv /tmp/apitest && /tmp/apitest/bin/pip install -q fastapi httpx itsdangerous uvicorn
/tmp/apitest/bin/python scripts/test_auth_flow.py
```

## Verifying changes (mandatory)

Astro renders at build time, so checking `dist/` output is usually enough:

```bash
cd web && npm run build && npm test
```

For visual changes, render a screenshot before reporting done. The environment
has a Python venv with Playwright at `/tmp/opencode/svgtest/bin/python`:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':1400,'height':1000}, device_scale_factor=2)
    pg.goto('http://localhost:8090/', wait_until='networkidle')
    pg.wait_for_timeout(1500)
    pg.screenshot(path='/tmp/opencode/site.png', full_page=True)
    b.close()
```

## Conventions

- Language: plain HTML/CSS/JS in Astro components; Python 3.14 in `api/`.
  No TypeScript, no JSX.
- Indentation: 2 spaces (web), 4 spaces (python).
- Fonts: Inter (UI) + JetBrains Mono (code/eyebrows), loaded from Google Fonts
  in `layouts/Base.astro`.
- Naming: sections use kebab-case ids (`#workloads`, `#how`, `#demo`) matching
  nav links in `content/home.js`.
- No secrets in the repo, ever. `.env.local` is gitignored; `.env.example`
  documents the shape. FastAPI docs endpoints are disabled in production
  posture.
