#!/usr/bin/env python3
"""
analyze_impact.py  —  Phase 2a (Dynamic Sigma/Bias) Performance Monitor
==========================================================================
Compares pre-patch (skewnorm + static sigma) vs post-patch (norm.cdf +
empirical calibration matrix) trade quality, and shows real-time fair-value
pricing for all current open positions.

Usage:
    python3 analyze_impact.py
    python3 analyze_impact.py --verbose    # show per-trade rows
    python3 analyze_impact.py --live-only  # skip historical, show live state
"""

import json, math, re, sys, os, argparse
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from typing import Optional

try:
    from scipy.stats import norm as _norm
    SCIPY_OK = True
except ImportError:
    SCIPY_OK = False

try:
    import pandas as pd
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE  = os.path.join(BASE_DIR, "trades.jsonl")
MONITOR_FILE = os.path.join(BASE_DIR, "monitor_state.json")
LOG_FILE     = os.path.join(BASE_DIR, "tempbot.log")

PHASE2A_PATCH_TS = datetime(2026, 3, 21, 13, 1, 19, tzinfo=timezone.utc).timestamp()

_CALIB_MATRIX = {
    ("london", 2): {1: ( 0.14, 0.85), 3: ( 0.61, 1.93), 5: (-0.43, 3.24)},
    ("london", 3): {1: ( 0.08, 0.67), 3: (-0.06, 1.96), 5: ( 0.31, 3.23)},
    ("seoul",  2): {1: (-0.18, 1.37), 3: (-0.39, 3.39), 5: (-1.28, 5.89)},
    ("seoul",  3): {1: ( 0.03, 0.85), 3: (-0.13, 1.78), 5: ( 0.69, 3.08)},
}
_SIGMA_FALLBACK = {
    ("london", None): 2.6,
    ("seoul",  2):    4.2,
    ("seoul",  3):    2.8,
    ("seoul",  None): 3.5,
}
_TDAYS_NODES   = [1, 3, 5]
_CITY_SIGMA_BASE = {"london": 2.2, "seoul": 2.8}

# ── Calibration helpers ───────────────────────────────────────────────────────

def get_dynamic_calibration(city, month, tdays):
    nearest_t = min(_TDAYS_NODES, key=lambda t: abs(tdays - t))
    row = _CALIB_MATRIX.get((city.lower(), month))
    if row is not None:
        return row[nearest_t]
    sigma_fb = _SIGMA_FALLBACK.get(
        (city.lower(), month),
        _SIGMA_FALLBACK.get((city.lower(), None), 2.6))
    return (0.0, sigma_fb)

def phase1_sigma(city, tdays):
    base = _CITY_SIGMA_BASE.get(city.lower(), 2.5)
    return base * math.sqrt(max(tdays, 0.04) / 7.0)

def sigma_label(city, month, tdays):
    nearest_t = min(_TDAYS_NODES, key=lambda t: abs(tdays - t))
    row = _CALIB_MATRIX.get((city.lower(), month))
    if row:
        bias, sigma = row[nearest_t]
        src = f"matrix[{city[:3]},m{month:02d},T{nearest_t}]"
    else:
        bias, sigma = get_dynamic_calibration(city, month, tdays)
        src = f"fallback[{city[:3]},m{month:02d}]"
    return sigma, src

def phase2a_fair(forecast_temp, bucket_type, threshold,
                 city, tdays, month, observed_max=None):
    if not SCIPY_OK:
        return None
    bias, sigma = get_dynamic_calibration(city, month, tdays)
    adj = forecast_temp - bias
    btype = (bucket_type or "").lower()
    if "higher" in btype or "above" in btype:
        if observed_max is not None and observed_max >= threshold:
            return 0.995
        return 1.0 - _norm.cdf(threshold - 0.5, loc=adj, scale=sigma)
    elif "lower" in btype or "below" in btype:
        return _norm.cdf(threshold + 0.5, loc=adj, scale=sigma)
    else:
        return (_norm.cdf(threshold + 0.5, loc=adj, scale=sigma)
                - _norm.cdf(threshold - 0.5, loc=adj, scale=sigma))

# ── Bucket parser ─────────────────────────────────────────────────────────────

def parse_bucket_type(title):
    q = title.lower()
    for pat, btype in [
        (r"(\d+).*?or higher", "or_higher"), (r"at least (\d+)", "or_higher"),
        (r"(\d+).*?or lower",  "or_lower"),  (r"at most (\d+)",  "or_lower"),
        (r"be\s+(\d+)",        "exact"),      (r"(\d+)\s*\xb0?c", "exact"),
    ]:
        m = re.search(pat, q)
        if m:
            return btype, float(m.group(1))
    m = re.search(r"(\d+)", q)
    return ("exact", float(m.group(1))) if m else (None, None)

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_trades():
    records = []
    if not os.path.exists(TRADES_FILE):
        return records
    with open(TRADES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records

def load_monitor():
    if not os.path.exists(MONITOR_FILE):
        return {}
    with open(MONITOR_FILE, encoding="utf-8") as f:
        return json.load(f)

def load_latest_forecasts():
    london_fc, seoul_fc = {}, {}
    metar_lon = metar_seo = None
    if not os.path.exists(LOG_FILE):
        return london_fc, seoul_fc, metar_lon, metar_seo
    try:
        with open(LOG_FILE, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4_000_000))
            blob = fh.read().decode("utf-8", errors="replace")
        lines = blob.splitlines()[-50_000:]
    except Exception:
        lines = []
    for line in lines:
        m = re.search(r"ENSEMBLE LONDON: (\{.*?\})", line)
        if m:
            try:
                london_fc = json.loads(m.group(1).replace("'", '"'))
            except Exception:
                pass
        m = re.search(r"ENSEMBLE SEOUL: (\{.*?\})", line)
        if m:
            try:
                seoul_fc = json.loads(m.group(1).replace("'", '"'))
            except Exception:
                pass
        m = re.search(r"METAR EGLC: max_today=(\d+)", line)
        if m:
            metar_lon = float(m.group(1))
        m = re.search(r"METAR RKSI: max_today=(\d+)", line)
        if m:
            metar_seo = float(m.group(1))
    return london_fc, seoul_fc, metar_lon, metar_seo

# ── Print helpers ─────────────────────────────────────────────────────────────

BOLD = "\033[1m"; CYAN = "\033[96m"; GREEN = "\033[92m"
RED  = "\033[91m"; YEL  = "\033[93m"; RST  = "\033[0m"

def hr(c="─", w=74): print(c * w)

def header(title):
    print(); hr("="); print(f"{BOLD}{CYAN}  {title}{RST}"); hr("=")

def print_table(rows, cols):
    if not rows:
        print("  (no data)"); return
    if PANDAS_OK:
        df = pd.DataFrame(rows)[cols]
        print(df.to_string(index=False))
    else:
        widths = {c: max(len(c), max(len(str(r.get(c,""))) for r in rows)) for c in cols}
        print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
        print("  " + "  ".join("-"*widths[c] for c in cols))
        for r in rows:
            print("  " + "  ".join(str(r.get(c,"")).ljust(widths[c]) for c in cols))

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1: Trade classification
# ═════════════════════════════════════════════════════════════════════════════

def analyze_trades(all_records, verbose=False):
    header("SECTION 1 -- Trade Classification  (pre vs post Phase 2a)")
    entries = [r for r in all_records if r.get("event") == "ENTRY"]
    pre  = [e for e in entries if e["ts"] < PHASE2A_PATCH_TS]
    post = [e for e in entries if e["ts"] >= PHASE2A_PATCH_TS]

    patch_dt = datetime.fromtimestamp(PHASE2A_PATCH_TS, tz=timezone.utc)
    print(f"  Phase 2a patch time : {patch_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Total ENTRY records : {len(entries)}")
    print(f"  Pre-patch trades    : {len(pre)}")
    print(f"  Post-patch trades   : {len(post)}")

    for label, group in [("PRE-PATCH  (Phase 1 skewnorm)", pre),
                          ("POST-PATCH (Phase 2a norm.cdf)", post)]:
        if not group:
            print(f"\n  -- {label}: no data yet --"); continue
        edges    = [e.get("edge_entry") for e in group if e.get("edge_entry") is not None]
        tdays_v  = [e.get("Tdays",0) for e in group if e.get("Tdays") is not None]
        cities   = Counter(e.get("city","?") for e in group)
        strats   = Counter(e.get("strategy","?") for e in group)
        print(f"\n  -- {BOLD}{label}{RST} ({len(group)} trades) --")
        if edges:
            avg_e = sum(edges)/len(edges)
            clr = GREEN if avg_e > 0 else RED
            print(f"     Avg edge_entry : {clr}{avg_e:+.4f}{RST}"
                  f"   min={min(edges):+.4f}  max={max(edges):+.4f}")
        if tdays_v:
            print(f"     Avg T-days     : {sum(tdays_v)/len(tdays_v):.2f}"
                  f"   min={min(tdays_v):.2f}  max={max(tdays_v):.2f}")
        print(f"     Cities         : {dict(cities)}")
        print(f"     Strategies     : {dict(strats)}")

    if verbose and entries:
        rows = []
        for e in entries:
            tag = "POST" if e["ts"] >= PHASE2A_PATCH_TS else "pre"
            dt_s = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            rows.append({
                "Tag": tag, "Date": dt_s,
                "City": e.get("city","?")[:3], "Strat": e.get("strategy","?")[:4],
                "Side": e.get("side","?"), "Price": f"{e.get('price',0):.3f}",
                "Tdays": f"{e.get('Tdays',0):.1f}",
                "Edge": f"{e.get('edge_entry',0):+.4f}",
                "FairP": f"{e.get('fair_prob',0):.4f}" if e.get("fair_prob") else "N/A",
            })
        print()
        print_table(rows, ["Tag","Date","City","Strat","Side","Price","Tdays","Edge","FairP"])

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2: Sigma distribution
# ═════════════════════════════════════════════════════════════════════════════

def analyze_sigma_distribution(all_records):
    header("SECTION 2 -- Sigma Distribution  (calibration matrix node usage)")
    entries = [r for r in all_records if r.get("event") == "ENTRY"]

    node_counter = Counter()
    sigma_edges  = defaultdict(list)
    p1_vs_p2a    = []

    for e in entries:
        city  = e.get("city", "london")
        tdays = e.get("Tdays") or 1.0
        ts    = e.get("ts", PHASE2A_PATCH_TS)
        month = datetime.fromtimestamp(ts, tz=timezone.utc).month

        nearest_t = min(_TDAYS_NODES, key=lambda t: abs(tdays - t))
        row = _CALIB_MATRIX.get((city.lower(), month))
        if row:
            bias, sigma = row[nearest_t]
            node_key = f"{city[:3]:3s}  m{month:02d}  T{nearest_t}  sig={sigma:.2f}"
        else:
            bias, sigma = get_dynamic_calibration(city, month, tdays)
            node_key = f"{city[:3]:3s}  m{month:02d}  FALLBACK  sig={sigma:.2f}"

        node_counter[node_key] += 1
        edge = e.get("edge_entry")
        if edge is not None:
            sigma_edges[round(sigma, 2)].append(edge)

        p1_sig = phase1_sigma(city, tdays)
        p1_vs_p2a.append({"city": city, "tdays": tdays, "month": month,
                           "p1": p1_sig, "p2a": sigma, "delta": sigma - p1_sig})

    total = max(len(entries), 1)
    print(f"\n  Calibration-matrix node hits across {len(entries)} ENTRY trades:")
    print(f"\n  {'Node':46s}  {'N':>4}  {'%':>6}  Bar")
    print(f"  {'─'*46}  {'─'*4}  {'─'*6}  {'─'*15}")
    for node, cnt in node_counter.most_common():
        bar = "#" * cnt + "." * max(0, 10 - cnt)
        print(f"  {node:46s}  {cnt:4d}  {cnt/total*100:5.1f}%  {bar}")

    print(f"\n  Avg edge_entry by effective Phase-2a sigma value:")
    print(f"\n  {'sigma':>7}  {'N':>5}  {'AvgEdge':>9}  {'MinEdge':>9}  {'MaxEdge':>9}")
    print(f"  {'─'*7}  {'─'*5}  {'─'*9}  {'─'*9}  {'─'*9}")
    for sig in sorted(sigma_edges):
        evs = sigma_edges[sig]
        avg = sum(evs)/len(evs)
        clr = GREEN if avg > 0 else RED
        print(f"  {sig:7.2f}  {len(evs):5d}  {clr}{avg:+9.4f}{RST}  "
              f"{min(evs):9.4f}  {max(evs):9.4f}")

    if p1_vs_p2a:
        deltas = [x["delta"] for x in p1_vs_p2a]
        avg_d  = sum(deltas)/len(deltas)
        tighter = sum(1 for d in deltas if d < 0)
        wider   = len(deltas) - tighter
        direction = "TIGHTER" if avg_d < 0 else "WIDER"
        clr = GREEN if avg_d < 0 else YEL
        print(f"\n  Phase 1 vs Phase 2a sigma comparison ({len(p1_vs_p2a)} trades):")
        print(f"    Avg delta sigma (P2a - P1) : {clr}{avg_d:+.3f}deg  [{direction}]{RST}")
        print(f"    Trades tighter (P2a < P1)  : {tighter}")
        print(f"    Trades wider   (P2a > P1)  : {wider}")
        print(f"    Note: tighter sigma = higher confidence, sharper pricing")

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3: Spread analysis
# ═════════════════════════════════════════════════════════════════════════════

def analyze_spread(all_records):
    header("SECTION 3 -- Entry Spread Analysis  (fair price vs market price)")
    entries = [r for r in all_records if r.get("event") == "ENTRY"]

    # Reconstruct fair from edge_entry + entry_price if fair_prob=0
    valid = []
    for e in entries:
        edge  = e.get("edge_entry")
        price = e.get("price")
        fp    = e.get("fair_prob")
        mp    = e.get("mkt_prob")
        if edge is not None and price is not None:
            fair = fp if (fp is not None and fp != 0) else (price + edge)
            mkt  = mp if (mp is not None and mp != 0) else price
            valid.append({**e, "_fair": fair, "_mkt": mkt})

    pre  = [e for e in valid if e["ts"] < PHASE2A_PATCH_TS]
    post = [e for e in valid if e["ts"] >= PHASE2A_PATCH_TS]

    for label, group in [("PRE-PATCH", pre), ("POST-PATCH", post)]:
        if not group:
            print(f"\n  -- {label}: no data --"); continue
        spreads = [abs(e["_fair"] - e["_mkt"]) for e in group]
        edges   = [e.get("edge_entry", 0) for e in group if e.get("edge_entry") is not None]
        buys    = sum(1 for e in group if e.get("side") == "BUY")
        sells   = sum(1 for e in group if e.get("side") == "SELL")

        print(f"\n  -- {BOLD}{label}{RST} ({len(group)} trades) --")
        if spreads:
            avg_sp = sum(spreads)/len(spreads)
            print(f"     Avg |fair - mkt|   : {avg_sp:.4f}")
            print(f"     Max |fair - mkt|   : {max(spreads):.4f}")
        if edges:
            avg_e = sum(edges)/len(edges)
            clr = GREEN if avg_e > 0 else RED
            print(f"     Avg edge_entry     : {clr}{avg_e:+.4f}{RST}")
        print(f"     BUY / SELL         : {buys} / {sells}")

        by_city = defaultdict(list)
        for e in group:
            by_city[e.get("city","?")].append(e.get("edge_entry",0))
        for city in sorted(by_city):
            ces = by_city[city]
            avg_ce = sum(ces)/len(ces)
            clr = GREEN if avg_ce > 0 else RED
            print(f"     {city:8s} avg edge : {clr}{avg_ce:+.4f}{RST}  (n={len(ces)})")

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4: Monitor metrics
# ═════════════════════════════════════════════════════════════════════════════

def analyze_monitor_metrics():
    header("SECTION 4 -- Monitor Metrics  (monitor_state.json)")
    mon = load_monitor()
    if not mon:
        print("  monitor_state.json not found."); return

    ts_data = mon.get("trades_summary", {})
    params  = mon.get("params", {})

    print(f"\n  {'Metric':28s}  Value")
    print(f"  {'─'*28}  {'─'*20}")
    for k, v in [
        ("win_rate",        ts_data.get("win_rate")),
        ("sharpe",          ts_data.get("sharpe")),
        ("sortino",         ts_data.get("sortino")),
        ("calmar",          ts_data.get("calmar")),
        ("profit_factor",   ts_data.get("profit_factor")),
        ("expectancy",      ts_data.get("expectancy")),
        ("avg_win",         ts_data.get("avg_win")),
        ("avg_loss",        ts_data.get("avg_loss")),
        ("total_pnl",       ts_data.get("total_pnl")),
        ("n_resolved",      ts_data.get("n_resolved")),
        ("n_wins / losses", f"{ts_data.get('n_wins',0)} / {ts_data.get('n_losses',0)}"),
        ("daily_pnl",       mon.get("daily_pnl")),
        ("capital",         mon.get("capital")),
        ("total_exposure",  mon.get("total_exposure")),
        ("drawdown_pct",    mon.get("drawdown_pct")),
        ("peak_capital",    mon.get("peak_capital")),
    ]:
        if v is None:
            v_s = "N/A (no resolved trades)"
        elif isinstance(v, float):
            v_s = f"{v:.4f}"
        else:
            v_s = str(v)
        print(f"  {k:28s}  {v_s}")

    print(f"\n  pnl_by_city      : {ts_data.get('pnl_by_city',{})}")
    print(f"  pnl_by_strategy  : {ts_data.get('pnl_by_strategy',{})}")
    print(f"  active params    : {params}")
    print(f"  exposure_by_city : {mon.get('exposure_by_city',{})}")

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5: Live state (fair values for open positions)
# ═════════════════════════════════════════════════════════════════════════════

def analyze_live_state():
    header("SECTION 5 -- Live Fair-Value Pricing  (Phase 2a recomputation)")

    mon = load_monitor()
    if not mon:
        print("  monitor_state.json not found."); return

    mon_ts = mon.get("ts", 0)
    now    = datetime.now(timezone.utc)
    age_s  = now.timestamp() - mon_ts
    mon_dt = datetime.fromtimestamp(mon_ts, tz=timezone.utc) if mon_ts else None

    print(f"  monitor_state   : {age_s:.0f}s old  "
          f"({mon_dt.strftime('%H:%M:%S UTC') if mon_dt else 'N/A'})")
    print(f"  Capital         : ${mon.get('capital',0):.4f}")
    print(f"  Total exposure  : ${mon.get('total_exposure',0):.4f}")
    print(f"  Open positions  : {mon.get('open_positions_count', len(mon.get('positions',{})))}")

    london_fc, seoul_fc, metar_lon, metar_seo = load_latest_forecasts()
    print(f"\n  ENSEMBLE LONDON : {london_fc}")
    print(f"  ENSEMBLE SEOUL  : {seoul_fc}")
    print(f"  METAR EGLC (today max) : {metar_lon}C")
    print(f"  METAR RKSI (today max) : {metar_seo}C")

    positions = mon.get("positions", {})
    if not positions:
        print("\n  No open positions."); return

    rows = []
    for tid, pos in positions.items():
        city      = pos.get("city", "?")
        avg_price = pos.get("avg_price", 0)
        edge_in   = pos.get("edge", 0)
        size      = pos.get("size", 0)
        notional  = pos.get("notional", size * avg_price)
        title     = pos.get("title", "")

        btype, threshold = parse_bucket_type(title)

        # Extract market date from title
        dm = re.search(r"(January|February|March|April|May|June|July|August|"
                       r"September|October|November|December)\s+(\d+)", title)
        mkt_date = None
        if dm:
            from calendar import month_name as _mn
            _mmap = {m: i for i, m in enumerate(_mn) if m}
            mon_num = _mmap.get(dm.group(1), 0)
            day_num = int(dm.group(2))
            if mon_num:
                mkt_date = f"{now.year}-{mon_num:02d}-{day_num:02d}"

        # T-days
        if mkt_date:
            mkt_dt = datetime.strptime(mkt_date, "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=timezone.utc)
            tdays = max(0.0, (mkt_dt - now).total_seconds() / 86400)
        else:
            tdays = 1.0

        # Market month
        mkt_month = int(mkt_date[5:7]) if mkt_date else now.month

        # Forecast for this date
        fc_map = london_fc if city == "london" else seoul_fc
        ft     = fc_map.get(mkt_date) if mkt_date else None
        obs    = metar_lon if city == "london" else metar_seo

        # Phase 2a recomputed fair price
        fair = None
        if ft is not None and btype is not None:
            fair = phase2a_fair(ft, btype, threshold, city, tdays, mkt_month,
                                observed_max=obs)

        # Sigma info
        p2a_sig, _ = sigma_label(city, mkt_month, tdays)
        p1_sig     = phase1_sigma(city, tdays)

        # Edge NOW vs entry edge
        mkt_edge_now = f"{fair - avg_price:+.4f}" if fair is not None else "N/A"

        rows.append({
            "City":    city[:3].upper(),
            "Date":    mkt_date or "?",
            "T(d)":    f"{tdays:.2f}",
            "BktType": (btype or "?")[:9],
            "Thr":     f"{threshold:.0f}" if threshold is not None else "?",
            "AvgPx":   f"{avg_price:.3f}",
            "EdgeIn":  f"{edge_in:+.4f}",
            "FcTemp":  f"{ft:.1f}" if ft is not None else "N/A",
            "FairP2a": f"{fair:.4f}" if fair is not None else "N/A",
            "EdgeNow": mkt_edge_now,
            "sigP2a":  f"{p2a_sig:.2f}",
            "sigP1":   f"{p1_sig:.2f}",
            "Sz":      f"{size:.1f}",
            "Notl($)": f"{notional:.2f}",
        })

    cols = ["City","Date","T(d)","BktType","Thr","AvgPx","EdgeIn",
            "FcTemp","FairP2a","EdgeNow","sigP2a","sigP1","Sz","Notl($)"]
    print()
    print_table(rows, cols)

    total_notl = sum(pos.get("notional", 0) for pos in positions.values())
    all_edges  = [pos.get("edge", 0) for pos in positions.values()]
    print(f"\n  Total open notional : ${total_notl:.2f}")
    if all_edges:
        avg_e = sum(all_edges)/len(all_edges)
        clr = GREEN if avg_e > 0 else RED
        print(f"  Avg entry edge      : {clr}{avg_e:+.4f}{RST}")
        print(f"  Positive-edge pos   : "
              f"{sum(1 for e in all_edges if e>0)} / {len(all_edges)}")

    # Sigma heat summary for current positions
    print(f"\n  Phase 2a sigma heat (current positions):")
    sig_map = defaultdict(int)
    for row in rows:
        sig_map[row["sigP2a"]] += 1
    for sig, cnt in sorted(sig_map.items()):
        bar = "#" * cnt
        print(f"    sigma={sig}  -> {cnt} position(s)  {bar}")

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Phase 2a Impact Analyzer")
    parser.add_argument("--verbose",   action="store_true")
    parser.add_argument("--live-only", action="store_true")
    args = parser.parse_args()

    print()
    hr("=")
    print(f"{BOLD}  TempBot Phase 2a Impact Analyzer"
          f"  |  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}{RST}")
    hr("=")
    print(f"  Patch epoch : {datetime.fromtimestamp(PHASE2A_PATCH_TS, tz=timezone.utc).isoformat()}")
    print(f"  scipy OK    : {SCIPY_OK}   |  pandas OK : {PANDAS_OK}")

    all_records = load_trades()

    if not args.live_only:
        analyze_trades(all_records, verbose=args.verbose)
        analyze_sigma_distribution(all_records)
        analyze_spread(all_records)
        analyze_monitor_metrics()

    analyze_live_state()

    print()
    hr("=")
    print(f"{BOLD}  Done.{RST}  Re-run in ~30s for updated live state.")
    hr("=")
    print()

if __name__ == "__main__":
    main()
