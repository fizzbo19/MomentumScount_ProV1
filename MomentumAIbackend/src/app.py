"""
MomentumScout Backend V1.2 (Stable on Render)
---------------------------------------------------------
Fixes:
- Robust CORS (Origins only; no paths)
- Google Sheets posting indentation/try safety
- Email sending is optional + never blocks API
- Budget target guards (no 500s when DB missing/columns differ)
- Debug endpoint to confirm CSV loaded + available columns
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
from flask_cors import CORS, cross_origin
from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_mail import Mail, Message
from werkzeug.exceptions import HTTPException

# ----------------------------------------------------
# APP INIT
# ----------------------------------------------------
app = Flask(__name__, static_folder="public")
app.url_map.strict_slashes = False

def _norm_origin(o: str) -> str:
    return (o or "").strip().lower().rstrip("/")

FRONTEND_URL = _norm_origin(os.environ.get("FRONTEND_URL", "https://momentumscout.netlify.app"))

ALLOWED_ORIGINS = {
    FRONTEND_URL,
    "https://momentumscout.netlify.app",
    "https://momentum-ai-io.netlify.app",
    "https://momentumscout.com",
    "https://www.momentumscout.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
}

ALLOWED_ORIGINS = {_norm_origin(o) for o in ALLOWED_ORIGINS if o}

# ----------------------------------------------------
# ROBUST CORS HANDLER
# ----------------------------------------------------



@app.after_request
def add_cors_headers(resp):
    origin = request.headers.get("Origin")
    if origin and _norm_origin(origin) in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin  # ✅ REQUIRED
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Accept,Authorization,X-Requested-With"
    return resp



def cors_preflight_204():
    resp = make_response("", 204)
    origin = request.headers.get("Origin")
    if origin and _norm_origin(origin) in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"

        # Echo requested method / headers if provided (very robust)
        req_method = request.headers.get("Access-Control-Request-Method", "GET,POST,OPTIONS")
        req_headers = request.headers.get("Access-Control-Request-Headers")

        resp.headers["Access-Control-Allow-Methods"] = req_method
        if req_headers:
            resp.headers["Access-Control-Allow-Headers"] = req_headers
        else:
            resp.headers["Access-Control-Allow-Headers"] = (
                "Content-Type,Accept,Authorization,X-Requested-With"
            )

    return resp





# ❌ REMOVE this (don’t keep both)
# @app.before_request
# def handle_options():
#     if request.method == "OPTIONS":
#         return make_response("", 204)



@app.errorhandler(Exception)
def handle_global_error(e):
    print("🔥 Backend Error Detected:", str(e))
    traceback.print_exc()

    code = 500
    if isinstance(e, HTTPException):
        code = e.code

    resp = make_response(jsonify({
        "success": False,
        "error": str(e),
        "message": "Internal Server Error - Check logs"
    }), code)

    # 🔐 Ensure CORS headers even on errors
    origin = request.headers.get("Origin")
    if origin and _norm_origin(origin) in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        # Echo back requested headers if present (more robust)
        req_headers = request.headers.get("Access-Control-Request-Headers")
        if req_headers:
            resp.headers["Access-Control-Allow-Headers"] = req_headers
        else:
            resp.headers["Access-Control-Allow-Headers"] = (
                "Content-Type,Accept,Authorization,X-Requested-With"
            )

    return resp




# ----------------------------------------------------
# EMAIL CONFIG (OPTIONAL; NEVER BREAK API)
# ----------------------------------------------------
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.hostinger.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = (os.environ.get("MAIL_USE_TLS", "True") == "True")
app.config["MAIL_USE_SSL"] = (os.environ.get("MAIL_USE_SSL", "False") == "True")
app.config["MAIL_USERNAME"] = MAIL_USERNAME
app.config["MAIL_PASSWORD"] = MAIL_PASSWORD
app.config["MAIL_TIMEOUT"] = int(os.environ.get("MAIL_TIMEOUT", "10"))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit


# Default sender must be a valid email string
if MAIL_USERNAME:
    app.config["MAIL_DEFAULT_SENDER"] = ("MomentumScout Intelligence", MAIL_USERNAME)
else:
    app.config["MAIL_DEFAULT_SENDER"] = ("MomentumScout Intelligence", "info@momentumscout.com")
    print("⚠️ MAIL_USERNAME not set; using fallback sender info@momentumscout.com", flush=True)

mail = Mail(app)

def _send_emails_bg(app, internal_msg, customer_msg):
    """Send emails in background so endpoint never blocks."""
    # If creds missing, just skip
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print("⚠️ Email skipped: missing MAIL_USERNAME or MAIL_PASSWORD", flush=True)
        return

    with app.app_context():
        try:
            mail.send(internal_msg)
            print("✅ Internal email sent", flush=True)
        except Exception as e:
            print("❌ Internal email failed:", repr(e), flush=True)
            traceback.print_exc()

        try:
            mail.send(customer_msg)
            print("✅ Customer email sent", flush=True)
        except Exception as e:
            print("❌ Customer email failed:", repr(e), flush=True)
            traceback.print_exc()


# ----------------------------------------------------
# BASIC HEALTHCHECK
# ----------------------------------------------------
@app.route("/api/ping", methods=["GET", "OPTIONS"])
def api_ping():
    return jsonify({"ok": True}), 200



# ----------------------------------------------------
# CONSTANTS / CONFIG
# ----------------------------------------------------
# 1. Get the directory where app.py actually lives
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Try several common locations for the 'data' folder relative to app.py
potential_paths = [
    os.path.join(BASE_DIR, 'data'),
    os.path.join(BASE_DIR, '..', 'data'),
    os.path.join(os.getcwd(), 'data'),
    "/opt/render/project/src/pro_version/MomentumAIbackend/data",
    "/opt/render/project/src/data"
]

DATA_FOLDER_PATH = None
for p in potential_paths:
    if os.path.exists(p):
        DATA_FOLDER_PATH = os.path.abspath(p)
        print(f"✅ FOUND DATA FOLDER AT: {DATA_FOLDER_PATH}")
        break

if not DATA_FOLDER_PATH:
    # Fallback to current directory if nothing found
    DATA_FOLDER_PATH = os.path.join(os.getcwd(), 'data')
    print(f"⚠️ WARNING: Data folder not found, defaulting to: {DATA_FOLDER_PATH}")



DATA_FILENAME_BASE = os.environ.get("DATA_FILENAME_BASE", "FC26_MomentumScout.csv")
DATA_FILENAME_BALLER = os.environ.get("DATA_FILENAME_BALLER", "baller_league_uk.xlsx")
DATA_FILENAME_NEXT_MATCH = os.environ.get("DATA_FILENAME_NEXT_MATCH", "baller_next_match.xlsx")

DATA_FILENAME_SIGNUPS = "signups.csv"
DATA_FILENAME_AUDIT = "analyst_usage.csv"

GOOGLE_SCRIPT_URL = (os.environ.get("GOOGLE_SCRIPT_URL") or "").strip()

ACCESS_CODES = {"club": "SCOUT2025", "baller": "BALLER2025"}

ENTITLEMENTS_MAP = {
    "Agent": {"analyst_ai": False, "export_csv": False},
    "Tier 3": {"analyst_ai": False, "export_csv": False},
    "Tier 2": {"analyst_ai": "yearly_only", "export_csv": True},
    "Tier 1": {"analyst_ai": True, "export_csv": True},
    "Baller League": {"analyst_ai": False, "export_csv": False},
    "Admin": {"analyst_ai": True, "export_csv": True},
}

OWNER_EMAILS = {"info@momentumscout.com", "info@fizmaygroup.com", "fisayo.s19@gmail.com"}
def is_owner(email: str) -> bool:
    return (email or "").strip().lower() in OWNER_EMAILS


# Global data
player_data_base = None
player_data_baller = None
next_match_data = None
POS_BENCHMARKS = {}


# ----------------------------------------------------
# WEIGHTS & DRILLS (unchanged)
# ----------------------------------------------------
DRILL_DATABASE = {
    "pace": "Speed ladders and resistance sprint training.",
    "shooting": "1v1 finishing drills and shot placement practice.",
    "passing": "Rondo drills (5v2) and long-range switch play.",
    "dribbling": "Cone weaving and close-control box drills.",
    "defending": "Shadow defending and timing interception drills.",
    "physic": "Core strength conditioning and shielding practice.",
    "goals": "Finishing under pressure and rebound anticipation.",
    "assists": "Vision training and final-third crossing drills.",
    "tackles": "1v1 defensive duels and slide tackle timing.",
    "saves": "Reaction reflex training and positioning drills.",
    "mentality_vision": "Video analysis of passing lanes and scanning drills.",
    "defending_standing_tackle": "Jockeying and block tackle technique.",
}

POSITION_WEIGHTS = {
    "GK": {"goalkeeping_diving": 20, "goalkeeping_handling": 20, "goalkeeping_kicking": 20, "goalkeeping_positioning": 20, "goalkeeping_reflexes": 20},
    "CB": {"defending_standing_tackle": 30, "defending_marking_awareness": 20, "power_strength": 15, "mentality_interceptions": 15, "pace": 10},
    "LB": {"pace": 35, "defending_standing_tackle": 20, "attacking_crossing": 15, "power_stamina": 15, "dribbling": 15},
    "RB": {"pace": 35, "defending_standing_tackle": 20, "attacking_crossing": 15, "power_stamina": 15, "dribbling": 15},
    "CDM": {"mentality_interceptions": 25, "defending_standing_tackle": 20, "power_strength": 15, "passing": 15},
    "CM": {"passing": 25, "dribbling": 20, "mentality_vision": 20, "power_stamina": 15, "shooting": 10},
    "CAM": {"mentality_vision": 25, "passing": 25, "dribbling": 20, "shooting": 15, "pace": 10},
    "LW": {"pace": 30, "dribbling": 25, "shooting": 20, "attacking_crossing": 15},
    "RW": {"pace": 30, "dribbling": 25, "shooting": 20, "attacking_crossing": 15},
    "ST": {"attacking_finishing": 30, "mentality_positioning": 25, "power_shot_power": 15, "pace": 15, "power_strength": 10},
    "CF": {"attacking_finishing": 25, "mentality_vision": 20, "dribbling": 20, "passing": 15, "pace": 10},
}

BALLER_WEIGHTS = {
    "ALL": {"goals": 10, "assists": 10, "tackles": 10, "total_saves": 10},
    "FWD": {"goals": 30, "total_shots": 20, "xg_per_90": 20},
    "MID": {"assists": 30, "pass_accuracy": 20, "interceptions": 15},
    "DEF": {"tackles": 30, "clearances": 20, "interceptions": 25},
    "GK": {"total_saves": 30, "clean_sheets": 30},
}

POSITION_MAP = {
    "FWD": "ST", "MID": "CM", "DEF": "CB", "GOALKEEPER": "GK", "FORWARD": "ST",
    "ST": "ST", "CM": "CM", "CB": "CB", "GK": "GK",
}

# ----------------------------------------------------
# HELPERS
# ----------------------------------------------------
def make_access_code(length=8):
    return secrets.token_urlsafe(6).upper().replace("-", "")[:length]

def safe_int(val, default=0):
    try:
        if pd.isna(val) or val == "" or val is None:
            return default
        s_val = str(val).strip()
        for char in ["+", "-", " "]:
            if char in s_val:
                s_val = s_val.split(char)[0]
        return int(float(s_val))
    except Exception:
        return default

def safe_float(val, default=0.0):
    try:
        return float(val) if pd.notnull(val) else default
    except Exception:
        return default

def clean_column_name(col_name):
    s = str(col_name).strip().lower()
    s = s.replace(" ", "_").replace(".", "").replace("%", "_pct")
    s = s.replace("/", "_").replace("(", "").replace(")", "")
    return s



# ----------------------------------------------------
# USER MANAGEMENT (CSV)
# ----------------------------------------------------
def save_signup(data):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp):
        pd.DataFrame(columns=["fullName", "email", "organization", "role", "tier", "plan", "timestamp", "access_code"]).to_csv(fp, index=False)

    email_norm = (data.get("email") or "").strip().lower()
    new_row = {
        "fullName": data.get("fullName"),
        "email": email_norm,
        "organization": data.get("organization"),
        "role": data.get("role"),
        "tier": data.get("tier", "Tier 3"),
        "plan": data.get("plan", "monthly"),
        "timestamp": datetime.now().isoformat(),
        "access_code": data.get("access_code", ""),
    }

    try:
        df = pd.read_csv(fp)
        if "email" in df.columns and email_norm in df["email"].astype(str).values:
            df.loc[df["email"].astype(str) == email_norm, list(new_row.keys())] = pd.Series(new_row)
            df.to_csv(fp, index=False)
        else:
            pd.DataFrame([new_row]).to_csv(fp, mode="a", header=False, index=False)
        return True
    except Exception as e:
        print("Save signup failed:", repr(e), flush=True)
        traceback.print_exc()
        return False

def check_login_status(email: str):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp):
        return False, "No users found.", {}

    try:
        df = pd.read_csv(fp)
        if "email" not in df.columns:
            return False, "System Error: signups.csv missing 'email' column.", {}

        user_row = df[df["email"].astype(str).str.strip().str.lower() == email.strip().lower()]
        if user_row.empty:
            return False, "Email not recognized.", {}

        user = user_row.iloc[-1]
        tier = user.get("tier", "Tier 3")
        plan = user.get("plan", "monthly")
        signup_str = user.get("timestamp", datetime.now().isoformat())

        # 14-day trial check
        try:
            signup_date = pd.to_datetime(signup_str).to_pydatetime()
            days_since = (datetime.now() - signup_date).days
            is_exempt = (tier == "Tier 1") or (plan == "yearly")
            if days_since > 14 and not is_exempt:
                return False, "Trial Expired (14 Days). Upgrade required.", {}
        except Exception:
            pass

        config = ENTITLEMENTS_MAP.get(tier, ENTITLEMENTS_MAP["Tier 3"])

        analyst_access = False
        raw_access = config.get("analyst_ai", False)
        if raw_access is True:
            analyst_access = True
        elif raw_access == "yearly_only" and plan == "yearly":
            analyst_access = True

        export_access = bool(config.get("export_csv", False))

        return True, "Login Verified", {
            "tier": tier,
            "plan": plan,
            "analyst_ai": analyst_access,
            "export_csv": export_access,
        }

    except Exception as e:
        print("check_login_status error:", repr(e), flush=True)
        traceback.print_exc()
        return False, f"System Error: {str(e)}", {}

def log_analyst_usage(email, player_name):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_AUDIT)
    if not os.path.exists(fp):
        pd.DataFrame(columns=["email", "player", "timestamp"]).to_csv(fp, index=False)
    try:
        new_row = {"email": email, "player": player_name, "timestamp": datetime.now().isoformat()}
        pd.DataFrame([new_row]).to_csv(fp, mode="a", header=False, index=False)
    except Exception:
        pass


# ----------------------------------------------------
# AI / DATA HELPERS (unchanged)
# ----------------------------------------------------
def generate_training_plan(row, position, is_baller=False):
    plan = {"weakness": "General Conditioning", "drills": ["Standard fitness regime", "Tactical positioning review"]}
    if is_baller:
        weights = BALLER_WEIGHTS.get(position, BALLER_WEIGHTS["ALL"])
    else:
        weights = POSITION_WEIGHTS.get(position, POSITION_WEIGHTS.get("CM", {}))
    if not weights:
        return plan

    lowest_attr, lowest_val = None, 1e9
    for attr in weights.keys():
        val = safe_float(row.get(attr, 0))
        if is_baller and val < 20:
            val = val * 5
        if val < lowest_val:
            lowest_val, lowest_attr = val, attr

    if lowest_attr:
        drill = DRILL_DATABASE.get(lowest_attr, "General technical drills.")
        readable_attr = lowest_attr.replace("_", " ").title()
        plan["weakness"] = f"Improve {readable_attr} (Current: {int(lowest_val)})"
        plan["drills"] = [f"Primary: {drill}", f"Secondary: High-intensity {readable_attr} simulations."]
    return plan

def generate_heatmap_data(row, position):
    zones = {"box": 10, "wide": 10, "mid": 10, "def": 10}
    if position in ["ST", "CF", "FWD"]:
        zones.update({"box": 90, "wide": 40, "mid": 30, "def": 5})
    elif position in ["RW", "LW"]:
        zones.update({"box": 60, "wide": 95, "mid": 40, "def": 20})
    elif position in ["CAM", "CM", "MID"]:
        zones.update({"box": 40, "wide": 30, "mid": 95, "def": 40})
    elif position in ["CDM"]:
        zones.update({"box": 15, "wide": 20, "mid": 80, "def": 80})
    elif position in ["CB", "DEF"]:
        zones.update({"box": 5, "wide": 10, "mid": 30, "def": 95})
    elif position in ["LB", "RB"]:
        zones.update({"box": 10, "wide": 85, "mid": 50, "def": 80})
    elif position == "GK":
        zones.update({"box": 100, "wide": 0, "mid": 0, "def": 100})

    shooting = safe_float(row.get("shooting", 0))
    if shooting > 80:
        zones["box"] += 10
    pace = safe_float(row.get("pace", 0))
    if pace > 85:
        zones["wide"] += 10

    return {k: min(100, v) for k, v in zones.items()}

def compute_score_for_player(row, position="CM", user_weights=None, is_baller=False):
    if is_baller:
        weights = BALLER_WEIGHTS.get(position, BALLER_WEIGHTS["ALL"]).copy()
        if user_weights:
            weights.update(user_weights)
        total_w = sum(weights.values()) or 1
        score = 0.0
        for attr, weight in weights.items():
            val = safe_float(row.get(attr), 0.0)
            norm_val = min(100, val * 5) if "pct" not in attr else val
            score += (norm_val / 100.0) * (weight / total_w)
        return round(score * 100, 2)

    weights = POSITION_WEIGHTS.get(position, POSITION_WEIGHTS.get("CM", {})).copy()
    if user_weights:
        weights.update(user_weights)
    total_w = sum(weights.values()) or 1
    score = 0.0
    for attr, weight in weights.items():
        val = safe_float(row.get(attr), 0.0)
        score += (val / 100.0) * (weight / total_w)
    return round(score * 100, 2)

def years_to_project(age: int) -> int:
    if age <= 20:
        return 5
    if 21 <= age <= 25:
        return 4
    if 26 <= age <= 30:
        return 3
    return 2

def project_player(row, years=3):
    ovr = int(row.get("overall", 0) or 0)
    value = float(row.get("value_eur", 0) or 0)
    age = safe_int(row.get("age", 21))
    projections = []
    for y in range(1, years + 1):
        if age + y > 29:
            ovr = max(0, ovr - 1)
            value = value * 0.9
        else:
            value = value * 1.1
            ovr += 1
        projections.append({"year": y, "projected_value_eur": int(value), "projected_overall": ovr})
    return projections

def _infer_position_col(df: pd.DataFrame) -> str:
    for c in [
        "club_position", "position", "pos", "primary_position",
        "player_position", "role", "positions"
    ]:
        if c in df.columns:
            return c
    return ""

def json_sanitize(x):
    """Recursively replace NaN/Infinity with None so jsonify can produce valid JSON."""
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    if isinstance(x, (np.floating,)):
        xf = float(x)
        if math.isnan(xf) or math.isinf(xf):
            return None
        return xf
    if isinstance(x, dict):
        return {k: json_sanitize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_sanitize(v) for v in x]
    return x



def _normalize_positions(series: pd.Series) -> pd.Series:
    def _map_one(x):
        s = str(x).strip().upper()
        return POSITION_MAP.get(s, s if s in POSITION_WEIGHTS else "CM")
    return series.map(_map_one)

def _archetype_from_gaps(position: str, weakest_attrs: list) -> str:
    w = set(weakest_attrs)
    if position in ["CB"]:
        if "pace" in w and "defending_standing_tackle" in w:
            return "Recovery CB (fast, strong in duels)"
        if "passing" in w or "mentality_vision" in w:
            return "Ball-Playing CB (progressive passer)"
        return "Dominant Stopper (duels, positioning)"
    if position in ["LB", "RB"]:
        if "pace" in w and "power_stamina" in w:
            return "High-Engine Fullback (overlaps all game)"
        if "attacking_crossing" in w:
            return "Creative Fullback (final-third delivery)"
        return "Defensive Fullback (1v1, positioning)"
    if position in ["CDM"]:
        if "mentality_interceptions" in w or "defending_standing_tackle" in w:
            return "Ball-Winning #6 (screen + recoveries)"
        if "passing" in w:
            return "Deep-Lying Playmaker (build-up controller)"
        return "Hybrid #6 (duels + circulation)"
    if position in ["CM"]:
        if "power_stamina" in w:
            return "Box-to-Box (engine + pressure resistance)"
        if "passing" in w or "mentality_vision" in w:
            return "Tempo Setter (progression + chance creation)"
        return "All-round CM (balance + retention)"
    if position in ["CAM"]:
        if "shooting" in w:
            return "Chance Creator 10 (final pass priority)"
        if "passing" in w or "mentality_vision" in w:
            return "Creative 10 (through balls + linking)"
        return "Attacking 10 (arrivals + box threat)"
    if position in ["LW", "RW"]:
        if "pace" in w and "dribbling" in w:
            return "1v1 Winger (isolation + beating man)"
        if "shooting" in w:
            return "Inside Forward (goal threat)"
        return "Wide Creator (crossing + combinations)"
    if position in ["ST", "CF"]:
        if "attacking_finishing" in w:
            return "Clinical Finisher (conversion boost)"
        if "pace" in w:
            return "Run-in-behind Striker (depth threat)"
        return "Complete Forward (link + finish)"
    if position == "GK":
        return "Shot-stopper GK (reflexes + handling)"
    return "Role Upgrade"


        # elite = top 10% overall within position (fallback to full pool if missing)
def build_position_benchmarks(df: pd.DataFrame):
    """
    Pre-compute average and elite benchmarks per position.
    Used by squad gap analysis and player analytics.
    """
    global POS_BENCHMARKS
    POS_BENCHMARKS = {}

    if df is None or df.empty or "club_position" not in df.columns:
        return

    core = [
        "pace", "shooting", "passing", "dribbling",
        "defending", "physic", "overall", "value_eur"
    ]

    for pos in POSITION_WEIGHTS.keys():
        pool = df[df["club_position"].astype(str).str.upper() == pos]

        if pool.empty:
            continue

        # -----------------------------
        # Elite = top 10% by overall
        # -----------------------------
        elite = pool
        if "overall" in pool.columns:
            overall_num = pd.to_numeric(pool["overall"], errors="coerce").fillna(0)
            thr = float(overall_num.quantile(0.90))

            elite_candidate = pool[overall_num >= thr]
            elite = elite_candidate if not elite_candidate.empty else pool

        # -----------------------------
        # Helper to compute averages
        # -----------------------------
        def avg_of(frame: pd.DataFrame):
            out = {}
            for k in core:
                if k in frame.columns:
                    out[k] = float(
                        pd.to_numeric(frame[k], errors="coerce")
                        .fillna(0)
                        .mean()
                    )
            return out

        # -----------------------------
        # Store benchmarks
        # -----------------------------
        POS_BENCHMARKS[pos] = {
            "avg": avg_of(pool),
            "elite": avg_of(elite),
        }



# ----------------------------------------------------
# DATA LOADERS
# ----------------------------------------------------
def _load_baller_league_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp):
        return pd.DataFrame()
    try:
        sheets = pd.read_excel(fp, sheet_name=None)
        merged = None
        for _, df in sheets.items():
            df.columns = [clean_column_name(c) for c in df.columns]
            if "name" not in df.columns:
                continue
            merged = df if merged is None else pd.merge(merged, df, on=["name"], how="outer", suffixes=("", "_dup"))

        if merged is None:
            return pd.DataFrame()

        merged = merged.fillna(0)
        merged["short_name"] = merged.get("name", "Unknown")

        raw_pos = merged.get("position", merged.get("pos", "Baller"))
        if isinstance(raw_pos, pd.Series):
            merged["club_position"] = raw_pos.map(lambda x: POSITION_MAP.get(str(x).upper(), "Baller"))
        else:
            merged["club_position"] = "Baller"

        goals = pd.to_numeric(merged.get("goals", 0), errors="coerce").fillna(0)
        assists = pd.to_numeric(merged.get("assists", 0), errors="coerce").fillna(0)
        tackles = pd.to_numeric(merged.get("tackles", 0), errors="coerce").fillna(0)

        if "momentum_score" not in merged.columns:
            merged["momentum_score"] = (goals * 5) + (assists * 3) + (tackles * 2)

        merged["overall"] = 50 + (merged["momentum_score"].clip(upper=50)).astype(int)
        merged["potential"] = merged["overall"] + 5
        merged["value_eur"] = merged["momentum_score"] * 10000
        return merged
    except Exception:
        traceback.print_exc()
        return pd.DataFrame()

def _load_next_match_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp):
        return None
    try:
        df = pd.read_excel(fp)
        df.columns = [clean_column_name(c) for c in df.columns]
        return df.iloc[0].to_dict() if not df.empty else None
    except Exception:
        traceback.print_exc()
        return None

def _load_fc26_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp):
        print(f"⚠️ FC26 data file not found at: {fp}", flush=True)
        return pd.DataFrame()

    try:
        df = pd.read_csv(fp, encoding="utf-8-sig") if filename.endswith(".csv") else pd.read_excel(fp)
    except Exception:
        traceback.print_exc()
        return pd.DataFrame()

    df.columns = [clean_column_name(c) for c in df.columns]

    for col in df.columns:
        if any(x in col for x in ["overall", "value", "wage", "pace", "shooting", "passing", "dribbling", "defending", "physic", "age", "contract"]):
            try:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            except Exception:
                pass

    if "sofifa_id" in df.columns:
        def get_face_url(x):
            try:
                if pd.isna(x):
                    return ""
                val = int(x)
                return f"https://cdn.sofifa.net/players/{val//1000:03d}/{val%1000:03d}/24.webp"
            except Exception:
                return ""
        df["player_face_url"] = df["sofifa_id"].apply(get_face_url)

    return df


def initialize_app():
    global player_data_base, player_data_baller, next_match_data
    player_data_base = _load_fc26_data(DATA_FILENAME_BASE)
    player_data_baller = _load_baller_league_data(DATA_FILENAME_BALLER)
    next_match_data = _load_next_match_data(DATA_FILENAME_NEXT_MATCH)
    build_position_benchmarks(player_data_base)


    print(f"📦 DATA_FOLDER_PATH = {DATA_FOLDER_PATH}", flush=True)
    print(f"📦 BASE rows={len(player_data_base) if player_data_base is not None else 'None'}", flush=True)
    print(f"📦 BALLER rows={len(player_data_baller) if player_data_baller is not None else 'None'}", flush=True)

    # Ensure admin exists in signups.csv
    admin_email = "info@momentumscout.com"
    is_valid, _, _ = check_login_status(admin_email)
    if not is_valid:
        save_signup({
            "fullName": "Admin",
            "email": admin_email,
            "organization": "Admin",
            "role": "Admin",
            "tier": "Tier 1",
            "plan": "yearly",
        })    

def _clean_json(v):
    # prevents NaN causing "Unexpected token N" in frontend JSON parse
    try:
        if v is None:
            return 0
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return 0
        if pd.isna(v):
            return 0
        return v
    except Exception:
        return 0

def _sanitize_dict(d):
    return {k: _clean_json(v) for k, v in (d or {}).items()}

def _num(row, key, default=0.0):
    try:
        v = row.get(key, default)
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default

def compute_fit_score(row, position="CM", club_style="balanced"):
    """
    club_style: balanced / high_press / counter / possession
    """
    pos = (position or row.get("club_position") or "CM").upper()
    weights = POSITION_WEIGHTS.get(pos, POSITION_WEIGHTS.get("CM", {})).copy()

    # light style tweaks (fast to demo)
    style = (club_style or "balanced").lower()
    if style == "high_press":
        weights["power_stamina"] = weights.get("power_stamina", 0) + 10
        weights["pace"] = weights.get("pace", 0) + 5
    elif style == "counter":
        weights["pace"] = weights.get("pace", 0) + 10
        weights["dribbling"] = weights.get("dribbling", 0) + 5
    elif style == "possession":
        weights["passing"] = weights.get("passing", 0) + 10
        weights["mentality_vision"] = weights.get("mentality_vision", 0) + 5

    total_w = sum(weights.values()) or 1
    score = 0.0
    for attr, w in weights.items():
        val = _num(row, attr, 0.0)
        score += (val / 100.0) * (w / total_w)

    # small age bonus for development window
    age = safe_int(row.get("age"), 23)
    if age <= 23:
        score += 0.03
    elif age >= 30:
        score -= 0.03

    return int(max(0, min(100, round(score * 100))))

def compute_momentum_index(row):
    """
    Quick trajectory proxy:
    - potential gap (potential - overall)
    - value signal vs overall
    - age curve
    Returns -100..+100 (we’ll cap it visually)
    """
    ovr = safe_int(row.get("overall"), 0)
    pot = safe_int(row.get("potential"), ovr)
    gap = pot - ovr

    value = _num(row, "value_eur", 0.0)
    age = safe_int(row.get("age"), 23)

    # cheap normalization
    value_signal = 0
    if value > 0 and ovr > 0:
        value_signal = min(20, max(-20, int((math.log10(value + 1) - 6) * 10)))  # rough

    age_signal = 0
    if age <= 23:
        age_signal = 15
    elif 24 <= age <= 27:
        age_signal = 8
    elif 28 <= age <= 30:
        age_signal = 0
    else:
        age_signal = -12

    momentum = (gap * 6) + value_signal + age_signal
    momentum = int(max(-100, min(100, momentum)))

    label = "Rising" if momentum >= 20 else "Stable" if momentum >= -10 else "Declining"
    arrow = "up" if momentum >= 20 else "flat" if momentum >= -10 else "down"
    return {"score": momentum, "label": label, "arrow": arrow}

def compute_risk_profile(row):
    """
    Includes simple injury risk proxy:
    - age
    - stamina / physic (if available)
    - (optional) injury_proneness if present
    """
    age = safe_int(row.get("age"), 23)
    stamina = _num(row, "power_stamina", _num(row, "stamina", 50))
    physic = _num(row, "physic", 50)
    injury_prone = _num(row, "injury_proneness", 50)  # only if exists

    # Injury risk proxy (0..100)
    injury_risk = 30
    injury_risk += max(0, (age - 27) * 3)
    injury_risk += max(0, (55 - stamina) * 0.8)
    injury_risk += max(0, (55 - physic) * 0.6)
    injury_risk += max(0, (injury_prone - 50) * 0.6)
    injury_risk = int(max(0, min(100, injury_risk)))

    # Contract risk
    end_year = safe_int(row.get("club_contract_valid_until_year"), 0)
    contract_risk = 20
    if end_year:
        years_left = end_year - datetime.now().year
        if years_left <= 1:
            contract_risk = 65
        elif years_left == 2:
            contract_risk = 45
        else:
            contract_risk = 20

    # Wage risk (high wage for low overall)
    wage_weekly = _num(row, "wage_eur", 0.0)
    ovr = safe_int(row.get("overall"), 0)
    wage_risk = 15
    if wage_weekly > 0 and ovr > 0:
        yearly = wage_weekly * 52
        if yearly > 3000000 and ovr < 80:
            wage_risk = 70
        elif yearly > 1500000 and ovr < 78:
            wage_risk = 55
        elif yearly > 1000000 and ovr < 75:
            wage_risk = 45

    def _band(x):
        return "Low" if x < 35 else "Medium" if x < 65 else "High"

    suggestions = []
    if injury_risk >= 65:
        suggestions.append("Limit minutes early; add recovery micro-cycles + strength maintenance.")
    if wage_risk >= 55:
        suggestions.append("Negotiate wage-to-role alignment; consider performance incentives.")
    if contract_risk >= 60:
        suggestions.append("Move quickly—short contract window increases competition & price volatility.")
    if not suggestions:
        suggestions.append("Low overall risk—focus on tactical onboarding and role clarity.")

    return {
        "injury_risk": injury_risk,
        "contract_risk": int(contract_risk),
        "wage_risk": int(wage_risk),
        "injury_band": _band(injury_risk),
        "contract_band": _band(contract_risk),
        "wage_band": _band(wage_risk),
        "ai_suggestions": suggestions[:3],
    }

def compute_deal_intel(row):
    value = safe_int(row.get("value_eur"), 0)
    release = safe_int(row.get("release_clause_eur"), 0)
    end_year = safe_int(row.get("club_contract_valid_until_year"), 0)

    leverage = "Unknown"
    if end_year:
        years_left = end_year - datetime.now().year
        leverage = "High" if years_left <= 1 else "Medium" if years_left <= 2 else "Low"

    clause_note = "No clause data"
    if release > 0 and value > 0:
        ratio = release / max(1, value)
        if ratio <= 1.2:
            clause_note = "Release clause close to market value (opportunity)"
        elif ratio >= 2.0:
            clause_note = "Release clause very high (negotiation needed)"
        else:
            clause_note = "Release clause reasonable vs value"

    return {
        "contract_end_year": end_year,
        "leverage": leverage,
        "release_clause_eur": release,
        "clause_note": clause_note,
    }

def _feature_vector(row, keys):
    vec = []
    for k in keys:
        vec.append(float(_num(row, k, 0.0)))
    return np.array(vec, dtype=float)

def find_similar_players(df, row, top_n=5):
    # Keep keys small & reliable for speed
    keys = [k for k in ["pace", "shooting", "passing", "dribbling", "defending", "physic", "overall"] if k in df.columns]
    if not keys or df is None or df.empty:
        return []

    target = _feature_vector(row, keys)
    norm_t = np.linalg.norm(target) or 1.0

    sims = []
    for idx, r in df.iterrows():
        v = _feature_vector(r, keys)
        norm_v = np.linalg.norm(v) or 1.0
        sim = float(np.dot(target, v) / (norm_t * norm_v))
        sims.append((sim, idx))

    sims.sort(reverse=True, key=lambda x: x[0])

    out = []
    for sim, idx in sims[:top_n + 1]:  # +1 because first may be itself
        r = df.loc[idx]
        name = r.get("short_name") or r.get("name") or "Unknown"
        if str(name) == str(row.get("short_name") or row.get("name")):
            continue
        out.append({
            "short_name": str(name),
            "club_position": r.get("club_position", "CM"),
            "overall": safe_int(r.get("overall"), 0),
            "value_eur": safe_int(r.get("value_eur"), 0),
            "similarity": round(sim, 3),
        })
        if len(out) >= top_n:
            break
    return out



# ----------------------------------------------------
# ROUTES
# ----------------------------------------------------
@app.route("/", methods=["GET", "OPTIONS"])
def health():
    return (jsonify({"status": "online"}))

@app.route("/api/squad_gap_analysis", methods=["POST", "OPTIONS"])
@app.route("/api/squad_gap_analysis/", methods=["POST", "OPTIONS"])
def api_squad_gap_analysis():
    try:
        payload = request.get_json(silent=True) or {}
        csv_text = payload.get("csv_text") or payload.get("csv_data") or ""
        scope_pos = str(payload.get("position") or "ALL").upper().strip()
        club_style = str(payload.get("club_style") or "balanced").strip().lower()

        if not str(csv_text).strip():
            return jsonify({
                "success": False,
                "message": "csv_text is required.",
                "positions_found": [],
                "report": []
            }), 200

        # -----------------------------
        # Parse CSV
        # -----------------------------
        try:
            csv_text_clean = csv_text.lstrip("\ufeff")
            squad_df = pd.read_csv(
                StringIO(csv_text_clean),
                sep=None,
                engine="python",
                on_bad_lines="skip"
            )
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Could not parse CSV: {str(e)}",
                "positions_found": [],
                "report": []
            }), 200

        if squad_df is None or squad_df.empty:
            return jsonify({
                "success": False,
                "message": "CSV is empty.",
                "positions_found": [],
                "report": []
            }), 200

        squad_df.columns = [clean_column_name(c) for c in squad_df.columns]

        pos_col = _infer_position_col(squad_df) or ""
        if pos_col:
            squad_df[pos_col] = _normalize_positions(squad_df[pos_col])

        # Numeric coercion
        numeric_cols = [
            "pace", "shooting", "passing", "dribbling", "defending", "physic",
            "overall", "potential", "age",
            "power_stamina", "power_strength",
            "mentality_vision", "mentality_interceptions",
            "defending_standing_tackle", "defending_marking_awareness",
            "attacking_finishing", "attacking_crossing",
        ]
        for c in numeric_cols:
            if c in squad_df.columns:
                squad_df[c] = pd.to_numeric(squad_df[c], errors="coerce").fillna(0)

        if player_data_base is None or player_data_base.empty:
            return jsonify({
                "success": False,
                "message": "Server database not loaded.",
                "positions_found": [],
                "report": []
            }), 200

        db = player_data_base.copy()

        if pos_col:
            positions_found = sorted({
                str(x).upper()
                for x in squad_df[pos_col].dropna().unique()
                if str(x).strip()
            })
        else:
            positions_found = []


        report = []
        if scope_pos != "ALL":
            positions_to_analyze = [scope_pos]
        else:
            positions_to_analyze = positions_found if positions_found else ["CM"]

            

        # =====================================================
        # POSITION LOOP
        # =====================================================
        for pos in positions_to_analyze:
            pos = (pos or "CM").upper()

            if pos_col:
                squad_slice = squad_df[squad_df[pos_col].astype(str).str.upper() == pos]
                if squad_slice.empty:
                    squad_slice = squad_df
            else:
                squad_slice = squad_df

            weights = POSITION_WEIGHTS.get(pos, POSITION_WEIGHTS.get("CM", {}))
            keys = [k for k in weights.keys() if k in squad_df.columns and k in db.columns]

            if not keys:
                report.append({
                    "position": pos,
                    "squad_score": 0,
                    "elite_score": 0,
                    "archetype": f"{pos} upgrade profile",
                    "gaps": [],
                    "reasoning": ["No comparable stat columns found."],
                    "targets": []
                })
                continue

            try:
                pool = db[db["club_position"].astype(str).str.upper() == pos]
                if pool.empty:
                    pool = db

                overall_num = pd.to_numeric(pool.get("overall", 0), errors="coerce").fillna(0)
                thr = float(overall_num.quantile(0.90))
                elite = pool[overall_num >= thr]
                if elite.empty:
                    elite = pool

                # ---- Scores
                def weighted_score(df_slice):
                    total_w = sum(weights.values()) or 1
                    avg = {
                        k: float(pd.to_numeric(df_slice[k], errors="coerce").fillna(0).mean())
                        for k in keys
                    }
                    score = sum((avg[k] / 100.0) * (weights.get(k, 10) / total_w) for k in keys)
                    return int(max(0, min(100, round(score * 100))))

                squad_score = weighted_score(squad_slice)
                elite_score = weighted_score(elite)

                # ---- Gaps
                gaps = []
                for k in keys:
                    squad_avg = float(pd.to_numeric(squad_slice[k], errors="coerce").fillna(0).mean())
                    elite_avg = float(pd.to_numeric(elite[k], errors="coerce").fillna(0).mean())
                    gap = round(elite_avg - squad_avg, 2)
                    gaps.append([k, int(round(squad_avg)), int(round(elite_avg)), gap])

                gaps.sort(key=lambda x: float(x[3]), reverse=True)
                top_gaps = gaps[:6]
                weakest_attrs = [g[0] for g in top_gaps[:3]]

                archetype = _archetype_from_gaps(pos, weakest_attrs)

                # ---- Candidates
                candidates = pool.copy()
                if "age" in candidates.columns:
                    candidates = candidates[pd.to_numeric(candidates["age"], errors="coerce").fillna(0) <= 32]

                try:
                    candidates["momentum_score"] = candidates.apply(
                        lambda r: compute_score_for_player(r, pos, None, False),
                        axis=1
                    )
                except Exception:
                    candidates["momentum_score"] = 0

                targets_df = candidates.sort_values("momentum_score", ascending=False).head(8)

                targets = []
                for _, row in targets_df.iterrows():
                    try:
                        p = row.to_dict()
                        row_pos = str(p.get("club_position") or pos)

                        age = safe_int(p.get("age"), 23)

                        projections = project_player(row, years=years_to_project(age))
                        bench = POS_BENCHMARKS.get(row_pos, POS_BENCHMARKS.get("CM", {}))

                        fit = compute_fit_score(row, row_pos, club_style)
                        momentum = compute_momentum_index(row)
                        risk = compute_risk_profile(row)

                        targets.append({
                            "short_name": str(p.get("short_name") or p.get("name") or "Unknown"),
                            "club_position": row_pos,
                            "overall": safe_int(p.get("overall"), 0),
                            "value_eur": safe_int(p.get("value_eur"), 0),
                            "player_face_url": str(p.get("player_face_url") or ""),
                            "full_attributes": p,
                            "projections": projections,
                            "benchmarks": bench,
                            "momentum_score": safe_float(p.get("momentum_score"), 0),
                            "fit_score": fit,
                            "momentum": momentum,
                            "risk_profile": risk,
                            "similar_players": []
                        })

                    except Exception as player_err:
                        print(f"⚠️ Player skipped: {player_err}")
                        continue

                # ---- Append ONE report per position
                report.append({
                    "position": pos,
                    "squad_score": squad_score,
                    "elite_score": elite_score,
                    "archetype": archetype,
                    "gaps": top_gaps,
                    "reasoning": [
                        "Elite benchmark = top 10% overall in database.",
                        f"Top weaknesses: {', '.join([a.replace('_',' ') for a in weakest_attrs])}."
                    ],
                    "targets": targets
                })

            except Exception as e_pos:
                print(f"⚠️ Position {pos} failed: {e_pos}")
                traceback.print_exc()
                continue

        # ✅ SUCCESS RESPONSE MUST BE OUTSIDE THE LOOP
        payload = {
            "success": True,
            "positions_found": positions_found,
            "report": report
        }
        return jsonify(json_sanitize(payload)), 200

    except Exception as e:
        traceback.print_exc()
        payload = {
            "success": False,
            "message": f"Server error: {str(e)}",
            "positions_found": [],
            "report": []
        }
        return jsonify(json_sanitize(payload)), 200





@app.route("/api/cors_debug", methods=["GET", "OPTIONS"])
def api_cors_debug():
    return (jsonify({
        "origin_raw": request.headers.get("Origin"),
        "origin_norm": _norm_origin(request.headers.get("Origin")),
        "matched": _norm_origin(request.headers.get("Origin")) in ALLOWED_ORIGINS,
        "method": request.method,
    }))


@app.route("/api/sample_player", methods=["GET"])
def api_sample_player():
    if player_data_base is None or player_data_base.empty:
        return jsonify({
            "ok": False,
            "reason": "player_data_base is empty",
            "data_folder_path": DATA_FOLDER_PATH,
            "resolved_path": os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_BASE),
            "file_exists": os.path.exists(os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_BASE)),
        }), 200

    row = player_data_base.iloc[0].to_dict()
    # keep it light
    return jsonify({
        "ok": True,
        "rows": int(len(player_data_base)),
        "sample": {
            "short_name": row.get("short_name"),
            "club_position": row.get("club_position"),
            "overall": row.get("overall"),
            "value_eur": row.get("value_eur"),
        }
    }), 200


@app.route("/api/verify_login", methods=["POST"])
def api_verify_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = str(data.get("code") or "").strip()
    portal = (data.get("portal") or "").strip()

    if is_owner(email):
        return jsonify({
            "success": True,
            "message": "Owner access granted",
            "entitlements": {"tier": "Admin", "plan": "yearly", "analyst_ai": True, "export_csv": True}
        }), 200

    if ACCESS_CODES.get(portal) == code:
        is_valid, msg, entitlements = check_login_status(email)
        if not is_valid:
            return jsonify({"success": False, "message": msg}), 403
        return jsonify({"success": True, "message": msg, "entitlements": entitlements}), 200

    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp):
        return jsonify({"success": False, "message": "No signups found."}), 403

    try:
        df = pd.read_csv(fp)
        match = df[
            (df["email"].astype(str).str.strip().str.lower() == email)
            & (df["access_code"].astype(str).str.strip() == code)
        ]
        if match.empty:
            return jsonify({"success": False, "message": "Invalid access code or email."}), 403

        user = match.iloc[-1]
        tier = user.get("tier", "Tier 3")
        plan = user.get("plan", "monthly")

        config = ENTITLEMENTS_MAP.get(tier, ENTITLEMENTS_MAP["Tier 3"])
        analyst_access = False
        raw_access = config.get("analyst_ai", False)
        if raw_access is True:
            analyst_access = True
        elif raw_access == "yearly_only" and plan == "yearly":
            analyst_access = True

        entitlements = {
            "tier": tier,
            "plan": plan,
            "analyst_ai": analyst_access,
            "export_csv": bool(config.get("export_csv", False)),
        }
        return jsonify({"success": True, "message": "Login Verified", "entitlements": entitlements}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"System Error: {str(e)}"}), 500


@app.route("/api/submit_demo", methods=["POST"])
def api_submit_demo():
    if request.method == "OPTIONS":
        return (make_response("", 204))

    try:
        data = request.get_json(silent=True) or {}
        full_name = (data.get("fullName") or "User").strip()
        user_email = (data.get("email") or "").strip().lower()
        org = (data.get("organization") or "N/A").strip()
        role = (data.get("role") or "N/A").strip()

        if not user_email:
            return jsonify({"success": False, "message": "Email required."}), 400

        access_code = make_access_code(8)
        data["access_code"] = access_code

        saved = save_signup(data)
        if not saved:
            print("⚠️ Warning: save_signup returned False", flush=True)

        # ✅ Google Sheets push (best effort)
        if GOOGLE_SCRIPT_URL:
            payload = {
                "fullName": full_name,
                "email": user_email,
                "organization": org,
                "role": role,
                "tier": data.get("tier", "Tier 3"),
                "plan": data.get("plan", "monthly"),
                "access_code": access_code,
            }
            try:
                r = requests.post(GOOGLE_SCRIPT_URL, json=payload, timeout=6)
                print("✅ Google Sheet POST:", r.status_code, flush=True)
            except Exception as e:
                print("⚠️ Google Script POST failed:", repr(e), flush=True)
                traceback.print_exc()

        # ✅ Email (best effort, async)
        try:
            internal_recipient = os.environ.get("INTERNAL_ALERT_EMAIL", "info@momentumscout.com")

            internal_msg = Message(
                subject=f"🔥 New Demo Request: {org} - {full_name}",
                recipients=[internal_recipient],
                body=(
                    f"New Professional Lead Details:\n"
                    f"Name: {full_name}\n"
                    f"Email: {user_email}\n"
                    f"Organization: {org}\n"
                    f"Role: {role}\n"
                    f"Access Code: {access_code}\n"
                ),
            )

            customer_msg = Message(
                subject="Welcome to MomentumScout – Demo Request Received",
                recipients=[user_email],
                body=(
                    f"Dear {full_name},\n\n"
                    "Thank you for requesting a professional demo of MomentumScout.\n\n"
                    f"Your unique access code is: {access_code}\n\n"
                    "Login here: https://momentumscout.netlify.app/login.html\n\n"
                    "Best regards,\n"
                    "MomentumScout Team\n"
                ),
            )

            threading.Thread(
                target=_send_emails_bg,
                args=(app, internal_msg, customer_msg),
                daemon=True,
            ).start()

        except Exception as e:
            print("⚠️ Could not start email thread:", repr(e), flush=True)
            traceback.print_exc()

        return jsonify({"success": True, "message": "Demo request submitted successfully.", "access_code": access_code}), 200

    except Exception as e:
        print("Demo Request Error:", repr(e), flush=True)
        traceback.print_exc()
        return jsonify({"success": False, "message": "Submission error. Please try again."}), 500

@app.route("/api/find_players", methods=["POST", "OPTIONS"])
@app.route("/api/find_players/", methods=["POST", "OPTIONS"])
def api_find_players():
    try:
        data = request.get_json(silent=True) or {}

        is_baller = (data.get("data_source") == "baller")
        df = player_data_baller if is_baller else player_data_base
        if df is None or df.empty:
            return jsonify({"players": [], "error": "Database not loaded"}), 200

        df = df.copy()

        # -----------------------------
        # Filters
        # -----------------------------
        filters = data.get("filters", {}) or {}
        for key, rng in filters.items():
            col = clean_column_name(key)
            if "value" in col:
                col = "value_eur"

            if col in df.columns and isinstance(rng, list) and len(rng) >= 2:
                try:
                    lo = float(rng[0])
                    hi = float(rng[1])
                    df = df[(pd.to_numeric(df[col], errors="coerce").fillna(0) >= lo) &
                            (pd.to_numeric(df[col], errors="coerce").fillna(0) <= hi)]
                except Exception:
                    continue

        # -----------------------------
        # Momentum score (ranking)
        # -----------------------------
        position_req = str(data.get("position", "ALL")).upper().strip()
        weights_req = data.get("weights")

        df["momentum_score"] = df.apply(
            lambda r: compute_score_for_player(r, position_req, weights_req, is_baller),
            axis=1,
        )

        # -----------------------------
        # Build output (top 20)
        # -----------------------------
        out = []
        for _, row in df.sort_values("momentum_score", ascending=False).head(20).iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k, v in p_dict.items()}

            row_pos = p_dict.get("club_position") or row.get("club_position") or "CM"

            tp = generate_training_plan(row, row_pos, is_baller)
            hm = generate_heatmap_data(row, row_pos)
            projections = project_player(row)

            # ✅ NEW 5 sections (safe defaults)
            try:
                fit = compute_fit_score(row, row_pos, data.get("club_style", "balanced"))
            except Exception:
                fit = 0

            try:
                momentum = compute_momentum_index(row)
            except Exception:
                momentum = {"score": 0, "label": "Unknown", "arrow": "flat"}

            try:
                risk = compute_risk_profile(row)
            except Exception:
                risk = {"injury_risk": 0, "notes": [], "ai_suggestion": ""}

            try:
                deal = compute_deal_intel(row)
            except Exception:
                deal = {"contract_end_year": safe_int(row.get("club_contract_valid_until_year"), 0)}

            # optional: similars (can be heavy, but fine for top-20)
            try:
                similars = []  # find_similar_players(df, row, top_n=5)
            except Exception:
                similars = []

            out.append({
                "short_name": p_dict.get("short_name") or p_dict.get("name") or "Unknown",
                "club_position": row_pos,
                "momentum_score": safe_float(p_dict.get("momentum_score"), 0),
                "value_eur": safe_int(p_dict.get("value_eur", 0)),
                "player_face_url": p_dict.get("player_face_url", ""),
                "ai_training": tp,
                "heatmap_zones": hm,
                "full_attributes": p_dict,
                "projections": projections,

                # ✅ added fields
                "fit_score": fit,
                "momentum": momentum,
                "risk_profile": risk,
                "deal_intel": deal,
                "similar_players": similars,
            })

        return jsonify({"players": out}), 200

    except Exception as e:
        traceback.print_exc()
        resp = jsonify({"error": str(e)})
        resp.status_code = 500
        return (resp)

@app.route("/api/search_player", methods=["POST", "OPTIONS"])
@app.route("/api/search_player/", methods=["POST","OPTIONS"])
def api_search_player():
    # ✅ handle preflight safely
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        data = request.get_json(silent=True) or {}   # ✅ changed
        query = str(data.get("player_name", "")).lower().strip()
        is_baller = (data.get("data_source") == "baller")

        df = player_data_baller if is_baller else player_data_base
        if df is None or df.empty or not query:
            return jsonify([]), 200

        # Build mask safely (handles missing cols)
        mask = pd.Series(False, index=df.index)
        if "short_name" in df.columns:
            mask |= df["short_name"].astype(str).str.lower().str.contains(query, na=False)
        if "name" in df.columns:
            mask |= df["name"].astype(str).str.lower().str.contains(query, na=False)

        results = df.loc[mask].head(10)

        out = []
        for _, row in results.iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k, v in p_dict.items()}

            row_pos = p_dict.get("club_position") or row.get("club_position") or "CM"

            # existing analytics
            bench = POS_BENCHMARKS.get(row_pos, POS_BENCHMARKS.get("CM", {}))
            score = compute_score_for_player(row, row_pos, None, is_baller)
            age = safe_int(row.get("age"), 21)
            projections = project_player(row, years_to_project(age))
            tp = generate_training_plan(row, row_pos, is_baller)
            hm = generate_heatmap_data(row, row_pos)

            # ✅ NEW 5 sections (safe defaults if funcs fail / not implemented yet)
            try:
                fit = compute_fit_score(row, row_pos, data.get("club_style", "balanced"))
            except Exception:
                fit = 0

            try:
                momentum = compute_momentum_index(row)
            except Exception:
                momentum = {"score": 0, "label": "Unknown", "arrow": "flat"}

            try:
                risk = compute_risk_profile(row)
            except Exception:
                risk = {"injury_risk": 0, "notes": [], "ai_suggestion": ""}

            try:
                deal = compute_deal_intel(row)
            except Exception:
                deal = {"contract_end_year": safe_int(row.get("club_contract_valid_until_year"), 0)}

            try:
                similars = []  # find_similar_players(df, row, top_n=5)
            except Exception:
                similars = []

            out.append({
                "short_name": p_dict.get("short_name") or p_dict.get("name") or "Unknown",
                "club_position": row_pos,
                "momentum_score": score,
                "projections": projections,
                "ai_training": tp,
                "heatmap_zones": hm,
                "benchmarks": bench,
                "value_eur": safe_int(p_dict.get("value_eur")),
                "full_attributes": p_dict,

                # ✅ added fields
                "fit_score": fit,
                "momentum": momentum,
                "risk_profile": risk,
                "deal_intel": deal,
                "similar_players": similars,
            })

        return jsonify(out), 200

    except Exception as e:
        traceback.print_exc()
        resp = jsonify({"error": str(e)})
        resp.status_code = 500
        return (resp)


from datetime import datetime

def normalize_position(pos: str) -> str:
    p = (pos or "").strip().upper()
    # keep it simple; your DB likely already uses these
    aliases = {
        "RWB": "RB", "LWB": "LB",
        "RCB": "CB", "LCB": "CB",
        "CDM": "CDM", "CM": "CM", "CAM": "CAM",
        "RF": "CF", "LF": "CF",
    }
    return aliases.get(p, p)

def guess_interested_clubs(row, df):
    """
    Deterministic + realistic-ish heuristic:
    - Uses overall/value/wage and (if present) league + club name.
    - Returns a short list of clubs that *could* be interested.
    """
    # pools by tier
    elite = ["Real Madrid", "Manchester City", "Bayern Munich", "Paris Saint-Germain", "Barcelona", "Liverpool", "Arsenal", "Inter", "AC Milan"]
    ucl = ["Tottenham", "Chelsea", "Manchester United", "Atletico Madrid", "Juventus", "Borussia Dortmund", "Napoli", "RB Leipzig", "Bayer Leverkusen"]
    europa = ["Sevilla", "Roma", "Lazio", "Real Sociedad", "Villarreal", "Newcastle", "Aston Villa", "Brighton", "West Ham"]
    stepping = ["Benfica", "Porto", "Sporting CP", "Ajax", "PSV", "Feyenoord", "RB Salzburg", "Club Brugge", "Celtic"]

    club = (row.get("club_name") or row.get("club") or "").strip()
    league = (row.get("league_name") or row.get("league") or "").strip()

    overall = int(pd.to_numeric(row.get("overall", 0), errors="coerce") or 0)
    value = int(pd.to_numeric(row.get("value_eur", 0), errors="coerce") or 0)
    wage_week = int(pd.to_numeric(row.get("wage_eur", row.get("wage", 0)), errors="coerce") or 0)
    wage_year = wage_week * 52 if wage_week else int(pd.to_numeric(row.get("wage_yearly_calc", 0), errors="coerce") or 0)

    # pick tier based on player level/value
    if overall >= 86 or value >= 70_000_000:
        pool = elite + ucl
    elif overall >= 82 or value >= 35_000_000:
        pool = ucl + europa
    elif overall >= 78 or value >= 15_000_000:
        pool = europa + stepping
    else:
        pool = stepping

    # light “league realism” nudge (optional)
    # if player already in Premier League, still keep PL big clubs in pool, etc.
    # (don’t overfit—keep it simple)

    # remove current club if present
    pool = [c for c in pool if c.lower() != club.lower()]

    # wage sanity: if wage is huge, remove “stepping stone” clubs
    if wage_year >= 8_000_000:  # ~€150k/wk+
        pool = [c for c in pool if c not in stepping]

    # return 5 unique
    out = []
    for c in pool:
        if c not in out:
            out.append(c)
        if len(out) >= 5:
            break
    return out


@app.route("/api/player_financial", methods=["POST", "OPTIONS"])
@app.route("/api/player_financial/", methods=["POST", "OPTIONS"])
def api_player_financial():
    if request.method == "OPTIONS":
        return cors_preflight_204()

    payload = request.get_json(silent=True) or {}
    q = (payload.get("player_name") or "").strip()
    if not q:
        return jsonify({"error": "player_name required"}), 200

    if player_data_base is None or player_data_base.empty:
        return jsonify({"error": "Database not loaded"}), 200

    df = player_data_base.copy()

    # locate columns
    name_col = "short_name" if "short_name" in df.columns else ("name" if "name" in df.columns else None)
    if not name_col:
        return jsonify({"error": "Name column not found in DB"}), 200

    wage_col = next((c for c in ("wage_eur", "wage", "wage_weekly", "wage_yearly") if c in df.columns), None)
    year_col = next((c for c in ("club_contract_valid_until_year", "contract_valid_until", "contract_end") if c in df.columns), None)
    pos_col = "club_position" if "club_position" in df.columns else None

    # wage yearly calc
    if wage_col:
        wage_num = pd.to_numeric(df[wage_col], errors="coerce").fillna(0)
        df["wage_yearly_calc"] = wage_num * 52 if wage_col in ("wage_eur", "wage", "wage_weekly") else wage_num
    else:
        df["wage_yearly_calc"] = 0

    # match by contains (case-insensitive)
    mask = df[name_col].astype(str).str.contains(q, case=False, na=False)
    hits = df[mask].copy()

    if hits.empty:
        return jsonify({"matches": [], "error": "No player found"}), 200

    # choose best hit (highest overall)
    hits["overall"] = pd.to_numeric(hits.get("overall", 0), errors="coerce").fillna(0).astype(int)
    row = hits.sort_values("overall", ascending=False).iloc[0]

    # contract year
    contract_end = 0
    if year_col:
        try:
            contract_end = int(pd.to_numeric(row.get(year_col, 0), errors="coerce") or 0)
        except Exception:
            contract_end = 0

    current_year = datetime.utcnow().year
    years_left = max(0, contract_end - current_year) if contract_end else 0

    position = normalize_position(row.get(pos_col, "")) if pos_col else "N/A"

    profile = {
        "short_name": row.get("short_name") or row.get("name") or "Unknown",
        "club_name": row.get("club_name") or row.get("club") or "",
        "league_name": row.get("league_name") or row.get("league") or "",
        "club_position": position,
        "age": int(pd.to_numeric(row.get("age", 0), errors="coerce") or 0),
        "overall": int(pd.to_numeric(row.get("overall", 0), errors="coerce") or 0),
        "value_eur": int(pd.to_numeric(row.get("value_eur", 0), errors="coerce") or 0),
        "wage_yearly": int(pd.to_numeric(row.get("wage_yearly_calc", 0), errors="coerce") or 0),
        "contract_end": contract_end,
        "years_left": years_left,
        "release_clause_eur": int(pd.to_numeric(row.get("release_clause_eur", 0), errors="coerce") or 0),
        "potential_interested_clubs": guess_interested_clubs(row, df),
    }

    return jsonify({"matches": [profile]}), 200

@app.route("/api/budget_target", methods=["POST", "OPTIONS"])
@app.route("/api/budget_target/", methods=["POST", "OPTIONS"])
def api_budget_target():
    if request.method == "OPTIONS":
        return cors_preflight_204()

    try:
        payload = request.get_json(silent=True) or {}
        position_filter = (payload.get("position") or "").strip().upper()
        age_min = safe_int(payload.get("age_min"), 0)
        age_max = safe_int(payload.get("age_max"), 99)
        max_wage = safe_int(payload.get("max_wage"), 500000)
        contract_year = safe_int(payload.get("contract_year"), 2026)
        club_style = (payload.get("club_style") or "balanced").strip().lower()

        # -----------------------------
        # Guard: DB not loaded
        # -----------------------------
        if player_data_base is None or player_data_base.empty:
            return jsonify({"targets": [], "error": "Database not loaded"}), 200

        df = player_data_base.copy()

        # -----------------------------
        # JSON-safe helpers (NaN -> None, numpy -> python)
        # -----------------------------
        def json_safe(v):
            try:
                if pd.isna(v):
                    return None
            except Exception:
                pass
            if isinstance(v, (np.integer,)):
                return int(v)
            if isinstance(v, (np.floating,)):
                return float(v)
            if isinstance(v, (np.bool_,)):
                return bool(v)
            return v

        def dict_json_safe(d):
            return {k: json_safe(v) for k, v in (d or {}).items()}

        # -----------------------------
        # Locate columns
        # -----------------------------
        wage_col = next(
            (c for c in ("wage_eur", "wage", "wage_weekly", "wage_yearly") if c in df.columns),
            None
        )
        if not wage_col:
            return jsonify({"targets": [], "error": "Wage column not found in DB"}), 200

        year_col = next(
            (c for c in ("club_contract_valid_until_year", "contract_valid_until", "contract_end") if c in df.columns),
            None
        )
        if year_col:
            df[year_col] = pd.to_numeric(df[year_col], errors="coerce").fillna(0).astype(int)

        # ensure a position column exists-ish for scoring/fit
        pos_col = "club_position" if "club_position" in df.columns else None

        # -----------------------------
        # Normalize wage to yearly
        # -----------------------------
        wage_num = pd.to_numeric(df[wage_col], errors="coerce").fillna(0)
        df["wage_yearly_calc"] = wage_num * 52 if wage_col in ("wage_eur", "wage", "wage_weekly") else wage_num

        
        
        
        # -----------------------------
        # Apply filters
        # -----------------------------
        df = df[df["wage_yearly_calc"] <= max_wage]
        if year_col:
            df = df[df[year_col] <= contract_year]

        # -----------------------------
# Extra filters: position + age
# -----------------------------
        if position_filter and pos_col:
            df["__pos_norm"] = df[pos_col].astype(str).apply(normalize_position)
            df = df[df["__pos_norm"] == normalize_position(position_filter)]

        if "age" in df.columns:
            df["age"] = pd.to_numeric(df["age"], errors="coerce").fillna(0).astype(int)
            df = df[(df["age"] >= age_min) & (df["age"] <= age_max)]


        if df.empty:
            return jsonify({"targets": [], "error": "No players matched constraints"}), 200
        

        # -----------------------------
        # Sort by overall (and momentum as tiebreaker)
        # -----------------------------
        df["overall"] = pd.to_numeric(df.get("overall", 0), errors="coerce").fillna(0).astype(int)

        # optional: compute momentum_score for ranking context
        try:
            df["momentum_score"] = df.apply(
                lambda r: compute_score_for_player(
                    r,
                    (r.get("club_position") if pos_col else "CM") or "CM",
                    None,
                    False
                ),
                axis=1,
            )
        except Exception:
            df["momentum_score"] = 0

        targets = df.sort_values(["overall", "momentum_score"], ascending=[False, False]).head(20)

        # -----------------------------
        # Build output
        # -----------------------------
        out = []
        for _, row in targets.iterrows():
            full_stats = dict_json_safe(row.to_dict())
            row_pos = (full_stats.get("club_position") or "CM") if pos_col else "CM"

            # ✅ projections + benchmarks MUST be computed inside loop (row exists here)
            age = safe_int(full_stats.get("age", 23), 23)
            projections = project_player(row, years=years_to_project(age))
            bench = POS_BENCHMARKS.get(row_pos, POS_BENCHMARKS.get("CM", {}))

            # ✅ NEW 5 sections (safe defaults)
            try:
                fit = compute_fit_score(row, row_pos, club_style)
            except Exception:
                fit = 0

            try:
                momentum = compute_momentum_index(row)
            except Exception:
                momentum = {
                    "score": safe_float(full_stats.get("momentum_score"), 0),
                    "label": "Unknown",
                    "arrow": "flat",
                }

            try:
                risk = compute_risk_profile(row)
            except Exception:
                risk = {"injury_risk": 0, "contract_risk": 0, "wage_risk": 0, "ai_suggestions": []}

            try:
                deal = compute_deal_intel(row)
            except Exception:
                deal = {
                    "contract_end_year": safe_int(full_stats.get(year_col, 0)) if year_col else 0,
                    "release_clause_eur": safe_int(full_stats.get("release_clause_eur", 0)),
                    "ai_note": "",
                }

            # similars optional (can be heavy)
            try:
                similars = find_similar_players(df, row, top_n=3)
            except Exception:
                similars = []

            out.append({
                "short_name": full_stats.get("short_name") or full_stats.get("name") or "Unknown",
                "club_position": full_stats.get("club_position") or "N/A",
                "overall": safe_int(full_stats.get("overall", 0)),
                "value_eur": safe_int(full_stats.get("value_eur", 0)),
                "wage_yearly": safe_int(full_stats.get("wage_yearly_calc", 0)),
                "contract_end": safe_int(full_stats.get(year_col, 0)) if year_col else 0,

                # ✅ charts data
                "projections": projections,
                "benchmarks": bench,

                # existing
                "full_stats": full_stats,

                # added fields
                "momentum_score": safe_float(full_stats.get("momentum_score"), 0),
                "fit_score": fit,
                "momentum": momentum,
                "risk_profile": risk,
                "deal_intel": deal,
                "similar_players": similars,
            })

        return jsonify({"targets": out}), 200

    except Exception as e:
        traceback.print_exc()
        resp = jsonify({"error": str(e)})
        resp.status_code = 500
        return resp


@app.route("/api/next_match", methods=["GET", "OPTIONS"])
def api_next_match():
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
                "score": next_match_data.get("threat_score", 80),
            },
            "weak_link": {
                "name": next_match_data.get("weakness_name", "N/A"),
                "position": next_match_data.get("weakness_pos", "DEF"),
                "tackles": next_match_data.get("weakness_stat", 0),
                "score": next_match_data.get("weakness_score", 50),
            },
            "prep_drills": [
                next_match_data.get("drill_1", "General Prep"),
                next_match_data.get("drill_2", "Tactical Review"),
            ],
        }), 200

    return jsonify({
        "opponent": "Rebels FC (Mock)",
        "formation": "4-3-3",
        "team_rating": 78,
        "insights": ["Counter risk"],
        "key_threat": {"name": "Marcus Jones", "position": "LW", "goals": 12, "score": 88},
        "weak_link": {"name": "Liam Smith", "position": "CB", "tackles": 38, "score": 42},
        "prep_drills": ["Low block"],
    }), 200


@app.route("/assets/<path:filename>")
def serve_assets(filename):
    return send_from_directory(os.path.join(app.root_path, "public/assets"), filename)


# ----------------------------------------------------
# STARTUP
# ----------------------------------------------------
initialize_app()

if __name__ == "__main__":
    print("🚀 Backend starting locally...", flush=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
