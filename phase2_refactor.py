#!/usr/bin/env python3
"""TGT1 Phase 2: Replace global state variables with STATE.* equivalents."""
import re
import sys

FILE = "/home/ubuntu/polymarket-temp-bot/tempbot_v427_stable_20260313.py"

with open(FILE, "r") as f:
    lines = f.readlines()

out = []

# ── Section A: global lines to remove or modify ──
# We'll handle these by detecting the exact global patterns.

# Simple regex replacements for variables (sections C-K).
# These apply ONLY to indented lines (inside functions), NOT to comment-only lines,
# NOT to lines containing 'global ', and NOT to module-level definitions (col 0).
SIMPLE_VARS = [
    # C: kill_until
    "kill_until",
    # D: daily_pnl
    "daily_pnl",
    # E: peak_capital
    "peak_capital",
    # F: _daily_reset_capital
    "_daily_reset_capital",
    # G: daily_reset_ts
    "daily_reset_ts",
    # H: _last_trade_ids
    "_last_trade_ids",
    # I: _local_positions
    "_local_positions",
    # K: throttle timestamps
    "_last_creds_refresh",
    "_last_capital_fetch",
    "_last_capital_value",
    "_last_redeem_check",
    "_redeemed_condition_ids",
    "_last_position_exit_check",
    "_last_stoploss_check",
    "_last_exposure_sync",
    "_last_cancel_ts",
    # J: _sl_pending
    "_sl_pending",
]

# _SL_PENDING_TTL -> STATE.SL_PENDING_TTL (name changes!)
SL_PENDING_TTL_RE = re.compile(r'\b_SL_PENDING_TTL\b')

# Build regex for each simple var
SIMPLE_RES = [(v, re.compile(r'\b' + re.escape(v) + r'\b')) for v in SIMPLE_VARS]

# TOTAL_EXPOSURE regex (for f-strings/logs only — structural ones handled separately)
TOTAL_EXPOSURE_RE = re.compile(r'\bTOTAL_EXPOSURE\b')

for i, line in enumerate(lines):
    lineno = i + 1  # 1-indexed
    stripped = line.strip()

    # ── Skip pure comments ──
    if stripped.startswith('#'):
        out.append(line)
        continue

    # ── Skip module-level definitions (no indentation) ──
    if line and not line[0].isspace() and not stripped.startswith('global '):
        out.append(line)
        continue

    # ── Section A: Handle global lines ──
    if 'global ' in line and stripped.startswith('global '):
        # Line ~1628: global _last_creds_refresh → DELETE
        if stripped == 'global _last_creds_refresh':
            continue  # delete

        # Line ~1780: global TOTAL_EXPOSURE → DELETE
        if stripped == 'global TOTAL_EXPOSURE':
            continue  # delete

        # Line ~1816: global _last_cancel_ts, TOTAL_EXPOSURE → DELETE
        if stripped == 'global _last_cancel_ts, TOTAL_EXPOSURE':
            continue  # delete

        # Line ~1886: global daily_pnl, _last_trade_ids → DELETE
        if stripped == 'global daily_pnl, _last_trade_ids':
            continue  # delete

        # Line ~1996: global _last_capital_fetch, _last_capital_value → DELETE
        if stripped == 'global _last_capital_fetch, _last_capital_value':
            continue  # delete

        # Line ~2088: global _last_redeem_check → DELETE
        if stripped == 'global _last_redeem_check':
            continue  # delete

        # Line ~2239: global _last_position_exit_check → DELETE
        if stripped == 'global _last_position_exit_check':
            continue  # delete

        # Line ~2353: global _last_stoploss_check → DELETE
        if stripped == 'global _last_stoploss_check':
            continue  # delete

        # Line ~2504: global TOTAL_EXPOSURE, _last_exposure_sync → DELETE
        if stripped == 'global TOTAL_EXPOSURE, _last_exposure_sync':
            continue  # delete

        # Line ~2621: global daily_pnl, daily_reset_ts, kill_until, peak_capital, _daily_reset_capital → DELETE
        if stripped == 'global daily_pnl, daily_reset_ts, kill_until, peak_capital, _daily_reset_capital':
            continue  # delete

        # Line ~2677: global kill_until, TOTAL_EXPOSURE → DELETE
        if stripped == 'global kill_until, TOTAL_EXPOSURE':
            continue  # delete

        # Line ~3725: global peak_capital, DRY_RUN, _daily_reset_capital → REPLACE with global DRY_RUN
        if stripped == 'global peak_capital, DRY_RUN, _daily_reset_capital':
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + 'global DRY_RUN\n')
            continue

        # Line ~3774: global _last_redeem_check → DELETE (second occurrence, in main_async)
        # Already handled above by the generic match

        # Keep: global RESOLVED_FUNDER, global DRY_RUN, global GLS_CALIBRATION
        # These don't match any of the above patterns, so they pass through
        out.append(line)
        continue

    # ── From here: indented, non-comment, non-global lines ──
    # Only apply replacements to indented lines (function bodies)
    if not line[0:1].isspace():
        out.append(line)
        continue

    modified = line

    # ── Section B: TOTAL_EXPOSURE structural replacements ──
    # B1: TOTAL_EXPOSURE += contracts * price → STATE.add_exposure(contracts * price)
    if 'TOTAL_EXPOSURE += contracts * price' in modified:
        modified = modified.replace('TOTAL_EXPOSURE += contracts * price',
                                     'STATE.add_exposure(contracts * price)')
    # B2: if TOTAL_EXPOSURE == 0.0: return 0
    elif 'if TOTAL_EXPOSURE == 0.0: return 0' in modified:
        modified = modified.replace('if TOTAL_EXPOSURE == 0.0: return 0',
                                     'if STATE.total_exposure == 0.0: return 0')
    # B3: TOTAL_EXPOSURE = max(0.0, TOTAL_EXPOSURE - cancel_notional)
    elif 'TOTAL_EXPOSURE = max(0.0, TOTAL_EXPOSURE - cancel_notional)' in modified:
        modified = modified.replace('TOTAL_EXPOSURE = max(0.0, TOTAL_EXPOSURE - cancel_notional)',
                                     'STATE.remove_exposure(cancel_notional)')
    # B4: old_exp = TOTAL_EXPOSURE
    elif 'old_exp = TOTAL_EXPOSURE' in modified:
        modified = modified.replace('old_exp = TOTAL_EXPOSURE', 'old_exp = STATE.total_exposure')
    # B5: TOTAL_EXPOSURE = total_new
    elif 'TOTAL_EXPOSURE = total_new' in modified:
        modified = modified.replace('TOTAL_EXPOSURE = total_new',
                                     'STATE.set_total_exposure(total_new)')
    # B6: TOTAL_EXPOSURE in f-strings/logs (remaining references)
    elif TOTAL_EXPOSURE_RE.search(modified):
        # Don't touch MAX_TOTAL_EXPOSURE_FRAC
        # Only replace bare TOTAL_EXPOSURE, not MAX_TOTAL_EXPOSURE_FRAC
        modified = TOTAL_EXPOSURE_RE.sub('STATE.total_exposure', modified)

    # ── Section J special: _SL_PENDING_TTL → STATE.SL_PENDING_TTL ──
    if SL_PENDING_TTL_RE.search(modified):
        modified = SL_PENDING_TTL_RE.sub('STATE.SL_PENDING_TTL', modified)

    # ── Sections C-K: Simple variable replacements ──
    for var, var_re in SIMPLE_RES:
        if var_re.search(modified):
            modified = var_re.sub(f'STATE.{var}', modified)

    out.append(modified)

with open(FILE, "w") as f:
    f.writelines(out)

print(f"Phase 2 refactoring complete. Processed {len(lines)} lines, wrote {len(out)} lines.")
print(f"Removed {len(lines) - len(out)} global declaration lines.")
