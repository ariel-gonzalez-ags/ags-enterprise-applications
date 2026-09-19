"""Launch the agent inside the sandbox ACI container.

The agent's body is the SCRIPT string in agent_script.py (split out for size,
rule 1); this module only builds the container command that delivers and runs
it: base64 the script, bootstrap pip, authenticate as the sandbox's managed
identity (so every az call is RBAC-scoped to its own resource group), then exec.
"""
from __future__ import annotations

from .agent_script import SCRIPT  # noqa: F401  (re-export; the agent payload)

def command_for() -> list[str]:
    """The ACI container command: install the toolchain, authenticate to Azure
    as the sandbox's managed identity (so every az call is RBAC-scoped to its
    own resource group), then run the agent script (base64, no custom image).
    """
    import base64
    b64 = base64.b64encode(SCRIPT.encode()).decode()
    return ["sh", "-c",
            # No `set -x` and bootstrap stdout is silenced: this preamble (pip,
            # az login, the base64 payload) is platform plumbing, and the
            # container's stdout becomes the customer-visible run.log. Tracing it
            # leaks our mechanism AND dumps an ugly base64 wall. Only failures
            # (stderr) surface here; the agent's own output starts at /tmp/agent.py.
            # azure-cli base has python3 but no pip. Bootstrap pip into a
            # self-contained dir and put it on PYTHONPATH so a partial system
            # pip can't break the import (the ACI failure mode we hit).
            "python3 -m ensurepip >/dev/null 2>&1; "
            "python3 -m pip install --quiet --target=/app/pylibs openai >/dev/null 2>&1; "
            "export PYTHONPATH=/app/pylibs; "
            # Authenticate as the attached per-RG managed identity, THEN set the
            # subscription context. Login used --allow-no-subscriptions, so without
            # `az account set` the CLI has no default subscription and any command
            # needing one (az cosmosdb, az account list) resolves against the
            # tenant and fails SubscriptionNotFound. provision() already blocked
            # until the identity's role assignment propagated, so these succeed.
            "az login --identity --allow-no-subscriptions >/dev/null 2>&1 || true; "
            # No quotes around the id: it is a GUID (safe), and baked-in quotes
            # made az account set fail with a malformed subscription id (real bug).
            "az account set --subscription $AZURE_SUBSCRIPTION_ID >/dev/null 2>&1 || true; "
            f"echo {b64} | base64 -d > /tmp/agent.py && "
            "PYTHONPATH=/app/pylibs python3 /tmp/agent.py"]
