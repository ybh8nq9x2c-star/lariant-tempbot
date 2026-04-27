"""Paper trading execution layer for EGLC V2."""
import json
import os
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from strategy.eglc_config import can_paper_trade_v2, get_effective_mode, EGLC_V2_MAX_POSITION_USD_TINY

log = logging.getLogger("tempbot")

PAPER_TRADE_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "eglc_paper_trades.jsonl"
)


def log_paper_trade(v2_result: Dict, market_info: Dict) -> Optional[Dict]:
    """
    Log a V2 paper trade intent. Called when V2 would trade but mode is paper.
    Returns the paper trade record dict or None if mode doesn't allow paper.
    """
    mode = get_effective_mode()
    if mode not in ("paper", "tiny_live"):
        return None

    record = {
        "ts": time.time(),
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "market_id": market_info.get("condition_id", ""),
        "slug": market_info.get("slug", ""),
        "target_date": market_info.get("target_date", ""),
        "city": market_info.get("city", ""),
        "bucket": v2_result.get("oracle_bucket", ""),
        "action": v2_result.get("decision", ""),
        "edge": v2_result.get("edge", 0),
        "confidence": v2_result.get("confidence", ""),
        "regime_code": v2_result.get("regime_code", ""),
        "reason_code": v2_result.get("reason_code", ""),
        "adjustment_c": v2_result.get("adjustment_c", 0),
        "forecast_raw": v2_result.get("forecast_raw", 0),
        "would_trade": v2_result.get("decision") == "TRADE",
        "paper_price": market_info.get("mid", 0),
        "paper_size": v2_result.get("suggested_size", 0),
        "status": "OPEN",
    }

    os.makedirs(os.path.dirname(PAPER_TRADE_LOG), exist_ok=True)
    with open(PAPER_TRADE_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")

    return record


def settle_paper_trades(settlement_data: Dict[str, Any]) -> int:
    """
    Mark paper trades as settled after market resolution.
    settlement_data: {condition_id: {resolved_bucket: str, actual_temp: float}}

    Returns the number of trades settled.
    """
    if not os.path.exists(PAPER_TRADE_LOG):
        return 0

    settled_count = 0
    records = []

    # Read all records
    try:
        with open(PAPER_TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    records.append(line)  # keep as-is
                    continue

                if rec.get("status") != "OPEN":
                    records.append(rec)
                    continue

                market_id = rec.get("market_id", "")
                settlement = settlement_data.get(market_id)
                if settlement is None:
                    records.append(rec)
                    continue

                # Settle this trade
                resolved_bucket = settlement.get("resolved_bucket", "")
                actual_temp = settlement.get("actual_temp", 0)
                trade_bucket = rec.get("bucket", "")
                trade_action = rec.get("action", "")
                paper_price = rec.get("paper_price", 0)

                # Determine if the trade would have won
                # BUY = predicted bucket matches resolved bucket
                # SELL = predicted bucket does NOT match resolved bucket
                if trade_action == "BUY":
                    trade_won = str(trade_bucket) == str(resolved_bucket)
                elif trade_action == "SELL":
                    trade_won = str(trade_bucket) != str(resolved_bucket)
                else:
                    trade_won = False

                if trade_won:
                    # Won: payout = (1 - paper_price) * size for BUY, price * size for SELL
                    size = rec.get("paper_size", EGLC_V2_MAX_POSITION_USD_TINY)
                    if trade_action == "BUY":
                        pnl = round((1.0 - paper_price) * size, 4)
                    else:
                        pnl = round(paper_price * size, 4)
                else:
                    # Lost: lose the amount risked
                    size = rec.get("paper_size", EGLC_V2_MAX_POSITION_USD_TINY)
                    if trade_action == "BUY":
                        pnl = round(-paper_price * size, 4)
                    else:
                        pnl = round(-(1.0 - paper_price) * size, 4)

                rec["status"] = "SETTLED"
                rec["settled_ts"] = time.time()
                rec["settled_ts_iso"] = datetime.now(timezone.utc).isoformat()
                rec["resolved_bucket"] = resolved_bucket
                rec["actual_temp"] = actual_temp
                rec["trade_won"] = trade_won
                rec["paper_pnl"] = pnl

                records.append(rec)
                settled_count += 1

    except Exception as e:
        log.warning("Failed to read paper trade log for settlement: %s", e)
        return 0

    # Rewrite the log with settled records
    try:
        with open(PAPER_TRADE_LOG, "w") as f:
            for rec in records:
                if isinstance(rec, str):
                    f.write(rec + "\n")
                else:
                    f.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        log.warning("Failed to rewrite paper trade log after settlement: %s", e)
        return 0

    log.info("Settled %d paper trades", settled_count)
    return settled_count


def compute_paper_pnl() -> Dict[str, Any]:
    """Read paper trade log and compute hypothetical PnL."""
    result: Dict[str, Any] = {
        "total_trades": 0,
        "open_trades": 0,
        "settled_trades": 0,
        "won": 0,
        "lost": 0,
        "hit_rate": 0.0,
        "total_pnl": 0.0,
        "avg_pnl_per_trade": 0.0,
        "max_win": 0.0,
        "max_loss": 0.0,
        "pnl_by_regime": {},
        "pnl_by_confidence": {},
        "daily_pnl": {},
        "cumulative_pnl": [],
        "open_positions": [],
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    if not os.path.exists(PAPER_TRADE_LOG):
        return result

    pnl_by_regime: Dict[str, Dict[str, float]] = {}
    pnl_by_conf: Dict[str, Dict[str, float]] = {}
    daily_pnl: Dict[str, float] = {}
    cumulative = 0.0
    cumulative_list = []

    try:
        with open(PAPER_TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                result["total_trades"] += 1
                status = rec.get("status", "OPEN")

                if status == "OPEN":
                    result["open_trades"] += 1
                    result["open_positions"].append({
                        "market_id": rec.get("market_id", ""),
                        "bucket": rec.get("bucket", ""),
                        "action": rec.get("action", ""),
                        "paper_price": rec.get("paper_price", 0),
                        "ts_iso": rec.get("ts_iso", ""),
                    })
                    continue

                if status != "SETTLED":
                    continue

                result["settled_trades"] += 1
                pnl = rec.get("paper_pnl", 0.0)
                result["total_pnl"] += pnl

                if rec.get("trade_won"):
                    result["won"] += 1
                else:
                    result["lost"] += 1

                if pnl > result["max_win"]:
                    result["max_win"] = pnl
                if pnl < result["max_loss"]:
                    result["max_loss"] = pnl

                # PnL by regime
                regime = rec.get("regime_code", "R0")
                if regime not in pnl_by_regime:
                    pnl_by_regime[regime] = {"count": 0, "pnl": 0.0}
                pnl_by_regime[regime]["count"] += 1
                pnl_by_regime[regime]["pnl"] += pnl

                # PnL by confidence
                conf = rec.get("confidence", "LOW")
                if conf not in pnl_by_conf:
                    pnl_by_conf[conf] = {"count": 0, "pnl": 0.0}
                pnl_by_conf[conf]["count"] += 1
                pnl_by_conf[conf]["pnl"] += pnl

                # Daily PnL
                ts_iso = rec.get("settled_ts_iso", rec.get("ts_iso", ""))
                day = ts_iso[:10] if ts_iso else "unknown"
                daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl

                # Cumulative
                cumulative += pnl
                cumulative_list.append({
                    "ts": rec.get("settled_ts", rec.get("ts", 0)),
                    "pnl": round(pnl, 4),
                    "cumulative": round(cumulative, 4),
                })

    except Exception as e:
        log.warning("Failed to compute paper PnL: %s", e)

    # Finalize computed fields
    settled = result["settled_trades"]
    if settled > 0:
        result["hit_rate"] = round(result["won"] / settled * 100, 2)
        result["avg_pnl_per_trade"] = round(result["total_pnl"] / settled, 4)

    result["total_pnl"] = round(result["total_pnl"], 4)
    result["pnl_by_regime"] = pnl_by_regime
    result["pnl_by_confidence"] = pnl_by_conf
    result["daily_pnl"] = {k: round(v, 4) for k, v in sorted(daily_pnl.items())}
    result["cumulative_pnl"] = cumulative_list

    return result
