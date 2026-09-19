"""Sandbox lifecycle: the platform control plane. Creates an isolated,
fully-tagged resource group per run, a per-RG managed identity the agent runs
under (containment), and the ACI container that hosts the agent. Tears it all
down and returns a teardown proof + cost ledger row.

Pure orchestration over the Azure management SDK. All SDK calls go through the
`az` clients dict passed in, so tests inject fakes and never touch Azure.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from . import tags as tagger

# Contributor role definition id (well-known, same in every subscription).
_CONTRIBUTOR = "b24988ac-6180-42a0-ab88-20f7382dd24c"


def _rg_scope(subscription_id: str, rg_name: str) -> str:
    return f"/subscriptions/{subscription_id}/resourceGroups/{rg_name}"


class Sandbox:
    """Handle to one live sandbox; passed between lifecycle phases."""

    def __init__(self, task_id, owner_sub, org_id, ttl_minutes):
        self.task_id = task_id
        self.owner_sub = owner_sub
        self.org_id = org_id
        self.ttl_minutes = ttl_minutes
        self.run_id = uuid.uuid4().hex[:12]
        self.rg_name = tagger.sandbox_rg_name(task_id, self.run_id)
        self.identity_name = tagger.identity_name(task_id, self.run_id)
        self.container_group = tagger.container_group_name(task_id, self.run_id)
        self.identity_id = ""
        self.identity_client_id = ""
        self.created_at = int(time.time())


def provision(az, settings, sb: Sandbox) -> Sandbox:
    """Create the tagged RG, the per-sandbox identity, and scope that identity
    to the RG with Contributor. Returns the populated Sandbox."""
    tags = tagger.sandbox_tags(task_id=sb.task_id, owner_sub=sb.owner_sub,
                               org_id=sb.org_id, run_id=sb.run_id,
                               ttl_minutes=sb.ttl_minutes)
    az["resource"].resource_groups.create_or_update(
        sb.rg_name, {"location": settings.azure_location, "tags": tags})

    ident = az["msi"].user_assigned_identities.create_or_update(
        sb.rg_name, sb.identity_name, {"location": settings.azure_location})
    sb.identity_id = ident.id
    sb.identity_client_id = ident.client_id

    scope = _rg_scope(settings.azure_subscription_id, sb.rg_name)
    # Azure RBAC propagation is eventually consistent: a brand-new identity can
    # take ~30-60s before a role assignment on it succeeds. Retry with backoff
    # so a fresh sandbox doesn't fail on the propagation race.
    last_exc: Exception | None = None
    for attempt in range(6):
        try:
            az["auth"].role_assignments.create(
                scope=scope,
                role_assignment_name=uuid.uuid4(),
                parameters={
                    "role_definition_id": f"{scope}/providers/Microsoft.Authorization"
                                          f"/roleDefinitions/{_CONTRIBUTOR}",
                    "principal_id": ident.principal_id,
                    "principal_type": "ServicePrincipal",
                },
            )
            last_exc = None
            break
        except Exception as exc:  # propagation race / transient
            last_exc = exc
            time.sleep(min(2 ** attempt, 20))
    if last_exc is not None:
        raise last_exc
    # The assignment existing is not enough: it must be VISIBLE to the identity
    # before we launch the agent, or the agent's az login gets 'no access' and
    # spins. Block here until the assignment is readable at the RG scope.
    _wait_for_rbac(az, scope, ident.principal_id)
    return sb


def _wait_for_rbac(az, scope: str, principal_id: str,
                   timeout_seconds: int = 150, poll: float = 5.0) -> None:
    """Block until the identity's Contributor assignment is actually visible at
    the sandbox RG scope (i.e. RBAC has propagated). Launching the agent before
    this is what caused 'no subscriptions found' spin loops. Raises on timeout:
    better to fail the run fast than start an unauthenticated agent."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            for ra in az["auth"].role_assignments.list_for_scope(scope):
                if getattr(ra, "principal_id", None) == principal_id:
                    return
        except Exception:
            pass  # transient read error; keep polling
        time.sleep(poll)
    raise RuntimeError("RBAC assignment did not propagate in time")


def launch_agent(az, settings, sb: Sandbox, image: str, env: dict[str, str],
                 command: list[str]) -> None:
    """Run the agent container group in the sandbox RG with the per-RG managed
    identity attached, so the agent's tokens are scoped to its own RG only."""
    poller = az["aci"].container_groups.begin_create_or_update(
        sb.rg_name,
        sb.container_group,
        {
            "location": settings.azure_location,
            "identity": {
                "type": "UserAssigned",
                "user_assigned_identities": {sb.identity_id: {}},
            },
            "containers": [{
                "name": "agent",
                "image": image,
                "command": command,
                "environment_variables": [
                    {"name": k, "secure_value": v} for k, v in env.items()
                ],
                "resources": {"requests": {"cpu": 1.0, "memory_in_gb": 1.5}},
            }],
            "os_type": "Linux",
            "restart_policy": "Never",
        },
    )
    # Block until the group is actually deployed. Returning right after the
    # long-running operation is ACCEPTED (not complete) means the first log get
    # hits ContainerGroupDeploymentNotReady and crashes the run.
    poller.result()


def teardown(az, settings, sb: Sandbox) -> dict:
    """Delete the whole resource group (removes the identity, role assignment,
    container, and everything the agent created), then VERIFY it is actually
    gone, and return a teardown proof + cost ledger row. The post-delete
    existence check is the teardown PROOF (#13): teardown_complete is only true
    when Azure confirms the RG no longer exists, so 'verified' also means
    'provably nothing left running up cost'."""
    poller = az["resource"].resource_groups.begin_delete(sb.rg_name)
    poller.result()
    destroyed_at = int(time.time())
    duration_s = destroyed_at - sb.created_at
    # Prove it: after delete, a GET on the RG must 404. Retry briefly because
    # Azure's delete is eventually consistent.
    gone = False
    for _ in range(6):
        try:
            az["resource"].resource_groups.get(sb.rg_name)
        except Exception:
            gone = True  # a raise on GET = the RG no longer exists
            break
        time.sleep(2)
    return {
        "resource_group": sb.rg_name,
        "run_id": sb.run_id,
        "task_id": sb.task_id,
        "org_id": sb.org_id,
        "owner_sub": sb.owner_sub,
        "created_at": sb.created_at,
        "destroyed_at": destroyed_at,
        "duration_seconds": duration_s,
        # Estimated until reconciled with Azure Cost Management (by tags).
        "estimated_usd": 0.0,
        # Teardown proof: true only when Azure confirmed the RG is gone.
        "teardown_complete": gone,
        "verified_gone": gone,
    }


def container_state(az, sb: Sandbox) -> str:
    """Current provisioning state of the agent container group."""
    grp = az["aci"].container_groups.get(sb.rg_name, sb.container_group)
    return getattr(grp, "provisioning_state", None) or getattr(
        grp, "instance_view", None) and grp.instance_view.state or "Unknown"


def container_runtime_state(az, sb: Sandbox) -> str:
    """The agent container's instance state (Waiting/Running/Terminated). A
    container stuck in Waiting (e.g. image pull failure) never reaches a
    terminal provisioning state, so we watch this too and fail fast."""
    try:
        grp = az["aci"].container_groups.get(sb.rg_name, sb.container_group)
        for c in getattr(grp, "containers", []) or []:
            iv = getattr(c, "instance_view", None)
            cur = getattr(iv, "current_state", None)
            if cur is not None:
                return getattr(cur, "state", "Unknown")
    except Exception:
        # Best-effort state probe: if ACI read/parsing fails, fall back to
        # "Unknown" so the caller can continue polling until timeout/terminal.
        return "Unknown"
    return "Unknown"


async def wait_terminal(az, sb: Sandbox, timeout_seconds: int,
                        poll: float = 3.0) -> str:
    """Poll until the AGENT CONTAINER reaches a terminal runtime state
    (Terminated), it is clearly stuck (Waiting past a grace period), or the
    guardrail timeout. Returns 'Succeeded'/'Failed'/'Timeout'/'ImagePullStuck'.

    We watch the container's instance state, NOT the group's
    provisioning_state: the group flips to 'Succeeded' as soon as ACI
    provisions it (long before the agent finishes), which was the bug that made
    runs read empty logs and tear down mid-work."""
    deadline = time.time() + timeout_seconds
    waiting_since: float | None = None
    while time.time() < deadline:
        runtime = container_runtime_state(az, sb)
        if runtime == "Terminated":
            exit_code = container_exit_code(az, sb)
            return "Succeeded" if exit_code == 0 else "Failed"
        if runtime == "Waiting":
            waiting_since = waiting_since or time.time()
            # Stuck pulling/starting (image pull rate limit etc): don't wait
            # the whole timeout for a container that will never run.
            if time.time() - waiting_since > 90:
                return "ImagePullStuck"
        else:
            waiting_since = None
        await asyncio.sleep(poll)
    return "Timeout"


def container_exit_code(az, sb: Sandbox) -> int:
    """The agent container's exit code once Terminated (0 = success)."""
    try:
        grp = az["aci"].container_groups.get(sb.rg_name, sb.container_group)
        for c in getattr(grp, "containers", []) or []:
            cur = getattr(getattr(c, "instance_view", None), "current_state", None)
            if cur is not None and getattr(cur, "exit_code", None) is not None:
                return cur.exit_code
    except Exception:
        # Best-effort read: ACI state can be temporarily unavailable; treat as
        # unknown exit code so callers can continue polling/teardown flow.
        return -1
    return -1


def read_logs(az, sb: Sandbox, container: str = "agent") -> str:
    """Read the agent container's stdout/stderr. Tolerates the group being
    momentarily not-ready (ContainerGroupDeploymentNotReady) right after
    creation: returns empty rather than raising, so the poll loop keeps going."""
    try:
        logs = az["aci"].containers.list_logs(sb.rg_name, sb.container_group, container)
        return getattr(logs, "content", "") or ""
    except Exception:
        return ""


def debug_state(az, sb: Sandbox) -> str:
    """Diagnostic snapshot of the container group (state, exit, events) so a
    silent/empty-log failure is still diagnosable. Cheap; called on the error
    path only."""
    try:
        grp = az["aci"].container_groups.get(sb.rg_name, sb.container_group)
        parts = [f"group provisioning_state={getattr(grp, 'provisioning_state', '?')}"]
        for c in getattr(grp, "containers", []) or []:
            iv = getattr(c, "instance_view", None)
            cur = getattr(iv, "current_state", None) if iv else None
            if cur:
                parts.append(f"container state={getattr(cur,'state','?')} "
                             f"exit={getattr(cur,'exit_code','?')}")
            evs = getattr(iv, "events", None) if iv else None
            for e in (evs or [])[-4:]:
                parts.append(f"event: {getattr(e,'type','')} {getattr(e,'message','')}")
        return " | ".join(parts)
    except Exception as exc:
        return f"debug_state unavailable: {type(exc).__name__}"
