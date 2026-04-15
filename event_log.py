"""
event_log.py — Append-only Event Log per TempBot
═══════════════════════════════════════════════════
Risolve: audit risk #4 (order lifecycle), #9 (logica risk interna),
         "Terzo" (event log append-only per ricostruzione stato)
         
Ogni evento è una riga JSON nel file events.jsonl.
Lo stato può essere ricostruito facendo replay degli eventi.
"""

import json
import time
import logging
import os
from typing import Optional

log = logging.getLogger("tempbot.events")

EVENT_FILE = os.path.expanduser("~/polymarket-temp-bot/events.jsonl")


class EventLog:
    """Append-only event journal.
    
    Tipi di eventi:
    - order_submitted: ordine inviato
    - order_confirmed: ordine confermato da API
    - order_rejected:  ordine rifiutato
    - order_cancelled: ordine cancellato
    - fill:            fill parziale o totale
    - position_resolved: mercato risolto
    - position_redeemed: CTF token riscattato
    - stoploss_triggered: stop-loss attivato
    - kill_activated:  kill-switch attivato
    - capital_update:  capitale aggiornato
    - exposure_sync:   esposizione sincronizzata
    - creds_refresh:   API creds rigenerate
    - error:           errore operativo
    """

    def __init__(self, filepath: str = EVENT_FILE):
        self.filepath = filepath
        self._fd = None
        try:
            self._fd = open(filepath, "a", buffering=1)  # line-buffered
        except Exception as e:
            log.warning(f"EVENT LOG: cannot open {filepath}: {e}")

    def _write(self, event_type: str, data: dict):
        if not self._fd:
            return
        event = {
            "ts": time.time(),
            "type": event_type,
            **data,
        }
        try:
            self._fd.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            log.warning(f"EVENT LOG WRITE ERROR: {e}")

    # ── Order lifecycle ─────────────────────────────────────────

    def order_submitted(self, token_id: str, side: str, price: float,
                        size: float, notional: float, city: str,
                        strategy: str, order_resp: Optional[dict] = None):
        self._write("order_submitted", {
            "token_id": token_id[:16],
            "side": side,
            "price": price,
            "size": size,
            "notional_usd": notional,
            "city": city,
            "strategy": strategy,
            "api_response": str(order_resp)[:200] if order_resp else None,
        })

    def order_confirmed(self, order_id: str, token_id: str):
        self._write("order_confirmed", {
            "order_id": order_id[:16],
            "token_id": token_id[:16],
        })

    def order_rejected(self, token_id: str, side: str, error: str):
        self._write("order_rejected", {
            "token_id": token_id[:16],
            "side": side,
            "error": error[:200],
        })

    def order_cancelled(self, order_id: str):
        self._write("order_cancelled", {"order_id": order_id[:16]})

    # ── Position lifecycle ──────────────────────────────────────

    def fill(self, token_id: str, side: str, price: float,
             size: float, pnl: Optional[float] = None):
        self._write("fill", {
            "token_id": token_id[:16],
            "side": side,
            "price": price,
            "size": size,
            "pnl": pnl,
        })

    def position_resolved(self, token_id: str, outcome: str,
                          pnl: float, exposure_freed: float):
        self._write("position_resolved", {
            "token_id": token_id[:16],
            "outcome": outcome,
            "pnl": pnl,
            "exposure_freed": exposure_freed,
        })

    def position_redeemed(self, n_positions: int, result: str):
        self._write("position_redeemed", {
            "n_positions": n_positions,
            "result": result[:200],
        })

    def stoploss_triggered(self, token_id: str, title: str,
                            pnl_pct: float, size: float, sell_price: float):
        self._write("stoploss_triggered", {
            "token_id": token_id[:16],
            "title": title[:50],
            "pnl_pct": pnl_pct,
            "size": size,
            "sell_price": sell_price,
        })

    # ── Risk events ─────────────────────────────────────────────

    def kill_activated(self, reason: str, duration_h: float,
                       capital: float, drawdown: float):
        self._write("kill_activated", {
            "reason": reason,
            "duration_h": duration_h,
            "capital": capital,
            "drawdown": drawdown,
        })

    def capital_update(self, old_cap: float, new_cap: float, source: str):
        self._write("capital_update", {
            "old": old_cap,
            "new": new_cap,
            "source": source,
        })

    def exposure_sync(self, old_exp: float, new_exp: float, n_positions: int):
        self._write("exposure_sync", {
            "old": old_exp,
            "new": new_exp,
            "n_positions": n_positions,
        })

    def creds_refresh(self, success: bool, error: Optional[str] = None):
        self._write("creds_refresh", {
            "success": success,
            "error": error[:100] if error else None,
        })

    def error(self, component: str, message: str):
        self._write("error", {
            "component": component,
            "message": message[:300],
        })

    # ── Snapshot ────────────────────────────────────────────────

    def snapshot(self, state_dict: dict):
        """Registra snapshot periodico dello stato."""
        self._write("snapshot", state_dict)

    def close(self):
        if self._fd:
            self._fd.close()
