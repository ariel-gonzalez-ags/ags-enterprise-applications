"""Launch the agent inside the sandbox ACI container.

The curated ghcr image (TODO 14b) already carries the toolchain and the
runnable agent at /opt/ags/agent.py (materialized from agent_script.SCRIPT at
image build time). So the container command is just: authenticate to Azure as
the sandbox's managed identity (so every az call is RBAC-scoped to its own
resource group), then run the baked agent. No base64 payload, no runtime pip
bootstrap -- that cold-start cost and failure surface moved to image build.
"""
from __future__ import annotations

def command_for() -> list[str]:
    """The ACI container command: authenticate to Azure as the sandbox's
    managed identity, then run the agent baked into the image. No `set -x` and
    the login preamble's stdout is silenced: it is platform plumbing, and the
    container's stdout becomes the customer-visible run.log -- tracing it leaks
    our mechanism. Only failures (stderr) surface here.
    """
    return ["bash", "-lc",
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
            # The agent loop + the openai dep are baked into the image.
            "exec python3 /opt/ags/agent.py"]
