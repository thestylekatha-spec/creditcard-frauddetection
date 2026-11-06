"""API + feature-engineering tests.

Run from the backend folder:  python -m pytest -q
The app is imported with an isolated temporary database so the real
backend/fraud.db is never touched.
"""
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")  # before app import
# representative CORS setup: exact origin + a regex covering Vercel preview URLs
os.environ["CORS_ORIGINS"] = (
    "http://localhost:3000,https://myapp.vercel.app,https://.*\\.vercel\\.app$")
os.environ.pop("ADMIN_API_KEY", None)

import app as app_module  # noqa: E402
from features import CATEGORIES, FEATURES, RAW, engineer, explain  # noqa: E402

VALID = {
    "amount": 48500, "hour": 2, "merchant_category": "electronics",
    "channel": "online", "distance_from_home_km": 640, "international": True,
    "txn_count_24h": 7, "avg_txn_amount": 1800,
}


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def post(client, **overrides):
    payload = {**VALID, **overrides}
    return client.post("/api/predict", json=payload)


# ---------- health & options ----------
def test_health_reports_model(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["model_loaded"] is True
    assert 0 < data["threshold"] < 1


def test_options_lists(client):
    data = client.get("/api/options").get_json()
    assert len(data["categories"]) == len(CATEGORIES) == 10
    assert data["channels"] == ["online", "in_store", "atm"]


# ---------- authentication ----------
def test_register_login_and_logout(client):
    registered = client.post("/api/auth/register", json={
        "name": "Ada Lovelace", "email": "ADA@example.com", "password": "safe-password-123"})
    assert registered.status_code == 201
    cookie = registered.headers["Set-Cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert registered.get_json()["email"] == "ada@example.com"
    assert client.get("/api/auth/me").get_json() == {
        "authenticated": True, "name": "Ada Lovelace", "email": "ada@example.com"}

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").get_json() == {"authenticated": False}

    logged_in = client.post("/api/auth/login", json={
        "email": "ada@example.com", "password": "safe-password-123"})
    assert logged_in.status_code == 200
    assert client.get("/api/auth/me").get_json()["authenticated"] is True

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").get_json() == {"authenticated": False}


def test_auth_rejects_duplicate_email_and_bad_credentials(client):
    details = {"name": "Ada", "email": "duplicate@example.com", "password": "safe-password-123"}
    assert client.post("/api/auth/register", json=details).status_code == 201
    assert client.post("/api/auth/register", json=details).status_code == 409
    client.post("/api/auth/logout")
    bad_login = client.post("/api/auth/login", json={
        "email": details["email"], "password": "wrong-password"})
    assert bad_login.status_code == 401
    assert client.get("/api/auth/me").get_json() == {"authenticated": False}


def test_auth_validates_name_email_and_password(client):
    response = client.post("/api/auth/register", json={
        "name": " ", "email": "not-an-email", "password": "short"})
    assert response.status_code == 400
    assert set(response.get_json()["fields"]) == {"name", "email", "password"}


# ---------- prediction happy path ----------
def test_predict_valid(client):
    r = post(client)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data["label"] in ("Fraudulent", "Legitimate")
    assert 0.0 <= data["probability"] <= 1.0
    assert data["risk_level"] in ("low", "medium", "high")
    assert isinstance(data["reasons"], list) and data["reasons"]
    assert isinstance(data["id"], int)


def test_predict_optional_fields_may_be_missing(client):
    r = client.post("/api/predict", json={
        "amount": 1250, "hour": 14, "merchant_category": "grocery", "channel": "in_store"})
    assert r.status_code == 200, r.get_json()


# ---------- validation ----------
@pytest.mark.parametrize("payload,field", [
    ({k: v for k, v in VALID.items() if k != "amount"}, "amount"),
    ({**VALID, "amount": "nan"}, "amount"),
    ({**VALID, "amount": "inf"}, "amount"),
    ({**VALID, "amount": -5}, "amount"),
    ({**VALID, "amount": 0}, "amount"),
    ({**VALID, "amount": "abc"}, "amount"),
    ({**VALID, "hour": 30}, "hour"),
    ({**VALID, "hour": -1}, "hour"),
    ({**VALID, "hour": 14.5}, "hour"),
    ({**VALID, "merchant_category": "spaceships"}, "merchant_category"),
    ({**VALID, "channel": "drone"}, "channel"),
    ({**VALID, "distance_from_home_km": "nan"}, "distance_from_home_km"),
    ({**VALID, "txn_count_24h": -3}, "txn_count_24h"),
])
def test_predict_rejects_bad_input(client, payload, field):
    r = client.post("/api/predict", json=payload)
    assert r.status_code == 400
    assert field in r.get_json()["fields"]


def test_predict_rejects_non_object_body(client):
    r = client.post("/api/predict", json=[1, 2, 3])
    assert r.status_code == 400


def test_predict_rejects_oversized_field(client):
    r = post(client, merchant_category="x" * 5000)
    assert r.status_code in (400, 413)


# ---------- stats / transactions ----------
def test_stats_and_transactions_flow(client):
    post(client)
    post(client, amount=999, hour=15, merchant_category="grocery", channel="in_store")

    stats = client.get("/api/stats").get_json()
    assert stats["total"] >= 2
    assert stats["legitimate"] + stats["fraudulent"] == stats["total"]
    assert len(stats["timeline"]) == 14
    assert set(stats["risk"]) == {"low", "medium", "high"}
    assert stats["by_category"]

    txns = client.get("/api/transactions?limit=1").get_json()
    assert len(txns) == 1
    assert isinstance(txns[0]["reasons"], list)

    offset = client.get("/api/transactions?limit=1&offset=1").get_json()
    assert len(offset) >= 1
    if len(txns) == 1 and len(offset) == 1:
        assert txns[0]["id"] != offset[0]["id"]

    labels = client.get("/api/transactions?label=Fraudulent").get_json()
    assert all(t["label"] == "Fraudulent" for t in labels)
    # nonsense label params are ignored rather than causing an error
    assert client.get("/api/transactions?label=<script>").status_code == 200
    # limit is clamped, never unbounded
    assert len(client.get("/api/transactions?limit=99999").get_json()) <= 200


def test_clear_requires_api_key_when_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_API_KEY", "sekret")
    assert client.delete("/api/transactions").status_code == 401
    assert client.delete("/api/transactions", headers={"x-api-key": "wrong"}).status_code == 401
    assert client.delete("/api/transactions", headers={"x-api-key": "sekret"}).status_code == 200


def test_clear_open_by_default(client):
    assert client.delete("/api/transactions").status_code == 200
    assert client.get("/api/stats").get_json()["total"] == 0


def test_model_info(client):
    data = client.get("/api/model-info").get_json()
    assert "best_model" in data and "models" in data
    assert 0 < data.get("threshold", 0.5) < 1


# ---------- CORS (Vercel UI -> Render API) ----------
def _allow_origin(client, origin, path="/api/health"):
    r = client.get(path, headers={"Origin": origin})
    return r.headers.get("Access-Control-Allow-Origin")


def test_cors_allows_exact_origin(client):
    assert _allow_origin(client, "https://myapp.vercel.app") == "https://myapp.vercel.app"


def test_cors_allows_vercel_preview_url(client):
    origin = "https://myapp-git-feat-x-y-1234.vercel.app"
    assert _allow_origin(client, origin) == origin


def test_cors_rejects_unknown_origin(client):
    assert _allow_origin(client, "https://evil.com") is None


def test_cors_rejects_suffix_spoofing(client):
    # the regex is $-anchored, so neither of these must be allowed
    assert _allow_origin(client, "https://myapp.vercel.app.evil.com") is None
    assert _allow_origin(client, "https://myappXvercel.app") is None


def test_cors_preflight_for_predict(client):
    r = client.open("/api/predict", method="OPTIONS", headers={
        "Origin": "https://myapp.vercel.app",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code in (200, 204)
    assert r.headers.get("Access-Control-Allow-Origin") == "https://myapp.vercel.app"
    assert "POST" in (r.headers.get("Access-Control-Allow-Methods") or "")


# ---------- features ----------
def test_engineer_produces_expected_columns_and_handles_infinities():
    df = pd.DataFrame([{"amount": np.inf, "hour": 2, "merchant_category": "grocery",
                        "channel": "online", "avg_txn_amount": 0.0,
                        "distance_from_home_km": np.nan, "international": 1,
                        "txn_count_24h": np.nan}])
    out = engineer(df)
    assert list(out.columns) == FEATURES
    numeric = out.select_dtypes(include="number").to_numpy(dtype=float)
    assert not np.isinf(numeric).any()
    assert out["amount_ratio"].isna().all()        # non-positive average -> unknown
    assert out["is_night"].iloc[0] == 1


def test_engineer_fills_missing_raw_columns():
    out = engineer(pd.DataFrame([{"amount": 100, "hour": 14}]))
    assert list(out.columns) == FEATURES


def test_explain_flags_and_default():
    reasons = explain(VALID)
    assert any("night" in r.lower() for r in reasons)
    assert any("International" in r for r in reasons)
    assert explain({"amount": 100, "hour": 14, "merchant_category": "grocery",
                    "channel": "in_store"}) == ["No unusual patterns detected"]


def test_engineered_frame_passes_through_trained_model():
    model = app_module.load_model()
    proba = model.predict_proba(engineer(pd.DataFrame([VALID])))
    assert np.isfinite(proba).all()
    assert len(RAW) == 8


def test_metrics_json_is_valid():
    info = json.loads(open(os.path.join(os.path.dirname(__file__), "metrics.json")).read())
    assert {"best_model", "models", "feature_importance", "threshold"} <= set(info)
