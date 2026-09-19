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

## Status: the azure variant is wired into the executor (2026-09-19)

`api/app/executors/azure_exec/_const.py::_AGENT_IMAGE` points at
`ghcr.io/ariel-gonzalez-ags/ags-sandbox-azure:latest`. `Dockerfile.base`
materializes the runnable agent from `agent_script.SCRIPT` at image build time
(`/opt/ags/agent.py`, build fails if it isn't valid python), so
`agent_runner.command_for()` just does `az login --identity` + `az account set`
+ `exec python3 /opt/ags/agent.py` -- no base64 payload, no runtime pip
bootstrap (that ~1 min cold-start cost moved off the customer's bill).

Proven live (2026-09-19): a real ACI run on the public ghcr azure image reached
`verified`, produced real artifacts (runbook.md / main.tf / verify.log), the
run.log had no base64/pip/ensurepip bootstrap, and teardown was clean
(verified_gone, zero leftover RG, 323s).

The aws/gcp variants are built + tool-verified but NOT yet run in a live
sandbox (they need AWS/GCP creds configured; the planner picks the variant from
the task's `provider`).
