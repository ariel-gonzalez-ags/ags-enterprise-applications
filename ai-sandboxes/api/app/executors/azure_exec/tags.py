"""Chargeback tagging model. Every sandbox resource group is tagged at
creation; these tags are the backbone of cost tracking and multi-tenant
chargeback (Azure Cost Management can group/filter spend by them).

One sandbox = one resource group = one task run. The org is the chargeback
unit; owner_sub attributes it to a person; task/run ids give per-run
granularity. Keep keys stable: renaming a tag breaks cost continuity.
"""

MANAGED_BY = "agisphire"


def sandbox_tags(*, task_id: str, owner_sub: str, org_id: str, run_id: str,
                 ttl_minutes: int) -> dict[str, str]:
    """The tag set applied to a sandbox resource group (and required on every
    resource the agent creates inside it, enforced by Azure Policy)."""
    return {
        "ags:managed-by": MANAGED_BY,
        "ags:task-id": task_id,
        "ags:run-id": run_id,
        "ags:owner-sub": owner_sub,
        "ags:org-id": org_id,
        "ags:ttl-minutes": str(ttl_minutes),
    }


def sandbox_rg_name(task_id: str) -> str:
    """One RG per sandbox run, named so a human can find it in the portal and a
    reaper can find all of ours with a single prefix scan."""
    return f"ags-sb-{task_id}"


def identity_name(task_id: str) -> str:
    return f"ags-id-{task_id}"


def container_group_name(task_id: str) -> str:
    return f"ags-run-{task_id}"
