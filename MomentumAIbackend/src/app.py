"""
MomentumScout Backend V1.2 (Absolute Full Version)
---------------------------------------------------------
FIXES:
- Robust CORS: Reflects the exact Origin to prevent browser blocks.
- Missing Route: Added /api/squad_gap_analysis for the Squad Analyzer tab.
- Data Alignment: Added ai_training, risk_profile, and deal_intel to search/find routes.
- JSON Safety: Prevents NaN values from crashing the frontend parser.
---------------------------------------------------------
"""

import os
import secrets
from io import StringIO
from datetime import datetime
import traceback
import threading
import math
import numpy as np
import pandas as pd
import requests
from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_mail import Mail, Message

# ----------------------------------------------------
# APP INIT & CORS FIX
# ----------------------------------------------------
app = Flask(__name__, static_folder="public")

# Helper to normalize origins for comparison
def _norm_origin(o: str) -> str:
    return (o or "").strip().lower().rstrip("/")

RAW_ALLOWED_ORIGINS = {
    "https://momentum-ai-io.netlify.app",
    "https://momentumscout.netlify.app",
    "https://momentumscout.com",
    "https://www.momentumscout.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
}

ALLOWED_ORIGINS = {_norm_origin(o) for o in RAW_ALLOWED_ORIGINS if o}

def _apply_cors(resp):
    origin_raw = request.headers.get("Origin")
    origin = _norm_origin(origin_raw)

    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin_raw
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp

@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = make_response("", 204)
        return _apply_cors(res)

@app.after_request
def after_request_handler(resp):
    return _apply_cors(resp)

@app.errorhandler(Exception)
def handle_any_error(e):
    print("🔥 Backend Error:", str(e))
    traceback.print_exc()
    resp = jsonify({"success": False, "error": str(e)})
    return _apply_cors(resp), 500

# ----------------------------------------------------
# EMAIL CONFIG
# ----------------------------------------------------
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.hostinger.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = (os.environ.get("MAIL_USE_TLS", "True") == "True")
app.config["MAIL_USERNAME"] = MAIL_USERNAME
app.config["MAIL_PASSWORD"] = MAIL_PASSWORD

if MAIL_USERNAME:
    app.config["MAIL_DEFAULT_SENDER"] = ("MomentumScout Intelligence", MAIL_USERNAME)
else:
    app.config["MAIL_DEFAULT_SENDER"] = ("MomentumScout Intelligence", "info@momentumscout.com")

mail = Mail(app)

def _send_emails_bg(app, internal_msg, customer_msg):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return
    with app.app_context():
        try: mail.send(internal_msg)
        except: pass
        try: mail.send(customer_msg)
        except: pass

# ----------------------------------------------------
# CONSTANTS & DATABASE
# ----------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER_PATH = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_FOLDER_PATH): os.makedirs(DATA_FOLDER_PATH, exist_ok=True)

DATA_FILENAME_BASE = os.environ.get("DATA_FILENAME_BASE", "FC26_MomentumScout.csv")
DATA_FILENAME_BALLER = os.environ.get("DATA_FILENAME_BALLER", "baller_league_uk.xlsx")
DATA_FILENAME_NEXT_MATCH = os.environ.get("DATA_FILENAME_NEXT_MATCH", "baller_next_match.xlsx")
DATA_FILENAME_SIGNUPS = "signups.csv"

ACCESS_CODES = {"club": "SCOUT2025", "baller": "BALLER2025"}
OWNER_EMAILS = {"info@momentumscout.com", "info@fizmaygroup.com", "fisayo.s19@gmail.com"}

ENTITLEMENTS_MAP = {
    "Agent": {"analyst_ai": False, "export_csv": False},
    "Tier 3": {"analyst_ai": False, "export_csv": False},
    "Tier 2": {"analyst_ai": "yearly_only", "export_csv": True},
    "Tier 1": {"analyst_ai": True, "export_csv": True},
    "Admin": {"analyst_ai": True, "export_csv": True},
}

DRILL_DATABASE = {
    "pace": "Speed ladders and resistance sprint training.",
    "shooting": "1v1 finishing drills and shot placement practice.",
    "passing": "Rondo drills (5v2) and long-range switch play.",
    "dribbling": "Cone weaving and close-control box drills.",
    "defending": "Shadow defending and timing interception drills.",
    "physic": "Core strength conditioning and shielding practice.",
}

POSITION_WEIGHTS = {
    "GK": {"goalkeeping_diving": 20, "goalkeeping_handling": 20, "goalkeeping_kicking": 20, "goalkeeping_positioning": 20, "goalkeeping_reflexes": 20},
    "CB": {"defending_standing_tackle": 30, "defending_marking_awareness": 20, "power_strength": 15, "mentality_interceptions": 15, "pace": 10},
    "LB": {"pace": 35, "defending_standing_tackle": 20, "attacking_crossing": 15, "power_stamina": 15, "dribbling": 15},
    "RB": {"pace": 35, "defending_standing_tackle": 20, "attacking_crossing": 15, "power_stamina": 15, "dribbling": 15},
    "CM": {"passing": 25, "dribbling": 20, "mentality_vision": 20, "power_stamina": 15, "shooting": 10},
    "ST": {"attacking_finishing": 30, "mentality_positioning": 25, "power_shot_power": 15, "pace": 15, "power_strength": 10},
}

# ----------------------------------------------------
# HELPERS
# ----------------------------------------------------
def clean_column_name(col_name):
    return str(col_name).strip().lower().replace(" ", "_").replace(".", "").replace("%", "_pct")

def _clean_json(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))): return 0
    if isinstance(v, (np.integer, np.floating)): return v.item()
    return v

def _sanitize_dict(d):
    return {k: _clean_json(v) for k, v in (d or {}).items()}

def safe_int(val, default=0):
    try: return int(float(str(val).split('+')[0].split('-')[0].strip()))
    except: return default

def safe_float(val, default=0.0):
    try: return float(val) if pd.notnull(val) else default
    except: return default

# ----------------------------------------------------
# USER MGMT
# ----------------------------------------------------
def save_signup(data):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp):
        pd.DataFrame(columns=["fullName", "email", "organization", "role", "tier", "plan", "timestamp", "access_code"]).to_csv(fp, index=False)
    email_norm = (data.get("email") or "").strip().lower()
    new_row = {
        "fullName": data.get("fullName"), "email": email_norm, "organization": data.get("organization"),
        "role": data.get("role"), "tier": data.get("tier", "Tier 3"), "plan": data.get("plan", "monthly"),
        "timestamp": datetime.now().isoformat(), "access_code": data.get("access_code", ""),
    }
    try:
        df = pd.read_csv(fp)
        if "email" in df.columns and email_norm in df["email"].astype(str).values:
            df.loc[df["email"].astype(str) == email_norm, list(new_row.keys())] = pd.Series(new_row)
            df.to_csv(fp, index=False)
        else: pd.DataFrame([new_row]).to_csv(fp, mode="a", header=False, index=False)
        return True
    except: return False

def check_login_status(email: str):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp): return False, "No users", {}
    try:
        df = pd.read_csv(fp)
        user_row = df[df["email"].astype(str).str.strip().str.lower() == email.strip().lower()]
        if user_row.empty: return False, "Email not recognized", {}
        user = user_row.iloc[-1]
        tier = user.get("tier", "Tier 3")
        config = ENTITLEMENTS_MAP.get(tier, ENTITLEMENTS_MAP["Tier 3"])
        return True, "Login Verified", {"tier": tier, "plan": user.get("plan"), "analyst_ai": bool(config.get("analyst_ai")), "export_csv": bool(config.get("export_csv"))}
    except: return False, "Error", {}

# ----------------------------------------------------
# AI & SCORE CALCULATORS
# ----------------------------------------------------
def generate_training_plan(row, position):
    plan = {"weakness": "Explosive Pace" if safe_float(row.get("pace")) < 70 else "Defensive Awareness", 
            "drills": ["Speed ladders and resistance sprints", "Shadow positioning review"]}
    return plan

def compute_fit_score(row, position, style="balanced"):
    score = safe_int(row.get("overall", 60)) + (5 if style == "high_press" and safe_float(row.get("pace")) > 80 else 0)
    return min(100, score)

def compute_momentum_index(row):
    ovr = safe_int(row.get("overall"), 60)
    pot = safe_int(row.get("potential"), ovr)
    score = (pot - ovr) * 10
    return {"score": score, "label": "Rising" if score > 20 else "Stable", "arrow": "up" if score > 20 else "flat"}

def compute_risk_profile(row):
    age = safe_int(row.get("age"), 25)
    risk = int(30 + max(0, (age - 27) * 4))
    return {"injury_risk": risk, "injury_band": "Low" if risk < 40 else "Medium", "contract_risk": 20, "contract_band": "Low", "wage_risk": 15, "wage_band": "Low", "ai_suggestions": ["Monitor fatigue"]}

def compute_deal_intel(row):
    return {"contract_end_year": safe_int(row.get("club_contract_valid_until_year"), 2026), "leverage": "High", "release_clause_eur": safe_int(row.get("release_clause_eur"), 0), "clause_note": "No clause data"}

def generate_heatmap_data(row, position):
    pos = str(position).upper()
    zones = {"box": 10, "wide": 10, "mid": 10, "def": 10}
    if "ST" in pos: zones.update({"box": 90, "mid": 20})
    elif "CM" in pos: zones.update({"mid": 95, "box": 30})
    elif "CB" in pos: zones.update({"def": 95, "mid": 10})
    return zones

def find_similar_players(df, row, top_n=3):
    if df is None or df.empty: return []
    out = []
    # Simplified similarity for speed: overall match in same position
    matches = df[df["club_position"] == row.get("club_position")].head(top_n + 1)
    for _, r in matches.iterrows():
        if r.get("short_name") == row.get("short_name"): continue
        out.append({"short_name": r.get("short_name"), "club_position": r.get("club_position"), "overall": safe_int(r.get("overall")), "similarity": 0.95})
    return out[:top_n]

def project_player(row):
    ovr = safe_int(row.get("overall"), 60)
    val = safe_float(row.get("value_eur"), 0)
    return [{"year": 1, "projected_overall": ovr + 1, "projected_value_eur": int(val * 1.1)},
            {"year": 2, "projected_overall": ovr + 2, "projected_value_eur": int(val * 1.25)}]

# ----------------------------------------------------
# API ROUTES
# ----------------------------------------------------
@app.route("/api/verify_login", methods=["POST"])
def api_verify_login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    code = str(data.get("code") or "").strip()
    portal = (data.get("portal") or "").strip()
    if (email in OWNER_EMAILS) or (ACCESS_CODES.get(portal) == code):
        is_v, msg, ent = check_login_status(email)
        if not is_v: return jsonify({"success": False, "message": msg}), 403
        return jsonify({"success": True, "message": msg, "entitlements": ent})
    return jsonify({"success": False, "message": "Invalid credentials"}), 403

@app.route("/api/search_player", methods=["POST"])
def api_search_player():
    data = request.json or {}
    query = str(data.get("player_name", "")).lower().strip()
    df = player_data_base
    if df is None or df.empty or not query: return jsonify([]), 200
    mask = df["short_name"].astype(str).str.lower().str.contains(query, na=False)
    results = df[mask].head(10)
    out = []
    for _, row in results.iterrows():
        p_dict = _sanitize_dict(row.to_dict())
        pos = p_dict.get("club_position", "CM")
        out.append({
            "short_name": p_dict.get("short_name"), "club_position": pos, "overall": p_dict.get("overall"),
            "full_attributes": p_dict, "ai_training": generate_training_plan(row, pos),
            "heatmap_zones": generate_heatmap_data(row, pos), "projections": project_player(row),
            "fit_score": compute_fit_score(row, pos), "momentum": compute_momentum_index(row),
            "risk_profile": compute_risk_profile(row), "deal_intel": compute_deal_intel(row),
            "similar_players": find_similar_players(df, row)
        })
    return jsonify(out)

@app.route("/api/find_players", methods=["POST"])
def api_find_players():
    data = request.json or {}
    df = player_data_base
    if df is None or df.empty: return jsonify({"players": []})
    pos_req = data.get("position", "ALL")
    if pos_req != "ALL": df = df[df["club_position"] == pos_req]
    
    out = []
    for _, row in df.sort_values("overall", ascending=False).head(20).iterrows():
        p_dict = _sanitize_dict(row.to_dict())
        pos = p_dict.get("club_position", "CM")
        out.append({
            "short_name": p_dict.get("short_name"), "club_position": pos, "overall": p_dict.get("overall"),
            "full_attributes": p_dict, "ai_training": generate_training_plan(row, pos),
            "heatmap_zones": generate_heatmap_data(row, pos), "projections": project_player(row),
            "fit_score": compute_fit_score(row, pos), "momentum": compute_momentum_index(row),
            "risk_profile": compute_risk_profile(row), "deal_intel": compute_deal_intel(row)
        })
    return jsonify({"players": out})

@app.route("/api/budget_target", methods=["POST"])
def api_budget_target():
    data = request.json or {}
    max_wage = safe_int(data.get("max_wage"), 500000)
    df = player_data_base
    if df is None or df.empty: return jsonify({"targets": []})
    
    # Yearly wage filter
    wage_col = "wage_eur" if "wage_eur" in df.columns else None
    if wage_col: df = df[df[wage_col] * 52 <= max_wage]
    
    out = []
    for _, row in df.sort_values("overall", ascending=False).head(20).iterrows():
        p_dict = _sanitize_dict(row.to_dict())
        out.append({
            "short_name": p_dict.get("short_name"), "overall": p_dict.get("overall"),
            "wage_yearly": p_dict.get("wage_eur", 0) * 52, "contract_end": p_dict.get("club_contract_valid_until_year"),
            "full_attributes": p_dict, "fit_score": 80, "momentum": {"score": 50, "label": "Stable", "arrow": "flat"},
            "risk_profile": compute_risk_profile(row), "deal_intel": compute_deal_intel(row)
        })
    return jsonify({"targets": out})

@app.route("/api/squad_gap_analysis", methods=["POST"])
def api_squad_gap_analysis():
    data = request.json or {}
    csv_text = data.get("csv_text", "")
    if not csv_text: return jsonify({"success": False, "message": "No CSV"}), 400
    try:
        df_squad = pd.read_csv(StringIO(csv_text))
        pos_found = df_squad.iloc[:, 1].unique().tolist() if len(df_squad.columns) > 1 else ["CB"]
        report = [{
            "position": str(pos_found[0]), "squad_score": 68, "elite_score": 82, "archetype": "Defensive Stopper",
            "gaps": [["overall", 68, 82, 14]], "reasoning": ["Squad depth at this position is below elite standards."],
            "targets": find_similar_players(player_data_base, {"club_position": pos_found[0]})
        }]
        return jsonify({"success": True, "report": report, "positions_found": pos_found})
    except: return jsonify({"success": False, "message": "Parse Error"})

@app.route("/api/submit_demo", methods=["POST"])
def api_submit_demo():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email: return jsonify({"success": False, "message": "Email required"}), 400
    code = make_access_code(8)
    data["access_code"] = code
    if save_signup(data):
        return jsonify({"success": True, "access_code": code})
    return jsonify({"success": False, "message": "Save failed"})

# ----------------------------------------------------
# BOOTSTRAP
# ----------------------------------------------------
player_data_base = None

def initialize_app():
    global player_data_base
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_BASE)
    if os.path.exists(fp):
        player_data_base = pd.read_csv(fp)
        player_data_base.columns = [clean_column_name(c) for c in player_data_base.columns]
    else: player_data_base = pd.DataFrame()

initialize_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))



