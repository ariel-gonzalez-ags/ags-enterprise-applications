"""Planner agent — Gemini via Google's OpenAI-compatible endpoint.

Deliberately uses the `openai` SDK against the Gemini OpenAI-compat shim so
the move to Vertex AI + WIF is a client-factory change only (base_url/auth),
not a rewrite of routes or services.

Contract: the model must reply with JSON {reply, plan|null}. `reply` is the
chat text; `plan` (when the conversation is converged) carries the proposed
deliverables/clouds/estimate for the in-chat plan card.
"""
import json
from typing import Optional

from openai import AsyncOpenAI

from .config import Settings

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_SYSTEM = """You are the Agisphire planner agent. Users describe infrastructure
or ops work; you clarify, then propose an execution plan that will run in an
ephemeral cloud sandbox with verified results.

Always respond with STRICT JSON, no markdown fences:
{
  "reply": "<chat text, concise, plain text with newlines>",
  "title": "<short task title, <=60 chars, inferred from the conversation>",
  "plan": null | {
    "summary": "<1-2 sentence execution plan>",
    "clouds": ["azure"|"aws"|"gcp"],
    "deliverables": [{"id": "<terraform|ansible|arm|bash|powershell|markdown>", "why": "<short rationale>"}],
    "est_hours": <number>
  }
}

Rules:
- Ask clarifying questions while requirements are ambiguous (plan = null).
- Propose exactly one plan when you have enough to execute; after that keep
  the same plan unless the user changes scope.
- Deliverables are what the sandbox run will produce and verify.
- Prefer idempotent IaC deliverables when the task is provisioning-shaped.
- Be terse: reply under 80 words, plan summary one sentence, each "why"
  under 12 words. Total response well under 900 tokens."""


class PlannerUnavailable(Exception):
    pass


def _client(settings: Settings) -> AsyncOpenAI:
    if not settings.planner_configured:
        raise PlannerUnavailable("GEMINI_API_KEY not set")
    return AsyncOpenAI(api_key=settings.gemini_api_key, base_url=_BASE_URL)


def _fallback(reply: str) -> dict:
    return {"reply": reply, "title": None, "plan": None}


async def reply(settings: Settings, history: list[dict]) -> dict:
    """history: [{'role': 'user'|'agent', 'text': ...}] oldest first.
    Returns {'reply', 'title', 'plan'}; never raises on model/parse errors —
    a degraded chat is better than a broken one."""
    messages = [{"role": "system", "content": _SYSTEM}]
    for m in history[-20:]:  # keep context window small and cheap
        messages.append({
            "role": "user" if m["role"] == "user" else "assistant",
            "content": m["text"],
        })

    try:
        resp = await _client(settings).chat.completions.create(
            model=settings.gemini_model,
            messages=messages,
            temperature=0.3,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )
        choice = resp.choices[0]
        raw = choice.message.content or ""
        if choice.finish_reason == "length":
            return _fallback("The plan got long — could you narrow the scope a bit?")
    except PlannerUnavailable:
        raise
    except Exception as exc:  # API/network/quota — degrade gracefully
        return _fallback(f"(planner temporarily unavailable: {type(exc).__name__})")

    try:
        data = json.loads(raw)
        assert isinstance(data.get("reply"), str)
    except (ValueError, AssertionError):
        return _fallback(raw.strip()[:800] or "(planner returned an empty response)")

    plan = data.get("plan")
    if not isinstance(plan, dict):
        plan = None
    return {
        "reply": data["reply"],
        "title": data.get("title") if isinstance(data.get("title"), str) else None,
        "plan": plan,
    }
