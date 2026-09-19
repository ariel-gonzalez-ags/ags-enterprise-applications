"""Shared constants + the off-loop helper for the Azure executor, split out so
both executor.py (the AzureExecutor class) and runloop.py (the run body) can use
them WITHOUT importing each other. This module imports nothing from the package,
so it is a leaf: keeping the shared bits here is what avoids an import cycle.
"""
from __future__ import annotations

import asyncio

__all__ = ["_AGENT_IMAGE", "_IMAGE_FOR_PROVIDER", "image_for_provider", "BUILD", "_blocking"]

# Agent container image. We run the curated ghcr image (TODO 14b): the
# toolchain + the runnable agent (/opt/ags/agent.py) are baked in, so a run
# needs no runtime pip/bootstrap. ghcr public image -> no ACI pull auth and no
# anonymous rate limit (the reason we avoided Docker Hub). The prior MCR
# azure-cli image is the fallback if a ghcr image ever fails to pull.
_AGENT_IMAGE = "ghcr.io/ariel-gonzalez-ags/ags-sandbox-azure:latest"

# Which cloud-variant image a run uses, keyed by the task's provider. The
# toolchain differs per cloud (az / aws / gcloud baked into each variant); the
# base + agent are shared. Adding a cloud = add a variant image + an entry here
# (the executor backend routing is get_executor's job, not this map's).
_GHCR = "ghcr.io/ariel-gonzalez-ags/ags-sandbox"
_IMAGE_FOR_PROVIDER = {
    "azure": f"{_GHCR}-azure:latest",
    "aws": f"{_GHCR}-aws:latest",
    "gcp": f"{_GHCR}-gcp:latest",
}


def image_for_provider(provider: str) -> str:
    """Pick the cloud-variant image for a run from the task's provider. Unknown
    / unset provider falls back to azure (the only wired backend today); the
    executor backend routing (get_executor) is what refuses a provider with no
    live backend, so this never silently runs the wrong cloud's tooling where
    it matters."""
    return _IMAGE_FOR_PROVIDER.get((provider or "").strip().lower(), _AGENT_IMAGE)

# Bumped on each behavior change so a running container can prove which code it
# has (guards against the stale-image churn we hit while debugging). Surfaced
# in the first progress line.
BUILD = "azexec-2026-09-19.1"  # ghcr curated image (baked toolchain+agent); no base64/pip bootstrap


async def _blocking(fn, *args, **kwargs):
    """Run a synchronous Azure SDK call off the event loop. The management SDK
    is sync; calling it directly in this async generator blocks the whole api
    (healthz, SSE, every request) for the duration of the network call, which
    is what wedged the app during long provisions/teardowns."""
    return await asyncio.to_thread(fn, *args, **kwargs)
