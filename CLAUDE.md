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
venv/bin/pip install pyTelegramBotAPI python-dotenv garminconnect anthropic

# Local text model (Ollama). Pull once; run the server.
ollama pull qwen2.5:14b
ollama serve            # foreground (or use the launchd agent below for persistence)

# Run the Telegram bot (long-polling, foreground)
venv/bin/python telegram_bot.py

# Reboot persistence — this Mac is HEADLESS (SSH-only, no GUI login session), so use
# LaunchDaemons, NOT LaunchAgents. Agents only load at GUI login (gui/$(id -u) fails
# with error 125 over SSH and never auto-loads without a console session). Daemons load
# at BOOT via the system domain. They run as the user (UserName=sasimovva) with HOME/PATH
# set so ollama models, the sops age key, git/ssh, and the venv all resolve. Needs sudo:
sudo cp com.sasimovva.ollama.daemon.plist        /Library/LaunchDaemons/com.sasimovva.ollama.plist
sudo cp com.sasimovva.biometric-bot.daemon.plist /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
sudo chown root:wheel /Library/LaunchDaemons/com.sasimovva.ollama.plist /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
sudo chmod 644        /Library/LaunchDaemons/com.sasimovva.ollama.plist /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.sasimovva.ollama.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.sasimovva.biometric-bot.plist
launchctl print system/com.sasimovva.biometric-bot | head   # verify (look for state = running)
# Stop / restart a service (sudo, system domain):
# sudo launchctl bootout   system/com.sasimovva.biometric-bot
# sudo launchctl kickstart -k system/com.sasimovva.biometric-bot
# Only ONE bot poller / ONE ollama at a time — stop any manual instances first.
# (com.sasimovva.*.plist without ".daemon" are the GUI-Mac LaunchAgent variants — unused here.)

# Sync the last N days of Garmin workouts (needs GARMIN_EMAIL / GARMIN_PASSWORD)
venv/bin/python import_garmin.py --days 7

# Inspect the workout database
venv/bin/python query_workouts.py            # list all
venv/bin/python query_workouts.py --stats    # aggregate totals
venv/bin/python query_workouts.py --date 2026-06-04

# Verify shared secrets decrypt correctly
venv/bin/python secrets_loader.py
```

## LLM configuration (hybrid: local text + cloud vision)

`llm_parser.py` routes by modality:

- **Text** (intent classification, data extraction, question answering) runs on a **local
  model via Ollama** — default `qwen2.5:14b` (`TEXT_LLM_MODEL`, endpoint `OLLAMA_BASE`,
  default `http://localhost:11434/v1`). Free, private, fast on the M4 Mac mini. If Ollama is
  unreachable it **falls back to Claude Haiku** automatically.
- **Images** (Garmin screenshots) always use the **Anthropic SDK** with **Claude Haiku 4.5**
  (`claude-haiku-4-5`, override via `LLM_MODEL`) — vision needs a vision model. ~$0.004/image.

`ANTHROPIC_API_KEY` comes from the shared sops secrets via `secrets_loader`; the Anthropic
client is built lazily. **Ollama must be running** for local text (`ollama serve`, or
`brew services start ollama` from a normal terminal for reboot persistence). Pull the model
once with `ollama pull qwen2.5:14b`.

### Text intent routing

Free-text messages are first classified by the local model as **log** vs **query**
(`classify_intent`):
- **log** → `parse_user_input` extracts metrics → saved to DB + dashboard.
- **query** → `answer_query` answers in natural language using the last ~20 workouts as
  context (e.g. "show me my recent run", "how many miles have I logged?").

The `/start`, `/help`, `/stats`, and `/sync` commands work without any LLM call.

## When making changes

- Keep scripts self-contained; avoid cross-project imports beyond `secrets_loader.py`.
- New monthly data needs its dashboard file (`dashboards/YYYY-MM.md`) created first — the
  importers update existing rows, they don't create files.
- Don't commit `.env` or any decrypted secret. To add a shared key, edit the encrypted store
  in the monorepo: `sops ~/Code/secrets.enc.json`.
