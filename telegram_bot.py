#!/usr/bin/env python3
# /// script
# dependencies = [
#   "pyTelegramBotAPI",
#   "python-dotenv",
#   "garminconnect",
# ]
# ///
import os
import sys
import json
import base64
import subprocess
from datetime import datetime, date
import telebot
from llm_parser import parse_user_input, parse_image_input, classify_intent, answer_query
from import_garmin import update_workout_database, update_markdown_dashboard
from query_workouts import load_workouts

# Load local environment config if .env exists (fallback / overrides)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Reuse the shared, sops-encrypted API keys from the ~/Code monorepo
# (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, ...). Anything
# already set via the real environment or .env above takes precedence.
from secrets_loader import load_shared_secrets
load_shared_secrets()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

if not TOKEN:
    print("❌ Error: TELEGRAM_BOT_TOKEN environment variable not set.")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)

# Enforce security gate
def is_allowed_user(message):
    if not ALLOWED_USER_ID:
        return True
    return str(message.from_user.id) == str(ALLOWED_USER_ID)

# Dynamic metrics updater for dashboards
def update_metrics_in_dashboard(weight_lbs, trf_status, rs2_dose, act_date):
    year_month = act_date[:7]
    md_filename = f"{year_month}.md"
    md_path = os.path.join(os.path.dirname(__file__), 'dashboards', md_filename)
    if not os.path.exists(md_path):
        return False, f"Monthly dashboard {md_filename} not found."

    try:
        with open(md_path, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Failed to read dashboard: {e}"

    dt = datetime.strptime(act_date, "%Y-%m-%d")
    day_abbr = dt.strftime("%A")[:3]
    month_name = dt.strftime("%b")
    day_num = dt.day
    date_str = f"{month_name} {day_num}"

    updated = False
    new_lines = []
    changes_logged = []

    for line in lines:
        if f"**{day_abbr}**" in line and date_str in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 6:
                # Table columns: | Day | Date | Weight (lbs) | TRF Window | RS2 Dose | CGM | Movement |
                # Indices in split array:
                # parts[0]: empty (due to leading |)
                # parts[1]: Day (**Thu**)
                # parts[2]: Date (Jun 4)
                # parts[3]: Weight (lbs)
                # parts[4]: TRF Window
                # parts[5]: RS2 Dose
                
                if weight_lbs is not None:
                    parts[3] = str(weight_lbs)
                    changes_logged.append(f"Weight: {weight_lbs} lbs")
                if trf_status is not None:
                    parts[4] = str(trf_status)
                    changes_logged.append(f"TRF Window: {trf_status}")
                if rs2_dose is not None:
                    parts[5] = str(rs2_dose)
                    changes_logged.append(f"RS2 Dose: {rs2_dose}")

                # Reassemble the row
                new_line = " | ".join(parts[1:-1])
                line = f"| {new_line} |\n"
                updated = True
        new_lines.append(line)

    if updated:
        try:
            with open(md_path, 'w') as f:
                f.writelines(new_lines)
            return True, f"Updated June sheet: {', '.join(changes_logged)}"
        except Exception as e:
            return False, f"Failed to write dashboard updates: {e}"
    
    return False, f"Could not find table row for {date_str} in the dashboard."

# Dynamic stats calculator from workouts
def get_aggregate_stats():
    workouts_dir = os.path.join(os.path.dirname(__file__), 'knowledge', 'workouts')
    if not os.path.exists(workouts_dir):
        return "No workouts directory found."
    
    workouts = []
    import glob
    for filepath in glob.glob(os.path.join(workouts_dir, '*.json')):
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    workouts.extend(data)
        except Exception:
            pass
            
    if not workouts:
        return "No workouts logged in the database yet."
        
    workouts.sort(key=lambda w: w.get('date', ''), reverse=True)
    
    total_dist = sum(w.get('distance_miles', 0.0) for w in workouts)
    total_steps = sum(w.get('steps', 0) for w in workouts)
    total_cals = sum(w.get('calories_burned', 0) for w in workouts)
    total_duration_sec = 0
    for w in workouts:
        duration_str = w.get('duration', '00:00:00')
        try:
            h, m, s = map(int, duration_str.split(':'))
            total_duration_sec += h * 3600 + m * 60 + s
        except Exception:
            pass
            
    hours = total_duration_sec // 3600
    minutes = (total_duration_sec % 3600) // 60
    
    report = (
        f"📈 *AGGREGATE WORKOUT STATISTICS*\n"
        f"--------------------------------------\n"
        f"🏃 Sessions: {len(workouts)}\n"
        f"📏 Total Distance: {total_dist:.2f} miles\n"
        f"👣 Total Steps: {total_steps:,} steps\n"
        f"🔥 Total Calories: {total_cals:,} kcal\n"
        f"⏱️ Total Duration: {hours}h {minutes}m\n"
        f"--------------------------------------"
    )
    return report

# Recent workouts as a JSON context blob for natural-language questions
def get_recent_workouts_json(limit=20):
    try:
        workouts = load_workouts()  # already sorted most-recent-first
    except Exception:
        workouts = []
    return json.dumps(workouts[:limit], indent=2, default=str)

# Telegram Bot Handlers
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not is_allowed_user(message):
        bot.reply_to(message, "🔒 Access denied. This bot is locked to its authorized owner.")
        return
        
    help_text = (
        "🤖 *Precision Biometric Tracker Bot*\n\n"
        "Send me natural language updates or Garmin workout screenshots to log your data!\n\n"
        "*Commands:*\n"
        "/stats - Get your aggregate workout stats\n"
        "/sync - Commit & sync data with GitHub (pull + push)\n"
        "/help - Display this help manual\n\n"
        "*Log data (free-form):*\n"
        "• _'My weight is 185.2 today'_\n"
        "• _'Just completed 18h fast and took 20g potato starch'_\n"
        "• _'Completed 3.5 mile ruck with 30 lbs in 55 mins'_\n\n"
        "*Ask questions (free-form):*\n"
        "• _'Show me my recent run'_\n"
        "• _'How many total miles have I logged?'_\n"
        "• _'What was my longest hike?'_"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def send_stats(message):
    if not is_allowed_user(message):
        bot.reply_to(message, "🔒 Access denied.")
        return
    stats_msg = get_aggregate_stats()
    bot.reply_to(message, stats_msg, parse_mode="Markdown")

# Sync logged data with GitHub: commit local changes, pull, then push
def git_sync():
    repo_dir = os.path.dirname(os.path.abspath(__file__))

    def run(args):
        proc = subprocess.run(
            ["git", "-C", repo_dir] + args,
            capture_output=True, text=True, timeout=120,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    steps = []

    # Stage and commit anything new (dashboards, workouts, etc.)
    run(["add", "-A"])
    _, status = run(["status", "--porcelain"])
    if status:
        msg = f"Sync from Telegram bot {datetime.now():%Y-%m-%d %H:%M}"
        rc, out = run(["commit", "-m", msg])
        steps.append("📝 Committed local changes" if rc == 0 else f"⚠️ Commit: {out}")
    else:
        steps.append("✓ Nothing new to commit")

    # Pull (rebase, autostash) then push
    rc, out = run(["pull", "--rebase", "--autostash"])
    steps.append("⬇️ Pulled latest" if rc == 0 else f"❌ Pull failed:\n{out}")
    if rc == 0:
        rc, out = run(["push"])
        steps.append("⬆️ Pushed to GitHub" if rc == 0 else f"❌ Push failed:\n{out}")

    return "🔄 *Git Sync*\n" + "\n".join(f"• {s}" for s in steps)

@bot.message_handler(commands=['sync'])
def sync_repo(message):
    if not is_allowed_user(message):
        bot.reply_to(message, "🔒 Access denied.")
        return
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        result = git_sync()
    except Exception as e:
        result = f"❌ Sync error: {e}"
    bot.reply_to(message, result, parse_mode="Markdown")

# Handle free text messages
@bot.message_handler(content_types=['text'])
def handle_text_updates(message):
    if not is_allowed_user(message):
        bot.reply_to(message, "🔒 Access denied.")
        return

    bot.send_chat_action(message.chat.id, 'typing')
    text = message.text

    # Route by intent: is the user asking about their data, or logging new data?
    if classify_intent(text) == "query":
        answer = answer_query(text, get_recent_workouts_json())
        bot.reply_to(message, answer)
        return

    parsed = parse_user_input(text)
    if not parsed:
        bot.reply_to(message, "⚠️ Failed to parse details. Please check model status or endpoint availability.")
        return

    process_parsed_payload(message, parsed)

# Handle image updates
@bot.message_handler(content_types=['photo'])
def handle_image_updates(message):
    if not is_allowed_user(message):
        bot.reply_to(message, "🔒 Access denied.")
        return

    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Get the largest photo size
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Base64 encode the image
        img_b64 = base64.b64encode(downloaded_file).decode('utf-8')
        mime_type = "image/jpeg" # Telegram photos are jpegs
        
        caption = message.caption or ""
        parsed = parse_image_input(img_b64, mime_type, caption)
        
        if not parsed:
            bot.reply_to(message, "⚠️ Failed to parse screenshot metrics. Ensure the image is clear and the vision model is online.")
            return

        process_parsed_payload(message, parsed)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error processing image upload: {e}")

# Process and save structured metrics to workspace
def process_parsed_payload(message, parsed):
    act_date = date.today().isoformat()
    # If a workout date was extracted, default to it
    if parsed.get("workout") and parsed["workout"].get("date"):
        act_date = parsed["workout"]["date"]
        
    responses = []
    
    # 1. Update Weight, TRF, or RS2
    weight = parsed.get("weight_lbs")
    trf = parsed.get("trf_status")
    rs2 = parsed.get("rs2_dose")
    
    if weight is not None or trf is not None or rs2 is not None:
        ok, detail = update_metrics_in_dashboard(weight, trf, rs2, act_date)
        responses.append(f"📊 *Dashboard:* {detail}" if ok else f"⚠️ *Dashboard:* {detail}")

    # 2. Update Workout Database
    workout = parsed.get("workout")
    if workout:
        # Ensure date fields match standard format
        workout["date"] = act_date
        workout["day_of_week"] = datetime.strptime(act_date, "%Y-%m-%d").strftime("%A")
        
        try:
            update_workout_database(workout)
            update_markdown_dashboard(workout)
            responses.append(
                f"🏃 *Workout Logged:*\n"
                f"• *Activity:* {workout['activity_type']} ({workout.get('title', 'Session')})\n"
                f"• *Distance/Time:* {workout.get('distance_miles', 0.0)} mi / {workout.get('duration', '00:00:00')}\n"
                f"• *Metrics:* {workout.get('steps', 0):,} steps, {workout.get('avg_hr', 0)} bpm avg HR"
            )
        except Exception as e:
            responses.append(f"❌ *Workout Update Failed:* {e}")

    # 3. Final summary reply
    if not responses:
        responses.append("🤷 I processed your message but couldn't extract any new metrics matching the protocol baseline.")
        
    reply_text = "\n\n".join(responses)
    bot.reply_to(message, reply_text, parse_mode="Markdown")

if __name__ == "__main__":
    print(f"🤖 Starting Precision Tracker Bot (User Gate: {ALLOWED_USER_ID or 'DISABLED'})...")
    # The bot token is shared with the ~/Code Orchestrator, which uses a webhook.
    # A token can't serve a webhook and long-polling at once, so drop any existing
    # webhook before polling. (Orchestrator re-registers its webhook when it starts.)
    bot.remove_webhook()
    bot.infinity_polling()
