"""Sandbox executors: the seam between the task state machine and where a run
actually happens.

`run_task()` (app/runner.py) is executor-agnostic: it drives drafting ->
running -> verified, narrates progress, and records artifacts. An executor is
the thing that, given a run payload, provisions an isolated environment,
executes the deliverables inside it, streams logs back, and tears it down.

Swap point: `get_executor(settings)` picks the backend from config. `azure`
runs the payload as a real, isolated Azure sandbox (resource group + per-RG
managed identity + agent container). BYOC is a further credential source
behind the same interface, not a rewrite. When the executor is disabled
(EXECUTOR_ENABLED=0) the runner uses its simulated path instead.
"""
from .base import Executor, RunResult

__all__ = ["Executor", "RunResult", "get_executor"]


def get_executor(settings) -> Executor:
    """Build the executor for the configured backend. `azure` runs a real,
    isolated sandbox (RG + per-RG identity + agent container). Any other value
    is a misconfiguration: raise so it surfaces, rather than silently running
    the wrong thing. The runner only calls this when real_executor is set."""
    backend = getattr(settings, "executor_backend", "azure")
    if backend == "azure":
        # Named azure_exec (not azure) so our package never shadows the Azure
        # SDK's `azure` namespace package.
        from .azure_exec import AzureExecutor
        return AzureExecutor(settings)
    raise ValueError(f"unknown executor backend: {backend!r} (expected 'azure')")
