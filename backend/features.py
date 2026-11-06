"""Shared feature engineering used by both training and the Flask API."""
import numpy as np
import pandas as pd

CATEGORIES = [
    "grocery", "fuel", "restaurant", "utilities", "entertainment",
    "electronics", "online_shopping", "travel", "jewelry", "atm_withdrawal",
]
CHANNELS = ["online", "in_store", "atm"]

RAW = [
    "amount", "hour", "merchant_category", "channel",
    "distance_from_home_km", "international", "txn_count_24h", "avg_txn_amount",
]
NUMERIC = ["amount", "log_amount", "hour", "distance_from_home_km",
           "txn_count_24h", "avg_txn_amount", "amount_ratio"]
BINARY = ["international", "is_night"]
CATEGORICAL = ["merchant_category", "channel"]
FEATURES = NUMERIC + BINARY + CATEGORICAL

RISKY_CATEGORIES = {"electronics", "jewelry", "travel", "online_shopping", "atm_withdrawal"}


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    """Turn raw transaction columns into model features (missing values stay NaN
    and are imputed inside the sklearn pipeline)."""
    df = df.copy()
    for col in RAW:
        if col not in df:
            df[col] = np.nan
    for col in ["amount", "hour", "distance_from_home_km", "international",
                "txn_count_24h", "avg_txn_amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["log_amount"] = np.log1p(df["amount"])
    # NaN when the average is unknown or not positive; the pipeline imputes it.
    # The clip guards against tiny averages exploding the ratio for odd inputs.
    df["amount_ratio"] = (df["amount"] / df["avg_txn_amount"].where(df["avg_txn_amount"] > 0)
                          .clip(lower=1e-6)).clip(upper=1e6)
    df["is_night"] = df["hour"].between(0, 5).astype(int)
    out = df[FEATURES]
    # ±inf (e.g. from a bad CSV) becomes NaN so the imputer handles it instead of
    # poisoning StandardScaler / the estimator.
    return out.replace([np.inf, -np.inf], np.nan)


def explain(t: dict) -> list[str]:
    """Human-readable risk indicators shown next to the ML prediction."""
    reasons = []
    hour = t.get("hour")
    if hour is not None and 0 <= hour <= 5:
        reasons.append(f"Transaction made late at night ({int(hour):02d}:00)")
    avg, amt = t.get("avg_txn_amount"), t.get("amount")
    if avg and avg > 0 and amt and amt / avg >= 3:
        reasons.append(f"Amount is {amt / avg:.1f}x the customer's usual spend")
    if amt and amt >= 20000:
        reasons.append("High-value transaction")
    if t.get("international"):
        reasons.append("International transaction")
    if (t.get("distance_from_home_km") or 0) >= 100:
        reasons.append(f"Made {t['distance_from_home_km']:.0f} km away from home location")
    if (t.get("txn_count_24h") or 0) >= 5:
        reasons.append(f"{int(t['txn_count_24h'])} transactions in the last 24 hours")
    if t.get("merchant_category") in RISKY_CATEGORIES and t.get("channel") == "online":
        reasons.append(f"Online purchase in a high-risk category ({t['merchant_category'].replace('_', ' ')})")
    return reasons or ["No unusual patterns detected"]
