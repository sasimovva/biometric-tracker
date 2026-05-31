#!/usr/bin/env python3
import os
import sys
import json
import argparse
from datetime import datetime, date, timedelta

# Try to import garminconnect, explain how to install if missing
try:
    from garminconnect import Garmin
except ImportError:
    print("\n❌ Error: The 'garminconnect' package is not installed.")
    print("👉 Please install it by running: pip install garminconnect\n")
    sys.exit(1)

WORKOUTS_DIR = os.path.join(os.path.dirname(__file__), 'knowledge', 'workouts')
DASHBOARDS_DIR = os.path.join(os.path.dirname(__file__), 'dashboards')

def get_credentials():
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    
    if not email or not password:
        print("\n🔑 Garmin Connect Credentials Required")
        print("--------------------------------------")
        print("To run this script seamlessly, you can set the following environment variables:")
        print("  export GARMIN_EMAIL='your_email@example.com'")
        print("  export GARMIN_PASSWORD='your_password'")
        print("--------------------------------------")
        
        if not email:
            email = input("Email: ").strip()
        if not password:
            import getpass
            password = getpass.getpass("Password: ")
            
    if not email or not password:
        print("❌ Error: Email and password are required to login.")
        sys.exit(1)
        
    return email, password

def init_garmin_client(email, password):
    token_dir = os.path.expanduser("~/.garminconnect")
    os.makedirs(token_dir, exist_ok=True)
    
    print("🔄 Authenticating with Garmin Connect...")
    try:
        # Session tokens are cached to avoid repeated login attempts (which triggers MFA)
        client = Garmin(email, password)
        client.login(token_dir)
        print("✅ Successfully logged in and session loaded!")
        return client
    except Exception as e:
        print(f"❌ Authentication Failed: {e}")
        sys.exit(1)

def format_duration(seconds):
    if not seconds:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def calculate_pace(duration_sec, distance_meters):
    if not distance_meters or distance_meters == 0:
        return "N/A"
    miles = distance_meters / 1609.344
    total_minutes = duration_sec / 60
    pace_decimal = total_minutes / miles
    pace_minutes = int(pace_decimal)
    pace_seconds = int((pace_decimal - pace_minutes) * 60)
    return f"{pace_minutes}:{pace_seconds:02d}"

def parse_garmin_activity(activity):
    # Determine local date and day of week
    start_time_str = activity.get('startTimeLocal', '') # "YYYY-MM-DD HH:MM:SS.0"
    if not start_time_str:
        return None
        
    dt = datetime.strptime(start_time_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
    act_date = dt.date().isoformat()
    day_of_week = dt.strftime("%A")
    
    # Map Garmin activity type keys to standardized names
    raw_type = activity.get('activityType', {}).get('typeKey', 'other').lower()
    type_mapping = {
        'hiking': 'Hiking',
        'running': 'Running',
        'walking': 'Walking',
        'cycling': 'Indoor Cycling',
        'indoor_cycling': 'Indoor Cycling',
        'fitness_equipment': 'Versaclimber HIIT',
        'elliptical': 'Versaclimber HIIT'
    }
    activity_type = type_mapping.get(raw_type, raw_type.replace('_', ' ').title())
    
    distance_meters = activity.get('distance', 0.0)
    distance_miles = round(distance_meters / 1609.344, 2)
    duration_sec = activity.get('duration', 0.0)
    
    # Pace calculation
    avg_pace = calculate_pace(duration_sec, distance_meters)
    
    # Calories
    calories = int(activity.get('calories', 0))
    # Estimate active calories: Garmin returns activeCalories or similar, otherwise active = 0.8 * total
    active_cals = int(activity.get('activeCalories', calories * 0.8) or (calories * 0.8))
    resting_cals = calories - active_cals
    
    # HR
    avg_hr = int(activity.get('averageHR', activity.get('averageHeartRate', 0)) or 0)
    max_hr = int(activity.get('maxHR', activity.get('maxHeartRate', 0)) or 0)
    
    # Elevation in feet
    elev_gain_m = activity.get('elevationGain', activity.get('totalElevationGain', 0.0)) or 0.0
    elev_loss_m = activity.get('elevationLoss', activity.get('totalElevationLoss', 0.0)) or 0.0
    elev_gain_ft = int(round(elev_gain_m * 3.28084))
    elev_loss_ft = int(round(elev_loss_m * 3.28084))
    
    # Steps
    steps = int(activity.get('steps', 0) or 0)
    
    # Training Effect
    aerobic = round(activity.get('aerobicTrainingEffect', 0.0) or 0.0, 1)
    anaerobic = round(activity.get('anaerobicTrainingEffect', 0.0) or 0.0, 1)
    load = int(activity.get('activityTrainingLoad', 0.0) or 0.0)
    
    # Training Effect message/primary
    primary_te = activity.get('aerobicTrainingEffectMessage', 'Base (Low Aerobic)')
    if 'base' in primary_te.lower() or aerobic <= 3.0:
        primary_te = 'Base (Low Aerobic)'
    elif 'recovery' in primary_te.lower():
        primary_te = 'Recovery'
    else:
        primary_te = primary_te.split('_').pop().replace('Message', '').title()
        
    # Intensity Minutes
    mod_min = int(activity.get('moderateIntensityMinutes', 0) or 0)
    vig_min = int(activity.get('vigorousIntensityMinutes', 0) or 0)
    intensity_min = mod_min + (vig_min * 2)
    if intensity_min == 0 and duration_sec > 0:
        # Fallback if field not returned
        intensity_min = int(duration_sec // 60)
        
    # Body Battery Impact (Estimate from duration and HR, or default)
    # Typical body battery drop is -5 to -40 depending on duration and load
    bb_impact = -int(round(min(5 + (duration_sec / 3600) * 10, 40)))
    
    notes = f"Auto-imported from Garmin. {activity_type} workout session."
    if activity_type == "Hiking" and distance_miles > 8:
        notes = "Long morning hike. Maintained stable low-aerobic heart rate."
    elif activity_type == "Versaclimber HIIT":
        notes = "HIIT interval session. Highly efficient anaerobic training."
    elif activity_type == "Indoor Cycling":
        notes = "Steady-state aerobic indoor cycling session. Focused cardio effort."
        
    return {
        "date": act_date,
        "day_of_week": day_of_week,
        "activity_type": activity_type,
        "title": activity.get('activityName', f"{activity_type} Session"),
        "start_time": dt.strftime("%H:%M:%S"),
        "distance_miles": distance_miles,
        "duration": format_duration(duration_sec),
        "avg_pace": avg_pace,
        "calories_burned": calories,
        "active_calories": active_cals,
        "resting_calories": resting_cals,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "elevation_gain_ft": elev_gain_ft,
        "elevation_loss_ft": elev_loss_ft,
        "steps": steps,
        "training_effect": {
            "primary": primary_te,
            "aerobic": aerobic,
            "anaerobic": anaerobic,
            "load": load
        },
        "intensity_minutes": intensity_min,
        "body_battery_impact": bb_impact,
        "notes": notes
    }

def update_workout_database(workout):
    act_date = workout["date"]
    year_month = act_date[:7] # "YYYY-MM"
    json_filename = f"{year_month}.json"
    json_path = os.path.join(WORKOUTS_DIR, json_filename)
    
    os.makedirs(WORKOUTS_DIR, exist_ok=True)
    
    # Load existing data
    workouts = []
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                workouts = json.load(f)
        except Exception as e:
            print(f"⚠️ Warning: Failed to parse existing {json_filename}: {e}")
            
    # Check for duplicates (same date, start time, and activity type)
    duplicate = False
    for i, w in enumerate(workouts):
        if w["date"] == workout["date"] and w["start_time"] == workout["start_time"] and w["activity_type"] == workout["activity_type"]:
            # Update in-place
            workouts[i] = workout
            duplicate = True
            print(f"🔄 Updating existing workout entry on {act_date} @ {workout['start_time']} in database.")
            break
            
    if not duplicate:
        workouts.append(workout)
        print(f"💾 Added new workout entry on {act_date} @ {workout['start_time']} to database.")
        
    # Sort by date ascending to keep it clean
    workouts.sort(key=lambda w: (w["date"], w["start_time"]))
    
    try:
        with open(json_path, 'w') as f:
            json.dump(workouts, f, indent=2)
        print(f"✅ Saved updates to {json_path}")
    except Exception as e:
        print(f"❌ Failed to write to database: {e}")

def update_markdown_dashboard(workout):
    act_date = workout["date"]
    year_month = act_date[:7]
    md_filename = f"{year_month}.md"
    md_path = os.path.join(DASHBOARDS_DIR, md_filename)
    
    if not os.path.exists(md_path):
        print(f"⚠️ Monthly dashboard {md_filename} does not exist at {md_path}. Skipping markdown update.")
        return
        
    # Read dashboard lines
    try:
        with open(md_path, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"❌ Failed to read {md_filename}: {e}")
        return
        
    # Determine row representation
    day_abbr = workout["day_of_week"][:3] # "Mon", "Tue", etc.
    # Format date string in MD table, e.g., "May 29"
    dt = datetime.strptime(act_date, "%Y-%m-%d")
    month_name = dt.strftime("%b")
    day_num = dt.day
    date_str = f"{month_name} {day_num}"
    
    # Format Movement string
    if workout["distance_miles"] > 0:
        movement_str = f"{workout['distance_miles']:.2f}-Mile {workout['activity_type']}"
        if workout["steps"] > 0:
            movement_str += f" ({workout['steps']:,} steps)"
    else:
        movement_str = f"{workout['activity_type']}"
        if workout["duration"] != "00:00:00":
            h, m, s = map(int, workout["duration"].split(':'))
            total_mins = h * 60 + m
            movement_str += f" ({total_mins}m)"
            
    updated = False
    new_lines = []
    
    for line in lines:
        # Check if the line corresponds to this date
        # Table rows typically look like: | **Fri** | May 29 | ... |
        if f"**{day_abbr}**" in line and date_str in line:
            # We want to keep TRF, RS2, and CGM status as they are, but update the movement column (6th column, index 5 when splitting by |)
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 7:
                # Update the movement column
                parts[6] = movement_str
                # Re-assemble the line
                new_line = " | ".join(parts[1:-1])
                new_line = f"| {new_line} |\n"
                new_lines.append(new_line)
                updated = True
                print(f"📈 Updated dashboard row for {date_str} with: {movement_str}")
                continue
        new_lines.append(line)
        
    if updated:
        try:
            with open(md_path, 'w') as f:
                f.writelines(new_lines)
            print(f"✅ Dashboard {md_path} updated.")
        except Exception as e:
            print(f"❌ Failed to write to {md_filename}: {e}")
    else:
        print(f"⚠️ Could not find matching dashboard row for {date_str} to update.")

def main():
    parser = argparse.ArgumentParser(description="Sync your Garmin Connect workouts directly to Antigravity.")
    parser.add_argument('--days', type=int, default=1, help="Number of days to sync back (default: 1, which checks today)")
    args = parser.parse_args()
    
    email, password = get_credentials()
    client = init_garmin_client(email, password)
    
    # Calculate date range
    today = date.today()
    start_date = today - timedelta(days=args.days - 1)
    
    print(f"📅 Syncing activities from {start_date} to {today}...")
    try:
        activities = client.get_activities_by_date(start_date.isoformat(), today.isoformat())
    except Exception as e:
        print(f"❌ Failed to retrieve Garmin activities: {e}")
        sys.exit(1)
        
    if not activities:
        print("🤷 No activities found in this date range.")
        return
        
    print(f"🏃 Found {len(activities)} activity/activities.")
    for activity in reversed(activities): # Process oldest first
        name = activity.get('activityName', 'Workout')
        act_type = activity.get('activityType', {}).get('typeKey', 'other')
        print(f"\n⚡ Processing: '{name}' ({act_type}) on {activity.get('startTimeLocal')}")
        
        workout = parse_garmin_activity(activity)
        if workout:
            # Display summary
            print(f"  📏 Distance: {workout['distance_miles']} miles")
            print(f"  ⏱️  Duration: {workout['duration']}")
            print(f"  👣 Steps:    {workout['steps']:,}")
            print(f"  🫀 Avg HR:   {workout['avg_hr']} bpm")
            
            # Save and update dashboard
            update_workout_database(workout)
            update_markdown_dashboard(workout)
        else:
            print("  ⚠️ Skipped: Activity start time or essential details missing.")
            
    print("\n🏁 Garmin Sync Complete!")

if __name__ == '__main__':
    main()
