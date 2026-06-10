#!/usr/bin/env python3
"""Prototype: the bot's text path rebuilt on Pydantic AI (vs the hand-rolled version).

Same shape — route -> load context -> act — but the routing decision and the
extraction are Pydantic-typed structured outputs instead of a hand-written JSON
schema prompt + regex. Points at the live mlx-vlm server (Gemma 4) on :8080.

Run:  pai-venv/bin/python prototype_pydantic_ai.py
(standalone — does NOT touch the running production bot)
"""
from __future__ import annotations

import os
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from query_workouts import load_workouts  # reuse existing data layer (no heavy deps)

HERE = os.path.dirname(os.path.abspath(__file__))

# --- Model: local mlx-vlm via its OpenAI-compatible endpoint ---------------
# (One line to add a cloud fallback: pydantic_ai.models.fallback.FallbackModel)
MODEL = OpenAIChatModel(
    os.getenv("TEXT_LLM_MODEL", "mlx-community/gemma-4-12B-it-qat-4bit"),
    provider=OpenAIProvider(
        base_url=os.getenv("LOCAL_LLM_BASE", "http://127.0.0.1:8080/v1"),
        api_key="local",  # mlx-vlm ignores it, but the client requires a value
    ),
)

# --- Typed schemas (replace the giant hand-written JSON-schema prompt) ------
Action = Literal["log", "history", "plan", "protocol"]


class RouteDecision(BaseModel):
    """Which information source(s) are needed to respond."""
    actions: list[Action] = Field(
        description="All sources needed. 'log' = recording new data (exclusive). "
        "'history' = past logged workouts. 'plan' = training schedule. "
        "'protocol' = diet/fasting/RS2 rules. A question may need several."
    )


class Workout(BaseModel):
    activity_type: str | None = None
    title: str | None = None
    distance_miles: float | None = None
    duration: str | None = None  # "HH:MM:SS"
    avg_hr: int | None = None
    steps: int | None = None
    notes: str | None = None


class BiometricEntry(BaseModel):
    """Structured biometric/workout data extracted from a logging message."""
    weight_lbs: float | None = None
    trf_status: str | None = None       # e.g. "Fast Start", "Fasting"
    rs2_dose: str | None = None         # e.g. "20g", "1 tbsp"
    workout: Workout | None = None


# --- Agents (PromptedOutput so it works on the local model w/o tool-calling) -
router_agent = Agent(
    MODEL,
    output_type=PromptedOutput(RouteDecision),
    instructions=(
        "You route a fitness-tracker chat message. Decide which information "
        "source(s) are needed to fully respond. 'log' is exclusive (recording, "
        "not asking)."
    ),
)

extract_agent = Agent(
    MODEL,
    output_type=PromptedOutput(BiometricEntry),
    instructions=(
        "Extract biometric and workout data from the message. Only fill fields "
        "that are stated or clearly inferable; leave the rest null. Duration as "
        "HH:MM:SS."
    ),
)

answer_agent = Agent(
    MODEL,
    instructions=(
        "You are a helpful fitness assistant. Answer using ONLY the reference "
        "information in the message. Be concise and friendly. If the info does "
        "not contain the answer, say so plainly. Do not invent facts."
    ),
)


# --- Context loaders (date + the requested sources) ------------------------
def _knowledge(name: str) -> str:
    try:
        with open(os.path.join(HERE, "knowledge", name)) as f:
            return f.read()
    except OSError:
        return "(unavailable)"


def _recent_workouts(limit: int = 15) -> str:
    rows = []
    for w in load_workouts()[:limit]:
        rows.append(
            f"{w.get('date','?')} {w.get('day_of_week','')[:3]} | "
            f"{w.get('activity_type','?')} | {w.get('distance_miles',0)}mi | "
            f"{w.get('duration','?')} | {w.get('avg_hr',0)}bpm | {w.get('steps',0)} steps"
        )
    return "\n".join(rows) or "(no workouts logged)"


SOURCES = {
    "history": ("Recent workouts (most recent first)", _recent_workouts),
    "plan": ("Training plan / weekly schedule", lambda: _knowledge("workout_routine.md")),
    "protocol": ("Protocol — diet, fasting & RS2 rules", lambda: _knowledge("protocol_baseline.md")),
}


def build_context(actions: list[str]) -> str:
    today = date.today()
    parts = [f"=== Today ===\nToday is {today:%A}, {today.isoformat()}."]
    for key, (label, loader) in SOURCES.items():
        if key in actions:
            parts.append(f"=== {label} ===\n{loader()}")
    return "\n\n".join(parts)


# --- The reusable core: respond(message) -> reply --------------------------
def respond(text: str) -> str:
    actions = router_agent.run_sync(text).output.actions or ["history"]
    print(f"  🧭 actions: {actions}")

    if "log" in actions:
        entry = extract_agent.run_sync(text).output
        # (production bot would persist + git-sync here)
        return "📊 extracted -> " + entry.model_dump_json(exclude_none=True)

    prompt = f"{build_context(actions)}\n\n=== Question ===\n{text}"
    return answer_agent.run_sync(prompt).output


if __name__ == "__main__":
    import time

    tests = [
        "weight 183.5 today, did a 5 mile ruck with 30 lbs in 1h20m, HR 122, took 20g potato starch",
        "show me my recent run",
        "what is my workout plan for today",
        "what's my workout today and have I been hitting my mileage this week?",
        "when does my eating window open and how much potato starch?",
    ]
    for q in tests:
        print(f"\n📩 {q!r}")
        t = time.time()
        print(f"💬 {respond(q)[:400]}")
        print(f"   ({time.time() - t:.1f}s)")
