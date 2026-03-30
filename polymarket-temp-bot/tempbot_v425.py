#!/usr/bin/env python3
"""
Polymarket Temperature Bot — Seoul / London LIVE  v4.25 STABLE (Becker Quant Edition)
Patch v4.25: CRITICAL-01/02/03 + HIGH-02/03/04/05 + MEDIUM-01/02/03 applied (2026-03-12)
═══════════════════════════════════════════════════════════════════
"""
import os, time, asyncio, json, math, re, logging, argparse, subprocess, functools
import sys
from collections  import defaultdict, deque
from datetime     import datetime, timezone, timedelta
from dataclasses  import dataclass, field
from typing       import Dict, List, Optional, Tuple
from pathlib      import Path

import numpy as np
import aiohttp
import websockets
import requests
from dotenv       import load_dotenv
from scipy.stats  import norm

# State management & event log
sys.path.insert(0, str(Path(__file__).parent))
from portfolio_state import PortfolioState
from event_log import EventLog

STATE  = PortfolioState()
EVENTS = EventLog()
print = functools.partial(print, flush=True)

def _sync_get(url, params=None, headers=None, timeout=10):
    return requests.get(url, params=params, headers=headers, timeout=timeout)

async def _async_get(url, params=None, headers=None, timeout=10):
    loop = asyncio.get_event_loop()
    import functools as _ft
    fn = _ft.partial(requests.get, url, params=params, headers=headers, timeout=timeout)
    return await loop.run_in_executor(None, fn)

# ── PATTERN CORRETTO ─────────────────────────────────────────
async def _async_clob(func, *args, timeout=10.0, **kwargs):
    if func is None: return None
    loop = asyncio.get_event_loop()
    pfunc = functools.partial(func, *args, **kwargs)
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, pfunc), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning(f"CLOB TIMEOUT: {func.__name__} > {timeout}s")
        return None
    except Exception as e:
        log.warning(f"CLOB ERROR {func.__name__}: {e}")
        return None

def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    try:
        if config_path.exists():
            with open(config_path) as f:
                cfg = json.load(f)
                logging.info(f"CONFIG: loaded from {config_path}")
                return cfg
    except Exception as e:
        logging.warning(f"CONFIG ERROR: {e} — using defaults")
    return {}

_CFG = _load_config()
def cfg(section: str, key: str, default):
    return _CFG.get(section, {}).get(key, default)

try:
    import pulp
    PULP_AVAILABLE = True
except ImportError:
    PULP_AVAILABLE = False
    logging.warning("pulp non disponibile — ARB LP disabilitato")

try:
    from py_clob_client.client     import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs
    from py_clob_client.constants  import POLYGON
    try:
        from py_clob_client.clob_types import CreateOrderOptions
    except ImportError:
        CreateOrderOptions = None
    try:
        from py_clob_client.order_builder.constants import ORDER_TYPE_GTC
        _ORDER_TYPE_GTC = ORDER_TYPE_GTC
    except ImportError:
        try:
            from py_clob_client.constants import OrderType
            _ORDER_TYPE_GTC = OrderType.GTC
        except ImportError:
            _ORDER_TYPE_GTC = "GTC"
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    CreateOrderOptions = None
    _ORDER_TYPE_GTC = "GTC"

load_dotenv()
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ══════════════════════════════════════════════════════════════════
# CONSTANTS & RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════
MIN_MAKER_SPREAD     = cfg("trading", "MIN_MAKER_SPREAD", 0.0077)
MAX_MAKER_SPREAD     = cfg("trading", "MAX_MAKER_SPREAD", 0.0125)
GAMMA                = cfg("trading", "GAMMA", 0.1)
K_INTENSITY          = cfg("trading", "K_INTENSITY", 1.5)
MAX_RUIN_PROB        = cfg("risk", "MAX_RUIN_PROB", 0.01)
MAX_DD               = cfg("risk", "MAX_DD", 0.15)
MAX_PORTFOLIO_DD     = cfg("risk", "MAX_PORTFOLIO_DD", 0.40)
MC_PATHS             = cfg("risk", "MC_PATHS", 2_500)  # [FIX MEDIUM-03] 2500 paths: errore p95 <0.5%, -75% RAM/GC pressure
MIN_DEPTH_USD        = cfg("trading", "MIN_DEPTH_USD", 100.0)
UMA_BUFFER           = cfg("trading", "UMA_BUFFER", 0.004)
ARB_MAKER_BUFFER     = cfg("trading", "ARB_MAKER_BUFFER", 0.012)
SLIPPAGE_GUARD       = cfg("trading", "SLIPPAGE_GUARD", 0.008)
MISPRICING_THRESHOLD = cfg("trading", "MISPRICING_THRESHOLD", 0.005)
MIN_FORECAST_EDGE    = cfg("trading", "MIN_FORECAST_EDGE", 0.015)

# ❌ REGOLE ASSOLUTE
DAILY_STOPLOSS        = cfg("risk", "DAILY_STOPLOSS", -0.15)
KELLY_FULL_CAP        = cfg("risk", "KELLY_FULL_CAP", 0.5)
STOPLOSS_POSITION_PCT = cfg("risk", "STOPLOSS_POSITION_PCT", 0.95)

NEAR_ARB_CAP_FRAC    = cfg("risk", "NEAR_ARB_CAP_FRAC", 0.10)
CITY_CAP_FRAC        = cfg("risk", "CITY_CAP_FRAC", 0.40)
MIN_T_HOURS          = cfg("risk", "MIN_T_HOURS", 3)
_NEG_RISK_FALLBACK   = os.getenv("NEG_RISK_FALLBACK", "true").lower() != "false"

RESOLVED_FUNDER: str = ""
CREDS_REFRESH_INTERVAL = cfg("intervals", "CREDS_REFRESH_INTERVAL", 1800)
LOOP_SLEEP           = cfg("trading", "LOOP_SLEEP", 3)

# ❌ BECKER ZONES 
BECKER_BUY_ZONE_MIN  = 0.06
BECKER_BUY_ZONE_MAX  = 0.60
BECKER_SELL_ZONE_MIN = 0.75
BECKER_SELL_ZONE_MAX = 0.95

CITY_SIGMA_BASE      = {"seoul": 2.8, "london": 2.2}
GROUP_CAP_FRAC       = 0.25
MARKET_CACHE_TTL     = 60
FORECAST_CACHE_TTL   = 1800
FORECAST_FALLBACK_TTL = 300
POSITIONS_FILE       = "positions.json"

CANCEL_CONFIRM_TIMEOUT = 4.0
CANCEL_MIN_WAIT        = 2.0
_last_cancel_ts: Dict[str, float] = {}

MAX_ORDERS_PER_CYCLE   = 3
TOTAL_EXPOSURE         = 0.0
MAX_TOTAL_EXPOSURE_FRAC = cfg("risk", "MAX_TOTAL_EXPOSURE_FRAC", 0.75)

CITY_COORDS = {
    "seoul":  {"lat": 37.5665, "lon": 126.9780, "tz": "Asia/Seoul"},
    "london": {"lat": 51.5074, "lon": -0.1278,  "tz": "Europe/London"},
}

WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

logging.basicConfig(
    filename="tempbot.log", level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("tempbot")

FORECAST_CACHE: dict = {}
MARKET_CACHE:   dict = {"data": [], "ts": 0.0}
WS_BOOK:        Dict[str, dict] = {}
TOKEN_META: Dict[str, dict] = {}
# [FIX HIGH-05] Lock per accesso thread-safe a WS_BOOK (ws_thread vs trading_loop)
import threading as _threading
_WS_BOOK_LOCK = _threading.Lock()

daily_pnl      = 0.0
daily_reset_ts = time.time()
_daily_reset_capital = 0.0
kill_until     = 0.0
peak_capital   = 0.0

# [FIX HIGH-02] OrderedDict FIFO — evita doppio conteggio dopo pruning casuale
from collections import OrderedDict as _OrderedDict
_last_trade_ids: _OrderedDict = _OrderedDict()
_local_positions: dict = {}

@dataclass
class GarchState:
    alpha0: float = 1e-5
    alpha1: float = 0.15
    beta1:  float = 0.80
    sigma2: float = 1e-4
    residuals: deque = field(default_factory=lambda: deque(maxlen=100))

    def update(self, price_return: float) -> float:
        eps2 = price_return ** 2
        self.residuals.append(eps2)
        self.sigma2 = (self.alpha0 + self.alpha1 * eps2 + self.beta1 * self.sigma2)
        self.sigma2 = max(self.sigma2, 1e-8)
        return math.sqrt(self.sigma2)

    def spread_multiplier(self) -> float:
        baseline = math.sqrt(self.alpha0 / max(1 - self.alpha1 - self.beta1, 0.01))
        ratio    = math.sqrt(self.sigma2) / max(baseline, 1e-6)
        mult     = min(3.0, max(1.0, ratio))
        return mult

GARCH_STATES: Dict[str, GarchState] = defaultdict(GarchState)

@dataclass
class FactorModel:
    f1_history: deque = field(default_factory=lambda: deque(maxlen=30))
    f2_seoul:   deque = field(default_factory=lambda: deque(maxlen=30))
    f2_london:  deque = field(default_factory=lambda: deque(maxlen=30))
    f3_history: deque = field(default_factory=lambda: deque(maxlen=30))

    def update_factors(self, seoul_anom: float, london_anom: float, spread_change: float):
        f1 = (seoul_anom + london_anom) / 2.0
        self.f1_history.append(f1)
        self.f2_seoul.append(seoul_anom - f1)
        self.f2_london.append(london_anom - f1)
        self.f3_history.append(spread_change)

    def factor_covariance(self) -> np.ndarray:
        if len(self.f1_history) < 5: return np.eye(3) * 0.001
        n = min(len(self.f1_history), len(self.f3_history), len(self.f2_seoul), len(self.f2_london))
        f2_avg = [(s+l)/2 for s,l in zip(list(self.f2_seoul)[-n:], list(self.f2_london)[-n:])]
        F = np.column_stack([list(self.f1_history)[-n:], f2_avg, list(self.f3_history)[-n:]])
        return np.cov(F.T) + np.eye(3) * 1e-8

    def factor_loadings(self, token_mid: float, city: str) -> np.ndarray:
        nao = _get_nao_corr()
        beta_f1 = max(0.0, nao * (1.0 - 2.0 * abs(token_mid - 0.5)))
        beta_f2 = 0.60 if city == "london" else (0.35 if city == "seoul" else 0.0)
        beta_f3 = 0.20
        return np.array([beta_f1, beta_f2, beta_f3])

    def idiosyncratic_var(self, mid: float) -> float:
        return max(mid * (1 - mid), 1e-6)

    def total_var(self, mid: float, city: str) -> float:
        beta = self.factor_loadings(mid, city)
        sigma_f = self.factor_covariance()
        return float(beta @ sigma_f @ beta) + self.idiosyncratic_var(mid)

def _seed_factor_model(fm: FactorModel, n_seed: int = 10):
    import random
    rng = random.Random(42)
    for _ in range(n_seed):
        common   = rng.gauss(0.0, 0.03)
        seo_idio = rng.gauss(0.0, 0.025)
        lon_idio = rng.gauss(0.0, 0.020)
        spread_c = rng.gauss(0.0, 0.015)
        fm.update_factors(common + seo_idio, common + lon_idio, spread_c)

FACTOR_MODEL = FactorModel()
_seed_factor_model(FACTOR_MODEL)

@dataclass
class PortfolioRisk:
    positions:   Dict[str, dict] = field(default_factory=dict)
    mc_var_99:   float = 0.0
    mc_var_ts:   float = 0.0
    MC_INTERVAL: int   = 600

    def update_position(self, token_id: str, size: float, mid: float, city: str):
        self.positions[token_id] = {"size": size, "mid": mid, "city": city}

    def parametric_var_99(self, capital: float) -> float:
        if not self.positions: return 0.0
        total_var = 0.0
        toks = list(self.positions.values())
        for pos in toks:
            w = pos["size"] / max(capital, 1.0)
            total_var += (w**2) * FACTOR_MODEL.total_var(pos["mid"], pos.get("city","london"))
        for i in range(len(toks)):
            for j in range(i+1, len(toks)):
                wi = toks[i]["size"] / max(capital, 1)
                wj = toks[j]["size"] / max(capital, 1)
                bi = FACTOR_MODEL.factor_loadings(toks[i]["mid"], toks[i].get("city","london"))
                bj = FACTOR_MODEL.factor_loadings(toks[j]["mid"], toks[j].get("city","london"))
                cov_ij = float(bi @ FACTOR_MODEL.factor_covariance() @ bj)
                total_var += 2 * wi * wj * cov_ij
        return 2.33 * math.sqrt(max(total_var, 0)) * capital

    def mc_portfolio_var(self, capital: float, n_paths: int = 10_000) -> float:
        now = time.time()
        if now - self.mc_var_ts < self.MC_INTERVAL and self.mc_var_99 > 0:
            return self.mc_var_99
        if not self.positions: return 0.0
        toks    = list(self.positions.values())
        sigma_f = FACTOR_MODEL.factor_covariance()
        try:
            L = np.linalg.cholesky(sigma_f + np.eye(3) * 1e-8)
        except np.linalg.LinAlgError:
            L = np.eye(3) * 0.01
        factor_shocks = np.random.randn(n_paths, 3) @ L.T
        pnl_paths     = np.zeros(n_paths)
        for pos in toks:
            beta       = FACTOR_MODEL.factor_loadings(pos["mid"], pos.get("city","london"))
            idio_sigma = math.sqrt(FACTOR_MODEL.idiosyncratic_var(pos["mid"]))
            ret        = factor_shocks @ beta + np.random.randn(n_paths) * idio_sigma
            pnl_paths += pos["size"] * ret
        var_99 = float(np.percentile(-pnl_paths, 99))
        self.mc_var_99 = max(var_99, 0.0)
        self.mc_var_ts = now
        return self.mc_var_99

    def var_budget_fraction(self, capital: float, new_size: float, new_mid: float, city: str) -> float:
        var_budget   = 0.20 * capital
        current_var  = self.parametric_var_99(capital)
        beta         = FACTOR_MODEL.factor_loadings(new_mid, city)
        marginal_var = 2.33 * math.sqrt(
            float(beta @ FACTOR_MODEL.factor_covariance() @ beta) + FACTOR_MODEL.idiosyncratic_var(new_mid)
        ) * new_size
        return (current_var + marginal_var) / max(var_budget, 1.0)

PORTFOLIO_RISK = PortfolioRisk()

GLS_CALIBRATION: dict = {}

def load_becker_gls(calib_file: str = "weather_calibration_becker.json") -> dict:
    try:
        with open(calib_file) as f: B = json.load(f)
        CAL = B.get("calibration_surface", {})
    except Exception:
        return {p: {"gls_edge": 0.0, "gls_sigma": 0.08} for p in range(1, 101)}

    gls_edges = {}
    for k, d in CAL.items():
        try:
            pct     = int(k)
            actual  = float(d["actual_probability"])
            implied = float(d["implied_probability"])
            n       = int(d["total_trades"])
            var_b   = max(actual * (1 - actual), 1e-8)
            T_proxy = 7.0
            var_s   = var_b * (1.0 + 5.0 / max(T_proxy, 0.5))
            gls_w   = n / var_s
            gls_edges[pct] = {
                "gls_edge": actual - implied,
                "gls_weight": gls_w,
                "gls_sigma": math.sqrt(var_b / max(n, 1)),
            }
        except Exception: continue
    if not gls_edges: return {p: {"gls_edge": 0.0, "gls_sigma": 0.08} for p in range(1, 101)}
    return gls_edges

def get_gls_edge(mid: float) -> Tuple[float, float]:
    global GLS_CALIBRATION
    if not GLS_CALIBRATION: GLS_CALIBRATION = load_becker_gls()
    pct = round(mid * 100)
    if pct in GLS_CALIBRATION:
        d = GLS_CALIBRATION[pct]
        return d["gls_edge"], d["gls_sigma"]
    keys = sorted(GLS_CALIBRATION.keys())
    for i in range(len(keys)-1):
        lo, hi = keys[i], keys[i+1]
        if lo <= pct <= hi:
            t = (pct-lo)/(hi-lo)
            return (GLS_CALIBRATION[lo]["gls_edge"]*(1-t) + GLS_CALIBRATION[hi]["gls_edge"]*t,
                    GLS_CALIBRATION[lo]["gls_sigma"]*(1-t) + GLS_CALIBRATION[hi]["gls_sigma"]*t)
    return 0.0, 0.08

def logit_clip(p: float) -> float:
    return max(0.02, min(0.98, p))

def _get_nao_corr() -> float:
    m = datetime.now(timezone.utc).month
    if 6 <= m <= 8: return 0.12
    elif m in (3,4,5,9,10,11): return 0.22
    return 0.35

def hours_to_resolution(m: dict) -> float:
    res = m.get("resolution_time") or m.get("endDate") or ""
    try:
        dt   = datetime.fromisoformat(res.replace("Z","+00:00"))
        diff = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.5, diff/3600.0)
    except Exception: return 0.0

def clean_token_id(raw) -> str:
    if isinstance(raw, list) and raw: return str(raw[0]).strip()
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if parsed: return str(parsed[0]).strip()
            except Exception: pass
        return raw
    return str(raw).strip() if raw else ""

async def ws_orderbook_listener(token_ids: List[str]):
    if not token_ids: return
    subscribe_msg = {"assets_ids": token_ids, "operation": "subscribe"}
    from websockets.asyncio.client import connect as ws_connect
    retry_delay = 5

    while True:
        try:
            async with ws_connect(
                WS_ENDPOINT, ping_interval=30, ping_timeout=30,
                close_timeout=10, open_timeout=20, additional_headers={"User-Agent": "TempBot/4.24"},
            ) as ws:
                await ws.send(json.dumps(subscribe_msg))
                print(f"📡 WS connected: {len(token_ids)} token", flush=True)
                retry_delay = 5
                async for raw in ws:
                    try:
                        if not raw or raw == '[]': await asyncio.sleep(0); continue
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            for item in parsed:
                                if isinstance(item, dict): _process_ws_message(item)
                        elif isinstance(parsed, dict): _process_ws_message(parsed)
                    except json.JSONDecodeError: continue
        except Exception as e:
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

def _process_ws_message(msg: dict):
    event_type = msg.get("event_type") or msg.get("type","")
    if event_type not in ("book","price_change","orderbook_snapshot","tick_size_change"): return
    market = (msg.get("asset_id") or msg.get("market") or msg.get("condition_id") or "")
    if not market: return

    def parse_side(entries):
        out = []
        for e in entries:
            try:
                p = float(e.get("price",0) or (e[0] if isinstance(e,(list,tuple)) else 0))
                s = float(e.get("size",0)  or (e[1] if isinstance(e,(list,tuple)) else 0))
                if p>0 and s>0: out.append((p,s))
            except Exception: continue
        return out

    bids = sorted(parse_side(msg.get("bids",[])), reverse=True)
    asks = sorted(parse_side(msg.get("asks",[])))
    if not bids and not asks: return

    # [FIX HIGH-05] lettura old_mid sotto lock prima di aggiornare GARCH
    with _WS_BOOK_LOCK:
        _old_entry = WS_BOOK.get(market)
    if _old_entry and bids and asks:
        old_mid = _old_entry.get("mid", 0)
        if old_mid > 0:
            new_mid = (bids[0][0] + asks[0][0]) / 2
            GARCH_STATES[market].update((new_mid - old_mid) / old_mid)

    new_mid = (bids[0][0]+asks[0][0])/2 if bids and asks else 0.0
    # [FIX HIGH-05] scrittura atomica con lock
    _new_entry = {"bids": bids, "asks": asks, "mid": new_mid, "depth": sum(b[1] for b in bids[:5]), "ts": time.time()}
    with _WS_BOOK_LOCK:
        WS_BOOK[market] = _new_entry

async def get_book(token_id: str, client=None, market_obj: dict = None) -> Tuple[Optional[list], Optional[list]]:
    # [FIX HIGH-05] lettura con lock
    with _WS_BOOK_LOCK:
        cached = WS_BOOK.get(token_id)
        _ws_age = time.time() - cached.get("ts", 0) if cached else 999
        if cached and _ws_age > 30.0:
            WS_BOOK.pop(token_id, None)
            cached = None
    if cached and _ws_age < 2.0: return cached["bids"], cached["asks"]
    if client: return await _orderbook_rest(client, token_id)
    if DRY_RUN:
        bids, asks = await _orderbook_public(token_id)
        if bids and asks: return bids, asks
        if market_obj: return _book_from_outcome_prices(market_obj)
    return None, None

async def _orderbook_public(token_id: str) -> Tuple[Optional[list], Optional[list]]:
    try:
        tid = clean_token_id(token_id)
        if not tid: return None, None
        r = await _async_get("https://clob.polymarket.com/book", params={"token_id": tid}, timeout=5)
        if r is None or r.status_code != 200: return None, None
        data = r.json()
        if "error" in data: return None, None
        def parse(entries):
            out = []
            for e in (entries or []):
                try:
                    p = float(e.get("price") or e.get("p") or 0)
                    s = float(e.get("size")  or e.get("s") or 0)
                    if p > 0 and s > 0: out.append((p, s))
                except Exception: continue
            return out
        return sorted(parse(data.get("bids", [])), reverse=True) or None, sorted(parse(data.get("asks", []))) or None
    except Exception: return None, None

def _book_from_outcome_prices(m: dict) -> Tuple[Optional[list], Optional[list]]:
    try:
        prices_raw = m.get("outcomePrices") or []
        if isinstance(prices_raw, str): prices_raw = json.loads(prices_raw)
        if not prices_raw: return None, None
        yes_price = float(prices_raw[0])
        if yes_price <= 0 or yes_price >= 1: return None, None
        half = MIN_MAKER_SPREAD / 2
        bid_raw, ask_raw = yes_price - half, yes_price + half
        lo, hi  = 0.011, 0.989
        if ask_raw > hi: shift = ask_raw - hi; bid_raw -= shift; ask_raw -= shift
        if bid_raw < lo: shift = lo - bid_raw; bid_raw += shift; ask_raw += shift
        bid, ask = max(lo, bid_raw), min(hi, ask_raw)
        if bid >= ask: return None, None
        size = 100.0
        return [(bid, size)], [(ask, size)]
    except Exception: return None, None

async def _orderbook_rest(client, token_id: str):
    try:
        book = await _async_clob(client.get_order_book, token_id)
        if not book: return None, None
        bids_raw = book.bids if hasattr(book,"bids") else book.get("bids",[])
        asks_raw = book.asks if hasattr(book,"asks") else book.get("asks",[])
        def parse(entries):
            out = []
            for e in entries:
                if hasattr(e,"price"): out.append((float(e.price), float(e.size)))
                elif isinstance(e,dict): out.append((float(e["price"]), float(e["size"])))
            return out
        return sorted(parse(bids_raw),reverse=True), sorted(parse(asks_raw))
    except Exception: return None, None

FORECAST_MODELS = [("ecmwf_ifs04", 0.40), ("gfs_seamless", 0.30), ("icon_seamless", 0.20), ("meteofrance_seamless", 0.10)]

async def _fetch_model_async(session: aiohttp.ClientSession, coords: dict, model_name: str, weight: float):
    try:
        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude":coords["lat"],"longitude":coords["lon"],"daily":"temperature_2m_max","timezone":coords["tz"],"forecast_days":7,"models":model_name},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return model_name, data, weight
    except Exception: return model_name, {}, 0.0

async def fetch_weather_forecast_async(city: str) -> dict:
    city = city.lower()
    now  = time.time()
    if city in FORECAST_CACHE:
        cached = FORECAST_CACHE[city]
        ttl = FORECAST_FALLBACK_TTL if cached.get("is_fallback") else FORECAST_CACHE_TTL
        if now - cached["ts"] < ttl: return cached["data"]

    coords = CITY_COORDS[city]
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[_fetch_model_async(session, coords, m, w) for m, w in FORECAST_MODELS], return_exceptions=True)

    ensemble: dict = {}; weights_used: dict = {}
    for res in results:
        if isinstance(res, Exception): continue
        model_name, data, weight = res
        if not data or weight == 0: continue
        try:
            for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                if temp is None: continue
                ensemble[date] = ensemble.get(date,0.0) + temp*weight
                weights_used[date] = weights_used.get(date,0.0) + weight
        except Exception: continue

    if not ensemble:
        CLIM = {"seoul": {(1,10): 9.0, (11,20): 12.0, (21,31): 15.0}, "london": {(1,10): 10.0, (11,20): 11.5, (21,31): 13.0}}
        clim_city = CLIM.get(city, CLIM["london"])
        today = datetime.now(timezone.utc).date()
        result = {}
        for day_offset in range(8):
            d = today + timedelta(days=day_offset)
            date_str = d.isoformat()
            day_of_month = d.day
            for (lo, hi), temp in clim_city.items():
                if lo <= day_of_month <= hi:
                    result[date_str] = round(temp + (day_of_month % 5 - 2) * 0.3, 1)
                    break
        FORECAST_CACHE[city] = {"data": result, "ts": now, "is_fallback": True}
        return result

    result = {d: round(ensemble[d]/weights_used[d],2) for d in ensemble if weights_used.get(d,0)>0}
    FORECAST_CACHE[city] = {"data": result, "ts": now, "is_fallback": False}
    return result

async def fetch_markets_async() -> list:
    now = time.time()
    if MARKET_CACHE["data"] and now - MARKET_CACHE["ts"] < MARKET_CACHE_TTL: return MARKET_CACHE["data"]
    MARKET_CACHE["printed"] = False

    all_markets, seen_ids, slugs = [], set(), []
    today = datetime.now(timezone.utc)
    for day_offset in range(0, 15):
        dt = today + timedelta(days=day_offset)
        date_part = f"{dt.strftime('%B').lower()}-{dt.day}-{dt.year}"
        for city in ("seoul", "london"): slugs.append(f"highest-temperature-in-{city}-on-{date_part}")

    async with aiohttp.ClientSession() as session:
        async def fetch_slug(slug: str):
            try:
                async with session.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=10) as resp:
                    if resp.status != 200: return []
                    data = await resp.json()
                    if not data: return []
                    event = data[0] if isinstance(data, list) else data
                    out = []
                    for m in (event.get("markets") or []):
                        if not isinstance(m, dict) or not m.get("acceptingOrders", False): continue
                        mid = m.get("conditionId") or m.get("id") or ""
                        if mid in seen_ids: continue
                        seen_ids.add(mid)
                        if not m.get("resolution_time") and not m.get("endDate"): m["resolution_time"] = (event.get("endDate") or event.get("end_date") or "")
                        if not m.get("question"): m["question"] = event.get("title") or ""
                        out.append(m)

                        _tok = clean_token_id(m.get("clobTokenIds") or m.get("clob_token_ids") or "")
                        if _tok:
                            _nr_raw = m.get("negRisk") if m.get("negRisk") is not None else m.get("neg_risk") if m.get("neg_risk") is not None else event.get("negRisk") if event.get("negRisk") is not None else event.get("neg_risk")
                            if _nr_raw is None:
                                _fallback_val = os.getenv("NEG_RISK_FALLBACK", "true").lower() != "false"
                                _neg_risk = _fallback_val
                                if not _fallback_val: continue
                            else: _neg_risk = bool(_nr_raw)

                            _tick_raw  = (m.get("minimumTickSize") or m.get("minimum_tick_size") or "0.01")
                            _tick_size = str(float(_tick_raw)) if _tick_raw else "0.01"
                            try:
                                _tick_size = f"{float(_tick_size):g}"
                                if "." not in _tick_size: _tick_size += ".0"
                            except Exception: _tick_size = "0.01"
                            TOKEN_META[_tok] = {"neg_risk": _neg_risk, "tick_size": _tick_size}
                    return out
            except Exception: return []

        for i in range(0, len(slugs), 5):
            results = await asyncio.gather(*[fetch_slug(s) for s in slugs[i:i+5]], return_exceptions=True)
            for mkts in results:
                if not isinstance(mkts, Exception): all_markets.extend(mkts)

    MARKET_CACHE["data"] = all_markets; MARKET_CACHE["ts"] = now; MARKET_CACHE["printed"] = True
    return all_markets

def bregman_projection(prices: list) -> tuple:
    s = sum(prices) or 1.0
    p_proj = [p/s for p in prices]
    KL = sum(p*math.log(p/pp) for p,pp in zip(prices,p_proj) if p>1e-10 and pp>1e-10)
    return abs(s-1.0), abs(KL)

def integer_program_arb(prices, best_asks, market_ids, kelly_size):
    if not PULP_AVAILABLE: return None, 0.0
    n    = len(best_asks)
    prob = pulp.LpProblem("arb", pulp.LpMaximize)
    buys = [pulp.LpVariable(f"buy_{i}", 0, kelly_size) for i in range(n)]
    ppu  = 1.0 - sum(best_asks)
    prob += ppu * pulp.lpSum(buys)
    for b in buys: prob += b <= kelly_size
    prob += pulp.lpSum(buys) <= kelly_size * n
    prob.solve(pulp.COIN_CMD(msg=0, gapRel=0.05))
    sizes = [pulp.value(b) or 0.0 for b in buys]
    net   = ppu * sum(sizes) - sum(sizes)*(UMA_BUFFER+SLIPPAGE_GUARD)
    if net>0 and ppu>0: return {mid:sz for mid,sz in zip(market_ids,sizes) if sz>0.5}, net
    return None, 0.0

def group_buckets_by_day(markets: list) -> dict:
    groups = defaultdict(list)
    for m in markets:
        q    = m.get("question","").lower()
        city = "seoul" if "seoul" in q else "london"
        dm   = re.search(r'(\d{4}-\d{2}-\d{2})', m.get("resolution_time","") or "")
        if dm: date_key = dm.group(1)
        else:
            mm = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})', q)
            if not mm: continue
            try: date_key = datetime.strptime(f"{mm.group(1)} {mm.group(2)} {datetime.now(timezone.utc).year}", "%B %d %Y").strftime("%Y-%m-%d")
            except Exception: continue
        groups[f"{city}_{date_key}"].append(m)
    return {k:v for k,v in groups.items() if len(v)>=2}

def cross_city_arb_check(fc_london: dict, fc_seoul: dict, date_key: str) -> dict:
    t_lon, t_seo = fc_london.get(date_key), fc_seoul.get(date_key)
    if t_lon is None or t_seo is None: return {"corr_edge":0.0,"direction":"none"}
    lon_anom, seo_anom = t_lon - 8.0, t_seo - 4.0
    nao = _get_nao_corr()
    corr_edge = min(abs(lon_anom - seo_anom*nao)/10.0, 0.05)
    direction = ("london_warm_seoul_cold" if lon_anom>0>seo_anom else "london_cold_seoul_warm" if lon_anom<0<seo_anom else "concordant")
    FACTOR_MODEL.update_factors(seo_anom, lon_anom, 0.0)
    return {"corr_edge":corr_edge,"direction":direction, "lon_temp":t_lon,"seo_temp":t_seo}

def parse_bucket(question: str) -> dict:
    q = question.lower()
    for patterns, btype in [([r'(\d+).*?or higher',r'at least (\d+)', r'(\d+).*?or above',r'above (\d+)'], "or_higher"),
                            ([r'(\d+).*?or lower',r'(\d+).*?or below', r'at most (\d+)',r'below (\d+)'], "or_lower"),
                            ([r'be\s+(\d+)',r'equal to (\d+)', r'exactly (\d+)',r'(\d+)\s*°?c'], "exact")]:
        for pat in patterns:
            m = re.search(pat, q)
            if m: return {"type":btype,"threshold":float(m.group(1)),"valid":True}
    m = re.search(r'(\d+)', q)
    if m: return {"type":"exact","threshold":float(m.group(1)),"valid":False}
    return {"type":"exact","threshold":0.0,"valid":False}

def ecmwf_true_prob(forecast_temp: float, bucket_type: str, threshold: float, T_days: float = None, city: str = "london", is_fallback: bool = False) -> float:
    base_sigma = CITY_SIGMA_BASE.get(city, 2.5)
    sigma = base_sigma * math.sqrt(max(T_days if T_days is not None else 7.0, 0.04) / 7.0)
    if is_fallback: sigma += 1.5
    if bucket_type == "exact": return (norm.cdf(threshold+0.5, forecast_temp, sigma) - norm.cdf(threshold-0.5, forecast_temp, sigma))
    elif bucket_type == "or_higher": return 1.0 - norm.cdf(threshold - 0.5, forecast_temp, sigma)
    elif bucket_type == "or_lower": return norm.cdf(threshold + 0.5, forecast_temp, sigma)
    return 0.5

def kelly_fraction_dynamic(mid: float, edge: float, sigma: float) -> float:
    if edge <= 0: return 0.0
    is_sell_zone = mid > 0.60
    effective_mid = (1.0 - mid) if is_sell_zone else mid
    pct = round(effective_mid * 100)
    if 1 <= pct <= 10: base = {1:0.15,2:0.15,3:0.15,4:0.18,5:0.18,6:0.18,7:0.20,8:0.20,9:0.22,10:0.25}.get(pct,0.15)
    elif 11 <= pct <= 30: base = 0.30
    elif 31 <= pct <= 50: base = 0.35
    elif 51 <= pct <= 60: base = 0.22
    else: base = 0.15

    edge_mult = min(1.6, max(0.7, edge/0.04))
    _, gls_sigma    = get_gls_edge(mid)
    gls_confidence  = min(1.0, max(0.5, 0.01/max(gls_sigma,1e-6)))
    vol_mult = min(1.3, max(0.5, 0.25/max(sigma,0.01)))
    return min(0.55, max(0.05, base * edge_mult * vol_mult * gls_confidence))

def avellaneda_stoikov_quote(mid: float, q: float, sigma: float, T: float, token_id: str = "") -> tuple:
    T, T_adj = max(T, 0.001), max(T, 0.04)
    res = mid - q*GAMMA*(sigma**2)*T
    delta = (GAMMA*(sigma**2)*T_adj + (2/GAMMA)*math.log(1+GAMMA/K_INTENSITY))
    bid, ask = logit_clip(res - delta/2), logit_clip(res + delta/2)
    gm = GARCH_STATES[token_id].spread_multiplier() if token_id else 1.0
    min_s, max_s = MIN_MAKER_SPREAD * gm, MAX_MAKER_SPREAD * gm * 1.5
    spread = ask - bid
    if spread < min_s: bid, ask = max(0.02, res - min_s/2), min(0.98, res + min_s/2)
    elif spread > max_s: bid, ask = max(0.02, res - max_s/2), min(0.98, res + max_s/2)
    return bid, ask

def estimate_sigma(mid: float, spread: float, T: float) -> float:
    sig_b = math.sqrt(max(mid*(1-mid),1e-6))
    sig_s = spread / max(mid,0.01)
    w     = min(1.0, max(0.0, 1.0-T*5))
    sigma = w*sig_b + (1-w)*sig_s
    if mid<0.05 or mid>0.95: sigma = min(sigma, 0.15)
    elif mid<0.10 or mid>0.90: sigma = min(sigma, 0.25)
    return max(sigma, 0.01)

def time_edge_mult(T_days: float) -> float:
    bp = [(0.5, 0.30), (1, 0.60), (3, 1.00), (7, 0.80), (14, 0.50)]
    if T_days <= 0.5: return 0.30
    if T_days >= 14:  return 0.50
    for i in range(len(bp)-1):
        lo_d, lo_m = bp[i]; hi_d, hi_m = bp[i+1]
        if lo_d <= T_days <= hi_d: return round(lo_m + ((T_days - lo_d) / (hi_d - lo_d)) * (hi_m - lo_m), 3)
    return 1.00

def kelly_and_mc(edge: float, mid: float, depth_usd: float, sigma: float, capital: float, T: float = 1.0, token_id: str = "", city: str = "london") -> float:
    sigma_real = estimate_sigma(mid, sigma, T)
    variance   = sigma_real**2 + 1e-9
    frac       = kelly_fraction_dynamic(mid, edge, sigma_real)
    if frac == 0.0: return 0.0

    kelly_full = min(edge / (max(mid,0.01)*variance), KELLY_FULL_CAP)
    depth_mult = min(2.0, max(0.3, np.sqrt(depth_usd/MIN_DEPTH_USD)))
    size       = frac * kelly_full * capital * depth_mult * time_edge_mult(T)
    # ❌ [BECKER] Hard cap 2% per trade
    size       = min(max(size,1.0), capital*0.02)

    # [FIX CRITICAL-02] lettura da STATE (fonte canonica)
    if not STATE.can_add_exposure(size, MAX_TOTAL_EXPOSURE_FRAC):
        size = max(0.0, STATE.capital * MAX_TOTAL_EXPOSURE_FRAC - STATE.total_exposure)
        if size < 1.0: return 0.0

    var_frac = PORTFOLIO_RISK.var_budget_fraction(capital, size, mid, city)
    if var_frac > 1.0: size = max(size * (1.0/var_frac)*0.9, 1.0)

    if sigma_real > 0.50: sigma_real = 0.50
    dt = 1.0/48
    if T <= 0.5:
        outcomes = np.random.binomial(1, mid, (MC_PATHS, 1)).astype(float)
        noise    = np.random.randn(MC_PATHS, 1) * math.sqrt(mid*(1-mid)) * 0.3
        paths    = np.clip(np.tile(outcomes + noise, (1, 48)), 0.01, 0.99)
    else:
        paths = np.clip(mid + np.cumsum(np.random.randn(MC_PATHS,48)*sigma_real*math.sqrt(dt), axis=1), 0.01, 0.99)

    peak      = np.maximum.accumulate(paths, axis=1)
    trough    = (peak - paths) / np.maximum(peak, 0.01)
    drawdowns = np.max(trough, axis=1) * (size/capital)

    if float(np.mean(drawdowns > MAX_DD)) > MAX_RUIN_PROB: return 0.0
    if float(np.percentile(drawdowns, 95)) > MAX_DD: size *= 0.5
    return size

_last_creds_refresh = 0.0
async def refresh_api_creds(client):
    global _last_creds_refresh
    now = time.time()
    if now - _last_creds_refresh < CREDS_REFRESH_INTERVAL: return
    _last_creds_refresh = now
    if not client or not CLOB_AVAILABLE: return
    try:
        creds = await _async_clob(client.create_or_derive_api_creds)
        if creds:
            client.set_api_creds(creds)
        EVENTS.creds_refresh(True)
    except Exception as e:
        EVENTS.creds_refresh(False, str(e))

async def place_order(client, token_id: str, side: str, price: float, size: float, city: str = "london", strategy: str = "MM"):
    if time.time() < kill_until or DRY_RUN or not CLOB_AVAILABLE: return

    if strategy != "STOPLOSS":
        if side == "BUY" and price > BECKER_BUY_ZONE_MAX + 0.01: return
        if side == "SELL" and price < BECKER_SELL_ZONE_MIN - 0.01: return

    chunks = [size/2, size/2] if size>50 else [size]
    for chunk in chunks:
        _meta = TOKEN_META.get(token_id, {})
        _neg_risk = _meta.get("neg_risk", True)
        _tick_size = str(_meta.get("tick_size", "0.01"))
        _tick_precision = len(_tick_size.split('.')[-1]) if '.' in _tick_size else 0
        _tick_unit = 10 ** _tick_precision
        contracts = math.floor((chunk / price if price > 0 else 0) * _tick_unit) / _tick_unit

        # [FIX CRITICAL-03] min 1 contratto (configurabile), non 5 hardcoded
        _min_contracts = cfg("trading", "MIN_CONTRACTS", 1)
        if contracts < _min_contracts or chunk < 1.0:
            log.debug(f"place_order skip: contracts={contracts:.4f} < {_min_contracts}, chunk={chunk:.2f}")
            continue

        await _cancel_guard_wait(token_id)
        try:
            # [FIX CRITICAL-01] create_order è SINCRONO (ECDSA + nonce HTTP) — wrappato in executor
            _order_args = OrderArgs(token_id=token_id, price=round(price, 4), size=contracts, side=side)
            if CreateOrderOptions is not None:
                order = await _async_clob(client.create_order, _order_args, CreateOrderOptions(neg_risk=_neg_risk, tick_size=_tick_size), timeout=15.0)
            else:
                order = await _async_clob(client.create_order, _order_args, timeout=15.0)

            if order is None:
                log.warning(f"place_order: create_order returned None for {token_id} side={side} price={price:.4f}")
                continue

            resp = await _async_clob(client.post_order, order, _ORDER_TYPE_GTC)
            resp_str = str(resp) if resp else ""
            if isinstance(resp, dict):
                err_msg = str(resp.get("errorMsg", "") or "").strip()
                order_id  = str(resp.get("orderID") or resp.get("id") or "").strip()
                order_ok  = bool(order_id) and err_msg == ""
            else:
                order_ok, order_id, err_msg = resp is not None and resp_str != "", "", ""
            
            if order_ok:
                # [FIX CRITICAL-02] fonte unica di exposure: STATE (thread-safe), TOTAL_EXPOSURE rimosso
                STATE.add_exposure(contracts * price)
                record_fill_direction(token_id, side)
                if order_id: STATE.record_order(order_id, token_id, side, price, contracts, chunk, city, strategy)
            else:
                EVENTS.order_rejected(token_id, side, err_msg if isinstance(resp, dict) else resp_str[:200])
                
        except Exception as e:
            EVENTS.order_rejected(token_id, side, str(e))

async def get_live_inventory(client, token_id: str) -> float:
    try:
        pos = await _async_clob(client.get_position, token_id)
        if pos is not None:
            q = float(pos); _local_positions[token_id]=q; return q
    except Exception: pass
    try:
        for p in await get_positions_safe(client):
            if str(p.get("asset") or p.get("token_id") or "") == token_id:
                q=float(p.get("size") or 0); _local_positions[token_id]=q; return q
    except Exception: pass
    return _local_positions.get(token_id,0.0)

async def cancel_orders_for_token(client, token_id: str) -> int:
    global _last_cancel_ts
    if not client or not hasattr(client, 'get_orders'): return 0
    try: all_orders = await _async_clob(client.get_orders) or []
    except Exception: _last_cancel_ts[token_id] = time.time(); return 0
    
    to_cancel, cancel_notional = [], 0.0
    for o in all_orders:
        if str(o.get("asset_id") or o.get("token_id") or "") == token_id:
            o_id = str(o.get("id") or o.get("orderId") or "")
            if o_id:
                to_cancel.append(o_id)
                o_size = float(o.get("original_size") or o.get("size") or 0)
                cancel_notional += max(0.0, o_size - float(o.get("size_matched") or 0)) * float(o.get("price") or 0)
    
    if not to_cancel: return 0
    for o_id in to_cancel:
        try: await _async_clob(client.cancel, o_id)
        except Exception: pass
    
    deadline, confirmed = time.time() + CANCEL_CONFIRM_TIMEOUT, 0
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        try:
            if not [o for o in (await _async_clob(client.get_orders) or []) if str(o.get("asset_id") or o.get("token_id") or "") == token_id and str(o.get("id") or o.get("orderId") or "") in to_cancel]:
                confirmed = len(to_cancel)
                # [FIX CRITICAL-02] rimozione exposure via STATE
                STATE.remove_exposure(cancel_notional)
                break
        except Exception: break
    
    _last_cancel_ts[token_id] = time.time()
    if confirmed == 0 and to_cancel:
        remaining = max(0.0, CANCEL_MIN_WAIT - (time.time() - (_last_cancel_ts[token_id] - CANCEL_CONFIRM_TIMEOUT)))
        if remaining > 0: await asyncio.sleep(remaining)
    return confirmed

async def _cancel_guard_wait(token_id: str):
    last_cancel = _last_cancel_ts.get(token_id, 0.0)
    if last_cancel == 0.0: return
    elapsed = time.time() - last_cancel
    if elapsed < CANCEL_MIN_WAIT: await asyncio.sleep(CANCEL_MIN_WAIT - elapsed)

async def update_pnl_from_fills(client):
    global daily_pnl, _last_trade_ids
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if not funder: return
    try:
        new_trades = []
        for _page in range(5):
            params = {"user": funder, "limit": 50}
            if _page > 0 and new_trades:
                oldest_ts = new_trades[-1].get("timestamp") or new_trades[-1].get("ts") or ""
                if oldest_ts: params["before"] = oldest_ts
            resp = await _async_get("https://data-api.polymarket.com/activity", params=params, timeout=10)
            if resp is None or resp.status_code in (401, 403): return
            page_trades = resp.json()
            if not page_trades or not isinstance(page_trades, list): break
            all_known = True
            for t in page_trades:
                tid = str(t.get("id") or t.get("txHash") or "")
                if tid and tid not in _last_trade_ids:
                    all_known = False
                    new_trades.append(t)
            if all_known or len(page_trades) < 50: break

        # [FIX HIGH-02] FIFO pruning — elimina i 1000 più vecchi in ordine di inserimento
        if len(_last_trade_ids) > 2000:
            for _ in range(1000):
                try: _last_trade_ids.popitem(last=False)
                except KeyError: break
        if not new_trades: return

        for t in new_trades:
            tid = str(t.get("id") or t.get("txHash") or "")
            if not tid: continue
            _last_trade_ids[tid] = True  # [FIX HIGH-02] chiave in OrderedDict FIFO
            token_id_fill = str(t.get("asset_id") or t.get("token_id") or "")
            price, size = float(t.get("price") or 0), float(t.get("size")  or 0)
            side, outcome = str(t.get("side") or t.get("type") or "BUY").upper(), str(t.get("outcome") or t.get("resolution") or "").upper()

            if token_id_fill and price > 0 and size > 0:
                meta = STATE.get_order_meta(token_id_fill) if hasattr(STATE, 'get_order_meta') else {}
                MONITOR.record_trade(token_id_fill, meta.get("strategy", "MM"), meta.get("city", "london"), meta.get("bucket_key", "fill_confirmed"), side, price, size * price, meta.get("T", 1.0), meta.get("edge", 0.0))

            pnl_d = t.get("profit") or t.get("pnl") or t.get("realizedPnl")
            if pnl_d is not None: daily_pnl += float(pnl_d); continue
            if not price or not size: continue
            if outcome in ("YES","1","WIN"): daily_pnl += (1-price)*size
            elif outcome in ("NO","0","LOSE"): daily_pnl -= price*size
            if token_id_fill and outcome in ("YES","1","WIN","NO","0","LOSE"):
                MONITOR.record_resolution(token_id_fill, outcome in ("YES","1","WIN"))
    except Exception: pass

_last_capital_fetch = 0.0
_last_capital_value: Optional[float] = None

async def fetch_portfolio_capital(client, funder: str = "", force: bool = False) -> Optional[float]:
    global _last_capital_fetch, _last_capital_value
    now = time.time()
    if not force and now - _last_capital_fetch < 60.0: return _last_capital_value
    funder = funder or (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if not funder: return _last_capital_value
    _last_capital_fetch = now

    if client and CLOB_AVAILABLE:
        try:
            if hasattr(client, 'get_balance_allowance'):
                from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                bal_resp = await _async_clob(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                if bal_resp and isinstance(bal_resp, dict):
                    rc = float(bal_resp.get("balance", 0) or 0)
                    if rc > 1e6: rc /= 1e6
                    if rc > 0: _last_capital_value = rc; return rc
        except Exception: pass
    try:
        r = await _async_get(f"https://data-api.polymarket.com/value?user={funder}", timeout=8)
        if r is not None and r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0: data = data[0]
            if isinstance(data, dict) and data.get("value") is not None:
                rc = float(data.get("value"))
                if rc > 0: _last_capital_value = rc; return rc
    except Exception: pass
    return _last_capital_value

_last_redeem_check = 0.0
_redeemed_condition_ids: set = set()

async def auto_redeem_resolved():
    global _last_redeem_check
    now = time.time()
    if now - _last_redeem_check < 300.0: return
    _last_redeem_check = now
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if not funder: return

    try:
        r = await _async_get(f"https://data-api.polymarket.com/positions?user={funder}", timeout=10)
        if r is None or r.status_code != 200: return
        positions = r.json()
        if not isinstance(positions, list): return
        redeemable = [p for p in positions if p.get("redeemable", False)]
        if not redeemable: return

        to_redeem = []
        for p in redeemable:
            cid = ""
            for key in ("conditionId", "condition_id", "market_condition_id", "marketConditionId"):
                if p.get(key, ""): cid = str(p.get(key, "")); break
            if cid and cid not in _redeemed_condition_ids: to_redeem.append(cid)
        if not to_redeem: return

        cli_env = {**os.environ}
        for key in ("PM_PRIVATE_KEY", "PM_FUNDER_KEY", "PM_API_KEY", "PM_API_SECRET", "PM_API_PASSPHRASE"):
            if os.getenv(key, ""): cli_env[key] = os.getenv(key, "")

        _redeem_client = None
        if CLOB_AVAILABLE:
            try:
                from py_clob_client.client import ClobClient as _CC
                _pk2 = os.getenv("PM_PRIVATE_KEY", "").strip()
                if _pk2: _redeem_client = _CC("https://clob.polymarket.com", key=_pk2, chain_id=POLYGON, funder=RESOLVED_FUNDER or funder)
            except Exception: pass
        for cid in to_redeem:
            try:
                if _redeem_client and hasattr(_redeem_client, "redeem_positions"):
                    await _async_clob(_redeem_client.redeem_positions, condition_id=cid)
                    _redeemed_condition_ids.add(cid)
                else:
                    _cwd = os.path.expanduser("~/polymarket-temp-bot")
                    _cwd = _cwd if os.path.isdir(_cwd) else os.getcwd()
                    import functools as _ft
                    _run_fn = _ft.partial(subprocess.run, ["polymarket", "--signature-type", "eoa", "ctf", "redeem", "--condition", cid], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, cwd=_cwd, env=cli_env)
                    result = await asyncio.get_event_loop().run_in_executor(None, _run_fn)
                    if result.returncode == 0: _redeemed_condition_ids.add(cid)
            except Exception: pass
    except Exception: pass

async def get_best_bid_for_token(token_id: str, client):
    ws_entry = WS_BOOK.get(token_id)
    if ws_entry and time.time() - ws_entry.get("ts", 0) < 30:
        bids = ws_entry.get("bids", [])
        if bids:
            try: return float(bids[0][0])
            except Exception: pass
    if client:
        try:
            book = await _async_clob(client.get_order_book, token_id)
            if book and hasattr(book, 'bids') and book.bids: return float(book.bids[0].price)
        except Exception: pass
    return None

_last_position_exit_check = 0.0

async def check_position_exit(client):
    global _last_position_exit_check
    now = time.time()
    if now - _last_position_exit_check < 120.0: return
    _last_position_exit_check = now
    if not client and not DRY_RUN: return
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if not funder: return
    try:
        resp = await _async_get(f"https://data-api.polymarket.com/positions?user={funder}", timeout=10)
        if resp is None or resp.status_code != 200: return
        positions = resp.json()
        if not isinstance(positions, list): return
        markets_by_slug: dict = {}
        try:
            for _m in await fetch_markets_async():
                if _m.get("slug", ""): markets_by_slug[_m.get("slug", "")] = _m
        except Exception: pass
        for p in positions:
            if p.get("redeemable", False): continue
            pnl_pct, cur_value, size, cur_price = float(p.get("percentPnl", 0)), float(p.get("currentValue", 0)), float(p.get("size", 0)), float(p.get("curPrice", 0))
            if cur_value < 0.05 or cur_price < 0.005 or size < 0.5: continue
            
            token_id, title, slug, total_bought = str(p.get("asset", "")), p.get("title", "???"), p.get("slug", ""), float(p.get("totalBought", size))
            T_hours = MIN_T_HOURS + 1.0
            market_obj = markets_by_slug.get(slug)
            if market_obj: T_hours = hours_to_resolution(market_obj)
            else:
                _ev_slug = p.get("eventSlug", "")
                if _ev_slug:
                    for _mv in markets_by_slug.values():
                        if _mv.get("slug", "").startswith(_ev_slug): T_hours = hours_to_resolution(_mv); break
            city = "seoul" if "seoul" in title.lower() else "london"
            exit_fraction = 0.0
            
            if T_hours < MIN_T_HOURS and cur_price > 0.01: exit_fraction = 1.0
            elif pnl_pct >= 40.0: exit_fraction = 1.0
            elif pnl_pct >= 20.0 and T_hours > 6.0:
                if size >= total_bought * 0.55: exit_fraction = min(0.75, max(0.25, (pnl_pct / 100.0) / (1.0 + pnl_pct / 100.0)))
            if exit_fraction <= 0: continue
            
            exit_size = round(size * exit_fraction, 2)
            if exit_size < 1.0: continue
            sell_price = await get_best_bid_for_token(token_id, client)
            sell_price = max(0.01, round(sell_price - 0.001, 4)) if sell_price and sell_price > 0.01 else max(0.01, round(cur_price - 0.005, 4))
            
            if not DRY_RUN: await place_order(client, token_id, "SELL", sell_price, exit_size * sell_price, city=city, strategy="STOPLOSS")
    except Exception: pass

_last_stoploss_check = 0.0

async def check_position_stoploss(client):
    global _last_stoploss_check
    now = time.time()
    if now - _last_stoploss_check < 120.0: return
    _last_stoploss_check = now
    if DRY_RUN or not client: return
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if not funder: return

    try:
        r = await _async_get(f"https://data-api.polymarket.com/positions?user={funder}", timeout=10)
        if r is None or r.status_code != 200: return
        positions = r.json()
        if not isinstance(positions, list): return

        for p in positions:
            if p.get("redeemable", False): continue
            pnl_pct, cur_value, size, cur_price = float(p.get("percentPnl", 0)), float(p.get("currentValue", 0)), float(p.get("size", 0)), float(p.get("curPrice", 0))
            if cur_value < 0.10 or cur_price < 0.005 or size < 1: continue

            if pnl_pct < -(STOPLOSS_POSITION_PCT * 100):
                token_id, title = str(p.get("asset", "")), p.get("title", "???")
                clob_balance = 0.0
                try:
                    pos_clob = await _async_clob(client.get_position, token_id)
                    if pos_clob is not None: clob_balance = float(pos_clob)
                except Exception: clob_balance = size
                if clob_balance < 1.0 and clob_balance < size * 0.5: continue

                city = "seoul" if "seoul" in title.lower() else "london"
                sell_price = max(0.01, cur_price - 0.002)
                EVENTS.stoploss_triggered(token_id, title, pnl_pct, size, sell_price)
                await place_order(client, token_id, "SELL", sell_price, size * sell_price, city=city, strategy="STOPLOSS")
    except Exception: pass

_last_exposure_sync = 0.0
# [FIX MEDIUM-02] semaforo: max 1 subprocess CLI alla volta per evitare thread pool starvation
_SUBPROCESS_SEM: asyncio.Semaphore = None  # inizializzato in main_async dopo loop start

async def get_positions_safe(client) -> list:
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if funder:
        try:
            r = await _async_get(f"https://data-api.polymarket.com/positions?user={funder}", timeout=10)
            if r is not None and r.status_code == 200:
                data = r.json()
                if isinstance(data, list): return data
        except Exception: pass
    if client and hasattr(client, 'get_positions'):
        try: return await _async_clob(client.get_positions) or []
        except Exception: pass
    return []

async def sync_exposure_from_positions(client):
    global TOTAL_EXPOSURE, _last_exposure_sync
    now = time.time()
    if now - _last_exposure_sync < 60.0: return
    try:
        positions = await get_positions_safe(client)
        positions_exposure = sum(float(p.get("size") or p.get("quantity") or 0) * float(p.get("price") or p.get("avg_price") or p.get("curPrice") or 0) for p in positions)
        pending_exposure = 0.0
        funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
        
        try:
            if funder:
                headers = {"POLY_API_KEY": os.getenv("PM_API_KEY", "")} if os.getenv("PM_API_KEY", "") else {}
                if headers: headers.update({"POLY_API_SECRET": os.getenv("PM_API_SECRET", ""), "POLY_API_PASSPHRASE": os.getenv("PM_API_PASSPHRASE", "")})
                r_orders = await _async_get("https://clob.polymarket.com/orders", params={"maker_address": funder, "state": "LIVE"}, headers=headers, timeout=8)
                if r_orders is not None and r_orders.status_code == 200:
                    orders_data = r_orders.json()
                    for o in (orders_data.get("data", []) if isinstance(orders_data, dict) else orders_data if isinstance(orders_data, list) else []):
                        if str(o.get("side", "")).upper() == "BUY":
                            pending_exposure += max(0, float(o.get("original_size") or o.get("size") or 0) - float(o.get("size_matched") or 0)) * float(o.get("price") or 0)
        except Exception: pass
        
        if pending_exposure == 0:
            try:
                _cli_cwd = os.path.expanduser("~/polymarket-temp-bot")
                _cli_cwd = _cli_cwd if os.path.isdir(_cli_cwd) else os.getcwd()
                import functools as _ft2
                _cli_fn = _ft2.partial(subprocess.run, ["polymarket", "--signature-type", "eoa", "-o", "json", "clob", "orders"], capture_output=True, text=True, timeout=15, cwd=_cli_cwd, stdin=subprocess.DEVNULL)
                # [FIX MEDIUM-02] semaforo: evita subprocess sovrapposti che esauriscono il thread pool
                _sem = _SUBPROCESS_SEM
                async with (_sem if _sem is not None else _DummyCtx()):
                    cli_result = await asyncio.get_event_loop().run_in_executor(None, _cli_fn)
                if cli_result.returncode == 0 and cli_result.stdout.strip():
                    cli_orders = json.loads(cli_result.stdout)
                    for o in (cli_orders.get("data", []) if isinstance(cli_orders, dict) else cli_orders if isinstance(cli_orders, list) else []):
                        if str(o.get("side", "")).upper() == "BUY":
                            pending_exposure += float(o.get("original_size") or o.get("size") or 0) * float(o.get("price") or 0)
            except Exception: pass

        total_new = positions_exposure + pending_exposure
        old_exp = TOTAL_EXPOSURE
        # [FIX CRITICAL-02] sync sia la variabile legacy che STATE
        TOTAL_EXPOSURE = total_new
        STATE.set_total_exposure(total_new)
        _last_exposure_sync = now

        if abs(old_exp - total_new) > 0.5: EVENTS.exposure_sync(old_exp, total_new, len(positions))
        if positions: STATE.sync_positions_from_api(positions)
                
    except Exception: pass

async def check_kill_switch(client, capital: float):
    global daily_pnl, daily_reset_ts, kill_until, peak_capital, _daily_reset_capital
    now = time.time()
    STATE.set_capital(capital)

    if capital > peak_capital: peak_capital = capital

    if peak_capital > 0:
        dd = (peak_capital-capital)/peak_capital
        if dd >= MAX_PORTFOLIO_DD:
            EVENTS.kill_activated(f"DD={dd:.1%}", 24, capital, dd)
            try: await _async_clob(client.cancel_all_orders)
            except Exception: pass
            kill_until = now+86400; return

    if now - daily_reset_ts >= 86400:
        daily_pnl = 0.0
        daily_reset_ts = now
        _daily_reset_capital = capital
        _last_trade_ids.clear()
        STATE.daily_reset()

    await update_pnl_from_fills(client)

    # [FIX MEDIUM-01] se _daily_reset_capital == 0 (fetch capital fallito), usa daily_pnl diretto
    if _daily_reset_capital and _daily_reset_capital > 0:
        equity_daily_loss_usd = capital - _daily_reset_capital
        effective_daily_pnl   = min(daily_pnl, equity_daily_loss_usd)
        threshold             = DAILY_STOPLOSS * _daily_reset_capital
    else:
        log.warning("check_kill_switch: _daily_reset_capital=0, usando daily_pnl grezzo come fallback")
        effective_daily_pnl = daily_pnl
        threshold           = DAILY_STOPLOSS * capital  # worst-case: capitale attuale come base

    if effective_daily_pnl < threshold:
        EVENTS.kill_activated(f"daily_pnl={effective_daily_pnl:.4f}", 24, capital, 0)
        try: await _async_clob(client.cancel_all_orders)
        except Exception: pass
        kill_until = now+86400

# ══════════════════════════════════════════════════════════════════
# MAIN TRADING LOOP (async)
# ══════════════════════════════════════════════════════════════════
async def trading_loop(client, args, CAPITAL_ref: list):
    global kill_until, TOTAL_EXPOSURE

    if client: await sync_exposure_from_positions(client)

    _last_snapshot_ts = 0.0

    while True:
        try:
            _cycle_n = getattr(trading_loop, '_cycle_n', 0) + 1
            trading_loop._cycle_n = _cycle_n
            print(f"⏳  CYCLE #{_cycle_n} | capital=${CAPITAL_ref[0]:.2f} | exposure={STATE.total_exposure:.2f} | {time.strftime('%H:%M:%S')}", flush=True)
            now = time.time()
            
            if now < kill_until:
                remaining = int((kill_until - now) / 60)
                print(f"💤 Bot in KILL-SWITCH (Pausa di sicurezza). Riparte tra {remaining} minuti.", flush=True)
                await asyncio.sleep(60) 
                continue

            _old_cap = CAPITAL_ref[0]
            real_cap = await fetch_portfolio_capital(client)
            if real_cap is not None and real_cap > 0:
                CAPITAL_ref[0] = real_cap
                if abs(CAPITAL_ref[0] - _old_cap) > 0.10: EVENTS.capital_update(_old_cap, CAPITAL_ref[0], "api")
            CAPITAL = CAPITAL_ref[0]
            if DRY_RUN and CAPITAL < 5.0:
                _dry_cap = float(os.getenv("PM_CAPITAL_USDC") or 100.0)
                if _dry_cap < 5.0: _dry_cap = 100.0
                CAPITAL_ref[0] = _dry_cap
                CAPITAL = _dry_cap

            await sync_exposure_from_positions(client)
            await auto_redeem_resolved()
            
            _ws_now = time.time()
            # [FIX HIGH-05] lettura keys sotto lock
            with _WS_BOOK_LOCK:
                _stale_keys = [k for k, v in list(WS_BOOK.items()) if _ws_now - v.get("ts", 0) > 300]
            for _sk in _stale_keys:
                with _WS_BOOK_LOCK:
                    WS_BOOK.pop(_sk, None)
                # [FIX HIGH-03] cleanup FILL_DIRECTION per token stale
                FILL_DIRECTION.pop(_sk, None)
                # [FIX HIGH-04] cleanup GARCH_STATES per token stale
                GARCH_STATES.pop(_sk, None)
            
            await refresh_api_creds(client)
            await check_position_stoploss(client)
            await check_position_exit(client)
            await check_kill_switch(client, CAPITAL)
            
            if time.time() < kill_until: await asyncio.sleep(60); continue

            _ph0 = await asyncio.gather(
                fetch_markets_async(),
                fetch_weather_forecast_async("london"),
                fetch_weather_forecast_async("seoul"),
                return_exceptions=True
            )
            
            if not isinstance(_ph0[0], Exception):
                markets = _ph0[0]
                if not markets:
                    print(f"⚠  CYCLE #{_cycle_n}: nessun mercato attivo — retry in 60s", flush=True)
                    await asyncio.sleep(60); continue
                fc_london = _ph0[1] if not isinstance(_ph0[1], Exception) else {}
                fc_seoul  = _ph0[2] if not isinstance(_ph0[2], Exception) else {}
                print(f"   CYCLE #{_cycle_n}: {len(markets)} mercati | london_fc={'OK' if fc_london else 'ERR'} | seoul_fc={'OK' if fc_seoul else 'ERR'}")
            else:
                markets = None
                log.error(f"MARKETS FETCH ERROR: {_ph0[0]}")
                await asyncio.sleep(10); continue

            groups              = group_buckets_by_day(markets)
            arb_executed_groups = set()
            arb_cycle_spend = 0.0

            _orders_this_cycle   = 0
            _arb_blocked_by_kill = (time.time() < kill_until)

            city_exposure: dict = {"seoul": 0.0, "london": 0.0}
            for _tok, _pdata in PORTFOLIO_RISK.positions.items():
                _city = _pdata.get("city", "london")
                city_exposure[_city] = city_exposure.get(_city, 0.0) + float(_pdata.get("size", 0))

            for group_key, group_markets in groups.items():
                if _arb_blocked_by_kill: break
                books = {}
                for gm in group_markets:
                    tid = clean_token_id(gm.get("clobTokenIds") or gm.get("clob_token_ids") or "")
                    if not tid: continue
                    bids, asks = await get_book(tid, client, market_obj=gm)
                    if bids and asks:
                        bb, ba = bids[0][0], asks[0][0]
                        books[gm["id"]] = {
                            "mid":(bb+ba)/2, "best_bid":bb, "best_ask":ba,
                            "spread":ba-bb, "depth_usd":sum(b[1] for b in bids[:5]), "token_id":tid
                        }
                if len(books) < len(group_markets): continue

                prices    = [books[gm["id"]]["mid"]      for gm in group_markets]
                best_asks = [books[gm["id"]]["best_ask"] for gm in group_markets]
                mkt_ids   = [gm["id"]                    for gm in group_markets]
                token_ids = [books[gm["id"]]["token_id"] for gm in group_markets]

                mispricing, KL = bregman_projection(prices)
                corr_bonus = 0.0
                date_key_arb = "_".join(group_key.split("_")[1:])
                try:
                    nao = cross_city_arb_check(fc_london, fc_seoul, date_key_arb)
                    corr_bonus = nao["corr_edge"]
                    if nao["direction"] != "concordant": mispricing = min(mispricing+corr_bonus, 0.5)
                except Exception: pass

                _buckets_parsed = [parse_bucket(gm.get('question','')) for gm in group_markets]
                _n_exact = sum(1 for b in _buckets_parsed if b.get('type')=='exact')
                _cumulative_frac = 1.0 - (_n_exact / max(len(_buckets_parsed), 1))
                if _cumulative_frac > 0.5: continue

                if mispricing > MISPRICING_THRESHOLD:
                    city_k   = group_key.split("_")[0]
                    date_k   = "_".join(group_key.split("_")[1:])
                    fc       = fc_london if city_k=="london" else fc_seoul
                    fc_temp  = fc.get(date_k)

                    if fc_temp is None: continue

                    T_arb = (sum(hours_to_resolution(gm) for gm in group_markets) / len(group_markets) / 24.0)
                    _T_arb_adj = T_arb
                    _fc_cache_arb = FORECAST_CACHE.get(city_k, {})
                    _is_fallback_arb = bool(_fc_cache_arb.get("is_fallback"))
                    if _is_fallback_arb: _T_arb_adj = max(T_arb, 5.0)

                    total_true_p = sum(
                        ecmwf_true_prob(
                            fc_temp, parse_bucket(gm.get("question",""))["type"],
                            parse_bucket(gm.get("question",""))["threshold"],
                            T_days=_T_arb_adj, city=city_k, is_fallback=_is_fallback_arb
                        ) for gm in group_markets
                    )
                    _overlap_haircut = 0.10 + _cumulative_frac * 0.50
                    overlap_adjust   = max(0.0, total_true_p - 1.0) * _overlap_haircut
                    _avg_mid_arb = sum(prices)/max(len(prices),1)
                    _gls_e_arb, _ = get_gls_edge(_avg_mid_arb)
                    effective_edge  = max(0.0, mispricing - overlap_adjust + max(0.0, _gls_e_arb))

                    if effective_edge < 0.012: continue

                    avg_mid    = sum(prices)/len(prices)
                    avg_spread = sum(b["spread"] for b in books.values())/len(books)
                    depth_arb  = sum(b["depth_usd"] for b in books.values())/max(len(books),1)

                    if MONITOR.is_killed('ARB', city_k): continue
                    ks = kelly_and_mc(effective_edge*0.5, avg_mid, depth_arb, avg_spread, CAPITAL, T=T_arb, city=city_k)
                    if ks <= 0: continue

                    arb_orders, net_profit = integer_program_arb(prices, best_asks, mkt_ids, ks)
                    if not arb_orders: continue

                    safe = True
                    for mkt_id, size in arb_orders.items():
                        idx = mkt_ids.index(mkt_id)
                        b   = parse_bucket(group_markets[idx].get("question",""))
                        if not b.get("valid", False): continue
                        tp  = ecmwf_true_prob(fc_temp, b["type"], b["threshold"], T_days=_T_arb_adj, city=city_k)
                        if tp < 0.01: safe = False; break

                    if safe:
                        arb_order_cost = sum(max(0.01, best_asks[mkt_ids.index(mid_)] - ARB_MAKER_BUFFER) * sz_ for mid_, sz_ in arb_orders.items())
                        arb_cap = CAPITAL * NEAR_ARB_CAP_FRAC
                        if arb_cycle_spend + arb_order_cost > arb_cap: continue
                        if city_exposure.get(city_k, 0.0) + arb_order_cost > CAPITAL * CITY_CAP_FRAC: continue

                        for mkt_id, sz in arb_orders.items():
                            idx = mkt_ids.index(mkt_id)
                            tok = token_ids[idx]
                            tok_mid = prices[idx]
                            ask_p = max(0.01, best_asks[idx] - ARB_MAKER_BUFFER)
                            
                            if tok_mid > BECKER_BUY_ZONE_MAX or ask_p > BECKER_BUY_ZONE_MAX: continue
                            if _orders_this_cycle >= MAX_ORDERS_PER_CYCLE: break
                            await place_order(client, tok, "BUY", ask_p, sz, city=city_k, strategy="ARB")
                            _orders_this_cycle += 1
                        arb_cycle_spend += arb_order_cost
                        city_exposure[city_k] = city_exposure.get(city_k, 0.0) + arb_order_cost
                        arb_executed_groups.add(group_key)

            for _m in markets:
                _is_resolved = (_m.get("active") is False or _m.get("resolved") is True or str(_m.get("status","")).upper() in ("RESOLVED","CLOSED"))
                if not _is_resolved: continue
                _tid = clean_token_id(_m.get("clobTokenIds") or _m.get("clob_token_ids") or "")
                if not _tid: continue
                _outcome_raw = str(_m.get("outcome") or _m.get("resolution") or "").upper()
                _resolved_yes = _outcome_raw in ("YES","1","WIN","TRUE")
                if _outcome_raw: MONITOR.record_resolution(_tid, _resolved_yes)

            group_exposure: dict = {}

            for m in markets:
                tid = clean_token_id(m.get("clobTokenIds") or m.get("clob_token_ids") or "")
                if not tid: continue

                bids, asks = await get_book(tid, client, market_obj=m)
                if not bids or not asks: continue

                best_bid  = bids[0][0]
                best_ask  = asks[0][0]
                mid       = (best_bid+best_ask)/2
                spread    = best_ask-best_bid
                depth_usd = sum(b[1] for b in bids[:5])
                T_check = hours_to_resolution(m) / 24.0
                if spread>0.15 or depth_usd<MIN_DEPTH_USD: continue
                if T_check < (MIN_T_HOURS/24.0): continue

                if not (BECKER_BUY_ZONE_MIN <= mid <= BECKER_BUY_ZONE_MAX or BECKER_SELL_ZONE_MIN <= mid <= BECKER_SELL_ZONE_MAX):
                    continue

                q_text = m.get("question","")
                city   = "seoul" if "seoul" in q_text.lower() else "london"
                if MONITOR.is_killed('MM', city): continue
                bucket = parse_bucket(q_text)
                if not bucket.get("valid", False): continue

                fc            = fc_london if city=="london" else fc_seoul
                forecast_temp = None
                T             = T_check
                dm = re.search(r'(\d{4}-\d{2}-\d{2})', m.get("resolution_time","") or "")
                if dm: forecast_temp = fc.get(dm.group(1))

                if forecast_temp is not None:
                    _T_adj = T
                    _fc_cache = FORECAST_CACHE.get(city, {})
                    _is_fallback_mm = bool(_fc_cache.get("is_fallback"))
                    if _is_fallback_mm: _T_adj = max(T, 5.0)
                    true_p  = ecmwf_true_prob(forecast_temp, bucket["type"], bucket["threshold"], T_days=_T_adj, city=city, is_fallback=_is_fallback_mm)
                    _gls_edge, _ = get_gls_edge(mid)

                    directional_edge = true_p - mid

                    if mid < BECKER_BUY_ZONE_MAX: edge_fc = max(max(0.0, directional_edge), max(0.0, _gls_edge))
                    elif mid > BECKER_SELL_ZONE_MIN: edge_fc = max(max(0.0, -directional_edge), max(0.0, -_gls_edge))
                    else: edge_fc = 0.0

                    if edge_fc < MIN_FORECAST_EDGE: continue

                    fc_anom = true_p - mid
                    FACTOR_MODEL.update_factors(fc_anom if city=="seoul" else 0.0, fc_anom if city=="london" else 0.0, spread)

                q_inv     = await get_live_inventory(client, tid)
                bid_p, ask_p = avellaneda_stoikov_quote(mid, q_inv, spread, T, token_id=tid)

                edge_mm = max((ask_p-best_ask)+(best_bid-bid_p), 0.001)
                size    = kelly_and_mc(edge_mm, mid, depth_usd, spread, CAPITAL, T=T, token_id=tid, city=city)
                if size <= 0: continue

                if city_exposure.get(city, 0.0) + size > CAPITAL * CITY_CAP_FRAC:
                    _city_remaining = max(0.0, CAPITAL * CITY_CAP_FRAC - city_exposure.get(city, 0.0))
                    if _city_remaining < 1.0: continue
                    size = _city_remaining

                _date_str = dm.group(1) if dm else m.get('endDate', '')[:10]
                _group_k = f"{city}_{_date_str}"
                if group_exposure.get(_group_k, 0.0) + size > CAPITAL * GROUP_CAP_FRAC:
                    _remaining = max(0.0, CAPITAL * GROUP_CAP_FRAC - group_exposure.get(_group_k, 0.0))
                    if _remaining < 1.0: continue
                    size = _remaining
                group_exposure[_group_k] = group_exposure.get(_group_k, 0.0) + size
                city_exposure[city] = city_exposure.get(city, 0.0) + size

                if not args.dry_check:
                    await cancel_orders_for_token(client, tid)
                    _adv_mult = adverse_selection_spread_mult(tid)
                    _half_spread = (ask_p - bid_p) / 2 * _adv_mult
                    _mid_p = (bid_p + ask_p) / 2
                    bid_adj = max(0.01, round(_mid_p - _half_spread - UMA_BUFFER, 4))
                    ask_adj = min(0.99, round(_mid_p + _half_spread + UMA_BUFFER, 4))

                    if mid < BECKER_BUY_ZONE_MAX:
                        if _orders_this_cycle >= MAX_ORDERS_PER_CYCLE: continue
                        await place_order(client, tid, "BUY", bid_adj, size, city=city, strategy="MM")
                        _orders_this_cycle += 1
                    elif mid > BECKER_SELL_ZONE_MIN:
                        if _orders_this_cycle >= MAX_ORDERS_PER_CYCLE: continue
                        await place_order(client, tid, "SELL", ask_adj, size, city=city, strategy="MM")
                        _orders_this_cycle += 1

            if args.dry_check:
                print("\n✅ DRY-CHECK COMPLETE — nessun ordine piazzato.")
                break

            await MONITOR.run_cycle(CAPITAL)
            
            if time.time() - _last_snapshot_ts > 600:
                _last_snapshot_ts = time.time()
                EVENTS.snapshot(STATE.snapshot())
            
            await asyncio.sleep(LOOP_SLEEP)
            
        except asyncio.CancelledError:
            log.info("Trading loop cancellato.")
            break
        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            log.error(f"CRASH CICLO: {e}\n{err_msg}")
            print(f"🚨 ERRORE: {e} — Riavvio in 10s...", flush=True)
            await asyncio.sleep(10)

@dataclass
class TradeRecord:
    ts:         float
    token_id:   str
    strategy:   str
    city:       str
    bucket:     str
    side:       str
    price:      float
    size:       float
    T_days:     float
    edge_entry: float
    pnl:        float = 0.0
    resolved:   bool  = False

class SelfMonitor:
    PARAM_BOUNDS = {
        "MIN_FORECAST_EDGE":    (0.010, 0.040),
        "MISPRICING_THRESHOLD": (0.003, 0.015),
        "MIN_MAKER_SPREAD":     (0.005, 0.015),
        "MAX_DD":               (0.08,  0.20),
    }
    TUNE_STEP_MAX    = 0.10
    TUNE_INTERVAL    = 3600
    ANALYSIS_INTERVAL = 1800
    RELOAD_INTERVAL  = 60
    KILL_WINDOW      = 50
    ARB_KILL_WINDOW  = 20
    KILL_DURATION_MM = 7200
    KILL_DURATION_ARB = 14400
    CONFIG_FILE      = "config_override.json"
    STATE_FILE       = "monitor_state.json"

    def __init__(self):
        self.trades:    List[TradeRecord] = []
        self.killed:    Dict[str, float]  = {}
        self.last_tune: float = 0.0
        self.last_analysis: float = 0.0
        self.last_reload:   float = 0.0
        self.last_config_mtime: float = 0.0
        self.param_baseline = {
            "MIN_FORECAST_EDGE":    MIN_FORECAST_EDGE,
            "MISPRICING_THRESHOLD": MISPRICING_THRESHOLD,
            "MIN_MAKER_SPREAD":     MIN_MAKER_SPREAD,
            "MAX_DD":               MAX_DD,
        }
        self._load_state()

    def record_trade(self, token_id: str, strategy: str, city: str, bucket: str, side: str, price: float, size: float, T_days: float, edge: float):
        r = TradeRecord(ts=time.time(), token_id=token_id, strategy=strategy, city=city, bucket=bucket, side=side, price=price, size=size, T_days=T_days, edge_entry=edge)
        self.trades.append(r)
        if len(self.trades) > 500: self.trades = self.trades[-500:]

    def record_resolution(self, token_id: str, resolved_yes: bool):
        for r in self.trades:
            if r.token_id == token_id and not r.resolved:
                r.resolved = True
                if r.side == "BUY": r.pnl = ((1 - r.price) * r.size if resolved_yes else -r.price * r.size)
                else: r.pnl = (-(1 - r.price) * r.size if resolved_yes else r.price * r.size)

    def analyze(self) -> dict:
        resolved = [r for r in self.trades if r.resolved]
        if len(resolved) < 5: return {}

        groups: Dict[str, list] = defaultdict(list)
        for r in resolved: groups[f"{r.strategy}_{r.city}"].append(r)

        results = {}
        for key, recs in groups.items():
            pnls = np.array([r.pnl for r in recs])
            wins = sum(1 for r in recs if r.pnl > 0)
            results[key] = {
                "n":              len(recs),
                "total_pnl":      float(pnls.sum()),
                "win_rate":       wins / len(recs),
                "avg_pnl":        float(pnls.mean()),
                "sharpe":         (float(pnls.mean() / pnls.std()) if pnls.std() > 1e-9 else 0.0),
                "avg_edge_entry": float(np.mean([r.edge_entry for r in recs])),
                "avg_edge_real":  float(pnls.mean() / max(np.mean([r.size for r in recs]), 1)),
            }
        return results

    def _print_analysis(self, results: dict):
        if not results: return
        for key, m in sorted(results.items()):
            status = "✅" if m["sharpe"] > 0.3 else ("⚠️ " if m["sharpe"] > -0.3 else "🔴")
            print(f"  {status} {key:<18} n={m['n']:>3} pnl={m['total_pnl']:>+7.2f} wr={m['win_rate']:.0%} sharpe={m['sharpe']:>+.2f} edge_entry={m['avg_edge_entry']:.3f} edge_real={m['avg_edge_real']:>+.3f}")
        
    def is_killed(self, strategy: str, city: str) -> bool:
        key, key_global, now = f"{strategy}_{city}", f"{strategy}_all", time.time()
        return self.killed.get(key, 0) > now or self.killed.get(key_global, 0) > now

    def _check_kills(self, results: dict):
        now = time.time()
        for key in list(self.killed.keys()):
            if self.killed[key] <= now:
                del self.killed[key]
                strategy, city = key.split("_", 1)
                print(f"✅ AUTO-KILL REVOCATO: {strategy}/{city}")
        for key, m in results.items():
            strategy, city = key.split("_", 1)
            if strategy == "MM" and m["n"] >= self.KILL_WINDOW and m["sharpe"] < -0.5:
                self.killed[key] = now + self.KILL_DURATION_MM
                print(f"🔴 AUTO-KILL MM/{city}: sharpe={m['sharpe']:.2f} → pausa 2h")
            if strategy == "ARB" and m["n"] >= self.ARB_KILL_WINDOW and m["win_rate"] < 0.40:
                self.killed[f"ARB_all"] = now + self.KILL_DURATION_ARB
                print(f"🔴 AUTO-KILL ARB: win_rate={m['win_rate']:.0%} → pausa 4h")

    def _auto_tune(self, results: dict, capital: float):
        tuned = {}
        for key, m in results.items():
            if m["n"] < 10: continue
            strategy, edge_entry, edge_real = key.split("_")[0], m["avg_edge_entry"], m["avg_edge_real"]
            if edge_entry <= 0: continue
            ratio = edge_real / edge_entry
            param = "MIN_FORECAST_EDGE" if strategy == "MM" else "MISPRICING_THRESHOLD"
            current = getattr(sys.modules[__name__], param, None)
            if current is None: continue
            lo, hi = self.PARAM_BOUNDS[param]

            if ratio > 1.2: delta = -current * 0.05
            elif ratio < 0.6: delta = current * 0.08
            else: continue

            new_val = max(lo, min(hi, current + max(-current*self.TUNE_STEP_MAX, min(current*self.TUNE_STEP_MAX, delta))))
            if abs(new_val - current) > 1e-6: tuned[param] = (current, new_val, ratio)

        if tuned:
            print(f"\n🔧 AUTO-TUNE ({datetime.now().strftime('%H:%M')})")
            for param, (old, new, ratio) in tuned.items():
                print(f"   {'▼' if new < old else '▲'} {param}: {old:.4f} → {new:.4f}  (ratio={ratio:.2f})")
                globals()[param] = new
            print()

    def _hot_reload(self):
        try:
            if not os.path.exists(self.CONFIG_FILE): return
            mtime = os.path.getmtime(self.CONFIG_FILE)
            if mtime <= self.last_config_mtime: return
            self.last_config_mtime = mtime
            with open(self.CONFIG_FILE) as f: overrides = json.load(f)

            flat: dict = {}
            for key, val in overrides.items():
                if isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        if not isinstance(sub_val, dict): flat[sub_key] = sub_val
                else: flat[key] = val

            applied = []
            allowed = (set(self.PARAM_BOUNDS.keys()) | {
                "TAKE_PROFIT_PCT", "PARTIAL_PROFIT_PCT", "POSITION_EXIT_INTERVAL",
                "LOOP_SLEEP", "MC_PATHS", "MAX_PORTFOLIO_DD", "MIN_DEPTH_USD",
                "MAX_RUIN_PROB", "CANCEL_MIN_WAIT", "MAX_ORDERS_PER_CYCLE", "MAX_TOTAL_EXPOSURE_FRAC"
            })
            for param, value in flat.items():
                if param in allowed:
                    old = globals().get(param)
                    try: new_val = type(old)(value) if old is not None else value
                    except Exception: continue
                    globals()[param] = new_val
                    applied.append(f"{param}: {old} → {new_val}")

            if applied:
                print(f"🔄 HOT-RELOAD: {len(applied)} param aggiornati")
                for a in applied: print(f"   {a}")
        except Exception: pass

    def _save_state(self):
        try:
            state = {
                "killed": {k: v for k, v in self.killed.items()},
                "trades_summary": {"n_total": len(self.trades), "n_resolved": sum(1 for r in self.trades if r.resolved), "total_pnl": sum(r.pnl for r in self.trades if r.resolved)},
                "params": {k: globals().get(k) for k in self.PARAM_BOUNDS},
                "ts": time.time(),
            }
            with open(self.STATE_FILE, "w") as f: json.dump(state, f, indent=2)
        except Exception: pass

    def _load_state(self):
        try:
            if not os.path.exists(self.STATE_FILE): return
            with open(self.STATE_FILE) as f: state = json.load(f)
            now = time.time()
            for k, v in state.get("killed", {}).items():
                if v > now: self.killed[k] = v
            for param, val in state.get("params", {}).items():
                if val is not None and param in globals(): globals()[param] = val
        except Exception: pass

    async def run_cycle(self, capital: float):
        now = time.time()
        if now - self.last_reload > self.RELOAD_INTERVAL: self._hot_reload(); self.last_reload = now
        if now - self.last_analysis > self.ANALYSIS_INTERVAL:
            results = self.analyze()
            self._print_analysis(results); self._check_kills(results)
            self.last_analysis = now
        if now - self.last_tune > self.TUNE_INTERVAL:
            results = self.analyze()
            if results: self._auto_tune(results, capital)
            self.last_tune = now; self._save_state()

MONITOR = SelfMonitor()
FILL_DIRECTION: Dict[str, deque] = {}

def record_fill_direction(token_id: str, side: str):
    if token_id not in FILL_DIRECTION: FILL_DIRECTION[token_id] = deque(maxlen=20)
    FILL_DIRECTION[token_id].append(1 if side=='BUY' else 0)

def adverse_selection_spread_mult(token_id: str) -> float:
    fills = FILL_DIRECTION.get(token_id)
    if not fills or len(fills) < 5: return 1.0
    ratio = sum(fills) / len(fills)
    if ratio > 0.75 or ratio < 0.25: return 1.5
    return 1.0

class _DummyCtx:
    """Async context manager no-op — usato quando il semaforo non è ancora inizializzato."""
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass

async def main_async(args):
    global peak_capital, DRY_RUN, _daily_reset_capital, _SUBPROCESS_SEM
    _SUBPROCESS_SEM = asyncio.Semaphore(1)  # [FIX MEDIUM-02] init dopo loop creation
    print(f"🚨 DRY_RUN={DRY_RUN} | TempBot v4.24 STABLE (Becker Quant Edition)\n")

    if not CLOB_AVAILABLE and not DRY_RUN: raise RuntimeError("py_clob_client non installato — usa DRY_RUN=true")
    pk = os.getenv("PM_PRIVATE_KEY")
    if not pk and not DRY_RUN: raise RuntimeError("PM_PRIVATE_KEY mancante")

    CAPITAL_ref  = [float(os.getenv("PM_CAPITAL_USDC") or 1000)]
    peak_capital = CAPITAL_ref[0]
    _daily_reset_capital = CAPITAL_ref[0]

    global RESOLVED_FUNDER
    _eoa_address = ""
    if pk:
        try:
            from eth_account import Account as _EthAccount
            _eoa_address = _EthAccount.from_key(pk).address
        except Exception: pass
    _funder_env = os.getenv("PM_FUNDER_KEY", "").strip()
    RESOLVED_FUNDER = _funder_env if (_funder_env and _eoa_address and _funder_env.lower() != _eoa_address.lower()) else (_eoa_address or _funder_env)

    global _last_redeem_check
    _last_redeem_check = time.time()  # [STABLE] skip primo redeem check all'avvio

    client = None
    if CLOB_AVAILABLE and pk:
        client = ClobClient("https://clob.polymarket.com", key=pk, chain_id=POLYGON, funder=RESOLVED_FUNDER)
        client.set_api_creds(ApiCreds(api_key=os.getenv("PM_API_KEY"), api_secret=os.getenv("PM_API_SECRET"), api_passphrase=os.getenv("PM_API_PASSPHRASE")))

    real_cap = None
    for _retry in range(3):
        real_cap = await fetch_portfolio_capital(client, force=True)
        if real_cap is not None: break
        wait = 2 ** (_retry + 1)
        print(f"   ⚠️  Capital fetch tentativo {_retry+1}/3 fallito — retry in {wait}s...")
        await asyncio.sleep(wait)

    if real_cap is not None and real_cap >= 5.0:
        CAPITAL_ref[0] = real_cap
        peak_capital   = real_cap
        print(f"   💰 Capitale API: ${real_cap:.2f}")
    elif real_cap is not None and real_cap < 5.0:
        if DRY_RUN:
            CAPITAL_ref[0] = real_cap
            peak_capital   = real_cap
            print(f"   💰 Capitale API: ${real_cap:.2f} (dry-run)")
        else:
            raise SystemExit("Capitale insufficiente per operare in sicurezza.")
    else:
        env_cap = float(os.getenv("PM_CAPITAL_USDC") or 0)
        if env_cap >= 5.0 and not args.dry_check:
            DRY_RUN = True
            CAPITAL_ref[0] = env_cap
            peak_capital   = env_cap
            print(f"   ⚠️  DEGRADED MODE: API capitale irraggiungibile dopo 3 retry. DRY_RUN forzato a True.")
        else:
            raise SystemExit("Capital fetch fallito e nessun fallback env disponibile.")

    if DRY_RUN and CAPITAL_ref[0] < 5.0:
        _dry_cap = float(os.getenv("PM_CAPITAL_USDC") or 100.0)
        CAPITAL_ref[0] = peak_capital = _dry_cap

    print(f"🚀 TEMPBOT v4.24 STABLE (Becker Quant Edition) — Seoul/London")
    print(f"   Capital: ${CAPITAL_ref[0]:.0f} | MinDepth: ${MIN_DEPTH_USD:.0f} | MaxDD: {MAX_PORTFOLIO_DD:.0%} | MaxExp: {MAX_TOTAL_EXPOSURE_FRAC:.0%}")

    global GLS_CALIBRATION
    GLS_CALIBRATION = load_becker_gls()

    markets = await fetch_markets_async()
    token_ids = [t for t in [clean_token_id(m.get("clobTokenIds") or m.get("clob_token_ids") or "") for m in markets] if t]

    if client and CLOB_AVAILABLE:
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            NEG_RISK_ADAPTER = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
            bal_resp = await _async_clob(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            if bal_resp and isinstance(bal_resp, dict):
                allowances = bal_resp.get("allowances", {}) or {}
                if int(allowances.get(NEG_RISK_ADAPTER, 0) or 0) < 10**6:
                    print(f"   ⚠️  ALLOWANCE ALERT: NegRisk Adapter ha allowance bassa — SELL potrebbero fallire.")
        except Exception: pass

    if args.dry_check:
        print(f"\n✅ DRY-CHECK — {len(markets)} mercati trovati.")
        return

    import threading
    def ws_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try: loop.run_until_complete(ws_orderbook_listener(token_ids))
        except Exception: pass
        finally: loop.close()

    t = threading.Thread(target=ws_thread, daemon=True, name="ws-listener")
    t.start()

    await trading_loop(client, args, CAPITAL_ref)

def main():
    parser = argparse.ArgumentParser(description="TempBot v4.24 STABLE")
    parser.add_argument("--dry-check", action="store_true", help="Fetch mercati, mostra ARB/forecast. Nessun ordine.")
    args = parser.parse_args()
    try: asyncio.run(main_async(args))
    except KeyboardInterrupt: print("\n⏹ TempBot fermato.")

if __name__ == "__main__":
    main()