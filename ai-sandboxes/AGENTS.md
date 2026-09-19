# Agisphire. Agent Guide

You are working on **Agisphire**: a marketing site that is becoming a product.
Static [Astro](https://astro.build) frontend (nginx) + FastAPI backend (auth,
future product API). This file is the single source of truth for how the
project is structured and how to change it. Read it fully before editing
anything.

## What this is

Two services, orchestrated by `docker-compose.yml`:

- **`web/`**: static Astro site, compiled to plain HTML at build time, served
  by nginx-unprivileged. No UI framework, no CSS framework, and only one
  sanctioned piece of client JS (`public/js/auth.js`, see rule 8).
- **`api/`**: FastAPI service. Google OAuth (authorization-code flow
  with PKCE, signed session cookie), health endpoint, and the product API:
  tasks, brainstorm chat with the planner (Gemini), plan approval, simulated
  sandbox runs. Python files live under `api/app/`, routers in
  `api/app/routers/`. State persists in SQLite on a compose volume.

The browser only talks to nginx (container :8080, host :8090). nginx serves
static files directly and proxies `/api/*` to the api container.

**Phase note.** Auth (phase 2) and the product backend (phase 3: tasks,
planner chat, simulated runs) are done. Next is real sandbox execution: when a requirement crosses new architectural ground (cloud credentials,
sandbox orchestration, websockets for live status), **stop and flag it to
the user before implementing**. See "Evolution path".

## Stack decisions (already made, don't revisit without a reason)

- **Astro 7**, static output. Zero JavaScript ships to the browser beyond
  `public/js/auth.js`. Note: Astro 7 minifies inlined CSS (lowercase hex,
  no spaces): smoke tests must match tolerantly, not by exact bytes.
   Astro 7 also **drops elements with `style="display:none"`** at build
   time, use the `hidden` attribute instead (gate overlay in ConsoleShell).
   **Inline `<script>` bodies get quote-normalized to backticks** by the
   bundler: smoke tests that assert on script contents must match with a
   `['`]` character class, not an exact single-quoted string.
   The sign-in screen is a dedicated page (`pages/login.astro`, copy in
   `content/home.js` `login`): anonymous `/app` visitors are redirected
   there by the gate script, and signed-in visitors to `/login` are bounced
   back to `/app`. The `#gate` overlay in ConsoleShell is now only a no-JS
   fallback linking to `/login`.
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
  `GEMINI_API_KEY`, `GEMINI_MODEL`, `BASE_URL`, `COOKIE_SECURE`: template in
  `.env.example`, real values in gitignored `.env.local`, injected by compose.
  Never in code or images.
- **Persistence**: SQLite via async SQLAlchemy (`app/db.py`, `app/models.py`),
  file on the `ags-data` compose volume mounted at `/data`. Task IDs are
  GUIDs (uuid4): product convention. Chosen over Postgres deliberately:
  zero extra services while the product is single-node; the dialect is
  abstracted, so Postgres later is a connection-string change (DATABASE_URL),
  not a rewrite. Revisit when we need multi-writer, real concurrency, or
  managed backups.
- **Planner LLM**: Gemini via Google's OpenAI-compatible endpoint, driven by
  the `openai` SDK (`app/planner.py`). This is deliberate, migrating to
  Vertex AI + Workload Identity Federation later changes only the client
  factory (base_url/auth), not routes or services. The model must return
  strict JSON `{reply, title, plan|null}`; parse/API failures degrade to a
  plain chat reply, never a 500.
- **Runs are simulated by default** (`app/runner.py`): an asyncio coroutine ticks
  `checks_passed` and lands at `verified` with per-format proof artifacts.
  It exercises the exact task state machine real sandboxes use. No Celery/Redis yet.
- **Real execution = Azure sandboxes (opt-in)** behind the executor seam in
  `app/executors/`. `run_task()` is the executor-agnostic state machine; the
  executor is the only thing that knows where a run happens. `EXECUTOR_ENABLED=1`
  + `EXECUTOR_BACKEND=azure` switches `run_task()` from the simulated timer to a
  real run. **The backend is agentic, not terraform-only**: a Gemini tool-calling
  (ReAct) agent (`executors/azure_exec/agent.py`) runs inside the sandbox with
  cloud tooling and does whatever is needed to reach the outcome, then verifies.
  **Containment is structural, never prompt-based**: the platform SP (the only
  subscription-level credential, in env) runs in our API and, per run, creates a
  tagged resource group + a user-assigned managed identity scoped `Contributor`
  to that RG ONLY, then launches the agent container (ACI) under that identity.
  The agent physically cannot leave its RG. Teardown deletes the whole RG.
  **Chargeback**: every RG is tagged (`ags:org-id` = chargeback unit,
  `ags:owner-sub`, `ags:task-id`, `ags:ttl-minutes`, `ags:managed-by`), and each
  run writes a `cost_events` ledger row (models.py) reconciled later against
  Azure Cost Management via those tags. `azure_configured` is on `/api/healthz`.
  Package is named `azure_exec`, NOT `azure`, so it never shadows the Azure
  SDK's `azure` namespace package (an earlier `executors/azure` broke imports).
  azure-mgmt-resource v26: import `ResourceManagementClient` from
  `azure.mgmt.resource.resources`, not `azure.mgmt.resource`.
  **Azure execution gotchas (learned the hard way):** (1) ACI anonymous Docker
  Hub pulls are rate-limited; the agent image must come from MCR
  (`mcr.microsoft.com/azure-cli`, no anon limit) or ACR. (2) `wait_terminal`
  must poll the container's instance `current_state.state` (Terminated), NOT
  the group's `provisioning_state`, which flips to Succeeded the moment ACI
  provisions the group and would otherwise tear down mid-run. (3) The agent
  must `az login --identity` before any az call. (4) The run's done-marker is a
   standalone `DONE` line; never substring-match it (`"DONE" in text` also
   matches `INCOMPLETE`). (5) The full agent transcript is persisted to the task
   as a `run.log` artifact before teardown, because teardown deletes the
   container and its logs. run.log shows ONLY the agent's own work, never the
   platform bootstrap (pip, `az login --identity`, `az account set`, the base64
   agent payload): `command_for()` does not `set -x` the bootstrap and silences
   its stdout, AND `transcript.customer_log()` drops everything before the agent
   loop's first marker (applied to the live feed and the persisted log). The
   agent logs FULL tool commands/args (no [:150] cut) and full reasoning; only
   long tool RESULTS are head+tail clipped with a spill pointer. (6) A
   sandbox-scoped Gemini key must not carry an IP
  allowlist, or ACI's egress IP gets a 403. (7) Verification requires every
  requested deliverable file to be present and non-empty in the transcript: an
   agent that declares done but skips a file (leaving an empty artifact) is
   REJECTED as incomplete, and the system prompt makes writing each file
   mandatory. (7b) Verified also requires EVIDENCE, not just DONE: the agent has
   a `verify_outcome(check_command)` tool it MUST call before declare_done; the
   harness runs the check and emits `VERIFY-RESULT: exit=N` + `VERIFY-CMD:` +
   captured output, and the platform stamps verified only when a VERIFY-RESULT
   exits 0 (domain-agnostic: we check THAT a check passed, never WHAT). `verify.log`
   holds ONLY that evidence (command + pass/fail + output); `run.log` keeps the
   full transcript. (7c) Honest non-success outcomes: the agent can also call
   `declare_infeasible(reason, evidence)` (the ask is impossible, documented) or
   `declare_blocked(reason, evidence)` (the platform/sandbox failed it: auth,
   quota). These emit INFEASIBLE:/BLOCKED: + EVIDENCE: markers, WIN over DONE, and
   land the task in a TERMINAL `infeasible`/`blocked` state (not back to planned)
   with the documented reason + evidence stored as verify.log. The point: no
   silent failure and no fake success; the final decision is always an inspectable
   statement. (8) The task carries `run_stage` + a live `run_log` transcript
  (appended per line during the run) so the console can render a live activity
  feed while the agent works, instead of a frozen thread. (9) The Azure
  management SDK is SYNCHRONOUS: every SDK call in the executor must go through
  `asyncio.to_thread` (the `_blocking` helper), never run inline in the async
  generator. A blocking provision/teardown froze the whole event loop (healthz
  timed out, the UI stuck on "loading…") until this was fixed. (10) Ensure the
  per-RG role assignment has PROPAGATED before launching the agent
  (`_wait_for_rbac`): launching early makes the agent's `az login --identity`
  return "no subscriptions found" and spin. (11) **Agent context engineering**
  (mirrored in the API-side `agent.py` and the in-container `agent_script.SCRIPT`;
  keep them in sync): the ReAct loop keeps the window lean so long runs stay
  sharp and cheap. Each step it (a) PRUNES all but the last 3 tool results to a
  `[cleared]` stub (full text stays in the on-disk transcript), (b) truncates
  every tool result head+tail to ~1800 chars with the overflow spilled to a
  sandbox file the agent can grep (restorable: pointer kept, bulk dropped), (c)
  COMPACTS when estimated tokens (`chars//4`) cross `AGS_CTX_BUDGET` (default
  90000): a Gemini call summarizes the older head into a brief, keeping system +
  task + a verbatim recent tail, and (d) RECITES the goal + owed deliverables
  every 6 steps to fight lost-in-the-middle drift. The agent also has a
  `fetch_docs(url)` tool to consult current official docs (MS Learn, terraform
  registry) instead of guessing from training data. Patterns borrowed from
  OpenCode/Claude Code/OpenClaw harnesses. Both loops also accumulate
  `total_tokens` from each response's `usage` and emit a running
  `USAGE_TOKENS: <n>` line EVERY step (not just at clean completion): a hard
  abort kills the agent mid-loop, so the last printed running total is what the
  executor's `transcript.usage_tokens` (last-match) parses. Keep the token
  reporting in sync across both files too.
  **Embers (cost meter).** 1 Ember = $0.01, the customer-facing unit. Every run
  burns Embers from two meters we control exactly: sandbox-seconds (we create
  and destroy the sandbox) + agent LLM tokens (Gemini returns `usage`; the
  in-container agent prints a `USAGE_TOKENS:` line the executor parses).
  `app/embers.py` rates them at a blended configurable rate
  (`EMBER_PER_SANDBOX_MIN` + `EMBER_PER_1K_TOKENS`). Per-user `ember_accounts`
  balance + append-only `ember_ledger`; the trial allowance
  (`EMBER_TRIAL_ALLOWANCE`) is granted once per user and is the trial gate. The
  `/approve` route refuses with 402 when balance < estimated cost; the agent
  also self-limits mid-run (warns at 80% of `AGS_BUDGET_SECONDS`, grace window
  `EMBER_GRACE_SECONDS` to write deliverables, then stops) so an over-budget run
  never loses work silently. Embers are decoupled from real Azure spend on
  purpose (CostEvent reconciles margin internally); this is the industry pattern
  (Copilot credits, Anthropic CCU): never show raw infra cost, meter a logical
  unit.   Stripe top-up + card-gated trial (real anti-multi-account) is Phase 2.
  **`/usage` page.** A minimal usage/showback page (opencode-style: no charts,
  just numbers). Single-pane shell (ConsoleShell renders one centered column
  when only the `stage` slot is passed), `UsagePanel.astro` + `usage.js`
  rendering balance, an allowance progress bar (spent vs granted), aggregate
  stats, and a per-run history from `/api/embers`. Linked from ConsoleNav; the
  nav `active` state is path-aware (`Astro.url.pathname`).
  **Billing (Phase 2b: Stripe).** Server-side secret key only; the browser is
  always redirected to a Stripe-hosted Checkout page, so card numbers never
  touch our servers (PCI scope stays with Stripe). This is Stripe's standard
  integration model: a bearer secret for server-to-API calls + a webhook signing
  secret to verify inbound events. `app/billing.py` owns the Stripe logic; one
  Stripe Customer per user (keyed by Google `sub`, cached on
  `EmberAccount.stripe_customer_id`). Endpoints (`routers/billing.py`, under
  `/api/billing`): `GET /config` (enabled, peg, min, card_on_file), `POST
  /topup` (custom USD amount, `EMBER_MIN_TOPUP_USD` floor, returns a Checkout
  URL), `POST /card-setup` (a $0 setup-mode Checkout = the trial gate), and
  `POST /webhook` (UNAUTHENTICATED by design; the Stripe signature is the auth;
  an event that fails verification is 400 and never touches the ledger). Embers
  move ONLY in the webhook on `checkout.session.completed`: a payment session
  credits the purchased Embers (`topup`), a setup session flips `card_on_file`
  and grants the trial (`trial_grant`). A client can never credit itself.
  **Card-gated trial:** with `STRIPE_CARD_GATE=1` (default) and Stripe
  configured, `embers.get_or_create` holds the trial allowance until
  `card_on_file` is true (the anti-multi-account control). UI: `/usage` shows an
  "Unlock your trial" banner (no card) and an "Add Embers" top-up card (credit
  packs from `content/usage.js` `packs` + a custom amount, $10 min), both wired
  in `usage.js`. All Stripe SDK calls are sync -> wrapped in `asyncio.to_thread`
  (same discipline as the Azure SDK).
  **Kill switch (TODO #7).** A running task can be stopped mid-flight:
  `POST /api/tasks/{id}/abort` (owner-scoped, 409 unless running) calls
  `killswitch.request_abort(task_id)`, which flags the run and force-tears-down
  the live sandbox via the executor's `abort()` (idempotent RG delete). The run
  loop (`runner._run_real`) polls `killswitch.is_aborted` and breaks; the
  settlement is `_abort_run`: the Ember burn up to the kill still counts (the
  sandbox really ran), the task returns to `planned` (not `verified`), and the
  transcript is kept. Cancellation state lives in `app/killswitch.py`
  (executor registry + abort flag) so it is shared between the abort endpoint's
  coroutine and the run loop's. The console shows a red "Stop run" button in the
  Sandbox stage panel (RunInspector), behind the shared confirm dialog
  (`askConfirm` sets title + action label per call now that the dialog is shared
   by delete and stop-run).
  **Rate limits (TODO #6).** `app/ratelimit.py` caps a single user's blast radius
   even if they beat the card gate: `RATELIMIT_MAX_CONCURRENT` running sandboxes
   at once, and `RATELIMIT_MAX_SANDBOX_HOURS_DAY` of sandbox compute per UTC day
   (summed from `Task.sandbox_seconds`). Both checked at `/approve` (429), both
   off when 0. They cap *velocity*; the Ember gate caps *total spend*.
  **Teardown proof (TODO #13).** `lifecycle.teardown` does not just delete the
   resource group; it then GETs it and only reports `verified_gone` once Azure
   404s (eventual-consistency retry). The runner stores that proof as a
   `teardown.json` artifact on verified and aborted runs, so "verified" also
   means "provably nothing left running up cost."

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
│   ├── Dockerfile        ← python:3.14-slim + uvicorn, non-root, /data volume
│   ├── requirements.txt  ← pinned majors (fastapi, uvicorn, httpx,
│   │                       itsdangerous, sqlalchemy[asyncio], aiosqlite, openai)
│   └── app/
│       ├── main.py       ← create_app: CORS, /api/healthz, routers, lifespan db init
│       ├── config.py     ← env-driven settings (no secrets in code)
│       ├── db.py         ← async engine/session factory, create_schema
│       ├── models.py     ← Task/Message/Artifact (GUID task ids)
│       ├── planner.py    ← Gemini via OpenAI-compat endpoint, JSON contract
│       ├── runner.py     ← run state machine (simulated timer OR real engine);
│       │   │               settlement (Ember burn, artifacts, terminal state)
│       │   │               lives in _settle.py
│       ├── _settle.py    ← how a finished/aborted/failed run is settled
│       ├── simfiles.py   ← simulated-run artifact generation helpers
│       ├── ratelimit.py  ← per-user rate limits (concurrent + daily sandbox-hours)
│       ├── killswitch.py ← abort registry: executor handles + cancel flags (TODO #7)
│       ├── executors/    ← executor seam: base.py (RunPayload/RunResult contract),
│       │   │               images.py (template gallery). get_executor() picks the
│       │   │               backend from EXECUTOR_BACKEND
│       │   └── azure_exec/ ← Azure backend (named so it never shadows the SDK's
│       │       │             `azure` namespace): credentials.py (platform SP ->
│       │       │             mgmt clients), lifecycle.py (tagged RG + per-RG
│       │       │             identity + ACI agent container + teardown/cost row),
│       │       │             executor.py (AzureExecutor class: thin wrapper),
│       │       │             runloop.py (the provision->launch->stream->teardown
│       │       │             run body), agent_script.py (the in-container agent,
│       │       │             a raw SCRIPT string), agent_runner.py (command_for:
│       │       │             base64 + launch that script), agent.py (run_agent,
│       │       │             the API-side loop), agent_context.py (its helpers +
│       │       │             toolbelt + system prompt), tags.py (chargeback tags)
│       ├── events.py     ← in-process pub/sub bus for SSE task updates
│       ├── session.py    ← signed session cookie create/read/clear
│       └── routers/      ← auth.py (Google OAuth) + tasks.py (product API)
└── web/
    ├── astro.config.mjs
    ├── package.json
    ├── public/
    │   ├── assets/       ← static brand files (logo SVGs = favicon) + provider logos
    │   └── js/           ← ONLY client JS: auth.js (nav/session), usage.js
    │       │               (/usage), and console/ (the /app console as native
    │       │               ES modules: index.js entry + state/confirm/rail/
    │       │               thread/inspector/picker/palette/dataflow; rule 8)
    ├── src/
    │   ├── content/      ← ALL copy as JS data (home.js)
    │   ├── layouts/      ← Base.astro: <head>, fonts, global CSS, auth prop
    │   ├── styles/       ← tokens.css.astro (palette) + base.css.astro (primitives)
    │   ├── components/   ← one UI section per file, scoped styles
    │   └── pages/        ← index.astro + app.astro + login.astro (composition only)
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
   by name. Add new icon paths to that one file, never paste SVGs into pages.
6. **Pages are composition only.** `pages/index.astro` should read like a list
   of sections. If logic or copy appears in a page, move it to `content/` or a
   component.
7. **The logo mark is sacred.** Defined once in `components/Logo.astro` and as
   static files in `public/assets/`. Never redraw or restyle it elsewhere. See
   "Brand system" below.
 8. **Client-side JS is an explicit exception, not a pattern.** The sanctioned
   scripts are `public/js/auth.js` (nav auth state), the /app console under
   `public/js/console/` (data flow against `/api/tasks*`), `public/js/usage.js`
   (/usage data flow against `/api/embers`), the gate script in
   `pages/app.astro`, and the signed-in bounce in `pages/login.astro`. All are
   vanilla and only call `/api/*`.
   The console outgrew a single file (console.js hit ~950 lines), so it is split
   into **native ES modules** under `public/js/console/`, loaded by the gate as
   `<script type="module" src="/js/console/index.js">` and served raw (NOT
   bundled; the browser resolves the `import` graph). The modules: `state.js`
   (the single shared-state object `S` + the `dom` element map + pure helpers
   `api/esc/ago/provImg` + format tables + a null-safe `on(el,ev,fn)` listener
   helper), `confirm.js`, `rail.js`, `thread.js`, `inspector.js`, `picker.js`,
   `palette.js`, `dataflow.js` (renderAll/select/refreshList/SSE/poll),
   `index.js` (entry: imports all for wiring, then boots). Cross-module state
   flows ONLY through `S.*`; DOM refs ONLY through `dom.*`. Import cycles between
   dataflow and the feature modules are safe because the cross-calls happen at
   runtime (listeners/boot), never at module top level. Keep new console behavior
   in a focused module; do NOT let a module grow past the rule-1 size again.
   The console renders into `data-console="*"` hooks in the component shells;
   **any markup it injects needs `:global()` selectors in the component's
   `<style>`**: Astro scoping doesn't reach runtime DOM. Any further client
   JS needs the user's sign-off.
9. **Backend rules (api/):** routes under `routers/` (one file per domain),
   config only via `config.py`, sessions only via `session.py`. FastAPI docs
   endpoints stay disabled (`docs_url=None`). Cookie values must be
   alphanumeric-safe, for structured data, sign with itsdangerous rather
   than hand-rolling separators.

## Brand system

**The mark** (C4g-6 twin-arc ring + A1 counter A), 100×100 viewBox:

- **Ring**: two arcs `M14 50 A36 36 0 0 1 86 50` (top) and sweep-0 (bottom),
  `stroke #F05623`, `stroke-width 6`, round caps; bottom arc at 45% opacity.
- **A**: single evenodd path `M50 29 L67 70 L55.5 70 L53 61.5 L47 61.5 L44.5
  70 L33 70 Z M50 43 L53.8 54 L46.2 54 Z`: the open counter is what keeps it
  legible at 16px. Never fill it.
- **Colors**: A fills with `currentColor` by default (follows the active
  theme), `#060504` when forced light (`<Logo light />`).
  The ring is always ember, never recolor it, never add a background disc.
- **Clear space** = one arc-cap width on all sides.

**Palette** (tokens in `styles/tokens.css.astro`: reference these, don't copy):

| Token | Hex | Use |
|-------|-----|-----|
| `--accent` | `#F05623` | ember. CTAs, highlights, ring |
| `--text` | `#F2EDE8` | off-white, headings, the A |
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
the content-as-data pattern are stack-agnostic, carry them forward whatever
happens. Auth (phase 2) and the product backend (phase 3: tasks + planner +
simulated runs, SQLite persistence) are done. Growth from here:

1. **More pages / blog / docs** → stays static, just add pages. No flag needed.
2. **More product API surface** → new routers in `api/app/routers/`, gated by
   `session.get_session`. The session is identity-provider-agnostic:
   enterprise SSO (SAML/OIDC via Keycloak or Entra ID) later is a
   config-level change, not a rewrite.
3. **Real sandbox execution** (stage 1 done: `app/executors/`, opt-in via
   `EXECUTOR_ENABLED`) → the remaining swap is the backend. `local` runs on the
   host Docker daemon today; `azure` (stage 2) adds an `AzureExecutor` behind
   the same interface (platform service principal in env, images pushed to
   ACR), plus real cost guardrails since it bills our subscription. BYOC
   (per-customer cloud) is a later enterprise feature: a credential store
   feeding the same interface. Flag to the user before stage 2: it touches
   cloud credentials and spend.
4. **Multi-LLM / heavier planning** → the planner is provider-agnostic by
   construction (OpenAI-compat endpoint); Vertex AI + WIF is the planned
   swap, touching only the client factory.
5. **Postgres** → when single-node SQLite stops fitting (multi-writer,
   managed backups), it's a connection-string change. SQLAlchemy already
   abstracts the dialect; don't add it earlier "just in case".
6. **Heavy product UI** (live console) → reconsider client interactivity per
   feature (Astro islands), not as a blanket rewrite.

Never bolt a backend onto the static nginx image; the api service exists for
that. Marketing pages stay prerendered (static) regardless.

## Product API contract (what console.js expects)

| Endpoint | Purpose |
|---|---|
| `GET /api/tasks` | rail list (lightweight, no messages) |
| `POST /api/tasks` `{title, provider, model}` | create in `drafting`, returns the task |
| `GET /api/models` | planner models the user can pick (from `planner.MODELS`) |
| `GET /api/tasks/{id}` | full detail: messages, artifacts, config |
| `POST /api/tasks/{id}/messages` `{text}` | **202 instantly**; planner replies in background, lands in thread |
| `PATCH /api/tasks/{id}` `{formats, provider, model}` | user edits deliverables / target cloud / planner model (drafting/planned only); formats slug+dedupe (min 1), provider and model validated against allowlists |
| `DELETE /api/tasks/{id}` | 204; drafting/planned only. running/verified/delivered are records: 409 |
| `POST /api/tasks/{id}/approve` | `planned` → `running`, spawns the run. Gates in order: 402 if the Ember balance is short, 429 if a rate limit is hit |
| `POST /api/tasks/{id}/abort` | **kill switch** (running only, else 409): tears the sandbox down now via `killswitch.request_abort`; the run returns to `planned` and the Ember burn up to the kill is still settled |
| `GET /api/tasks/{id}/artifacts/{filename}` | artifact contents, owner-scoped; `Content-Disposition: attachment`, `no-store` |
| `GET /api/tasks/{id}/events` | SSE stream; one `{"changed": true}` nudge per state change, owner-scoped |

**Chat and runs are asynchronous by design.** The POST never waits on the
LLM; it sets `agent_pending` and returns. Live updates flow over SSE: the
console opens `GET /tasks/{id}/events` (an in-process pub/sub bus in
`app/events.py`) while a task is live (`running` or `agent_pending`) and
re-fetches the task on each `{"changed": true}` nudge. The nudge carries no
payload, so the stream can never drift from the DB; the planner and runner
call `events.publish(task_id)` after each commit. If SSE is unavailable or
drops, the client falls back to polling `GET /tasks/{id}` every 3s. This
decouples LLM latency and (eventually) multi-hour sandbox runs from any
HTTP request. Don't reintroduce synchronous planner calls. The bus is
single-node (one dict of asyncio queues); a multi-node swap means replacing
it with Redis pub/sub behind the same interface.

**The planner sees the task's current settings.** `planner.reply(settings,
history, state, model)` injects a context message with the accepted deliverables,
target cloud, and idempotent/destroy/max-hours values, so re-planning
respects the plan-card toggles instead of reverting to a prior plan. The
`model` arg is the per-task planner model from the New-task picker
(`Task.model`, validated against `planner.MODELS`, default
`gemini-3.6-flash`); the picker is data-driven from `GET /api/models`, so
adding a model (or another provider later) is a list edit, not new routes.

**Deliverable ids come from a canonical catalog, but custom is always open.**
The catalog (`web/src/content/console.js` `outputFormats`, mirrored by
`planner.CANONICAL_FORMATS` and `runner._FORMAT_FILES`) is the vocabulary the
planner PREFERS when proposing a plan. It is a preference, not a constraint:
the schema keeps `id` a free string (no enum), and a user-added custom
deliverable is resolved to a real file by `planner.normalize_format`. Because
prompt-steering alone is unreliable (the model emitted `Terraform_code`,
`markdown_runbook`), `planner._canonical_id` snaps near-miss ids to the
catalog after the model responds (underscores/case-insensitive, prefix/token
match) while leaving genuine customs (opa-gatekeeper-policy) untouched. The
catalog drives chip labels/kinds and artifact filenames; adding a known
format is a data edit in `outputFormats` + a filename in `_FORMAT_FILES`.

**The match-exactly rule only applies once the user has chosen deliverables.**
`_context_message` branches on `state.formats`: when it is non-empty, the
planner is told the deliverables list MUST match that set exactly (so toggles
stick); when it is empty (a fresh task), the planner is instead told to propose
the sensible set and never return an empty list. Enforcing "match exactly"
against an empty set made every first plan come back with `deliverables: []`
(the planner obeyed, seeded nothing, and stayed stuck), which surfaced in the
UI as a plan card with no Terraform/Ansible/Markdown chips. Keep the empty-set
branch permissive.

**Output is enforced by JSON schema, not prose.** Gemini 3.x are reasoning
models that ignore a prose-only format instruction; `planner` sends
`_PLAN_RESPONSE_FORMAT` (a `json_schema`) so the `{reply, title, plan}`
shape is mandatory. `max_tokens` is 3000 because reasoning tokens count
against the budget.

**The typing effect is client-side and model-agnostic.** Reasoning models
buffer the whole reply (no incremental token stream), so `console.js`
reveals the newest agent reply progressively (faux-typing) after it lands.
The effect is independent of which model produced the text; the plan card
pops in once the text finishes (structured JSON cannot render half-formed).

**Plan-card UI conventions in console.js.** Two things that are easy to get
wrong: (1) the New-task target-cloud highlight is driven only by the stored
pick (`localStorage` `ags-provider`), never by the selected task's provider,
otherwise the highlight snaps back to the task and the click looks dead. (2)
Each plan-card deliverable chip keeps its "why" rationale collapsed behind an
`.opt-info` toggle so the card stays compact; that toggle must not flip the
checkbox or fire the PATCH (the thread click handler returns early on
`.opt-info`).

All gated by the session cookie, scoped to `owner_sub` (404 across owners,
not 403, don't leak existence). Task JSON shape: `{id (GUID), title, state,
provider, formats[], idempotent, checks{passed,total}, updated}`; detail adds
`config{destroyAfter,maxHours}`, `messages[{role,text,plan,at}]`,
`artifacts[{id,kind,size,note,url}]`. The `url` is the download endpoint; the
console renders artifact cards as buttons that open an in-app viewer (modal
in RunInspector.astro, fetched as text) with a download link. Planner messages carry
`plan = {summary, clouds[], deliverables[{id,why}], est_hours} | null`.

Operational gotchas:
- DB schema changes: create_all does not migrate. db.py carries tiny idempotent column migrations; when the schema gets real, introduce Alembic.
- `/api/healthz` reports `oauth_configured` + `planner_configured`: check
  it first when chat or sign-in 503s; both mean "env var missing".
- The api container runs as `nobody`; the `/data` volume (SQLite file)
  inherits ownership from the image's `chown nobody:nogroup /data`. If you
  ever see `sqlite3.OperationalError: unable to open database file`, that's
  a volume created before that chown: `docker compose down -v` fixes it.
- nginx has `absolute_redirect off` (see nginx.conf). Without it, redirects
  like `/app` → `/app/` carry the container port (8080) and browsers hit
  connection-refused, because the host maps 8090→8080. Never re-enable
  absolute redirects while behind a port mapping.
- nginx `proxy_read_timeout` is 300s on `/api/` and `proxy_buffering` is
  off there: the SSE stream (`/tasks/{id}/events`) is long-lived and must
  not be buffered, and planner chat waits on an LLM round-trip. The planner
  client self-caps at 90s (one retry) so the worst case is an in-chat
  degradation message, not a 504. The app also sends `X-Accel-Buffering:
  no` on the stream. If you lower the nginx timeout, lower the planner's
  first.

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

### Always check automated PR review suggestions (mandatory)

Before asking the user to review or merge a PR, read its automated feedback and
resolve every finding: GitHub Advanced Security / CodeQL code-scanning notes,
Copilot review comments, dependency-review, and similar bots. Pull them with
`gh api repos/{owner}/{repo}/pulls/<n>/comments` and
`gh pr view <n> --json reviews,comments`. These fire often here (the mixed
`import`/`import from` note, unused variables, empty `except` blocks have all
recurred across PRs), so make checking them part of "the PR is ready", not an
afterthought the user has to point out. Fix the finding in code (do not just
dismiss it), re-run the affected suite, push, and confirm the check goes green.
When the same note recurs, fix the pattern everywhere in the file, not just the
line the bot flagged.

### Auth setup (one-time, human task)

Google OAuth credentials cannot be generated by an agent. Create them in the
[Google Cloud console](https://console.cloud.google.com/apis/credentials)
(APIs & Services → Credentials → Create OAuth client ID → Web application):

- Authorized JavaScript origin: `http://localhost:8090`
- Authorized redirect URI: `http://localhost:8090/api/auth/callback`

Then `cp .env.example .env.local`, fill in the values (generate
`SESSION_SECRET` with `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`),
and restart: `docker compose up -d`. Without credentials the site still works;
`/api/auth/login` returns 503 and DB schema changes: create_all does not migrate. db.py carries tiny idempotent column migrations; when the schema gets real, introduce Alembic.
- `/api/healthz` reports
`oauth_configured: false`.

### Testing auth without Google credentials

`scripts/test_auth_flow.py` exercises the full OAuth dance with Google's
token/userinfo endpoints mocked (PKCE, state enforcement, session creation,
tampering rejection):

```bash
python3 -m venv /tmp/apitest && /tmp/apitest/bin/pip install -q -r api/requirements.txt
/tmp/apitest/bin/python scripts/test_auth_flow.py
```

### Testing the product API without credentials

`scripts/test_tasks_flow.py` exercises the full task lifecycle with the
planner mocked (auth gate → create → chat → plan flips to planned → approve
→ simulated run to verified → ownership scoping):

```bash
/tmp/apitest/bin/python scripts/test_tasks_flow.py
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
- **No em-dashes (—) anywhere: not in copy, comments, docs, or commit
  messages.** They read as machine-generated. Rewrite with a period, colon,
  semicolon, or parentheses instead. Placeholder characters (empty states)
  are the only exception.
- No secrets in the repo, ever. `.env.local` is gitignored; `.env.example`
  documents the shape. FastAPI docs endpoints are disabled in production
  posture.
