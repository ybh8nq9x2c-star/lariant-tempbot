#!/usr/bin/env python3
"""[v4.30-memfix-r1] Re-apply _trade_index + startup recovery to the mathfix'd file."""
import re, sys

TARGET = "tempbot_v427_stable_20260313.py"
with open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── PATCH 1: Add _trade_index to __init__ ──
OLD_INIT = (
    '        self._ledgered_ids: set = set()    # [LEDGER] fill_ids already written\n'
    '        self._resolved_keys: set = set()  # [DEDUP] (token_id, round(entry_ts,1)) already written\n'
    '        self._ledger_load_known_ids()     # [LEDGER] restart-safe dedup'
)
NEW_INIT = (
    '        self._ledgered_ids: set = set()    # [LEDGER] fill_ids already written\n'
    '        self._resolved_keys: set = set()  # [DEDUP] (token_id, round(entry_ts,1)) already written\n'
    '        self._trade_index: dict = {}       # [v4.30-memfix] token_id -> [TradeRecord] persistent across eviction\n'
    '        self._ledger_load_known_ids()     # [LEDGER] restart-safe dedup + state recovery'
)
if OLD_INIT in src:
    src = src.replace(OLD_INIT, NEW_INIT, 1)
    changes += 1
    print("PATCH 1 OK: _trade_index added to __init__")
else:
    print("PATCH 1 SKIP: pattern not found (already applied?)")

# ── PATCH 2: Expand _ledger_load_known_ids with ENTRY recovery ──
OLD_LOAD = '''    def _ledger_load_known_ids(self):
        """Re-populate _ledgered_ids from trades.jsonl on restart (dedup guard).
        Reads at most the last 5000 lines to cap startup cost."""
        try:
            if not os.path.exists(self.LEDGER_FILE):
                return
            with open(self.LEDGER_FILE, "r", encoding="utf-8") as _lf:
                _lines = _lf.readlines()
            for _line in _lines[-5000:]:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _obj = json.loads(_line)
                    _fid = _obj.get("fill_id")
                    if _fid:
                        self._ledgered_ids.add(str(_fid))
                    # [DEDUP] load already-written RESOLUTION keys
                    if _obj.get("event") == "RESOLUTION":
                        _rk_tid = _obj.get("token_id", "")
                        _rk_ts  = _obj.get("entry_ts", 0.0)
                        if _rk_tid and _rk_ts:
                            self._resolved_keys.add((_rk_tid, round(float(_rk_ts), 1)))
                except Exception:
                    pass
            log.info(f"LEDGER: {len(self._ledgered_ids)} fill_ids, {len(self._resolved_keys)} resolved_keys loaded")
        except Exception as _le:
            log.warning(f"LEDGER LOAD ERROR: {_le}")'''

NEW_LOAD = '''    def _ledger_load_known_ids(self):
        """[v4.30-memfix-r1] Re-populate dedup sets AND rebuild _trade_index
        from trades.jsonl on restart, so in-flight positions survive restarts
        and the 500-record rolling window eviction."""
        try:
            if not os.path.exists(self.LEDGER_FILE):
                return
            with open(self.LEDGER_FILE, "r", encoding="utf-8") as _lf:
                _lines = _lf.readlines()
            _raw_entries: dict = {}   # token_id -> [TradeRecord]
            for _line in _lines[-5000:]:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _obj = json.loads(_line)
                    _fid = _obj.get("fill_id")
                    if _fid:
                        self._ledgered_ids.add(str(_fid))
                    _ev = _obj.get("event")
                    # [DEDUP] load already-written RESOLUTION keys
                    if _ev == "RESOLUTION":
                        _rk_tid = _obj.get("token_id", "")
                        _rk_ts  = _obj.get("entry_ts", 0.0)
                        if _rk_tid and _rk_ts:
                            self._resolved_keys.add((_rk_tid, round(float(_rk_ts), 1)))
                    # [v4.30-memfix-r1] Reconstruct TradeRecord from ENTRY lines
                    elif _ev == "ENTRY" and _obj.get("side", "BUY") == "BUY":
                        _tid = _obj.get("token_id", "")
                        _px  = float(_obj.get("price", 0) or 0)
                        _sz  = float(_obj.get("notional_usd", 0) or 0)
                        _ts  = float(_obj.get("ts", 0) or 0)
                        if _tid and _px > 1e-9 and _sz > 0 and _ts > 0:
                            _tr = TradeRecord(
                                ts=_ts, token_id=_tid,
                                strategy=_obj.get("strategy", "MM"),
                                city=_obj.get("city", ""),
                                bucket=_obj.get("bucket", ""),
                                side="BUY", price=_px, size=_sz,
                                T_days=float(_obj.get("Tdays", 1.0) or 1.0),
                                edge_entry=float(_obj.get("edge_entry", 0) or 0),
                            )
                            _raw_entries.setdefault(_tid, []).append(_tr)
                except Exception:
                    pass
            # Cross-reference with resolved_keys to mark settled entries
            _recovered = 0
            for _tid, _recs in _raw_entries.items():
                for _tr in _recs:
                    _rk = (_tid, round(_tr.ts, 1))
                    if _rk in self._resolved_keys:
                        _tr.resolved = True
                    else:
                        _recovered += 1
                    self._trade_index.setdefault(_tid, []).append(_tr)
            log.info(
                f"LEDGER: {len(self._ledgered_ids)} fill_ids, "
                f"{len(self._resolved_keys)} resolved_keys, "
                f"{_recovered} unresolved entries recovered into _trade_index"
            )
        except Exception as _le:
            log.warning(f"LEDGER LOAD ERROR: {_le}")'''

if OLD_LOAD in src:
    src = src.replace(OLD_LOAD, NEW_LOAD, 1)
    changes += 1
    print("PATCH 2 OK: _ledger_load_known_ids expanded with ENTRY recovery")
else:
    print("PATCH 2 SKIP: pattern not found")

# ── PATCH 3: Append to _trade_index in record_trade (BUY only) ──
OLD_RT = '''        if _fid:
            self._ledgered_ids.add(_fid)

    def record_resolution'''
NEW_RT = '''        if _fid:
            self._ledgered_ids.add(_fid)
        # [v4.30-memfix] Persist BUY entries in _trade_index (immune to 500-cap eviction)
        if side == "BUY":
            self._trade_index.setdefault(token_id, []).append(r)

    def record_resolution'''

if OLD_RT in src:
    src = src.replace(OLD_RT, NEW_RT, 1)
    changes += 1
    print("PATCH 3 OK: record_trade appends BUY to _trade_index")
else:
    print("PATCH 3 SKIP: pattern not found")

# ── PATCH 4: record_resolution checks _trade_index as well ──
OLD_RES = '''        for r in self.trades:
            if r.token_id == token_id and not r.resolved:'''
NEW_RES = '''        # [v4.30-memfix] Check both rolling window AND persistent index
        _all_records = list(self.trades)
        for _idx_r in self._trade_index.get(token_id, []):
            if _idx_r not in _all_records:
                _all_records.append(_idx_r)
        for r in _all_records:
            if r.token_id == token_id and not r.resolved:'''

if OLD_RES in src:
    src = src.replace(OLD_RES, NEW_RES, 1)
    changes += 1
    print("PATCH 4 OK: record_resolution checks _trade_index")
else:
    print("PATCH 4 SKIP: pattern not found")

if changes == 0:
    print("ERROR: No patches applied. Aborting.")
    sys.exit(1)

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\nDONE: {changes}/4 patches applied to {TARGET}")
