#!/usr/bin/env python3
"""[v4.30-memfix] TradeRecord memory eviction fix.
Adds self._trade_index so record_resolution never misses entries
that were pushed out of the 500-record self.trades rolling window.
No strategy logic, thresholds, or config changed.
"""
import sys

TARGET = "tempbot_v427_stable_20260313.py"

with open(TARGET, "r") as f:
    content = f.read()

ok = 0

# ═══════════════════════════════════════════════════════════════
# PATCH 1 — add _trade_index to __init__ right after _resolved_keys
# ═══════════════════════════════════════════════════════════════
old_init = (
    '        self._ledgered_ids: set = set()    # [LEDGER] fill_ids already written\n'
    '        self._resolved_keys: set = set()  # [DEDUP] (token_id, round(entry_ts,1)) already written'
)
new_init = (
    '        self._ledgered_ids: set = set()    # [LEDGER] fill_ids already written\n'
    '        self._resolved_keys: set = set()  # [DEDUP] (token_id, round(entry_ts,1)) already written\n'
    '        # [v4.30-memfix] Persistent index of BUY TradeRecords, keyed by token_id.\n'
    '        # Unlike self.trades (capped at 500), this is never evicted, so\n'
    '        # record_resolution always finds the original cost-basis even after\n'
    '        # >500 events have been processed since entry.\n'
    '        self._trade_index: Dict[str, List["TradeRecord"]] = {}'
)
if old_init in content:
    content = content.replace(old_init, new_init, 1)
    ok += 1
    print("[+] Patch 1: _trade_index added to __init__")
else:
    print("[!] Patch 1: anchor not found", file=sys.stderr)
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# PATCH 2 — populate _trade_index in record_trade (BUY entries only)
# ═══════════════════════════════════════════════════════════════
old_rt = (
    '        self.trades.append(r)\n'
    '        # Keep rolling window di 500 trade\n'
    '        if len(self.trades) > 500:\n'
    '            self.trades = self.trades[-500:]'
)
new_rt = (
    '        self.trades.append(r)\n'
    '        # Keep rolling window di 500 trade\n'
    '        if len(self.trades) > 500:\n'
    '            self.trades = self.trades[-500:]\n'
    '        # [v4.30-memfix] Persist BUY entries in unbounded index for record_resolution\n'
    '        if side == "BUY":\n'
    '            self._trade_index.setdefault(token_id, []).append(r)'
)
if old_rt in content:
    content = content.replace(old_rt, new_rt, 1)
    ok += 1
    print("[+] Patch 2: _trade_index populated in record_trade")
else:
    print("[!] Patch 2: anchor not found", file=sys.stderr)
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# PATCH 3 — use _trade_index in record_resolution as fallback
# ═══════════════════════════════════════════════════════════════
old_rr = (
    '    def record_resolution(self, token_id: str, resolved_yes: bool):\n'
    '        """Records settlement PnL for open BUY positions on this token.\n'
    '        [FIX-1] Only processes BUY entries (SELL exits already captured by EXIT_FILL).\n'
    '        [FIX-1] Dedup guard fires BEFORE r.resolved is set to prevent phantom re-entries.\n'
    '        [FIX-3] PnL math: win=(contracts-notional), lose=(-notional).\n'
    '        """\n'
    '        for r in self.trades:\n'
    '            # [FIX-1] BUY-only: SELL exits are already captured in EXIT_FILL ledger\n'
    '            if r.token_id != token_id or r.side != "BUY" or r.resolved:\n'
    '                continue'
)
new_rr = (
    '    def record_resolution(self, token_id: str, resolved_yes: bool):\n'
    '        """Records settlement PnL for open BUY positions on this token.\n'
    '        [FIX-1] Only processes BUY entries (SELL exits already captured by EXIT_FILL).\n'
    '        [FIX-1] Dedup guard fires BEFORE r.resolved is set to prevent phantom re-entries.\n'
    '        [FIX-3] PnL math: win=(contracts-notional), lose=(-notional).\n'
    '        [v4.30-memfix] Lookup order: _trade_index first (unbounded), self.trades as\n'
    '        secondary. Union ensures entries evicted from the 500-record rolling window\n'
    '        are still resolved correctly.\n'
    '        """\n'
    '        # [v4.30-memfix] Build candidate list: persistent index union rolling window.\n'
    '        # dict preserves insertion order; id() key deduplicates object references.\n'
    '        _seen_ids: set = set()\n'
    '        _candidates: list = []\n'
    '        for _r in self._trade_index.get(token_id, []) + self.trades:\n'
    '            if id(_r) not in _seen_ids:\n'
    '                _seen_ids.add(id(_r))\n'
    '                _candidates.append(_r)\n'
    '        for r in _candidates:\n'
    '            # [FIX-1] BUY-only: SELL exits are already captured in EXIT_FILL ledger\n'
    '            if r.token_id != token_id or r.side != "BUY" or r.resolved:\n'
    '                continue'
)
if old_rr in content:
    content = content.replace(old_rr, new_rr, 1)
    ok += 1
    print("[+] Patch 3: record_resolution uses _trade_index fallback")
else:
    print("[!] Patch 3: anchor not found", file=sys.stderr)
    sys.exit(1)

with open(TARGET, "w") as f:
    f.write(content)

print(f"\n=== {ok}/3 patches applied successfully ===")
