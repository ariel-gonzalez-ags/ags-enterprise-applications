"""Simulated-run artifact generation. These helpers produce the plausible,
clearly-marked deliverables the SIMULATED path stores; real executors replace
them with actual outputs. Split from runner.py so the orchestration stays lean.
"""
from .models import Task

# Known formats get realistic filenames; the runner must produce a file for
# EVERY accepted format, so unknown/custom ones fall back to a generic name.
FORMAT_FILES = {
    "terraform": ("main.tf", "8.2 KB", "winning configuration, fully idempotent"),
    "ansible": ("harden.yml", "5.7 KB", "idempotent playbook, check-mode clean"),
    "arm": ("main.bicep", "4.3 KB", "compiled clean, what-if empty"),
    "helm": ("chart.tgz", "9.6 KB", "lint clean, template renders"),
    "kubernetes": ("manifests.yaml", "6.8 KB", "validated, dry-run apply clean"),
    "dockerfile": ("Dockerfile", "1.8 KB", "multi-stage, hadolint clean"),
    "bash": ("setup.sh", "2.1 KB", "set -euo pipefail, rerunnable"),
    "powershell": ("Setup.ps1", "2.4 KB", "idempotent, supports -WhatIf"),
    "python": ("run.py", "3.3 KB", "typed, idempotent, exit-coded"),
    "json": ("policy.json", "3.1 KB", "definition + assignment, validated"),
    "yaml": ("config.yaml", "2.7 KB", "schema-validated"),
    "markdown": ("runbook.md", "6.4 KB", "what ran, evidence, how to re-verify"),
}
ALWAYS = [("verify.sh", "bash", "0.9 KB", "rerun the acceptance checks anywhere")]

# Extensions guessed from the format slug so custom deliverables get a sane
# filename instead of being dropped.
_EXT = {"json": ".json", "yaml": ".yml", "yml": ".yml", "helm": ".tgz",
        "dockerfile": "", "xml": ".xml", "hcl": ".tf", "csv": ".csv"}


def _mechanical_file(fmt: str) -> tuple[str, str, str]:
    """Offline fallback: extension guessed from any known token in the slug."""
    ext = next((e for tok in fmt.replace("_", "-").split("-") if (e := _EXT.get(tok))), ".txt")
    filename = fmt if not ext or fmt.endswith(ext) else fmt + ext
    return (filename, "1.2 KB", f"custom deliverable: {fmt.replace('-', ' ')}")


async def file_for(fmt: str, settings=None) -> tuple[str, str, str]:
    """(filename, size, note) for any accepted format; nothing is dropped.
    Custom formats ask the planner to resolve the real file (fixes typos,
    picks the extension the user meant: "jsn policy" -> json-policy.json).
    Falls back to the mechanical guess when the planner is unavailable."""
    if fmt in FORMAT_FILES:
        return FORMAT_FILES[fmt]
    if settings is not None:
        from . import planner
        norm = await planner.normalize_format(settings, fmt.replace("-", " "))
        if norm:
            ext = norm["ext"]
            filename = norm["format"] if norm["format"].endswith(ext) else norm["format"] + ext
            return (filename, "1.2 KB", f"custom deliverable: {fmt.replace('-', ' ')}")
    return _mechanical_file(fmt)


def simulated_content(filename: str, task: Task, note: str, total: int) -> str:
    """Plausible simulated content, clearly marked. Real executors replace
    this with actual outputs; the storage and delivery path stay."""
    return f"""# {filename}
# Deliverable for: {task.title}
# Task: {task.id} ({task.provider}) | checks: {total}/{total} green
# Note: {note}
#
# SIMULATED CONTENT. The sandbox executor is not wired yet; this file
# exercises the storage and delivery path that real outputs will use.
# Structure mirrors the real deliverable: idempotent, rerunnable, evidence-first.

# --- plan ---
# formats requested: {", ".join(task.formats) or "runbook"}
# idempotent result: {"yes" if task.idempotent else "no"}
# destroy sandbox after handover: {"yes" if task.destroy_after else "no"}
# max sandbox hours: {task.max_hours}

# --- evidence (simulated) ---
# check 1..{total}: PASS
# drift after re-apply: none
# sandbox teardown: complete; evidence retained under this task
"""
