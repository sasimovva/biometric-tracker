"""LLM layer on Pydantic AI: routing + structured extraction + grounded answers.

Text (route / extract / answer) runs on the local mlx-vlm server (Gemma 4) with an
automatic fallback to Claude Haiku if the local server fails. Image extraction uses
Claude Haiku (vision). Schemas are Pydantic-typed — Pydantic AI generates the prompt,
validates the output, and retries on a bad parse (replacing the old hand-written JSON
schema + regex). ANTHROPIC_API_KEY comes from the shared sops secrets via secrets_loader.

Public interface is unchanged (route / parse_user_input / answer_query /
parse_image_input) so telegram_bot.py is untouched.
"""
from __future__ import annotations

import base64
import os
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, PromptedOutput
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

LOCAL_LLM_BASE = os.getenv("LOCAL_LLM_BASE", "http://127.0.0.1:8080/v1").rstrip("/")
TEXT_LLM_MODEL = os.getenv("TEXT_LLM_MODEL", "mlx-community/gemma-4-12B-it-qat-4bit")
ANTHROPIC_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5")
# Photo OCR backend: "local" = Gemma 4 via mlx-vlm (fully local/free) with a Haiku
# fallback on error; "cloud" = Claude Haiku (best accuracy on dense screenshots).
VISION_BACKEND = os.getenv("VISION_BACKEND", "local")

# ---------------------------------------------------------------------------
# Typed schemas (replace the hand-written JSON-schema prompt + regex parsing)
# ---------------------------------------------------------------------------
Action = Literal["log", "history", "plan", "protocol"]
VALID_ACTIONS = {"log", "history", "plan", "protocol"}


class RouteDecision(BaseModel):
    """Which information source(s) are needed to respond to a message."""
    actions: list[Action] = Field(
        description="All sources needed. 'log' = recording new data (exclusive — "
        "if recording, this is the only action). 'history' = the user's past "
        "logged workouts/metrics. 'plan' = training schedule / what to do. "
        "'protocol' = diet/fasting/RS2 rules. A question may need several."
    )


class TrainingEffect(BaseModel):
    primary: str | None = None
    aerobic: float | None = None
    anaerobic: float | None = None
    load: int | None = None


class Workout(BaseModel):
    activity_type: str | None = None
    title: str | None = None
    distance_miles: float | None = None
    duration: str | None = None  # "HH:MM:SS"
    avg_pace: str | None = None
    calories_burned: int | None = None
    active_calories: int | None = None
    resting_calories: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    elevation_gain_ft: int | None = None
    elevation_loss_ft: int | None = None
    steps: int | None = None
    training_effect: TrainingEffect | None = None
    intensity_minutes: int | None = None
    body_battery_impact: int | None = None
    notes: str | None = None


class BiometricEntry(BaseModel):
    """Biometric/habit/workout data extracted from a message or screenshot."""
    weight_lbs: float | None = None
    trf_status: str | None = None  # e.g. "Fast Start", "Fasting", "36h Fast Completed"
    rs2_dose: str | None = None    # e.g. "20g", "1 tbsp (Oats)"
    workout: Workout | None = None


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------
ROUTER_INSTRUCTIONS = (
    "You route a fitness-tracker chat message. Decide which information source(s) "
    "are needed to fully respond. 'log' is exclusive (recording new data, not asking)."
)

EXTRACT_INSTRUCTIONS = (
    "Extract biometric, habit, and workout data from the message. Only fill fields "
    "that are stated or clearly inferable; leave everything else null. Duration is "
    "HH:MM:SS. For a workout: if steps aren't given for a hike/ruck/run, estimate at "
    "~2000 steps per mile; if pace isn't given, compute it from duration and distance; "
    "use sensible base defaults for training effect (aerobic 2.5-3.0, anaerobic 0.0, "
    "load 40, body_battery_impact -10) only when a workout is present. Potato starch "
    "goes in rs2_dose; fasting/TRF state goes in trf_status."
)

ANSWER_INSTRUCTIONS = (
    "You are a helpful fitness assistant. Answer the user's question using ONLY the "
    "reference information in the message. Be concise and friendly; use the units in "
    "the data (miles, bpm, etc.). If the information does not contain the answer, say "
    "so plainly. Do not invent facts."
)

VISION_INSTRUCTIONS = (
    "Extract all fitness, workout, and biometric numbers from the screenshot into the "
    "structured fields. Only fill what is visible or clearly inferable; leave the rest "
    "null. Duration is HH:MM:SS."
)

# ---------------------------------------------------------------------------
# Lazy agent construction (so ANTHROPIC_API_KEY is loaded before the Anthropic
# client is built). Each text role has a (local, haiku) pair for fallback.
# ---------------------------------------------------------------------------
_CACHE: dict = {}


def _agents() -> dict:
    if _CACHE:
        return _CACHE
    local = OpenAIChatModel(
        TEXT_LLM_MODEL,
        provider=OpenAIProvider(base_url=LOCAL_LLM_BASE, api_key="local"),
    )
    haiku = AnthropicModel(ANTHROPIC_MODEL)  # reads ANTHROPIC_API_KEY from env

    def pair(output_type, instructions):
        kw = {"instructions": instructions}
        if output_type is not None:
            kw["output_type"] = output_type
        return (Agent(local, **kw), Agent(haiku, **kw))

    _CACHE["router"] = pair(PromptedOutput(RouteDecision), ROUTER_INSTRUCTIONS)
    _CACHE["extract"] = pair(PromptedOutput(BiometricEntry), EXTRACT_INSTRUCTIONS)
    _CACHE["answer"] = pair(None, ANSWER_INSTRUCTIONS)
    # Vision pair: local Gemma (PromptedOutput, no tool-calling) + Haiku (native).
    _CACHE["vision_local"] = Agent(local, output_type=PromptedOutput(BiometricEntry),
                                   instructions=VISION_INSTRUCTIONS)
    _CACHE["vision_haiku"] = Agent(haiku, output_type=BiometricEntry,
                                   instructions=VISION_INSTRUCTIONS)
    return _CACHE


def _run_text(role: str, *args):
    """Run a text role on the local model, falling back to Claude Haiku on failure."""
    local_agent, haiku_agent = _agents()[role]
    try:
        out = local_agent.run_sync(*args).output
        print(f"🖥️  Local LLM ({TEXT_LLM_MODEL}) responded [{role}].")
        return out
    except Exception as e:
        print(f"⚠️  Local LLM failed ({e}); falling back to Claude {ANTHROPIC_MODEL}.")
        return haiku_agent.run_sync(*args).output


# ---------------------------------------------------------------------------
# Public API (unchanged signatures)
# ---------------------------------------------------------------------------
def route(text):
    """Return the list of actions a message needs. 'log' is exclusive."""
    try:
        actions = [a for a in _run_text("router", text).actions if a in VALID_ACTIONS]
        if "log" in actions:
            return ["log"]
        return actions or ["history"]
    except Exception as e:
        print(f"⚠️  Router failed ({e}); defaulting to 'log'.")
        return ["log"]


def parse_user_input(text):
    """Extract structured data from a free-form text message (returns dict or None)."""
    try:
        return _run_text("extract", f"Parse this message: {text!r}").model_dump()
    except Exception as e:
        print(f"❌ Text extraction failed: {e}")
        return None


def answer_query(text, context):
    """Answer a natural-language question using the provided reference context."""
    try:
        return _run_text("answer", f"{context}\n\n=== Question ===\n{text}").strip()
    except Exception as e:
        return f"⚠️ Couldn't answer that right now: {e}"


def parse_image_input(image_base64, mime_type, text_prompt=None):
    """Extract structured data from a workout/biometric screenshot.

    VISION_BACKEND="cloud" (default) uses Claude Haiku for best OCR accuracy;
    "local" uses Gemma 4 (mlx-vlm) and falls back to Haiku on error."""
    try:
        img = BinaryContent(data=base64.b64decode(image_base64), media_type=mime_type)
        prompt = "Extract the fitness/workout/biometric numbers from this screenshot."
        if text_prompt:
            prompt += f" Additional context: {text_prompt}"
        agents = _agents()
        if VISION_BACKEND == "local":
            try:
                print(f"🖼️  Local vision ({TEXT_LLM_MODEL})...")
                return agents["vision_local"].run_sync([prompt, img]).output.model_dump()
            except Exception as e:
                print(f"⚠️  Local vision failed ({e}); falling back to Claude {ANTHROPIC_MODEL}.")
        print(f"🔗 Vision via Claude ({ANTHROPIC_MODEL})...")
        return agents["vision_haiku"].run_sync([prompt, img]).output.model_dump()
    except Exception as e:
        print(f"❌ Image extraction failed: {e}")
        return None


if __name__ == "__main__":
    from secrets_loader import load_shared_secrets
    load_shared_secrets()
    for t in ["weight 184 today, took 20g potato starch", "show me my recent run",
              "what's my plan for today"]:
        print(f"{t!r} -> {route(t)}")
