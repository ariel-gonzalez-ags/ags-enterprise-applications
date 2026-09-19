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


def get_executor(settings, provider: str = "") -> Executor:
    """Build the executor backend for a run. Routing is BY PROVIDER (the task's
    target cloud), not a single global backend: each provider maps to the
    backend that hosts its sandbox. Today only `azure` has a real backend (RG +
    per-RG identity + ACI agent container); any other provider raises a clear
    'not supported yet' instead of silently running it on Azure. Adding a cloud
    = add its backend here + its credentials, not a rewrite of the runner.
    The optional `provider` arg defaults to the configured executor_backend so
    existing single-backend callers keep working.
    """
    # Per-task provider wins; fall back to the configured backend for callers
    # that don't carry a provider (single-backend dev/simulated paths).
    backend = (provider or getattr(settings, "executor_backend", "azure")).strip().lower()
    if backend == "azure":
        # Named azure_exec (not azure) so our package never shadows the Azure
        # SDK's `azure` namespace package.
        from .azure_exec import AzureExecutor
        return AzureExecutor(settings)
    raise ValueError(
        f"no executor backend for provider {backend!r} yet (only 'azure' is wired; "
        "aws/gcp backends land when we add those clouds)")
