#!/usr/bin/env python3
"""[v4.30-memfix-r1] Adds startup state recovery to _trade_index.
On restart, every unresolved ENTRY in trades.jsonl is reconstructed
into a TradeRecord and loaded into _trade_index so record_resolution
never loses cost-basis for in-flight positions.
No strategy logic, thresholds, sizing, or config changed.
"""
import sys

TARGET = "tempbot_v427_stable_20260313.py"

with open(TARGET, "r") as f:
    content = f.read()

ok = 0

# ═══════════════════════════════════════════════════════════════
# PATCH — extend _ledger_load_known_ids to rebuild _trade_index
# on restart from every unresolved ENTRY in trades.jsonl.
# We piggyback on the existing single-pass file read.
# ═══════════════════════════════════════════════════════════════
old_load = (
    '    def _ledger_load_known_ids(self):\n'
    '        """Re-populate _ledgered_ids from trades.jsonl on restart (dedup guard).\n'
    '        Reads at most the last 5000 lines to cap startup cost."""\n'
    '        try:\n'
    '            if not os.path.exists(self.LEDGER_FILE):\n'
    '                return\n'
    '            with open(self.LEDGER_FILE, "r", encoding="utf-8") as _lf:\n'
    '                _lines = _lf.readlines()\n'
    '            for _line in _lines[-5000:]:\n'
    '                _line = _line.strip()\n'
    '                if not _line:\n'
    '                    continue\n'
    '                try:\n'
    '                    _obj = json.loads(_line)\n'
    '                    _fid = _obj.get("fill_id")\n'
    '                    if _fid:\n'
    '                        self._ledgered_ids.add(str(_fid))\n'
    '                    # [DEDUP] load already-written RESOLUTION keys\n'
    '                    if _obj.get("event") == "RESOLUTION":\n'
    '                        _rk_tid = _obj.get("token_id", "")\n'
    '                        _rk_ts  = _obj.get("entry_ts", 0.0)\n'
    '                        if _rk_tid and _rk_ts:\n'
    '                            self._resolved_keys.add((_rk_tid, round(float(_rk_ts), 1)))\n'
    '                except Exception:\n'
    '                    pass\n'
    '            log.info(f"LEDGER: {len(self._ledgered_ids)} fill_ids, {len(self._resolved_keys)} resolved_keys loaded")\n'
    '        except Exception as _le:\n'
    '            log.warning(f"LEDGER LOAD ERROR: {_le}")'
)

new_load = (
    '    def _ledger_load_known_ids(self):\n'
    '        """Re-populate _ledgered_ids from trades.jsonl on restart (dedup guard).\n'
    '        [v4.30-memfix-r1] Also rebuilds _trade_index from every unresolved ENTRY\n'
    '        so record_resolution never loses cost-basis for in-flight positions.\n'
    '        Reads at most the last 5000 lines to cap startup cost."""\n'
    '        try:\n'
    '            if not os.path.exists(self.LEDGER_FILE):\n'
    '                return\n'
    '            with open(self.LEDGER_FILE, "r", encoding="utf-8") as _lf:\n'
    '                _lines = _lf.readlines()\n'
    '            # [v4.30-memfix-r1] Two structures built in this single pass:\n'
    '            #   _raw_entries  : token_id -> list of reconstructed TradeRecords (BUY ENTRY only)\n'
    '            #   _resolved_tks : set of (token_id, round(entry_ts,1)) already settled\n'
    '            _raw_entries: dict = {}   # token_id -> [TradeRecord, ...]\n'
    '            for _line in _lines[-5000:]:\n'
    '                _line = _line.strip()\n'
    '                if not _line:\n'
    '                    continue\n'
    '                try:\n'
    '                    _obj = json.loads(_line)\n'
    '                    _fid = _obj.get("fill_id")\n'
    '                    if _fid:\n'
    '                        self._ledgered_ids.add(str(_fid))\n'
    '                    _ev = _obj.get("event")\n'
    '                    # [DEDUP] load already-written RESOLUTION keys\n'
    '                    if _ev == "RESOLUTION":\n'
    '                        _rk_tid = _obj.get("token_id", "")\n'
    '                        _rk_ts  = _obj.get("entry_ts", 0.0)\n'
    '                        if _rk_tid and _rk_ts:\n'
    '                            self._resolved_keys.add((_rk_tid, round(float(_rk_ts), 1)))\n'
    '                    # [v4.30-memfix-r1] Reconstruct BUY ENTRY as TradeRecord\n'
    '                    elif _ev == "ENTRY" and _obj.get("side", "BUY") == "BUY":\n'
    '                        _tid = _obj.get("token_id", "")\n'
    '                        _px  = float(_obj.get("price", 0) or 0)\n'
    '                        _sz  = float(_obj.get("notional_usd", 0) or 0)\n'
    '                        _ts  = float(_obj.get("ts", 0) or 0)\n'
    '                        if _tid and _px > 1e-9 and _sz > 0 and _ts > 0:\n'
    '                            _tr = TradeRecord(\n'
    '                                ts=_ts,\n'
    '                                token_id=_tid,\n'
    '                                strategy=_obj.get("strategy", "MM"),\n'
    '                                city=_obj.get("city", ""),\n'
    '                                bucket=_obj.get("bucket", ""),\n'
    '                                side="BUY",\n'
    '                                price=_px,\n'
    '                                size=_sz,\n'
    '                                T_days=float(_obj.get("Tdays", 1.0) or 1.0),\n'
    '                                edge_entry=float(_obj.get("edge_entry", 0) or 0),\n'
    '                            )\n'
    '                            _raw_entries.setdefault(_tid, []).append(_tr)\n'
    '                except Exception:\n'
    '                    pass\n'
    '            # [v4.30-memfix-r1] Populate _trade_index with unresolved entries only.\n'
    '            # An entry is resolved if its (token_id, round(ts,1)) key is in _resolved_keys.\n'
    '            _recovered = 0\n'
    '            for _tid, _recs in _raw_entries.items():\n'
    '                for _tr in _recs:\n'
    '                    _rk = (_tid, round(_tr.ts, 1))\n'
    '                    if _rk in self._resolved_keys:\n'
    '                        _tr.resolved = True   # mark settled; skip in record_resolution\n'
    '                    else:\n'
    '                        _recovered += 1\n'
    '                    # Always index — record_resolution dedup guard handles settled ones\n'
    '                    self._trade_index.setdefault(_tid, []).append(_tr)\n'
    '            log.info(\n'
    '                f"LEDGER: {len(self._ledgered_ids)} fill_ids, "\n'
    '                f"{len(self._resolved_keys)} resolved_keys, "\n'
    '                f"{_recovered} unresolved entries recovered into _trade_index"\n'
    '            )\n'
    '        except Exception as _le:\n'
    '            log.warning(f"LEDGER LOAD ERROR: {_le}")'
)

if old_load in content:
    content = content.replace(old_load, new_load, 1)
    ok += 1
    print("[+] State recovery injected into _ledger_load_known_ids")
else:
    print("[!] Anchor not found", file=sys.stderr)
    sys.exit(1)

with open(TARGET, "w") as f:
    f.write(content)

print(f"\n=== {ok}/1 patches applied successfully ===")
