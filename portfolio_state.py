"""
portfolio_state.py — Single Source of Truth per stato TempBot
═══════════════════════════════════════════════════════════════
Risolve: audit risk #1 (troppo stato globale), #3 (exposure fragile),
         #4 (order management ottimistico)
"""

import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from enum import Enum

log = logging.getLogger("tempbot.state")


class OrderStatus(Enum):
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    PARTIAL   = "partial"
    FILLED    = "filled"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"
    RESOLVED  = "resolved"


@dataclass
class OrderRecord:
    order_id: str
    token_id: str
    side: str            # BUY / SELL
    price: float
    size_contracts: float
    notional_usd: float
    city: str
    strategy: str        # MM / ARB
    status: OrderStatus
    ts_submitted: float
    ts_updated: float = 0.0
    fill_size: float = 0.0
    fill_price: float = 0.0
    bucket: str = "fill_confirmed"  # [PATCH-P1] real market bucket from parse_bucket()


@dataclass
class PositionRecord:
    token_id: str
    size: float          # contratti
    avg_price: float
    notional: float      # size * avg_price
    city: str
    title: str = ""
    ts_opened: float = 0.0
    ts_updated: float = 0.0


class PortfolioState:
    """Unica fonte di verità per posizioni, esposizione e ordini.
    
    Regole:
    - Nessun altro modulo deve tracciare posizioni o esposizione
    - Ogni mutazione passa per i metodi di questa classe
    - Lo stato è thread-safe (lock)
    - Exposure = somma dei notional delle posizioni aperte
    """
    def __init__(self):
        self._lock = threading.Lock()
        
        # Posizioni: token_id → PositionRecord
        self.positions: Dict[str, PositionRecord] = {}
        
        # Ordini attivi: order_id → OrderRecord
        self.orders: Dict[str, OrderRecord] = {}
        
        # Stato finanziario
        self.capital: float = 0.0
        self.peak_capital: float = 0.0
        self.daily_pnl: float = 0.0
        self.daily_reset_ts: float = time.time()
        
        # Kill switch
        self.kill_until: float = 0.0
        
        # Trade IDs già processati (per PnL)
        self.processed_trade_ids: set = set()
        
        # Contatori per logging
        self._order_count: int = 0
        self._fill_count: int = 0

        # AUDIT FIX: Exposure tracciata globalmente e thread-safe
        self.total_exposure: float = 0.0

        # ── [TGT1] Migrated from module-level globals ────────────
        self._daily_reset_capital: float = 0.0
        self._local_positions: dict = {}       # token_id -> quantity (cached inventory)
        self._sl_pending: dict = {}            # token_id -> timestamp [FIX-A]
        self.SL_PENDING_TTL: float = 300.0     # seconds before stoploss retry
        self._last_trade_ids: set = set()      # dedup for PnL fill processing
        self._redeemed_condition_ids: set = set()  # dedup for auto-redeem

        # ── [TGT1] Throttle timestamps ───────────────────────────
        self._last_creds_refresh: float = 0.0
        self._last_capital_fetch: float = 0.0
        self._last_capital_value: Optional[float] = None
        self._last_redeem_check: float = 0.0
        self._last_position_exit_check: float = 0.0
        self._last_stoploss_check: float = 0.0
        self._last_exposure_sync: float = 0.0
        self._last_cancel_ts: Dict[str, float] = {}
        self._exit_tier_cache: Dict[str, str] = {}  # [PATCH-P1] token_id -> exit_tier string

    # ── Metodi Esposizione (Thread-Safe FIX CRITICAL-02) ─────────
    def add_exposure(self, amount: float):
        with self._lock:
            self.total_exposure += amount

    def remove_exposure(self, amount: float):
        with self._lock:
            self.total_exposure = max(0.0, self.total_exposure - amount)

    def set_total_exposure(self, value: float):
        with self._lock:
            self.total_exposure = max(0.0, value)

    # ── Exposure Checks ──────────────────────────────────────────
    @property
    def exposure_fraction(self) -> float:
        if self.capital <= 0: return 0.0
        return self.total_exposure / self.capital

    def can_add_exposure(self, amount: float, max_frac: float) -> bool:
        if self.capital <= 0: return False
        with self._lock:
            return ((self.total_exposure + amount) / self.capital) <= max_frac

    def exposure_by_city(self) -> Dict[str, float]:
        with self._lock:
            res = {"seoul": 0.0, "london": 0.0}
            for p in self.positions.values():
                c = p.city.lower()
                if c in res:
                    res[c] += p.notional
            return res

    # ── Gestione Ordini e Posizioni ──────────────────────────────
    def record_order(self, order_id: str, token_id: str, side: str, price: float, size: float, notional: float, city: str, strategy: str):
        with self._lock:
            self.orders[order_id] = OrderRecord(
                order_id=order_id, token_id=token_id, side=side, price=price,
                size_contracts=size, notional_usd=notional, city=city, strategy=strategy,
                status=OrderStatus.SUBMITTED, ts_submitted=time.time()
            )
            self._order_count += 1

    def sync_positions_from_api(self, api_positions: List[dict]):
        with self._lock:
            self.positions.clear()
            for p in api_positions:
                tid = str(p.get("asset") or p.get("token_id") or "")
                if not tid: continue
                size = float(p.get("size") or p.get("quantity") or 0)
                if size <= 0: continue
                # [ACTION-2] Two-phase cost-basis guard (mirrors sync_exposure_from_positions):
                #   curPrice == 0 → resolved/expired → notional = 0 (no active exposure)
                #   curPrice  > 0 → active market   → use avgPrice (entry cost-basis)
                # Prevents city/family-risk exposure inflation on winning/losing positions.
                _cur_p = float(p.get("curPrice") or p.get("currentPrice") or 0)
                if _cur_p > 0.001:
                    price = float(p.get("avgPrice") or p.get("avg_price") or _cur_p)
                else:
                    price = 0.0
                
                title = p.get("title", "")
                city = "seoul" if "seoul" in title.lower() else "london"
                
                self.positions[tid] = PositionRecord(
                    token_id=tid, size=size, avg_price=price,
                    notional=size * price, city=city, title=title,
                    ts_updated=time.time()
                )

    def set_capital(self, capital: float):
        with self._lock:
            self.capital = capital
            if capital > self.peak_capital:
                self.peak_capital = capital

    def daily_reset(self):
        with self._lock:
            self.daily_pnl = 0.0
            self.daily_reset_ts = time.time()
            self.processed_trade_ids.clear()
            log.info("DAILY RESET: pnl=0, trade_ids cleared")

    def add_pnl(self, amount: float):
        with self._lock:
            self.daily_pnl += amount

    # ── Drawdown ────────────────────────────────────────────────
    @property
    def portfolio_drawdown(self) -> float:
        if self.peak_capital <= 0:
            return 0.0
        return (self.peak_capital - self.capital) / self.peak_capital

    def get_order_meta(self, token_id: str) -> dict:
        # [PATCH-P1] Return real market bucket from OrderRecord.bucket (was always hardcoded 'fill_confirmed')
        with self._lock:
            for o in self.orders.values():
                if o.token_id == token_id:
                    return {"strategy": o.strategy, "city": o.city, "bucket_key": o.bucket, "T": 1.0, "edge": 0.0}
        return {"strategy": "MM", "city": "london", "bucket_key": "fill_confirmed", "T": 1.0, "edge": 0.0}

    # ── Snapshot ────────────────────────────────────────────────
    def snapshot(self) -> dict:
        """Snapshot completo per logging/debug."""
        with self._lock:
            return {
                "ts": time.time(),
                "capital": self.capital,
                "peak_capital": self.peak_capital,
                "total_exposure": getattr(self, 'total_exposure', 0.0),
                "exposure_frac": self.exposure_fraction,
                "daily_pnl": self.daily_pnl,
                "n_positions": len(self.positions),
                "n_orders": len(self.orders),
                "kill_until": self.kill_until,
                "city_exposure": self.exposure_by_city(),
                "positions": {
                    tid: {"size": p.size, "avg": p.avg_price, "notional": p.notional, "city": p.city}
                    for tid, p in self.positions.items()
                }
            }