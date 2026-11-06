"""Flask backend: web-application layer + prediction API + database layer.

Configuration (all optional, sensible local defaults):
    PORT             port to listen on            (default 5000)
    FLASK_DEBUG      1/true enables debug mode    (default off - never enable in production)
    CORS_ORIGINS     comma separated allowed origins (default http://localhost:3000)
    DB_PATH          SQLite database file         (default backend/fraud.db)
    MODEL_PATH       trained model file           (default backend/model.pkl)
    METRICS_PATH     training metrics file        (default backend/metrics.json)
    FRAUD_THRESHOLD  override the decision threshold (default: best F1 threshold from training)
    ADMIN_API_KEY     if set, DELETE /api/transactions requires the x-api-key header
"""
import datetime as dt
import json
import math
import os
import re
import secrets
import sqlite3
from pathlib import Path

import joblib
import pandas as pd
from flask import Flask, g, jsonify, request, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

import train_model
from features import CATEGORIES, CHANNELS, engineer, explain

BASE = Path(__file__).parent

# Optional local overrides from backend/.env (does not override real env vars,
# so Render/Vercel-style environment configuration always wins).
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except ImportError:
    pass

MODEL_PATH = Path(os.getenv("MODEL_PATH", BASE / "model.pkl"))
METRICS_PATH = Path(os.getenv("METRICS_PATH", BASE / "metrics.json"))
DB_PATH = Path(os.getenv("DB_PATH", BASE / "fraud.db"))
DEBUG = os.getenv("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes"),
)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # request bodies > 16 KB are rejected
CORS(app, origins=[o.strip() for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()] or ["http://localhost:3000"],
    supports_credentials=True)


# ---------- model serving layer ----------
def _read_threshold(default=0.5) -> float:
    """Explicit env var wins, then the threshold chosen during training, then 0.5."""
    env = os.getenv("FRAUD_THRESHOLD")
    if env not in (None, ""):
        try:
            val = float(env)
        except ValueError:
            print(f"Ignoring invalid FRAUD_THRESHOLD={env!r}")
        else:
            if 0.0 < val < 1.0:
                return val
            print(f"Ignoring out-of-range FRAUD_THRESHOLD={env!r}")
    if METRICS_PATH.exists():
        try:
            trained = float(json.loads(METRICS_PATH.read_text()).get("threshold", default))
            if 0.0 < trained < 1.0:
                return trained
        except (ValueError, OSError, json.JSONDecodeError):
            pass
    return default


def load_model():
    """Load the trained pipeline, training one first if it is missing."""
    if not MODEL_PATH.exists():
        print("No trained model found - training one now ...")
        train_model.train(out_dir=MODEL_PATH.parent)
    return joblib.load(MODEL_PATH)


model, MODEL_ERROR = None, None
try:
    model = load_model()
except Exception as exc:  # keep the API alive; /api/health reports the problem
    MODEL_ERROR = f"{type(exc).__name__}: {exc}"
    print(f"WARNING: could not load the model - {MODEL_ERROR}")

FRAUD_THRESHOLD = _read_threshold()


def risk_level(p: float) -> str:
    return "high" if p >= 0.7 else "medium" if p >= 0.35 else "low"


# ---------- database layer ----------
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")        # safer concurrent reads/writes
    conn.execute("PRAGMA busy_timeout=30000")      # wait instead of "database is locked"
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


with _connect() as _c:
    _c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
        amount REAL, hour INTEGER, merchant_category TEXT, channel TEXT,
        distance_from_home_km REAL, international INTEGER, txn_count_24h REAL,
        avg_txn_amount REAL, probability REAL, label TEXT, risk_level TEXT, reasons TEXT)""")
    _c.execute("CREATE INDEX IF NOT EXISTS idx_txn_created ON transactions(created_at)")
    _c.execute("CREATE INDEX IF NOT EXISTS idx_txn_label ON transactions(label)")
    _c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")


# ---------- validation ----------
def _num(data, key, errors, required=False, minimum=0.0, integer=False):
    raw = data.get(key)
    if raw in (None, ""):
        if required:
            errors[key] = "This field is required"
        return None
    if isinstance(raw, bool) or (isinstance(raw, str) and len(raw) > 32):
        errors[key] = "Must be a number"
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        errors[key] = "Must be a number"
        return None
    if not math.isfinite(val):          # blocks NaN / Infinity from reaching the model
        errors[key] = "Must be a finite number"
        return None
    if val < minimum or (key == "amount" and val <= 0):
        errors[key] = "Value is out of range"
        return None
    if integer and not val.is_integer():
        errors[key] = "Must be a whole number"
        return None
    return int(val) if integer else val


def parse_payload(data):
    if not isinstance(data, dict):
        return {}, {"payload": "Expected a JSON object"}
    errors = {}
    clean = {
        "amount": _num(data, "amount", errors, required=True),
        "hour": _num(data, "hour", errors, required=True, integer=True),
        "distance_from_home_km": _num(data, "distance_from_home_km", errors),
        "txn_count_24h": _num(data, "txn_count_24h", errors),
        "avg_txn_amount": _num(data, "avg_txn_amount", errors),
        "merchant_category": data.get("merchant_category"),
        "channel": data.get("channel"),
        "international": 1 if str(data.get("international")).lower() in ("1", "true", "yes") else 0,
    }
    if clean["hour"] is not None and not 0 <= clean["hour"] <= 23:
        errors["hour"] = "Hour must be between 0 and 23"
    if clean["merchant_category"] not in CATEGORIES:
        errors["merchant_category"] = "Unknown category"
    if clean["channel"] not in CHANNELS:
        errors["channel"] = "Unknown channel"
    return clean, errors


# ---------- routes ----------
def _validated_auth_input(data, include_name=False):
    if not isinstance(data, dict):
        return None, {"payload": "Expected a JSON object"}

    name = data.get("name", "")
    email = data.get("email", "")
    password = data.get("password", "")
    errors = {}
    if include_name and (not isinstance(name, str) or not name.strip() or len(name.strip()) > 100):
        errors["name"] = "Enter a name between 1 and 100 characters"
    if not isinstance(email, str) or len(email.strip()) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
        errors["email"] = "Enter a valid email address"
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        errors["password"] = "Password must be between 8 and 128 characters"
    if errors:
        return None, errors
    return {"name": name.strip(), "email": email.strip().lower(), "password": password}, {}


@app.post("/api/auth/register")
def register():
    data, errors = _validated_auth_input(request.get_json(silent=True), include_name=True)
    if errors:
        return jsonify(error="Invalid input", fields=errors), 400

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (data["name"], data["email"], generate_password_hash(data["password"]),
             dt.datetime.now().isoformat(timespec="seconds")))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="An account with this email already exists"), 409

    session.clear()
    session["user_id"] = cur.lastrowid
    return jsonify(ok=True, name=data["name"], email=data["email"]), 201


@app.post("/api/auth/login")
def login():
    data, errors = _validated_auth_input(request.get_json(silent=True))
    if errors:
        return jsonify(error="Invalid input", fields=errors), 400

    user = get_db().execute(
        "SELECT id, name, email, password_hash FROM users WHERE email = ?",
        (data["email"],)).fetchone()
    if user is None or not check_password_hash(user["password_hash"], data["password"]):
        return jsonify(error="Email or password is incorrect"), 401

    session.clear()
    session["user_id"] = user["id"]
    return jsonify(ok=True, name=user["name"], email=user["email"])


@app.get("/api/auth/me")
def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify(authenticated=False)
    user = get_db().execute(
        "SELECT name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        session.clear()
        return jsonify(authenticated=False)
    return jsonify(authenticated=True, name=user["name"], email=user["email"])


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/health")
def health():
    return jsonify(status="ok" if model is not None else "degraded",
                   model_loaded=model is not None, threshold=FRAUD_THRESHOLD,
                   error=MODEL_ERROR)


@app.get("/api/options")
def options():
    return jsonify(categories=CATEGORIES, channels=CHANNELS)


@app.post("/api/predict")
def predict():
    if model is None:
        return jsonify(error="Model is unavailable", detail=MODEL_ERROR), 503

    clean, errors = parse_payload(request.get_json(silent=True) or {})
    if errors:
        return jsonify(error="Invalid input", fields=errors), 400

    try:
        prob = float(model.predict_proba(engineer(pd.DataFrame([clean])))[0][1])
    except Exception as exc:  # never leak a stack trace to the client
        app.logger.exception("prediction failed")
        return jsonify(error="Prediction failed", detail=str(exc)), 500
    if not math.isfinite(prob):
        return jsonify(error="Prediction failed", detail="model returned a non-finite probability"), 500

    label = "Fraudulent" if prob >= FRAUD_THRESHOLD else "Legitimate"
    level, reasons = risk_level(prob), explain(clean)

    db = get_db()
    cur = db.execute(
        """INSERT INTO transactions (created_at, amount, hour, merchant_category, channel,
           distance_from_home_km, international, txn_count_24h, avg_txn_amount,
           probability, label, risk_level, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (dt.datetime.now().isoformat(timespec="seconds"), clean["amount"], clean["hour"],
         clean["merchant_category"], clean["channel"], clean["distance_from_home_km"],
         clean["international"], clean["txn_count_24h"], clean["avg_txn_amount"],
         prob, label, level, json.dumps(reasons)))
    db.commit()
    return jsonify(id=cur.lastrowid, label=label, probability=round(prob, 4),
                   risk_level=level, reasons=reasons, threshold=FRAUD_THRESHOLD)


@app.get("/api/transactions")
def transactions():
    limit = request.args.get("limit", 25, type=int) or 25
    limit = min(max(limit, 1), 200)
    offset = max(request.args.get("offset", 0, type=int) or 0, 0)
    label = request.args.get("label")
    sql, params = "SELECT * FROM transactions", []
    if label in ("Fraudulent", "Legitimate"):
        sql += " WHERE label = ?"
        params.append(label)
    rows = get_db().execute(
        sql + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["reasons"] = json.loads(d["reasons"] or "[]")
        except json.JSONDecodeError:
            d["reasons"] = []
        out.append(d)
    return jsonify(out)


@app.delete("/api/transactions")
def clear_transactions():
    if ADMIN_API_KEY and request.headers.get("x-api-key") != ADMIN_API_KEY:
        return jsonify(error="Unauthorized: missing or invalid x-api-key header"), 401
    db = get_db()
    db.execute("DELETE FROM transactions")
    db.commit()
    return jsonify(ok=True)


@app.get("/api/stats")
def stats():
    db = get_db()
    total, fraud, avg_amt = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(label='Fraudulent'),0), COALESCE(AVG(amount),0) FROM transactions"
    ).fetchone()
    by_cat = db.execute(
        """SELECT merchant_category AS category,
                  SUM(label='Legitimate') AS legitimate, SUM(label='Fraudulent') AS fraudulent
           FROM transactions GROUP BY merchant_category ORDER BY COUNT(*) DESC""").fetchall()
    since = (dt.date.today() - dt.timedelta(days=13)).isoformat()
    # substr() in WHERE (not the SELECT alias) so the query stays portable SQL
    daily = {r["day"]: r for r in db.execute(
        """SELECT substr(created_at,1,10) AS day, SUM(label='Legitimate') AS legitimate,
                  SUM(label='Fraudulent') AS fraudulent
           FROM transactions WHERE substr(created_at,1,10) >= ? GROUP BY day""", (since,))}
    timeline = []
    for i in range(14):
        day = (dt.date.today() - dt.timedelta(days=13 - i)).isoformat()
        r = daily.get(day)
        timeline.append({"date": day[5:], "legitimate": r["legitimate"] if r else 0,
                         "fraudulent": r["fraudulent"] if r else 0})
    risk = {r["risk_level"]: r["n"] for r in db.execute(
        "SELECT risk_level, COUNT(*) AS n FROM transactions GROUP BY risk_level")}
    return jsonify(total=total, fraudulent=fraud, legitimate=total - fraud,
                   fraud_rate=round(fraud / total, 4) if total else 0,
                   avg_amount=round(avg_amt, 2),
                   by_category=[dict(r) for r in by_cat], timeline=timeline,
                   risk={"low": risk.get("low", 0), "medium": risk.get("medium", 0),
                         "high": risk.get("high", 0)})


@app.get("/api/model-info")
def model_info():
    try:
        return jsonify(json.loads(METRICS_PATH.read_text()) if METRICS_PATH.exists() else {})
    except (OSError, json.JSONDecodeError):
        return jsonify({})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=DEBUG)
