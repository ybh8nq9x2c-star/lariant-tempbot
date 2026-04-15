"""
Family-level risk monitor for mutually exclusive weather buckets.
A family = city + event_date + market_type (exact / or_higher).
Only one exact bucket can resolve YES per family → risk must be computed at family level.

Designed as a stateless helper: call build_families() with current positions,
call check_family_gate() before placing a BUY.  No heavy deps, no threads.
"""
import re, time, logging
from typing import Dict, Tuple, Optional

log = logging.getLogger("family_risk")

# ── Configurable thresholds (overridable via config_override.json hot-reload) ──
MAX_FAMILY_WORSTCASE_LOSS_USD = 5.0   # block if worst-case net < -$5
MAX_FAMILY_OUTSIDE_LOSS_USD   = 5.0   # block if uncovered scenario net < -$5
MAX_FAMILY_COST_USD           = 8.0   # block if total family cost > $8
ENABLE_FAMILY_RISK_GATE       = True  # master switch

# ── Title parser ──────────────────────────────────────────────────────────────
_TITLE_RE = re.compile(
    r'(?:highest|lowest)\s+temperature\s+in\s+(london|seoul)\s+be\s+'
    r'(\d+)\s*°?C?\s+on\s+((?:january|february|march|april|may|june|'
    r'july|august|september|october|november|december)\s+\d{1,2})',
    re.IGNORECASE)

_OR_HIGHER_RE = re.compile(r'(\d+)\s*°?C?\s*or\s+higher', re.IGNORECASE)
_OR_LOWER_RE  = re.compile(r'(\d+)\s*°?C?\s*or\s+lower',  re.IGNORECASE)

_MONTHS = {m: i for i, m in enumerate(
    ['', 'january', 'february', 'march', 'april', 'may', 'june',
     'july', 'august', 'september', 'october', 'november', 'december']
) if m}


def parse_family_key(title: str) -> Optional[dict]:
    """Parse a weather-market title into family components.
    Returns dict with city, date, threshold, market_type, family_key or None."""
    if not title:
        return None
    m = _TITLE_RE.search(title)
    if not m:
        return None
    city = m.group(1).lower()
    threshold = float(m.group(2))
    date_raw = m.group(3).strip()

    # Parse "March 19" → "2026-03-19"
    parts = date_raw.split()
    if len(parts) != 2:
        return None
    month_num = _MONTHS.get(parts[0].lower())
    if not month_num:
        return None
    try:
        day = int(parts[1])
    except ValueError:
        return None
    from datetime import datetime, timezone
    year = datetime.now(timezone.utc).year
    event_date = f"{year}-{month_num:02d}-{day:02d}"

    # Detect market type
    if _OR_HIGHER_RE.search(title):
        mtype = "or_higher"
    elif _OR_LOWER_RE.search(title):
        mtype = "or_lower"
    else:
        mtype = "exact"

    family_key = f"{city}_{event_date}_{mtype}"
    return {
        "city": city,
        "event_date": event_date,
        "threshold": threshold,
        "market_type": mtype,
        "family_key": family_key,
    }


def build_families(positions: dict) -> dict:
    """Build family-level risk from current positions dict.
    positions: {token_id: {size, avg_price, notional, city, title, edge, ...}}
    Returns: {family_key: {city, event_date, market_type, n_positions, total_cost,
              covered_buckets, bucket_cost_map, bucket_shares_map, bucket_net_map,
              best_case_net, worst_case_net, uncovered_loss, last_update_ts, partial}}
    """
    families: Dict[str, dict] = {}
    for tid, pos in positions.items():
        title = pos.get("title", "")
        fk = parse_family_key(title)
        if not fk:
            continue
        key = fk["family_key"]
        if key not in families:
            families[key] = {
                "city": fk["city"],
                "event_date": fk["event_date"],
                "market_type": fk["market_type"],
                "n_positions": 0,
                "total_cost": 0.0,
                "total_shares": 0.0,
                "covered_buckets": [],
                "bucket_cost_map": {},
                "bucket_shares_map": {},
                "bucket_net_map": {},
                "best_case_net": 0.0,
                "worst_case_net": 0.0,
                "uncovered_loss": 0.0,
                "last_update_ts": time.time(),
                "partial": False,
            }
        fam = families[key]
        bk = str(fk["threshold"])
        cost = pos.get("notional") or 0.0
        shares = pos.get("size") or 0.0
        avg_price = pos.get("avg_price") or 0.0

        # Skip ghost positions
        if cost < 1e-6 and shares < 1e-6:
            continue

        # If avg_price is 0 but we have cost and shares, flag partial
        if avg_price < 1e-9 and cost > 0:
            fam["partial"] = True

        fam["n_positions"] += 1
        fam["total_cost"] += cost
        fam["total_shares"] += shares
        fam["covered_buckets"].append(bk)
        fam["bucket_cost_map"][bk] = fam["bucket_cost_map"].get(bk, 0.0) + cost
        fam["bucket_shares_map"][bk] = fam["bucket_shares_map"].get(bk, 0.0) + shares

    # Compute payoff scenarios for each family
    for key, fam in families.items():
        total_cost = fam["total_cost"]
        bucket_net = {}

        if fam["market_type"] == "exact":
            # Mutually exclusive: exactly one bucket resolves YES (pays $1/share)
            # If bucket_i wins: net = shares_i * 1.0 - total_cost
            # If uncovered bucket wins: net = 0 - total_cost = -total_cost
            for bk, shares in fam["bucket_shares_map"].items():
                bucket_net[bk] = round(shares - total_cost, 6)
            fam["bucket_net_map"] = bucket_net

            covered_nets = list(bucket_net.values()) if bucket_net else [0.0]
            uncovered_loss = round(-total_cost, 6)
            fam["uncovered_loss"] = uncovered_loss
            fam["best_case_net"] = round(max(covered_nets), 6)
            fam["worst_case_net"] = round(min(min(covered_nets), uncovered_loss), 6)
        else:
            # or_higher / or_lower: NOT mutually exclusive
            # Conservative: treat worst case as -total_cost
            fam["bucket_net_map"] = {}
            fam["uncovered_loss"] = round(-total_cost, 6)
            fam["best_case_net"] = round(fam["total_shares"] - total_cost, 6)
            fam["worst_case_net"] = round(-total_cost, 6)
            fam["partial"] = True  # mark as conservative estimate

    return families


def check_family_gate(
    positions: dict,
    proposed_token_id: str,
    proposed_title: str,
    proposed_price: float,
    proposed_size_usd: float,
) -> Tuple[bool, str]:
    """Check if a proposed BUY order passes family-level risk gate.
    Returns (allowed: bool, reason: str).
    If ENABLE_FAMILY_RISK_GATE is False, always allows."""
    if not ENABLE_FAMILY_RISK_GATE:
        return True, "gate_disabled"

    fk = parse_family_key(proposed_title)
    if not fk:
        return True, "unparseable_title"

    # Simulate: add proposed order to a copy of positions
    sim_positions = dict(positions)
    proposed_shares = proposed_size_usd / proposed_price if proposed_price > 1e-9 else 0.0
    existing = sim_positions.get(proposed_token_id, {})
    sim_positions[proposed_token_id] = {
        "size": (existing.get("size") or 0.0) + proposed_shares,
        "avg_price": proposed_price,
        "notional": (existing.get("notional") or 0.0) + proposed_size_usd,
        "city": fk["city"],
        "title": proposed_title,
    }

    families = build_families(sim_positions)
    fam = families.get(fk["family_key"])
    if not fam:
        return True, "no_family_built"

    # Gate checks
    if fam["total_cost"] > MAX_FAMILY_COST_USD:
        return False, f"family_cost={fam['total_cost']:.2f}>${MAX_FAMILY_COST_USD:.2f}"

    if fam["worst_case_net"] < -MAX_FAMILY_WORSTCASE_LOSS_USD:
        return False, f"worst_case={fam['worst_case_net']:.2f}<-${MAX_FAMILY_WORSTCASE_LOSS_USD:.2f}"

    if fam["uncovered_loss"] < -MAX_FAMILY_OUTSIDE_LOSS_USD:
        return False, f"uncovered_loss={fam['uncovered_loss']:.2f}<-${MAX_FAMILY_OUTSIDE_LOSS_USD:.2f}"

    return True, "ok"


# ── SELL analysis (asymmetric: warn, don't hard-block protective sells) ──────

# Threshold: how much worse can worst-case get before we warn/block discretionary SELL
MAX_FAMILY_SELL_WORSENING_USD = 3.0   # warn if SELL worsens worst-case by >$3

def check_family_sell(
    positions: dict,
    proposed_token_id: str,
    proposed_title: str,
    proposed_price: float,
    proposed_size_usd: float,
    is_protective: bool = False,
) -> Tuple[bool, str]:
    """Evaluate a proposed SELL against family risk.

    Returns (allowed: bool, reason: str).
    - Protective SELLs (stoploss, position-exit) are ALWAYS allowed.
    - Discretionary MM SELLs are allowed if they reduce or maintain family risk.
    - Discretionary MM SELLs are BLOCKED only if they materially worsen
      worst-case by more than MAX_FAMILY_SELL_WORSENING_USD.
    """
    if not ENABLE_FAMILY_RISK_GATE:
        return True, "gate_disabled"

    # Protective sells: never block
    if is_protective:
        return True, "protective_sell"

    fk = parse_family_key(proposed_title)
    if not fk:
        return True, "unparseable_title"

    # Current family state (before sell)
    families_before = build_families(positions)
    fam_before = families_before.get(fk["family_key"])

    if not fam_before:
        # No family exposure → selling into a naked short: this is
        # the worsening case, but for weather markets on Polymarket
        # SELL means selling existing shares, not shorting. So if
        # we don't hold any family position, this is a no-op.
        return True, "no_family_exposure"

    # Simulate: reduce position by sell amount
    sim_positions = {}
    for tid, pos in positions.items():
        sim_positions[tid] = dict(pos)  # shallow copy

    if proposed_token_id in sim_positions:
        p = sim_positions[proposed_token_id]
        sell_shares = proposed_size_usd / proposed_price if proposed_price > 1e-9 else 0.0
        new_size = max(0.0, (p.get("size") or 0.0) - sell_shares)
        new_notional = max(0.0, (p.get("notional") or 0.0) - proposed_size_usd)
        if new_notional < 1e-6:
            del sim_positions[proposed_token_id]
        else:
            sim_positions[proposed_token_id] = {
                **p,
                "size": new_size,
                "notional": new_notional,
            }

    families_after = build_families(sim_positions)
    fam_after = families_after.get(fk["family_key"])

    # If family disappears entirely after sell, that's reducing risk
    if not fam_after:
        return True, "family_cleared"

    wc_before = fam_before["worst_case_net"]
    wc_after  = fam_after["worst_case_net"]
    worsening = wc_before - wc_after  # positive means after is worse

    # Sell improves or maintains worst-case → always allow
    if worsening <= 0:
        return True, f"risk_reduced wc={wc_before:.2f}->{wc_after:.2f}"

    # Sell worsens worst-case but within tolerance
    if worsening <= MAX_FAMILY_SELL_WORSENING_USD:
        return True, f"minor_worsening +${worsening:.2f} wc={wc_after:.2f}"

    # Material worsening: block discretionary SELL
    return False, (f"sell_worsens_family wc={wc_before:.2f}->{wc_after:.2f} "
                   f"delta=${worsening:.2f}>${MAX_FAMILY_SELL_WORSENING_USD:.2f}")
