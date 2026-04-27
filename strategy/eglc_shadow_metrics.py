"""Enhanced V1 vs V2 shadow comparison metrics."""
import json
import os
import logging
import time
from typing import Dict, Any
from collections import Counter, defaultdict

log = logging.getLogger("tempbot")

SHADOW_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "v1_v2_shadow_comparison.jsonl"
)


def compute_shadow_metrics(hours: int = 48) -> Dict[str, Any]:
    """
    Read shadow comparison log and compute aggregate metrics.

    Returns:
        total_evals: int
        total_eligible: int  (where V2 was not errored)
        bucket_changed_pct: float
        action_changed_pct: float
        v2_trade_v1_skip: int
        v2_skip_v1_trade: int
        reason_code_dist: dict
        regime_code_dist: dict
        neutral_fallback_pct: float
        missing_wind_pct: float
        confidence_dist: dict
        near_boundary_pct: float
        divergence_by_bucket: dict
    """
    metrics: Dict[str, Any] = {
        "total_evals": 0,
        "total_eligible": 0,
        "bucket_changed_pct": 0.0,
        "action_changed_pct": 0.0,
        "v2_trade_v1_skip": 0,
        "v2_skip_v1_trade": 0,
        "reason_code_dist": {},
        "regime_code_dist": {},
        "neutral_fallback_pct": 0.0,
        "missing_wind_pct": 0.0,
        "confidence_dist": {},
        "near_boundary_pct": 0.0,
        "divergence_by_bucket": {},
        "hours_covered": hours,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if not os.path.exists(SHADOW_LOG_PATH):
        return metrics

    cutoff_ts = time.time() - (hours * 3600)
    records = []

    try:
        with open(SHADOW_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = rec.get("ts", 0)
                if ts < cutoff_ts:
                    continue
                records.append(rec)
    except Exception as e:
        log.warning("Failed to read shadow log for metrics: %s", e)
        return metrics

    total = len(records)
    metrics["total_evals"] = total
    if total == 0:
        return metrics

    # Count eligible (V2 not errored)
    eligible = [r for r in records if r.get("v2_warnings") is not None or r.get("regime_code") is not None]
    metrics["total_eligible"] = len(eligible)

    # Bucket changed
    bucket_changed = sum(1 for r in records if r.get("bucket_changed"))
    metrics["bucket_changed_pct"] = round(bucket_changed / total * 100, 2)

    # Action/decision changed
    action_changed = sum(1 for r in records if r.get("decision_changed"))
    metrics["action_changed_pct"] = round(action_changed / total * 100, 2)

    # V2 trades when V1 skips
    metrics["v2_trade_v1_skip"] = sum(1 for r in records if r.get("v2_trades_when_v1_skips"))

    # V2 skips when V1 trades
    metrics["v2_skip_v1_trade"] = sum(1 for r in records if r.get("v1_trades_when_v2_defers"))

    # Reason code distribution (from defer_reason field)
    reason_counter: Dict[str, int] = Counter()
    for r in records:
        reason = r.get("defer_reason", "") or "OK"
        reason_counter[reason] += 1
    metrics["reason_code_dist"] = dict(reason_counter)

    # Regime code distribution
    regime_counter: Dict[str, int] = Counter()
    for r in records:
        regime = r.get("regime_code", "R0")
        regime_counter[regime] += 1
    metrics["regime_code_dist"] = dict(regime_counter)

    # Neutral fallback (R0 or R4 regime = neutral)
    neutral_count = sum(1 for r in records if r.get("regime_code") in ("R0", "R4"))
    metrics["neutral_fallback_pct"] = round(neutral_count / total * 100, 2)

    # Missing wind (regime_reason == NO_WIND_DATA or defer_reason mentions wind)
    missing_wind = sum(
        1 for r in records
        if r.get("regime_reason") == "NO_WIND_DATA"
        or "NO_WIND" in (r.get("defer_reason", "") or "")
    )
    metrics["missing_wind_pct"] = round(missing_wind / total * 100, 2)

    # Confidence distribution
    conf_counter: Dict[str, int] = Counter()
    for r in records:
        conf = r.get("confidence_v2", "LOW")
        conf_counter[conf] += 1
    metrics["confidence_dist"] = dict(conf_counter)

    # Near boundary (oracle_boundary == True)
    boundary_count = sum(1 for r in records if r.get("oracle_boundary"))
    metrics["near_boundary_pct"] = round(boundary_count / total * 100, 2)

    # Divergence by bucket threshold
    bucket_div: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "decision_changed": 0, "edge_delta_sum": 0.0})
    for r in records:
        threshold = str(r.get("threshold", "unknown"))
        bucket_div[threshold]["count"] += 1
        if r.get("decision_changed"):
            bucket_div[threshold]["decision_changed"] += 1
        edge_delta = r.get("edge_changed", 0.0)
        if edge_delta is not None:
            bucket_div[threshold]["edge_delta_sum"] += edge_delta
    # Finalize divergence by bucket
    metrics["divergence_by_bucket"] = {
        k: {
            "count": v["count"],
            "divergence_pct": round(v["decision_changed"] / v["count"] * 100, 2) if v["count"] else 0.0,
            "avg_edge_delta": round(v["edge_delta_sum"] / v["count"], 4) if v["count"] else 0.0,
        }
        for k, v in bucket_div.items()
    }

    return metrics


def write_shadow_report(metrics: Dict, output_path: str) -> None:
    """Write human-readable markdown report."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    lines = [
        "# V1/V2 Shadow Comparison Report",
        "",
        f"**Generated**: {metrics.get('computed_at', 'N/A')}",
        f"**Window**: {metrics.get('hours_covered', 'N/A')} hours",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Evaluations | {metrics.get('total_evals', 0)} |",
        f"| Total Eligible | {metrics.get('total_eligible', 0)} |",
        f"| Bucket Changed | {metrics.get('bucket_changed_pct', 0):.1f}% |",
        f"| Action Changed | {metrics.get('action_changed_pct', 0):.1f}% |",
        f"| V2 Trades / V1 Skips | {metrics.get('v2_trade_v1_skip', 0)} |",
        f"| V2 Skips / V1 Trades | {metrics.get('v2_skip_v1_trade', 0)} |",
        f"| Neutral Fallback | {metrics.get('neutral_fallback_pct', 0):.1f}% |",
        f"| Missing Wind | {metrics.get('missing_wind_pct', 0):.1f}% |",
        f"| Near Boundary | {metrics.get('near_boundary_pct', 0):.1f}% |",
        "",
    ]

    # Regime distribution
    regime_dist = metrics.get("regime_code_dist", {})
    if regime_dist:
        lines.append("## Regime Distribution")
        lines.append("")
        lines.append("| Regime | Count | % |")
        lines.append("|--------|-------|---|")
        total = sum(regime_dist.values())
        for regime in sorted(regime_dist.keys()):
            cnt = regime_dist[regime]
            lines.append(f"| {regime} | {cnt} | {cnt/total*100:.1f}% |")
        lines.append("")

    # Confidence distribution
    conf_dist = metrics.get("confidence_dist", {})
    if conf_dist:
        lines.append("## Confidence Distribution")
        lines.append("")
        lines.append("| Confidence | Count | % |")
        lines.append("|------------|-------|---|")
        total_c = sum(conf_dist.values())
        for conf in sorted(conf_dist.keys()):
            cnt = conf_dist[conf]
            lines.append(f"| {conf} | {cnt} | {cnt/total_c*100:.1f}% |")
        lines.append("")

    # Divergence by bucket
    div_by_bucket = metrics.get("divergence_by_bucket", {})
    if div_by_bucket:
        lines.append("## Divergence by Bucket")
        lines.append("")
        lines.append("| Threshold | Count | Divergence % | Avg Edge Δ |")
        lines.append("|-----------|-------|-------------|------------|")
        for thr, d in sorted(div_by_bucket.items()):
            lines.append(f"| {thr}°C | {d['count']} | {d['divergence_pct']:.1f}% | {d['avg_edge_delta']:+.4f} |")
        lines.append("")

    # Reason code distribution
    reason_dist = metrics.get("reason_code_dist", {})
    if reason_dist:
        lines.append("## Reason Code Distribution")
        lines.append("")
        lines.append("| Reason | Count |")
        lines.append("|--------|-------|")
        for reason, cnt in sorted(reason_dist.items(), key=lambda x: -x[1]):
            lines.append(f"| {reason} | {cnt} |")
        lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    log.info("Shadow report written to %s", output_path)


def write_shadow_json(metrics: Dict, output_path: str) -> None:
    """Write machine-readable JSON."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    log.info("Shadow JSON written to %s", output_path)
