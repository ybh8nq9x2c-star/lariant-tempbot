"""
EGLC V1 Legacy Prediction Module

Isolates the original V1 (pre-regime-bias) prediction logic for
comparison with V2. This module does NOT apply regime classification,
BST inheritance, or oracle simulation. It uses the original skew-normal
CDF + Kelly sizing pipeline from tempbot_v427.

This is a frozen snapshot for shadow comparison only.
Do not modify - create new versions instead.
"""

import math
import logging
from typing import Dict, Any

log = logging.getLogger("tempbot")

# --- City parameters (from tempbot_v427 lines 433-434) ---
CITY_SIGMA_BASE = {
    "london": 2.2,
    "seoul": 2.8,
}
CITY_SKEW_ALPHA = {
    "london": -1.5,
    "seoul": -1.5,
}


def _skew_normal_cdf(x, loc, scale, alpha):
    """Standard skew-normal CDF using scipy-like parameterization."""
    from scipy import stats as st
    return st.skewnorm.cdf(x, alpha, loc=loc, scale=scale)


def predict_v1(forecast_temp: float, bucket_type: str, threshold: float,
               T_days: float, city: str = "london", mid: float = 0.5,
               is_fallback: bool = False, observed_max: float = None) -> Dict[str, Any]:
    """
    Run the V1 (legacy) prediction pipeline.

    Args:
        forecast_temp: ECMWF ensemble max temperature (°C)
        bucket_type: 'above' or 'below'
        threshold: bucket threshold temperature
        T_days: days to resolution
        city: 'london' or 'seoul'
        mid: current market mid price
        is_fallback: True if using climatological fallback
        observed_max: METAR observed max (if available)

    Returns:
        dict with pipeline='V1', true_p, edge_fc, bucket, decision, and all V1 fields
    """
    result = {
        "pipeline": "V1",
        "forecast_temp": forecast_temp,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "T_days": T_days,
        "city": city,
        "mid": mid,
        "is_fallback": is_fallback,
        "observed_max": observed_max,
        "true_p": None,
        "edge_fc": None,
        "bucket": None,
        "decision": "SKIP",
        "reason": "",
        "kelly_frac": None,
        "size_contracts": None,
    }

    # Guard: missing forecast
    if forecast_temp is None:
        result["reason"] = "NO_FORECAST"
        return result

    # V1: Use observed max if available (from METAR cache)
    if observed_max is not None:
        result["forecast_temp"] = observed_max
        result["reason"] = f"Using observed_max={observed_max}C"

    # V1 sigma: base + T decay
    sigma = CITY_SIGMA_BASE.get(city, 2.5) + max(0, 5 - T_days) * 0.15
    alpha = CITY_SKEW_ALPHA.get(city, -1.5)

    # Skew-normal CDF for P(T <= threshold)
    try:
        cdf_val = _skew_normal_cdf(threshold, forecast_temp, sigma, alpha)
    except Exception as e:
        log.warning("V1 skew-normal CDF failed: %s", e)
        result["reason"] = f"CDF_ERROR:{e}"
        return result

    # Convert to probability based on bucket type
    if bucket_type == "above":
        true_p = 1.0 - cdf_val
    else:
        true_p = cdf_val

    result["true_p"] = round(true_p, 4)

    # Edge vs market
    edge_fc = true_p - mid
    result["edge_fc"] = round(edge_fc, 4)

    # Kelly fraction (simplified from v427)
    if abs(edge_fc) > 0.01:
        kelly = edge_fc / (mid * (1 - mid)) if mid > 0 and mid < 1 else 0
        kelly = max(-0.5, min(0.5, kelly))  # cap at 50%
        result["kelly_frac"] = round(kelly, 4)
    else:
        result["kelly_frac"] = 0.0

    # Decision
    if edge_fc > 0.05:
        result["decision"] = "BUY"
        result["bucket"] = f">{threshold}" if bucket_type == "above" else f"<{threshold}"
        result["reason"] = f"V1 edge={edge_fc:.3f} > 0.05"
    elif edge_fc < -0.05:
        result["decision"] = "SELL"
        result["bucket"] = f"<{threshold}" if bucket_type == "above" else f">{threshold}"
        result["reason"] = f"V1 edge={edge_fc:.3f} < -0.05"
    else:
        result["decision"] = "SKIP"
        result["reason"] = f"V1 edge={edge_fc:.3f} within [-0.05, 0.05]"

    log.info(
        "V1 legacy: city=%s forecast=%.1f bucket=%s/%s true_p=%.3f edge=%.3f -> %s",
        city, forecast_temp, bucket_type, threshold, true_p, edge_fc, result["decision"]
    )

    return result


def predict_v1_from_bot(bot_globals: dict, market_info: dict) -> Dict[str, Any]:
    """
    Adapter to call predict_v1 using the bot's existing globals.
    bot_globals should contain: ecmwf_true_prob function, CITY_SIGMA_BASE, etc.
    """
    forecast_temp = market_info.get("forecast_temp")
    bucket_type = market_info.get("bucket_type", "above")
    threshold = market_info.get("threshold", 14.0)
    T_days = market_info.get("T_days", 1.0)
    city = market_info.get("city", "london")
    mid = market_info.get("mid", 0.5)
    observed_max = market_info.get("observed_max")

    return predict_v1(forecast_temp, bucket_type, threshold, T_days,
                     city, mid, observed_max=observed_max)
