# Sandbox agent images

Layered container images the Agisphire sandbox agent runs on. See TODO item 14b.

- `Dockerfile.base` — cloud-agnostic base: pinned python slim + git/curl/jq/yq/
  make + kubectl + terraform + the `openai` pip dep. **No cloud CLI.** This is
  the default "literally anything" image.
- `Dockerfile.azure` / `Dockerfile.aws` / `Dockerfile.gcp` — thin variants,
  each `FROM` the base plus that cloud's CLI (az / aws / gcloud). Docker layers
  stack, so a host pulls base once and only the thin cloud layer per run.

## Measured sizes (compressed, what a sandbox host pulls)

| image | compressed |
|---|---|
| base  | ~138 MB |
| azure | ~240 MB |
| aws   | ~205 MB |
| gcp   | ~222 MB |

## Build / push

`.github/workflows/sandbox-images.yml` builds base, then the three variants
`FROM` that exact base (by sha tag), and pushes all four to
`ghcr.io/<owner>/ags-sandbox-{base,azure,aws,gcp}:{sha,latest}`. Public images,
so a sandbox host pulls with no registry auth.

## Status: build-only (not yet wired into the executor)

These images are built and tool-verified but the executor does **not** use them
yet. Wiring them in is a separate, live-verification step:

- `api/app/executors/azure_exec/_const.py::_AGENT_IMAGE` still points at
  `mcr.microsoft.com/azure-cli:latest`.
- `agent_runner.command_for()` still base64-encodes `agent_script.SCRIPT` into
  the container command and bootstraps pip at runtime.

**Contract caveat:** `agent_script.py` exposes its agent as a `SCRIPT` raw
string, not an executable file — the executor decodes that string to
`/tmp/agent.py` and runs it. So `COPY agent_script.py` into the image is not
directly runnable as-is. When we wire the image in, either (a) have the image
materialize the runnable script from `SCRIPT` at build time (a tiny build step:
`python -c "from agent_script import SCRIPT; open('/opt/ags/agent.py','w').write(SCRIPT)"`),
or (b) refactor `agent_script.py` so the agent is a real importable/runnable
module and `SCRIPT` is derived from it. Then `command_for()` drops the base64 +
pip bootstrap and just `az login --identity` + `exec python3 /opt/ags/agent.py`.

Keep the MCR fallback until a ghcr image is proven in a real run (zero-spend
mock first, then one live run per cloud variant).
