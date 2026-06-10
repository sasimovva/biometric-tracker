"""LLM extraction + querying for the biometric tracker.

Text (logging, intent classification, question answering) runs on a LOCAL model
served by mlx-vlm (default Gemma 4 12B, MLX) over an OpenAI-compatible endpoint —
fast, private, zero cost — with an automatic fallback to Claude Haiku if the
local server is unreachable. Image/vision parsing always uses Claude Haiku via
the Anthropic SDK. Swap the local model by setting TEXT_LLM_MODEL / LOCAL_LLM_BASE.

API key (ANTHROPIC_API_KEY) comes from the shared sops secrets via secrets_loader.
"""
import json
import os
import re
import urllib.request
import urllib.error

import anthropic

# --- Vision / Anthropic fallback model ---
ANTHROPIC_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5")
MAX_TOKENS = 1024

# --- Local text model (OpenAI-compatible server; mlx-vlm by default) ---
LOCAL_LLM_BASE = os.getenv("LOCAL_LLM_BASE", "http://127.0.0.1:8080/v1").rstrip("/")
TEXT_LLM_MODEL = os.getenv("TEXT_LLM_MODEL", "mlx-community/gemma-4-12B-it-qat-4bit")

# System prompt forcing structured JSON outputs
SYSTEM_PROMPT = """You are a precision data-extraction assistant. Your job is to extract biometric and habit data from the user's free-form chat message and output it in STRICT JSON format.

Your output must be a single JSON object. Do not include any explanation, markdown formatting, or notes.

The JSON schema you must return is:
{
  "weight_lbs": null or float (e.g. 185.4),
  "trf_status": null or string (e.g. "✅", "❌", "Fast Start", "Fasting", "36h Fast Completed"),
  "rs2_dose": null or string (e.g. "1 tbsp (Oats)", "20g", "0 tbsp"),
  "workout": null or {
    "activity_type": string (e.g. "Hiking", "Running", "Rucking", "Indoor Cycling", "Versaclimber HIIT"),
    "title": string (e.g. "Weighted Outdoor Hike (Ruck)"),
    "distance_miles": float,
    "duration": string in format "HH:MM:SS" (e.g. "00:59:40"),
    "avg_pace": string (e.g. "18:31" or "N/A"),
    "calories_burned": integer,
    "active_calories": integer,
    "resting_calories": integer,
    "avg_hr": integer,
    "max_hr": integer,
    "elevation_gain_ft": integer,
    "elevation_loss_ft": integer,
    "steps": integer,
    "training_effect": {
      "primary": string (e.g. "Base (Low Aerobic)", "Threshold (High Aerobic)"),
      "aerobic": float (e.g. 2.8),
      "anaerobic": float (e.g. 0.0),
      "load": integer
    },
    "intensity_minutes": integer,
    "body_battery_impact": negative integer (e.g. -12),
    "notes": string
  }
}

Rules for extraction:
1. Only populate fields that are mentioned or can be reasonably inferred. If a field is not present, set it to null.
2. For workouts: if the user describes a workout (e.g., rucking, running, cycling), extract all metrics. If steps are not mentioned but it is a hike/ruck/run, estimate steps based on ~2,000 steps per mile. If pace is not mentioned, calculate it from duration and distance. If training effect, intensity minutes, or body battery are not mentioned, estimate sensible base defaults (e.g. aerobic 2.5-3.0, anaerobic 0.0, load 40, body battery -10).
3. If the user mentions taking their potato starch, log it in 'rs2_dose'.
4. If the user mentions fasting or TRF state (e.g. 'struggling with TRF', 'started fast', 'completed 36h fast'), log it in 'trf_status'.
"""

ROUTER_PROMPT = """You route a fitness-tracker chat message to exactly ONE action.

- "log": the user is RECORDING new data (e.g. "weight 185", "did a 4 mile ruck", "took my potato starch", "started my 36h fast").
- "history": the user ASKS about their OWN PAST logged workouts/metrics (e.g. "show me my recent run", "how many miles this week", "what was my longest hike", "average heart rate").
- "plan": the user ASKS about their TRAINING SCHEDULE or what to do (e.g. "what's my workout today", "what's on Tuesday", "how long should I ruck", "what's the weekly split").
- "protocol": the user ASKS about diet/fasting RULES (e.g. "when does my eating window open", "how much potato starch", "when do I start my fast", "what supplements", "smoothie rules", "hydration target").

Respond with a single JSON object only: {"action": "log"|"history"|"plan"|"protocol"}."""

VALID_ACTIONS = {"log", "history", "plan", "protocol"}

QUERY_PROMPT = """You are a helpful fitness assistant. Answer the user's question using ONLY the reference information below. Be concise and friendly. Use the units in the data (miles, bpm, etc.). If the information does not contain the answer, say so plainly. Do not invent facts.

Reference information:
{context}
"""

_client = None


def _get_client():
    """Lazily build the Anthropic client (key is set by secrets_loader at runtime)."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ---------------------------------------------------------------------------
# Text backend: local mlx-vlm server first, Anthropic Haiku as fallback
# ---------------------------------------------------------------------------

def _local_chat(system, user_text, want_json):
    """Call the local OpenAI-compatible LLM server (mlx-vlm)."""
    payload = {
        "model": TEXT_LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.1,
    }
    if want_json:
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        f"{LOCAL_LLM_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def _anthropic_chat(system, user_text):
    """Fallback text call to Claude Haiku."""
    response = _get_client().messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


def _text_chat(system, user_text, want_json=True):
    """Run a text completion on the local server with Anthropic fallback."""
    try:
        content = _local_chat(system, user_text, want_json)
        print(f"🖥️  Local LLM ({TEXT_LLM_MODEL}) responded.")
        return content
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as e:
        print(f"⚠️  Local LLM unavailable ({e}); falling back to Claude {ANTHROPIC_MODEL}.")
        return _anthropic_chat(system, user_text)


def _extract_json(content):
    """Pull a JSON object out of a model's text response."""
    cleaned = content.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1)
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route(text):
    """Route a free-text message to one action (log/history/plan/protocol).

    Defaults to 'log' on failure (data-preserving: a misrouted question just
    yields an empty extraction, whereas a dropped log loses data)."""
    try:
        action = _extract_json(_text_chat(ROUTER_PROMPT, text, want_json=True)).get("action")
        return action if action in VALID_ACTIONS else "log"
    except Exception as e:
        print(f"⚠️  Router failed ({e}); defaulting to 'log'.")
        return "log"


def answer_query(text, context):
    """Answer a natural-language question using the provided reference context."""
    system = QUERY_PROMPT.format(context=context)
    try:
        return _text_chat(system, text, want_json=False).strip()
    except Exception as e:
        return f"⚠️ Couldn't answer that right now: {e}"


def parse_user_input(text):
    """Extract structured data from a free-form text message (logging)."""
    try:
        return _extract_json(_text_chat(SYSTEM_PROMPT, f"Parse this message: '{text}'", want_json=True))
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON from LLM: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error in text parsing: {e}")
        return None


def parse_image_input(image_base64, mime_type, text_prompt=None):
    """Extract structured data from a workout/biometric screenshot (Claude vision)."""
    user_text = (
        "Identify and extract all fitness, workout, or biometric numbers from "
        "this screenshot. "
    )
    if text_prompt:
        user_text += f"Also incorporate this context: '{text_prompt}'"

    print(f"🔗 Sending image to Claude ({ANTHROPIC_MODEL})...")
    try:
        response = _get_client().messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": image_base64},
                    },
                ],
            }],
        )
        content = next((b.text for b in response.content if b.type == "text"), "")
        print(f"🤖 LLM Raw Response:\n{content}\n")
        return _extract_json(content)
    except anthropic.APIError as e:
        print(f"❌ Anthropic API error: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error in image parsing: {e}")
        return None


# Quick local test module
if __name__ == "__main__":
    from secrets_loader import load_shared_secrets
    load_shared_secrets()

    for t in [
        "My weight is 185.6. Did a 4 mile ruck with 20 lbs in 1 hour. HR ~120. Took 1 tbsp potato starch.",
        "show me my recent run",
        "how many total miles have I logged?",
    ]:
        print(f"\n📝 {t!r}  ->  intent: {classify_intent(t)}")
