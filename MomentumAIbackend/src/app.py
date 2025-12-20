"""
MomentumScout Backend V1.1 (Final Complete)
---------------------------------------------------------
1. DATA: FC26 (Clubs) + Multi-Tab Excel (Baller League).
2. AI CORE: Squad Gap, Budget Target, Dynamic Training, Next Match.
3. VISUALS: Heatmaps, Player Comparison, Similar Players.
4. PREMIUM: Momentum Analyst AI + Tiered Entitlements.
5. SECURITY: 14-Day Trial Enforcement + Email Verification.
---------------------------------------------------------
"""
import os
import math
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from io import StringIO
from datetime import datetime, timedelta

app = Flask(__name__, static_folder="public")

# --- CONFIG & ORIGINS ---
# Added 'https://momentumscout.netlify.app' to default allowed origins to fix CORS
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://momentumscout.netlify.app") 
ALLOWED_ORIGINS = [
    FRONTEND_URL,
    "https://momentum-ai-io.netlify.app", # Keep old one just in case
    "https://momentumscout.netlify.app",  # Your actual live site
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

# --- FILE PATHS ---
DATA_FOLDER_PATH = os.environ.get("DATA_FOLDER_PATH", "data")
DATA_FILENAME_BASE = os.environ.get("DATA_FILENAME_BASE", "FC26_MomentumScout.csv")
DATA_FILENAME_BALLER = os.environ.get("DATA_FILENAME_BALLER", "baller_league_uk.xlsx") 
DATA_FILENAME_NEXT_MATCH = os.environ.get("DATA_FILENAME_NEXT_MATCH", "baller_next_match.xlsx")
DATA_FILENAME_SIGNUPS = "signups.csv" 
DATA_FILENAME_AUDIT = "analyst_usage.csv"

# Global Data
player_data_base = None 
player_data_baller = None 
next_match_data = None 

# --- ENTITLEMENTS (NEW V1.1) ---
ENTITLEMENTS_MAP = {
    'Agent': {'analyst_ai': False, 'export_csv': False}, 
    'Tier 3': {'analyst_ai': False, 'export_csv': False}, 
    'Tier 2': {'analyst_ai': 'yearly_only', 'export_csv': True}, 
    'Tier 1': {'analyst_ai': True, 'export_csv': True},  
    'Baller League': {'analyst_ai': False, 'export_csv': False}, 
    'Admin': {'analyst_ai': True, 'export_csv': True}
}

ACCESS_CODES = {'club': 'SCOUT2025', 'baller': 'BALLER2025'}

# --- DATABASES ---
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

POSITION_MAP = {
    'FWD': 'ST', 'MID': 'CM', 'DEF': 'CB', 'GOALKEEPER': 'GK', 'FORWARD': 'ST',
    'ST': 'ST', 'CM': 'CM', 'CB': 'CB', 'GK': 'GK'
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
    plan = { "weakness": "General Conditioning", "drills": ["Standard fitness regime", "Tactical positioning review"] }
    if is_baller:
        pos_key = 'ALL'
        if position in BALLER_WEIGHTS: pos_key = position
        weights = BALLER_WEIGHTS.get(pos_key, BALLER_WEIGHTS['ALL'])
    else:
        weights = POSITION_WEIGHTS.get(position, POSITION_WEIGHTS.get('CM', {}))
    if not weights: return plan
    lowest_attr, lowest_val = None, 1000
    for attr in weights.keys():
        if attr not in row: continue
        val = safe_float(row.get(attr, 0))
        if is_baller and val < 20: val = val * 5 
        if val < lowest_val: lowest_val, lowest_attr = val, attr
    if lowest_attr:
        drill = DRILL_DATABASE.get(lowest_attr, DRILL_DATABASE.get(lowest_attr.split('_')[-1], "General technical drills."))
        readable_attr = lowest_attr.replace('_', ' ').title()
        plan['weakness'] = f"Improve {readable_attr} (Current: {int(lowest_val)})"
        plan['drills'] = [ f"Primary: {drill}", f"Secondary: High-intensity {readable_attr} simulations." ]
    return plan

def generate_heatmap_data(row, position):
    zones = {'box': 10, 'wide': 10, 'mid': 10, 'def': 10}
    if position in ['ST', 'CF', 'FWD']: zones.update({'box': 90, 'wide': 40, 'mid': 30, 'def': 5})
    elif position in ['RW', 'LW']: zones.update({'box': 60, 'wide': 95, 'mid': 40, 'def': 20})
    elif position in ['CAM', 'CM', 'MID']: zones.update({'box': 40, 'wide': 30, 'mid': 95, 'def': 40})
    elif position in ['CDM']: zones.update({'box': 15, 'wide': 20, 'mid': 80, 'def': 80})
    elif position in ['CB', 'DEF']: zones.update({'box': 5, 'wide': 10, 'mid': 30, 'def': 95})
    elif position in ['LB', 'RB']: zones.update({'box': 10, 'wide': 85, 'mid': 50, 'def': 80})
    elif position == 'GK': zones.update({'box': 100, 'wide': 0, 'mid': 0, 'def': 100})
    shooting = safe_float(row.get('shooting', 0))
    if shooting > 80: zones['box'] += 10
    pace = safe_float(row.get('pace', 0))
    if pace > 85: zones['wide'] += 10
    return {k: min(100, v) for k,v in zones.items()}

def generate_analyst_insight(row, tier='Tier 3', plan='monthly'):
    """NEW V1.1: Generates advanced Momentum Analyst insights."""
    insights = {
        "status": "active", "risk_level": "Low",
        "momentum_trend": "Stable", "market_verdict": "Hold", "tactical_fit": []
    }
    physic = safe_float(row.get('physic', 0))
    age = safe_int(row.get('age', 0))
    passing = safe_float(row.get('passing', 0))
    defending = safe_float(row.get('defending', 0))

    if physic < 60 and age > 28:
        insights['risk_level'] = "High (Declining Physicality)"
        insights['market_verdict'] = "Sell / Avoid"
    elif age < 23 and physic > 65:
        insights['risk_level'] = "Low (High Durability)"
        insights['market_verdict'] = "Strong Buy (High Growth)"

    if passing > 78: insights['tactical_fit'].append("Ideal for Possession-based systems")
    if defending > 78: insights['tactical_fit'].append("Fits High-Press block")
    if not insights['tactical_fit']: insights['tactical_fit'].append("Standard tactical fit")
    
    # Tier 1 Specific Depth
    if tier == 'Tier 1' or (tier == 'Tier 2' and plan == 'yearly'):
        insights['negotiation_leverage'] = "High - Player contract expiring in <12 months."
        insights['scout_recommendation'] = "Immediate bid recommended within valuation range."

    return insights

def compute_score_for_player(row, position="CM", user_weights=None, is_baller=False):
    if is_baller:
        weights = BALLER_WEIGHTS.get(position, BALLER_WEIGHTS['ALL']).copy()
        if user_weights: weights.update(user_weights)
        score, total_w = 0.0, sum(weights.values()) or 1
        for attr, weight in weights.items():
            val = safe_float(row.get(attr), 0.0)
            norm_val = min(100, val * 5) if 'pct' not in attr else val
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
    value = float(row.get('value_eur', 0) or 0)
    age = safe_int(row.get('age', 21))
    projections = []
    
    for y in range(1, years + 1):
        # Age Decay Logic
        if age + y > 29:
            ovr = max(0, ovr - 1) # Decline
            value = value * 0.9
        else:
            value = value * 1.1 
            ovr += 1
            
        projections.append({"year": y, "projected_value_eur": int(value), "projected_overall": ovr})
    return projections

def negotiation_range(current_value: int, projected_value: int):
    return { "min_offer": int(current_value * 0.8), "max_offer": int(projected_value * 1.1) }

# --- USER MANAGEMENT & SECURITY (UPDATED) ---
def save_signup(data):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp):
        df = pd.DataFrame(columns=['fullName', 'email', 'organization', 'role', 'tier', 'plan', 'timestamp'])
        df.to_csv(fp, index=False)
    
    new_row = {
        'fullName': data.get('fullName'),
        'email': data.get('email', '').strip().lower(),
        'organization': data.get('organization'),
        'role': data.get('role'),
        'tier': data.get('tier', 'Tier 3'), # Default to lowest tier
        'plan': data.get('plan', 'monthly'), # Default to monthly
        'timestamp': datetime.now().isoformat()
    }
    try:
        # Check for duplicates
        df = pd.read_csv(fp)
        if 'email' in df.columns and new_row['email'] in df['email'].values:
             # Update existing user
             df.loc[df['email'] == new_row['email']] = pd.Series(new_row)
             df.to_csv(fp, index=False)
        else:
             # Append new user
             df_new = pd.DataFrame([new_row])
             df_new.to_csv(fp, mode='a', header=False, index=False)
        return True
    except Exception as e:
        print(f"❌ Error saving signup: {e}")
        return False

def check_email_authorized(email):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp): return False
    try:
        df = pd.read_csv(fp)
        if 'email' in df.columns:
            return email.strip().lower() in df['email'].str.strip().str.lower().values
        return False
    except: return False

def check_login_status(email):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp): return False, "No users found.", {}

    try:
        df = pd.read_csv(fp)
        user_row = df[df['email'].str.strip().str.lower() == email.strip().lower()]
        
        if user_row.empty: return False, "Email not recognized.", {}
        
        # Get User Info
        user = user_row.iloc[-1]
        tier = user.get('tier', 'Tier 3')
        plan = user.get('plan', 'monthly')
        signup_str = user.get('timestamp', datetime.now().isoformat())

        # Trial Logic
        try:
            signup_date = pd.to_datetime(signup_str).to_pydatetime()
            days_since = (datetime.now() - signup_date).days
            
            # Allow access if under 14 days OR if they are Tier 1 OR Yearly plan
            is_exempt = (tier == 'Tier 1') or (plan == 'yearly')
            
            if days_since > 14 and not is_exempt:
                return False, "Trial Expired (14 Days). Upgrade required.", {}
        except Exception as e: 
            print(f"⚠️ Date parse error for {email}: {e}")
            pass 

        # Entitlements Logic
        config = ENTITLEMENTS_MAP.get(tier, ENTITLEMENTS_MAP['Tier 3'])
        
        analyst_access = False
        raw_access = config.get('analyst_ai', False)
        if raw_access is True: analyst_access = True
        elif raw_access == 'yearly_only' and plan == 'yearly': analyst_access = True
        
        return True, "Login Verified", {"tier": tier, "analyst_ai": analyst_access}

    except Exception as e:
        print(f"❌ Login Check Error: {e}")
        return False, f"System Error: {str(e)}", {}

def log_analyst_usage(email, player_name):
    """Simple audit logging for Analyst AI usage."""
    fp = os.path.join(DATA_FOLDER_PATH, "analyst_usage.csv")
    if not os.path.exists(fp):
        pd.DataFrame(columns=['email', 'player', 'timestamp']).to_csv(fp, index=False)
    try:
        new_row = {'email': email, 'player': player_name, 'timestamp': datetime.now().isoformat()}
        pd.DataFrame([new_row]).to_csv(fp, mode='a', header=False, index=False)
    except: pass

# --- DATA LOADERS ---
def _load_baller_league_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp): return pd.DataFrame()
    try:
        sheets = pd.read_excel(fp, sheet_name=None)
        merged = None
        for _, df in sheets.items():
            df.columns = [clean_column_name(c) for c in df.columns]
            if 'name' not in df.columns: continue
            if merged is None: merged = df
            else: merged = pd.merge(merged, df, on=['name'], how='outer', suffixes=('', '_dup'))
        if merged is not None:
            merged = merged.fillna(0)
            merged['short_name'] = merged.get('name', 'Unknown')
            
            # --- FIX 9: Position Normalization ---
            # Try to map 'position' or 'pos' column to standard ID if it exists
            # Otherwise default to 'Baller'
            raw_pos = merged.get('position', merged.get('pos', 'Baller'))
            # If raw_pos is a Series (column), map it
            if isinstance(raw_pos, pd.Series):
                merged['club_position'] = raw_pos.map(lambda x: POSITION_MAP.get(str(x).upper(), 'Baller'))
            else:
                merged['club_position'] = 'Baller'

            # --- FIX 1: Vectorized Momentum Score Calculation ---
            # Using pd.to_numeric to handle entire columns safely
            goals = pd.to_numeric(merged.get('goals', 0), errors='coerce').fillna(0)
            assists = pd.to_numeric(merged.get('assists', 0), errors='coerce').fillna(0)
            tackles = pd.to_numeric(merged.get('tackles', 0), errors='coerce').fillna(0)
            
            if 'momentum_score' not in merged.columns:
                merged['momentum_score'] = (goals * 5) + (assists * 3) + (tackles * 2)
            
            merged['overall'] = 50 + (merged['momentum_score'].clip(upper=50)).astype(int)
            merged['potential'] = merged['overall'] + 5
            merged['value_eur'] = merged['momentum_score'] * 10000
            return merged
        return pd.DataFrame()
    except Exception as e:
        print(f"Error reading Baller Excel: {e}")
        return pd.DataFrame()

def _load_next_match_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp): return None
    try:
        df = pd.read_excel(fp)
        df.columns = [clean_column_name(c) for c in df.columns]
        return df.iloc[0].to_dict() if not df.empty else None
    except: return None

def _load_fc26_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp): return pd.DataFrame()
    try:
        if filename.endswith('.csv'): df = pd.read_csv(fp, encoding="utf-8-sig")
        else: df = pd.read_excel(fp) 
    except: return pd.DataFrame()
    
    df.columns = [clean_column_name(c) for c in df.columns]
    
    # CRITICAL FIX: Ensure all numeric-like columns are coerced to numbers
    numeric_cols = [col for col in df.columns if df[col].dtype == 'object']
    for col in numeric_cols:
        try: 
            if any(x in col for x in ['overall', 'value', 'wage', 'pace', 'shooting', 'passing', 'dribbling', 'defending', 'physic', 'age', 'contract']):
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        except: pass
        
    if "sofifa_id" in df.columns:
        def get_face_url(x):
            try:
                if pd.isna(x): return ""
                val = int(x)
                return f"https://cdn.sofifa.net/players/{val//1000:03d}/{val%1000:03d}/24.webp"
            except:
                return ""
        df["player_face_url"] = df["sofifa_id"].apply(get_face_url)
    return df

def initialize_app():
    global player_data_base, player_data_baller, next_match_data
    player_data_base = _load_fc26_data(DATA_FILENAME_BASE)
    player_data_baller = _load_baller_league_data(DATA_FILENAME_BALLER)
    next_match_data = _load_next_match_data(DATA_FILENAME_NEXT_MATCH)
    
    # FIX 3: Check if admin exists before adding
    admin_email = 'info@momentumscout.com'
    if not check_email_authorized(admin_email):
        save_signup({'fullName': 'Admin', 'email': admin_email, 'organization': 'Admin', 'role': 'Admin', 'tier': 'Tier 1', 'plan': 'yearly'})

# --- API ROUTES ---
@app.route("/", methods=["GET"])
def health_check(): return jsonify({"status": "online"}), 200

@app.route("/api/verify_login", methods=["POST", "OPTIONS"])
def api_verify_login():
    if request.method == "OPTIONS": return "", 200
    try:
        data = request.json or {}
        email, code, portal = data.get("email", ""), data.get("code", ""), data.get("portal", "")
        if ACCESS_CODES.get(portal) != code: return jsonify({"success": False, "message": "Invalid Access Code"}), 401
        is_valid, msg, entitlements = check_login_status(email)
        if not is_valid: return jsonify({"success": False, "message": msg}), 403
        return jsonify({"success": True, "message": msg, "entitlements": entitlements})
    except Exception as e: 
        print(f"Login Error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/submit_demo", methods=["POST", "OPTIONS"])
def api_submit_demo():
    if request.method == "OPTIONS": return "", 200
    try:
        save_signup(request.json)
        return jsonify({"success": True, "message": "Signup recorded."})
    except Exception as e: 
        print(f"Submit Error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/momentum_analyst", methods=["POST", "OPTIONS"])
def api_momentum_analyst():
    """NEW: Generates high-value insights."""
    if request.method == "OPTIONS": return "", 200
    try:
        data = request.json or {}
        # Enforce Entitlements using SERVER-SIDE lookup
        email = data.get("email") # Require frontend to send email
        is_valid, _, entitlements = check_login_status(email)
        
        if not is_valid or not entitlements.get("analyst_ai"):
            return jsonify({"success": False, "message": "Upgrade to Tier 2 (Yearly) or Tier 1 to access Momentum Analyst AI."}), 403

        name = data.get("player_name")
        tier = entitlements.get("tier", "Tier 3")
        plan = "monthly" 

        # Audit Log
        log_analyst_usage(email, name)

        player = player_data_base[player_data_base['short_name'] == name]
        if not player.empty:
            return jsonify({"success": True, "insights": generate_analyst_insight(player.iloc[0], tier, plan)})
        return jsonify({"success": False, "message": "Player not found."})
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/find_players", methods=["POST", "OPTIONS"])
def api_find_players():
    if request.method == "OPTIONS": return "", 200
    try:
        data = request.json or {}
        df = player_data_baller if data.get("data_source") == 'baller' else player_data_base
        
        if df is None: 
            # Reload Attempt
            if data.get("data_source") == 'baller':
                 df = _load_baller_league_data(DATA_FILENAME_BALLER)
            else:
                 df = _load_fc26_data(DATA_FILENAME_BASE)
        
        if df is None or df.empty: return jsonify({"players": []})
        
        df = df.copy() # Make copy immediately
        
        # Apply Filters...
        filters = data.get("filters", {})
        for key, rng in filters.items():
            col = clean_column_name(key)
            if 'value' in col: col = 'value_eur'
            if col in df.columns and isinstance(rng, list) and len(rng) >= 2:
                 df = df[(df[col] >= float(rng[0])) & (df[col] <= float(rng[1]))]

        # Apply Scoring...
        is_baller = (data.get("data_source") == 'baller')
        df['momentum_score'] = df.apply(lambda r: compute_score_for_player(r, data.get("position", "ALL"), data.get("weights"), is_baller), axis=1)
        
        out = []
        for _, row in df.sort_values('momentum_score', ascending=False).head(20).iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k,v in p_dict.items()}
            
            # Add AI Plans
            tp = generate_training_plan(row, row.get('club_position','CM'), is_baller)
            hm = generate_heatmap_data(row, row.get('club_position','CM'))
            
            out.append({
                "short_name": p_dict.get('short_name'),
                "club_position": p_dict.get('club_position'),
                "momentum_score": p_dict.get('momentum_score'),
                "value_eur": p_dict.get('value_eur', 0),
                "player_face_url": p_dict.get('player_face_url', ''),
                "ai_training": tp,
                "heatmap_zones": hm,
                "full_attributes": p_dict,
                "projections": project_player(row)
            })
        return jsonify({"players": out})
    except Exception as e: return jsonify({"players": [], "error": str(e)}), 500

# [ADVANCED V1 ROUTES (Comparison, Similar, Squad, Budget, Next Match)]
@app.route("/api/compare_players", methods=["POST", "OPTIONS"])
def api_compare_players():
    if request.method == "OPTIONS": return "", 200
    try:
        data = request.json or {}
        p1_name = data.get("player1", "").lower() 
        p2_name = data.get("player2", "").lower()
        data_source = data.get("data_source", "base")
        df = player_data_baller if data.get("data_source") == 'baller' else player_data_base
        
        p1 = df[df['short_name'].astype(str).str.lower().str.contains(p1_name)].head(1)
        p2 = df[df['short_name'].astype(str).str.lower().str.contains(p2_name)].head(1)
        
        if p1.empty or p2.empty: return jsonify({"error": "Player not found"}), 404
        
        p1_row, p2_row = p1.iloc[0], p2.iloc[0]
        return jsonify({
            "player1": { "name": p1_row['short_name'], "overall": safe_int(p1_row.get('overall')), "heatmap": generate_heatmap_data(p1_row, p1_row.get('club_position')), "stats": p1_row.to_dict() },
            "player2": { "name": p2_row['short_name'], "overall": safe_int(p2_row.get('overall')), "heatmap": generate_heatmap_data(p2_row, p2_row.get('club_position')), "stats": p2_row.to_dict() }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/similar_players", methods=["POST", "OPTIONS"])
def api_similar_players():
    if request.method == "OPTIONS": return "", 200
    try:
        data = request.json or {}
        target_name = data.get("target_player", "").lower() 
        data_source = data.get("data_source", "base")
        df = player_data_baller if data_source == 'baller' else player_data_base
        target = df[df['short_name'].astype(str).str.lower().str.contains(target_name)].head(1)
        if target.empty: return jsonify({"similar": []})
        target_row = target.iloc[0]
        key_stats = ['pace', 'shooting', 'passing', 'dribbling', 'defending', 'physic']
        if data_source == 'baller': key_stats = ['goals', 'assists', 'tackles', 'total_saves']
        candidates = df[df['club_position'] == target_row['club_position']].copy()
        def calc_dist(row):
            dist = 0
            for k in key_stats:
                dist += abs(safe_float(row.get(k,0)) - safe_float(target_row.get(k,0)))
            return dist
        candidates['distance'] = candidates.apply(calc_dist, axis=1)
        similar = candidates.sort_values('distance').head(6) 
        out = []
        for _, row in similar.iterrows():
            if row['short_name'] != target_row['short_name']:
                out.append({
                    "name": row['short_name'],
                    "match_percentage": max(0, 100 - row['distance']),
                    "value_eur": safe_int(row.get('value_eur', 0))
                })
        return jsonify({"similar": out})
    except Exception as e:
         return jsonify({"similar": []})

@app.route("/api/search_player", methods=["POST", "OPTIONS"])
def api_search_player():
    if request.method == "OPTIONS": return "", 200
    try:
        data = request.json or {}
        query = str(data.get("player_name", "")).lower().strip()
        is_baller = (data.get("data_source") == 'baller')
        df = player_data_baller if is_baller else player_data_base
        
        if df is None or df.empty or not query: return jsonify([])
        mask = df['short_name'].astype(str).str.lower().str.contains(query) 
        if 'name' in df.columns:
             mask |= df['name'].astype(str).str.lower().str.contains(query)
             
        results = df[mask].head(10)
        
        out = []
        for _, row in results.iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k,v in p_dict.items()}
            score = compute_score_for_player(row, row.get('club_position','CM'), None, is_baller)
            age = safe_int(row.get("age"), 21)
            projections = project_player(row, years_to_project(age))
            training_plan = generate_training_plan(row, row.get('club_position', 'CM'), is_baller)
            heatmap = generate_heatmap_data(row, row.get('club_position', 'CM'))
            
            out.append({
                "short_name": p_dict.get('short_name'),
                "club_position": p_dict.get('club_position'),
                "momentum_score": score,
                "projections": projections,
                "ai_training": training_plan, 
                "heatmap_zones": heatmap,
                "value_eur": safe_int(p_dict.get('value_eur')),
                "full_attributes": p_dict
            })
        return jsonify(out)
    except Exception as e:
        return jsonify([]), 500

@app.route("/api/squad_gap_analysis", methods=["POST", "OPTIONS"])
def api_squad_gap_analysis():
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        squad_csv_text = payload.get("csv_data", "")
        target_pos = payload.get("position", "ALL")
        
        if not squad_csv_text: 
            return jsonify({"suggestions": ["No squad data uploaded."]})

        squad_df = pd.read_csv(StringIO(squad_csv_text))
        squad_df.columns = [clean_column_name(c) for c in squad_df.columns]
        squad_df = squad_df.fillna(0) # FIXED: Handle NaN in uploads
        
        suggestions = []
        suggestions.append(f"Squad Size Analysis: {len(squad_df)} players loaded.")
        
        positions_to_check = ['CB', 'CM', 'ST'] if target_pos == "ALL" else [target_pos]
            
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
    if request.method == "OPTIONS": return "", 200
    try:
        payload = request.json or {}
        max_wage = safe_int(payload.get("max_wage"), 500000)
        contract_year = safe_int(payload.get("contract_year"), 2026)
        
        df = player_data_base.copy()
        
        contract_col = 'contract_valid_until'
        if 'club_contract_valid_until_year' in df.columns:
            contract_col = 'club_contract_valid_until_year'
            
        df = df[ (df['wage_eur'] * 52) <= max_wage ]
        if contract_col in df.columns:
            df = df[df[contract_col] <= contract_year]
            
        targets = df.sort_values(by='overall', ascending=False).head(10)
        out = []
        for _, row in targets.iterrows():
            out.append({
                "short_name": row['short_name'],
                "club_position": row['club_position'],
                "overall": row['overall'],
                "value_eur": row['value_eur'],
                "wage_yearly": row['wage_eur'] * 52,
                "contract_end": int(row.get(contract_col, 0))
            })
        return jsonify({"targets": out})
    except Exception:
         return jsonify({"targets": []})

@app.route("/api/next_match", methods=["GET", "OPTIONS"])
def api_next_match():
    if request.method == "OPTIONS": return "", 200
    if next_match_data:
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
    return jsonify({
        "opponent": "Rebels FC (Mock)",
        "formation": "4-3-3 (Attack)",
        "team_rating": 78,
        "insights": ["Concede 65% goals from left flank.", "High defensive line vulnerability."],
        "key_threat": {"name": "Marcus Jones", "position": "LW", "goals": 12, "score": 88.2},
        "weak_link": {"name": "Liam Smith", "position": "CB", "tackles": 38, "score": 42.5},
        "prep_drills": ["Drill: Low-block transitions.", "Drill: Counter-attack passing channels."]
    })

@app.route("/api/player_detail/<player_id>", methods=["GET", "OPTIONS"])
def api_player_detail(player_id):
    if request.method == "OPTIONS": return "", 200
    return jsonify({"charts": []})

@app.route("/assets/<path:filename>")
def serve_assets(filename):
    return send_from_directory(os.path.join(app.root_path, "public/assets"), filename)

# --- INITIALIZATION LOGIC (CRITICAL: Runs immediately) ---
try:
    print("🚀 Auto-Initializing backend data loaders...")
    initialize_app()
except Exception as e:
    print(f"❌ Initialization Error: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)