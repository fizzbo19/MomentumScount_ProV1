"""
MomentumScout Backend V1.1 (Robust)
---------------------------------------------------------
- Robust CORS (single source of truth)
- Demo signup saved to CSV + emailed + optionally pushed to Google Sheets (Apps Script)
- Verify login supports:
  1) Static portal codes (SCOUT2025 / BALLER2025)
  2) Per-user access_code stored in signups.csv
---------------------------------------------------------
"""

import os
import secrets
from io import StringIO
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_mail import Mail, Message


# ----------------------------------------------------
# APP INIT
# ----------------------------------------------------
app = Flask(__name__, static_folder="public")


# ----------------------------------------------------
# CORS CONFIG (ROBUST + SAFE)
# ----------------------------------------------------
FRONTEND_URL = (os.environ.get("FRONTEND_URL", "https://momentum-ai-io.netlify.app") or "").rstrip("/")

# Use a set for fast membership checks
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
}

# Clean empty strings if FRONTEND_URL env not set
ALLOWED_ORIGINS = {o for o in ALLOWED_ORIGINS if o and isinstance(o, str)}

def _cors_origin():
    """
    Return the request Origin if it is explicitly allowed.
    """
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        return origin
    return None

def _add_cors_headers(resp):
    """
    Always attach CORS headers when Origin is allowed.
    This runs for normal responses and also error responses.
    """
    origin = _cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        # You are NOT using cookies-based auth, but leaving this true won't break.
        # If you ever enable credentials, you must NOT use '*' origin.
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

@app.before_request
def cors_preflight():
    # Handle all OPTIONS requests centrally
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        return _add_cors_headers(resp)

@app.after_request
def cors_after(resp):
    return _add_cors_headers(resp)


# ----------------------------------------------------
# EMAIL CONFIG
# ----------------------------------------------------
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.hostinger.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = (os.environ.get("MAIL_USE_TLS", "True") == "True")
app.config["MAIL_USE_SSL"] = (os.environ.get("MAIL_USE_SSL", "False") == "True")
app.config["MAIL_USERNAME"] = MAIL_USERNAME
app.config["MAIL_PASSWORD"] = MAIL_PASSWORD

# IMPORTANT: sender email should match MAIL_USERNAME mailbox
app.config["MAIL_DEFAULT_SENDER"] = (
    "MomentumScout Intelligence",
    MAIL_USERNAME,
)

if not MAIL_USERNAME or not MAIL_PASSWORD:
    print("❌ Missing MAIL_USERNAME or MAIL_PASSWORD env vars", flush=True)

mail = Mail(app)


# ----------------------------------------------------
# BASIC HEALTHCHECK
# ----------------------------------------------------
@app.route("/api/ping", methods=["GET", "OPTIONS"])
def api_ping():
    return jsonify({"ok": True}), 200


# ----------------------------------------------------
# CONSTANTS / CONFIG
# ----------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(BASE_DIR, "data")):
    DEFAULT_DATA_PATH = os.path.join(BASE_DIR, "data")
elif os.path.exists(os.path.join(BASE_DIR, "../data")):
    DEFAULT_DATA_PATH = os.path.join(BASE_DIR, "../data")
else:
    DEFAULT_DATA_PATH = os.path.join(os.getcwd(), "data")

DATA_FOLDER_PATH = os.environ.get("DATA_FOLDER_PATH", DEFAULT_DATA_PATH)
os.makedirs(DATA_FOLDER_PATH, exist_ok=True)

DATA_FILENAME_BASE = os.environ.get("DATA_FILENAME_BASE", "FC26_MomentumScout.csv")
DATA_FILENAME_BALLER = os.environ.get("DATA_FILENAME_BALLER", "baller_league_uk.xlsx")
DATA_FILENAME_NEXT_MATCH = os.environ.get("DATA_FILENAME_NEXT_MATCH", "baller_next_match.xlsx")

DATA_FILENAME_SIGNUPS = "signups.csv"
DATA_FILENAME_AUDIT = "analyst_usage.csv"

GOOGLE_SCRIPT_URL = (os.environ.get("GOOGLE_SCRIPT_URL") or "").strip()

ACCESS_CODES = {
    "club": "SCOUT2025",
    "baller": "BALLER2025",
}

ENTITLEMENTS_MAP = {
    "Agent": {"analyst_ai": False, "export_csv": False},
    "Tier 3": {"analyst_ai": False, "export_csv": False},
    "Tier 2": {"analyst_ai": "yearly_only", "export_csv": True},
    "Tier 1": {"analyst_ai": True, "export_csv": True},
    "Baller League": {"analyst_ai": False, "export_csv": False},
    "Admin": {"analyst_ai": True, "export_csv": True},
}

# Global Data Variables
player_data_base = None
player_data_baller = None
next_match_data = None


# ----------------------------------------------------
# DATABASES (WEIGHTS & DRILLS)
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
    return str(col_name).strip().lower().replace(" ", "_").replace(".", "").replace("%", "_pct")


# ----------------------------------------------------
# USER MANAGEMENT (CSV)
# ----------------------------------------------------
def save_signup(data):
    fp = os.path.join(DATA_FOLDER_PATH, DATA_FILENAME_SIGNUPS)
    if not os.path.exists(fp):
        df = pd.DataFrame(columns=["fullName", "email", "organization", "role", "tier", "plan", "timestamp", "access_code"])
        df.to_csv(fp, index=False)

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
# AI / DATA HELPERS
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
        return None

def _load_fc26_data(filename):
    fp = os.path.join(DATA_FOLDER_PATH, filename)
    if not os.path.exists(fp):
        return pd.DataFrame()
    try:
        df = pd.read_csv(fp, encoding="utf-8-sig") if filename.endswith(".csv") else pd.read_excel(fp)
    except Exception:
        return pd.DataFrame()

    df.columns = [clean_column_name(c) for c in df.columns]

    numeric_cols = [col for col in df.columns if df[col].dtype == "object"]
    for col in numeric_cols:
        try:
            if any(x in col for x in ["overall", "value", "wage", "pace", "shooting", "passing", "dribbling", "defending", "physic", "age", "contract"]):
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


# ----------------------------------------------------
# ROUTES
# ----------------------------------------------------
@app.route("/", methods=["GET", "OPTIONS"])
def health():
    return jsonify({"status": "online"}), 200


@app.route("/api/verify_login", methods=["POST", "OPTIONS"])
def api_verify_login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    code = str(data.get("code") or "").strip()
    portal = (data.get("portal") or "").strip()

    # 1) static portal codes
    if ACCESS_CODES.get(portal) == code:
        is_valid, msg, entitlements = check_login_status(email)
        if not is_valid:
            return jsonify({"success": False, "message": msg}), 403
        return jsonify({"success": True, "message": msg, "entitlements": entitlements}), 200

    # 2) per-user access_code in signups.csv
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
        return jsonify({"success": False, "message": f"System Error: {str(e)}"}), 500


@app.route("/api/submit_demo", methods=["POST", "OPTIONS"])
def api_submit_demo():
    print("✅ /api/submit_demo HIT", flush=True)

    try:
        data = request.json or {}
        full_name = data.get("fullName", "User")
        user_email = (data.get("email") or "").strip().lower()
        org = data.get("organization", "N/A")
        role = data.get("role", "N/A")

        if not user_email:
            return jsonify({"success": False, "message": "Email required."}), 400

        # generate unique access code and add to data
        access_code = make_access_code(8)
        data["access_code"] = access_code

        # save locally to CSV
        saved = save_signup(data)
        if not saved:
            print("⚠️ Warning: save_signup returned False", flush=True)

        # OPTIONAL: push to Google Sheet (Apps Script)
        gs_url = (os.environ.get("GOOGLE_SCRIPT_URL") or "").strip()
        if gs_url:
            print("✅ Posting to GOOGLE_SCRIPT_URL:", gs_url, flush=True)

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
                r = requests.post(gs_url, json=payload, timeout=10)
                print("✅ Google Sheet POST status:", r.status_code, flush=True)
                print("✅ Google Sheet response:", r.text[:300], flush=True)
            except Exception as e:
                print("⚠️ Google Script POST failed:", repr(e), flush=True)

        # Build emails
        internal_msg = Message(
            subject=f"🔥 New Demo Request: {org} - {full_name}",
            recipients=[os.environ.get("INTERNAL_ALERT_EMAIL", "info@momentumscout.com")],
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

        try:
            mail.send(internal_msg)
            mail.send(customer_msg)
        except Exception as e:
            print("Email send failed:", repr(e), flush=True)
            # Still success because data saved
            return jsonify({"success": True, "message": "Demo saved but email failed. Check server logs."}), 200

        return jsonify({"success": True, "message": "Demo request submitted successfully.", "access_code": access_code}), 200

    except Exception as e:
        print("Demo Request Error:", repr(e), flush=True)
        return jsonify({"success": False, "message": "Submission error. Please try again."}), 500


@app.route("/api/find_players", methods=["POST", "OPTIONS"])
def api_find_players():
    try:
        data = request.json or {}
        df = player_data_baller if data.get("data_source") == "baller" else player_data_base
        if df is None:
            return jsonify({"players": []}), 200

        df = df.copy()
        filters = data.get("filters", {})
        for key, rng in filters.items():
            col = clean_column_name(key)
            if "value" in col:
                col = "value_eur"
            if col in df.columns and isinstance(rng, list) and len(rng) >= 2:
                df = df[(df[col] >= float(rng[0])) & (df[col] <= float(rng[1]))]

        is_baller = (data.get("data_source") == "baller")
        df["momentum_score"] = df.apply(
            lambda r: compute_score_for_player(r, data.get("position", "ALL"), data.get("weights"), is_baller),
            axis=1,
        )

        out = []
        for _, row in df.sort_values("momentum_score", ascending=False).head(20).iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k, v in p_dict.items()}

            tp = generate_training_plan(row, row.get("club_position", "CM"), is_baller)
            hm = generate_heatmap_data(row, row.get("club_position", "CM"))

            out.append({
                "short_name": p_dict.get("short_name"),
                "club_position": p_dict.get("club_position"),
                "momentum_score": p_dict.get("momentum_score"),
                "value_eur": p_dict.get("value_eur", 0),
                "player_face_url": p_dict.get("player_face_url", ""),
                "ai_training": tp,
                "heatmap_zones": hm,
                "full_attributes": p_dict,
                "projections": project_player(row),
            })

        return jsonify({"players": out}), 200
    except Exception as e:
        return jsonify({"players": [], "error": str(e)}), 500


@app.route("/api/search_player", methods=["POST", "OPTIONS"])
def api_search_player():
    try:
        data = request.json or {}
        query = str(data.get("player_name", "")).lower().strip()
        is_baller = (data.get("data_source") == "baller")
        df = player_data_baller if is_baller else player_data_base
        if df is None or df.empty or not query:
            return jsonify([]), 200

        mask = df["short_name"].astype(str).str.lower().str.contains(query)
        if "name" in df.columns:
            mask |= df["name"].astype(str).str.lower().str.contains(query)

        results = df[mask].head(10)
        out = []
        for _, row in results.iterrows():
            p_dict = row.to_dict()
            p_dict = {k: (0 if pd.isna(v) else v) for k, v in p_dict.items()}
            score = compute_score_for_player(row, row.get("club_position", "CM"), None, is_baller)
            age = safe_int(row.get("age"), 21)
            projections = project_player(row, years_to_project(age))
            tp = generate_training_plan(row, row.get("club_position", "CM"), is_baller)
            hm = generate_heatmap_data(row, row.get("club_position", "CM"))

            out.append({
                "short_name": p_dict.get("short_name"),
                "club_position": p_dict.get("club_position"),
                "momentum_score": score,
                "projections": projections,
                "ai_training": tp,
                "heatmap_zones": hm,
                "value_eur": safe_int(p_dict.get("value_eur")),
                "full_attributes": p_dict,
            })

        return jsonify(out), 200
    except Exception:
        return jsonify([]), 500


@app.route("/api/budget_target", methods=["POST", "OPTIONS"])
def api_budget_target():
    try:
        payload = request.json or {}
        max_wage = safe_int(payload.get("max_wage"), 500000)
        contract_year = safe_int(payload.get("contract_year"), 2026)

        df = player_data_base.copy()
        wage_col = "wage_eur" if "wage_eur" in df.columns else "wage"
        year_col = "club_contract_valid_until_year" if "club_contract_valid_until_year" in df.columns else "contract_valid_until"

        if year_col in df.columns:
            df[year_col] = pd.to_numeric(df[year_col], errors="coerce").fillna(0).astype(int)

        df = df[(df[wage_col] * 52) <= max_wage]
        if year_col in df.columns:
            df = df[df[year_col] <= contract_year]

        targets = df.sort_values(by="overall", ascending=False).head(20)

        out = []
        for _, row in targets.iterrows():
            out.append({
                "short_name": row["short_name"],
                "club_position": row["club_position"],
                "overall": row["overall"],
                "value_eur": row.get("value_eur", 0),
                "wage_yearly": row[wage_col] * 52,
                "contract_end": int(row.get(year_col, 0)),
                "full_stats": row.to_dict(),
            })

        return jsonify({"targets": out}), 200

    except Exception as e:
        print("Budget target error:", repr(e), flush=True)
        return jsonify({"targets": [], "error": str(e)}), 500


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
    print("🚀 Backend starting...", flush=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

