import os
import json
import re
import urllib.request
import urllib.error

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

def parse_user_input(text):
    api_base = os.getenv("LLM_API_BASE", "http://localhost:11434/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "llama3")
    api_key = os.getenv("LLM_API_KEY", "unused")

    url = f"{api_base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    # Build the OpenAI-compatible request payload
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Parse this message: '{text}'"}
        ],
        "temperature": 0.1
    }

    return _send_request(url, headers, payload)

def parse_image_input(image_base64, mime_type, text_prompt=None):
    api_base = os.getenv("LLM_API_BASE", "http://localhost:11434/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "llama3-vision") # Typically a vision model locally or public
    api_key = os.getenv("LLM_API_KEY", "unused")

    url = f"{api_base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    user_text = "Identify and extract all fitness, workout, or biometric numbers from this screenshot. "
    if text_prompt:
        user_text += f"Also incorporate this context: '{text_prompt}'"

    # Build the standard OpenAI-compatible vision payload
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.1
    }

    return _send_request(url, headers, payload)

def _send_request(url, headers, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    print(f"🔗 Sending request to LLM at {url} using model '{payload['model']}'...")
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            content = res_data["choices"][0]["message"]["content"].strip()
            
            print(f"🤖 LLM Raw Response:\n{content}\n")
            
            # Clean up the output in case the LLM returned markdown code blocks
            cleaned = content
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
            if match:
                cleaned = match.group(1)
            
            # Additional safety: extract first matching JSON object
            cleaned = cleaned.strip()
            if not (cleaned.startswith("{") and cleaned.endswith("}")):
                json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
                if json_match:
                    cleaned = json_match.group(1)

            parsed = json.loads(cleaned)
            return parsed

    except urllib.error.URLError as e:
        print(f"❌ LLM API connection error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON response from LLM: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error in LLM parsing: {e}")
        return None

# Quick local test module
if __name__ == "__main__":
    # Test with dummy configs if env is empty
    os.environ["LLM_API_BASE"] = os.getenv("LLM_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai")
    os.environ["LLM_MODEL"] = os.getenv("LLM_MODEL", "gemini-1.5-flash")
    # For testing, you must supply your own GEMINI_API_KEY or OpenAI key if not running locally.
    
    test_text = "My weight is 185.6. Did a 4 mile ruck carrying 20 lbs in 1 hour. Heart rate was around 120. Took my 1 tbsp potato starch."
    print(f"📝 Test Input: {test_text}")
    res = parse_user_input(test_text)
    print(f"✨ Parsed Result:\n{json.dumps(res, indent=2)}")

