"""Template-image gallery: which sandbox image a run uses.

The user asked for exactly this: predefined tooling per task type, with the
planner (or the deliverable set) deciding which image fits. An image is a
curated base with the common tools preinstalled; anything missing can still be
installed inside the run (the images carry a package manager).

Today the selection is deterministic (derived from the accepted deliverables),
which is testable and predictable. When the planner should choose, this module
is the single place to swap a rules-based pick for a model pick — the runner
just calls `select_image(formats, provider)`.

Each entry maps to a real base image. Stage 1 (local) uses them directly;
stage 2 (Azure) pushes the same images to ACR and references them there.
"""

# Curated bases. `base` is the upstream image; `tools` documents what's on it
# (for the gallery UI and for the planner's reasoning later). Keep bases
# minimal and pinned by major tag; heavy toolchains can be layered on demand.
GALLERY = [
    {
        "id": "iac",
        "name": "IaC workspace",
        "base": "hashicorp/terraform:1.11",
        "tools": ["terraform", "git", "sh"],
        "blurb": "Provisioning-shaped tasks: terraform plan/apply, idempotency re-apply.",
        "handles": {"terraform", "arm", "json", "yaml"},
    },
    {
        "id": "config",
        "name": "Config & automation",
        "base": "python:3.13-slim",
        "tools": ["python", "pip", "ansible (pip)", "bash"],
        "blurb": "Ansible playbooks, scripts, and general automation.",
        "handles": {"ansible", "bash", "powershell", "python", "yaml"},
    },
    {
        "id": "containers",
        "name": "Containers & k8s",
        "base": "bitnami/kubectl:latest",
        "tools": ["kubectl", "helm", "sh"],
        "blurb": "Kubernetes manifests, Helm charts, Dockerfile lint/dry-run.",
        "handles": {"kubernetes", "helm", "dockerfile", "yaml"},
    },
    {
        "id": "docs",
        "name": "Docs & general",
        "base": "alpine:3.21",
        "tools": ["sh", "git"],
        "blurb": "Runbooks and anything without a dedicated toolchain.",
        "handles": {"markdown"},
    },
]

# Order matters: the first image that handles any accepted format wins, so the
# most task-defining toolchains are checked before the generic fallbacks.
_PRIORITY = ["iac", "containers", "config", "docs"]

_DEFAULT = "docs"


def select_image(formats: list[str], provider: str = "azure") -> dict:
    """Pick the gallery image for a run. Deterministic: the highest-priority
    image whose `handles` intersects the accepted deliverables. Unknown/custom
    formats don't steer the pick (the docs image can still write them out)."""
    wanted = set(formats or [])
    by_id = {g["id"]: g for g in GALLERY}
    for gid in _PRIORITY:
        if by_id[gid]["handles"] & wanted:
            return by_id[gid]
    return by_id[_DEFAULT]
