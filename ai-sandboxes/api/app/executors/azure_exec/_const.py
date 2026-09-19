"""Shared constants + the off-loop helper for the Azure executor, split out so
both executor.py (the AzureExecutor class) and runloop.py (the run body) can use
them WITHOUT importing each other. This module imports nothing from the package,
so it is a leaf: keeping the shared bits here is what avoids an import cycle.
"""
from __future__ import annotations

import asyncio

__all__ = ["_AGENT_IMAGE", "BUILD", "_blocking"]

# Agent container base. Microsoft Container Registry (MCR), NOT Docker Hub:
# ACI's anonymous Docker Hub pulls hit the rate limit and the container sits in
# "Waiting" forever (the run then times out and tears down). MCR has no anon
# limit. azure-cli ships az + python3 + ensurepip; we bootstrap pip + openai in
# the command. Flagged for a curated ACR image with the toolchain preinstalled
# (faster cold start, pinned) once an ACR exists. See TODO.
_AGENT_IMAGE = "mcr.microsoft.com/azure-cli:latest"

# Bumped on each behavior change so a running container can prove which code it
# has (guards against the stale-image churn we hit while debugging). Surfaced
# in the first progress line.
BUILD = "azexec-2026-09-18.3"  # + per-step USAGE_TOKENS so hard aborts bill tokens


async def _blocking(fn, *args, **kwargs):
    """Run a synchronous Azure SDK call off the event loop. The management SDK
    is sync; calling it directly in this async generator blocks the whole api
    (healthz, SSE, every request) for the duration of the network call, which
    is what wedged the app during long provisions/teardowns."""
    return await asyncio.to_thread(fn, *args, **kwargs)
