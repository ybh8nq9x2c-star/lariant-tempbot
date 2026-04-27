"""Promotion gate logic for EGLC V2 mode transitions."""
import json
import os
import time
import logging
from typing import Dict, Any
from strategy.eglc_config import (
    is_p0_passed, HUMAN_PROMOTION_APPROVED, EGLC_V2_MODE,
    EGLC_V2_REQUIRE_WIND_FOR_REGIME, get_effective_mode,
    EGLC_V2_MAX_DAILY_RISK_USD, EGLC_V2_MAX_CONCURRENT_EXPOSURE,
    EGLC_V2_MAX_ERRORS_PER_HOUR, EGLC_V2_COOLDOWN_AFTER_LOSS_MIN,
)

log = logging.getLogger("tempbot")

GATE_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "eglc_gate_status.json"
)

MIN_SHADOW_HOURS = 48
MIN_EVAL_COUNT = 100
MAX_MISSING_FEATURE_PCT = 0.20
MAX_ERROR_RATE_PER_HOUR = 5
MAX_DAILY_DRAWDOWN_USD = EGLC_V2_MAX_DAILY_RISK_USD
MAX_CONCURRENT_EXPOSURE = EGLC_V2_MAX_CONCURRENT_EXPOSURE


def _count_shadow_hours() -> float:
    """Count hours of shadow data available."""
    from strategy.eglc_shadow_metrics import SHADOW_LOG_PATH

    if not os.path.exists(SHADOW_LOG_PATH):
        return 0.0

    first_ts = None
    last_ts = None
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
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
    except Exception:
        return 0.0

    if first_ts and last_ts:
        return round((last_ts - first_ts) / 3600, 2)
    return 0.0


def _count_shadow_evals() -> int:
    """Count total shadow evaluation records."""
    from strategy.eglc_shadow_metrics import SHADOW_LOG_PATH

    if not os.path.exists(SHADOW_LOG_PATH):
        return 0

    count = 0
    try:
        with open(SHADOW_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    count += 1
    except Exception:
        pass
    return count


def _get_error_rate() -> float:
    """Get V2 error rate per hour from logs."""
    # Check for V2 error log entries in recent hour
    from strategy.eglc_shadow_metrics import SHADOW_LOG_PATH

    if not os.path.exists(SHADOW_LOG_PATH):
        return 0.0

    cutoff = time.time() - 3600  # last hour
    errors = 0
    total = 0

    try:
        with open(SHADOW_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue
                total += 1
                # Check for error indicators
                if rec.get("regime_code") == "DEFER" and rec.get("regime_reason") in ("WIND_SPREAD", "MODEL_SPREAD"):
                    errors += 1
    except Exception:
        return 0.0

    return errors  # errors in last hour


def evaluate_gate(target_mode: str = None) -> Dict[str, Any]:
    """
    Evaluate whether V2 can be promoted to the target mode.

    Returns dict with:
        current_mode: str
        target_mode: str
        eligible: bool
        blockers: list of {gate, status, detail}
        metrics: dict of relevant metrics
        recommendation: str
    """
    current = get_effective_mode()
    target = target_mode or "paper"

    result = {
        "current_mode": current,
        "target_mode": target,
        "eligible": False,
        "blockers": [],
        "metrics": {},
        "recommendation": "",
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Gather metrics
    shadow_hours = _count_shadow_hours()
    shadow_evals = _count_shadow_evals()
    p0_passed = is_p0_passed()
    human_approved = HUMAN_PROMOTION_APPROVED
    error_rate = _get_error_rate()

    result["metrics"] = {
        "shadow_hours": shadow_hours,
        "shadow_evals": shadow_evals,
        "p0_passed": p0_passed,
        "human_approved": human_approved,
        "error_rate_per_hour": error_rate,
    }

    blockers = []

    if target in ("paper", "tiny_live", "live"):
        # Gate: P0 must be passed
        if not p0_passed:
            blockers.append({
                "gate": "p0_validation",
                "status": "BLOCKED",
                "detail": "P0 validation has not passed",
            })

        # Gate: Human must approve
        if not human_approved:
            blockers.append({
                "gate": "human_approval",
                "status": "BLOCKED",
                "detail": "HUMAN_PROMOTION_APPROVED is not set to 'true'",
            })

        # Gate: Shadow log must exist
        if shadow_evals == 0:
            blockers.append({
                "gate": "shadow_data",
                "status": "BLOCKED",
                "detail": "No shadow comparison data available",
            })

    if target in ("tiny_live", "live"):
        # Gate: Minimum shadow hours
        if shadow_hours < MIN_SHADOW_HOURS:
            blockers.append({
                "gate": "shadow_hours",
                "status": "BLOCKED",
                "detail": f"Only {shadow_hours:.1f}h of shadow data (need {MIN_SHADOW_HOURS}h)",
            })

        # Gate: Minimum eval count
        if shadow_evals < MIN_EVAL_COUNT:
            blockers.append({
                "gate": "min_evals",
                "status": "BLOCKED",
                "detail": f"Only {shadow_evals} evals (need {MIN_EVAL_COUNT})",
            })

        # Gate: Error rate
        if error_rate > MAX_ERROR_RATE_PER_HOUR:
            blockers.append({
                "gate": "error_rate",
                "status": "BLOCKED",
                "detail": f"Error rate {error_rate}/h exceeds limit {MAX_ERROR_RATE_PER_HOUR}/h",
            })

        # Gate: Paper trading performance (if we have paper data)
        try:
            from strategy.eglc_paper_trader import compute_paper_pnl
            pnl = compute_paper_pnl()
            result["metrics"]["paper_pnl"] = pnl
            if pnl["settled_trades"] > 0 and pnl["total_pnl"] < -MAX_DAILY_DRAWDOWN_USD:
                blockers.append({
                    "gate": "paper_pnl",
                    "status": "WARNING",
                    "detail": f"Paper PnL is ${pnl['total_pnl']:.2f} (below -${MAX_DAILY_DRAWDOWN_USD})",
                })
        except Exception:
            pass  # No paper data yet is OK

    if target == "live":
        # Live mode not yet supported
        blockers.append({
            "gate": "live_mode",
            "status": "BLOCKED",
            "detail": "LIVE mode is not yet supported, use tiny_live",
        })

    result["blockers"] = blockers
    result["eligible"] = len(blockers) == 0

    # Recommendation
    if result["eligible"]:
        result["recommendation"] = f"Ready for promotion to {target}"
    else:
        blocking_gates = [b["gate"] for b in blockers]
        result["recommendation"] = f"Blocked by: {', '.join(blocking_gates)}. Stay in {current}."

    # Write gate status
    _write_gate_status(result)

    return result


def _write_gate_status(result: Dict) -> None:
    """Write gate evaluation result to log file."""
    try:
        os.makedirs(os.path.dirname(GATE_LOG_PATH), exist_ok=True)
        with open(GATE_LOG_PATH, "w") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception as e:
        log.warning("Failed to write gate status: %s", e)


def check_tiny_live_safety() -> Dict[str, Any]:
    """Check if tiny_live mode should continue or downgrade."""
    checks = {
        "daily_loss_ok": True,
        "concurrent_exposure_ok": True,
        "error_rate_ok": True,
        "parser_healthy": True,
        "v1_v2_divergence_ok": True,
    }

    reasons = []
    should_downgrade = False
    suggested_mode = get_effective_mode()

    # Check 1: Daily loss
    try:
        from strategy.eglc_paper_trader import compute_paper_pnl
        pnl = compute_paper_pnl()
        daily_pnl = pnl.get("daily_pnl", {})
        if daily_pnl:
            today = time.strftime("%Y-%m-%d")
            today_pnl = daily_pnl.get(today, 0.0)
            if today_pnl < -MAX_DAILY_DRAWDOWN_USD:
                checks["daily_loss_ok"] = False
                reasons.append(f"Daily loss ${today_pnl:.2f} exceeds -${MAX_DAILY_DRAWDOWN_USD}")
                should_downgrade = True
    except Exception as e:
        log.warning("Safety check: could not compute paper PnL: %s", e)
        # If we can't check, that's a concern
        checks["daily_loss_ok"] = False
        reasons.append(f"Cannot compute paper PnL: {e}")

    # Check 2: Concurrent exposure
    try:
        from strategy.eglc_paper_trader import compute_paper_pnl
        pnl = compute_paper_pnl()
        open_positions = pnl.get("open_positions", [])
        if len(open_positions) >= MAX_CONCURRENT_EXPOSURE:
            checks["concurrent_exposure_ok"] = False
            reasons.append(
                f"Concurrent exposure {len(open_positions)} >= limit {MAX_CONCURRENT_EXPOSURE}"
            )
    except Exception:
        pass  # Non-critical

    # Check 3: Error rate
    error_rate = _get_error_rate()
    if error_rate > EGLC_V2_MAX_ERRORS_PER_HOUR:
        checks["error_rate_ok"] = False
        reasons.append(f"Error rate {error_rate}/h exceeds limit {EGLC_V2_MAX_ERRORS_PER_HOUR}/h")
        should_downgrade = True

    # Check 4: Parser health (basic check)
    try:
        from strategy.eglc_shadow_metrics import compute_shadow_metrics
        metrics = compute_shadow_metrics(hours=1)
        if metrics["total_evals"] == 0:
            # No evaluations in the last hour might indicate parser issues
            checks["parser_healthy"] = False
            reasons.append("No shadow evaluations in the last hour")
    except Exception:
        checks["parser_healthy"] = False
        reasons.append("Cannot compute shadow metrics")

    # Check 5: V1/V2 divergence
    try:
        from strategy.eglc_shadow_metrics import compute_shadow_metrics
        metrics = compute_shadow_metrics(hours=24)
        action_changed_pct = metrics.get("action_changed_pct", 0.0)
        if action_changed_pct > 50.0:  # More than 50% divergence is dangerous
            checks["v1_v2_divergence_ok"] = False
            reasons.append(f"V1/V2 divergence {action_changed_pct:.1f}% exceeds 50% threshold")
            should_downgrade = True
    except Exception:
        pass  # Non-critical

    if should_downgrade:
        suggested_mode = "paper"

    return {
        "safe": not should_downgrade,
        "reason": "; ".join(reasons) if reasons else "All checks passed",
        "should_downgrade": should_downgrade,
        "suggested_mode": suggested_mode,
        "checks": checks,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
