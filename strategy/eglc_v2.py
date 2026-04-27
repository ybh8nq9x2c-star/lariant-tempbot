import math
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from strategy.eglc_config import (
    REGIME_DELTAS, HOT_DAY_ADJ, REGIME_CAP, BOUNDARY_PROXIMITY,
    MIN_CONFIDENCE, MIN_EDGE, ORACLE_SIGMA, MIN_REGIME_EFFECT,
    WIND_SPREAD_LIMIT, MODEL_SPREAD_LIMIT, BST_START_MONTH, BST_END_MONTH,
    CONFIDENCE_LEVELS, can_trade_v2_live, EGLC_V2_SHADOW, EGLC_V2_ENABLED
)

log = logging.getLogger("tempbot")


# === STEP 1: REGIME CLASSIFICATION ===

def classify_regime(wind_dir_deg: float, forecast_max_c: float,
                    is_bst: bool = None, wind_spread_deg: float = None,
                    model_spread_c: float = None) -> dict:
    """
    Classify wind direction into regime R1-R6.
    Returns dict with: regime_code, wind_regime, wind_delta, wind_confidence,
    hot_day_flag, hot_delta, total_adjustment, confidence, reason_code
    """
    # Guard: uncertain wind
    if wind_spread_deg is not None and wind_spread_deg > WIND_SPREAD_LIMIT:
        return {
            "regime_code": "DEFER", "wind_regime": "UNKNOWN",
            "wind_delta": 0.0, "wind_confidence": "LOW",
            "hot_day_flag": False, "hot_delta": 0.0,
            "total_adjustment": 0.0, "confidence": "LOW",
            "reason_code": "WIND_SPREAD",
            "is_bst": is_bst, "wind_dir_deg": wind_dir_deg
        }

    # Guard: model disagreement
    if model_spread_c is not None and model_spread_c > MODEL_SPREAD_LIMIT:
        return {
            "regime_code": "DEFER", "wind_regime": "UNKNOWN",
            "wind_delta": 0.0, "wind_confidence": "LOW",
            "hot_day_flag": False, "hot_delta": 0.0,
            "total_adjustment": 0.0, "confidence": "LOW",
            "reason_code": "MODEL_SPREAD",
            "is_bst": is_bst, "wind_dir_deg": wind_dir_deg
        }

    # Classify wind direction
    wind_dir = wind_dir_deg % 360
    if 60 <= wind_dir <= 150:
        wind_regime = "E_SE"
        wind_delta = REGIME_DELTAS["E_SE"]
        wind_conf = "MEDIUM"
    elif 150 < wind_dir <= 270:
        wind_regime = "S_SW"
        wind_delta = REGIME_DELTAS["S_SW"]
        wind_conf = "MEDIUM"
    elif (270 < wind_dir <= 360) or (0 <= wind_dir < 30):
        wind_regime = "W_NW"
        wind_delta = REGIME_DELTAS["W_NW"]
        wind_conf = "MEDIUM"
    elif 30 <= wind_dir < 60:
        wind_regime = "N_NE"
        wind_delta = REGIME_DELTAS["N_NE"]
        wind_conf = "LOW"
    else:
        wind_regime = "UNKNOWN"
        wind_delta = 0.0
        wind_conf = "LOW"

    # Hot day adjustment
    is_hot = forecast_max_c >= 28
    if forecast_max_c >= 30:
        hot_delta = HOT_DAY_ADJ[">=30"]
    elif forecast_max_c >= 28:
        hot_delta = HOT_DAY_ADJ[">=28"]
    else:
        hot_delta = 0.0

    # Combine into regime code R1-R6
    if wind_regime == "E_SE" and is_hot:
        regime_code = "R1"
        total = max(wind_delta + hot_delta, -REGIME_CAP)
        confidence = "MEDIUM"
    elif wind_regime == "E_SE" and not is_hot:
        regime_code = "R2"
        total = wind_delta
        confidence = "MEDIUM"
    elif wind_regime == "S_SW":
        regime_code = "R3"
        total = wind_delta
        confidence = "LOW"
    elif wind_regime == "W_NW" and is_hot:
        regime_code = "R4"
        total = 0.0  # conflicting signals
        confidence = "LOW"
    elif wind_regime == "W_NW" and not is_hot:
        regime_code = "R5"
        total = wind_delta
        confidence = "MEDIUM"
    elif wind_regime == "N_NE":
        regime_code = "R6"
        total = 0.0
        confidence = "LOW"
    else:
        regime_code = "R0"
        total = 0.0
        confidence = "LOW"

    # Cap adjustment
    total = max(-REGIME_CAP, min(REGIME_CAP, total))

    return {
        "regime_code": regime_code,
        "wind_regime": wind_regime,
        "wind_delta": wind_delta,
        "wind_confidence": wind_conf,
        "hot_day_flag": is_hot,
        "hot_delta": hot_delta,
        "total_adjustment": round(total, 3),
        "confidence": confidence,
        "reason_code": "OK",
        "is_bst": is_bst,
        "wind_dir_deg": wind_dir_deg
    }


# === STEP 2: REGIME ADJUSTMENT ===

def apply_regime_adjustment(forecast_raw: float, regime: dict,
                            bucket_edges: list = None) -> tuple:
    """
    Apply conditional regime adjustment ONLY when near a bucket boundary.
    Returns (adjusted_forecast, adjustment_applied, reason).
    """
    adjustment = regime["total_adjustment"]

    # Skip if regime says DEFER
    if regime["reason_code"] != "OK":
        return forecast_raw, 0.0, f"DEFER:{regime['reason_code']}"

    # Skip if adjustment too small
    if abs(adjustment) < MIN_REGIME_EFFECT:
        return forecast_raw, 0.0, "REGIME_SMALL"

    # Boundary proximity check
    if bucket_edges:
        near_boundary = any(abs(forecast_raw - edge) <= BOUNDARY_PROXIMITY
                          for edge in bucket_edges)
        if not near_boundary:
            return forecast_raw, 0.0, "FAR_BOUNDARY"

    adjusted = forecast_raw + adjustment
    adjusted = max(-10.0, min(45.0, adjusted))  # physical sanity

    return round(adjusted, 3), adjustment, f"R:{regime['regime_code']}:{adjustment:+.3f}C"


# === STEP 3: BST CHECK ===

def is_bst_period(target_date) -> bool:
    """Check if date falls in BST period (last Sun Mar -> last Sun Oct)."""
    if isinstance(target_date, str):
        target_date = datetime.fromisoformat(target_date).date()
    month = target_date.month
    return BST_START_MONTH <= month <= BST_END_MONTH


def bst_inheritance_check(target_date, forecast_max_c: float,
                          metar_23xx_max: float = None) -> dict:
    """
    During BST, 23:00-23:59 UTC METARs count toward NEXT local day.
    If inherited temp > forecast max, adjust upward.
    """
    is_bst = is_bst_period(target_date)
    if not is_bst:
        return {"bst_flag": False, "inheritance_adj": 0.0, "reason": "GMT: no inheritance"}

    if metar_23xx_max is None:
        return {"bst_flag": True, "inheritance_adj": 0.0, "reason": "BST: no 23xx data"}

    if metar_23xx_max > forecast_max_c:
        adj = round(metar_23xx_max - forecast_max_c, 2)
        return {"bst_flag": True, "inheritance_adj": adj,
                "reason": f"BST: inherited {metar_23xx_max}C > forecast {forecast_max_c}C"}

    return {"bst_flag": True, "inheritance_adj": 0.0, "reason": "BST: inherited <= forecast"}


# === STEP 4: ORACLE SIMULATION ===

def simulate_oracle_resolution(forecast_max_01c: float,
                               n_samples: int = 10000) -> dict:
    """
    Simulate oracle resolution: half-up rounding + discrete sampling.
    Returns dict with oracle_value, boundary_flag, rounding_direction, bucket_probs.
    """
    # Half-up rounding (ICAO standard)
    oracle_value = math.floor(forecast_max_01c + 0.5)
    decimal_part = forecast_max_01c - math.floor(forecast_max_01c)
    boundary_flag = (0.3 <= decimal_part <= 0.7)
    rounding_direction = "up" if decimal_part >= 0.5 else "down"

    # Monte Carlo: simulate max of 48 discrete samples
    samples = np.random.normal(forecast_max_01c, ORACLE_SIGMA, n_samples)
    rounded_samples = np.floor(samples + 0.5).astype(int)

    # Bucket probability distribution
    unique, counts = np.unique(rounded_samples, return_counts=True)
    bucket_probs = {int(u): round(c / n_samples, 4) for u, c in zip(unique, counts)}

    return {
        "oracle_value": oracle_value,
        "boundary_flag": boundary_flag,
        "rounding_direction": rounding_direction,
        "decimal_part": round(decimal_part, 2),
        "bucket_probs": bucket_probs,
        "forecast_input": forecast_max_01c
    }



# === STEP 5: FULL V2 PIPELINE ===

def predict_market_resolution_v2(forecast_raw: float, bucket_edges: list,
                                wind_dir_deg: float = None,
                                wind_spread_deg: float = None,
                                model_spread_c: float = None,
                                target_date=None,
                                metar_23xx_max: float = None,
                                city: str = "london") -> dict:
    """
    Full V2 prediction pipeline:
    forecast -> regime -> adjustment -> BST -> oracle -> bucket -> confidence
    """
    result = {
        "pipeline": "V2",
        "forecast_raw": forecast_raw,
        "city": city,
        "regime_code": "R0",
        "regime_reason": "NO_DATA",
        "adjustment_c": 0.0,
        "bst_flag": False,
        "bst_adj": 0.0,
        "oracle_value": None,
        "oracle_boundary": False,
        "oracle_rounding": None,
        "confidence": "LOW",
        "trade_or_defer": "DEFER",
        "defer_reason": "INIT",
        "bucket_probs": {},
        "warnings": []
    }

    # Step 1: Regime classification
    if wind_dir_deg is not None:
        is_bst = is_bst_period(target_date) if target_date else None
        regime = classify_regime(wind_dir_deg, forecast_raw, is_bst,
                                wind_spread_deg, model_spread_c)
        result["regime_code"] = regime["regime_code"]
        result["regime_reason"] = regime["reason_code"]
        result["confidence"] = regime["confidence"]
    else:
        regime = {
            "regime_code": "R0", "total_adjustment": 0.0,
            "reason_code": "NO_WIND_DATA", "confidence": "LOW"
        }
        result["warnings"].append("No wind direction data")

    # Step 2: Apply regime adjustment (only near boundaries)
    forecast_adjusted, adj_applied, adj_reason = apply_regime_adjustment(
        forecast_raw, regime, bucket_edges
    )
    result["adjustment_c"] = adj_applied
    result["adjustment_reason"] = adj_reason
    if adj_applied != 0.0:
        result["confidence"] = max(
            result["confidence"], regime.get("confidence", "LOW"),
            key=lambda x: CONFIDENCE_LEVELS.get(x, 0)
        )

    # Step 3: BST inheritance check
    if target_date:
        bst_result = bst_inheritance_check(target_date, forecast_adjusted, metar_23xx_max)
        result["bst_flag"] = bst_result["bst_flag"]
        result["bst_adj"] = bst_result["inheritance_adj"]
        result["bst_reason"] = bst_result["reason"]
        if bst_result["inheritance_adj"] > 0:
            forecast_adjusted += bst_result["inheritance_adj"]
            forecast_adjusted = round(forecast_adjusted, 3)
            result["warnings"].append(f"BST inheritance: +{bst_result['inheritance_adj']}C")

    # Step 4: Oracle simulation
    oracle = simulate_oracle_resolution(forecast_adjusted)
    result["oracle_value"] = oracle["oracle_value"]
    result["oracle_boundary"] = oracle["boundary_flag"]
    result["oracle_rounding"] = oracle["rounding_direction"]
    result["oracle_decimal"] = oracle["decimal_part"]
    result["bucket_probs"] = oracle["bucket_probs"]

    # Step 5: Bucket assignment - confidence boost at boundary
    if oracle["boundary_flag"]:
        if result["confidence"] == "LOW":
            result["confidence"] = "MEDIUM"
        result["warnings"].append(
            f"Boundary zone (decimal={oracle['decimal_part']:.2f}, "
            f"rounds {oracle['rounding_direction']})"
        )

    result["primary_bucket_temp"] = oracle["oracle_value"]

    # Step 6: Trade decision
    trade_decision = should_trade_v2(result, bucket_edges)
    result["trade_or_defer"] = trade_decision["action"]
    result["defer_reason"] = trade_decision.get("reason", "")
    if "direction" in trade_decision:
        result["trade_direction"] = trade_decision["direction"]
        result["trade_bucket"] = trade_decision["bucket"]
        result["trade_edge"] = trade_decision["edge"]

    # Log pipeline trace
    log.info(
        "V2 pipeline: raw=%.1f adj=%.3f (%s) oracle=%d (%s/%s) "
        "regime=%s conf=%s -> %s",
        forecast_raw, adj_applied, adj_reason,
        oracle["oracle_value"], oracle["rounding_direction"],
        "BOUNDARY" if oracle["boundary_flag"] else "clear",
        result["regime_code"], result["confidence"],
        result["trade_or_defer"]
    )

    return result


# === STEP 6: TRADE DECISION ===

def should_trade_v2(prediction_result: dict, bucket_edges: list = None,
                    market_implied_probs: dict = None,
                    min_edge: float = MIN_EDGE,
                    min_confidence: str = MIN_CONFIDENCE) -> dict:
    """
    Decide whether to trade based on edge and confidence.
    Returns dict with action (TRADE/DEFER), reason, and optional trade details.
    """
    confidence = prediction_result.get("confidence", "LOW")
    oracle_value = prediction_result.get("oracle_value")

    # Confidence gate
    if CONFIDENCE_LEVELS.get(confidence, 0) < CONFIDENCE_LEVELS.get(min_confidence, 0):
        return {
            "action": "DEFER",
            "reason": f"Confidence {confidence} < {min_confidence}"
        }

    # Defer if regime classification said to defer
    if prediction_result.get("regime_reason") in ("WIND_SPREAD", "MODEL_SPREAD"):
        return {
            "action": "DEFER",
            "reason": f"Regime DEFER: {prediction_result['regime_reason']}"
        }

    # Need oracle value
    if oracle_value is None:
        return {"action": "DEFER", "reason": "No oracle value"}

    # If market probabilities provided, compute edge
    if market_implied_probs and isinstance(market_implied_probs, dict):
        bucket_probs = prediction_result.get("bucket_probs", {})
        best_bucket = None
        best_edge = 0.0
        for temp_val, model_prob in bucket_probs.items():
            market_prob = market_implied_probs.get(temp_val, 0.0)
            edge = model_prob - market_prob
            if abs(edge) > abs(best_edge):
                best_edge = edge
                best_bucket = temp_val

        if abs(best_edge) < min_edge:
            return {
                "action": "DEFER",
                "reason": f"Edge {best_edge:.3f} < min {min_edge}"
            }

        direction = "BUY" if best_edge > 0 else "SELL"
        return {
            "action": "TRADE",
            "direction": direction,
            "bucket": best_bucket,
            "edge": round(best_edge, 4),
            "confidence": confidence,
            "reason": f"Edge {best_edge:+.3f} on bucket {best_bucket}"
        }

    # Without market probs: trade on oracle if boundary flag (deterministic edge)
    boundary_flag = prediction_result.get("oracle_boundary", False)
    if boundary_flag:
        rounding = prediction_result.get("oracle_rounding", "unknown")
        return {
            "action": "TRADE",
            "direction": "BUY",
            "bucket": oracle_value,
            "edge": None,
            "confidence": confidence,
            "reason": f"Deterministic rounding {rounding} at boundary"
        }

    return {
        "action": "DEFER",
        "reason": "No actionable edge (no market probs, no boundary)"
    }


# === UTILITY: FIND NEAREST BOUNDARY ===

def find_nearest_boundary(forecast: float, bucket_edges: list) -> float:
    """Find the closest bucket boundary to the forecast."""
    if not bucket_edges:
        return forecast
    return min(bucket_edges, key=lambda edge: abs(forecast - edge))


def compute_bucket_probs(oracle_value: int, boundary_flag: bool,
                         confidence: str, model_spread: float,
                         oracle_sigma: float = ORACLE_SIGMA) -> dict:
    """
    Compute probability distribution across integer temperature buckets.
    Uses normal distribution centered on oracle_value.
    """
    # Wider spread if boundary flag or low confidence
    sigma = oracle_sigma
    if boundary_flag:
        sigma *= 1.2
    if confidence == "LOW":
        sigma *= 1.3
    if model_spread > 2.0:
        sigma *= (1.0 + (model_spread - 2.0) * 0.2)

    # Generate probs for buckets within 3 sigma
    buckets = range(int(oracle_value - 3 * sigma), int(oracle_value + 3 * sigma) + 1)
    probs = {}
    for b in buckets:
        p = np.exp(-0.5 * ((b - oracle_value) / sigma) ** 2)
        p /= (sigma * math.sqrt(2 * math.pi))
        probs[b] = round(p, 6)

    # Normalize
    total = sum(probs.values())
    if total > 0:
        probs = {k: round(v / total, 4) for k, v in probs.items()}

    return probs
