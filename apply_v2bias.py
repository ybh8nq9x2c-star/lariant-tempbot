#!/usr/bin/env python3
"""[v4.29-bias] Phase 2 Bias patch — entry-side only.
Rules: A (notional floor), B (directional floor), C (quarantine), E (version tag).
Does NOT touch exit logic, stoploss, takeprofit, sigma, caps, or thresholds.
"""
import sys

TARGET = "tempbot_v427_stable_20260313.py"

with open(TARGET, "r") as f:
    content = f.read()

original = content  # keep for diff
ok = 0

# ═══════════════════════════════════════════════════════════════
# RULE A — constant definition
# ═══════════════════════════════════════════════════════════════
anchor = 'FAIR_PROB_FLOOR_MM   = 0.02  # [v4.28-glsfloor] GLS cannot enter when model near-impossible'
replacement = (
    'FAIR_PROB_FLOOR_MM   = 0.02  # [v4.28-glsfloor] GLS cannot enter when model near-impossible\n'
    'MIN_NOTIONAL_USD     = 1.0   # [v4.29-bias] Rule A: skip micro-dust entries below 1 USD'
)
if anchor in content:
    content = content.replace(anchor, replacement, 1)
    ok += 1
    print("[+] Rule A constant added")
else:
    print("[!] Rule A constant: anchor not found", file=sys.stderr); sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# RULE A — size guard after kelly (size == notional USD)
# ═══════════════════════════════════════════════════════════════
anchor = '                if size <= 0: return\n\n                log.debug(f"MM'
insert_after = '                if size <= 0: return\n'
rule_a_block = (
    '                # [v4.29-bias] Rule A: skip micro-dust entries\n'
    '                if size < MIN_NOTIONAL_USD:\n'
    '                    log.debug("MM NOTIONAL_FLOOR SKIP %s: sz=%.2f < %.1f", tid[:12], size, MIN_NOTIONAL_USD)\n'
    '                    return\n'
)
if anchor in content:
    content = content.replace(insert_after, insert_after + rule_a_block, 1)
    ok += 1
    print("[+] Rule A size guard injected")
else:
    print("[!] Rule A size guard: anchor not found", file=sys.stderr); sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# RULE B — hard directional floor in BUY zone
# ═══════════════════════════════════════════════════════════════
anchor = (
    '                        if true_p < FAIR_PROB_FLOOR_MM:  # [v4.28-glsfloor]\n'
    '                            log.debug(f"MM FAIR_FLOOR SKIP {tid[:12]} truep={true_p:.4f}")\n'
    '                            return\n'
    '                        edge_fc = max(max(0.0, directional_edge), max(0.0, _gls_edge))'
)
replacement = (
    '                        if true_p < FAIR_PROB_FLOOR_MM:  # [v4.28-glsfloor]\n'
    '                            log.debug(f"MM FAIR_FLOOR SKIP {tid[:12]} truep={true_p:.4f}")\n'
    '                            return\n'
    '                        # [v4.29-bias] Rule B: hard directional floor\n'
    '                        if true_p < (mid * 0.85):\n'
    '                            log.debug("MM DIR_FLOOR SKIP %s truep=%.4f < mid*0.85=%.4f", tid[:12], true_p, mid * 0.85)\n'
    '                            return\n'
    '                        edge_fc = max(max(0.0, directional_edge), max(0.0, _gls_edge))'
)
if anchor in content:
    content = content.replace(anchor, replacement, 1)
    ok += 1
    print("[+] Rule B injected (BUY zone)")
else:
    print("[!] Rule B: anchor not found", file=sys.stderr); sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# RULE C — quarantine seoul / fill_confirmed in _mm_task
# ═══════════════════════════════════════════════════════════════
anchor = (
    '                bucket = parse_bucket(q_text)\n'
    '                if not bucket.get("valid", False):\n'
    '                    return'
)
replacement = (
    '                bucket = parse_bucket(q_text)\n'
    '                if not bucket.get("valid", False):\n'
    '                    return\n'
    '                # [v4.29-bias] Rule C: quarantine seoul/fill_confirmed\n'
    '                if city == "seoul" and bucket.get("type") == "fill_confirmed":\n'
    '                    log.debug("QUARANTINE SKIP %s: seoul/fill_confirmed", tid[:12])\n'
    '                    return'
)
if anchor in content:
    content = content.replace(anchor, replacement, 1)
    ok += 1
    print("[+] Rule C injected (_mm_task)")
else:
    print("[!] Rule C: anchor not found", file=sys.stderr); sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# RULE C — also guard the fill-handler path
# ═══════════════════════════════════════════════════════════════
anchor = (
    '                effective_side = "SELL" if strategy_fill in _EXIT_STRATS else side\n'
    '                if effective_side != side:\n'
    '                    log.debug(f"[FILL_SIDE] raw={side}->SELL strat={strategy_fill} tok={token_id_fill[:12]}")'
)
rule_c_fill = (
    '\n                # [v4.29-bias] Rule C: quarantine seoul/fill_confirmed at fill level\n'
    '                if city_fill == "seoul" and bucket_fill == "fill_confirmed" and effective_side == "BUY":\n'
    '                    log.debug("QUARANTINE FILL SKIP %s: seoul/fill_confirmed", token_id_fill[:12])\n'
    '                    continue'
)
if anchor in content:
    content = content.replace(anchor, anchor + rule_c_fill, 1)
    ok += 1
    print("[+] Rule C injected (fill handler)")
else:
    print("[~] Rule C fill handler: anchor not found (non-critical)")

# ═══════════════════════════════════════════════════════════════
# RULE E — version tag in ledger write
# ═══════════════════════════════════════════════════════════════
anchor = '            "book_snapshot":   (book_snapshot or None) if side == "BUY" else None,  # [QLIB] ENTRY only; None if no WS data\n        })'
replacement = (
    '            "book_snapshot":   (book_snapshot or None) if side == "BUY" else None,  # [QLIB] ENTRY only; None if no WS data\n'
    '            "version":         "v2-bias-patch-stable",  # [v4.29-bias] Rule E: data segmentation\n'
    '        })'
)
if anchor in content:
    content = content.replace(anchor, replacement, 1)
    ok += 1
    print("[+] Rule E version tag injected")
else:
    print("[!] Rule E: anchor not found", file=sys.stderr); sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# Write back
# ═══════════════════════════════════════════════════════════════
with open(TARGET, "w") as f:
    f.write(content)

print(f"\n=== {ok} patches applied successfully ===")
