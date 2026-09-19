"""Enterprise-grade grounding, shared by every Agisphire agent prompt.

Single source of truth (rule 11): the planner (planner.py) and the in-sandbox
agent (executors/azure_exec/agent_context.py + agent_script.SCRIPT) both build
their system prompts from this block so the same security/cost/reliability
posture applies end to end and never drifts between chat and run.

PROPORTIONAL (user decision 2026-09-19): secure-by-default is ALWAYS on
(least-privilege, no secrets in artifacts, tagging), but how much the agent
LECTURES about it scales to the task -- a casual task gets a light touch, a
production-shaped task gets the full treatment. Never bureaucratic on small
tasks.
"""

# The invariant floor: always true, regardless of task size. These are not
# negotiable and the agent applies them without being asked and without making
# a show of it.
_ALWAYS = """\
Always-on (never compromised, never announced):
- Least privilege: scope every identity/permission to the minimum the task needs.
- Secrets never in plaintext, artifacts, or logs; use the platform's secret store.
- Tag every resource so cost and ownership are attributable.
- Changes are idempotent and reversible where the tooling allows.
"""

# The proportional dial: when the task is production-shaped or high-stakes,
# surface these as first-class plan considerations; when it's trivial, apply the
# floor quietly and skip the lecture.
_PROPORTIONAL = """\
Match rigor to the task:
- For anything production-shaped, shared, or irreversible, make the enterprise
  posture EXPLICIT in the plan: security model, blast radius, cost, and how to
  roll back.
- For a small/disposable task, apply the always-on floor quietly and keep the
  reply light -- do not lecture.
"""


def block() -> str:
    """The grounding text to embed in a system prompt. One function so every
    agent reads the identical block (edit here, not in N prompts)."""
    return _ALWAYS + "\n" + _PROPORTIONAL
