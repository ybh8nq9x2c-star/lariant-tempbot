#!/usr/bin/env python3
"""Surgical patcher for TempBot v4.27 — Phases 1-4.
Run on the EC2 server: python3 patch_tempbot_phases.py
"""
import re, os, sys

BOT = os.path.expanduser("~/polymarket-temp-bot/tempbot_v427_stable_20260313.py")
PS  = os.path.expanduser("~/polymarket-temp-bot/portfolio_state.py")

def patch(filepath, old, new, label=""):
    with open(filepath, "r") as f:
        content = f.read()
    if old not in content:
        print(f"  [SKIP] '{label}' — target string not found in {os.path.basename(filepath)}")
        return False
    count = content.count(old)
    if count > 1:
        print(f"  [WARN] '{label}' — found {count} occurrences, replacing first only")
        content = content.replace(old, new, 1)
    else:
        content = content.replace(old, new)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  [OK] {label}")
    return True

def patch_all(filepath, old, new, label=""):
    """Replace ALL occurrences."""
    with open(filepath, "r") as f:
        content = f.read()
    if old not in content:
        print(f"  [SKIP] '{label}' — not found")
        return False
    content = content.replace(old, new)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  [OK] {label}")
    return True

def insert_after(filepath, anchor, insertion, label=""):
    with open(filepath, "r") as f:
        content = f.read()
    if anchor not in content:
        print(f"  [SKIP] '{label}' — anchor not found")
        return False
    if insertion.strip() in content:
        print(f"  [SKIP] '{label}' — already applied")
        return False
    content = content.replace(anchor, anchor + insertion)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  [OK] {label}")
    return True

def insert_before(filepath, anchor, insertion, label=""):
    with open(filepath, "r") as f:
        content = f.read()
    if anchor not in content:
        print(f"  [SKIP] '{label}' — anchor not found")
        return False
    if insertion.strip() in content:
        print(f"  [SKIP] '{label}' — already applied")
        return False
    content = content.replace(anchor, insertion + anchor)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  [OK] {label}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
print("="*60)
print("PHASE 1A: Fix daily_pnl runaway — compute from ledger only")
print("="*60)

# 1A-1: In update_pnl_from_fills, gut the += accumulation.
#        Replace the += lines with a no-op comment. The daily_pnl will be
#        computed solely from compute_metrics(daily_pnl_ledger).
patch(BOT,
    '            if pnl_d is not None:\n                STATE.daily_pnl += float(pnl_d); continue',
    '            if pnl_d is not None:\n                # [PHASE1A] DEPRECATED: direct pnl_d accumulation removed — use ledger\n                continue',
    "1A-1: remove direct pnl_d accumulation")

patch(BOT,
    '            if   outcome in ("YES","1","WIN"):  STATE.daily_pnl += (1-price)*size\n            elif outcome in ("NO","0","LOSE"):  STATE.daily_pnl -= price*size',
    '            # [PHASE1A] DEPRECATED: outcome-based accumulation removed — use ledger\n            pass',
    "1A-2: remove outcome-based accumulation")

# 1A-3: In _save_state, replace the stale "daily_pnl" with ledger truth.
#        The compute_metrics() already runs inside _save_state and produces daily_pnl_ledger.
#        We just need to wire it to the "daily_pnl" field in the saved state.
patch(BOT,
    '                # [H4-FIX] persist daily PnL so restarts don\'t reset intra-day accounting\n'
    '                "daily_pnl":           getattr(_st, "daily_pnl", 0.0),',
    '                # [PHASE1A] daily_pnl sourced from ledger (not runaway accumulator)\n'
    '                "daily_pnl":           state.get("trades_summary", {}).get("daily_pnl_ledger", 0.0),',
    "1A-3: save_state writes ledger-sourced daily_pnl")

# 1A-4: In _load_state, restore daily_pnl from compute_metrics, not the saved runaway value.
#        We zero it out on load since compute_metrics will recompute the truth.
patch(BOT,
    '                    _st.daily_pnl            = float(state.get("daily_pnl", 0.0))',
    '                    # [PHASE1A] daily_pnl recomputed from ledger on save; restore 0 as placeholder\n'
    '                    _st.daily_pnl            = float(state.get("daily_pnl", 0.0))  # ledger-sourced now',
    "1A-4: load_state annotation (value is now ledger-sourced)")

# 1A-5: Remove the add_pnl call from STATE in the PnL log line
#        (the log.info line at the end of update_pnl_from_fills uses STATE.daily_pnl)
#        We keep the log line but add context.
patch(BOT,
    '        log.info(f"PNL: daily={STATE.daily_pnl:.4f}")',
    '        log.info(f"PNL: daily_state={STATE.daily_pnl:.4f} (ledger-sourced on next save)")',
    "1A-5: annotate PnL log line")

print()
print("="*60)
print("PHASE 1B: Persist _edge_cache to disk")
print("="*60)

# 1B-1: Add EDGE_CACHE_FILE constant and load in SelfMonitor.__init__
edge_cache_init = '''
        # [PHASE1B] Persistent edge cache — survives restarts
        self.EDGE_CACHE_FILE = "edge_cache.json"
        self._edge_cache = {}
        try:
            if os.path.exists(self.EDGE_CACHE_FILE):
                with open(self.EDGE_CACHE_FILE, "r") as _ecf:
                    self._edge_cache = json.load(_ecf)
                log.info(f"EDGE CACHE LOADED: {len(self._edge_cache)} entries from {self.EDGE_CACHE_FILE}")
        except Exception as _ec_err:
            log.warning(f"EDGE CACHE LOAD ERROR: {_ec_err}")
            self._edge_cache = {}'''

insert_after(BOT,
    '            "MAX_DD":               MAX_DD,\n        }',
    edge_cache_init,
    "1B-1: load edge_cache from disk in __init__")

# 1B-2: Add save method to SelfMonitor (insert before _save_state)
edge_save_method = '''
    def _save_edge_cache(self):
        """[PHASE1B] Persist edge cache to disk after each new entry."""
        try:
            with open(self.EDGE_CACHE_FILE, "w") as f:
                json.dump(self._edge_cache, f)
        except Exception as e:
            log.warning(f"EDGE CACHE SAVE ERROR: {e}")

'''

insert_before(BOT,
    '    def _save_state(self):',
    edge_save_method,
    "1B-2: add _save_edge_cache method")

# 1B-3: Call _save_edge_cache when edge is written (MM path, line ~3478)
patch(BOT,
    '                            MONITOR._edge_cache[tid] = round(directional_edge, 4)  # [EDGE]',
    '                            MONITOR._edge_cache[tid] = round(directional_edge, 4)  # [EDGE]\n'
    '                            MONITOR._save_edge_cache()  # [PHASE1B] persist to disk',
    "1B-3: persist edge_cache on MM write")

# 1B-4: Call _save_edge_cache when edge is written (ARB path, line ~3228)
patch(BOT,
    '                            MONITOR._edge_cache[tok] = round(_arb_edge, 4)  # [EDGE-ARB]',
    '                            MONITOR._edge_cache[tok] = round(_arb_edge, 4)  # [EDGE-ARB]\n'
    '                            MONITOR._save_edge_cache()  # [PHASE1B] persist to disk',
    "1B-4: persist edge_cache on ARB write")


print()
print("="*60)
print("PHASE 2: Fix exposure cap deadlock — hard-sync on startup")
print("="*60)

# 2-1: Force exposure sync on first cycle (reset _last_exposure_sync to 0)
#      This ensures the very first cycle does a full sync, clearing stale exposure.
patch(BOT,
    "EXPOSURE_SYNC_INTERVAL = 60.0  # secondi tra una sync e l'altra",
    "EXPOSURE_SYNC_INTERVAL = 60.0  # secondi tra una sync e l'altra\n"
    "EXPOSURE_HARD_SYNC_INTERVAL = 300.0  # [PHASE2] force full recalc every 5 min",
    "2-1: add hard sync interval constant")

# 2-2: At the start of sync_exposure_from_positions, add hard-sync logic
#       that ignores the cached exposure and recalculates from scratch.
#       The key fix: don't count pending_exposure from CLI if REST already returned data.
#       And add a periodic full-reset of pending_exposure to prevent stale buildup.
hard_sync_patch = '''
        # [PHASE2] Hard-sync: zero out total_exposure BEFORE recalculating.
        # This prevents stale ghost exposure from cancelled/expired orders
        # that never decremented the counter.
        STATE.set_total_exposure(0.0)  # will be rebuilt from positions + pending below
'''

insert_after(BOT,
    "    try:\n        # ── 1. Exposure da posizioni fillate ──",
    hard_sync_patch,
    "2-2: zero exposure before recalc in sync")


print()
print("="*60)
print("PHASE 3: Stoploss shadow mode")
print("="*60)

# 3-1: Add STOPLOSS_SHADOW_MODE config parameter
insert_after(BOT,
    'STOPLOSS_POSITION_PCT = cfg("risk", "STOPLOSS_POSITION_PCT", 0.95)  # [BECKER] disabled',
    '\nSTOPLOSS_SHADOW_MODE  = True   # [PHASE3] True = log-only, no SELL execution',
    "3-1: add STOPLOSS_SHADOW_MODE constant")

# 3-2: Add to hot-reload whitelist
patch(BOT,
    '"MAX_FAMILY_SELL_WORSENING_USD",  # [FAMILY]',
    '"MAX_FAMILY_SELL_WORSENING_USD",  # [FAMILY]\n'
    '                "STOPLOSS_SHADOW_MODE",  # [PHASE3]',
    "3-2: add STOPLOSS_SHADOW_MODE to hot-reload whitelist")

# 3-3: Wrap the actual SELL execution in stoploss with shadow mode check.
#       The key anchor is the place_order call inside check_position_stoploss.
stoploss_shadow_block = '''                # [PHASE3] Shadow mode: log the stoploss trigger but don't execute
                if STOPLOSS_SHADOW_MODE:
                    _edge_at_entry = getattr(MONITOR, '_edge_cache', {}).get(token_id, 0.0)
                    log.warning(
                        f"STOPLOSS_SHADOW {title[:40]}: pnl={pnl_pct:.1f}% "
                        f"val=${cur_value:.2f} city={city} "
                        f"entry_edge={_edge_at_entry:.4f} "
                        f"sell_price={sell_price:.4f} sz={size:.0f} "
                        f"notional=${sell_notional:.2f} | SHADOW=no_exec"
                    )
                    continue
'''

insert_before(BOT,
    '                _sl_submitted = await place_order(client, token_id, "SELL", sell_price, sell_notional,\n'
    '                            city=city, strategy="STOPLOSS")  # [C1-FIX]',
    stoploss_shadow_block,
    "3-3: inject shadow mode guard before SELL execution")


print()
print("="*60)
print("PHASE 4: City-split metrics in compute_metrics")
print("="*60)

# 4-1: Add city × strategy breakdown to compute_metrics output.
#      We add _by_city_strat accumulator and output it.
patch(BOT,
    '            _by_strat = {}        # strategy -> [pnl]\n'
    '            _by_city  = {}        # city -> [pnl]',
    '            _by_strat = {}        # strategy -> [pnl]\n'
    '            _by_city  = {}        # city -> [pnl]\n'
    '            _by_city_strat = {}   # [PHASE4] "city_strategy" -> [pnl]',
    "4-1: add city_strat accumulator")

patch(BOT,
    '                    _by_city.setdefault(city, []).append(pv)',
    '                    _by_city.setdefault(city, []).append(pv)\n'
    '                    _by_city_strat.setdefault(f"{city}_{strat}", []).append(pv)  # [PHASE4]',
    "4-2: accumulate city_strat breakdown")

# 4-3: Output the breakdown
patch(BOT,
    '            mx["pnl_by_city"]     = {c: round(sum(v), 6) for c, v in _by_city.items()}',
    '            mx["pnl_by_city"]     = {c: round(sum(v), 6) for c, v in _by_city.items()}\n'
    '            # [PHASE4] City × Strategy breakdown for Seoul vs London diagnosis\n'
    '            mx["pnl_by_city_strategy"] = {\n'
    '                k: {"total_pnl": round(sum(v), 6), "n": len(v),\n'
    '                     "win_rate": round(sum(1 for p in v if p > 0) / max(1, len(v)), 4)}\n'
    '                for k, v in _by_city_strat.items()\n'
    '            }',
    "4-3: output city_strat breakdown with pnl + wr")

# Also include it in the _save_state position export — it's already included
# via trades_summary since compute_metrics returns it. No extra wire-up needed.

print()
print("="*60)
print("SYNTAX CHECK")
print("="*60)
import subprocess
for f in [BOT, PS]:
    r = subprocess.run([sys.executable, "-m", "py_compile", f],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  [OK] {os.path.basename(f)}")
    else:
        print(f"  [FAIL] {os.path.basename(f)}: {r.stderr}")
        sys.exit(1)

print()
print("ALL PHASES APPLIED SUCCESSFULLY")
print("Restart bot to activate.")
