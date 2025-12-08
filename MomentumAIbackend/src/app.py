"""
MomentumScout Backend V1 (Final Production-Ready Core)
- Club/Agent: Uses FC26 Data.
- Baller League: Uses Multi-Tab Excel (Attack/Defense/etc).
- AI FEATURES: Squad Gap, Budget Target, Dynamic Training, Next Match, Heatmaps, and Comparison logic.
"""
import os
import math
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from io import StringIO # Required for CSV parsing

app = Flask(__name__, static_folder="public")

# Frontend origin for CORS
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://momentum-ai-io.netlify.app") 
ALLOWED_ORIGINS = [
    FRONTEND_URL,
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:5000", "http://127.0.0.1:5000"
]

CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}}, supports_credentials=True)

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# --- CONFIGURATION ---
DATA_FOLDER_PATH = os.environ.get("DATA_FOLDER_PATH", "data")
DATA_FILENAME_BASE = os.environ.get("DATA_FILENAME_BASE", "FC26_MomentumScout.csv")
DATA_FILENAME_BALLER = os.environ.get("DATA_FILENAME_BALLER", "baller_league_uk.xlsx") 
# NEW: Configuration for the Next Match data file
DATA_FILENAME_NEXT_MATCH = os.environ.get("DATA_FILENAME_NEXT_MATCH", "baller_next_match.xlsx")

# Global variables
player_data_base = None 
player_data_baller = None 
next_match_data = None # Stores the loaded next match info

# --- AI DRILL DATABASE ---
DRILL_DATABASE = {
    'pace': 'Speed ladders and resistance sprint training.',
    'shooting': '1v1 finishing drills and shot placement practice.',
    'passing': 'Rondo drills (5v2) and long-range switch play.',
    'dribbling': 'Cone weaving and close-control box drills.',
    'defending': 'Shadow defending and timing interception drills.',
    'physic': 'Core strength conditioning and shielding practice.',
    'goals': 'Finishing under pressure and rebound anticipation.',
    'assists': 'Vision training and final-third crossing drills.',
    'tackles': '1v1 defensive duels and slide tackle timing.',
    'saves': 'Reaction reflex training and positioning drills.',
    'mentality_vision': 'Video analysis of passing lanes and scanning drills.',
    'defending_standing_tackle': 'Jockeying and block tackle technique.'
}

# --- POSITION WEIGHTS ---
POSITION_WEIGHTS = {
    'GK': {'goalkeeping_diving': 20,'goalkeeping_handling': 20,'goalkeeping_kicking': 20,'goalkeeping_positioning': 20,'goalkeeping_reflexes': 20},
    'CB': {'defending_standing_tackle': 30, 'defending_marking_awareness': 20, 'power_strength': 15, 'mentality_interceptions': 15, 'pace': 10},
    'LB': {'pace': 35, 'defending_standing_tackle': 20, 'attacking_crossing': 15, 'power_stamina': 15, 'dribbling': 15},
    'RB': {'pace': 35, 'defending_standing_tackle': 20, 'attacking_crossing': 15, 'power_stamina': 15, 'dribbling': 15},
    'CDM': {'mentality_interceptions': 25, 'defending_standing_tackle': 20, 'power_strength': 15, 'passing': 15},
    'CM': {'passing': 25, 'dribbling': 20, 'mentality_vision': 20, 'power_stamina': 15, 'shooting': 10},
    'CAM': {'mentality_vision': 25, 'passing': 25, 'dribbling': 20, 'shooting': 15, 'pace': 10},
    'LW': {'pace': 30, 'dribbling': 25, 'shooting': 20, 'attacking_crossing': 15},
    'RW': {'pace': 30, 'dribbling': 25, 'shooting': 20, 'attacking_crossing': 15},
    'ST': {'attacking_finishing': 30, 'mentality_positioning': 25, 'power_shot_power': 15, 'pace': 15, 'power_strength': 10},
    'CF': {'attacking_finishing': 25, 'mentality_vision': 20, 'dribbling': 20, 'passing': 15, 'pace': 10}
}
BALLER_WEIGHTS = {
    'ALL': {'goals': 10, 'assists': 10, 'tackles': 10, 'total_saves': 10},
    'FWD': {'goals': 30, 'total_shots': 20, 'xg_per_90': 20},
    'MID': {'assists': 30, 'pass_accuracy': 20, 'interceptions': 15},
    'DEF': {'tackles': 30, 'clearances': 20, 'interceptions': 25},
    'GK':  {'total_saves': 30, 'clean_sheets': 30}
}

# --- HELPERS ---
def safe_int(val, default=0):
    try: return int(float(val)) if pd.notnull(val) else default
    except: return default

def safe_float(val, default=0.0):
    try: return float(val) if pd.notnull(val) else default
    except: return default

def clean_column_name(col_name):
    return str(col_name).strip().lower().replace(' ', '_').replace('.', '').replace('%', '_pct')

# --- AI GENERATORS ---
def generate_training_plan(row, position, is_baller=False):
    """Analyzes player stats against their position requirements to suggest drills."""
    plan = {
        "weakness": "General Conditioning",
        "drills": ["Standard fitness regime", "Tactical positioning review"]
    }
    
    if is_baller:
        # Simple mapping for Baller League positions
        pos_key = 'ALL'
        if position in BALLER_WEIGHTS: pos_key = position
        weights = BALLER_WEIGHTS.get(pos_key, BALLER_WEIGHTS['ALL'])
    else:
        weights = POSITION_WEIGHTS.get(position, POSITION_WEIGHTS.get('CM', {}))
        
    if not weights: return plan
    
    lowest_attr = None
    lowest_val = 1000
    
    for attr in weights.keys():
        val = safe_float(row.get(attr, 0))
        # Normalize Baller stats (0-20 scale) to 0-100 for comparison
        if is_baller and val < 20: val = val * 5 
        
        if val < lowest_val:
            lowest_val = val
            lowest_attr = attr
            
    if lowest_attr:
        drill = DRILL_DATABASE.get(lowest_attr, DRILL_DATABASE.get(lowest_attr.split('_')[-1], "General technical drills."))
        readable_attr = lowest_attr.replace('_', ' ').title()
        plan['weakness'] = f"Improve {readable_attr} (Current: {int(lowest_val)})"
        plan['drills'] = [ f"Primary: {drill}", f"Secondary: High-intensity {readable_attr} simulations." ]
        
    return plan

def generate_heatmap_data(row, position):
    """
    Generates synthetic heatmap zone data (0-100 intensity) based on position & attributes.
    Zones: 'box' (Penalty Area), 'wide' (Wings), 'mid' (Central Midfield), 'def' (Defensive Third)
    """
    zones = {'box': 10, 'wide': 10, 'mid': 10, 'def': 10}
    
    # Base intensity by position
    if position in ['ST', 'CF', 'FWD']:
        zones.update({'box': 90, 'wide': 40, 'mid': 30, 'def': 5})
    elif position in ['RW', 'LW']:
        zones.update({'box': 60, 'wide': 95, 'mid': 40, 'def': 20})
    elif position in ['CAM', 'CM', 'MID']:
        zones.update({'box': 40, 'wide': 30, 'mid': 95, 'def': 40})
    elif position in ['CDM']:
        zones.update({'box': 15, 'wide': 20, 'mid': 80, 'def': 80})
    elif position in ['CB', 'DEF']:
        zones.update({'box': 5, 'wide': 10, 'mid': 30, 'def': 95})
    elif position in ['LB', 'RB']:
        zones.update({'box': 10, 'wide': 85, 'mid': 50, 'def': 80})
    elif position == 'GK':
        zones.update({'box': 100, 'wide': 0, 'mid': 0, 'def': 100}) # GK stays home

    # Attribute Modifiers (If a defender has high shooting, bump box threat)
    shooting = safe_float(row.get('shooting', 0))
    if shooting > 80: zones['box'] += 10
    
    pace = safe_float(row.get('pace', 0))
    if pace > 85: zones['wide'] += 10 # Fast players tend to drift wide
    
    return {k: min(100, v) for k,v in zones.items()}

def compute_score_for_player(row, position="CM", user_weights=None, is_baller=False):
    if is_baller:
        weights = BALLER_WEIGHTS.get(position, BALLER_WEIGHTS['ALL']).copy()
        if user_weights: weights.update(user_weights)
        score, total_w = 0.0, sum(weights.values()) or 1
        for attr, weight in weights.items():
            val = safe_float(row.get(attr), 0.0)
            norm_val = min(100, val * 5) if 'pct' not in attr and 'accuracy' not in attr else val
            score += (norm_val / 100.0) * (weight / total_w)
        return round(score * 100, 2)
    
    base_weights = POSITION_WEIGHTS.get(position, POSITION_WEIGHTS.get('CM', {})).copy()
    if user_weights: base_weights.update(user_weights)
    total_w = sum(base_weights.values()) or 1
    score = 0.0
    for attr, weight in base_weights.items():
        val = safe_float(row.get(attr), 0.0)
        score += (val / 100.0) * (weight / total_w)
    return round(score * 100, 2)

def years_to_project(age: int) -> int:
    if age <= 20: return 5
    if 21 <= age <= 25: return 4
    if 26 <= age <= 30: return 3
    return 2

def project_player(row, years=3):
    ovr = int(row.get('overall', 0) or 0)
    pot = int(row.get('potential', 0) or ovr)
    age = int(row.get('age', 0) or 0)
    value = float(row.get('value_eur', 0) or 0)
    projections = []
    for y in range(1, years + 1):
        growth = (pot - ovr) / max(1, years) if pot > ovr else 0
        ovr += growth
        val_growth = 0.10 if age < 23 else 0.05 if age < 28 else -0.05
        value = value * (1 + val_growth)
        projections.append({"year": y, "projected_value_eur": int(value), "projected_overall": int(ovr)})
    return projections

def negotiation_range(current_value: int, projected_value: int):
    if current_value <= 0: return {"min_offer": 0, "max_offer": 0}
    return { "min_offer": int(current_value * 0.8), "max_offer": int(max(current_value, projected_value) * 1.1) }

# --- DATA LOADERS ---
def _load_baller_league_data(filename):
    """Specific loader for Multi-Tab Excel file."""
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp): 
        print(f"Baller file not found: {filename}")
        return pd.DataFrame()
    try:
        sheets_dict = pd.read_excel(fp, sheet_name=None)
        merged_df = None
        for sheet_name, df in sheets_dict.items():
            df.columns = [clean_column_name(c) for c in df.columns]
            merge_key = 'id' if 'id' in df.columns else 'name'
            if merge_key not in df.columns: continue
            if merged_df is None: merged_df = df
            else:
                cols = df.columns.difference(merged_df.columns).tolist()
                cols.append(merge_key)
                merged_df = pd.merge(merged_df, df[cols], on=merge_key, how='outer')
        
        if merged_df is not None:
            merged_df = merged_df.fillna(0)
            merged_df['short_name'] = merged_df.get('name', 'Unknown')
            merged_df['club_position'] = 'Baller'
            if 'momentum_score' not in merged_df.columns:
                merged_df['momentum_score'] = (safe_float(merged_df.get('goals', 0))*5 + safe_float(merged_df.get('assists', 0))*3)
            merged_df['overall'] = 50 + (merged_df['momentum_score'].clip(upper=50)).astype(int)
            merged_df['potential'] = merged_df['overall'] + 5
            merged_df['value_eur'] = merged_df['momentum_score'] * 10000
            return merged_df
        return pd.DataFrame()
    except Exception as e:
        print(f"Error reading Baller Excel: {e}")
        return pd.DataFrame()

def _load_next_match_data(filename):
    """Loads the Next Match analysis data from Excel."""
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp):
        print(f"Next match file not found at {fp}. Using mock data.")
        return None
    try:
        df = pd.read_excel(fp)
        df.columns = [clean_column_name(c) for c in df.columns]
        if not df.empty:
            # Convert first row to dictionary for JSON response
            return df.iloc[0].to_dict()
        return None
    except Exception as e:
        print(f"Error reading Next Match Excel: {e}")
        return None

def _load_fc26_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp): return pd.DataFrame()
    try:
        if filename.endswith('.csv'): df = pd.read_csv(fp, encoding="utf-8-sig")
        else: df = pd.read_excel(fp) 
    except: return pd.DataFrame()
    
    df.columns = [clean_column_name(c) for c in df.columns]
    
    NUMERIC_COLS = ['overall','potential','age','value_eur','pace','shooting','passing','dribbling','defending','physic','wage_eur', 'contract_valid_until',
                    'defending_standing_tackle', 'mentality_interceptions', 'power_strength', 'movement_sprint_speed']
    
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
    if "sofifa_id" in df.columns:
        df["player_face_url"] = df["sofifa_id"].apply(lambda x: f"https://cdn.sofifa.net/players/{int(x)//1000:03d}/{int(x)%1000:03d}/24.webp" if pd.notna(x) else "")
    return df

def initialize_app():
    global player_data_base, player_data_baller, next_match_data
    
    print("--- Loading Data ---")
    player_data_base = _load_fc26_data(DATA_FILENAME_BASE)
    print(f"✅ Club/Agent Data Ready: {len(player_data_base)} players.")
    
    player_data_baller = _load_baller_league_data(DATA_FILENAME_BALLER)
    print(f"✅ Baller League Data Ready: {len(player_data_baller)} players.")
    
    next_match_data = _load_next_match_data(DATA_FILENAME_NEXT_MATCH)
    if next_match_data:
        print("✅ Next Match Data Loaded.")

# --- ROUTES ---

@app.route("/api/find_players", methods=["POST", "OPTIONS"])
def api_find_players():
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        data_source = payload.get("data_source", "base")
        df = player_data_baller if data_source == 'baller' else player_data_base
        if df is None or df.empty: return jsonify({"players": []})

        filters = payload.get("filters", {})
        for key, rng in filters.items():
            col = clean_column_name(key)
            if 'value' in col: col = 'value_eur'
            if col in df.columns and isinstance(rng, list) and len(rng) >= 2:
                 df = df[(df[col] >= float(rng[0])) & (df[col] <= float(rng[1]))]

        position = payload.get("position", "ALL")
        user_weights = payload.get("weights", {})
        is_baller = (data_source == 'baller')
        
        df = df.copy()
        df['momentum_score'] = df.apply(lambda row: compute_score_for_player(row, position, user_weights, is_baller), axis=1)
        
        sorted_df = df.sort_values(by='momentum_score', ascending=False).head(20)
        
        out = []
        for _, row in sorted_df.iterrows():
            age = safe_int(row.get("age"), 21)
            years = years_to_project(age)
            projections = project_player(row, years)
            val = safe_int(row.get("value_eur"), 0)
            neg = negotiation_range(val, projections[-1]['projected_value_eur'])
            
            # Generate Training Plan & Heatmap
            training_plan = generate_training_plan(row, row.get('club_position', 'CM'), is_baller)
            heatmap = generate_heatmap_data(row, row.get('club_position', 'CM'))
            
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k,v in p_dict.items()}
            
            out.append({
                "short_name": p_dict.get('short_name', 'Unknown'),
                "club_position": p_dict.get('club_position', 'Player'),
                "momentum_score": safe_float(p_dict.get('momentum_score')),
                "value_eur": val,
                "player_face_url": p_dict.get('player_face_url', ''),
                "projections": projections,
                "negotiation": neg,
                "ai_training": training_plan, 
                "heatmap_zones": heatmap, # NEW Heatmap Data
                "full_attributes": p_dict
            })
        return jsonify({"players": out})

    except Exception as e:
        print(f"Error in find: {e}")
        return jsonify({"players": [], "error": str(e)}), 500

@app.route("/api/search_player", methods=["POST", "OPTIONS"])
def api_search_player():
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        query = str(payload.get("player_name", "")).lower().strip()
        data_source = payload.get("data_source", "base")
        is_baller = (data_source == 'baller')
        
        df = player_data_baller if is_baller else player_data_base
        if df is None or df.empty or not query: return jsonify([])
        
        mask = df['short_name'].astype(str).str.lower().str.contains(query) | df['name'].astype(str).str.lower().str.contains(query)
        results = df[mask].head(10)
        
        out = []
        for _, row in results.iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k,v in p_dict.items()}
            score = compute_score_for_player(row, row.get('club_position','CM'), None, is_baller)
            age = safe_int(row.get("age"), 21)
            projections = project_player(row, years_to_project(age))
            
            # AI Logic
            training_plan = generate_training_plan(row, row.get('club_position', 'CM'), is_baller)
            heatmap = generate_heatmap_data(row, row.get('club_position', 'CM'))
            
            out.append({
                "short_name": p_dict.get('short_name'),
                "club_position": p_dict.get('club_position'),
                "momentum_score": score,
                "projections": projections,
                "ai_training": training_plan, 
                "heatmap_zones": heatmap, # NEW Heatmap Data
                "value_eur": safe_int(p_dict.get('value_eur')),
                "full_attributes": p_dict
            })
        return jsonify(out)
    except Exception as e:
        return jsonify([]), 500

@app.route("/api/squad_gap_analysis", methods=["POST", "OPTIONS"])
def api_squad_gap_analysis():
    """ACTIVATED: Analyzes uploaded squad CSV against 'League Average'."""
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        squad_csv_text = payload.get("csv_data", "")
        target_pos = payload.get("position", "ALL")
        
        if not squad_csv_text: 
            return jsonify({"suggestions": ["No squad data uploaded."]})

        squad_df = pd.read_csv(StringIO(squad_csv_text))
        squad_df.columns = [clean_column_name(c) for c in squad_df.columns]
        
        suggestions = []
        suggestions.append(f"Squad Size Analysis: {len(squad_df)} players loaded.")
        
        if target_pos == "ALL": positions_to_check = ['CB', 'CM', 'ST'] 
        else: positions_to_check = [target_pos]
            
        for pos in positions_to_check:
            key_attrs = POSITION_WEIGHTS.get(pos, {}).keys()
            squad_pos_df = squad_df[squad_df['club_position'] == pos] if 'club_position' in squad_df.columns else pd.DataFrame()
            league_pos_df = player_data_base[player_data_base['club_position'] == pos]
            
            if squad_pos_df.empty:
                suggestions.append(f"⚠️ CRITICAL GAP: No players found for position {pos}.")
            else:
                for attr in list(key_attrs)[:3]:
                    if attr in squad_pos_df.columns and attr in league_pos_df.columns:
                        squad_avg = squad_pos_df[attr].mean()
                        league_avg = league_pos_df[attr].mean()
                        if squad_avg < (league_avg - 5):
                            suggestions.append(f"📉 WEAKNESS ({pos}): avg {attr} is {squad_avg:.1f} (League avg: {league_avg:.1f}).")

        return jsonify({"suggestions": suggestions})
    except Exception as e:
        return jsonify({"suggestions": ["Error analyzing squad data."]}), 500

@app.route("/api/budget_target", methods=["POST", "OPTIONS"])
def api_budget_target():
    """ACTIVATED: Recommends players based on wage budget and contract expiry."""
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        max_wage = safe_int(payload.get("max_wage"), 500000)
        contract_year = safe_int(payload.get("contract_year"), 2026)
        
        df = player_data_base.copy()
        df = df[ (df['wage_eur'] * 52) <= max_wage ]
        if 'contract_valid_until' in df.columns:
            df = df[df['contract_valid_until'] <= contract_year]
            
        targets = df.sort_values(by='overall', ascending=False).head(10)
        out = []
        for _, row in targets.iterrows():
            out.append({
                "short_name": row['short_name'],
                "club_position": row['club_position'],
                "overall": row['overall'],
                "value_eur": row['value_eur'],
                "wage_yearly": row['wage_eur'] * 52,
                "contract_end": int(row.get('contract_valid_until', 0))
            })
        return jsonify({"targets": out})
    except Exception:
         return jsonify({"targets": []})

@app.route("/api/next_match", methods=["GET", "OPTIONS"])
def api_next_match():
    """Returns competitor data for the 'Next Up' tab (from Excel or Mock)."""
    if request.method == "OPTIONS": return "", 200
    
    if next_match_data:
        # Return data from Excel
        return jsonify({
            "opponent": next_match_data.get("opponent", "Unknown FC"),
            "formation": next_match_data.get("formation", "4-4-2"),
            "team_rating": next_match_data.get("rating", 75),
            "insights": [next_match_data.get("insight_1", ""), next_match_data.get("insight_2", "")],
            "key_threat": {
                "name": next_match_data.get("threat_name", "N/A"),
                "position": next_match_data.get("threat_pos", "FWD"),
                "goals": next_match_data.get("threat_goals", 0),
                "score": next_match_data.get("threat_score", 80)
            },
            "weak_link": {
                "name": next_match_data.get("weakness_name", "N/A"),
                "position": next_match_data.get("weakness_pos", "DEF"),
                "tackles": next_match_data.get("weakness_stat", 0),
                "score": next_match_data.get("weakness_score", 50)
            },
            "prep_drills": [next_match_data.get("drill_1", "General Prep"), next_match_data.get("drill_2", "Tactical Review")]
        })
    
    # Fallback Mock Data
    return jsonify({
        "opponent": "Rebels FC (Mock)",
        "formation": "4-3-3 (Attack)",
        "team_rating": 78,
        "insights": ["Concede 65% goals from left flank.", "High defensive line vulnerability."],
        "key_threat": {"name": "Marcus Jones", "position": "LW", "goals": 12, "score": 88.2},
        "weak_link": {"name": "Liam Smith", "position": "CB", "tackles": 38, "score": 42.5},
        "prep_drills": ["Drill: Low-block transitions.", "Drill: Counter-attack passing channels."]
    })

# --- NEW: COMPARISON & SIMILAR PLAYER ENDPOINTS ---

@app.route("/api/compare_players", methods=["POST", "OPTIONS"])
def api_compare_players():
    """Returns side-by-side stats for two players."""
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        p1_name = payload.get("player1", "").lower()
        p2_name = payload.get("player2", "").lower()
        data_source = payload.get("data_source", "base")
        
        df = player_data_baller if data_source == 'baller' else player_data_base
        if df is None: return jsonify({"error": "Data not loaded"}), 500
        
        # Simple fuzzy find
        p1 = df[df['short_name'].astype(str).str.lower().str.contains(p1_name)].head(1)
        p2 = df[df['short_name'].astype(str).str.lower().str.contains(p2_name)].head(1)
        
        if p1.empty or p2.empty: return jsonify({"error": "Player not found"}), 404
        
        p1_row = p1.iloc[0]
        p2_row = p2.iloc[0]
        
        return jsonify({
            "player1": {
                "name": p1_row['short_name'],
                "overall": safe_int(p1_row.get('overall')),
                "heatmap": generate_heatmap_data(p1_row, p1_row.get('club_position')),
                "stats": p1_row.to_dict()
            },
            "player2": {
                "name": p2_row['short_name'],
                "overall": safe_int(p2_row.get('overall')),
                "heatmap": generate_heatmap_data(p2_row, p2_row.get('club_position')),
                "stats": p2_row.to_dict()
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/similar_players", methods=["POST", "OPTIONS"])
def api_similar_players():
    """Finds players with similar attributes (Euclidean distance simplified)."""
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        target_name = payload.get("target_player", "").lower()
        data_source = payload.get("data_source", "base")
        
        df = player_data_baller if data_source == 'baller' else player_data_base
        
        # 1. Find Target
        target = df[df['short_name'].astype(str).str.lower().str.contains(target_name)].head(1)
        if target.empty: return jsonify({"similar": []})
        target_row = target.iloc[0]
        
        # 2. Define Key Stats for Similarity
        key_stats = ['pace', 'shooting', 'passing', 'dribbling', 'defending', 'physic']
        if data_source == 'baller': key_stats = ['goals', 'assists', 'tackles', 'total_saves']
        
        # 3. Simple Similarity (Delta sum) - fast for prototype
        # Real ML would use Cosine Similarity
        candidates = df[df['club_position'] == target_row['club_position']].copy()
        
        def calc_dist(row):
            dist = 0
            for k in key_stats:
                dist += abs(safe_float(row.get(k,0)) - safe_float(target_row.get(k,0)))
            return dist
            
        candidates['distance'] = candidates.apply(calc_dist, axis=1)
        similar = candidates.sort_values('distance').head(6) # Top 5 similar (excluding self usually 1st)
        
        out = []
        for _, row in similar.iterrows():
            if row['short_name'] == target_row['short_name']: continue
            out.append({
                "name": row['short_name'],
                "match_percentage": max(0, 100 - row['distance']), # Rough proxy for similarity %
                "value_eur": safe_int(row.get('value_eur', 0))
            })
            
        return jsonify({"similar": out})
    except Exception as e:
         return jsonify({"similar": []})


@app.route("/api/player_detail/<player_id>", methods=["GET", "OPTIONS"])
def api_player_detail(player_id):
    if request.method == "OPTIONS": return "", 200
    # Returns placeholder charts, but detailed training logic is now in find/search routes
    return jsonify({"charts": []})

@app.route("/assets/<path:filename>")
def serve_assets(filename):
    return send_from_directory(os.path.join(app.root_path, "public/assets"), filename)

if __name__ == "__main__":
    print("🚀 Initializing backend...")
    initialize_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)