# CLAUDE.md

Personal **biometric & performance tracker**. Logs Garmin workouts and habit/biometric
data (weight, time-restricted-feeding window, RS2 potato-starch dosing) into
month-segmented JSON + Markdown dashboards. Data is captured two ways: an
interactive **Telegram bot** (free-text + workout screenshots) and a direct
**Garmin Connect** sync.

## Layout

```
.
├── telegram_bot.py      # Interactive Telegram bot (telebot long-polling) — main service
├── secrets_loader.py    # Decrypts shared API keys from ~/Code/secrets.enc.json (sops + age)
├── llm_parser.py        # OpenAI-compatible LLM client; parses free text + screenshots → JSON
├── import_garmin.py     # Pull workouts from Garmin Connect → workouts DB + dashboards
├── query_workouts.py    # CLI to render/aggregate logged workouts
├── dashboard.md         # Human-facing index/portal of the monthly dashboards
├── dashboards/          # Monthly Markdown habit dashboards (YYYY-MM.md)
├── knowledge/
│   ├── workouts/        # Workout database, one JSON file per month (YYYY-MM.json)
│   ├── protocol_baseline.md
│   ├── workout_routine.md
│   └── protocol_scientific_briefing.md
├── hooks.json           # Agent hooks + scheduled-task prompts (Antigravity workspace config)
├── .env.example         # Local config template (LLM endpoint, optional overrides)
└── .gitignore           # .env is git-ignored
```

## Conventions

- **Python only**, single-file scripts (no package, no test suite, no linter, no build step).
  Each script is runnable directly. Targets Python 3.13+.
- **Data is month-segmented.** Workouts live in `knowledge/workouts/YYYY-MM.json`; habit
  dashboards in `dashboards/YYYY-MM.md`. Scripts derive the filename from the entry date —
  the matching dashboard must already exist for a row to be updated (it is not created).
- **Secrets are shared and encrypted.** API keys come from the `~/Code` monorepo's
  `secrets.enc.json` (sops + age), not from a local copy. `secrets_loader.py` decrypts it at
  runtime and merges keys into `os.environ` with `setdefault` — so the real environment and
  `.env` always take precedence. The age key is at
  `~/Library/Application Support/sops/age/keys.txt`. Never write decrypted secrets to disk.
- **Local-only config** (LLM endpoint, Garmin credentials) goes in `.env` (git-ignored);
  see `.env.example`.

## Telegram bot (the service)

`telegram_bot.py` runs a long-polling [`pyTelegramBotAPI`](https://pypi.org/project/pyTelegramBotAPI/)
bot. It accepts:

- **Commands:** `/start`, `/help`, `/stats` (aggregate workout stats), `/sync` (commit local
  data + `git pull --rebase` + `git push`).
- **Free text** → classified by the local model as **log** or **query** (see LLM section):
  - log (_"weight 185.2; did a 4mi ruck with 20lbs in 1h"_) → metrics extracted & saved.
  - query (_"show me my recent run"_, _"how many miles have I logged?"_) → answered in natural
    language from recent workout history.
- **Photos** (Garmin screenshots) → `llm_parser.parse_image_input` (Claude vision) → metrics.

Parsed metrics update the monthly dashboard row and/or the workout database. Access is gated
to `ALLOWED_USER_ID` when set (otherwise open).

**Single-consumer caveat:** the bot token (`TELEGRAM_BOT_TOKEN`) is shared with the `~/Code`
Orchestrator (`@Mantis1Bot`). A token can serve **either** a webhook **or** long-polling, and
only **one** poller at a time (a second poller gets HTTP 409). This bot polls and removes any
webhook on startup; the Orchestrator's `telegram_bot.py` was changed to `deleteWebhook` (not
`setWebhook`) so it no longer competes. Always make sure exactly one instance is running.

## Common commands

```bash
# One-time setup: venv + deps (uv not installed on this machine, so use a venv)
python3 -m venv venv
venv/bin/pip install pyTelegramBotAPI python-dotenv garminconnect anthropic \
  "pydantic-ai-slim[openai,anthropic]"

# Local text model server (mlx-vlm, separate python3.13 venv — mlx needs <=3.13).
# Model auto-downloads from HuggingFace on first run into ~/.cache/huggingface.
python3.13 -m venv mlx-venv
mlx-venv/bin/pip install mlx-vlm
mlx-venv/bin/python -m mlx_vlm.server --model mlx-community/gemma-4-12B-it-qat-4bit \
  --host 127.0.0.1 --port 8080      # foreground (or use the launchd daemon below)

# Run the Telegram bot (long-polling, foreground)
venv/bin/python telegram_bot.py

# Reboot persistence — this Mac is HEADLESS (SSH-only, no GUI login session), so use
# LaunchDaemons, NOT LaunchAgents. Agents only load at GUI login (gui/$(id -u) fails
# with error 125 over SSH and never auto-loads without a console session). Daemons load
# at BOOT via the system domain. They run as the user (UserName=sasimovva) with HOME/PATH
# set so the model cache, sops age key, git/ssh, and the venvs all resolve. Needs sudo:
sudo cp com.sasimovva.mlx-llm.daemon.plist       /Library/LaunchDaemons/com.sasimovva.mlx-llm.plist
sudo cp com.sasimovva.biometric-bot.daemon.plist /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
sudo chown root:wheel /Library/LaunchDaemons/com.sasimovva.mlx-llm.plist /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
sudo chmod 644        /Library/LaunchDaemons/com.sasimovva.mlx-llm.plist /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.sasimovva.mlx-llm.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
launchctl print system/com.sasimovva.biometric-bot | head   # verify (look for state = running)
# Stop / restart a service (sudo, system domain):
# sudo launchctl bootout   system/com.sasimovva.biometric-bot
# sudo launchctl kickstart -k system/com.sasimovva.biometric-bot   # after pulling new bot code
# Only ONE bot poller / ONE mlx server at a time — stop any manual instances first.
# (Ollama has been removed; com.sasimovva.ollama.* plists are legacy/unused.)

# Sync the last N days of Garmin workouts (needs GARMIN_EMAIL / GARMIN_PASSWORD)
venv/bin/python import_garmin.py --days 7

# Inspect the workout database
venv/bin/python query_workouts.py            # list all
venv/bin/python query_workouts.py --stats    # aggregate totals
venv/bin/python query_workouts.py --date 2026-06-04

# Verify shared secrets decrypt correctly
venv/bin/python secrets_loader.py
```

## LLM configuration (Pydantic AI; local text + cloud vision)

`llm_parser.py` is built on **Pydantic AI** — schemas (`RouteDecision`, `BiometricEntry`) are
typed Pydantic models; the framework generates the prompt, validates the output, and retries
on a bad parse (no hand-written JSON schema or regex). Output uses `PromptedOutput` mode so it
works on the local model (mlx-vlm doesn't do tool-calling). Routes by modality:

- **Text** (route / extract / answer) runs on a **local model served by mlx-vlm** — default
  `mlx-community/gemma-4-12B-it-qat-4bit` (`TEXT_LLM_MODEL`, endpoint `LOCAL_LLM_BASE`, default
  `http://127.0.0.1:8080/v1`). Each text role has a (local, Haiku) agent pair; on a local
  failure it **falls back to Claude Haiku**. Gemma 4 is multimodal (`gemma4_unified`), so it
  needs **mlx-vlm**, not mlx-lm. Latency: route ~3s, query ~9s, full-workout extraction ~30s.
- **Images** (Garmin screenshots) — `VISION_BACKEND` (default `"cloud"`) = **Claude Haiku 4.5**
  for best OCR accuracy; set `"local"` to use Gemma 4 via mlx-vlm (fully local) with a Haiku
  fallback on error. Cloud ~$0.004/image.

`ANTHROPIC_API_KEY` comes from the shared sops secrets via `secrets_loader`; agents are built
lazily (after the key is loaded). The **mlx-vlm server must be running** for local text. The
bot venv (3.14) holds `pydantic-ai-slim[openai,anthropic]`; mlx itself lives in the separate
`mlx-venv` (3.13). `prototype_pydantic_ai.py` is a standalone, domain-agnostic template of the
router → context → act pattern for reuse in other chat interactions.

### Text routing (router → load context → act)

Each free-text message gets ONE router call (`route()` in `llm_parser.py`) that picks an
action; a handler then loads that action's context and makes a second call:
- **log** → `parse_user_input` extracts metrics → saved to DB + dashboard.
- **history** → `answer_query` over the last ~15 workouts (`get_recent_workouts_context`).
- **plan** → `answer_query` over `knowledge/workout_routine.md` (training schedule).
- **protocol** → `answer_query` over `knowledge/protocol_baseline.md` (TRF/fasting/RS2/diet).

Add an action by extending `ROUTER_PROMPT`/`VALID_ACTIONS` and the `KNOWLEDGE_CONTEXT` map
in `telegram_bot.py`. Two LLM calls per message (route + act).

The `/start`, `/help`, `/stats`, and `/sync` commands work without any LLM call.

## When making changes

- Keep scripts self-contained; avoid cross-project imports beyond `secrets_loader.py`.
- New monthly data needs its dashboard file (`dashboards/YYYY-MM.md`) created first — the
  importers update existing rows, they don't create files.
- Don't commit `.env` or any decrypted secret. To add a shared key, edit the encrypted store
  in the monorepo: `sops ~/Code/secrets.enc.json`.
