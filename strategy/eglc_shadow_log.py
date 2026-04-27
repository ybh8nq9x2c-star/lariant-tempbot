"""
EGLC V1/V2 Shadow Comparison Logger

Every time the bot evaluates an EGLC market, this module logs both
V1 and V2 predictions side-by-side for later analysis.

Fields logged:
- forecast_raw, regime_code, regime_reason, adjustment_c
- bst_flag, oracle_bucket_v1, oracle_bucket_v2
- confidence_v2, trade_or_defer, defer_reason
- decision_changed, bucket_changed, edge_changed, size_changed
"""

import json
import time
import os
import logging
from typing import Dict, Any

log = logging.getLogger("tempbot")

# Log path: sibling to strategy/ directory
V1_V2_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "v1_v2_shadow_comparison.jsonl"
)


def _ensure_log_dir():
    """Ensure parent directory exists."""
    os.makedirs(os.path.dirname(V1_V2_LOG_PATH), exist_ok=True)


def log_shadow_comparison(v1_result: Dict[str, Any],
                          v2_result: Dict[str, Any],
                          market_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare V1 vs V2 predictions and append to JSONL log.

    Args:
        v1_result: Output from eglc_v1_legacy.predict_v1()
        v2_result: Output from eglc_v2.predict_market_resolution_v2()
        market_info: dict with market_id, city, token_id, condition_id, etc.

    Returns:
        diff dict with comparison fields
    """
    _ensure_log_dir()

    # Extract decisions
    v1_decision = v1_result.get("decision", "SKIP")
    v2_action = v2_result.get("trade_or_defer", "DEFER")
    v2_decision = v2_action if v2_action != "TRADE" else v2_result.get("trade_direction", "SKIP")

    # Extract edges
    v1_edge = v1_result.get("edge_fc", 0.0) or 0.0
    v2_edge = v2_result.get("trade_edge", 0.0) or 0.0

    # Extract buckets
    v1_bucket = v1_result.get("bucket", "")
    v2_bucket = v2_result.get("trade_bucket", "")

    # Build diff record
    diff = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "market_id": market_info.get("market_id", ""),
        "token_id": market_info.get("token_id", ""),
        "condition_id": market_info.get("condition_id", ""),
        "city": market_info.get("city", ""),
        "bucket_type": market_info.get("bucket_type", ""),
        "threshold": market_info.get("threshold", 0.0),

        # V1 fields
        "v1_decision": v1_decision,
        "v1_edge": v1_edge,
        "v1_bucket": v1_bucket,
        "v1_true_p": v1_result.get("true_p"),
        "v1_kelly_frac": v1_result.get("kelly_frac"),

        # V2 fields
        "v2_decision": v2_decision,
        "v2_edge": v2_edge,
        "v2_bucket": v2_bucket,
        "regime_code": v2_result.get("regime_code", "R0"),
        "regime_reason": v2_result.get("regime_reason", ""),
        "adjustment_c": v2_result.get("adjustment_c", 0.0),
        "bst_flag": v2_result.get("bst_flag", False),
        "bst_adj": v2_result.get("bst_adj", 0.0),
        "oracle_value": v2_result.get("oracle_value"),
        "oracle_boundary": v2_result.get("oracle_boundary", False),
        "oracle_rounding": v2_result.get("oracle_rounding"),
        "confidence_v2": v2_result.get("confidence", "LOW"),
        "trade_or_defer": v2_action,
        "defer_reason": v2_result.get("defer_reason", ""),
        "v2_warnings": v2_result.get("warnings", []),

        # Comparison flags
        "decision_changed": v1_decision != v2_decision,
        "bucket_changed": v1_bucket != v2_bucket,
        "edge_changed": round(v2_edge - v1_edge, 4),
        "size_changed": False,  # populated if sizing differs
        "v2_trades_when_v1_skips": v1_decision == "SKIP" and v2_action == "TRADE",
        "v1_trades_when_v2_defers": v1_decision in ("BUY", "SELL") and v2_action == "DEFER",
    }

    # Write to JSONL
    try:
        with open(V1_V2_LOG_PATH, "a") as f:
            f.write(json.dumps(diff, default=str) + "\n")
    except Exception as e:
        log.warning("Failed to write shadow comparison log: %s", e)

    # Also log summary
    if diff["decision_changed"]:
        log.info(
            "SHADOW_DIFF: market=%s V1=%s->V2=%s (regime=%s adj=%+.3f edge_delta=%+.4f)",
            diff["market_id"], v1_decision, v2_decision,
            diff["regime_code"], diff["adjustment_c"], diff["edge_changed"]
        )

    return diff


def get_shadow_stats() -> Dict[str, Any]:
    """
    Read the JSONL log and return aggregate comparison statistics.
    """
    stats = {
        "total_records": 0,
        "decision_changed": 0,
        "bucket_changed": 0,
        "v2_trades_when_v1_skips": 0,
        "v1_trades_when_v2_defers": 0,
        "regime_distribution": {},
        "defer_reasons": {},
        "avg_edge_delta": 0.0,
        "last_record_ts": None,
    }

    if not os.path.exists(V1_V2_LOG_PATH):
        return stats

    total_edge_delta = 0.0
    count_edge = 0

    try:
        with open(V1_V2_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                stats["total_records"] += 1
                stats["last_record_ts"] = rec.get("ts_iso")

                if rec.get("decision_changed"):
                    stats["decision_changed"] += 1
                if rec.get("bucket_changed"):
                    stats["bucket_changed"] += 1
                if rec.get("v2_trades_when_v1_skips"):
                    stats["v2_trades_when_v1_skips"] += 1
                if rec.get("v1_trades_when_v2_defers"):
                    stats["v1_trades_when_v2_defers"] += 1

                regime = rec.get("regime_code", "R0")
                stats["regime_distribution"][regime] = stats["regime_distribution"].get(regime, 0) + 1

                defer = rec.get("defer_reason", "")
                if defer:
                    stats["defer_reasons"][defer] = stats["defer_reasons"].get(defer, 0) + 1

                edge_delta = rec.get("edge_changed")
                if edge_delta is not None:
                    total_edge_delta += edge_delta
                    count_edge += 1

    except Exception as e:
        log.warning("Failed to read shadow stats: %s", e)

    if count_edge > 0:
        stats["avg_edge_delta"] = round(total_edge_delta / count_edge, 4)

    return stats


def write_p0_validation_status(status: str, details: str = "") -> str:
    """
    Write P0 validation status to file.
    status: PENDING | PASS | FAIL
    """
    from strategy.eglc_config import P0_STATUS_FILE

    data = {
        "status": status,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "details": details,
    }

    try:
        os.makedirs(os.path.dirname(P0_STATUS_FILE), exist_ok=True)
        with open(P0_STATUS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        log.info("P0 validation status updated: %s", status)
    except Exception as e:
        log.warning("Failed to write P0 status: %s", e)

    return status
