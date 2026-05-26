#!/usr/bin/env python3
import json
import os
import argparse

WORKOUTS_DIR = os.path.join(os.path.dirname(__file__), 'knowledge', 'workouts')

def load_workouts():
    if not os.path.exists(WORKOUTS_DIR):
        print(f"Error: Workouts directory not found at {WORKOUTS_DIR}")
        return []
    workouts = []
    import glob
    for filepath in glob.glob(os.path.join(WORKOUTS_DIR, '*.json')):
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    workouts.extend(data)
        except Exception as e:
            print(f"Warning: Failed to load {filepath}: {e}")
    # Sort workouts by date descending
    workouts.sort(key=lambda w: w.get('date', ''), reverse=True)
    return workouts

def display_workout(w):
    print(f"\n==================================================")
    print(f"🏃  {w['title']} ({w['activity_type']})")
    print(f"📅  {w['day_of_week']}, {w['date']} @ {w['start_time']}")
    print(f"==================================================")
    print(f"📏  Distance:            {w['distance_miles']} miles")
    print(f"⏱️  Duration:            {w['duration']}")
    print(f"⚡  Avg Pace:            {w['avg_pace']} /mi")
    print(f"👣  Total Steps:         {w['steps']:,}")
    print(f"🔥  Total Energy:        {w['calories_burned']} kcal (Active: {w['active_calories']} kcal)")
    print(f"🫀  Avg Heart Rate:      {w['avg_hr']} bpm (Max: {w['max_hr']} bpm)")
    print(f"🏔️  Elevation Gain/Loss: +{w['elevation_gain_ft']} ft / -{w['elevation_loss_ft']} ft")
    print(f"⚡  Intensity Minutes:    {w['intensity_minutes']} mins")
    print(f"📈  Training Effect:     {w['training_effect']['primary']}")
    print(f"   └─ Aerobic:           {w['training_effect']['aerobic']}")
    print(f"   └─ Anaerobic:         {w['training_effect']['anaerobic']}")
    print(f"   └─ Exercise Load:     {w['training_effect']['load']}")
    print(f"🔋  Body Battery Impact: {w['body_battery_impact']}")
    print(f"==================================================\n")

def main():
    parser = argparse.ArgumentParser(description="Query structured workout history.")
    parser.add_argument('--date', type=str, help="Specific date to query (YYYY-MM-DD)")
    parser.add_argument('--stats', action='store_true', help="Calculate summary statistics")
    args = parser.parse_args()

    workouts = load_workouts()
    if not workouts:
        return

    if args.date:
        filtered = [w for w in workouts if w['date'] == args.date]
        if not filtered:
            print(f"No workouts found on {args.date}")
        for w in filtered:
            display_workout(w)
    elif args.stats:
        total_dist = sum(w['distance_miles'] for w in workouts)
        total_steps = sum(w['steps'] for w in workouts)
        total_cals = sum(w['calories_burned'] for w in workouts)
        total_duration_sec = 0
        for w in workouts:
            h, m, s = map(int, w['duration'].split(':'))
            total_duration_sec += h * 3600 + m * 60 + s
        
        hours = total_duration_sec // 3600
        minutes = (total_duration_sec % 3600) // 60
        seconds = total_duration_sec % 60
        
        print("\n==================================================")
        print("📈  AGGREGATE WORKOUT STATISTICS")
        print("==================================================")
        print(f"Sessions:         {len(workouts)}")
        print(f"Total Distance:   {total_dist:.2f} miles")
        print(f"Total Steps:      {total_steps:,} steps")
        print(f"Total Calories:   {total_cals:,} kcal")
        print(f"Total Duration:   {hours}h {minutes}m {seconds}s")
        print("==================================================\n")
    else:
        # Default: list all workouts
        for w in workouts:
            display_workout(w)

if __name__ == '__main__':
    main()
