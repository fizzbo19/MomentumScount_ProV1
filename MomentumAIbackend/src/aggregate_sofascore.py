import pandas as pd
import json
import os
import glob
import math

# --- Configuration ---
# Set this to the folder where your JSON match stats files were dumped
# IMPORTANT: You must ensure this folder exists and contains your JSON files.
JSON_INPUT_DIR = './National League/' 
CSV_OUTPUT_FILE = 'sofascore_player_metrics.csv'
TOTAL_MINUTES_IN_MATCH = 90.0

# --- Helper Functions ---
def calculate_per_90(value, minutes):
    """Calculates a normalized stat value per 90 minutes."""
    minutes = safe_float(minutes)
    if minutes <= 0:
        return 0.0
    return (safe_float(value) / minutes) * TOTAL_MINUTES_IN_MATCH

def safe_float(value):
    """Safely converts input to float, handling None or errors."""
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return 0.0
        return float(value)
    except:
        return 0.0

def get_stat_value(stat_list, stat_name):
    """Helper to safely retrieve a value from a nested list of SofaScore stats."""
    if not stat_list:
        return 0
    # SofaScore stats are usually structured as a list of dictionaries with 'name' and 'value' keys
    for stat in stat_list:
        if stat.get('name') == stat_name:
            return safe_float(stat.get('value', 0))
    return 0


def aggregate_player_stats():
    all_player_data = []
    
    # 1. Loop through all JSON files created by the scraper
    for filename in glob.glob(os.path.join(JSON_INPUT_DIR, '*.json')):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                match_data = json.load(f)
        except Exception as e:
            print(f"Skipping file {filename}: {e}")
            continue

        # CRITICAL JSON EXTRACTION: SofaScore match stats usually separate players by team.
        # We assume the scraper dumped the full Match Statistics JSON which contains detailed player data.
        
        # NOTE: The exact keys here ('homePlayers', 'awayPlayers') are common placeholders. 
        # You may need to inspect one of your JSON files and adjust these keys!
        
        home_players = match_data.get('home', {}).get('players', [])
        away_players = match_data.get('away', {}).get('players', [])
        
        # Combine all players from the match into one list
        all_players_in_match = home_players + away_players

        for player in all_players_in_match:
            # Check if player played minutes and has stats
            minutes = safe_float(player.get('minutes', 0))
            if minutes == 0:
                continue

            stats = player.get('statistics', {})
            
            # --- Extract Desired Metrics ---
            # NOTE: The keys in the dictionary below MUST match the headers in your final CSV!
            
            # Extract basic attributes (Goals, Assists)
            goals = safe_float(player.get('goals', 0))
            assists = safe_float(player.get('goalAssists', 0))
            
            # Extract detailed performance metrics (These are often nested/named differently in JSON)
            # This is where you would call a function like get_stat_value if the stats were nested.
            
            # Since we don't have the exact JSON structure, we will use placeholders for calculation:
            
            # FINAL PLAYER DATA ROW FOR AGGREGATION
            all_player_data.append({
                'Name': player.get('name', 'UNKNOWN'), 
                'Total_Minutes': minutes,
                'Total_Goals': goals,
                'Total_Assists': assists,
                'Total_Tackles': safe_float(stats.get('tackles', 0)),
                'Total_Interceptions': safe_float(stats.get('interceptions', 0)),
                # You would add all your other 'Total_' metrics here
            })

    if not all_player_data:
        print("No player data was extracted. Check your JSON structure.")
        return
        
    # 2. Convert to DataFrame and Aggregate (Sum the TOTALS)
    df_raw = pd.DataFrame(all_player_data)
    
    # Group by player Name and sum all the total stats
    df_aggregate = df_raw.groupby('Name').agg(
        Total_Minutes=('Total_Minutes', 'sum'),
        Total_Goals=('Total_Goals', 'sum'),
        Total_Assists=('Total_Assists', 'sum'),
        Total_Tackles=('Total_Tackles', 'sum'),
        Total_Interceptions=('Total_Interceptions', 'sum'),
        # Add all your other 'Total_' metrics here
    ).reset_index()

    # 3. Calculate Final Per-90 Metrics (Normalize)
    # This loop iterates through the calculated totals and creates the final p90 metrics
    final_metrics = []
    for index, row in df_aggregate.iterrows():
        mins = row['Total_Minutes']
        if mins == 0: continue
        
        final_metrics.append({
            'Name': row['Name'], 
            # --- These are the Final Columns for your sofascore_player_metrics.csv ---
            'xG_per_90': calculate_per_90(row.get('Total_xG', 0), mins), 
            'tackles_success_p90': calculate_per_90(row['Total_Tackles'], mins),
            'Goals': safe_float(row['Total_Goals']),
            'Assists': safe_float(row['Total_Assists']),
            'Total_Minutes': mins,
            'Datasource': 'SofaScore'
            # Add all your other final p90 metrics here
        })

    # 4. Save to CSV
    df_final = pd.DataFrame(final_metrics)
    df_final.to_csv(CSV_OUTPUT_FILE, index=False)
    
    print(f"\n✅ Aggregation complete. Saved to {CSV_OUTPUT_FILE}")
    print(f"Data Head:\n{df_final.head()}")
    print(f"Total Unique Players: {len(df_final)}")


 #--- Execution ---
#AGGREGATE THIS SCRIPT LOCALLY TO CREATE THE DATA FILE.
aggregate_player_stats()