#!/usr/bin/env python3
"""
reconcile_full_ledger.py — Phase 1 Quant Audit: Full Ledger Reconciliation
===========================================================================
Author  : Lead Quant Data Engineer / Python Architect
Created : 2026-03-20
Purpose : Discover all historical trade files, normalise events, perform
          FIFO PnL reconciliation, and emit a full markdown audit report.

Constraints:
- READ-ONLY on all source files
- Does NOT restart the bot or touch trading logic
- Idempotent: safe to run multiple times
"""

import json
import os
import csv
import time
import math
from collections import defaultdict
from datetime import datetime, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────
BOT_DIR   = "/home/ubuntu/polymarket-temp-bot"
HOME_DIR  = "/home/ubuntu"
OUT_DIR   = BOT_DIR  # output artefacts written here

LEDGER_FILE            = os.path.join(BOT_DIR, "trades.jsonl")
FULL_EVENTS_CSV        = os.path.join(OUT_DIR, "full_reconciled_events.csv")
FIFO_MATCHES_CSV       = os.path.join(OUT_DIR, "reconciled_fifo_matches.csv")
ORPHAN_EXITS_CSV       = os.path.join(OUT_DIR, "unresolved_orphans.csv")
RESOLUTION_PNL_CSV     = os.path.join(OUT_DIR, "resolution_pnl.csv")
AUDIT_REPORT_MD        = os.path.join(OUT_DIR, "quant_audit_report.md")

# ── SCHEMA FIELDS (normalised) ─────────────────────────────────────────────
SCHEMA = [
    "event", "ts", "ts_human", "fill_id", "token_id", "strategy", "city",
    "bucket", "side", "price", "size_contracts", "notional_usd",
    "edge_entry", "exit_reason", "exit_channel", "exit_tier",
    "entry_ts", "pnl_usd", "condition_id", "source_file",
]

# ── HELPER: safe float ─────────────────────────────────────────────────────
def sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default

def ts_human(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)

# ── STEP 1: DISCOVER ALL HISTORICAL FILES ──────────────────────────────────
def discover_files():
    """Walk /home/ubuntu for any .jsonl, .csv, .log, .bak files with trade events."""
    candidates = []
    search_dirs = [HOME_DIR]
    skip_prefix = ("/home/ubuntu/venv", "/home/ubuntu/.local")

    for root, dirs, files in os.walk(HOME_DIR):
        # Prune virtualenv / package directories for speed
        dirs[:] = [d for d in dirs if not any(
            os.path.join(root, d).startswith(sp) for sp in skip_prefix
        )]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in (".jsonl", ".csv", ".log", ".bak", ".old", ".backup") or \
               fname.startswith("trades") or fname == "nohup.out":
                full = os.path.join(root, fname)
                try:
                    sz = os.path.getsize(full)
                    mt = os.path.getmtime(full)
                    candidates.append((full, sz, mt))
                except OSError:
                    pass
    return candidates

def count_trade_keywords(path):
    """Count lines containing ENTRY|EXIT_FILL|RESOLUTION in a file."""
    try:
        count = 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "ENTRY" in line or "EXIT_FILL" in line or "RESOLUTION" in line:
                    count += 1
        return count
    except Exception:
        return 0

# ── STEP 2: READ & NORMALISE EVENTS ───────────────────────────────────────
def parse_jsonl_file(path):
    """Parse a JSONL file and yield normalised event dicts."""
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event_type = obj.get("event", "")
                if event_type not in ("ENTRY", "EXIT_FILL", "RESOLUTION"):
                    continue
                norm = normalise_event(obj, path)
                events.append(norm)
    except Exception as e:
        print(f"  [WARN] Cannot read {path}: {e}")
    return events

def normalise_event(obj, source_file):
    """Normalise any event dict to the canonical schema."""
    ev = obj.get("event", "UNKNOWN")
    token_id = str(obj.get("token_id", "") or "")
    price    = sf(obj.get("price"))
    raw_size = sf(obj.get("size_contracts")) or (
        sf(obj.get("notional_usd")) / price if price > 1e-9 else 0.0
    )
    notional = sf(obj.get("notional_usd")) or (raw_size * price)

    # exit_reason: prefer explicit field, fall back to strategy on SELL
    exit_reason = obj.get("exit_reason")
    if exit_reason is None and ev == "EXIT_FILL":
        exit_reason = obj.get("strategy")

    return {
        "event":         ev,
        "ts":            sf(obj.get("ts")),
        "ts_human":      ts_human(obj.get("ts")),
        "fill_id":       str(obj.get("fill_id") or ""),
        "token_id":      token_id,
        "strategy":      str(obj.get("strategy") or ""),
        "city":          str(obj.get("city") or ""),
        "bucket":        str(obj.get("bucket") or "fill_confirmed"),
        "side":          str(obj.get("side") or ""),
        "price":         price,
        "size_contracts": raw_size,
        "notional_usd":  notional,
        "edge_entry":    sf(obj.get("edge_entry")),
        "exit_reason":   str(exit_reason or ""),
        "exit_channel":  str(obj.get("exit_channel") or ""),  # not yet in schema — will be ""
        "exit_tier":     str(obj.get("exit_tier") or ""),      # not yet in schema — will be ""
        "entry_ts":      sf(obj.get("entry_ts")),
        "pnl_usd":       sf(obj.get("pnl_usd")),
        "condition_id":  str(obj.get("condition_id") or ""),
        "source_file":   source_file,
    }

# ── STEP 3: FIFO PnL MATCHING ──────────────────────────────────────────────
def fifo_match(events):
    """
    Match ENTRY BUY events to EXIT_FILL SELL events per token_id (FIFO).
    Returns: matched pairs list, orphan exits list, unclosed entries list.
    """
    # Group by token_id
    by_token = defaultdict(lambda: {"entries": [], "exits": []})
    for ev in events:
        if ev["event"] == "ENTRY" and ev["side"] == "BUY":
            by_token[ev["token_id"]]["entries"].append(ev)
        elif ev["event"] == "EXIT_FILL" and ev["side"] == "SELL":
            by_token[ev["token_id"]]["exits"].append(ev)

    matches       = []
    orphan_exits  = []
    unclosed      = []
    match_id      = 0

    for token_id, buckets in by_token.items():
        # Sort by timestamp
        entries = sorted(buckets["entries"], key=lambda x: x["ts"])
        exits   = sorted(buckets["exits"],  key=lambda x: x["ts"])

        # FIFO: consume entries queue against exits queue (by contracts)
        entry_q = [(e["size_contracts"], e) for e in entries]
        exit_q  = [(x["size_contracts"], x) for x in exits]

        ei = 0  # entry pointer
        xi = 0  # exit pointer
        entry_rem = 0.0
        exit_rem  = 0.0
        cur_entry = None
        cur_exit  = None

        while ei < len(entry_q) or exit_rem > 1e-6:
            # Load next entry if needed
            if cur_entry is None and ei < len(entry_q):
                entry_rem, cur_entry = entry_q[ei]
                ei += 1
            # Load next exit if needed
            if cur_exit is None and xi < len(exit_q):
                exit_rem, cur_exit = exit_q[xi]
                xi += 1

            if cur_entry is None:
                # More exits than entries — orphan
                if cur_exit is not None:
                    orphan_exits.append(cur_exit.copy())
                    cur_exit = None
                while xi < len(exit_q):
                    orphan_exits.append(exit_q[xi][1].copy())
                    xi += 1
                break

            if cur_exit is None:
                # No more exits — unclosed entry
                unclosed.append({**cur_entry, "_remaining_contracts": round(entry_rem, 6)})
                cur_entry = None
                while ei < len(entry_q):
                    unclosed.append({**entry_q[ei][1], "_remaining_contracts": round(entry_q[ei][0], 6)})
                    ei += 1
                break

            # Match: consume min(entry_rem, exit_rem)
            matched_contracts = min(entry_rem, exit_rem)
            buy_price  = sf(cur_entry["price"])
            sell_price = sf(cur_exit["price"])
            pnl        = matched_contracts * (sell_price - buy_price)
            hold_secs  = cur_exit["ts"] - cur_entry["ts"]

            match_id += 1
            matches.append({
                "match_id":           match_id,
                "token_id":           token_id,
                "matched_contracts":  round(matched_contracts, 6),
                "entry_ts":           cur_entry["ts"],
                "entry_ts_human":     cur_entry["ts_human"],
                "exit_ts":            cur_exit["ts"],
                "exit_ts_human":      cur_exit["ts_human"],
                "buy_price":          round(buy_price, 6),
                "sell_price":         round(sell_price, 6),
                "pnl_usd":            round(pnl, 6),
                "hold_secs":          round(hold_secs, 1),
                "exit_reason":        cur_exit["exit_reason"],
                "entry_strategy":     cur_entry["strategy"],
                "exit_strategy":      cur_exit["strategy"],
                "city":               cur_entry["city"],
                "entry_edge":         cur_entry["edge_entry"],
                "entry_fill_id":      cur_entry["fill_id"],
                "exit_fill_id":       cur_exit["fill_id"],
            })

            entry_rem -= matched_contracts
            exit_rem  -= matched_contracts

            if entry_rem < 1e-6:
                cur_entry = None
            if exit_rem < 1e-6:
                cur_exit = None

        # Flush any remaining exit as orphan
        if cur_exit is not None and exit_rem > 1e-6:
            orphan_exits.append(cur_exit.copy())
            cur_exit = None
        while xi < len(exit_q):
            orphan_exits.append(exit_q[xi][1].copy())
            xi += 1

    return matches, orphan_exits, unclosed

# ── STEP 4: RESOLUTION PnL (deduplicated) ─────────────────────────────────
def collect_resolution_pnl(events):
    """Deduplicate RESOLUTION events by (token_id, round(entry_ts, 1))."""
    seen = set()
    rows = []
    for ev in events:
        if ev["event"] != "RESOLUTION":
            continue
        key = (ev["token_id"], round(ev["entry_ts"], 1))
        if key in seen:
            continue
        seen.add(key)
        rows.append(ev)
    return rows

# ── STEP 5: CSV WRITERS ────────────────────────────────────────────────────
def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Wrote {len(rows)} rows → {path}")

# ── STEP 6: MARKDOWN REPORT ────────────────────────────────────────────────
def write_report(md_path, discovery_table, events, matches, orphans, unclosed,
                 resolution_rows, fifo_total, resolution_total,
                 data_quality_notes):
    lines = []
    def h(n, t): lines.append("#" * n + " " + t)
    def p(t=""): lines.append(t)
    def row(*cols): lines.append("| " + " | ".join(str(c) for c in cols) + " |")
    def sep(*cols): lines.append("| " + " | ".join("-" * max(3, len(str(c))) for c in cols) + " |")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    h(1, f"Phase 1 Quant Audit Report")
    p(f"**Generated:** {now_str}  ")
    p(f"**Bot file:** `tempbot_v427_stable_20260313.py`  ")
    p(f"**Ledger:** `{LEDGER_FILE}`  ")
    p()

    # Discovery table
    h(2, "1. File Discovery")
    row("File", "Size (bytes)", "Last Modified", "Trade Events Found")
    sep("File", "Size (bytes)", "Last Modified", "Trade Events Found")
    for item in discovery_table:
        row(f"`{item['path']}`", item['size'], item['mtime'], item['count'])
    p()

    # Event summary
    h(2, "2. Event Stream Summary")
    entry_count  = sum(1 for e in events if e["event"] == "ENTRY")
    exit_count   = sum(1 for e in events if e["event"] == "EXIT_FILL")
    res_count    = sum(1 for e in events if e["event"] == "RESOLUTION")
    token_ids    = set(e["token_id"] for e in events)
    p(f"| Metric | Value |")
    p(f"| ------ | ----- |")
    p(f"| Total events | {len(events)} |")
    p(f"| ENTRY events | {entry_count} |")
    p(f"| EXIT_FILL events | {exit_count} |")
    p(f"| RESOLUTION events | {res_count} |")
    p(f"| Unique token_ids | {len(token_ids)} |")
    p(f"| FIFO matched pairs | {len(matches)} |")
    p(f"| Orphan exits (no matching ENTRY) | {len(orphans)} |")
    p(f"| Unclosed entries (no EXIT yet) | {len(unclosed)} |")
    p()

    # PnL summary
    h(2, "3. PnL Reconciliation")
    h(3, "3a. FIFO Realized PnL")
    p(f"| Metric | Value |")
    p(f"| ------ | ----- |")
    p(f"| Total FIFO matched contracts | {sum(m['matched_contracts'] for m in matches):.4f} |")
    p(f"| **FIFO Total Realized PnL** | **${fifo_total:.4f}** |")
    wins  = sum(1 for m in matches if m["pnl_usd"] > 0)
    losses = sum(1 for m in matches if m["pnl_usd"] < 0)
    p(f"| Winning matches | {wins} |")
    p(f"| Losing matches | {losses} |")
    if matches:
        avg_pnl = fifo_total / len(matches)
        p(f"| Average PnL per match | ${avg_pnl:.4f} |")
    p()

    # PnL by exit_reason
    by_exit = defaultdict(list)
    for m in matches:
        by_exit[m["exit_reason"] or "UNKNOWN"].append(m["pnl_usd"])
    if by_exit:
        p("**FIFO PnL by exit_reason:**")
        p()
        row("exit_reason", "count", "total_pnl_usd", "avg_pnl_usd")
        sep("exit_reason", "count", "total_pnl_usd", "avg_pnl_usd")
        for reason, pnls in sorted(by_exit.items()):
            row(reason, len(pnls), f"${sum(pnls):.4f}", f"${sum(pnls)/len(pnls):.4f}")
        p()

    # PnL by strategy
    by_strat = defaultdict(list)
    for m in matches:
        by_strat[m["entry_strategy"] or "UNKNOWN"].append(m["pnl_usd"])
    if by_strat:
        p("**FIFO PnL by entry strategy:**")
        p()
        row("strategy", "count", "total_pnl_usd", "avg_pnl_usd")
        sep("strategy", "count", "total_pnl_usd", "avg_pnl_usd")
        for strat, pnls in sorted(by_strat.items()):
            row(strat, len(pnls), f"${sum(pnls):.4f}", f"${sum(pnls)/len(pnls):.4f}")
        p()

    h(3, "3b. Resolution PnL (RESOLUTION events)")
    p(f"| Metric | Value |")
    p(f"| ------ | ----- |")
    p(f"| Resolution events (deduplicated) | {len(resolution_rows)} |")
    p(f"| **Resolution Total PnL** | **${resolution_total:.4f}** |")
    p()

    # Orphan exits
    h(2, "4. Orphan Exit Analysis")
    if orphans:
        p(f"**{len(orphans)} orphan EXIT_FILL records** (SELL fills with no matching BUY ENTRY in ledger)")
        p()
        row("token_id[:16]", "ts_human", "strategy", "side", "price", "size_contracts", "exit_reason")
        sep("token_id[:16]", "ts_human", "strategy", "side", "price", "size_contracts", "exit_reason")
        for o in orphans[:30]:
            row(o["token_id"][:16], o["ts_human"], o["strategy"], o["side"],
                f"{o['price']:.4f}", f"{o['size_contracts']:.2f}", o["exit_reason"])
        if len(orphans) > 30:
            p(f"... and {len(orphans) - 30} more (see {ORPHAN_EXITS_CSV})")
    else:
        p("No orphan exits found.")
    p()

    # Unclosed entries
    h(2, "5. Unclosed Entries (Open Positions)")
    if unclosed:
        p(f"**{len(unclosed)} ENTRY records** have no matching EXIT_FILL (still open or lost)")
        p()
        row("token_id[:16]", "ts_human", "strategy", "price", "remaining_contracts", "notional_usd", "edge_entry")
        sep("token_id[:16]", "ts_human", "strategy", "price", "remaining_contracts", "notional_usd", "edge_entry")
        for u in unclosed[:30]:
            row(u["token_id"][:16], u["ts_human"], u["strategy"],
                f"{u['price']:.4f}", f"{u.get('_remaining_contracts', u['size_contracts']):.2f}",
                f"{u['notional_usd']:.4f}", f"{u['edge_entry']:.4f}")
    else:
        p("All entries have matching exits.")
    p()

    # Source trace findings
    h(2, "6. Source Trace Findings")
    p("### 6a. record_trade() — bucket field bug")
    p()
    p("**Root cause:** `portfolio_state.py:get_order_meta()` always returns")
    p('`"bucket_key": "fill_confirmed"` regardless of the actual market bucket.')
    p("The real market bucket computed by `parse_bucket(q_text)` in the MM loop")
    p("(line ~3363 of the bot) is **never stored** in `STATE.orders[order_id]`")
    p("(which is an `OrderRecord` dataclass with no `bucket` field) and therefore")
    p("never recoverable at fill time.")
    p()
    p("**Impact:** Every ENTRY and EXIT_FILL event in `trades.jsonl` has")
    p('`bucket = "fill_confirmed"` — this field carries zero informational value.')
    p()
    p("### 6b. exit_tier / exit_channel — missing fields")
    p()
    p("`check_position_exit()` computes `exit_tier` (e.g. `TIER0_TIME`, `TIER1_PROFIT`,")
    p("`TIER1B_DECAY`, `TIER2_PARTIAL`) but passes `strategy='STOPLOSS'` to `place_order()`")
    p("unconditionally (`[C1-FIX]` comment). The `exit_tier` string is **never passed** to")
    p("`record_trade()` / `_ledger_write()`. The EXIT_FILL events therefore carry no")
    p("`exit_channel` or `exit_tier` fields.")
    p()
    p("**Impact:** Cannot distinguish TIER0_TIME vs TIER1_PROFIT vs TIER1B_DECAY from the")
    p("ledger alone. All position-exit sells appear as `exit_reason=STOPLOSS`.")
    p()
    p("### 6c. Data quality notes")
    p()
    for note in data_quality_notes:
        p(f"- {note}")
    p()

    # Logging patch plan
    h(2, "7. Logging Patch Plan (data hygiene only)")
    p("The following changes are applied to `tempbot_v427_stable_20260313.py`:")
    p()
    p("1. **`portfolio_state.py` — `OrderRecord`**: add `bucket: str = 'fill_confirmed'`")
    p("   field. `place_order()` will populate it from `parse_bucket()` result.")
    p()
    p("2. **`portfolio_state.py` — `get_order_meta()`**: return `bucket_key=o.bucket`")
    p("   instead of the hardcoded `'fill_confirmed'`.")
    p()
    p("3. **`tempbot_v427_stable_20260313.py` — `check_position_exit()`**: pass")
    p("   `exit_tier` string to `place_order()` via a new kwarg, stored in")
    p("   `STATE` under `_exit_tier_cache[token_id]`.")
    p()
    p("4. **`_ledger_write` / `record_trade()`**: add `exit_channel` and `exit_tier`")
    p("   fields to the EXIT_FILL JSON payload (read from `STATE._exit_tier_cache`).")
    p()
    p("**None of these changes touch edge formula, Kelly sizing, stoploss thresholds,")
    p("or any trading logic.**")
    p()

    # Artefacts
    h(2, "8. Output Artefacts")
    p(f"| File | Description |")
    p(f"| ---- | ----------- |")
    p(f"| `{FULL_EVENTS_CSV}` | All normalised events, sorted by ts |")
    p(f"| `{FIFO_MATCHES_CSV}` | FIFO matched ENTRY→EXIT pairs with PnL |")
    p(f"| `{ORPHAN_EXITS_CSV}` | EXIT_FILL records with no matching ENTRY |")
    p(f"| `{RESOLUTION_PNL_CSV}` | Deduplicated RESOLUTION PnL events |")
    p(f"| `{AUDIT_REPORT_MD}` | This report |")
    p()

    content = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Wrote audit report → {md_path}")
    return content

# ── MAIN ───────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("PHASE 1 QUANT AUDIT — Full Ledger Reconciliation")
    print(f"Started: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    # 1. Discover files
    print("\n[1] Discovering trade files...")
    candidates = discover_files()
    discovery_table = []
    all_events = []
    seen_fill_ids = set()

    for path, size, mtime in sorted(candidates, key=lambda x: x[2]):
        mtime_str = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        kw_count  = count_trade_keywords(path)
        discovery_table.append({
            "path":  path,
            "size":  size,
            "mtime": mtime_str,
            "count": kw_count,
        })
        print(f"  {path} | {size}B | {mtime_str} | {kw_count} keywords")

        # Parse only JSONL files for actual events
        if path.endswith(".jsonl"):
            evs = parse_jsonl_file(path)
            for ev in evs:
                fid = ev["fill_id"]
                # Dedup by fill_id if present
                if fid and fid in seen_fill_ids:
                    continue
                if fid:
                    seen_fill_ids.add(fid)
                all_events.append(ev)

    # Sort by ts
    all_events.sort(key=lambda x: x["ts"])
    print(f"\n  Total unique events: {len(all_events)}")

    # 2. Compute data quality notes
    data_quality_notes = []
    entry_count = sum(1 for e in all_events if e["event"] == "ENTRY")
    exit_count  = sum(1 for e in all_events if e["event"] == "EXIT_FILL")
    res_count   = sum(1 for e in all_events if e["event"] == "RESOLUTION")

    # Check bucket field contamination
    all_buckets = set(e["bucket"] for e in all_events)
    if all_buckets == {"fill_confirmed"}:
        data_quality_notes.append(
            "**BUCKET FIELD CONTAMINATED**: all 82 events have `bucket='fill_confirmed'` — "
            "the real market bucket (or_higher/or_lower/exact) is never stored. "
            "Patch required: store `parse_bucket()` result in `OrderRecord`."
        )

    # Check exit_tier/exit_channel coverage
    has_exit_tier = sum(1 for e in all_events if e.get("exit_tier"))
    data_quality_notes.append(
        f"`exit_tier` populated in {has_exit_tier}/{exit_count} EXIT_FILL events "
        f"({'OK' if has_exit_tier == exit_count else 'MISSING — patch required'})."
    )
    has_exit_channel = sum(1 for e in all_events if e.get("exit_channel"))
    data_quality_notes.append(
        f"`exit_channel` populated in {has_exit_channel}/{exit_count} EXIT_FILL events "
        f"({'OK' if has_exit_channel == exit_count else 'MISSING — patch required'})."
    )

    # Check for negative-edge entries
    neg_edge_entries = [e for e in all_events if e["event"] == "ENTRY" and e["edge_entry"] < 0]
    if neg_edge_entries:
        data_quality_notes.append(
            f"{len(neg_edge_entries)} ENTRY events have `edge_entry < 0` "
            f"(min={min(e['edge_entry'] for e in neg_edge_entries):.4f}). "
            "These may indicate stale forecast data at order time."
        )

    # Check orphan exits from EXIT_FILL tokens with no ENTRY
    entry_tokens = set(e["token_id"] for e in all_events if e["event"] == "ENTRY")
    exit_tokens  = set(e["token_id"] for e in all_events if e["event"] == "EXIT_FILL")
    no_entry_tokens = exit_tokens - entry_tokens
    if no_entry_tokens:
        data_quality_notes.append(
            f"{len(no_entry_tokens)} token_ids have EXIT_FILL records but NO ENTRY in ledger: "
            f"{', '.join(t[:16] for t in sorted(no_entry_tokens)[:5])}... "
            "These are pre-ledger trades (bot ran before trades.jsonl was initialised)."
        )

    # 3. FIFO matching
    print("\n[2] Running FIFO PnL matching...")
    matches, orphans, unclosed = fifo_match(all_events)
    fifo_total = sum(m["pnl_usd"] for m in matches)
    print(f"  FIFO matches:   {len(matches)}")
    print(f"  Orphan exits:   {len(orphans)}")
    print(f"  Unclosed entries: {len(unclosed)}")
    print(f"  FIFO Total PnL: ${fifo_total:.4f}")

    # 4. Resolution PnL
    print("\n[3] Collecting Resolution PnL...")
    resolution_rows = collect_resolution_pnl(all_events)
    resolution_total = sum(sf(r["pnl_usd"]) for r in resolution_rows)
    print(f"  Resolution events (dedup): {len(resolution_rows)}")
    print(f"  Resolution Total PnL:      ${resolution_total:.4f}")

    # 5. Write CSVs
    print("\n[4] Writing output CSVs...")
    write_csv(FULL_EVENTS_CSV,    SCHEMA, all_events)
    write_csv(FIFO_MATCHES_CSV,   list(matches[0].keys()) if matches else
              ["match_id","token_id","matched_contracts","entry_ts","entry_ts_human",
               "exit_ts","exit_ts_human","buy_price","sell_price","pnl_usd","hold_secs",
               "exit_reason","entry_strategy","exit_strategy","city","entry_edge",
               "entry_fill_id","exit_fill_id"], matches)
    write_csv(ORPHAN_EXITS_CSV,   SCHEMA, orphans)
    write_csv(RESOLUTION_PNL_CSV, SCHEMA, resolution_rows)

    # 6. Write report
    print("\n[5] Writing audit report...")
    report_content = write_report(
        AUDIT_REPORT_MD, discovery_table, all_events, matches, orphans, unclosed,
        resolution_rows, fifo_total, resolution_total, data_quality_notes
    )

    # 7. Print summary to stdout
    print("\n" + "=" * 70)
    print("RECONCILIATION SUMMARY")
    print("=" * 70)
    print(f"  Total events ingested  : {len(all_events)}")
    print(f"  ENTRY                  : {entry_count}")
    print(f"  EXIT_FILL              : {exit_count}")
    print(f"  RESOLUTION             : {res_count}")
    print(f"  FIFO matches           : {len(matches)}")
    print(f"  Orphan exits           : {len(orphans)}")
    print(f"  Unclosed entries       : {len(unclosed)}")
    print(f"  FIFO Realized PnL      : ${fifo_total:.4f}")
    print(f"  Resolution PnL         : ${resolution_total:.4f}")
    print(f"  Combined PnL estimate  : ${(fifo_total + resolution_total):.4f}")
    print("=" * 70)
    print("\nData Quality Issues Found:")
    for note in data_quality_notes:
        print(f"  * {note}")
    print()
    print("Audit complete. All artefacts written to:", OUT_DIR)

if __name__ == "__main__":
    main()
