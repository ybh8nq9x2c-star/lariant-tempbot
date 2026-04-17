#!/usr/bin/env python3
import concurrent.futures as _cf_pool
_exec_pool = _cf_pool.ThreadPoolExecutor(  # [H1-FIX] single pool, right-sized
    max_workers=min(16, (__import__("os").cpu_count() or 1) * 4))  # [H1-FIX]
import asyncio as _asyncio_early
import warnings as _warnings_early
# [FIX-D] Suppress cosmetic DeprecationWarning from asyncio.get_event_loop() at module
# load time (Python 3.10+). This is NOT a crash cause — it fires because no event loop
# exists yet at import time. Suppressed here so operators don't confuse it with real errors.
_warnings_early.filterwarnings(
    "ignore", "There is no current event loop", DeprecationWarning
)
try:
    _asyncio_early.get_event_loop().set_default_executor(_exec_pool)
except (RuntimeError, DeprecationWarning):
    pass
import requests as _req_patch
import requests.adapters as _req_adap
_orig_send = _req_adap.HTTPAdapter.send
def _forced_send(self, req, **kw):
    # [v4.24-force-timeout] Force 5s timeout on ALL requests, cannot be bypassed
    kw["timeout"] = 5.0
    return _orig_send(self, req, **kw)
_req_adap.HTTPAdapter.send = _forced_send  # [v4.24-req-timeout]

import os as _os
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))
_SHADOW_SUFFIX = "_shadow" if "shadow" in _BASE_DIR else ""
_V5_BOOK_PATH = _os.path.join(_BASE_DIR, f"v5_book_data{_SHADOW_SUFFIX}.jsonl")
_ML_MODEL_PATH = _os.path.join(_BASE_DIR, "modello_final_lightgbm.joblib")


"""
Polymarket Temperature Bot — Seoul / London LIVE  v4.24 STABLE (Becker Quant Edition)
═══════════════════════════════════════════════════════════════════

CHANGELOG v4.24 (3 bug confermati matematicamente, audit quant):
[v4.24-fix-killswitch-units] Kill-switch: equity_loss era ratio (-0.03), threshold era USD (-$2.02)
                             → min(USD, ratio) sempre ignorava equity path → floating losses invisibili
                             Fix: equity_daily_loss_usd = capital - _daily_reset_capital (entrambi USD)
[v4.24-fix-exposure-leak]    TOTAL_EXPOSURE += era PRIMA di order_ok → ordini rifiutati gonfiavano exposure
                             cancel_orders_for_token non decrementava → bot paralizzato dopo ~24s in MM
                             Fix: += solo su order_ok confermato + decremento in cancel con notional reale
[v4.24-fix-sigma-scaling]    ecmwf_true_prob: sigma lineare (2.2 + 0.20*T) sovrastimava 4x a T<1d
                             → a T=6h bot calcolava SELL su contratti che dovevano essere BUY
                             Fix: sigma = base * sqrt(max(T, 0.04) / 7.0) (sub-lineare, calibrata ECMWF)

CHANGELOG v4.23 (Pre-Live Audit Fixes):
[v4.23-fix-exposure] Spostato l'aggiornamento dell'exposure DOPO la conferma order_ok
[v4.23-fix-lock]     Aggiunto cancel_orders_for_token prima di SELL in check_position_exit e stoploss
[v4.23-fix-dust]     Risolto bug frazioni dust in place_order (round a 6 decimali pre-floor)
[v4.23-fix-min-size] Rimosso boost a 1$ se la size Kelly calcolata è < 1$ (evita capital bleed)
[v4.23-fix-ws]       Sostituito del con .pop() in get_book per prevenire KeyError

CHANGELOG v4.22:
[v4.22-fix-exposure]   Optimistic TOTAL_EXPOSURE update in place_order (prevents 60s overshoot window)
[v4.22-fix-as-floor]   T floor = max(T, 0.04) in A-S spread — prevents spread collapse near expiry
[v4.22-fix-killswitch] Kill-switch now uses min(realized_pnl, equity_drawdown) — catches floating losses
[v4.22-fix-ticksize]   Dynamic tick_size truncation for SELL/BUY orders — fixes neg_risk silent rejects

CHANGELOG v4.19:
[v4.19-fix-funder]  EOA funder resolution: derived from private key
[v4.19-exit]        Smart position exit strategy (Kelly partial + take-profit + time exit)
CHANGELOG v4.17:
[v4.17-fix-ver]  Version strings aligned to v4.17 across docstring, prints, User-Agent, argparse
[v4.17-fix-neg]  neg_risk fallback made configurable via NEG_RISK_FALLBACK env var
[v4.17-fix-pnl]  update_pnl_from_fills converted to async, awaited at call-site
CHANGELOG v4.14 (5 fix da review):
[v4.14-C1] neg_risk: ora legge il campo reale (3 stati: True/False/mancante+WARNING); rimosso forced=True
[v4.14-C2] hot-reload: flatten automatico di override annidati; whitelist estesa; log dettagliato
[v4.14-C3] capital init: retry 3x con backoff (2s/4s/8s); fallback su PM_CAPITAL_USDC con DRY_RUN forzato
[v4.14-M1] ecmwf_true_prob: parametro is_fallback → sigma+1.5°C per forecast climatologico
[v4.14-M2] update_pnl_from_fills: paginazione con cursore (max 5 pagine); cap _last_trade_ids a 2000
[v4.13-IL3] place_order e check_position_stoploss convertiti in async; _cancel_guard_wait usa await asyncio.sleep (no più time.sleep bloccante)
[v4.13-IL2] integer_program_arb: objective LP usa best_asks (prezzo esecuzione reale) invece dei mid
[v4.13-IL4] Rimossa riga log.info PnL duplicata in update_pnl_from_fills
[v4.13-IL1] Rimosso global TOTAL_EXPOSURE da kelly_and_mc; rimosso TOTAL_EXPOSURE=0.0 dal daily reset
[v4.13-DC1] Rimosso ramo irraggiungibile if s==0 in bregman_projection
[v4.13-DC2] Rimossa _event_matches() mai chiamata
[v4.13-DC3] Rimossa _market_matches() mai chiamata (wrapper inutile)
[v4.13-DC4] Rimosso _token_exposure dict mai scritto; rimosso pop() sempre-zero nel blocco risoluzione
[v4.13-DC5] Rimossi ORDER_TYPE_FOK e ORDER_TYPE_FAK da import (mai usati)
[v4.13-DC6] Rimosso OrderStatus da import portfolio_state (mai usato)
[v4.13-IL6] Aggiunto timedelta all'import globale; rimosso __import__('datetime') ridondante
[v4.13-IL7] Rimosso import locale timedelta dentro fetch_weather_forecast_async
[v4.13-IL8] Rimosso import sys locale in _auto_tune; usa sys globale
[v4.13-QC4] Rimosso import asyncio locale in fetch_markets_async; usa asyncio globale
[v4.13-IL9] FILL_DIRECTION annotato come Dict[str, deque] (non Dict[str, object])
[v4.13-IL10] Versione aggiornata da v4.6 a v4.13 in docstring, print, argparse
[v4.13-QC1] Bare except specializzati in 9 punti critici
[v4.13-QC6] MAX_ORDERS_PER_CYCLE spostato da costante locale del loop a costante globale

CHANGELOG v4.5 (audit completo — tutti 7 fix):
CHANGELOG v4.6 (hardening finale):
[v4.6-fix1] Exposure reservation coerente in kelly_and_mc; eliminato il riuso della stessa headroom intra-cycle
[v4.6-fix2] Kelly full cap per ridurre oversizing con sigma sottostimata
[v4.6-fix3] Skip dei bucket non validi in MM/ARB
[v4.6-fix4] place_order non aggiorna più TOTAL_EXPOSURE direttamente (no double count)

  [v4.5-fix5]  gls_edge usata come segnale additivo in MM e ARB
  [v4.5-fix6]  kelly_fraction_dynamic monotonica: 30-50¢ → base=0.35
  [v4.5-fix7]  FactorModel seedato con 10 prior osservazioni (no eye*0.001)
  [v4.5-fix7b] Seoul β_f2=0.35, London β_f2=0.60 (NAO diverso)
  [v4.5-fix1b] record_resolution chiamata anche nel market loop

CHANGELOG v4.4 (SelfMonitor + exposure + city fixes):
  [v4.4-fix1] record_resolution() chiamata da update_pnl_from_fills → SM operativo
  [v4.4-fix2] record_fill_direction() chiamata da place_order → adverse sel attivo
  [v4.4-fix3] TOTAL_EXPOSURE si decrementa quando posizione risolve (intraday)
  [v4.4-fix4] place_order(city=) → PORTFOLIO_RISK.update_position con città corretta

CHANGELOG v4.2 (quant PM audit — 5 fix):
  [v4.2-C2] CITY_SIGMA_BASE: Seoul 2.8°C, London 2.2°C (RMSE reale ECMWF)
  [v4.2-C3] ARB cumulative guard: skip se >50% bucket or_higher/or_lower
  [v4.2-H1] Near-resolution VaR bimodale (T<0.5d)
  [v4.2-H2] Adverse selection guard: spread x1.5 se BUY_ratio>75%
  [v4.2-H3] Per-group cap 25% per city_date

CHANGELOG v4.1 (audit esterno — 5 fix critici):
  [v4.1-C1] ARB logic riparata: overlap_adjust + effective_edge haircut
             Era: fake arb su bucket cumulativi (ruin vector #1)
  [v4.1-C2] MC drawdown: peak-to-trough reale, non price range
             Era: (max-min) paths → mai triggera ruin guard
  [v4.1-C3] Global exposure cap: TOTAL_EXPOSURE <= 75% capital
             Era: PortfolioVaR ottimistico, no hard cap
  [v4.1-H1] GLS fallback robusto: conservativo se file mancante
             Era: dict vuoto → gls_confidence sempre 1.0
  [v4.1-H2] WebSocket subscribe format corretto
             Era: payload sbagliato → WS_BOOK sempre vuoto
  [v4.1-H3] ecmwf_true_prob sigma dinamico: 1.8 + 0.25*T_days
             Era: sigma=1.5 hardcoded → edge sovrastimato

CHANGELOG v4.0 (feature aggiunte):
  [WS]   WebSocket orderbook real-time
  [PAR]  Parallel API calls (aiohttp async)
  [GARCH] Spread adattivo post-shock GARCH(1,1)
  [FAC]  Factor Model F1/F2/F3 (covarianza Seoul/London)
  [GLS]  GLS calibration pesi 1/σ²(T) su Becker
  [PVAR] Portfolio VaR parametrico + MC

CHANGELOG v3 (CAO audit — 16 fix):
  [CRITICAL-1] MM loop scope separato da ARB
  [CRITICAL-2] time_edge_mult(T) senza *365
  [CRITICAL-3] PnL tracking reale (non cash flow)
  [CRITICAL-4] CAPITAL aggiornato da API ogni loop
  [HIGH-1]     percentile(95) non 5
  [HIGH-2]     Guard edge<=0 in kelly_fraction_dynamic
  [HIGH-3]     LP objective senza /n
  [HIGH-4]     MIN_DEPTH_USD=100
  [MEDIUM-1]   Sigma cap duplicato rimosso
  [MEDIUM-2]   Bucket check duplicato rimosso
  [MEDIUM-3]   forecast_days=7
  [MEDIUM-4]   NAO_CORR stagionale
  [LOW-1..4]   Log, unused const, sleep bloccante, print@module

DIPENDENZE:
  pip install aiohttp websockets numpy scipy pulp python-dotenv requests
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
from scipy.stats  import norm, skewnorm

# [v4.6-audit] State management & event log
sys.path.insert(0, str(Path(__file__).parent))
_BOT_DIR = Path(__file__).parent.resolve()  # [TGT3] Dynamic base dir for CLI subprocesses
from portfolio_state import PortfolioState
from event_log import EventLog
import family_risk  # [FAMILY] family-level risk gate

# [v4.32-ML] LightGBM edge weighting — optional, graceful fallback
try:
    import joblib as _joblib
    _warnings_early.filterwarnings("ignore", category=UserWarning, module="sklearn")  # [v4.32-ML] silence sklearn feature-name noise
    _ML_MODEL = _joblib.load(_ML_MODEL_PATH)
    print(f"[ML] Modello caricato OK — {_ML_MODEL.n_features_in_} feature")
except Exception as _ml_err:
    _ML_MODEL = None
    print(f"[ML] Modello non disponibile (fallback edge raw): {_ml_err}")
def ml_weight_edge(edge: float, mid: float, city: str, T: float) -> float:
    """[v4.32-ML] Pesa l'edge con P(win) del modello. Fallback = edge originale."""
    if _ML_MODEL is None or edge <= 0:
        return edge
    try:
        city_enc = 1.0 if city == 'seoul' else 0.0
        feats = [[
            edge,
            abs(edge),
            mid,
            city_enc,
            T,
            edge / max(mid, 0.01),
            city_enc * edge,
            1.0 / max(mid, 0.01),
        ]]
        ml_proba = float(_ML_MODEL.predict_proba(feats)[0][1])
        weighted = edge * (0.5 + 0.5 * ml_proba)
        return weighted
    except Exception as _e:
        return edge  # [v4.32-ML] fallback silenzioso

# ── [CB-v1] Circuit Breaker — globals ────────────────────────────────────────
from collections import deque as _deque
_edge_decay_window: dict = {"LONDON": _deque(maxlen=10), "SEOUL": _deque(maxlen=10)}
_latency_fail_count: int   = 0
_latency_blocked:    bool  = False
_consecutive_losses: int   = 0
_cb_loss_blocked_until: float = 0.0

def _edge_decay_guard(city: str = "LONDON") -> bool:
    """[CB-v1-fix] Blocca se ultimi 5 fill hanno edge negativo PER CITTA."""
    city_key = "LONDON" if "lon" in city.lower() else "SEOUL"
    w = _edge_decay_window.get(city_key)
    if w is None or len(w) < 5:
        return True
    if all(e < 0 for e in list(w)[-5:]):
        log.warning(f"[CB-EDGE] 5 edge negativi consecutivi ({city_key}) — skip MM")
        return False
    return True

def _consecutive_loss_guard() -> bool:
    """[CB-v1-fix] Blocca 1h dopo 4 SL; reset automatico a scadenza."""
    global _consecutive_losses
    if time.time() < _cb_loss_blocked_until:
        remaining = int(_cb_loss_blocked_until - time.time()) // 60
        log.debug(f"[CB-LOSS] Blocco attivo — {remaining}min rimasti")
        return False
    if _consecutive_losses >= 4:  # [CB-v1-fix] blocco appena scaduto → reset
        log.info(f"[CB-LOSS] Blocco scaduto — reset counter ({_consecutive_losses} → 0)")
        _consecutive_losses = 0
    return True

async def _latency_guard_loop(client):
    """[CB-v1] Monitora latenza CLOB ogni 60s; blocca MM se 3 fail consecutivi."""
    global _latency_fail_count, _latency_blocked
    while True:
        await asyncio.sleep(60)
        try:
            t0 = time.monotonic()
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, lambda: client.get_markets(next_cursor="")),
                timeout=2.0
            )
            latency_ms = (time.monotonic() - t0) * 1000
            if latency_ms > 500:
                _latency_fail_count += 1
                log.warning(f"[CB-LAT] Alta latenza {latency_ms:.0f}ms (#{_latency_fail_count})")
            else:
                _latency_fail_count = 0
                _latency_blocked = False
        except Exception as _cb_e:
            _latency_fail_count += 1
            log.warning(f"[CB-LAT] Ping fallito: {_cb_e}")
        if _latency_fail_count >= 3:
            _latency_blocked = True
            log.error("[CB-LAT] LatencyGuard ATTIVO — 3 fail consecutivi")
        else:
            _latency_blocked = False

# ── Global state instances (single source of truth) ──────────
STATE  = PortfolioState()
EVENTS = EventLog()
# [v4.15-fix10] Flush stdout globale: docker/cron usano stdout non-TTY
print = functools.partial(print, flush=True)

# [v4.15-fix9] Helper per eseguire requests.get in executor senza bloccare asyncio
def _sync_get(url, params=None, headers=None, timeout=10):
    """Wrapper sincrono — usare con await loop.run_in_executor(None, _sync_get, ...)"""
    return requests.get(url, params=params, headers=headers, timeout=timeout)

async def _async_get(url, params=None, headers=None, timeout=10):
    """Esegue requests.get in thread pool senza bloccare il loop asyncio. [v4.15-fix9]"""
    loop = asyncio.get_running_loop()  # [TGT2]
    import functools as _ft
    fn = _ft.partial(requests.get, url, params=params, headers=headers, timeout=timeout)
    return await loop.run_in_executor(None, fn)

# ── Config loader ────────────────────────────────────────────
async def _async_clob(func, *args, timeout=10.0, **kwargs):
    """Wrappa chiamate CLOB sincrone in executor con timeout."""
    if func is None: return None
    loop = asyncio.get_running_loop()  # [TGT2]
    pfunc = functools.partial(func, *args, **kwargs)
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, pfunc), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning(f"CLOB TIMEOUT: {func.__name__} > {timeout}s")
        return None
    except RuntimeError as e:
        if "cannot schedule new futures after shutdown" in str(e):
            # [HARDENING] Executor died — reinit silently and return None for this call
            try:
                import concurrent.futures as _cf_hard2
                global _exec_pool
                _exec_pool = _cf_hard2.ThreadPoolExecutor(
                    max_workers=min(16, (__import__("os").cpu_count() or 1) * 4))
                loop.set_default_executor(_exec_pool)
                log.warning(f"CLOB EXECUTOR REINITIALIZED via {func.__name__}")
            except Exception:
                pass
        log.warning(f"CLOB ERROR {func.__name__}: {e}")
        return None
    except Exception as e:
        log.warning(f"CLOB ERROR {func.__name__}: {e}")
        return None

def _load_config() -> dict:
    """Carica config da JSON con fallback ai defaults hardcoded."""
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
    """Legge parametro da config.json con fallback al default."""
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
    # [v4.12-S1] CreateOrderOptions porta neg_risk e tick_size alla firma EIP-712.
    # [v4.12-S2] OrderType.GTC è necessario per post_order — senza di esso il client
    # usa il default interno che può cambiare tra versioni.
    try:
        from py_clob_client.clob_types import CreateOrderOptions
    except ImportError:
        CreateOrderOptions = None  # versioni vecchie: non disponibile
    try:
        from py_clob_client.order_builder.constants import ORDER_TYPE_GTC
        _ORDER_TYPE_GTC = ORDER_TYPE_GTC
    except ImportError:
        try:
            from py_clob_client.constants import OrderType
            _ORDER_TYPE_GTC = OrderType.GTC
        except ImportError:
            _ORDER_TYPE_GTC = "GTC"  # fallback stringa
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    CreateOrderOptions = None
    _ORDER_TYPE_GTC = "GTC"
    logging.warning("py_clob_client non disponibile — solo dry-run")

load_dotenv()
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ══════════════════════════════════════════════════════════════════
# CONSTANTS (loaded from config.json with hardcoded fallbacks)
# ══════════════════════════════════════════════════════════════════
MIN_MAKER_SPREAD     = cfg("trading", "MIN_MAKER_SPREAD", 0.0077)
MAX_MAKER_SPREAD     = cfg("trading", "MAX_MAKER_SPREAD", 0.0125)
GAMMA                = cfg("trading", "GAMMA", 0.1)
K_INTENSITY          = cfg("trading", "K_INTENSITY", 1.5)
MAX_RUIN_PROB        = cfg("risk", "MAX_RUIN_PROB", 0.01)
MAX_DD               = cfg("risk", "MAX_DD", 0.15)
MAX_PORTFOLIO_DD     = cfg("risk", "MAX_PORTFOLIO_DD", 0.40)
MC_PATHS             = cfg("risk", "MC_PATHS", 10_000)
MIN_DEPTH_USD        = cfg("trading", "MIN_DEPTH_USD", 100.0)
UMA_BUFFER           = cfg("trading", "UMA_BUFFER", 0.004)
ARB_MAKER_BUFFER     = cfg("trading", "ARB_MAKER_BUFFER", 0.012)
SLIPPAGE_GUARD       = cfg("trading", "SLIPPAGE_GUARD", 0.008)
MISPRICING_THRESHOLD = cfg("trading", "MISPRICING_THRESHOLD", 0.005)
MIN_FORECAST_EDGE    = cfg("trading", "MIN_FORECAST_EDGE", 0.015)
DAILY_STOPLOSS       = cfg("risk", "DAILY_STOPLOSS", -0.15)  # [BECKER] generous room
KELLY_FULL_CAP       = cfg("risk", "KELLY_FULL_CAP", 0.5)   # [BECKER] tamed variance
NEAR_ARB_CAP_FRAC    = cfg("risk", "NEAR_ARB_CAP_FRAC", 0.10)
CITY_CAP_FRAC        = cfg("risk", "CITY_CAP_FRAC", 0.40)
MIN_T_HOURS          = cfg("risk", "MIN_T_HOURS", 3)
STOPLOSS_POSITION_PCT = cfg("risk", "STOPLOSS_POSITION_PCT", 0.95)  # [BECKER] disabled
STOPLOSS_SHADOW_MODE  = True   # [PHASE3] True = log-only, no SELL execution
# [v4.27] Anti-overtrading guards
REENTRY_COOLDOWN_SEC    = cfg("risk", "REENTRY_COOLDOWN_SEC",    900)
MAX_TOKEN_EXPOSURE_FRAC = cfg("risk", "MAX_TOKEN_EXPOSURE_FRAC", 0.08)
# [v4.27-B] 8% default: on small accounts (~$25-33) 3% = $0.79 which falls below
# Polymarket's executable minimum (~$1.0 / 5 contracts). 8% = ~$2.0 leaves room
# to place at least one executable order while still capping per-token accumulation.
ADJACENT_HAIRCUT1       = 0.60
ADJACENT_HAIRCUT2       = 0.35
# [v4.17-fix-neg] neg_risk fallback policy. True = assume NegRisk for temp markets
# when the API field is absent. False = skip tokens with missing field.
_NEG_RISK_FALLBACK = os.getenv("NEG_RISK_FALLBACK", "true").lower() != "false"

# [v4.19-fix-funder] Resolved funder address - set once in main_async after EOA derivation.
# All API queries (positions, activity, redeem) use this instead of os.getenv("PM_FUNDER_KEY").
RESOLVED_FUNDER: str = ""
CREDS_REFRESH_INTERVAL = cfg("intervals", "CREDS_REFRESH_INTERVAL", 1800)
LOOP_SLEEP           = cfg("trading", "LOOP_SLEEP", 3)
# [v4.6-audit] Becker calibration zones (722K trades, 3061 mercati weather)
# Sotto 40¢: reverse longshot bias → BUY edge +3-4%
# 40-65¢: mercato efficiente → zero edge
# Sopra 65¢: overpriced → SELL edge +2-5%
BECKER_BUY_ZONE_MIN  = 0.06   # BUY solo se mid > 6¢ (sotto = troppa varianza)
BECKER_BUY_ZONE_MAX  = 0.60   # [BECKER] raised to capture 51-60c inefficiency (+5.59% edge)
BECKER_SELL_ZONE_MIN = 0.75   # SELL solo se mid > 75¢ (Becker edge -2.5 a -5%)
BECKER_SELL_ZONE_MAX = 0.95   # SELL solo se mid < 95¢ (sopra = troppo vicino a 1)
BECKER_FILTER_ENABLED = True   # EMERGENCY SAFETY: DO NOT DISABLE ON PROD - Protects real capital
# [SEV-1] Environment isolation — SHADOW deve usare file/config separati
BOT_ENV = "live" if "shadow" not in _os.path.abspath(__file__) else "shadow"
if BOT_ENV == "shadow":
    DRY_RUN = True  # Shadow NON deve mai tradare con soldi veri
    print("⚠️  SHADOW MODE: DRY_RUN=True enforced, no real trades")

# [v4.2-C2] RMSE ECMWF reale per città (Seoul inverno/primavera, London primavera)
CITY_SIGMA_BASE      = {"seoul": 2.8, "london": 2.2}
CITY_SKEW_ALPHA      = {"seoul": -1.5, "london": -1.5}  # [FEAT1] negative = left-skewed max temps
METAR_STATIONS       = {"london": "EGLC", "seoul": "RKSI"}  # [FEAT2] ICAO codes
METAR_CACHE_TTL      = 300   # [FEAT2] 5 minutes
GROUP_CAP_FRAC       = 0.25   # [v4.3-H3] max 25% capitale per city_date group
MARKET_CACHE_TTL     = 60
FORECAST_CACHE_TTL   = 1800
FORECAST_FALLBACK_TTL = 900   # [v4.31-fix4] Raised from 300s: retry API every 15min to avoid 429 cascade
WEIGHT_EMA_ALPHA     = 0.15        # [FEAT3] EMA smoothing for forecast error tracking
WEIGHT_EVAL_INTERVAL = 86400       # [FEAT3] re-evaluate weights daily (seconds)
WEIGHT_MIN_FLOOR     = 0.05        # [FEAT3] no model drops below 5% weight
FORECAST_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forecast_weights.json")
POSITIONS_FILE       = "positions.json"
# [v4.9-fix] Cancel-race guard: ms minimi tra cancel e nuovo place sullo stesso token
# Il CLOB Polymarket impiega 2-3s a processare un cancel. Se si piazza un nuovo ordine
# prima che il cancel sia confermato, le shares del fill vanno all'ordine "orfano" →
# CLOB desync (shares visibili in Gamma/positions ma 0 nella sell page/CLI).
CANCEL_CONFIRM_TIMEOUT = 4.0    # secondi max ad aspettare conferma cancel
CANCEL_MIN_WAIT        = 2.0    # secondi minimi tra cancel e nuovo place (anche senza conferma)
# [TGT1] _last_cancel_ts -> STATE._last_cancel_ts

# [v4.11-B8] Throttle globale per ciclo di trading
# [v4.13-QC6] Spostato dalle costanti locali del loop alle costanti globali
MAX_ORDERS_PER_CYCLE   = 3      # max ordini per ciclo indipendentemente dall'exposure

# [v4.1-C3] Global exposure cap — aggiornato da sync_exposure_from_positions
# [TGT1] TOTAL_EXPOSURE -> STATE.total_exposure
MAX_TOTAL_EXPOSURE_FRAC = 0.75   # max 75% del capitale in posizioni aperte
# [v4.13-DC4] Rimosso _token_exposure: non veniva mai scritto (pop() sempre ritornava 0.0)
# La liberazione di exposure a risoluzione è gestita da sync_exposure_from_positions

CITY_COORDS = {
    "seoul":  {"lat": 37.4691, "lon": 126.4510, "tz": "Asia/Seoul"},   # [v4.25-rksi] RKSI Incheon Intl Airport
    "london": {"lat": 51.5048, "lon":  0.0495,  "tz": "Europe/London"},  # [v4.25-eglc] EGLC London City Airport
}

# [v4.25-eglc] Observed intraday max for London City Airport (EGLC).
# Source: updated externally (e.g. cron + wunderground scraper) — NOT fetched in-bot.
# Format: {"date": "YYYY-MM-DD", "max_c": 14.2}
# If absent or stale, falls back silently — bot behaves as before.
_EGLC_OBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eglc_obs.json")

def get_eglc_observed_max() -> "float | None":
    """[v4.25-eglc] [FEAT2] Read today's observed max temp at EGLC from METAR cache.
    Cache populated by fetch_metar_max('london') in async loop.
    Returns float (°C) if today's date matches, else None (safe fallback).
    """
    cached = _METAR_CACHE.get("EGLC")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cached and cached.get("date") == today_str:
        return cached["max_c"]
    return None

# [v4.25-rksi] Observed intraday max for Incheon Intl Airport (RKSI).
# Source: updated externally (e.g. cron + wunderground scraper) — NOT fetched in-bot.
# Format: {"date": "YYYY-MM-DD", "max_c": 14.2}
# If absent or stale, falls back silently — bot behaves as before.
_RKSI_OBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rksi_obs.json")

def get_rksi_observed_max() -> "float | None":
    """[v4.25-rksi] [FEAT2] Read today's observed max temp at RKSI from METAR cache.
    Cache populated by fetch_metar_max('seoul') in async loop."""
    cached = _METAR_CACHE.get("RKSI")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cached and cached.get("date") == today_str:
        return cached["max_c"]
    return None

# [v4.1-H2] WebSocket endpoint ufficiale Polymarket
WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ── LOGGING ───────────────────────────────────────────────────────
logging.basicConfig(
    filename="tempbot.log", level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("tempbot")
log.addHandler(logging.StreamHandler())
log.propagate = False  # [BUG3-FIX] prevent root FileHandler writing tempbot.log a 2nd time
# [FIX-LOG] basicConfig è no-op se il root logger ha già handler (APScheduler/asyncio import order).

# [TGT2] Persistent aiohttp session (avoids creating/destroying per-call)
_aiohttp_session: Optional[aiohttp.ClientSession] = None

async def _get_aio_session() -> aiohttp.ClientSession:
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        _aiohttp_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10))
    return _aiohttp_session

# [FEAT2] METAR observation cache: station → {"max_c": float, "ts": float, "date": str}
_METAR_CACHE: dict = {}

# [FEAT3] Dynamic forecast weight state (EMA error tracking)
_FORECAST_WEIGHT_STATE: dict = {
    "ema_errors": {},       # model_name → EMA of |forecast - actual|
    "dynamic_weights": {},  # model_name → current weight
    "last_eval_ts": 0.0,
}

async def fetch_metar_max(city: str) -> "float | None":
    """[FEAT2] Fetch last 24h METAR observations, return today's max temp in °C.
    Uses AviationWeather.gov JSON API. 5-min cache. Returns None on any error."""
    station = METAR_STATIONS.get(city.lower())
    if not station:
        return None
    now = time.time()
    cached = _METAR_CACHE.get(station)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cached and cached.get("date") == today_str and (now - cached["ts"]) < METAR_CACHE_TTL:
        return cached["max_c"]
    try:
        session = await _get_aio_session()
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json&hours=24"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            resp.raise_for_status()
            obs_list = await resp.json()
        if not obs_list:
            return None
        max_c = max(o["temp"] for o in obs_list if o.get("temp") is not None)
        _METAR_CACHE[station] = {"max_c": float(max_c), "ts": now, "date": today_str}
        log.info(f"METAR {station}: max_today={max_c}°C from {len(obs_list)} obs")
        return float(max_c)
    except Exception as e:
        log.warning(f"METAR {station} FAILED: {e}")
        return None

# Fix: FileHandler esplicito + level esplicito sul logger "tempbot" per garantire tempbot.log scritto.
log.setLevel(logging.INFO)
# [BUG3-FIX] FileHandler removed: tee in tmux writes file; propagate=False prevents root duplication

# ══════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════════════
FORECAST_CACHE: dict = {}
MARKET_CACHE:   dict = {"data": [], "ts": 0.0}
WS_BOOK:        Dict[str, dict] = {}          # token_id → {bids, asks, mid, depth, ts}

# [v4.12-S3] Cache per metadati di signing per token_id.
# neg_risk e tick_size fanno parte della firma EIP-712: sbagliare significa
# "invalid signature" dal CLOB e ordine rifiutato silenziosamente.
# I mercati temperatura Polymarket sono TUTTI neg_risk=True (multi-outcome event
# dove le probabilita' devono sommare a 1.0, gestito dal NegRisk Exchange contract
# 0xC5d563A36AE78145C45a50134d48A1215220f80a invece dell'exchange standard).
# tick_size tipicamente "0.01" per i mercati temperatura.
TOKEN_META: Dict[str, dict] = {}  # token_id -> {"neg_risk": bool, "tick_size": str}

# [TGT1] daily_pnl -> STATE.daily_pnl
# [TGT1] daily_reset_ts -> STATE.daily_reset_ts
# [TGT1] _daily_reset_capital -> STATE._daily_reset_capital
# [TGT1] kill_until -> STATE.kill_until
# [TGT1] peak_capital -> STATE.peak_capital

# [TGT1] _last_trade_ids -> STATE._last_trade_ids
# [TGT1] _local_positions -> STATE._local_positions
# [FIX-A] Per-token stoploss pending tracker: token_id -> timestamp of last SELL attempt.
# Prevents the same losing position being re-submitted every 120s before fill is confirmed.
# [TGT1] _sl_pending -> STATE._sl_pending
# [TGT1] _SL_PENDING_TTL -> STATE.SL_PENDING_TTL

# [v4.27-A] BUY re-entry cooldown
LAST_BUY_TS:   dict = {}
LAST_BUY_EDGE: dict = {}
# [v4.27-C] city_date -> set of exact thresholds with open inventory
_adjacent_exact_registry: dict = {}
_quant_meta_cache: dict = {}  # [QUANT] token_id → {fair_prob, mkt_prob, edge, kelly_frac, sizing_mode}

def should_allow_buy_reentry(token_id: str, edge: float, now: float = None) -> bool:
    """[v4.27-A] Block BUY re-entry within REENTRY_COOLDOWN_SEC unless edge >= 1.5x prior."""
    if now is None:
        now = time.time()
    last_ts = LAST_BUY_TS.get(token_id, 0.0)
    if now - last_ts < REENTRY_COOLDOWN_SEC:
        prior_edge = LAST_BUY_EDGE.get(token_id, 0.0)
        if prior_edge > 0 and edge < prior_edge * 1.5:
            return False
    return True

def record_buy_attempt(token_id: str, edge: float) -> None:
    """[v4.27-A] Record BUY submission for cooldown tracking."""
    LAST_BUY_TS[token_id]   = time.time()
    LAST_BUY_EDGE[token_id] = edge

def adjacent_bucket_haircut(city: str, date_str: str, threshold: float,
                             bucket_type: str) -> float:
    """[v4.27-C] Size multiplier for adjacent exact bucket accumulation.
    Only for exact buckets. Uses _adjacent_exact_registry — no API calls.
    """
    if bucket_type != "exact" or threshold < 0:
        return 1.0
    adj = _adjacent_exact_registry.get(f"{city}_{date_str}", set())
    adj_count = sum(1 for t in adj if abs(t - threshold) <= 1.0 and t != threshold)
    if adj_count >= 2:
        log.info(f"ADJACENT HAIRCUT x{ADJACENT_HAIRCUT2} {city} {date_str} {threshold}C adj={adj_count}")
        return ADJACENT_HAIRCUT2
    if adj_count == 1:
        log.info(f"ADJACENT HAIRCUT x{ADJACENT_HAIRCUT1} {city} {date_str} {threshold}C adj={adj_count}")
        return ADJACENT_HAIRCUT1
    return 1.0

# ══════════════════════════════════════════════════════════════════
# [GARCH] GARCH(1,1) — MIT Phase 6
# σ²ₜ = α₀ + α₁·ε²ₜ₋₁ + β₁·σ²ₜ₋₁
# Spread si allarga dopo uno shock info e decade a β₁ periodi.
# ══════════════════════════════════════════════════════════════════
@dataclass
class GarchState:
    alpha0: float = 1e-5   # varianza baseline
    alpha1: float = 0.15   # peso shock precedente
    beta1:  float = 0.80   # persistenza (decay rate)
    sigma2: float = 1e-4
    residuals: deque = field(default_factory=lambda: deque(maxlen=100))

    def update(self, price_return: float) -> float:
        eps2 = price_return ** 2
        self.residuals.append(eps2)
        self.sigma2 = (self.alpha0
                       + self.alpha1 * eps2
                       + self.beta1  * self.sigma2)
        self.sigma2 = max(self.sigma2, 1e-8)
        return math.sqrt(self.sigma2)

    def spread_multiplier(self) -> float:
        baseline = math.sqrt(self.alpha0 / max(1 - self.alpha1 - self.beta1, 0.01))
        ratio    = math.sqrt(self.sigma2) / max(baseline, 1e-6)
        mult     = min(3.0, max(1.0, ratio))
        if mult > 1.2:
            log.info(f"GARCH spread_mult={mult:.2f} σ²={self.sigma2:.2e}")
        return mult

GARCH_STATES: Dict[str, GarchState] = defaultdict(GarchState)

# ══════════════════════════════════════════════════════════════════
# [FAC] FACTOR MODEL — MIT Phase 8
# Rᵢ = αᵢ + β₁F1(global) + β₂F2(local) + β₃F3(liquidity) + εᵢ
# Senza questo il bot crede di avere 20 posizioni indipendenti
# mentre ha 3 factor exposure concentrate.
# ══════════════════════════════════════════════════════════════════
@dataclass
class FactorModel:
    f1_history: deque = field(default_factory=lambda: deque(maxlen=30))
    f2_seoul:   deque = field(default_factory=lambda: deque(maxlen=30))
    f2_london:  deque = field(default_factory=lambda: deque(maxlen=30))
    f3_history: deque = field(default_factory=lambda: deque(maxlen=30))

    def update_factors(self, seoul_anom: float,
                       london_anom: float, spread_change: float):
        f1 = (seoul_anom + london_anom) / 2.0
        self.f1_history.append(f1)
        self.f2_seoul.append(seoul_anom - f1)
        self.f2_london.append(london_anom - f1)
        self.f3_history.append(spread_change)

    def factor_covariance(self) -> np.ndarray:
        if len(self.f1_history) < 5:
            return np.eye(3) * 0.001
        n = min(len(self.f1_history), len(self.f3_history),
                len(self.f2_seoul), len(self.f2_london))
        # [v4.6-audit2] Usa media f2_seoul/f2_london come fattore F2
        f2_avg = [(s+l)/2 for s,l in
                  zip(list(self.f2_seoul)[-n:], list(self.f2_london)[-n:])]
        F = np.column_stack([
            list(self.f1_history)[-n:],
            f2_avg,
            list(self.f3_history)[-n:]
        ])
        return np.cov(F.T) + np.eye(3) * 1e-8

    def factor_loadings(self, token_mid: float, city: str) -> np.ndarray:
        nao    = _get_nao_corr()
        beta_f1 = max(0.0, nao * (1.0 - 2.0 * abs(token_mid - 0.5)))
        # [v4.5-fix7b] Beta differenziato: Seoul è meno correlato al NAO
        # London: β=0.60 (Atlantico N, forte dipendenza NAO)
        # Seoul:  β=0.35 (Asia E, influenza NAO indiretta via jet stream)
        beta_f2 = 0.60 if city == "london" else (0.35 if city == "seoul" else 0.0)
        beta_f3 = 0.20
        return np.array([beta_f1, beta_f2, beta_f3])

    def idiosyncratic_var(self, mid: float) -> float:
        return max(mid * (1 - mid), 1e-6)

    def total_var(self, mid: float, city: str) -> float:
        beta    = self.factor_loadings(mid, city)
        sigma_f = self.factor_covariance()
        return float(beta @ sigma_f @ beta) + self.idiosyncratic_var(mid)

def _seed_factor_model(fm: FactorModel, n_seed: int = 10):
    """
    [v4.5-fix7] Pre-popola deque con prior storico Seoul/London.
    NAO correlation ~0.3, idiosyncratic spread variability ~0.02.
    Evita np.eye*0.001 (zero correlation) per i primi 90s di run.
    """
    import random
    rng = random.Random(42)  # deterministic seed
    for _ in range(n_seed):
        # Prior: lieve correlazione positiva Seoul/London (~0.3 NAO tipico)
        common   = rng.gauss(0.0, 0.03)   # shock comune (F1)
        seo_idio = rng.gauss(0.0, 0.025)  # Seoul idiosyncratic
        lon_idio = rng.gauss(0.0, 0.020)  # London idiosyncratic
        spread_c = rng.gauss(0.0, 0.015)  # spread change (F3)
        fm.update_factors(
            common + seo_idio,  # seoul_anom
            common + lon_idio,  # london_anom
            spread_c
        )
    log.info(f"FactorModel seeded with {n_seed} prior observations")

FACTOR_MODEL = FactorModel()
_seed_factor_model(FACTOR_MODEL)

# ══════════════════════════════════════════════════════════════════
# [PVAR] PORTFOLIO VaR — MIT Phase 5
# σ²_p = wᵀΣw   (parametric fast)
# Monte Carlo completo ogni 10 min con Cholesky
# ══════════════════════════════════════════════════════════════════
@dataclass
class PortfolioRisk:
    positions:   Dict[str, dict] = field(default_factory=dict)
    mc_var_99:   float = 0.0
    mc_var_ts:   float = 0.0
    MC_INTERVAL: int   = 600

    def update_position(self, token_id: str, size: float,
                        mid: float, city: str):
        self.positions[token_id] = {"size": size, "mid": mid, "city": city}

    def parametric_var_99(self, capital: float) -> float:
        if not self.positions: return 0.0
        total_var = 0.0
        toks = list(self.positions.values())
        for pos in toks:
            w = pos["size"] / max(capital, 1.0)
            total_var += (w**2) * FACTOR_MODEL.total_var(pos["mid"],
                                                          pos.get("city","london"))
        for i in range(len(toks)):
            for j in range(i+1, len(toks)):
                wi = toks[i]["size"] / max(capital, 1)
                wj = toks[j]["size"] / max(capital, 1)
                bi = FACTOR_MODEL.factor_loadings(toks[i]["mid"],
                                                   toks[i].get("city","london"))
                bj = FACTOR_MODEL.factor_loadings(toks[j]["mid"],
                                                   toks[j].get("city","london"))
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
            beta       = FACTOR_MODEL.factor_loadings(pos["mid"],
                                                       pos.get("city","london"))
            idio_sigma = math.sqrt(FACTOR_MODEL.idiosyncratic_var(pos["mid"]))
            ret        = factor_shocks @ beta + np.random.randn(n_paths) * idio_sigma
            pnl_paths += pos["size"] * ret
        var_99 = float(np.percentile(-pnl_paths, 99))
        self.mc_var_99 = max(var_99, 0.0)
        self.mc_var_ts = now
        log.info(f"PORTFOLIO MC VaR99: ${self.mc_var_99:.2f} "
                 f"({self.mc_var_99/max(capital,1):.1%}) n={len(toks)}")
        return self.mc_var_99

    def var_budget_fraction(self, capital: float, new_size: float,
                             new_mid: float, city: str) -> float:
        var_budget   = 0.20 * capital
        current_var  = self.parametric_var_99(capital)
        beta         = FACTOR_MODEL.factor_loadings(new_mid, city)
        marginal_var = 2.33 * math.sqrt(
            float(beta @ FACTOR_MODEL.factor_covariance() @ beta)
            + FACTOR_MODEL.idiosyncratic_var(new_mid)
        ) * new_size
        return (current_var + marginal_var) / max(var_budget, 1.0)

PORTFOLIO_RISK = PortfolioRisk()

# ══════════════════════════════════════════════════════════════════
# [GLS] GLS CALIBRATION — MIT Phase 4
# Pesi 1/σ²(T) su dataset Becker: vicino alla scadenza la varianza
# collassa → quei trade pesano meno nella calibrazione.
# [v4.1-H1] Fallback conservativo se file mancante.
# ══════════════════════════════════════════════════════════════════
GLS_CALIBRATION: dict = {}

def load_becker_gls(calib_file: str = "weather_calibration_becker.json") -> dict:
    try:
        with open(calib_file) as f:
            B = json.load(f)
        CAL = B.get("calibration_surface", {})
    except Exception as e:
        log.warning(f"GLS: file '{calib_file}' non trovato ({e})")
        # [v4.1-H1] Fallback conservativo invece di dict vuoto
        # gls_sigma=0.08 → gls_confidence ~0.125 → Kelly ridotto del 87.5%
        log.warning("GLS: usando fallback conservativo (gls_sigma=0.08)")
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
                "gls_edge":   actual - implied,
                "gls_weight": gls_w,
                "gls_sigma":  math.sqrt(var_b / max(n, 1)),
            }
        except Exception:
            continue

    if not gls_edges:
        log.warning("GLS: calibration vuota → fallback conservativo")
        return {p: {"gls_edge": 0.0, "gls_sigma": 0.08} for p in range(1, 101)}

    log.info(f"GLS: {len(gls_edges)} bucket da '{calib_file}'")
    return gls_edges

def get_gls_edge(mid: float) -> Tuple[float, float]:
    global GLS_CALIBRATION
    if not GLS_CALIBRATION:
        GLS_CALIBRATION = load_becker_gls()
    pct = round(mid * 100)
    if pct in GLS_CALIBRATION:
        d = GLS_CALIBRATION[pct]
        return d["gls_edge"], d["gls_sigma"]
    keys = sorted(GLS_CALIBRATION.keys())
    for i in range(len(keys)-1):
        lo, hi = keys[i], keys[i+1]
        if lo <= pct <= hi:
            t = (pct-lo)/(hi-lo)
            return (GLS_CALIBRATION[lo]["gls_edge"]*(1-t)
                    + GLS_CALIBRATION[hi]["gls_edge"]*t,
                    GLS_CALIBRATION[lo]["gls_sigma"]*(1-t)
                    + GLS_CALIBRATION[hi]["gls_sigma"]*t)
    return 0.0, 0.08

# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════
def logit_clip(p: float) -> float:
    return max(0.02, min(0.98, p))

def _get_nao_corr() -> float:
    """[MEDIUM-4] NAO stagionale."""
    m = datetime.now(timezone.utc).month
    if 6 <= m <= 8:           return 0.12
    elif m in (3,4,5,9,10,11): return 0.22
    return 0.35

def hours_to_resolution(m: dict) -> float:
    res = m.get("resolution_time") or m.get("endDate") or ""
    try:
        dt   = datetime.fromisoformat(res.replace("Z","+00:00"))
        diff = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.5, diff/3600.0)
    except Exception:
        # [v4.16-fix3r] Return 0.0: markets with unparseable dates are skipped
        # by the T_check < MIN_T_HOURS/24.0 filter. Better to skip than to
        # assume 24h and trade a potentially expired/corrupt market.
        log.warning(f"hours_to_resolution parse fail: {res!r} - skipping market (T=0)")
        return 0.0

def clean_token_id(raw) -> str:
    """Estrae il primo token_id (YES token) da qualsiasi formato Polymarket.
    clobTokenIds può essere: list, JSON string, plain string.
    YES token = indice 0, NO token = indice 1.
    """
    if isinstance(raw, list) and raw:
        return str(raw[0]).strip()
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if parsed: return str(parsed[0]).strip()
            except Exception:
                pass
        # Rimuovi eventuali caratteri non numerici/hex
        return raw
    return str(raw).strip() if raw else ""

# ══════════════════════════════════════════════════════════════════
# [v4.1-H2] WEBSOCKET ORDERBOOK — formato subscribe corretto
# Era: {"type":"Market","markets":[{"condition_id":...}]}
# Fix: {"assets_ids":[...], "operation":"subscribe"}
# ══════════════════════════════════════════════════════════════════
async def ws_orderbook_listener(token_ids: List[str]):
    if not token_ids: return

    # [v4.1-H2] Payload corretto per Polymarket Market Channel
    subscribe_msg = {
        "assets_ids": token_ids,
        "operation":  "subscribe"
    }

    # [v4.14-WS] websockets 16.0 ha rimosso il parametro ssl= dalla firma di connect().
    # La negoziazione TLS avviene automaticamente quando l'URI è wss://.
    # Usiamo import esplicito da websockets.asyncio.client come raccomandato dalla v16 docs.
    from websockets.asyncio.client import connect as ws_connect

    retry_delay = 5

    while True:
        try:
            async with ws_connect(
                WS_ENDPOINT,
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10,
                open_timeout=20,
                additional_headers={"User-Agent": "TempBot/4.19"},
            ) as ws:
                await ws.send(json.dumps(subscribe_msg))
                log.info(f"WS connected: {len(token_ids)} token")
                print(f"📡 WS connected: {len(token_ids)} token", flush=True)
                retry_delay = 5  # reset backoff su connessione ok
                async for raw in ws:
                    try:
                        if not raw or raw == '[]':
                            await asyncio.sleep(0)  # yield al trading loop
                            continue
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            if not parsed:
                                await asyncio.sleep(0)  # lista vuota: yield
                                continue
                            for item in parsed:
                                if isinstance(item, dict):
                                    _process_ws_message(item)
                        elif isinstance(parsed, dict):
                            _process_ws_message(parsed)
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        log.warning(f"WS msg error: {e}")
        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException) as e:
            log.warning(f"WS disconnected: {e} — retry {retry_delay}s")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 60)
        except Exception as e:
            log.error(f"WS error: {e} — retry {retry_delay}s")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

def _process_ws_message(msg: dict):
    event_type = msg.get("event_type") or msg.get("type","")
    if event_type not in ("book","price_change","orderbook_snapshot","tick_size_change"):
        return
    # [v4.15-fix2] asset_id == token_id (hex). Prima: GARCH scritto con condition_id,
    # letto con token_id -> mismatch silenzioso, spread_multiplier sempre 1.0.
    market = (msg.get("asset_id") or msg.get("market") or
              msg.get("condition_id") or "")
    if not market: return

    def parse_side(entries):
        out = []
        for e in entries:
            try:
                p = float(e.get("price",0) or (e[0] if isinstance(e,(list,tuple)) else 0))
                s = float(e.get("size",0)  or (e[1] if isinstance(e,(list,tuple)) else 0))
                if p>0 and s>0: out.append((p,s))
            except (ValueError, TypeError, KeyError, IndexError): continue
        return out

    bids = sorted(parse_side(msg.get("bids",[])), reverse=True)
    asks = sorted(parse_side(msg.get("asks",[])))
    if not bids and not asks: return

    # Aggiorna GARCH con return implicito
    if market in WS_BOOK and bids and asks:
        old_mid = WS_BOOK[market].get("mid",0)
        if old_mid > 0:
            new_mid = (bids[0][0] + asks[0][0]) / 2
            GARCH_STATES[market].update((new_mid - old_mid) / old_mid)

    new_mid = (bids[0][0]+asks[0][0])/2 if bids and asks else 0.0
    WS_BOOK[market] = {
        "bids":  bids, "asks": asks, "mid": new_mid,
        "depth": sum(b[1] for b in bids[:5]), "ts": time.time()
    }

async def get_book(token_id: str, client=None,  # [v4.22-fix-book-async]
             market_obj: dict = None) -> Tuple[Optional[list], Optional[list]]:
    """
    Priorità:
      1. WS cache (fresca <2s) — live
      2. REST autenticato — live
      3. CLOB pubblico — dry-run
      4. outcomePrices sintetico — dry-run fallback se CLOB vuoto
    [v4.22-fix-book-async] Convertito ad async per supportare await _orderbook_public.
    """
    # [v4.15-fix7] TTL check: dati freschi (<2s) usati live; dati stale (>30s) scartati
    cached = WS_BOOK.get(token_id)
    _ws_age = time.time() - cached.get("ts", 0) if cached else 999
    if cached and _ws_age < 2.0:
        return cached["bids"], cached["asks"]
    if cached and _ws_age > 30.0:
        # Dato stale: rimuovi e fallback al REST
        del WS_BOOK[token_id]
        cached = None
    if client:
        # [v4.24-ws-only] Use WS cache if any data exists, else skip market
        # REST calls block thread pool and cause 1h+ hangs with 43 markets
        if cached:  # use stale WS data rather than blocking REST
            return cached["bids"], cached["asks"]
        # No WS data at all — skip this market, WS will populate next cycle
        return None, None
    if DRY_RUN:
        bids, asks = await _orderbook_public(token_id)  # [v4.22-fix-book-async]
        if bids and asks:
            return bids, asks
        # Fallback: book sintetico da outcomePrices
        if market_obj:
            return _book_from_outcome_prices(market_obj)
    return None, None

async def _orderbook_public(token_id: str) -> Tuple[Optional[list], Optional[list]]:  # [v4.22-fix-book-async]
    """Fetch orderbook pubblico senza autenticazione (usato in dry-run).
    Endpoint: GET https://clob.polymarket.com/book?token_id=<single_id>
    Fallback: outcomePrices dal market object se CLOB non ha dati.
    [v4.22-fix-book-async] Convertito ad async: usava requests.get bloccante
    con timeout=5s per token → 46 token × 5s = 230s di blocco per ciclo.
    """
    try:
        tid = clean_token_id(token_id)
        if not tid:
            return None, None

        r = await _async_get(
            "https://clob.polymarket.com/book",
            params={"token_id": tid},
            timeout=5
        )
        if r is None or r.status_code != 200:
            return None, None
        data = r.json()
        if "error" in data:
            return None, None

        def parse(entries):
            out = []
            for e in (entries or []):
                try:
                    p = float(e.get("price") or e.get("p") or 0)
                    s = float(e.get("size")  or e.get("s") or 0)
                    if p > 0 and s > 0: out.append((p, s))
                except (ValueError, TypeError, AttributeError): continue
            return out

        bids = sorted(parse(data.get("bids", [])), reverse=True)
        asks = sorted(parse(data.get("asks", [])))
        return bids or None, asks or None
    except Exception as e:
        log.debug(f"PUBLIC BOOK ERROR {str(token_id)[:16]}: {e}")
        return None, None


def _book_from_outcome_prices(m: dict) -> Tuple[Optional[list], Optional[list]]:
    """
    Costruisce un book sintetico da outcomePrices quando il CLOB è vuoto.
    outcomePrices = [yes_price, no_price] — es. ["0.9985", "0.0015"]
    yes_price = probabilità implicita del mercato.
    Spread sintetico = MIN_MAKER_SPREAD attorno al mid.
    """
    try:
        prices_raw = m.get("outcomePrices") or []
        if isinstance(prices_raw, str):
            prices_raw = json.loads(prices_raw)
        if not prices_raw: return None, None
        yes_price = float(prices_raw[0])
        if yes_price <= 0 or yes_price >= 1: return None, None
        # Book sintetico: spread minimo attorno al prezzo
        half = MIN_MAKER_SPREAD / 2
        # [v4.16-fix12] Window-shift: keep synthetic bid/ask strictly within
        # (0.011, 0.989). For extreme prices (e.g. 0.9985) the naive
        # max(0.01, mid-half) / min(0.99, mid+half) produces bid>0.99 or
        # a crossed book. Shift the spread window inward instead.
        bid_raw = yes_price - half
        ask_raw = yes_price + half
        lo, hi  = 0.011, 0.989
        if ask_raw > hi:
            shift   = ask_raw - hi
            bid_raw -= shift
            ask_raw -= shift
        if bid_raw < lo:
            shift   = lo - bid_raw
            bid_raw += shift
            ask_raw += shift
        bid = max(lo, bid_raw)
        ask = min(hi, ask_raw)
        if bid >= ask:
            return None, None
        # [v4.16-fix4r] size=100.0: depth_usd=100 = MIN_DEPTH_USD exactly.
        # depth_mult = sqrt(100/100) = 1.0 (neutral: synthetic book has unknown liquidity).
        # size=50 caused depth_usd=50 < MIN_DEPTH_USD=100 -> ALL synthetic tokens filtered
        # before kelly_and_mc, blocking MM entirely in dry-run / no-WS mode.
        size = 100.0
        return [(bid, size)], [(ask, size)]
    except Exception:
        return None, None

def _orderbook_rest(client, token_id: str):
    try:
        import socket as _s; _st=_s.getdefaulttimeout(); _s.setdefaulttimeout(4.0)
        try:
            book     = client.get_order_book(token_id)
        finally: _s.setdefaulttimeout(_st)
        bids_raw = book.bids if hasattr(book,"bids") else book.get("bids",[])
        asks_raw = book.asks if hasattr(book,"asks") else book.get("asks",[])
        def parse(entries):
            out = []
            for e in entries:
                if hasattr(e,"price"): out.append((float(e.price), float(e.size)))
                elif isinstance(e,dict): out.append((float(e["price"]), float(e["size"])))
            return out
        return sorted(parse(bids_raw),reverse=True), sorted(parse(asks_raw))
    except Exception as e:
        log.warning(f"ORDERBOOK REST {token_id[:12]}: {e}")
        return None, None

# ══════════════════════════════════════════════════════════════════
# [PAR] PARALLEL FORECAST — aiohttp async
# Era: 4 chiamate sequenziali ~2000ms
# Fix: asyncio.gather → ~500ms (4x speedup)
# ══════════════════════════════════════════════════════════════════
FORECAST_MODELS = [
    ("ecmwf_ifs04",          0.40),
    ("gfs_seamless",         0.30),
    ("icon_seamless",        0.20),
    ("meteofrance_seamless", 0.10),
]

async def _fetch_model_async(session: aiohttp.ClientSession,
                              coords: dict,
                              model_name: str,
                              weight: float):
    try:
        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude":coords["lat"],"longitude":coords["lon"],
                    "daily":"temperature_2m_max","timezone":coords["tz"],
                    "forecast_days":7,"models":model_name},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            # [v4.7-fix8] Debug: check if API returned all nulls
            temps = data.get("daily", {}).get("temperature_2m_max", [])
            valid_count = sum(1 for t in temps if t is not None)
            if valid_count == 0 and temps:
                log.debug(f"MODEL {model_name}: API OK but all {len(temps)} temps are null")
            return model_name, data, weight
    except Exception as e:
        log.warning(f"MODEL {model_name} FAILED: {e}")
        return model_name, {}, 0.0

async def fetch_weather_forecast_async(city: str) -> dict:
    """[PAR] 4 modelli meteo in parallelo. Fallback climatologico se API null."""
    city = city.lower()
    now  = time.time()
    if city in FORECAST_CACHE:
        cached = FORECAST_CACHE[city]
        ttl = FORECAST_FALLBACK_TTL if cached.get("is_fallback") else FORECAST_CACHE_TTL
        if now - cached["ts"] < ttl:
            return cached["data"]

    coords = CITY_COORDS[city]
    session = await _get_aio_session()  # [FEAT3] reuse shared session
    effective_weights = _get_effective_weights()  # [FEAT3] dynamic or static weights
    # [v4.31-fix4] Stagger model fetches to avoid open-meteo 429 burst
    await asyncio.sleep(0.3)
    results = await asyncio.gather(
        *[_fetch_model_async(session, coords, m, w)
          for m, w in effective_weights],
        return_exceptions=True
    )

    ensemble: dict = {}
    weights_used: dict = {}
    for res in results:
        if isinstance(res, Exception): continue
        model_name, data, weight = res
        if not data or weight == 0: continue
        try:
            for date, temp in zip(data["daily"]["time"],
                                   data["daily"]["temperature_2m_max"]):
                if temp is None: continue
                ensemble[date]     = ensemble.get(date,0.0) + temp*weight
                weights_used[date] = weights_used.get(date,0.0) + weight
        except (KeyError, TypeError, ValueError): continue

    if not ensemble:
        # [v4.7-fix8] Fallback climatologico — Open-Meteo ritorna null per tutte le date
        # Medie storiche marzo (fonte: weatherspark.com, climate-data.org)
        # Seoul: inizio marzo ~8-10°C, metà ~11-13°C, fine ~14-16°C
        # London: inizio marzo ~9-11°C, metà ~11-12°C, fine ~12-14°C
        CLIM = {
            "seoul":  {(1,10): 9.0, (11,20): 12.0, (21,31): 15.0},
            "london": {(1,10): 10.0, (11,20): 11.5, (21,31): 13.0},
        }
        clim_city = CLIM.get(city, CLIM["london"])
        today = datetime.now(timezone.utc).date()
        result = {}
        for day_offset in range(8):
            d = today + timedelta(days=day_offset)
            date_str = d.isoformat()
            day_of_month = d.day
            for (lo, hi), temp in clim_city.items():
                if lo <= day_of_month <= hi:
                    # Aggiungi jitter ±0.5°C basato su giorno per evitare flat
                    jitter = (day_of_month % 5 - 2) * 0.3
                    result[date_str] = round(temp + jitter, 1)
                    break
        FORECAST_CACHE[city] = {"data": result, "ts": now, "is_fallback": True}
        log.warning(f"ENSEMBLE {city.upper()} FALLBACK CLIMATOLOGICO: {result}")
        print(f"⚠️  FORECAST {city.upper()}: API null → fallback climatologico")
        return result

    result = {d: round(ensemble[d]/weights_used[d],2)
              for d in ensemble if weights_used.get(d,0)>0}
    FORECAST_CACHE[city] = {"data": result, "ts": now, "is_fallback": False}
    log.info(f"ENSEMBLE {city.upper()}: {result}")
    return result

# ══════════════════════════════════════════════════════════════════
# [FEAT3] Dynamic forecast model weights — EMA error tracking
# ══════════════════════════════════════════════════════════════════

def _load_forecast_weights():
    """Load persisted EMA errors and dynamic weights from disk."""
    global _FORECAST_WEIGHT_STATE
    try:
        with open(FORECAST_WEIGHTS_FILE) as f:
            saved = json.load(f)
        _FORECAST_WEIGHT_STATE["ema_errors"] = saved.get("ema_errors", {})
        _FORECAST_WEIGHT_STATE["dynamic_weights"] = saved.get("dynamic_weights", {})
        _FORECAST_WEIGHT_STATE["last_eval_ts"] = saved.get("last_eval_ts", 0.0)
        log.info(f"WEIGHTS LOADED: {_FORECAST_WEIGHT_STATE['dynamic_weights']}")
    except (FileNotFoundError, json.JSONDecodeError):
        log.info("WEIGHTS FILE not found — using static FORECAST_MODELS weights")

def _save_forecast_weights():
    """Persist current EMA errors and dynamic weights to disk."""
    try:
        with open(FORECAST_WEIGHTS_FILE, "w") as f:
            json.dump({
                "ema_errors": _FORECAST_WEIGHT_STATE["ema_errors"],
                "dynamic_weights": _FORECAST_WEIGHT_STATE["dynamic_weights"],
                "last_eval_ts": _FORECAST_WEIGHT_STATE["last_eval_ts"],
            }, f, indent=2)
    except Exception as e:
        log.warning(f"WEIGHTS SAVE FAILED: {e}")

def _get_effective_weights() -> list:
    """Return [(model_name, weight), ...] using dynamic weights if available,
    else static FORECAST_MODELS weights."""
    dw = _FORECAST_WEIGHT_STATE.get("dynamic_weights", {})
    if not dw:
        return list(FORECAST_MODELS)
    return [(m, dw.get(m, w)) for m, w in FORECAST_MODELS]

async def evaluate_forecast_weights(city: str, actual_temps: dict):
    """[FEAT3] Update EMA errors for each model by comparing forecasts to actuals.
    actual_temps: {date_str → actual_max_c} from METAR data.
    Called once per day (guarded by WEIGHT_EVAL_INTERVAL)."""
    now = time.time()
    if now - _FORECAST_WEIGHT_STATE["last_eval_ts"] < WEIGHT_EVAL_INTERVAL:
        return  # already evaluated today

    coords = CITY_COORDS.get(city.lower())
    if not coords:
        return

    session = await _get_aio_session()
    for model_name, _ in FORECAST_MODELS:
        try:
            async with session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": coords["lat"], "longitude": coords["lon"],
                        "daily": "temperature_2m_max", "timezone": coords["tz"],
                        "forecast_days": 2, "models": model_name},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                data = await resp.json()
            forecasts = dict(zip(
                data.get("daily", {}).get("time", []),
                data.get("daily", {}).get("temperature_2m_max", [])
            ))
            for date_str, actual in actual_temps.items():
                fc = forecasts.get(date_str)
                if fc is None or actual is None:
                    continue
                abs_err = abs(fc - actual)
                prev_ema = _FORECAST_WEIGHT_STATE["ema_errors"].get(model_name, 2.5)
                new_ema = WEIGHT_EMA_ALPHA * abs_err + (1 - WEIGHT_EMA_ALPHA) * prev_ema
                _FORECAST_WEIGHT_STATE["ema_errors"][model_name] = round(new_ema, 4)
                log.info(f"WEIGHT_EMA {model_name}: |{fc:.1f}-{actual:.1f}|={abs_err:.1f} ema={new_ema:.2f}")
        except Exception as e:
            log.warning(f"WEIGHT_EVAL {model_name} FAILED: {e}")

    # Compute inverse-error weights with floor
    ema = _FORECAST_WEIGHT_STATE["ema_errors"]
    if len(ema) >= 2:
        inv = {m: 1.0 / max(e, 0.1) for m, e in ema.items()}
        total_inv = sum(inv.values())
        raw_weights = {m: v / total_inv for m, v in inv.items()}
        floored = {m: max(w, WEIGHT_MIN_FLOOR) for m, w in raw_weights.items()}
        total_f = sum(floored.values())
        _FORECAST_WEIGHT_STATE["dynamic_weights"] = {
            m: round(w / total_f, 4) for m, w in floored.items()
        }
        log.info(f"DYNAMIC WEIGHTS: {_FORECAST_WEIGHT_STATE['dynamic_weights']}")

    _FORECAST_WEIGHT_STATE["last_eval_ts"] = now
    _save_forecast_weights()

async def fetch_markets_async() -> list:
    """
    Fetch mercati temperatura Seoul/London via slug deterministici.

    Pattern slug Polymarket (confermato):
      highest-temperature-in-{city}-on-{month}-{day}-{year}
      es: highest-temperature-in-seoul-on-march-2-2026

    Strategia:
      1. Genera slug per Seoul e London per oggi + prossimi 14 giorni
      2. Fetcha ogni evento direttamente per slug (100% hit rate)
      3. Estrae i bucket (mercati nested) da ogni evento trovato

    Il listing /events generale non è affidabile perché gli eventi
    temperatura compaiono solo in pagine profonde (>1000 offset) e
    spariscono dal listing quando active=False il giorno di risoluzione.
    """
    now = time.time()
    if MARKET_CACHE["data"] and now - MARKET_CACHE["ts"] < MARKET_CACHE_TTL:
        return MARKET_CACHE["data"]
    MARKET_CACHE["printed"] = False  # reset per nuovo fetch

    all_markets = []
    seen_ids = set()

    # Genera slug per i prossimi 14 giorni (oggi incluso)
    slugs = []
    today = datetime.now(timezone.utc)
    for day_offset in range(0, 15):
        dt = today + timedelta(days=day_offset)
        # Formato: "march-2" (no zero-padding sul giorno)
        month = dt.strftime("%B").lower()   # "march"
        day   = str(dt.day)                  # "2" non "02"
        year  = str(dt.year)                 # "2026"
        date_part = f"{month}-{day}-{year}"
        for city in ("seoul", "london"):
            slugs.append(f"highest-temperature-in-{city}-on-{date_part}")

    log.info(f"MARKET FETCH: {len(slugs)} slug da verificare")

    async with aiohttp.ClientSession() as session:

        # Fetcha tutti gli slug in parallelo (batch da 10)
        async def fetch_slug(slug: str):
            try:
                async with session.get(
                    "https://gamma-api.polymarket.com/events",
                    params={"slug": slug},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    if not data:
                        return []
                    event = data[0] if isinstance(data, list) else data
                    nested = event.get("markets") or []
                    out = []
                    for m in nested:
                        if not isinstance(m, dict): continue
                        # Salta mercati già risolti o non aperti al trading
                        if not m.get("acceptingOrders", False):
                            continue
                        mid = m.get("conditionId") or m.get("id") or ""
                        if mid in seen_ids: continue
                        seen_ids.add(mid)
                        # Propaga endDate se mancante
                        if not m.get("resolution_time") and not m.get("endDate"):
                            m["resolution_time"] = (event.get("endDate")
                                                    or event.get("end_date") or "")
                        if not m.get("question"):
                            m["question"] = event.get("title") or ""
                        out.append(m)

                        # [v4.12-S3] Popola TOKEN_META con neg_risk e tick_size per ogni token.
                        raw_tid = m.get("clobTokenIds") or m.get("clob_token_ids") or ""
                        _tok = clean_token_id(raw_tid)
                        if _tok:
                            # [v4.14-C1] neg_risk: legge il campo reale da API (negRisk camelCase).
                            # I temperature markets su Polymarket sono *strutturalmente* neg_risk
                            # (multi-outcome event vincolato a 1$), ma NON facciamo assumed=True
                            # implicitamente. Distinguiamo tre stati:
                            #   (a) campo presente e True  → neg_risk = True   (caso atteso)
                            #   (b) campo presente e False → neg_risk = False  (mercato anomalo)
                            #   (c) campo mancante         → neg_risk = True + WARNING in log
                            # Questo ci protegge se la Gamma API cambia struttura o se un mercato
                            # non-temp finisce nello slug matching.
                            _nr_raw = (m.get("negRisk") if m.get("negRisk") is not None
                                       else m.get("neg_risk") if m.get("neg_risk") is not None
                                       else event.get("negRisk") if event.get("negRisk") is not None
                                       else event.get("neg_risk"))  # None = campo assente dall'API
                            if _nr_raw is None:
                                # [v4.17-fix-neg] Fallback configurable via NEG_RISK_FALLBACK env var.
                                # Default True: correct for all current Polymarket temperature markets
                                # (they use the NegRisk Exchange contract). Set NEG_RISK_FALLBACK=false
                                # to disable the fallback and skip tokens with missing negRisk field instead.
                                _fallback_val = os.getenv("NEG_RISK_FALLBACK", "true").lower() != "false"
                                _neg_risk = _fallback_val
                                log.warning(f"TOKEN_META: negRisk field missing for {_tok[:16]} "
                                            f"— using NEG_RISK_FALLBACK={_fallback_val}. "
                                            f"Set NEG_RISK_FALLBACK=false to skip instead.")
                                if not _fallback_val:
                                    continue  # skip token: unknown neg_risk, safer than guessing
                            else:
                                _neg_risk = bool(_nr_raw)
                                if not _neg_risk:
                                    log.warning(f"TOKEN_META: negRisk=False for {_tok[:16]} "
                                                f"— unusual for temperature markets, proceeding with False.")

                            # tick_size: presente come "minimumTickSize" (camelCase) nella Gamma API.
                            # Default "0.01" se mancante (valore standard per temp markets).
                            _tick_raw  = (m.get("minimumTickSize") or m.get("minimum_tick_size")
                                          or "0.01")
                            _tick_size = str(float(_tick_raw)) if _tick_raw else "0.01"
                            # Normalizza: "0.01" non "0.010000"
                            try:
                                _ts_f = float(_tick_size)
                                _tick_size = f"{_ts_f:g}"
                                if "." not in _tick_size:
                                    _tick_size += ".0"
                            except (ValueError, TypeError):
                                _tick_size = "0.01"
                            TOKEN_META[_tok] = {"neg_risk": _neg_risk, "tick_size": _tick_size}
                    if out:
                        log.info(f"SLUG HIT: {slug} — {len(out)} bucket attivi")
                        if not MARKET_CACHE.get("printed"):
                            print(f"  ✅ {event.get('title',slug)} — {len(out)} bucket attivi")
                    return out
            except Exception as e:
                log.debug(f"SLUG {slug}: {e}")
                return []

        # Batch parallelo da 5 slug alla volta
        for i in range(0, len(slugs), 5):
            batch_slugs = slugs[i:i+5]
            # [v4.15-fix5] return_exceptions=True: un slug fallito non abbatte il batch
            results = await asyncio.gather(*[fetch_slug(s) for s in batch_slugs],
                                           return_exceptions=True)
            for mkts in results:
                if isinstance(mkts, Exception):
                    log.warning(f"SLUG BATCH: {mkts}")
                    continue
                all_markets.extend(mkts)

    out = all_markets
    MARKET_CACHE["data"]    = out
    MARKET_CACHE["ts"]      = now
    MARKET_CACHE["printed"] = True
    log.info(f"MARKETS: {len(out)} bucket trovati su {len(slugs)} slug verificati")
    if len(out) == 0:
        log.warning("MARKETS: 0 mercati trovati. Controllare che Polymarket abbia "
                    "mercati temperatura per le prossime 2 settimane.")
    return out

def _market_matches_question(question: str) -> bool:
    """Controlla se la question di un mercato flat è temperatura Seoul/London."""
    q = question.lower()
    city_match = "seoul" in q or "london" in q
    temp_match = any(k in q for k in [
        "temperature", "highest", "weather", "celsius", "°c", "degrees"
    ])
    return city_match and temp_match

# ══════════════════════════════════════════════════════════════════
# PILLAR 1 — COMBINATORIAL ARBITRAGE
# ══════════════════════════════════════════════════════════════════
def bregman_projection(prices: list) -> tuple:
    s = sum(prices) or 1.0
    # Nota: s >= 1.0 sempre per via dell'`or 1.0`, il check s==0 sarebbe irraggiungibile
    p_proj = [p/s for p in prices]
    KL = sum(p*math.log(p/pp) for p,pp in zip(prices,p_proj)
             if p>1e-10 and pp>1e-10)
    return abs(s-1.0), abs(KL)

def integer_program_arb(prices, best_asks, market_ids, kelly_size):
    """[HIGH-3] LP objective senza /n.
    [v4.13-IL2] Usa best_asks come costo di acquisto (non i mid): l'ARB si esegue
    all'ask, non al mid. ppu_exec = 1 - sum(best_asks) misura il profitto reale.
    """
    if not PULP_AVAILABLE: return None, 0.0
    n    = len(best_asks)
    prob = pulp.LpProblem("arb", pulp.LpMaximize)
    buys = [pulp.LpVariable(f"buy_{i}", 0, kelly_size) for i in range(n)]
    # [v4.13-IL2] ppu calcolato sulle ask reali, non sui mid
    ppu  = 1.0 - sum(best_asks)
    prob += ppu * pulp.lpSum(buys)
    for b in buys: prob += b <= kelly_size
    prob += pulp.lpSum(buys) <= kelly_size * n
    prob.solve(pulp.COIN_CMD(msg=0, gapRel=0.05))
    sizes = [pulp.value(b) or 0.0 for b in buys]
    net   = ppu * sum(sizes) - sum(sizes)*(UMA_BUFFER+SLIPPAGE_GUARD)
    if net>0 and ppu>0:
        return {mid:sz for mid,sz in zip(market_ids,sizes) if sz>0.5}, net
    return None, 0.0

def group_buckets_by_day(markets: list) -> dict:
    groups = defaultdict(list)
    for m in markets:
        q    = m.get("question","").lower()
        city = "seoul" if "seoul" in q else "london"
        dm   = re.search(r'(\d{4}-\d{2}-\d{2})', m.get("resolution_time","") or "")
        if dm:
            date_key = dm.group(1)
        else:
            mm = re.search(
                r'(january|february|march|april|may|june|july|august'
                r'|september|october|november|december)\s+(\d{1,2})', q)
            if not mm: continue
            year = datetime.now(timezone.utc).year
            try:
                date_key = datetime.strptime(
                    f"{mm.group(1)} {mm.group(2)} {year}", "%B %d %Y"
                ).strftime("%Y-%m-%d")
            except (ValueError, AttributeError): continue
        groups[f"{city}_{date_key}"].append(m)
    return {k:v for k,v in groups.items() if len(v)>=2}

# ══════════════════════════════════════════════════════════════════
# PILLAR 1b — NAO CROSS-CITY CORRELATION
# ══════════════════════════════════════════════════════════════════
def cross_city_arb_check(fc_london: dict, fc_seoul: dict,
                          date_key: str) -> dict:
    t_lon = fc_london.get(date_key)
    t_seo = fc_seoul.get(date_key)
    if t_lon is None or t_seo is None:
        return {"corr_edge":0.0,"direction":"none"}
    lon_anom  = t_lon - 8.0
    seo_anom  = t_seo - 4.0
    nao       = _get_nao_corr()
    corr_edge = min(abs(lon_anom - seo_anom*nao)/10.0, 0.05)
    direction = ("london_warm_seoul_cold" if lon_anom>0>seo_anom else
                 "london_cold_seoul_warm" if lon_anom<0<seo_anom else
                 "concordant")
    FACTOR_MODEL.update_factors(seo_anom, lon_anom, 0.0)
    log.info(f"NAO({nao:.2f}) {date_key}: lon={t_lon:.1f} seo={t_seo:.1f} "
             f"edge={corr_edge:.4f} dir={direction}")
    return {"corr_edge":corr_edge,"direction":direction,
            "lon_temp":t_lon,"seo_temp":t_seo}

# ══════════════════════════════════════════════════════════════════
# PILLAR 0 — FORECAST PARSING + PROBABILITY
# ══════════════════════════════════════════════════════════════════
def parse_bucket(question: str) -> dict:
    q = question.lower()
    for patterns, btype in [
        ([r'(\d+).*?or higher',r'at least (\d+)',
          r'(\d+).*?or above',r'above (\d+)'], "or_higher"),
        ([r'(\d+).*?or lower',r'(\d+).*?or below',
          r'at most (\d+)',r'below (\d+)'], "or_lower"),
        ([r'be\s+(\d+)',r'equal to (\d+)',
          r'exactly (\d+)',r'(\d+)\s*°?c'], "exact"),
    ]:
        for pat in patterns:
            m = re.search(pat, q)
            if m:
                return {"type":btype,"threshold":float(m.group(1)),"valid":True}
    m = re.search(r'(\d+)', q)
    if m:
        return {"type":"exact","threshold":float(m.group(1)),"valid":False}
    return {"type":"exact","threshold":0.0,"valid":False}

def ecmwf_true_prob(forecast_temp: float, bucket_type: str,
                    threshold: float, T_days: float = None,
                    city: str = "london", is_fallback: bool = False,
                    observed_max: "float | None" = None) -> float:  # [v4.25-eglc]
    """
    [v4.2-C2] sigma per città: CITY_SIGMA_BASE[city] + 0.20 * T_days
    RMSE ECMWF reale: Seoul 2.8°C, London 2.2°C base.
    Era: sigma=1.8 fisso → edge sopravvalutato 15-25% su T<4d.

    [v4.14-M1] Parametro is_fallback: quando il forecast proviene dal
    fallback climatologico (medie mensili storiche, non previsione numerica)
    aggiungiamo una penality sigma esplicita di +1.5°C oltre al _T_adj=5.0
    già applicato dai caller. Questo garantisce che la probabilità calcolata
    sia sistematicamente più piatta (verso 0.5) quando non abbiamo un
    forecast reale, riducendo il kelly_size proporzionalmente via edge minore.
    """
    base_sigma = CITY_SIGMA_BASE.get(city, 2.5)
    # [v4.24-fix-sigma-scaling] Scala sub-lineare: sigma cresce come sqrt(T/7)
    # BUG: sigma lineare (base + 0.20*T) sovrastimava di 4x a T<1d
    # → a T=6h: bot calcolava sigma=2.3°C vs reale ~0.6°C → probabilità troppo piatte
    # → edge invertito: bot vendeva contratti quasi-certi come se fossero incerti
    # FIX: sigma = base * sqrt(T/7) calibrata su RMSE ECMWF (7d = baseline)
    _T_eff = T_days if T_days is not None else 7.0
    sigma = base_sigma * math.sqrt(max(_T_eff, 0.04) / 7.0)
    # [v4.14-M1] Fallback climatologico: sigma aggiuntiva esplicita
    # La climatologia ha incertezza ~2-3°C in più rispetto a ECMWF 5d.
    if is_fallback:
        sigma += 1.5
        log.debug(f"ecmwf_true_prob FALLBACK: sigma aumentata a {sigma:.2f}°C "
                  f"(city={city}, T={T_days:.1f}d)")
    # [FEAT1] skew-normal: alpha<0 = left-skewed (hard ceiling on max temps)
    alpha = CITY_SKEW_ALPHA.get(city, -1.5)
    if   bucket_type == "exact":
        return (skewnorm.cdf(threshold+0.5, alpha, loc=forecast_temp, scale=sigma)
                - skewnorm.cdf(threshold-0.5, alpha, loc=forecast_temp, scale=sigma))
    elif bucket_type == "or_higher":
        # [v4.6-audit5] "X or higher" = P(T >= X) → include X with -0.5
        # [v4.25-eglc] If observed intraday max already >= threshold, outcome is certain.
        # Only for or_higher: mathematically correct (monotone CDF property).
        # exact/or_lower left untouched — semantics differ.
        if observed_max is not None and observed_max >= threshold:
            return 0.995  # ~1.0 but never exact 1 to avoid division issues downstream
        return 1.0 - skewnorm.cdf(threshold - 0.5, alpha, loc=forecast_temp, scale=sigma)
    elif bucket_type == "or_lower":
        # [v4.6-audit5] "X or lower/below" = P(T <= X) → include X with +0.5
        return skewnorm.cdf(threshold + 0.5, alpha, loc=forecast_temp, scale=sigma)
    return 0.5

# ══════════════════════════════════════════════════════════════════
# KELLY FRACTION con GLS confidence
# ══════════════════════════════════════════════════════════════════
def kelly_fraction_dynamic(mid: float, edge: float, sigma: float) -> float:
    """[v4.6-audit-fix3] SELL-aware Kelly.
    Per mid > 0.60 (SELL zone): inverte mid a (1-mid) per il calcolo base.
    Vendere un contratto a 80¢ ha il risk profile di comprare a 20¢.
    Rimuove kill zone 61-100¢ che bloccava tutti i SELL.
    """
    if edge <= 0:
        log.info(f"KELLY SKIP: edge={edge:.4f}<=0")
        return 0.0

    # [v4.6-audit] Per SELL (mid alto), inverti per calcolo base
    # Il rischio di vendere un contratto a 80¢ è simile a comprare a 20¢
    is_sell_zone = mid > 0.60
    effective_mid = (1.0 - mid) if is_sell_zone else mid
    pct = round(effective_mid * 100)

    if 1 <= pct <= 10:
        base = {1:0.15,2:0.15,3:0.15,4:0.18,5:0.18,
                6:0.18,7:0.20,8:0.20,9:0.22,10:0.25}.get(pct,0.15)
    elif 11 <= pct <= 30:
        base = 0.30
    elif 31 <= pct <= 50:
        base = 0.35
    elif 51 <= pct <= 60:
        base = 0.22
    else:
        base = 0.15

    edge_mult = min(1.6, max(0.7, edge/0.04))

    # [GLS] Alta incertezza → Kelly ridotto
    _, gls_sigma    = get_gls_edge(mid)
    gls_confidence  = min(1.0, max(0.5, 0.01/max(gls_sigma,1e-6)))

    vol_mult = min(1.3, max(0.5, 0.25/max(sigma,0.01)))
    frac     = base * edge_mult * vol_mult * gls_confidence
    frac     = min(0.55, max(0.05, frac))
    side_tag = "SELL" if is_sell_zone else "BUY"
    log.info(f"DYN_KELLY: {round(mid*100)}c({side_tag}) eff={pct}c base={base:.2f} "
             f"em={edge_mult:.2f} vm={vol_mult:.2f} gc={gls_confidence:.2f} frac={frac:.3f}")
    return frac

# ══════════════════════════════════════════════════════════════════
# PILLAR 4 — AVELLANEDA-STOIKOV con GARCH spread
# ══════════════════════════════════════════════════════════════════
def avellaneda_stoikov_quote(mid: float, q: float, sigma: float,
                              T: float, token_id: str = "") -> tuple:
    """[GARCH] Spread adattivo post-shock."""
    T   = max(T, 0.001)
    T_adj = max(T, 0.04)  # [v4.22-fix-as-floor] floor ~1h — prevents spread collapse near expiry
    # [v4.16-fix-as] Correct Avellaneda-Stoikov formula:
    # reservation price r = mid - q*γ*σ²*T  (NO constant term here)
    # optimal spread δ = γ*σ²*T + (2/γ)*ln(1+γ/k)  (constant belongs ONLY in δ)
    # Original code subtracted (1/γ)*ln(1+γ/k) from res too, causing ALL quotes
    # to be systematically biased downward regardless of inventory (q=0 → same bias).
    res   = mid - q*GAMMA*(sigma**2)*T
    delta = (GAMMA*(sigma**2)*T_adj  # [v4.22-fix-as-floor]
             + (2/GAMMA)*math.log(1+GAMMA/K_INTENSITY))
    bid  = logit_clip(res - delta/2)
    ask  = logit_clip(res + delta/2)

    gm       = GARCH_STATES[token_id].spread_multiplier() if token_id else 1.0
    min_s    = MIN_MAKER_SPREAD * gm
    max_s    = MAX_MAKER_SPREAD * gm * 1.5
    spread   = ask - bid
    if spread < min_s:
        # [v4.16-fix-as] Center on reservation price `res`, not (ask+bid)/2.
        # Using (ask+bid)/2 inherits any logit-clip bias and drifts away from mid.
        bid   = max(0.02, res - min_s/2)
        ask   = min(0.98, res + min_s/2)
    elif spread > max_s:
        bid   = max(0.02, res - max_s/2)
        ask   = min(0.98, res + max_s/2)
    return bid, ask

# ══════════════════════════════════════════════════════════════════
# PILLAR 2+3 — KELLY + MONTE CARLO
# ══════════════════════════════════════════════════════════════════
def estimate_sigma(mid: float, spread: float, T: float) -> float:
    """Stima sigma di prezzo blendando Bernoulli e spread-implied.

    spread: bid-ask spread del book (NON una sigma — è un proxy).
    sig_s = spread/mid converte lo spread in una stima approssimata della
    volatilità implicita di mercato (dimensionalmente un rapporto, non una
    deviazione standard, ma empiricamente correlato alla sigma reale).

    Blending weight w = max(0, 1 - T*5):
      T=0   → w=1.0: usa solo sig_b (Bernoulli, accurato a risoluzione)
      T=0.2d→ w=0.0: usa solo sig_s (spread-implied, più informativo lontano)
    Questo è corretto: vicino alla scadenza la distribuzione binaria domina.
    """
    sig_b = math.sqrt(max(mid*(1-mid),1e-6))
    sig_s = spread / max(mid,0.01)
    w     = min(1.0, max(0.0, 1.0-T*5))
    sigma = w*sig_b + (1-w)*sig_s
    if   mid<0.05 or mid>0.95: sigma = min(sigma, 0.15)
    elif mid<0.10 or mid>0.90: sigma = min(sigma, 0.25)
    return max(sigma, 0.01)

def time_edge_mult(T_days: float) -> float:
    """[v4.6-audit-fix4] Near-resolution = MINIMA size, non massima.
    T<=0.5d (12h): 0.30x — book illiquido, adverse selection max
    T~1d:          0.60x — ancora rischioso
    T~2-3d:        1.00x — sweet spot: forecast accurato, mercato non efficiente
    T~7d:          0.80x — forecast degradato
    T>=14d:        0.50x — troppa incertezza
    """
    bp = [(0.5, 0.30), (1, 0.60), (3, 1.00), (7, 0.80), (14, 0.50)]
    if T_days <= 0.5: return 0.30
    if T_days >= 14:  return 0.50
    for i in range(len(bp)-1):
        lo_d, lo_m = bp[i]; hi_d, hi_m = bp[i+1]
        if lo_d <= T_days <= hi_d:
            t = (T_days - lo_d) / (hi_d - lo_d)
            return round(lo_m + t * (hi_m - lo_m), 3)
    return 1.00

def kelly_and_mc(edge: float, mid: float, depth_usd: float,
                  sigma: float, capital: float,
                  T: float = 1.0,
                  token_id: str = "",
                  city: str = "london") -> float:
    """
    [v4.1-C2] MC drawdown: peak-to-trough reale (non price range).
    [v4.1-C3] Global exposure cap: TOTAL_EXPOSURE <= 75% capital.
    [PVAR]    VaR budget check per posizione.
    T è in giorni.
    [v4.13-IL1] Rimosso 'global TOTAL_EXPOSURE': questa funzione non scrive mai
    TOTAL_EXPOSURE (vedi commento L1281). Il cap usa STATE.total_exposure.
    """
    sigma_real = estimate_sigma(mid, sigma, T)
    variance   = sigma_real**2 + 1e-9
    frac       = kelly_fraction_dynamic(mid, edge, sigma_real)
    # [QUANT] persist kelly_frac for ledger injection
    if token_id:
        _quant_meta_cache.setdefault(token_id, {})["kelly_frac"] = round(frac, 6)
    if frac == 0.0: return 0.0

    kelly_full = edge / (max(mid,0.01)*variance)
    kelly_full = min(kelly_full, KELLY_FULL_CAP)
    depth_mult = min(2.0, max(0.3, np.sqrt(depth_usd/MIN_DEPTH_USD)))
    t_mult     = time_edge_mult(T)
    size       = frac * kelly_full * capital * depth_mult * t_mult
    size       = min(max(size, 5.0 * mid), capital * 0.08)  # [v4.32-fix] floor=CLOB min (5xmid), cap 8%

    # [v4.11-B2] Global exposure cap: usa STATE.total_exposure (calcolato da
    # positions reali, mai azzerato dal daily reset) invece di TOTAL_EXPOSURE
    # globale che veniva azzerato ogni ciclo causando burst di ordini senza cap.
    if not STATE.can_add_exposure(size, MAX_TOTAL_EXPOSURE_FRAC):
        state_exp = STATE.total_exposure
        cap_limit = STATE.capital * MAX_TOTAL_EXPOSURE_FRAC
        size = max(0.0, cap_limit - state_exp)
        if size < 1.0:
            log.warning(f"EXPOSURE CAP: STATE.total={state_exp:.2f} cap={cap_limit:.2f} → skip")
            return 0.0
        log.warning(f"EXPOSURE CAP: size ridotta a {size:.2f}")

    # [PVAR] VaR budget check
    var_frac = PORTFOLIO_RISK.var_budget_fraction(capital, size, mid, city)
    if var_frac > 1.0:
        size *= (1.0/var_frac)*0.9
        size  = max(size, 1.0)
        log.warning(f"PVAR RESIZE: var_frac={var_frac:.2f} → {size:.2f}")

    if sigma_real > 0.50: sigma_real = 0.50

    dt    = 1.0/48
    # [v4.2-H1] Near-resolution: distribuzione bimodale (asset binario)
    # Era: moto browniano clippato → VaR sottostimato 30-50% vicino a scadenza
    if T <= 0.5:  # <12h a risoluzione
        p_yes = mid
        outcomes = np.random.binomial(1, p_yes, (MC_PATHS, 1)).astype(float)
        noise    = np.random.randn(MC_PATHS, 1) * math.sqrt(p_yes*(1-p_yes)) * 0.3
        paths    = np.clip(np.tile(outcomes + noise, (1, 48)), 0.01, 0.99)
    else:
        paths = np.clip(
            mid + np.cumsum(
                np.random.randn(MC_PATHS,48)*sigma_real*math.sqrt(dt), axis=1),
            0.01, 0.99)

    # [v4.1-C2] FIX: peak-to-trough reale
    # Era: (max-min) paths → misura range totale, non drawdown sequenziale
    # Fix: accumulate peak, calcola trough relativo al peak progressivo
    peak      = np.maximum.accumulate(paths, axis=1)
    trough    = (peak - paths) / np.maximum(peak, 0.01)   # DD relativo
    max_dd    = np.max(trough, axis=1)                     # worst DD per path
    drawdowns = max_dd * (size/capital)

    ruin_prob = float(np.mean(drawdowns > MAX_DD))
    log.info(f"MC: σ={sigma_real:.4f} ruin={ruin_prob:.4f} "
             f"size={size:.2f} T={T:.2f}d")

    if ruin_prob > MAX_RUIN_PROB:
        log.warning(f"MC SKIP: ruin={ruin_prob:.4f}")
        return 0.0

    # [HIGH-1] percentile 95 = worst 5% (era percentile 5)
    dd_p95 = float(np.percentile(drawdowns, 95))
    if dd_p95 > MAX_DD:
        size *= 0.5
        log.warning(f"MC DD_P95={dd_p95:.4f} → halved={size:.2f}")

    # [v4.6-audit5] NON incrementa TOTAL_EXPOSURE qui.
    # L'incremento avviene nei callers (MM/ARB loop) dopo place_order,
    # e sync_exposure_from_positions corregge ogni 60s.
    return size

# ══════════════════════════════════════════════════════════════════
# API CREDS REFRESH — [v4.6-fix6] Previene 401 dopo 30min
# ══════════════════════════════════════════════════════════════════
# [TGT1] _last_creds_refresh -> STATE._last_creds_refresh

def refresh_api_creds(client):
    """Rigenera API creds dalla private key ogni CREDS_REFRESH_INTERVAL secondi."""
    now = time.time()
    if now - STATE._last_creds_refresh < CREDS_REFRESH_INTERVAL:
        return
    STATE._last_creds_refresh = now
    if not client or not CLOB_AVAILABLE:
        return
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        log.info("CREDS REFRESH: API creds rigenerate")
        EVENTS.creds_refresh(True)
    except Exception as e:
        log.warning(f"CREDS REFRESH ERROR: {e}")
        EVENTS.creds_refresh(False, str(e))
        # Fallback: prova con creds da env
        try:
            ak = os.getenv("PM_API_KEY", "")
            if ak:
                client.set_api_creds(ApiCreds(
                    api_key=ak,
                    api_secret=os.getenv("PM_API_SECRET", ""),
                    api_passphrase=os.getenv("PM_API_PASSPHRASE", ""),
                ))
                log.info("CREDS REFRESH: fallback env vars")
        except Exception as e2:
            log.error(f"CREDS REFRESH FALLBACK ERROR: {e2}")

# ══════════════════════════════════════════════════════════════════
# PLACE ORDER
# ══════════════════════════════════════════════════════════════════
async def place_order(client, token_id: str, side: str, price: float, size: float,
                city: str = "london", strategy: str = "MM"):
    """[v4.13-IL3] Coroutine async: size è in notional USD. Converte in contracts internamente.
    Async necessario per await _cancel_guard_wait senza bloccare l'event loop."""
    # [v4.11-B3] Guard kill-switch: place_order non deve mai eseguire durante kill.
    # In precedenza girava comunque perché il check era solo nel main loop,
    # non nei singoli path che chiamano place_order (stoploss, arb, ecc).
    print(f"[DBG_PO] kill={time.time()<STATE.kill_until} dry={DRY_RUN} clob={CLOB_AVAILABLE}", flush=True)
    if time.time() < STATE.kill_until:
        if strategy == "STOPLOSS" and side == "SELL":
            log.warning(f"PLACE_ORDER STOPLOSS bypass kill-switch {side} token={token_id[:12]}")  # [BUG2-FIX]
        else:
            log.debug(f"PLACE_ORDER BLOCKED (kill active) {strategy} {side} token={token_id[:12]}")
            return

    if DRY_RUN:
        log.info(f"[DRY] {strategy} {side} {city.upper()} "
                 f"token={token_id[:12]} p={price:.4f} notional=${size:.2f}")
        return False
    if not CLOB_AVAILABLE: return False

    # [v4.6-audit4] HARD Becker safety — ultimo filtro di difesa
    if strategy != "STOPLOSS":
        if side == "BUY" and price > BECKER_BUY_ZONE_MAX + 0.01:
            log.warning(f"BECKER HARD BLOCK: BUY price={price:.3f} > {BECKER_BUY_ZONE_MAX} "
                         f"token={token_id[:12]} strategy={strategy}")
            return False
        if side == "SELL" and price < BECKER_SELL_ZONE_MIN - 0.01:
            log.warning(f"BECKER HARD BLOCK: SELL price={price:.3f} < {BECKER_SELL_ZONE_MIN} "
                         f"token={token_id[:12]} strategy={strategy}")
            return

    chunks = [size/2, size/2] if size>50 else [size]
    _any_ok = False  # [C1-FIX] track if at least one chunk submitted
    for chunk in chunks:
        # [v4.12-S3] Recupera neg_risk e tick_size dalla cache TOKEN_META.
        _meta      = TOKEN_META.get(token_id, {})
        _neg_risk  = _meta.get("neg_risk", True) or True  # [FIX-SELL] temp markets always negRisk
        _tick_size = str(_meta.get("tick_size", "0.01"))

        # [v4.22-fix-ticksize] Dynamic truncation to tick_size precision (replaces static round 2)
        _tick_precision = len(_tick_size.split('.')[-1]) if '.' in _tick_size else 0
        _tick_unit = 10 ** _tick_precision
        raw_contracts = (chunk / price) if price > 0 else 0
        contracts = math.floor(raw_contracts * _tick_unit) / _tick_unit

        # [v4.12-S5] Minimum order size
        if (contracts < 5 or chunk < 1.0) and strategy != "STOPLOSS":  # [BUG2-FIX] stoploss bypasses min-size
            log.info(f"ORDER SKIP {strategy} {side} {city.upper()} "
                     f"token={token_id[:12]}: contracts={contracts:.1f} notional=${chunk:.2f}")
            continue

        # [v4.9-fix] Guard cancel-race: aspetta conferma cancel CLOB prima di place.
        # [v4.13-IL3] await per non bloccare l'event loop durante l'attesa.
        print("[DBG8] cancel_guard", flush=True)
        await _cancel_guard_wait(token_id)
        try:
            # [v4.12-S1] CREATE_ORDER con CreateOrderOptions(neg_risk, tick_size).
            # CRITICO per i SELL: senza neg_risk=True su mercati neg_risk, il client
            # firma con l'exchange contract sbagliato → "invalid signature" → SELL fallisce.
            # Il vecchio codice non passava nulla → il client chiamava get_neg_risk(token_id)
            # via HTTP che poteva fallire silenziosamente → firma errata.
            # Con neg_risk=True esplicito evitiamo la HTTP call e la firma è sempre corretta.
            if CreateOrderOptions is not None:
                _order_options = CreateOrderOptions(
                    neg_risk=_neg_risk,
                    tick_size=_tick_size,
                )
                order = await asyncio.get_running_loop().run_in_executor(None, lambda: client.create_order(  # [v4.24-create-async]
                    OrderArgs(token_id=token_id,
                              price=round(price, 4),
                              size=contracts,
                              side=side),
                    _order_options))
            else:
                # Fallback per versioni vecchie di py_clob_client senza CreateOrderOptions
                order = await asyncio.get_running_loop().run_in_executor(None, lambda: client.create_order(  # [v4.24-create-async]
                    OrderArgs(token_id=token_id,
                              price=round(price, 4),
                              size=contracts,
                              side=side)))
            log.debug(f"CREATE_ORDER {side} neg_risk={_neg_risk} tick={_tick_size} "
                      f"token={token_id[:12]} contracts={contracts:.2f}")

            # [v4.12-S2] POST_ORDER con OrderType esplicito GTC.
            # Senza di esso il client usa il default interno che può variare tra versioni.
            print("[DBG11] post_order", flush=True)
            resp = await _async_clob(client.post_order, order, _ORDER_TYPE_GTC, timeout=25.0)  # [C2-FIX]
            print(f"[API_DATA] Token: {token_id[:10]} | Resp: {resp}", flush=True)

            resp_str = str(resp) if resp else ""
            if isinstance(resp, dict):
                err_msg = str(resp.get("errorMsg", "") or "").strip()
                # [v4.11-B4] Fix order_ok: il CLOB risponde con takingAmount='' quando
                # l'ordine è LIVE nel book (limit non ancora matchato) — non è un reject.
                # Il vecchio codice loggava questi come rejected basandosi su takingAmount,
                # producendo 44 falsi rejected che il SelfMonitor non contava.
                # Ora: orderID valido + nessun errorMsg = submitted OK, indipendentemente
                # da takingAmount. Lo status ('live','matched') è informativo, non di errore.
                order_id  = str(resp.get("orderID") or resp.get("id") or "").strip()
                order_ok  = bool(order_id) and err_msg == ""
                order_status = str(resp.get("status") or "").lower()  # 'live' | 'matched' | ''
            else:
                order_ok     = resp is not None and resp_str != ""
                order_id     = ""
                order_status = ""
                err_msg      = ""
            
            # [v4.11-B2] Exposure log usa STATE.total_exposure, non il globale stale
            state_exp = STATE.total_exposure
            status_tag = "✅" if order_ok else "❌"
            log.info(f"ORDER {status_tag} {strategy} {side} {city.upper()} "
                     f"token={token_id[:12]} price={price:.4f} "
                     f"contracts={contracts:.2f} notional=${chunk:.2f} "
                     f"state_exposure={state_exp:.2f} status={order_status or 'n/a'} | "
                     f"{'ok='+order_id[:16] if order_ok else 'err='+err_msg if err_msg else resp_str[:60]}")
            
            EVENTS.order_submitted(
                token_id=token_id, side=side, price=price,
                size=contracts, notional=chunk, city=city,
                strategy=strategy, order_resp=resp)
            
            if order_ok:
                # [v4.24-fix-exposure-leak] Aggiorna exposure SOLO su ordine confermato
                # BUG v4.22/v4.23: += era prima di order_ok → ordini rifiutati gonfiavano exposure
                STATE.add_exposure(contracts * price)
                record_fill_direction(token_id, side)
                if order_id:
                    STATE.record_order(order_id, token_id, side, price, contracts, chunk, city, strategy)
                _any_ok = True  # [C1-FIX] at least one chunk submitted
            else:
                EVENTS.order_rejected(token_id, side,
                    err_msg if isinstance(resp, dict) else resp_str[:200])
                # [C3-FIX] CLOB balance desync → re-sync exposure so kill-switch
                # sees real capital, not stale allowance ghost
                _err_lower = (err_msg or resp_str).lower()
                if "not enough balance" in _err_lower or "allowance" in _err_lower:
                    log.warning(f"ALLOWANCE DESYNC {strategy} {side} — re-syncing exposure")
                    try:
                        await sync_exposure_from_positions(client)
                    except Exception as _se:
                        log.warning(f"SYNC EXPOSURE FAILED: {_se}")

        except Exception as e:
            log.error(f"ORDER ERROR {strategy} {side} {city.upper()} "
                      f"token={token_id[:12]} price={price:.4f}: {e}")
            EVENTS.order_rejected(token_id, side, str(e))

    return _any_ok  # [C1-FIX] True if ≥1 chunk submitted to CLOB

async def get_live_inventory(client, token_id: str) -> float:  # [v4.21-async-positions]
    try:
        pos = await _async_clob(client.get_position, token_id)
        if pos is not None:
            q = float(pos); STATE._local_positions[token_id]=q; return q
    except Exception: pass
    try:
        for p in await get_positions_safe(client):  # [v4.21-async-positions]
            tid = str(p.get("asset") or p.get("token_id") or "")
            if tid==token_id:
                q=float(p.get("size") or 0); STATE._local_positions[token_id]=q; return q
    except Exception: pass
    return STATE._local_positions.get(token_id,0.0)

# ══════════════════════════════════════════════════════════════════
# [v4.9-fix] CANCEL WITH CONFIRMATION
# Risolve il CLOB desync: aspetta che il cancel sia processato dal
# backend prima di piazzare un nuovo ordine sullo stesso token.
# Causa del bug: cancel(A) + place(B) a < 2s di gap → fill va su A
# (già locale-cancellato) → shares orfane: Gamma le vede, CLOB no.
# ══════════════════════════════════════════════════════════════════
async def cancel_orders_for_token(client, token_id: str) -> int:

    if not client or not hasattr(client, 'get_orders'): return 0
    if STATE.total_exposure == 0.0: return 0  # [v4.24-cancel-skip]

    try:
        all_orders = await _async_clob(client.get_orders) or []
    except Exception as e:
        STATE._last_cancel_ts[token_id] = time.time()
        return 0

    to_cancel = []
    cancel_notional = 0.0
    for o in all_orders:
        if str(o.get("asset_id") or o.get("token_id") or "") == token_id:
            o_id = str(o.get("id") or o.get("orderId") or "")
            if o_id:
                to_cancel.append(o_id)
                o_size      = float(o.get("original_size") or o.get("size") or 0)
                o_matched   = float(o.get("size_matched") or 0)
                o_remaining = max(0.0, o_size - o_matched)
                o_price     = float(o.get("price") or 0)
                cancel_notional += o_remaining * o_price

    if not to_cancel: return 0

    for o_id in to_cancel:
        try:
            await _async_clob(client.cancel, o_id)
            log.debug(f"CANCEL SENT {o_id[:12]}")
        except Exception:
            pass

    STATE.remove_exposure(cancel_notional)
    STATE._last_cancel_ts[token_id] = time.time()
    return len(to_cancel)

async def _cancel_guard_wait(token_id: str):
    """[v4.13-IL3] Coroutine async: aspetta se non è passato abbastanza tempo dall'ultimo cancel.
    Usa await asyncio.sleep invece di time.sleep per non bloccare l'event loop.
    Usata in place_order() come guardia di sicurezza finale.
    """
    last_cancel = STATE._last_cancel_ts.get(token_id, 0.0)
    if last_cancel == 0.0:
        return  # nessun cancel recente su questo token

    elapsed = time.time() - last_cancel
    if elapsed < CANCEL_MIN_WAIT:
        wait = CANCEL_MIN_WAIT - elapsed
        log.debug(f"CANCEL_GUARD {token_id[:12]}: aspetto {wait:.2f}s prima di place")
        await asyncio.sleep(wait)


# ══════════════════════════════════════════════════════════════════
# KILL-SWITCH + P&L (CRITICAL-3: P&L reale)
# ══════════════════════════════════════════════════════════════════
async def update_pnl_from_fills(client):  # [v4.17-fix-pnl] converted to async
    """Legge l'activity API e registra fill confermati nel SelfMonitor.

    [v4.11-B7] record_trade() veniva chiamato subito dopo place_order,
    prima della conferma fill. Risultato: 266 ordini inviati, 7 registrati
    nel monitor (solo quelli con forecast valido), win_rate sempre 0%,
    autokill mai triggered. Ora record_trade è chiamato QUI, solo su
    transazioni confermate dall'activity API.

    [v4.14-M2] Paginazione: con limit=50 in fase attiva (>50 fill/intervallo)
    si perdono eventi più vecchi, sporcando PnL e auto-tuning. Ora paginiamo
    finché non troviamo solo trade già visti (_last_trade_ids). Cap memoria a
    2000 ID per evitare crescita illimitata del set.
    """
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()  # [v4.19-fix-funder]
    if not funder:
        log.warning("PNL UPDATE SKIP: PM_FUNDER_KEY mancante"); return
    try:
        PAGE_SIZE  = 50
        MAX_PAGES  = 5   # max 250 eventi per ciclo — oltre è anomalo
        new_trades = []

        for _page in range(MAX_PAGES):
            # [v4.14-M2] Cursore: il campo "before" della Polymarket API accetta
            # un ID numerico (o timestamp) per paginare verso il passato.
            # Al primo giro non usiamo cursore; ai giri successivi usiamo l'ID
            # minimo tra i trade già letti in questa sessione di fetch.
            params: dict = {"user": funder, "limit": PAGE_SIZE}
            if _page > 0 and new_trades:
                # Paginazione verso il passato: usa il timestamp del più vecchio
                oldest_ts = new_trades[-1].get("timestamp") or new_trades[-1].get("ts") or ""
                if oldest_ts:
                    params["before"] = oldest_ts

            # [v4.17-fix-pnl] Non-blocking: uses _async_get (run_in_executor) — properly awaited
            resp = await _async_get("https://data-api.polymarket.com/activity",
                                    params=params, timeout=10)
            if resp is None:
                break  # network error: stop pagination gracefully
            if resp.status_code in (401, 403):
                log.error(f"PNL AUTH {resp.status_code}"); return
            resp.raise_for_status()
            page_trades = resp.json()
            if not page_trades or not isinstance(page_trades, list):
                break

            all_known = True
            for t in page_trades:
                tid = str(t.get("id") or t.get("txHash") or t.get("transactionHash") or "")  # [API-FIX] API uses transactionHash not txHash
                if not tid: continue
                if tid in STATE._last_trade_ids:
                    continue  # già processato
                all_known = False
                new_trades.append(t)

            if all_known or len(page_trades) < PAGE_SIZE:
                break  # nessun nuovo trade in questa pagina, stop

        # [v4.14-M2] Cap memoria: mantieni al massimo 2000 ID per evitare leak
        if len(STATE._last_trade_ids) > 2000:
            STATE._last_trade_ids = set(list(STATE._last_trade_ids)[-1000:])

        if not new_trades:
            return

        log.debug(f"PNL: {len(new_trades)} nuovi fill da processare")
        for t in new_trades:
            tid = str(t.get("id") or t.get("txHash") or t.get("transactionHash") or "")  # [API-FIX2] both loops need transactionHash
            if not tid: continue
            STATE._last_trade_ids.add(tid)
            token_id_fill = str(t.get("asset_id") or t.get("token_id") or t.get("asset") or "")  # [P1D-FIX] moved before cid cache; API uses "asset" not "asset_id"
            # [P1D] Cache condition_id for ledger
            _act_cid = str(t.get("conditionId", "") or t.get("condition_id", "") or "")
            if _act_cid:
                if not hasattr(STATE, '_fill_cid_cache'): STATE._fill_cid_cache = {}
                STATE._fill_cid_cache[token_id_fill] = _act_cid  # [P1D-FIX] now uses correct key

            price  = float(t.get("price") or 0)
            size   = float(t.get("size")  or 0)
            side   = str(t.get("side") or t.get("type") or "BUY").upper()
            outcome = str(t.get("outcome") or t.get("resolution") or "").upper()

            # [v4.11-B7] Registra fill confermato nel SelfMonitor.
            # Usiamo i metadati che STATE conserva per ogni ordine submitted
            # per ricostruire strategy/city/bucket — se non disponibili usiamo
            # valori di default ("MM", "london").
            if token_id_fill and price > 0 and size > 0:
                # [SEV-1 HOTFIX] Becker Guard su DCA fills — blocca fill fuori zona
                if BECKER_FILTER_ENABLED:
                    if not (BECKER_BUY_ZONE_MIN <= price <= BECKER_BUY_ZONE_MAX or
                            BECKER_SELL_ZONE_MIN <= price <= BECKER_SELL_ZONE_MAX):
                        log.warning(f"DCA BECKER BLOCK: price={price:.4f} outside zones, token={token_id_fill[:12]}")
                        continue

                order_meta = STATE.get_order_meta(token_id_fill) if hasattr(STATE, 'get_order_meta') else {}
                strategy_fill = order_meta.get("strategy", "MM")
                city_fill     = order_meta.get("city", "london")
                bucket_fill   = order_meta.get("bucket_key", "fill_confirmed")
                T_fill        = order_meta.get("T", 1.0)
                edge_fill     = getattr(MONITOR, '_edge_cache', {}).get(token_id_fill, order_meta.get("edge", 0.0))  # [EDGE]
                if side == "BUY":  # [CB-v1-fix] solo BUY, no SELL pollution
                    _cb_city_key = "LONDON" if "lon" in city_fill.lower() else "SEOUL"
                    if _cb_city_key in _edge_decay_window:
                        _edge_decay_window[_cb_city_key].append(edge_fill)
                # [QUANT] read full evaluation snapshot captured at order time
                _qm = _quant_meta_cache.get(token_id_fill, {})
                MONITOR.record_trade(token_id_fill, strategy_fill, city_fill,
                                     bucket_fill, side, price, size * price,
                                     T_fill, edge_fill, fill_id=tid,
                                     fair_prob=_qm.get("fair_prob", 0.0),
                                     mkt_prob=_qm.get("mkt_prob", 0.0),
                                     kelly_frac=_qm.get("kelly_frac", 0.0),
                                     sizing_mode=_qm.get("sizing_mode", "kelly"))  # [QUANT]
                log.debug(f"FILL CONFIRMED: {side} {token_id_fill[:12]} p={price:.4f} sz={size:.0f} strategy={strategy_fill}")

            # P&L diretto
            pnl_d = t.get("profit") or t.get("pnl") or t.get("realizedPnl")
            if pnl_d is not None:
                # [PHASE1A] DEPRECATED: direct pnl_d accumulation removed — use ledger
                continue
            # Outcome risolto
            if not price or not size: continue
            # [PHASE1A] DEPRECATED: outcome-based accumulation removed — use ledger
            pass
            # [v4.3-fix1] Aggiorna SelfMonitor con risoluzione
            if token_id_fill and outcome in ("YES","1","WIN","NO","0","LOSE"):
                resolved_yes = outcome in ("YES","1","WIN")
                MONITOR.record_resolution(token_id_fill, resolved_yes)
        log.info(f"PNL: daily_state={STATE.daily_pnl:.4f} (ledger-sourced on next save)")
    except Exception as e:
        log.warning(f"PNL ERROR: {e}")

# ══════════════════════════════════════════════════════════════════
# AUTO-CAPITAL: legge il capitale reale da API Polymarket
# ══════════════════════════════════════════════════════════════════
# [TGT1] _last_capital_fetch -> STATE._last_capital_fetch
# [TGT1] _last_capital_value -> STATE._last_capital_value
CAPITAL_FETCH_INTERVAL = 60.0  # secondi tra un fetch e l'altro

async def fetch_portfolio_capital(client, funder: str = "", force: bool = False) -> Optional[float]:  # [v4.21-async-capital]
    """Legge il capitale totale (cash + posizioni) da Polymarket.
    Ritorna None se non riesce a leggere nulla di affidabile.
    Rate-limited a 1 chiamata ogni CAPITAL_FETCH_INTERVAL secondi.
    Metodo 1: data-api portfolio endpoint.
    Metodo 2: somma posizioni live da CLOB client.
    """
    now = time.time()
    if not force and now - STATE._last_capital_fetch < CAPITAL_FETCH_INTERVAL:
        return STATE._last_capital_value
    if not funder:
        funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()  # [v4.19-fix-funder]
    if not funder:
        return STATE._last_capital_value

    STATE._last_capital_fetch = now

    # [v4.7-fix3] Metodo 1: py_clob_client balance (non blocca asyncio)
    if client and CLOB_AVAILABLE:
        try:
            # get_balance_allowance ritorna {"balance": "101.36", ...}
            if hasattr(client, 'get_balance_allowance'):
                from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                bal_resp = await _async_clob(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                if bal_resp and isinstance(bal_resp, dict):
                    real_cap = float(bal_resp.get("balance", 0) or 0)
                    # balance è in wei (6 decimali USDC) — converti se necessario
                    if real_cap > 1e6:
                        real_cap = real_cap / 1e6
                    if real_cap > 0:
                        log.info(f"CAPITAL CLOB BALANCE: ${real_cap:.2f}")
                        STATE._last_capital_value = real_cap
                        return real_cap
        except Exception as e:
            if STATE._last_capital_value > 0:
                log.warning(f"CAPITAL CLOB ERROR (cached ${STATE._last_capital_value:.2f}): {e}")
                return STATE._last_capital_value  # [BUG1-FIX] prevent false DD spike
            log.debug(f"CAPITAL CLOB ERROR: {e}")

    # [v4.7-fix3] Metodo 2: data-api/value (fallback — ritorna pos value, non cash)
    try:
        # [v4.21-async-capital] Converted to async: await _async_get instead of blocking requests.get
        r = await _async_get(
            f"https://data-api.polymarket.com/value?user={funder}",
            timeout=8)
        if r is not None and r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            if isinstance(data, dict):
                val = data.get("value")
                if val is not None:
                    real_cap = float(val)
                    if real_cap > 0:
                        log.info(f"CAPITAL API (value, pos only): ${real_cap:.2f}")
                        STATE._last_capital_value = real_cap
                        return real_cap
    except Exception as e:
        log.debug(f"CAPITAL API ERROR: {e}")

    # ── Metodo 3: somma posizioni da CLOB client ── [v4.15: precedentemente Metodo 2 duplicato]
    if client:
        try:
            positions = await get_positions_safe(client)  # [v4.21-async-capital]
            pos_value = 0.0
            for p in positions:
                size  = float(p.get("size") or p.get("quantity") or 0)
                price = float(p.get("price") or p.get("avg_price") or 0)
                pos_value += size * price
            if pos_value > 0:
                log.info(f"CAPITAL POSITIONS FALLBACK: ${pos_value:.2f} (solo posizioni, no cash)")
                STATE._last_capital_value = pos_value
                return pos_value
        except Exception as e:
            log.warning(f"CAPITAL POSITIONS ERROR: {e}")

    return STATE._last_capital_value

# ══════════════════════════════════════════════════════════════════
# AUTO-REDEEM: riscatta posizioni su mercati risolti
# ══════════════════════════════════════════════════════════════════
# [TGT1] _last_redeem_check -> STATE._last_redeem_check
REDEEM_CHECK_INTERVAL = 3600.0  # [DEBUG] temporaneo  # controlla ogni 5 minuti
# [v4.11-B10] Set di conditionId già riscattati in questa sessione.
# Previene il loop bug: stessa posizione loggata 3x in 25 minuti
# perché REDEEM_CHECK_INTERVAL=300s non era rispettato (ora sì, ma
# il set garantisce idempotenza anche se l'intervallo dovesse slittare).
# [TGT1] _redeemed_condition_ids -> STATE._redeemed_condition_ids


def _ctf_payout_denominator(condition_id: str) -> int:
    # [v4.28-fix] Check on-chain payoutDenominator. 0=unresolved, >0=resolved.
    try:
        from web3 import Web3 as _W3
        _w3 = _W3(_W3.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))
        _CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        _sel = _w3.keccak(text="payoutDenominator(bytes32)").hex()[:10]
        _cid = condition_id[2:] if condition_id.startswith("0x") else condition_id
        _raw = _w3.eth.call({"to": _CTF, "data": _sel + _cid})
        return int(_raw.hex(), 16)
    except Exception:
        return -1  # uncertain, allow attempt

async def auto_redeem_resolved():  # [v4.21-async-redeem]
    """Controlla posizioni redeemable e le riscatta via CLI polymarket.
    
    [v4.11-B6] Fix field name: l'API /positions restituisce 'conditionId'
    con la C maiuscola. Il CLI vuole --condition <conditionId>.
    In precedenza il codice cercava il campo correttamente (conditionId) ma
    i 12 errori 'required argument --condition' suggeriscono che alcuni
    oggetti position usano un nome diverso. Ora si provano tutti i candidati
    noti e si logga il JSON grezzo per diagnostica se nessuno funziona.
    
    [v4.11-B10] Dedup: conditionId già riscattati vengono skippati.
    """
    now = time.time()
    if now - STATE._last_redeem_check < REDEEM_CHECK_INTERVAL:
        return
    STATE._last_redeem_check = now

    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()  # [v4.19-fix-funder]
    if not funder:
        return

    try:
        # [v4.15-fix9] requests.get sincrono — auto_redeem_resolved e chiamata fuori await
        r = await _async_get(  # [v4.21-async-redeem]
            f"https://data-api.polymarket.com/positions?user={funder}",
            timeout=10)
        if r is None or r.status_code != 200:
            return
        positions = r.json()
        if not isinstance(positions, list):
            return

        redeemable = [p for p in positions if p.get("redeemable", False)]
        if not redeemable:
            return

        total_value = sum(float(p.get("currentValue", 0)) for p in redeemable)

        # [v4.11-B6] Prova tutti i nomi di campo conditionId noti nell'API Polymarket.
        # L'API è inconsistente tra endpoint: /positions usa 'conditionId',
        # ma alcune versioni usano 'condition_id' o 'market_id'.
        def extract_condition_id(p: dict) -> str:
            for key in ("conditionId", "condition_id", "market_condition_id", "marketConditionId"):
                val = p.get(key, "")
                if val:
                    return str(val)
            # Fallback: logga il JSON grezzo per diagnostica
            log.warning(f"REDEEM: conditionId non trovato in position: {json.dumps(p)[:200]}")
            return ""

        # [v4.11-B10] Filtra già riscattati
        to_redeem = []
        for p in redeemable:
            cid = extract_condition_id(p)
            if cid and cid not in STATE._redeemed_condition_ids:
                to_redeem.append(cid)

        if not to_redeem:
            log.debug("REDEEM: nessuna nuova condition da riscattare")
            return

        log.info(f"REDEEM: {len(to_redeem)} nuove conditions, valore ~${total_value:.2f}")

        cli_env = {**os.environ}
        for key in ("PM_PRIVATE_KEY", "PM_FUNDER_KEY", "PM_API_KEY",
                     "PM_API_SECRET", "PM_API_PASSPHRASE"):
            val = os.getenv(key, "")
            if val:
                cli_env[key] = val

        # [v4.15-fix10] Usa py_clob_client nativo invece del CLI (binario non disponibile,
        # cwd ~/polymarket-temp-bot non esisteva -> FileNotFoundError silenzioso 13x/ciclo)
        redeemed_count = 0
        _redeem_client = None
        if CLOB_AVAILABLE:
            try:
                from py_clob_client.client import ClobClient as _CC
                _pk2 = os.getenv("PM_PRIVATE_KEY", "").strip()
                if _pk2:
                    _redeem_client = _CC("https://clob.polymarket.com", key=_pk2,
                                        chain_id=POLYGON,
                                        funder=RESOLVED_FUNDER or funder)  # [v4.21-fix-redeem-funder]
            except Exception as _e:
                log.debug(f"REDEEM CLIENT INIT: {_e}")
        for cid in to_redeem:
            try:
                # [v4.28-fix-redeem-guard] On-chain guard: skip if not yet resolved
                _denom = _ctf_payout_denominator(cid)
                if _denom == 0:
                    log.warning(f"REDEEM SKIP {cid[:16]}: payoutDenominator=0 (UMA pending, NOOP)")
                    continue
                if _redeem_client and hasattr(_redeem_client, "redeem_positions"):
                    await _async_clob(_redeem_client.redeem_positions, condition_id=cid)
                    redeemed_count += 1
                    STATE._redeemed_condition_ids.add(cid)
                    log.info(f"REDEEM OK (py_clob) {cid[:16]}")
                else:
                    # Fallback: CLI con path robusto — [v4.21-async-redeem] wrapped in executor
                    _cwd = str(_BOT_DIR)  # [TGT3] dynamic path
                    import functools as _ft
                    _run_fn = _ft.partial(
                        subprocess.run,
                        ["polymarket", "--signature-type", "eoa", "ctf", "redeem",
                         "--condition", cid],
                        capture_output=True, text=True, timeout=60,
                        stdin=subprocess.DEVNULL, cwd=_cwd, env=cli_env
                    )
                    result = await asyncio.get_running_loop().run_in_executor(None, _run_fn)
                    # [v4.28-fix-false-positive] exit=0 with payout=0 is NOT success
                    _stdout_ok = result.returncode == 0 and (
                        "payout" in result.stdout.lower() or
                        "redeemed" in result.stdout.lower() or
                        "0x" in result.stdout
                    )
                    if _stdout_ok:
                        redeemed_count += 1
                        STATE._redeemed_condition_ids.add(cid)
                        log.info(f"REDEEM OK (cli) {cid[:16]}: {result.stdout[:100]}")
                    elif result.returncode == 0:
                        log.warning(f"REDEEM NOOP {cid[:16]}: exit=0 no payout signal stdout={result.stdout[:80]}")
                    else:
                        log.warning(f"REDEEM FAIL {cid[:16]}: {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                log.warning(f"REDEEM TIMEOUT {cid[:16]}")
            except Exception as e:
                log.warning(f"REDEEM ERROR {cid[:16]}: {e}")

        if redeemed_count > 0:
            EVENTS.position_redeemed(redeemed_count, f"{redeemed_count}/{len(to_redeem)} redeemed")
            log.info(f"REDEEM COMPLETE: {redeemed_count}/{len(to_redeem)}")

    except Exception as e:
        log.warning(f"REDEEM ERROR: {e}")

# ══════════════════════════════════════════════════════════════════
# STOP-LOSS PER POSIZIONE — [v4.6-fix7]
# ══════════════════════════════════════════════════════════════════


def get_best_bid_for_token(token_id: str, client):
    """[v4.19-exit] Returns best bid price from WS_BOOK cache or REST fallback."""
    ws_entry = WS_BOOK.get(token_id)
    if ws_entry and time.time() - ws_entry.get("ts", 0) < 30:
        bids = ws_entry.get("bids", [])
        if bids:
            try:
                return float(bids[0][0])
            except (TypeError, ValueError, IndexError):
                pass
    if client:
        try:
            book = client.get_order_book(token_id)
            if book and book.bids:
                return float(book.bids[0].price)
        except Exception:
            pass
    return None


# [TGT1] _last_position_exit_check -> STATE._last_position_exit_check
POSITION_EXIT_INTERVAL = cfg("risk", "POSITION_EXIT_INTERVAL", 120.0)
TAKE_PROFIT_PCT        = cfg("risk", "TAKE_PROFIT_PCT",        0.40)
PARTIAL_PROFIT_PCT     = cfg("risk", "PARTIAL_PROFIT_PCT",     0.20)


async def check_position_exit(client):
    """[v4.19-exit] Smart position exit: Kelly partial + full take-profit + time exit.
    # [99C-RISK-PATCH] Tier -1: Forced exit at >=99c pre-resolution
    if cur_price >= 0.99 and not p.get("redeemable", False):
        exit_fraction = 1.0
        exit_tier = "TIER_MINUS1_99C"
        EVENTS.append({"ts": time.time(), "event": "FORCE_99C_EXIT", "slug": p.get("slug"), "outcome": p.get("outcome"), "price": cur_price, "size": p.get("size", 0), "reason": "pre_resolution_capital_protection"})
        _FORCED_EXIT_99C.add(p.get("token_id", p.get("slug", "")))
        return exit_fraction, exit_tier, pnl_pct, days_held
    

    Tier 0: T < MIN_T_HOURS AND curPrice > 0.01 -> emergency full exit
    Tier 1: pnl >= TAKE_PROFIT_PCT*100          -> full take-profit exit
    Tier 2: pnl >= PARTIAL_PROFIT_PCT*100 AND T > 6h -> Kelly partial exit
    Tier 3: hold
    Kelly fraction: f = pnl/(1+pnl), capped [0.25, 0.75].
    Guard: only Tier 2 if size > totalBought * 0.55 (not already reduced).
    """
    now = time.time()
    if now - STATE._last_position_exit_check < POSITION_EXIT_INTERVAL:
        return
    STATE._last_position_exit_check = now
    if not client and not DRY_RUN:
        return
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()
    if not funder:
        return
    try:
        resp = await _async_get(
            f"https://data-api.polymarket.com/positions?user={funder}",
            timeout=10)
        if resp is None or resp.status_code != 200:
            return
        positions = resp.json()
        if not isinstance(positions, list):
            return
        markets_by_slug: dict = {}
        try:
            all_markets = await fetch_markets_async()
            for _m in all_markets:
                _sl = _m.get("slug", "")
                if _sl:
                    markets_by_slug[_sl] = _m
        except Exception as _me:
            log.warning(f"POSITION_EXIT: market fetch failed: {_me}")
        for p in positions:
            if p.get("redeemable", False):
                continue
            pnl_pct      = float(p.get("percentPnl", 0))
            cur_value    = float(p.get("currentValue", 0))
            size         = float(p.get("size", 0))
            cur_price    = float(p.get("curPrice", 0))
            token_id     = str(p.get("asset", ""))
            title        = p.get("title", "???")
            slug         = p.get("slug", "")
            total_bought = float(p.get("totalBought", size))
            if cur_value < 0.05 or cur_price < 0.005 or size < 0.5:
                continue
            T_hours = MIN_T_HOURS + 1.0
            market_obj = markets_by_slug.get(slug)
            if market_obj:
                T_hours = hours_to_resolution(market_obj)
            else:
                _ev_slug = p.get("eventSlug", "")
                if _ev_slug:
                    for _mv in markets_by_slug.values():
                        if _mv.get("slug", "").startswith(_ev_slug):
                            T_hours = hours_to_resolution(_mv)
                            break
            city = "seoul" if "seoul" in title.lower() else "london"
            exit_fraction = 0.0
            exit_tier = ""
            if T_hours < MIN_T_HOURS and cur_price > 0.01:
                exit_fraction = 1.0
                exit_tier = f"TIER0_TIME T={T_hours:.2f}h<{MIN_T_HOURS}h"
            elif pnl_pct >= TAKE_PROFIT_PCT * 100:
                _consecutive_losses = 0  # [CB-v1] reset on profit
                exit_fraction = 1.0
                exit_tier = f"TIER1_PROFIT pnl={pnl_pct:.1f}%>={TAKE_PROFIT_PCT*100:.0f}%"
            elif T_hours < 12.0 and cur_price > 0.01 and not p.get("redeemable", False):
                # [v4.27-D] TIER1B_DECAY: exit badly decayed losers before resolution
                _avg_entry = float(p.get("avgPrice", 0)) or (total_bought / max(size, 1e-9))
                if _avg_entry > 0 and cur_price < _avg_entry * 0.40:
                    exit_fraction = 1.0
                    exit_tier = (f"TIER1B_DECAY cur={cur_price:.4f} avg={_avg_entry:.4f} "
                                 f"ratio={cur_price/_avg_entry:.2f} T={T_hours:.1f}h")
            elif pnl_pct >= PARTIAL_PROFIT_PCT * 100 and T_hours > 6.0:
                already_reduced = size < total_bought * 0.55
                if not already_reduced:
                    kelly_f = min(0.75, max(0.25,
                        (pnl_pct / 100.0) / (1.0 + pnl_pct / 100.0)))
                    exit_fraction = kelly_f
                    exit_tier = f"TIER2_PARTIAL pnl={pnl_pct:.1f}% kelly_f={kelly_f:.2f} T={T_hours:.1f}h"
            if exit_fraction <= 0:
                continue
            exit_size = round(size * exit_fraction, 2)
            # [PATCH2] Safe floor: prevent CLOB reject from float precision mismatch
            if exit_fraction >= 0.99:
                exit_size = math.floor(exit_size * 100) / 100
            if exit_size < 5.0:  # [v4.31-fix2] CLOB rejects < 5 contracts
                continue
            sell_price = get_best_bid_for_token(token_id, client)
            if sell_price and sell_price > 0.01:
                sell_price = max(0.01, round(sell_price - 0.001, 4))
            else:
                sell_price = max(0.01, round(cur_price - 0.005, 4))
            sell_price = min(sell_price, 0.9899)  # [99C-FIX] cap for neg-risk CLOB
            sell_notional = exit_size * sell_price
            if DRY_RUN:
                log.info(f"[DRY] POSITION_EXIT [{exit_tier}] {title[:45]} "
                         f"size={exit_size:.2f}@{sell_price:.4f} notional=${sell_notional:.2f}")
                continue
            log.warning(
                f"POSITION_EXIT [{exit_tier}] {title[:45]}: "
                f"size={exit_size:.2f}/{size:.2f} @ {sell_price:.4f} = ${sell_notional:.2f}")
            _exit_ok = await place_order(client, token_id, "SELL", sell_price, sell_notional,
                              city=city, strategy="STOPLOSS")  # [C1-FIX]
            if not _exit_ok:
                log.warning(f"POSITION_EXIT NOT SUBMITTED [{exit_tier}] {title[:40]}")
                # [v4.31-fix3] Mark pending to prevent hammering on balance desync
                STATE._sl_pending[token_id] = time.time()
    except Exception as _e:
        log.warning(f"POSITION_EXIT ERROR: {_e}")

# [TGT1] _last_stoploss_check -> STATE._last_stoploss_check
STOPLOSS_CHECK_INTERVAL = 120.0  # controlla ogni 2 minuti

async def check_position_stoploss(client):
    """Controlla posizioni aperte e vende quelle in perdita > STOPLOSS_POSITION_PCT.
    
    [v4.9-fix] Rimosso lo skip neg-risk: il problema non era neg-risk (le shares
    si possono vendere), ma il CLOB desync causato dal cancel-race condition.
    Con il fix cancel_orders_for_token(), il CLOB ora è allineato e i SELL
    funzionano anche su mercati neg-risk.
    
    Aggiunta verifica CLOB balance prima di inviare il SELL: se il CLOB non vede
    le shares (desync residuo), logga un warning invece di inviare un SELL che
    fallirebbe con 'not enough balance'.
    """
    now = time.time()
    if now - STATE._last_stoploss_check < STOPLOSS_CHECK_INTERVAL:
        return
    STATE._last_stoploss_check = now

    if DRY_RUN or not client:
        return

    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()  # [v4.19-fix-funder]
    if not funder:
        return

    try:
        # [v4.15-fix9b] Async function: usa run_in_executor per non bloccare event loop
        loop = asyncio.get_running_loop()  # [TGT2]
        import functools as _ft
        _rfn = _ft.partial(requests.get,
            f"https://data-api.polymarket.com/positions?user={funder}",
            timeout=10)
        r = await loop.run_in_executor(None, _rfn)
        if r.status_code != 200:
            return
        positions = r.json()
        if not isinstance(positions, list):
            return

        for p in positions:
            # Skip posizioni già risolte o redeemable
            if p.get("redeemable", False):
                continue

            pnl_pct    = float(p.get("percentPnl", 0))
            cur_value  = float(p.get("currentValue", 0))
            size       = float(p.get("size", 0))
            cur_price  = float(p.get("curPrice", 0))
            token_id   = str(p.get("asset", ""))
            title      = p.get("title", "???")

            # Skip se la posizione è troppo piccola o il prezzo è 0
            if cur_value < 0.10 or cur_price < 0.005 or size < 1:
                continue

            # Stoploss: vendi se perdita > STOPLOSS_POSITION_PCT
            if pnl_pct < -(STOPLOSS_POSITION_PCT * 100):
                # [CB-v1] ConsecutiveLossGuard counter
                global _consecutive_losses, _cb_loss_blocked_until
                if time.time() >= _cb_loss_blocked_until:  # [CB-v1-fix] no double-count
                    _consecutive_losses += 1
                if _consecutive_losses >= 4 and time.time() >= _cb_loss_blocked_until:
                    _cb_loss_blocked_until = time.time() + 3600
                    log.error(
                        f"[CB-LOSS] {_consecutive_losses} SL consecutivi — "
                        f"blocco 1h fino a "
                        f"{time.strftime('%H:%M', time.localtime(_cb_loss_blocked_until))}"
                    )
                # [FIX-A] Guard: if a SELL was already fired for this token and the position
                # is still visible (not yet confirmed filled), skip until TTL expires.
                # Prevents the same bucket being stop-sold every 120s in a loop.
                _now_sl = time.time()
                if token_id in STATE._sl_pending:
                    _sl_elapsed = _now_sl - STATE._sl_pending[token_id]
                    if _sl_elapsed < STATE.SL_PENDING_TTL:
                        log.info(
                            f"STOPLOSS PENDING {title[:40]}: "
                            f"SELL already fired {_sl_elapsed:.0f}s ago, "
                            f"TTL={STATE.SL_PENDING_TTL:.0f}s — skipping until fill confirmed"
                        )
                        continue
                    else:
                        log.info(
                            f"STOPLOSS RETRY {title[:40]}: "
                            f"TTL expired ({_sl_elapsed:.0f}s), position still open — retrying SELL"
                        )
                # [v4.9-fix] RIMOSSO: skip neg-risk era sbagliato.
                # Il CLOB desync era causato dal cancel-race, non da neg-risk.
                # Neg-risk NON impedisce di vendere — è solo un flag di mercato.
                # Il fix corretto è cancel_orders_for_token() che previene il desync.
                
                # [v4.9-fix] Verifica CLOB balance PRIMA di inviare SELL.
                # Se il CLOB non vede le shares (desync residuo su posizioni vecchie),
                # un SELL fallirebbe con 'not enough balance' e creerebbe noise nei log.
                # In questo caso logghiamo un warning diagnostico invece di inviare.
                clob_balance = 0.0
                try:
                    pos_clob = await _async_clob(client.get_position, token_id)
                    if pos_clob is not None:
                        clob_balance = float(pos_clob)
                except Exception:
                    # get_position non disponibile → fallback: tenta il sell ugualmente
                    clob_balance = size  # assume allineato
                
                if clob_balance < 1.0 and clob_balance < size * 0.5:
                    # CLOB vede meno della metà delle shares → desync residuo
                    # Non inviare il SELL: fallirebbe. Loggare per diagnostica.
                    log.warning(
                        f"STOPLOSS DESYNC {title[:40]}: "
                        f"Gamma={size:.0f} shares, CLOB={clob_balance:.0f} shares. "
                        f"SELL non inviato. Aprire ticket Polymarket con token={token_id[:16]}"
                    )
                    continue

                city = "seoul" if "seoul" in title.lower() else "london"
                if size < 5.0:  # [v4.31-fix2] CLOB rejects < 5 contracts
                    log.info(f"STOPLOSS SKIP {title[:40]}: size={size:.2f} < 5 contracts minimum")
                    continue
                sell_price = max(0.01, cur_price - 0.002)
                sell_price = min(sell_price, 0.9899)  # [99C-FIX]  # leggermente sotto per fill rapido
                sell_notional = size * sell_price

                log.warning(
                    f"STOPLOSS {title[:40]}: pnl={pnl_pct:.1f}% "
                    f"val=${cur_value:.2f} → SELL {size:.0f} contracts "
                    f"(${sell_notional:.2f}) @ {sell_price:.4f}"
                )
                EVENTS.stoploss_triggered(token_id, title, pnl_pct, size, sell_price)
                # [P2] Skip SELL if position already resolved (curPrice≈0) or redeemable
                _cur_p2 = float(p.get("curPrice", 0) or p.get("currentPrice", 0) or 0)
                _redeemable_p2 = p.get("redeemable", False)
                if _redeemable_p2 or _cur_p2 < 0.001:
                    log.info(f"POSITION RESOLVED -> REDEEM (skip stoploss) {title[:40]} | "
                             f"redeemable={_redeemable_p2} curP={_cur_p2:.4f}")
                    try:
                        await auto_redeem_resolved()
                    except Exception as _re2:
                        log.warning(f"STOPLOSS->REDEEM ERROR: {_re2}")
                    continue
                # [PHASE3] Shadow mode: log the stoploss trigger but don't execute
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
                _sl_submitted = await place_order(client, token_id, "SELL", sell_price, sell_notional,
                            city=city, strategy="STOPLOSS")  # [C1-FIX]
                # [C1-FIX] Only mark pending if order reached CLOB (not silent None)
                if _sl_submitted:
                    STATE._sl_pending[token_id] = time.time()
                    log.info(
                        f"STOPLOSS SENT {title[:40]}: "
                        f"token={token_id[:16]} marked pending, "
                        f"will skip re-fire for {STATE.SL_PENDING_TTL:.0f}s or until fill confirmed"
                    )
                else:
                    log.warning(f"STOPLOSS NOT SUBMITTED {title[:40]}: resp=None, not marking pending")

    except Exception as e:
        log.warning(f"STOPLOSS CHECK ERROR: {e}")

# [TGT1] _last_exposure_sync -> STATE._last_exposure_sync
EXPOSURE_SYNC_INTERVAL = 60.0  # secondi tra una sync e l'altra
EXPOSURE_HARD_SYNC_INTERVAL = 300.0  # [PHASE2] force full recalc every 5 min

async def get_positions_safe(client) -> list:  # [v4.21-async-positions]
    """Recupera posizioni con fallback robusto.
    Metodo 1: REST data-api (più completo)
    Metodo 2: client.get_positions() se esiste
    Metodo 3: lista vuota"""
    # Metodo 1: REST API (sempre disponibile)
    funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()  # [v4.19-fix-funder]
    if funder:
        try:
            # [v4.21-async-positions] Non-blocking: await _async_get
            r = await _async_get(
                f"https://data-api.polymarket.com/positions?user={funder}",
                timeout=10)
            if r is not None and r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    # Metodo 2: CLOB client (potrebbe non esistere)
    if client and hasattr(client, 'get_positions'):
        try:
            return await _async_clob(client.get_positions) or []
        except Exception:
            pass
    return []

async def sync_exposure_from_positions(client):  # [v4.21-async-sync]
    """[v4.6-audit3] Sincronizza TOTAL_EXPOSURE = posizioni fillate + ordini pending.
    TOTAL_EXPOSURE = positions_exposure + pending_orders_exposure
    Questo evita il doppio conteggio: senza ordini pending, il sync azzera
    l'exposure e il bot piazza nuovi ordini fino a ri-saturare il cap."""
    now = time.time()
    if now - STATE._last_exposure_sync < EXPOSURE_SYNC_INTERVAL:
        return
    try:
        # ── 1. Exposure da posizioni fillate ──
        # [PHASE2] Hard-sync: zero out total_exposure BEFORE recalculating.
        # This prevents stale ghost exposure from cancelled/expired orders
        # that never decremented the counter.
        STATE.set_total_exposure(0.0)  # will be rebuilt from positions + pending below

        positions = await get_positions_safe(client)  # [v4.21-async-sync]
        # [v4.27-B-fix] Sync _local_positions so token cap reads real inventory
        STATE._local_positions.clear()
        for _p in positions:
            _tid = str(_p.get("asset") or _p.get("tokenId") or _p.get("token_id") or "")
            if _tid:
                STATE._local_positions[_tid] = float(_p.get("size") or _p.get("quantity") or 0)
        # [FIX-A] Clear _sl_pending for tokens no longer in the API position list.
        # This confirms a stoploss SELL was filled: position is gone from the wallet.
        _current_tids = set(STATE._local_positions.keys())
        for _sl_tid in list(STATE._sl_pending.keys()):
            if _sl_tid not in _current_tids:
                log.info(f"STOPLOSS FILL_CONFIRMED: token={_sl_tid[:16]} no longer in positions — removing from pending")
                del STATE._sl_pending[_sl_tid]
        positions_exposure = 0.0
        for p in positions:
            size  = float(p.get("size")  or p.get("quantity") or 0)
            price = float(p.get("price") or p.get("avg_price") or p.get("curPrice") or 0)
            positions_exposure += size * price

        # ── 2. Exposure da ordini BUY aperti (pending/live nel CLOB) ──
        pending_exposure = 0.0
        funder = (RESOLVED_FUNDER or os.getenv("PM_FUNDER_KEY", "")).strip()  # [v4.19-fix-funder]
        
        # Metodo A: REST API diretta (preferito)
        try:
            if funder:
                headers = {}
                # Usa API creds se disponibili
                ak = os.getenv("PM_API_KEY", "")
                if ak:
                    headers["POLY_API_KEY"] = ak
                    headers["POLY_API_SECRET"] = os.getenv("PM_API_SECRET", "")
                    headers["POLY_API_PASSPHRASE"] = os.getenv("PM_API_PASSPHRASE", "")
                
                r_orders = await _async_get(  # [v4.21-async-sync]
                    "https://clob.polymarket.com/orders",
                    params={"maker_address": funder, "state": "LIVE"},
                    headers=headers, timeout=8)
                if r_orders is not None and r_orders.status_code == 200:
                    orders_data = r_orders.json()
                    # L'API può ritornare lista o dict con "data" key
                    if isinstance(orders_data, dict):
                        orders_list = orders_data.get("data", [])
                    elif isinstance(orders_data, list):
                        orders_list = orders_data
                    else:
                        orders_list = []
                    
                    for o in orders_list:
                        o_side = str(o.get("side", "")).upper()
                        if o_side == "BUY":
                            o_size  = float(o.get("original_size") or o.get("size") or 0)
                            o_price = float(o.get("price") or 0)
                            # size_matched = quanto è già fillato
                            o_matched = float(o.get("size_matched") or 0)
                            o_remaining = max(0, o_size - o_matched)
                            pending_exposure += o_remaining * o_price
                    
                    if pending_exposure > 0:
                        log.info(f"PENDING ORDERS: {len(orders_list)} live, "
                                 f"BUY pending=${pending_exposure:.2f}")
        except Exception as oe:
            log.debug(f"PENDING ORDERS REST: {oe}")
        
        # Metodo B: CLI fallback se REST non ha funzionato
        if pending_exposure == 0:
            try:
                # [v4.21-async-sync] subprocess wrapped in executor — non blocca event loop
                _cli_cwd = str(_BOT_DIR)  # [TGT3] dynamic path
                import functools as _ft2
                _cli_fn = _ft2.partial(
                    subprocess.run,
                    ["polymarket", "--signature-type", "eoa", "-o", "json", "clob", "orders"],
                    capture_output=True, text=True, timeout=15,
                    cwd=_cli_cwd, stdin=subprocess.DEVNULL)
                cli_result = await asyncio.get_running_loop().run_in_executor(None, _cli_fn)
                if cli_result.returncode == 0 and cli_result.stdout.strip():
                    cli_orders = json.loads(cli_result.stdout)
                    if isinstance(cli_orders, dict):
                        cli_orders = cli_orders.get("data", [])
                    if isinstance(cli_orders, list):
                        for o in cli_orders:
                            o_side = str(o.get("side", "")).upper()
                            if o_side == "BUY":
                                o_size  = float(o.get("original_size") or o.get("size") or 0)
                                o_price = float(o.get("price") or 0)
                                pending_exposure += o_size * o_price
            except Exception:
                pass

        # ── 3. Totale = positions + pending ──
        total_new = positions_exposure + pending_exposure
        old_exp = STATE.total_exposure
        STATE.set_total_exposure(total_new)
        STATE._last_exposure_sync = now

        if abs(old_exp - total_new) > 0.5:
            log.info(f"EXPOSURE SYNC: {old_exp:.2f} -> {total_new:.2f} "
                     f"(pos=${positions_exposure:.2f} + pending=${pending_exposure:.2f})")
            EVENTS.exposure_sync(old_exp, total_new, len(positions))
        
        # [v4.6-audit5] Sync PortfolioState con dati GIÀ fetchati (no double call)
        if positions:
            STATE.sync_positions_from_api(positions)
        # [GHOST-PURGE] Remove zero/near-zero notional positions from STATE in-place
        _ghost_tids = [_tid for _tid, _p in list(getattr(STATE, 'positions', {}).items())
                       if (_p.notional or 0.0) <= 1e-6]
        for _gtid in _ghost_tids:
            STATE.positions.pop(_gtid, None)
        if _ghost_tids:
            log.debug(f"GHOST-PURGE: removed {len(_ghost_tids)} zero-notional positions")
                
    except Exception as e:
        log.warning(f"EXPOSURE SYNC ERROR: {e}")

async def check_kill_switch(client, capital: float):  # [v4.17-fix-pnl] async promotion
    """[LOW-3] No time.sleep() bloccante."""
    now = time.time()

    # [v4.6-audit] Sync capital to STATE
    STATE.set_capital(capital)

    if capital > STATE.peak_capital:
        STATE.peak_capital = capital
        log.info(f"NEW PEAK: ${STATE.peak_capital:.2f}")

    if STATE.peak_capital > 0:
        dd = (STATE.peak_capital-capital)/STATE.peak_capital
        log.info(f"PORTFOLIO DD: {dd:.1%} (peak=${STATE.peak_capital:.2f})")
        if False and dd >= MAX_PORTFOLIO_DD:  # [TESTING] hard stop temporarily disabled
            log.warning(f"HARD STOP! DD={dd:.1%}")
            print(f"🛑 HARD STOP! DD={dd:.1%} — pausa 24h")
            EVENTS.kill_activated(f"DD={dd:.1%}", 24, capital, dd)
            try: await _async_clob(client.cancel_all_orders)
            except Exception: pass
            STATE.kill_until = now+86400; return

    if now - STATE.daily_reset_ts >= 86400:
        log.info(f"DAILY RESET: pnl={STATE.daily_pnl:.4f}")
        STATE.daily_pnl = 0.0
        STATE.daily_reset_ts = now
        STATE._daily_reset_capital = capital  # [v4.22-fix-killswitch] snapshot capital at reset
        # STATE._last_trade_ids.clear()  # [T3-FIX] removed: 1000-cap in _save_state prevents leak
        STATE.daily_reset()

    await update_pnl_from_fills(client)  # [v4.17-fix-pnl]

    # [v4.24-fix-killswitch-units] equity_loss convertito in USD prima del min()
    # BUG v4.22: equity_daily_loss era ratio (es. -0.03) confrontato con daily_pnl in USD
    # → min(-1.50_USD, -0.03_ratio) = -1.50 — equity path sempre ignorata
    # → threshold = -0.02 * 101 = -$2.02: ratio -0.03 > -2.02 → mai triggerava
    # FIX: entrambi i valori in USD, threshold basato su _daily_reset_capital
    equity_daily_loss_usd = (capital - STATE._daily_reset_capital) \
        if STATE._daily_reset_capital and STATE._daily_reset_capital > 0 else 0.0
    effective_daily_pnl = min(STATE.daily_pnl, equity_daily_loss_usd)  # entrambi USD

    threshold = DAILY_STOPLOSS * STATE._daily_reset_capital  # USD, basato su capitale snapshot
    if False and effective_daily_pnl < threshold:  # [TESTING] kill-switch temporarily disabled
        log.warning(f"KILL-SWITCH! pnl={effective_daily_pnl:.4f} < {threshold:.4f}")
        print(f"🔴 KILL-SWITCH! pnl={effective_daily_pnl:.4f} — pausa 24h")
        EVENTS.kill_activated(f"daily_pnl={effective_daily_pnl:.4f}", 24, capital, 0)
        try: await _async_clob(client.cancel_all_orders)
        except Exception: pass
        STATE.kill_until = now+86400

# ══════════════════════════════════════════════════════════════════
# MAIN TRADING LOOP (async)
# ══════════════════════════════════════════════════════════════════
async def trading_loop(client, args, CAPITAL_ref: list):
    """
    CAPITAL_ref = [float] — lista mutabile per aggiornamento in-place.
    """

    # [v4.5-fix8] Sync exposure da posizioni live all avvio
    if client:
        await sync_exposure_from_positions(client)  # [v4.21-async-sync]
        log.info(f"STARTUP EXPOSURE SYNC: STATE.total_exposure={STATE.total_exposure:.2f}")

    _last_snapshot_ts = 0.0
    _prev_kill_active = False  # [BUG4-FIX] track kill→resume transition

    while True:
        try:
            _cycle_n = getattr(trading_loop, '_cycle_n', 0) + 1
            trading_loop._cycle_n = _cycle_n
            # [v4.20-hb] Heartbeat visibile ogni ciclo
            print(f"⏳  CYCLE #{_cycle_n} | capital=${CAPITAL_ref[0]:.2f} | exposure={STATE.total_exposure:.2f} | {time.strftime('%H:%M:%S')}", flush=True)
            now = time.time()
            if now < STATE.kill_until:
                _prev_kill_active = True  # [BUG4-FIX] mark kill was active
                remaining = int((STATE.kill_until - now) / 60)
                print(f"Kill-switch: {remaining} min", flush=True)
                await asyncio.sleep(60)
                continue

            # [BUG4-FIX] First cycle after kill-switch lifts: force fresh capital + exposure sync
            if _prev_kill_active:
                log.warning("KILL RESUME: forcing fresh capital fetch and exposure re-sync")
                _prev_kill_active = False
                await fetch_portfolio_capital(client, force=True)  # force ignores rate-limit cache
                await sync_exposure_from_positions(client)

            # [v4.11-B1] Capitale reale da API — nessun fallback su env.
            # Il vecchio max(_env_floor, real_cap) perpetuava il bug: se l'API
            # ritornava $1.02 reali ma env=$101, il cap rimaneva $101.
            _old_cap = CAPITAL_ref[0]
            real_cap = await fetch_portfolio_capital(client)  # [v4.21-async-capital]
            if real_cap is not None and real_cap > 0:
                CAPITAL_ref[0] = real_cap
                if abs(CAPITAL_ref[0] - _old_cap) > 0.10:
                    EVENTS.capital_update(_old_cap, CAPITAL_ref[0], "api")
            # Se real_cap è None manteniamo l'ultimo valore noto (rate-limit cache)
            CAPITAL = CAPITAL_ref[0]
            # [v4.20-fix-cap] DRY_RUN: non lasciare capital < 5 bloccare il loop
            if DRY_RUN and CAPITAL < 5.0:
                _dry_cap = float(os.getenv("PM_CAPITAL_USDC") or 100.0)
                if _dry_cap < 5.0:
                    _dry_cap = 100.0
                CAPITAL_ref[0] = _dry_cap
                CAPITAL = _dry_cap
                log.debug(f"DRY_RUN CAPITAL FLOOR applied: ${CAPITAL:.2f}")

            await sync_exposure_from_positions(client)  # [v4.21-async-sync]
            pass  # [DEBUG] auto_redeem disabilitato temporaneamente
            # [v4.15-fix8] WS_BOOK pruning: rimuovi entries stale (>300s) per evitare
            # memory leak e dati obsoleti su mercati risolti/delistati
            _ws_now = time.time()
            _stale_keys = [k for k, v in list(WS_BOOK.items()) if _ws_now - v.get("ts",0) > 300]
            for _sk in _stale_keys:
                WS_BOOK.pop(_sk, None)
            if _stale_keys:
                log.debug(f"WS_BOOK pruned {len(_stale_keys)} stale entries")
            refresh_api_creds(client)  # [v4.6-fix6] Refresh creds ogni 30min
            await check_position_stoploss(client)
            await check_position_exit(client)
            await check_kill_switch(client, CAPITAL)
            if time.time() < STATE.kill_until:
                await asyncio.sleep(60); continue

            # ── Phase 0: Fetch mercati + forecast in parallelo ──── [v4.15-fix6]
            # return_exceptions=True: errore meteo non abbatte il ciclo intero
            _ph0 = await asyncio.gather(
                fetch_markets_async(),
                fetch_weather_forecast_async("london"),
                fetch_weather_forecast_async("seoul"),
                fetch_metar_max("london"),   # [FEAT2] METAR nowcast — populates _METAR_CACHE
                fetch_metar_max("seoul"),     # [FEAT2] METAR nowcast — populates _METAR_CACHE
                return_exceptions=True
            )
            # [v4.20-log] Verbose Phase 0 logging + empty markets detection
            if not isinstance(_ph0[0], Exception):
                markets = _ph0[0]
                if not markets:
                    print(f"⚠  CYCLE #{_cycle_n}: nessun mercato attivo — retry in 60s", flush=True)
                    log.warning("MARKETS EMPTY: nessun mercato attivo o fetch fallito")
                    await asyncio.sleep(60); continue
                print(f"   CYCLE #{_cycle_n}: {len(markets)} mercati | london_fc={'OK' if not isinstance(_ph0[1], Exception) else 'ERR'} | seoul_fc={'OK' if not isinstance(_ph0[2], Exception) else 'ERR'}", flush=True)
            else:
                markets = None
                log.error(f"MARKETS FETCH ERROR: {_ph0[0]}")
                await asyncio.sleep(10); continue
            fc_london = _ph0[1] if not isinstance(_ph0[1], Exception) else {}
            fc_seoul  = _ph0[2] if not isinstance(_ph0[2], Exception) else {}
            if isinstance(_ph0[1], Exception):
                log.warning(f"LONDON FORECAST ERROR: {_ph0[1]}")
            if isinstance(_ph0[2], Exception):
                log.warning(f"SEOUL FORECAST ERROR: {_ph0[2]}")
            # [FEAT3] Daily weight evaluation using METAR actuals (guarded: once/day)
            for _metar_city in ("london", "seoul"):
                _metar_val = _METAR_CACHE.get(METAR_STATIONS.get(_metar_city, ""), {}).get("max_c")
                if _metar_val is not None:
                    _today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    await evaluate_forecast_weights(_metar_city, {_today_str: _metar_val})

            if args.dry_check:
                print(f"\n{'─'*60}")
                print(f"DRY-CHECK: {len(markets)} mercati trovati — simulazione completa")
                print(f"{'─'*60}")

            # ── Phase ARB: Combinatorial Arbitrage ────────────────
            groups              = group_buckets_by_day(markets)
            arb_executed_groups = set()
            arb_cycle_spend = 0.0  # [v4.6-fix5] Totale USDC spesi in ARB questo ciclo

            # [v4.11-B8] Throttle globale per ciclo: max MAX_ORDERS_PER_CYCLE ordini
            # indipendentemente dall'exposure calcolata. Previene burst da sync 0→X.
            _orders_this_cycle   = 0

            # [v4.11-B9] Guard kill-switch per ARB: se kill attivo saltiamo tutto il
            # Phase ARB. place_order ha già il guard (B3), ma questo evita di calcolare
            # l'intero LP inutilmente durante il kill (18 ordini ARB il 08/03 15:03).
            _arb_blocked_by_kill = (time.time() < STATE.kill_until)
            if _arb_blocked_by_kill:
                log.debug("ARB PHASE SKIPPED: kill active")

            # [v4.6-fix9] Calcola exposure per città da posizioni live
            city_exposure: dict = {"seoul": 0.0, "london": 0.0}
            for _tok, _pdata in PORTFOLIO_RISK.positions.items():
                _city = _pdata.get("city", "london")
                city_exposure[_city] = city_exposure.get(_city, 0.0) + float(_pdata.get("size", 0))


            for group_key, group_markets in groups.items():
                if _arb_blocked_by_kill:
                    break  # [v4.11-B9] skip tutto il loop ARB durante kill
                books = {}
                for gm in group_markets:
                    tid = clean_token_id(
                        gm.get("clobTokenIds") or gm.get("clob_token_ids") or "")
                    if not tid: continue
                    bids, asks = await get_book(tid, client, market_obj=gm)  # [v4.22-fix-book-async]
                    if bids and asks:
                        bb, ba = bids[0][0], asks[0][0]
                        books[gm["id"]] = {
                            "mid":(bb+ba)/2, "best_bid":bb, "best_ask":ba,
                            "spread":ba-bb,
                            "depth_usd":sum(b[1] for b in bids[:5]),
                            "token_id":tid
                        }
                if len(books) < len(group_markets): continue

                prices    = [books[gm["id"]]["mid"]      for gm in group_markets]
                best_asks = [books[gm["id"]]["best_ask"] for gm in group_markets]
                mkt_ids   = [gm["id"]                    for gm in group_markets]
                token_ids = [books[gm["id"]]["token_id"] for gm in group_markets]

                mispricing, KL = bregman_projection(prices)

                # NAO boost
                corr_bonus = 0.0
                date_key_arb = "_".join(group_key.split("_")[1:])
                try:
                    nao = cross_city_arb_check(fc_london, fc_seoul, date_key_arb)
                    corr_bonus = nao["corr_edge"]
                    if nao["direction"] != "concordant":
                        mispricing = min(mispricing+corr_bonus, 0.5)
                except Exception as ne:
                    log.warning(f"NAO: {ne}")

                if mispricing > MISPRICING_THRESHOLD:
                    log.debug(f"ARB {group_key}: sum={sum(prices):.4f} "
                          f"misp={mispricing:.5f} KL={KL:.6f} nao={corr_bonus:.4f}")

                # [v4.2-C3] Skip ARB se >50% bucket sono cumulativi (or_higher/or_lower)
                # Non è ARB classico — è scommessa direzionale su mercato illiquido
                _buckets_parsed = [parse_bucket(gm.get('question','')) for gm in group_markets]
                _n_exact = sum(1 for b in _buckets_parsed if b.get('type')=='exact')
                _cumulative_frac = 1.0 - (_n_exact / max(len(_buckets_parsed), 1))
                if _cumulative_frac > 0.5:
                    log.info(f'ARB SKIP {group_key}: {_cumulative_frac:.0%} bucket cumulativi')
                    continue

                if mispricing > MISPRICING_THRESHOLD:
                    city_k   = group_key.split("_")[0]
                    date_k   = "_".join(group_key.split("_")[1:])
                    fc       = fc_london if city_k=="london" else fc_seoul
                    fc_temp  = fc.get(date_k)

                    # [v4.1-C1] ARB logic corretta con overlap adjustment
                    # I bucket temperatura sono cumulativi (≥5, ≥10, ≥15...):
                    # la somma delle true_p > 1 per costruzione.
                    # overlap_adjust corregge il mispricing apparente.
                    if fc_temp is None:
                        log.debug(f"ARB SKIP {group_key}: forecast mancante")
                        continue

                    T_arb = (sum(hours_to_resolution(gm) for gm in group_markets)
                             / len(group_markets) / 24.0)

                    # [v4.7-fix8] Aumenta incertezza se forecast è climatologico
                    _T_arb_adj = T_arb
                    _fc_cache_arb = FORECAST_CACHE.get(city_k, {})
                    _is_fallback_arb = bool(_fc_cache_arb.get("is_fallback"))
                    if _is_fallback_arb:
                        _T_arb_adj = max(T_arb, 5.0)

                    # [v4.25-eglc/rksi] observed max per ARB — stessa logica MM
                    if city_k == "london":
                        _obs_arb = get_eglc_observed_max()
                    elif city_k == "seoul":
                        _obs_arb = get_rksi_observed_max()
                    else:
                        _obs_arb = None
                    total_true_p = sum(
                        ecmwf_true_prob(
                            fc_temp,
                            parse_bucket(gm.get("question",""))["type"],
                            parse_bucket(gm.get("question",""))["threshold"],
                            T_days=_T_arb_adj, city=city_k,
                            is_fallback=_is_fallback_arb,  # [v4.14-M1] sigma+1.5 per climatologia
                            observed_max=_obs_arb           # [v4.25-eglc/rksi]
                        )
                        for gm in group_markets
                    )
                    # Haircut conservativo per overlap strutturale
                    # [v4.5-fix8] overlap_adjust proporzionale alla % di bucket cumulativi.
                    # Se il gruppo è passato C3 (>50% esatti), l'overlap strutturale è minimo.
                    # _cumulative_frac=0.0 → tutti esatti → haircut=0.10 (solo errore forecast)
                    # _cumulative_frac=0.5 → metà cumulativi → haircut=0.35
                    # Erano: sempre 0.60 indipendentemente dalla composizione del gruppo.
                    _overlap_haircut = 0.10 + _cumulative_frac * 0.50
                    overlap_adjust   = max(0.0, total_true_p - 1.0) * _overlap_haircut
                    _avg_mid_arb = sum(prices)/max(len(prices),1)
                    _gls_e_arb, _ = get_gls_edge(_avg_mid_arb)  # [v4.5-fix5]
                    effective_edge  = max(0.0, mispricing - overlap_adjust + max(0.0, _gls_e_arb))

                    if effective_edge < 0.012:
                        log.info(f"ARB SKIP {group_key}: effective_edge={effective_edge:.4f} < 0.012")
                        log.debug(f"ARB SKIP {group_key}: effective_edge={effective_edge:.5f}")
                        continue

                    avg_mid    = sum(prices)/len(prices)
                    avg_spread = sum(b["spread"] for b in books.values())/len(books)
                    depth_arb  = sum(b["depth_usd"] for b in books.values())/max(len(books),1)

                    # Kelly con haircut aggiuntivo 0.5x per rischio ladder
                    if MONITOR.is_killed('ARB', city_k): continue
                    print("[DBGKM] kelly START", flush=True)
                    ks = await asyncio.get_running_loop().run_in_executor(  # [v4.24-kelly-async]
                        None, functools.partial(kelly_and_mc, effective_edge*0.5, avg_mid, depth_arb,                    avg_spread, CAPITAL,                    T=T_arb, city=city_k))
                    if ks <= 0: continue

                    arb_orders, net_profit = integer_program_arb(
                        prices, best_asks, mkt_ids, ks)
                    if not arb_orders: continue

                    # Sanity check finale: tp < 0.01 per nessun bucket comprato
                    safe = True
                    for mkt_id, size in arb_orders.items():
                        idx = mkt_ids.index(mkt_id)
                        b   = parse_bucket(group_markets[idx].get("question",""))
                        if not b.get("valid", False):
                            continue
                        tp  = ecmwf_true_prob(fc_temp, b["type"], b["threshold"],
                                              T_days=_T_arb_adj, city=city_k,
                                              observed_max=_obs_arb)  # [v4.7-fix/v4.25-rksi8]
                        if tp < 0.01:
                            log.warning(f"ARB BLOCKED tp={tp:.4f} {b['threshold']}°C")
                            safe = False; break

                    if safe:
                        # [v4.6-fix5] Near-ARB cap: max NEAR_ARB_CAP_FRAC del capitale per ciclo
                        arb_order_cost = sum(
                            max(0.01, best_asks[mkt_ids.index(mid_)] - ARB_MAKER_BUFFER) * sz_
                            for mid_, sz_ in arb_orders.items()
                        )
                        arb_cap = CAPITAL * NEAR_ARB_CAP_FRAC
                        if arb_cycle_spend + arb_order_cost > arb_cap:
                            log.info(f"ARB CAP {group_key}: spend={arb_cycle_spend:.2f}+{arb_order_cost:.2f} > cap={arb_cap:.2f} → skip")
                            continue

                        # [v4.6-fix9] City cap anche per ARB
                        if city_exposure.get(city_k, 0.0) + arb_order_cost > CAPITAL * CITY_CAP_FRAC:
                            log.info(f"ARB CITY CAP {city_k}: skip")
                            continue

                        print(f"🎯 ARB {group_key} eff_edge={effective_edge:.4f} "
                              f"net={net_profit:.4f}")
                        for mkt_id, sz in arb_orders.items():
                            idx = mkt_ids.index(mkt_id)
                            tok = token_ids[idx]
                            tok_mid = prices[idx]  # mid reale del token
                            ask_p = max(0.01, best_asks[idx] - ARB_MAKER_BUFFER)
                        
                            # [v4.6-audit4] Becker: check ENTRAMBI mid e ask_p
                            # Un token con mid=0.98 non deve MAI essere comprato
                            if tok_mid > BECKER_BUY_ZONE_MAX or ask_p > BECKER_BUY_ZONE_MAX:
                                log.info(f"ARB BECKER SKIP {tok[:12]}: "
                                         f"mid={tok_mid:.3f} ask={ask_p:.3f} > {BECKER_BUY_ZONE_MAX}")
                                continue
                            # [v4.27-B] Token cap ARB
                            _arb_tok_inv = STATE._local_positions.get(tok, 0.0) * tok_mid
                            _arb_tok_cap = CAPITAL * MAX_TOKEN_EXPOSURE_FRAC
                            if _arb_tok_inv >= _arb_tok_cap:
                                log.info(f"TOKEN CAP SKIP ARB {tok[:12]}: inv={_arb_tok_inv:.2f}")
                                continue
                            # [v4.27-A] Re-entry cooldown ARB
                            _arb_edge = abs(tok_mid - ask_p)
                            # [CRITICAL FIX #1] Hard guard for ARB: edge must be positive and sufficient
                            if _arb_edge < MIN_FORECAST_EDGE:
                                log.warning(f"HARD GUARD BLOCK ARB: _arb_edge={_arb_edge:.4f} < MIN_FORECAST_EDGE={MIN_FORECAST_EDGE} for {tok[:12]}")
                                continue
                            if not should_allow_buy_reentry(tok, _arb_edge):
                                log.info(f"REENTRY SKIP ARB {tok[:12]}: cooldown")
                                continue
                            # [FAMILY] Family-level risk gate for ARB
                            _arb_q = group_markets[idx].get("question", "")
                            _fam_pos_arb = {t: {"size": p.size, "avg_price": p.avg_price,
                                                "notional": p.notional, "city": p.city,
                                                "title": getattr(p, "title", "")}
                                            for t, p in STATE.positions.items()
                                            if (p.notional or 0) > 1e-6}
                            _fam_ok_arb, _fam_reason_arb = family_risk.check_family_gate(
                                _fam_pos_arb, tok, _arb_q, ask_p, sz * ask_p)
                            if not _fam_ok_arb:
                                log.info(f"FAMILY GATE BLOCK ARB {tok[:12]}: {_fam_reason_arb}")
                                continue
                            if _orders_this_cycle >= MAX_ORDERS_PER_CYCLE:
                                log.info(f"THROTTLE: {_orders_this_cycle}/{MAX_ORDERS_PER_CYCLE} ordini raggiunto, skip ARB")
                                break
                            try:
                                await asyncio.wait_for(place_order(client, tok, "BUY", ask_p, sz, city=city_k, strategy="ARB"), timeout=15.0)
                            except asyncio.TimeoutError:
                                log.warning("place_order TIMEOUT")
                            _orders_this_cycle += 1
                            record_buy_attempt(tok, _arb_edge)  # [v4.27-A]
                            if not hasattr(MONITOR, '_edge_cache'): MONITOR._edge_cache = {}
                            MONITOR._edge_cache[tok] = round(_arb_edge, 4)  # [EDGE-ARB]
                            MONITOR._save_edge_cache()  # [PHASE1B] persist to disk
                            # [v4.11-B7] record_trade spostato in update_pnl_from_fills
                            # — si registra solo su fill confermato dall'activity API.
                        arb_cycle_spend += arb_order_cost
                        city_exposure[city_k] = city_exposure.get(city_k, 0.0) + arb_order_cost
                        arb_executed_groups.add(group_key)

            # ── Phase 0b: Risoluzione mercati → aggiorna SelfMonitor ──
            # [v4.5-fix1b] Controlla mercati risolti direttamente dalla lista
            for _m in markets:
                _is_resolved = (_m.get("active") is False or
                                _m.get("resolved") is True or
                                str(_m.get("status","")).upper() in ("RESOLVED","CLOSED"))
                if not _is_resolved: continue
                _tid = clean_token_id(
                    _m.get("clobTokenIds") or _m.get("clob_token_ids") or "")
                if not _tid: continue
                _outcome_raw = str(_m.get("outcome") or _m.get("resolution") or "").upper()
                _resolved_yes = _outcome_raw in ("YES","1","WIN","TRUE")
                # Solo se abbiamo un outcome definitivo
                if _outcome_raw:
                    MONITOR.record_resolution(_tid, _resolved_yes)
                    # [v4.13-DC4] Rimosso _token_exposure.pop(): la struttura era mai scritta.
                    # L'exposure viene ricalibrata da sync_exposure_from_positions ogni ciclo.
                    log.info(f"RESOLVED {_tid[:12]} outcome={_outcome_raw}")

            # ── Phase MM: Market Making (loop separato) ───────────
            # [v4.3-H3] Reset per-group exposure ad ogni ciclo del loop
            group_exposure: dict = {}  # city_date → USDC allocati nel ciclo corrente

            # [CRITICAL-1] Loop separato da ARB — era dentro for group_key.
            # [v4.24-pvar-once] Compute pvar once per cycle not 43x
            pvar = await _async_clob(PORTFOLIO_RISK.mc_portfolio_var, CAPITAL)  # [v4.24-pvar-once]
            # [v4.24-gather] Per-market MM task — eseguito in parallelo con asyncio.gather
            async def _mm_task(m):  # [v4.24-gather]
                nonlocal _orders_this_cycle
                tid = clean_token_id(
                    m.get("clobTokenIds") or m.get("clob_token_ids") or "")
                if not tid: return
                # [99C-RISK-PATCH] Re-entry block after forced 99c exit
                if tid in _FORCED_EXIT_99C:
                    return

                bids, asks = await get_book(tid, client, market_obj=m)  # [v4.22-fix-book-async]
                if not bids or not asks: return

                best_bid  = bids[0][0]
                best_ask  = asks[0][0]
                mid       = (best_bid+best_ask)/2
                spread    = best_ask-best_bid
                depth_usd = sum(b[1] for b in bids[:5])
                T_check = hours_to_resolution(m) / 24.0
                # [V5-FIX] Always log book microstructure regardless of filters
                try:
                    import json as _json_v5
                    _v5_city = "seoul" if "seoul" in (m.get("question","") or "").lower() else "london"
                    _v5_sp = round((spread / mid * 10000) if mid > 0 else 9999, 1)
                    _v5_bd = round(sum(b[1] for b in bids[:5]), 2)
                    _v5_ad = round(sum(a[1] for a in asks[:5]), 2)
                    _v5_line = _json_v5.dumps({"ts": time.time(), "token_id": tid, "city": _v5_city, "mid": round(mid, 4), "spread_bp": _v5_sp, "bid_depth_usd": _v5_bd, "ask_depth_usd": _v5_ad, "edge_fc": 0.0, "true_p": 0.0, "T_days": round(T_check, 4)})
                    with open(_V5_BOOK_PATH, "a") as _v5f0:
                        _v5f0.write(_v5_line + chr(10))
                except Exception:
                    pass

                if spread>0.15 or depth_usd<MIN_DEPTH_USD: return
                # [v4.6-fix8] Non quotare a meno di MIN_T_HOURS dalla risoluzione
                if T_check < (MIN_T_HOURS/24.0): return  # [v4.15-fix12] explicit float division


                # [v4.6-audit2] Becker zone filter: only trade in high-edge zones
                # BUY 6-30¢, SELL 75-95¢, skip everything else
                if BECKER_FILTER_ENABLED:
                    if not (BECKER_BUY_ZONE_MIN <= mid <= BECKER_BUY_ZONE_MAX or
                            BECKER_SELL_ZONE_MIN <= mid <= BECKER_SELL_ZONE_MAX):
                        return

                q_text = m.get("question","")
                city   = "seoul" if "seoul" in q_text.lower() else "london"
                if MONITOR.is_killed('MM', city): return
                bucket = parse_bucket(q_text)
                if not bucket.get("valid", False):
                    return

                # Forecast con sigma dinamico [v4.1-H3]
                fc            = fc_london if city=="london" else fc_seoul
                forecast_temp = None
                T             = T_check
                dm = re.search(r'(\d{4}-\d{2}-\d{2})', m.get("resolution_time","") or "")
                if not dm:
                    dm = re.search(r'(\d{4}-\d{2}-\d{2})', str(m.get('endDate','')) or '')
                if not dm:
                    import calendar
                    _mt = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})', q_text.lower())
                    if _mt:
                        _months = {m:i for i,m in enumerate(calendar.month_name) if m}
                        _mn = _months.get(_mt.group(1).capitalize(),0)
                        if _mn:
                            from datetime import datetime, timezone
                            _yr = datetime.now(timezone.utc).year
                            dm = re.search(r'(\d{4}-\d{2}-\d{2})', f"{_yr}-{_mn:02d}-{int(_mt.group(2)):02d}")
                if dm:
                    forecast_temp = fc.get(dm.group(1))
                    print(f"[DBG_FC] dm={dm.group(1)} ft={forecast_temp}", flush=True)

                if forecast_temp is not None:
                    # [v4.7-fix8] Se usando fallback climatologico, aumenta incertezza
                    _T_adj = T
                    _fc_cache = FORECAST_CACHE.get(city, {})
                    _is_fallback_mm = bool(_fc_cache.get("is_fallback"))
                    if _is_fallback_mm:
                        _T_adj = max(T, 5.0)  # tratta come forecast a 5+ giorni
                    # [v4.25-eglc/rksi] Pass observed max for London (EGLC) or Seoul (RKSI)
                    if city == "london":
                        _obs = get_eglc_observed_max()
                    elif city == "seoul":
                        _obs = get_rksi_observed_max()
                    else:
                        _obs = None
                    true_p  = ecmwf_true_prob(forecast_temp,
                                               bucket["type"], bucket["threshold"],
                                               T_days=_T_adj, city=city,
                                               is_fallback=_is_fallback_mm,
                                               observed_max=_obs)  # [v4.25-eglc/rksi]
                    # [SEV-1 HOTFIX] Zero-Edge Guard (RELOCATED from ~3466)
                    if true_p <= 0.001 or mid <= 0.001:
                        log.debug(f"ZERO-EDGE SKIP: true_p={true_p:.4f} mid={mid:.4f} token={tid[:12]}")
                        return

                    _gls_edge, _ = get_gls_edge(mid)  # [v4.5-fix5] segnale primario

                    # [v4.6-audit-fix1+2] Edge direzionale, no abs(), no double-counting
                    # Usa max(forecast, becker) non la somma.
                    # Rispetta il segno: BUY quando underpriced, SELL quando overpriced.
                    directional_edge = true_p - mid  # + = underpriced(BUY), - = overpriced(SELL)

                    if mid < BECKER_BUY_ZONE_MAX:
                        # BUY zone: edge positivo = forecast conferma underpricing
                        edge_fc = max(max(0.0, directional_edge), max(0.0, _gls_edge))
                    elif mid > BECKER_SELL_ZONE_MIN:
                        # SELL zone: edge negativo = forecast conferma overpricing
                        # Becker bias negativo per >65¢ → -_gls_edge è positivo
                        edge_fc = max(max(0.0, -directional_edge), max(0.0, -_gls_edge))
                    else:
                        edge_fc = 0.0  # dead zone, non dovrebbe arrivarci

                    print(f"FC {city.upper()} {bucket['threshold']}°C: "
                          f"true={true_p:.4f} mkt={mid:.4f} "
                          f"dir_edge={directional_edge:+.4f} gls={_gls_edge:+.4f} "
                          f"final={edge_fc:.4f} "
                          f"σ={CITY_SIGMA_BASE.get(city,2.5)+0.20*T:.2f}")
                    edge_fc = ml_weight_edge(edge_fc, mid, city, T)  # [v4.32-ML] pesa con modello
                    # [V5-FIX] Log book data ALWAYS before any return
                    try:
                        import json as _json5
                        _v5_sp2 = round((spread / mid * 10000) if mid > 0 else 9999, 1)
                        _v5_bd2 = round(sum(b[1] for b in bids[:5]), 4)
                        _v5_ad2 = round(sum(a[1] for a in asks[:5]), 4)
                        with open(_V5_BOOK_PATH, "a") as _v5f2:
                            _v5f2.write(_json5.dumps({
                                "ts":            time.time(),
                                "token_id":      tid,
                                "city":          city,
                                "mid":           round(mid, 4),
                                "spread_bp":     _v5_sp2,
                                "bid_depth_usd": _v5_bd2,
                                "ask_depth_usd": _v5_ad2,
                                "edge_fc":       round(edge_fc, 4),
                                "true_p":        round(true_p, 4),
                                "T_days":        round(T, 4),
                            }) + "\n")
                    except Exception as _v5e2:
                        pass
                    if edge_fc < MIN_FORECAST_EDGE: return

                    # [FAC] Aggiorna fattori
                    fc_anom = true_p - mid
                    FACTOR_MODEL.update_factors(
                        fc_anom if city=="seoul"  else 0.0,
                        fc_anom if city=="london" else 0.0,
                        spread
                    )
                else:
                    # [v4.27-blind] No forecast available — skip to avoid blind MM
                    log.debug(f"MM BLIND SKIP {tid[:12]}: forecast_temp=None")
                    return

                q_inv     = STATE._local_positions.get(tid, 0.0)  # [v4.24-skip-inv-api]
                print("[DBGAS] avellaneda START", flush=True)
                bid_p, ask_p = avellaneda_stoikov_quote(
                    mid, q_inv, spread, T, token_id=tid)

                edge_mm = max((ask_p-best_ask)+(best_bid-bid_p), 0.001)
                print("[DBGKM] kelly START", flush=True)
                size = await asyncio.get_running_loop().run_in_executor(  # [v4.24-mm-kelly-async]
                    None, functools.partial(kelly_and_mc, edge_mm, mid, depth_usd, spread, CAPITAL, T=T, token_id=tid, city=city))
                if size <= 0: return

                log.debug(f"MM | {q_text[:45]} | "
                      f"bid={bid_p:.4f} ask={ask_p:.4f} sz={size:.2f} "
                      f"T={T:.1f}d VaR99=${pvar:.2f} exp={STATE.total_exposure:.0f}")

                # [v4.6-fix9] Per-city cap: max CITY_CAP_FRAC del capitale per città
                if city_exposure.get(city, 0.0) + size > CAPITAL * CITY_CAP_FRAC:
                    _city_remaining = max(0.0, CAPITAL * CITY_CAP_FRAC - city_exposure.get(city, 0.0))
                    if _city_remaining < 1.0:
                        log.info(f"CITY CAP {city}: skip ({city_exposure.get(city,0):.0f}/{CAPITAL*CITY_CAP_FRAC:.0f})")
                        return
                    size = _city_remaining

                # [v4.6-audit5] Per-group cap: usa resolution_time, non endDate
                _date_str = dm.group(1) if dm else m.get('endDate', '')[:10]
                _group_k = f"{city}_{_date_str}"
                if group_exposure.get(_group_k, 0.0) + size > CAPITAL * GROUP_CAP_FRAC:
                    _remaining = max(0.0, CAPITAL * GROUP_CAP_FRAC - group_exposure.get(_group_k, 0.0))
                    if _remaining < 1.0:
                        log.info(f"GROUP CAP {_group_k}: skip ({group_exposure.get(_group_k,0):.0f}/{CAPITAL*GROUP_CAP_FRAC:.0f})")
                        return
                    size = _remaining
                group_exposure[_group_k] = group_exposure.get(_group_k, 0.0) + size
                city_exposure[city] = city_exposure.get(city, 0.0) + size

                # [v4.27-B] Hard token inventory cap (BUY only)
                _tok_inv = STATE._local_positions.get(tid, 0.0) * mid
                _tok_cap = CAPITAL * MAX_TOKEN_EXPOSURE_FRAC
                if _tok_inv >= _tok_cap:
                    log.info(f"TOKEN CAP SKIP {tid[:12]}: inv={_tok_inv:.2f}>={_tok_cap:.2f}")
                    return
                if _tok_inv + size > _tok_cap:
                    _tok_rem = max(0.0, _tok_cap - _tok_inv)
                    if _tok_rem < 1.0:
                        log.info(f"TOKEN CAP SKIP {tid[:12]}: remaining<1.0")
                        return
                    log.info(f"TOKEN CAP RESIZE {tid[:12]}: {size:.2f}->{_tok_rem:.2f}")
                    size = _tok_rem

                # [v4.27-C] Adjacent exact bucket haircut (BUY only)
                _haircut = adjacent_bucket_haircut(
                    city, _date_str, bucket.get("threshold", -999.0), bucket.get("type", ""))
                if _haircut < 1.0:
                    size = size * _haircut
                    if size < 1.0:
                        log.info(f"ADJACENT HAIRCUT SKIP {tid[:12]}: size<1.0 after haircut")
                        return

                # [v4.27-A] Re-entry cooldown (BUY only)
                if not should_allow_buy_reentry(tid, edge_fc):
                    log.info(f"REENTRY SKIP {tid[:12]}: cooldown {REENTRY_COOLDOWN_SEC}s")
                    return

                if not args.dry_check:
                    # [v4.9-fix] Cancella ordini precedenti con conferma CLOB.
                    # Sostituisce il vecchio fire-and-forget che causava il desync:
                    # il vecchio codice inviava cancel e immediatamente piazzava il
                    # nuovo ordine senza aspettare — gap di 1.66-2.36s nei log
                    # era sufficiente a creare shares orfane nel CLOB.
                    try:
                        await asyncio.wait_for(cancel_orders_for_token(client, tid), timeout=8.0)
                    except asyncio.TimeoutError:
                        log.warning("cancel_orders_for_token TIMEOUT %s", tid[:12])

                    # [v4.2-H2] Adverse selection: widen spread se informed flow
                    _adv_mult = adverse_selection_spread_mult(tid)
                    _half_spread = (ask_p - bid_p) / 2 * _adv_mult
                    _mid_p = (bid_p + ask_p) / 2
                    bid_adj = max(0.01, round(_mid_p - _half_spread - UMA_BUFFER, 4))
                    ask_adj = min(0.99, round(_mid_p + _half_spread + UMA_BUFFER, 4))

                    # [v4.6-audit] Becker zone gating
                    if mid < BECKER_BUY_ZONE_MAX:
                        # [CB-v1] Circuit Breaker guards
                        if _latency_blocked:
                            log.debug(f"[CB-SKIP] LatencyGuard ({_latency_fail_count} fail)")
                            return
                        if not _edge_decay_guard(city):
                            return
                        if not _consecutive_loss_guard():
                            return
                        # [FAMILY] Family-level risk gate
                        _fam_pos = {t: {"size": p.size, "avg_price": p.avg_price,
                                        "notional": p.notional, "city": p.city,
                                        "title": getattr(p, "title", "")}
                                    for t, p in STATE.positions.items()
                                    if (p.notional or 0) > 1e-6}
                        _fam_ok, _fam_reason = family_risk.check_family_gate(
                            _fam_pos, tid, q_text, bid_adj, size)
                        if not _fam_ok:
                            log.info(f"FAMILY GATE BLOCK MM {tid[:12]}: {_fam_reason}")
                            return
                        if _orders_this_cycle >= MAX_ORDERS_PER_CYCLE:
                            log.info(f"THROTTLE: {_orders_this_cycle}/{MAX_ORDERS_PER_CYCLE} ordini raggiunto, skip MM")
                            return
                        _orders_this_cycle += 1  # [v4.24-race] prenota slot prima await
                        # [V5-DATA] Microstructure snapshot PRE-ORDER
                        try:
                            import json as _json
                            _v5_spread_bp   = round((spread / mid * 10000) if mid > 0 else 9999, 1)
                            _v5_bid_depth   = round(sum(b[1] for b in bids[:5]), 4)
                            _v5_ask_depth   = round(sum(a[1] for a in asks[:5]), 4)
                            with open(_V5_BOOK_PATH, "a") as _v5f:
                                _v5f.write(_json.dumps({
                                    "ts":            time.time(),
                                    "token_id":      tid,
                                    "city":          city,
                                    "mid":           round(mid, 4),
                                    "bid_adj":       round(bid_adj, 4),
                                    "spread_bp":     _v5_spread_bp,
                                    "bid_depth_usd": _v5_bid_depth,
                                    "ask_depth_usd": _v5_ask_depth,
                                    "edge_fc":       round(edge_fc, 4),
                                    "true_p":        round(true_p, 4),
                                    "T_days":        round(T, 4),
                                }) + "\n")
                            log.debug(f"[V5-DATA] Book logged token={tid[:12]} spread={_v5_spread_bp:.0f}bp")
                        except Exception as _v5e:
                            log.debug(f"[V5-DATA] Book log failed: {_v5e}")
                        # [CRITICAL FIX #1] Hard guard: never buy with negative directional edge
                        if directional_edge < MIN_FORECAST_EDGE:
                            log.warning(f"HARD GUARD BLOCK MM: directional_edge={directional_edge:.4f} < MIN_FORECAST_EDGE={MIN_FORECAST_EDGE} for {tid[:12]}")
                            return
                        print("[DBG7] calling place_order MM", flush=True)
                        try:
                            await asyncio.wait_for(place_order(client, tid, "BUY", bid_adj, size, city=city, strategy="MM"), timeout=15.0)
                        except asyncio.TimeoutError:
                            log.warning("place_order TIMEOUT")
                            _orders_this_cycle -= 1  # [v4.24-race] restituisce slot su timeout
                        # [v4.11-B7] record_trade spostato in update_pnl_from_fills
                        else:
                            record_buy_attempt(tid, edge_fc)  # [v4.27-A]
                            if not hasattr(MONITOR, '_edge_cache'): MONITOR._edge_cache = {}
                            MONITOR._edge_cache[tid] = round(directional_edge, 4)  # [EDGE]
                            MONITOR._save_edge_cache()  # [PHASE1B] persist to disk
                            # [QUANT] store full evaluation snapshot for ledger
                            _quant_meta_cache[tid] = {
                                "fair_prob":   round(true_p, 4),
                                "mkt_prob":    round(mid, 4),
                                "edge":        round(directional_edge, 4),
                                "kelly_frac":  _quant_meta_cache.get(tid, {}).get("kelly_frac", 0.0),
                                "sizing_mode": "kelly",
                                "cb_version":  "cb-v1",  # [CB-v1]
                            }
                            if bucket.get("type") == "exact":  # [v4.27-C]
                                _adjacent_exact_registry.setdefault(
                                    f"{city}_{_date_str}", set()).add(bucket.get("threshold", -999.0))
                    elif mid > BECKER_SELL_ZONE_MIN:
                        # [FAMILY] Family-level SELL analysis (discretionary MM SELL)
                        _fam_pos_sell = {t: {"size": p.size, "avg_price": p.avg_price,
                                             "notional": p.notional, "city": p.city,
                                             "title": getattr(p, "title", "")}
                                         for t, p in STATE.positions.items()
                                         if (p.notional or 0) > 1e-6}
                        _sell_ok, _sell_reason = family_risk.check_family_sell(
                            _fam_pos_sell, tid, q_text, ask_adj, size, is_protective=False)
                        if not _sell_ok:
                            log.info(f"FAMILY SELL BLOCK MM {tid[:12]}: {_sell_reason}")
                            return
                        if _orders_this_cycle >= MAX_ORDERS_PER_CYCLE:
                            log.info(f"THROTTLE: {_orders_this_cycle}/{MAX_ORDERS_PER_CYCLE} ordini raggiunto, skip MM SELL")
                            return
                        _orders_this_cycle += 1  # [v4.24-race] prenota slot prima await
                        # [v4.9-fix] Rimosso neg-risk skip: era sbagliato.
                        print("[DBG7] calling place_order MM", flush=True)
                        try:
                            await asyncio.wait_for(place_order(client, tid, "SELL", ask_adj, size, city=city, strategy="MM"), timeout=15.0)
                        except asyncio.TimeoutError:
                            log.warning("place_order TIMEOUT")
                            _orders_this_cycle -= 1  # [v4.24-race] restituisce slot su timeout
                        # [v4.11-B7] record_trade spostato in update_pnl_from_fills


            # [v4.24-gather] Limita concorrenza a 5 worker simultanei
            _mm_sem = asyncio.Semaphore(5)

            async def _mm_task_limited(m):
                async with _mm_sem:
                    return await _mm_task(m)

            await asyncio.gather(
                *[_mm_task_limited(m) for m in markets],
                return_exceptions=True,
            )

            if args.dry_check:
                print("\n✅ DRY-CHECK COMPLETE — nessun ordine piazzato.")
                break

            print("[DBG_RC] before run_cycle", flush=True)
            await MONITOR.run_cycle(CAPITAL)
            print("[DBG_RC] after run_cycle", flush=True)
        
            # [v4.6-audit5] Periodic state snapshot (ogni 10 min)
            if time.time() - _last_snapshot_ts > 600:
                _last_snapshot_ts = time.time()
                print("[DBG_EV] Spedizione snapshot in background...", flush=True)
                pass # neutralizzato
        
            print("[DBG_SL] before sleep", flush=True)
            await asyncio.sleep(LOOP_SLEEP)
            print("[DBG_SL] after sleep — CYCLE START", flush=True)

        except asyncio.CancelledError:
            log.info("Trading loop cancellato.")
            break
        except Exception as e:
            import traceback as _tb
            err_msg = _tb.format_exc()
            log.error(f"CRASH CICLO: {e}\n{err_msg}")
            print(f"ERRORE CICLO: {e} - Riavvio 10s", flush=True)
            # [HARDENING] Executor resurrection: if the thread pool was shut down
            # (RuntimeError: cannot schedule new futures after shutdown), rebuild it
            # in-place so subsequent run_in_executor calls succeed without a full restart.
            if "cannot schedule new futures after shutdown" in str(e):
                try:
                    import concurrent.futures as _cf_hard
                    global _exec_pool
                    _exec_pool = _cf_hard.ThreadPoolExecutor(
                        max_workers=min(16, (__import__("os").cpu_count() or 1) * 4))
                    asyncio.get_running_loop().set_default_executor(_exec_pool)
                    log.warning("EXECUTOR REINITIALIZED: recovered from shutdown state")
                    print("EXECUTOR REINITIALIZED — loop hardened", flush=True)
                except Exception as _reinit_err:
                    log.error(f"EXECUTOR REINIT FAILED: {_reinit_err}")
            await asyncio.sleep(10)



# ══════════════════════════════════════════════════════════════════
# SELF-MONITOR v1.0
# ══════════════════════════════════════════════════════════════════
# Quattro sistemi indipendenti:
#
#   [SM-1] POST-TRADE ANALYSIS
#          Ogni fill viene registrato con: token_id, strategy (MM/ARB),
#          city, bucket, side, price, size, T_days, edge_at_entry.
#          Ogni 30 minuti calcola PnL realizzato, Sharpe, win_rate
#          per strategy × city × bucket_range.
#
#   [SM-2] AUTO-KILL DI STRATEGIE SPECIFICHE
#          Se una combinazione (strategy, city) ha Sharpe < -0.5
#          su un rolling window di 50 trade → disabled per 2h.
#          Se ARB ha win_rate < 40% su 20 trade → disabled 4h.
#          Log + print quando una strategia viene killata/riabilitata.
#
#   [SM-3] AUTO-TUNING PARAMETRI
#          Ogni ora aggiusta MIN_FORECAST_EDGE, MISPRICING_THRESHOLD,
#          MIN_MAKER_SPREAD basandosi sui risultati recenti:
#          - Edge medio realizzato >> atteso → abbassa soglie (più trade)
#          - Edge medio << atteso → alza soglie (più selettivo)
#          Bounds: ±30% dal valore baseline. Nessun cambio > 10% per step.
#
#   [SM-4] HOT-RELOAD CONFIGURAZIONE
#          Controlla config_override.json ogni 60s.
#          Se il file esiste e ha timestamp > ultimo check, sovrascrive
#          i parametri senza restart. Permette tuning manuale live.
#          Formato: {"MIN_FORECAST_EDGE": 0.020, "MAX_DD": 0.12}

@dataclass
class TradeRecord:
    """Record di un singolo fill per post-trade analysis."""
    ts:         float
    token_id:   str
    strategy:   str          # "MM" | "ARB"
    city:       str          # "seoul" | "london"
    bucket:     str          # "exact_10" | "or_higher_12" ecc.
    side:       str          # "BUY" | "SELL"
    price:      float
    size:       float
    T_days:     float
    edge_entry: float        # edge stimato al momento del trade
    pnl:        float = 0.0  # compilato a risoluzione
    resolved:   bool  = False


class SelfMonitor:
    """
    Sistema di self-monitoring, auto-tuning e auto-kill per TempBot.
    
    Uso nel loop principale:
        monitor.record_trade(...)          # dopo ogni fill
        monitor.record_resolution(...)     # quando un mercato risolve
        await monitor.run_cycle(capital)   # ogni LOOP_SLEEP
    """

    # ── Parametri baseline (bounds per auto-tuning) ─────────────
    PARAM_BOUNDS = {
        "MIN_FORECAST_EDGE":    (0.010, 0.040),
        "MISPRICING_THRESHOLD": (0.003, 0.015),
        "MIN_MAKER_SPREAD":     (0.005, 0.015),
        "MAX_DD":               (0.08,  0.20),
    }
    TUNE_STEP_MAX    = 0.10    # max variazione 10% per step
    TUNE_INTERVAL    = 3600    # auto-tune ogni 60 min
    ANALYSIS_INTERVAL = 1800   # post-trade analysis ogni 30 min
    RELOAD_INTERVAL  = 60      # hot-reload config ogni 60s
    KILL_WINDOW      = 50      # trade per kill-check MM
    ARB_KILL_WINDOW  = 20      # trade per kill-check ARB
    KILL_DURATION_MM = 7200    # kill MM strategy per 2h
    KILL_DURATION_ARB = 14400  # kill ARB strategy per 4h
    CONFIG_FILE      = "config_override.json"
    STATE_FILE       = "monitor_state.json"
    LEDGER_FILE      = "trades.jsonl"  # [LEDGER] append-only trade log

    def __init__(self):
        self.trades:    List[TradeRecord] = []
        self.killed:    Dict[str, float]  = {}  # key → ts_until
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
            self._edge_cache = {}
        self._ledgered_ids: set = set()    # [LEDGER] fill_ids already written
        self._resolved_keys: set = set()  # [DEDUP] (token_id, round(entry_ts,1)) already written
        self._trade_index: dict = {}       # [v4.30-memfix] token_id -> [TradeRecord] persistent across eviction
        self._ledger_load_known_ids()     # [LEDGER] restart-safe dedup + state recovery
        self._load_state()

    # ── [SM-1] REGISTRAZIONE TRADE ───────────────────────────────
    # [LEDGER] ──────────────────────────────────────────────────────────────
    def _ledger_write(self, event: dict):
        """Append one JSON line to trades.jsonl. Never raises, always flushes."""
        try:
            with open(self.LEDGER_FILE, "a", encoding="utf-8") as _lf:
                _lf.write(json.dumps(event, default=str) + "\n")
                _lf.flush()
        except Exception as _le:
            log.warning(f"LEDGER WRITE ERROR: {_le}")

    def _ledger_load_known_ids(self):
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
            log.warning(f"LEDGER LOAD ERROR: {_le}")

    def record_trade(self, token_id: str, strategy: str, city: str,
                     bucket: str, side: str, price: float, size: float,
                     T_days: float, edge: float, fill_id: str = None,
                     fair_prob: float = 0.0, mkt_prob: float = 0.0,
                     kelly_frac: float = 0.0, sizing_mode: str = "kelly"):  # [QUANT]
        r = TradeRecord(
            ts=time.time(), token_id=token_id, strategy=strategy,
            city=city, bucket=bucket, side=side, price=price,
            size=size, T_days=T_days, edge_entry=edge
        )
        self.trades.append(r)
        # Keep rolling window di 500 trade
        if len(self.trades) > 500:
            self.trades = self.trades[-500:]
        # [v4.27-mathfix] EXIT_FILL: mark matching open BUY records as resolved
        # so record_resolution() won't double-count positions sold before oracle.
        # Realize PnL = sell_proceeds - cost_basis for the exited contracts.
        if side == "SELL":
            _exit_contracts = size / price if price > 1e-9 else 0.0
            _remaining = _exit_contracts
            for _tr in self.trades:
                if _remaining <= 0:
                    break
                if (_tr.token_id == token_id and _tr.side == "BUY"
                        and not _tr.resolved and _tr.price > 1e-9):
                    _tr_contracts = _tr.size / _tr.price
                    _consumed = min(_tr_contracts, _remaining)
                    if _consumed >= _tr_contracts - 1e-9:
                        # Fully consumed — mark resolved with exit PnL
                        _tr.resolved = True
                        _tr.pnl = (price - _tr.price) * _tr_contracts
                    else:
                        # Partially consumed — split: resolve consumed portion
                        _old_notional = _tr.size
                        _consumed_notional = _consumed * _tr.price
                        _tr.size = _old_notional - _consumed_notional
                        # PnL for consumed portion only
                        _tr.pnl += (price - _tr.price) * _consumed
                    _remaining -= _consumed
        # [LEDGER] Write ENTRY or EXIT_FILL event
        _fid = str(fill_id) if fill_id else None
        if _fid and _fid in self._ledgered_ids:
            return  # already persisted in a previous session
        _event_type = "EXIT_FILL" if side == "SELL" else "ENTRY"
        self._ledger_write({
            "event":           _event_type,
            "ts":              r.ts,
            "fill_id":         _fid,
            "token_id":        token_id,
            "strategy":        strategy,
            "city":            city,
            "bucket":          bucket,
            "side":            side,
            "price":           round(price, 6),
            "size_contracts":  round(size / price, 4) if price > 1e-9 else None,
            "notional_usd":    round(size, 6),
            "Tdays":           round(T_days, 4),
            "edge_entry":      round(edge, 6),
            "fair_prob":       round(fair_prob, 4),   # [QUANT]
            "mkt_prob":        round(mkt_prob, 4),    # [QUANT]
            "kelly_frac":      round(kelly_frac, 6),  # [QUANT]
            "sizing_mode":     sizing_mode,           # [QUANT]
            "exit_reason":     strategy if side == "SELL" else None,
            "condition_id":    getattr(STATE, "_fill_cid_cache", {}).get(token_id, None),  # [P1D]
            "version":         "v2-bias-patch-stable",  # [v4.29-bias] dashboard filter tag
        })
        if _fid:
            self._ledgered_ids.add(_fid)
        # [v4.30-memfix] Persist BUY entries in _trade_index (immune to 500-cap eviction)
        if side == "BUY":
            self._trade_index.setdefault(token_id, []).append(r)

    def record_resolution(self, token_id: str, resolved_yes: bool):
        """Compila PnL per tutti i fill aperti su questo token.
        [v4.27-mathfix] r.size is notional_usd (contracts*price), not contract count.
        Correct PnL: BUY+YES = payout - cost = (notional/price)*1 - notional
                      BUY+NO  = -notional (total loss of cost basis)
        """
        # [v4.30-memfix] Check both rolling window AND persistent index
        _all_records = list(self.trades)
        for _idx_r in self._trade_index.get(token_id, []):
            if _idx_r not in _all_records:
                _all_records.append(_idx_r)
        for r in _all_records:
            if r.token_id == token_id and not r.resolved:
                # [v4.31-fix1] SELLs are exit fills, never open positions — skip oracle PnL
                if r.side == "SELL":
                    r.resolved = True  # mark so it won't be re-checked
                    continue
                r.resolved = True
                # [v4.27-mathfix] contracts = notional / price; payout = contracts * $1
                _contracts = r.size / r.price if r.price > 1e-9 else 0.0
                if r.side == "BUY":
                    r.pnl = (_contracts - r.size if resolved_yes
                             else -r.size)
                else:  # SELL
                    r.pnl = (-(_contracts - r.size) if resolved_yes
                             else r.size)
                # [DEDUP] skip if this exact fill was already resolved in a previous session
                _rk = (token_id, round(r.ts, 1))
                if _rk in self._resolved_keys:
                    continue
                # [LEDGER] Write RESOLUTION event once PnL is finalised
                self._ledger_write({
                    "event":        "RESOLUTION",
                    "ts":           time.time(),
                    "fill_id":      None,
                    "token_id":     token_id,
                    "resolved_yes": resolved_yes,
                    "entry_side":   r.side,
                    "entry_price":        round(r.price, 6),
                    "entry_ts":           r.ts,
                    "size_contracts":     round(r.size / r.price, 4) if r.price > 1e-9 else None,
                    "entry_notional_usd": round(r.size, 6),
                    "strategy":           r.strategy,
                    "city":         r.city,
                    "bucket":       r.bucket,
                    "pnl_usd":      round(r.pnl, 6),
                    "Tdays":        round(r.T_days, 4),
                    "edge_entry":   round(r.edge_entry, 6),
                    "resolved":     True,
                    "version":      "v2-bias-patch-stable",  # [v4.29-bias] dashboard filter tag
                })
                self._resolved_keys.add(_rk)

    # ── [SM-1] POST-TRADE ANALYSIS ───────────────────────────────
    def analyze(self) -> dict:
        """
        Calcola metriche per strategy × city su trade risolti.
        Ritorna dict con Sharpe, win_rate, avg_edge_realized per gruppo.
        """
        resolved = [r for r in self.trades if r.resolved]
        if len(resolved) < 5:
            return {}

        groups: Dict[str, list] = defaultdict(list)
        for r in resolved:
            key = f"{r.strategy}_{r.city}"
            groups[key].append(r)

        results = {}
        for key, recs in groups.items():
            pnls = np.array([r.pnl for r in recs])
            wins = sum(1 for r in recs if r.pnl > 0)
            results[key] = {
                "n":              len(recs),
                "total_pnl":      float(pnls.sum()),
                "win_rate":       wins / len(recs),
                "avg_pnl":        float(pnls.mean()),
                "sharpe":         (float(pnls.mean() / pnls.std())
                                   if pnls.std() > 1e-9 else 0.0),
                "avg_edge_entry": float(np.mean([r.edge_entry for r in recs])),
                "avg_edge_real":  float(pnls.mean() / max(np.mean([r.size for r in recs]), 1)),
            }

        return results

    def _print_analysis(self, results: dict):
        if not results:
            return
        log.info(f"📊 POST-TRADE ANALYSIS ({datetime.now().strftime('%H:%M:%S')})")
        for key, m in sorted(results.items()):
            status = "✅" if m["sharpe"] > 0.3 else ("⚠️ " if m["sharpe"] > -0.3 else "🔴")
            print(f"  {status} {key:<18} n={m['n']:>3} "
                  f"pnl={m['total_pnl']:>+7.2f} "
                  f"wr={m['win_rate']:.0%} "
                  f"sharpe={m['sharpe']:>+.2f} "
                  f"edge_entry={m['avg_edge_entry']:.3f} "
                  f"edge_real={m['avg_edge_real']:>+.3f}")
        

    # ── [SM-2] AUTO-KILL ─────────────────────────────────────────
    def is_killed(self, strategy: str, city: str) -> bool:
        """True se questa combinazione strategy+city è killata."""
        key = f"{strategy}_{city}"
        key_global = f"{strategy}_all"
        now = time.time()
        if self.killed.get(key, 0) > now:
            return True
        if self.killed.get(key_global, 0) > now:
            return True
        return False

    def _check_kills(self, results: dict):
        """
        Auto-kill basato su:
        - MM: Sharpe < -0.5 su rolling 50 trade → kill 2h
        - ARB: win_rate < 40% su 20 trade → kill 4h
        - Riabilita automaticamente dopo durata
        """
        now = time.time()

        # Riabilita strategie scadute
        for key in list(self.killed.keys()):
            if self.killed[key] <= now:
                del self.killed[key]
                strategy, city = key.split("_", 1)
                print(f"✅ AUTO-KILL REVOCATO: {strategy}/{city}")
                log.info(f"AUTOKILL REVOKED: {key}")

        for key, m in results.items():
            strategy, city = key.split("_", 1)

            # MM kill: Sharpe basso
            if strategy == "MM" and m["n"] >= self.KILL_WINDOW:
                if m["sharpe"] < -0.5:
                    until = now + self.KILL_DURATION_MM
                    self.killed[key] = until
                    print(f"🔴 AUTO-KILL MM/{city}: sharpe={m['sharpe']:.2f} → pausa 2h")
                    log.warning(f"AUTOKILL MM/{city}: sharpe={m['sharpe']:.2f}")

            # ARB kill: win_rate bassa
            if strategy == "ARB" and m["n"] >= self.ARB_KILL_WINDOW:
                if m["win_rate"] < 0.40:
                    until = now + self.KILL_DURATION_ARB
                    self.killed[f"ARB_all"] = until
                    print(f"🔴 AUTO-KILL ARB: win_rate={m['win_rate']:.0%} → pausa 4h")
                    log.warning(f"AUTOKILL ARB: win_rate={m['win_rate']:.0%}")

    # ── [SM-3] AUTO-TUNING PARAMETRI ─────────────────────────────
    def _auto_tune(self, results: dict, capital: float):
        """
        Aggiusta MIN_FORECAST_EDGE, MISPRICING_THRESHOLD, MIN_MAKER_SPREAD
        basandosi sul rapporto edge_real / edge_entry:
        
        ratio > 1.2  → edge sottostimato → abbassa soglie (più trade)
        ratio < 0.6  → edge sovrastimato → alza soglie (più selettivo)
        0.6-1.2      → stabile → no cambio
        
        Nessun cambio > 10% per step. Bounds ±30% dal baseline.
        """
        tuned = {}
        for key, m in results.items():
            if m["n"] < 10: continue
            strategy = key.split("_")[0]
            edge_entry = m["avg_edge_entry"]
            edge_real  = m["avg_edge_real"]
            if edge_entry <= 0: continue
            ratio = edge_real / edge_entry

            if strategy == "MM":
                param = "MIN_FORECAST_EDGE"
            else:
                param = "MISPRICING_THRESHOLD"

            current = getattr(sys.modules[__name__], param, None)
            if current is None: continue
            baseline = self.param_baseline[param]
            lo, hi   = self.PARAM_BOUNDS[param]

            if ratio > 1.2:
                # Edge reale > atteso → possiamo essere meno selettivi
                delta = -current * 0.05
            elif ratio < 0.6:
                # Edge reale < atteso → più selettivi
                delta = current * 0.08
            else:
                continue

            # Cap delta al 10%
            delta = max(-current*self.TUNE_STEP_MAX,
                        min(current*self.TUNE_STEP_MAX, delta))
            new_val = max(lo, min(hi, current + delta))

            if abs(new_val - current) > 1e-6:
                tuned[param] = (current, new_val, ratio)

        if tuned:
            print(f"\n🔧 AUTO-TUNE ({datetime.now().strftime('%H:%M')})")
            for param, (old, new, ratio) in tuned.items():
                direction = "▼" if new < old else "▲"
                print(f"   {direction} {param}: {old:.4f} → {new:.4f}  "
                      f"(ratio={ratio:.2f})")
                log.info(f"AUTOTUNE {param}: {old:.4f} → {new:.4f} ratio={ratio:.2f}")
                # Applica globalmente
                globals()[param] = new
            print()

    # ── [SM-4] HOT-RELOAD CONFIGURAZIONE ─────────────────────────
    def _hot_reload(self):
        """
        Controlla config_override.json. Se modificato, aggiorna parametri
        senza restart.

        Supporta sia formato piatto che annidato per sezione:
          Piatto:   { "MIN_FORECAST_EDGE": 0.020, "LOOP_SLEEP": 5 }
          Annidato: { "risk": { "MAX_PORTFOLIO_DD": 0.12 },
                      "trading": { "MIN_FORECAST_EDGE": 0.020 } }

        [v4.14-C2] Il vecchio codice iterava i top-level keys senza flatten:
        un file { "risk": { "MAX_DD": ... } } non applicava nulla perché
        "risk" non è in PARAM_BOUNDS. Ora flatteniamo automaticamente:
        le sezioni dict vengono esplose nel loro contenuto, i valori scalari
        vengono applicati direttamente.
        """
        try:
            if not os.path.exists(self.CONFIG_FILE):
                return
            mtime = os.path.getmtime(self.CONFIG_FILE)
            if mtime <= self.last_config_mtime:
                return
            self.last_config_mtime = mtime
            with open(self.CONFIG_FILE) as f:
                overrides = json.load(f)

            # [v4.14-C2] Flatten: esplode i dizionari annidati (un solo livello)
            flat: dict = {}
            ignored_sections = []
            for key, val in overrides.items():
                if isinstance(val, dict):
                    # Sezione annidato: { "risk": { "MAX_DD": 0.12 } }
                    for sub_key, sub_val in val.items():
                        if isinstance(sub_val, dict):
                            # Massimo 1 livello di nesting supportato
                            ignored_sections.append(f"{key}.{sub_key}")
                        else:
                            flat[sub_key] = sub_val
                else:
                    flat[key] = val

            if ignored_sections:
                log.warning(f"HOT-RELOAD: sezioni ignorate (>1 livello nesting): {ignored_sections}")

            applied = []
            skipped = []
            allowed = (set(self.PARAM_BOUNDS.keys()) | {
                "TAKE_PROFIT_PCT", "PARTIAL_PROFIT_PCT", "POSITION_EXIT_INTERVAL",  # [v4.19-exit]
                "LOOP_SLEEP", "MC_PATHS", "MAX_PORTFOLIO_DD",
                "MIN_DEPTH_USD", "MAX_RUIN_PROB", "CANCEL_MIN_WAIT",
                "MAX_ORDERS_PER_CYCLE", "MAX_TOTAL_EXPOSURE_FRAC",
                "MAX_TOKEN_EXPOSURE_FRAC", "REENTRY_COOLDOWN_SEC",  # [v4.27]
                "MAX_FAMILY_WORSTCASE_LOSS_USD", "MAX_FAMILY_OUTSIDE_LOSS_USD",
                "MAX_FAMILY_COST_USD", "ENABLE_FAMILY_RISK_GATE",
                "MAX_FAMILY_SELL_WORSENING_USD",  # [FAMILY]
                "STOPLOSS_SHADOW_MODE",  # [PHASE3]
            })
            for param, value in flat.items():
                if param in allowed:
                    # [FAMILY] family_risk params live in module, not globals
                    if param.startswith("MAX_FAMILY_") or param.startswith("ENABLE_FAMILY_"):
                        old = getattr(family_risk, param, None)
                    else:
                        old = globals().get(param)
                    try:
                        new_val = type(old)(value) if old is not None else value
                    except (ValueError, TypeError) as e:
                        log.warning(f"HOT-RELOAD: conversione tipo fallita per {param}: {e}")
                        continue
                    # [FAMILY] family_risk params live in the module, not globals
                    if param.startswith("MAX_FAMILY_") or param.startswith("ENABLE_FAMILY_"):
                        setattr(family_risk, param, new_val)
                    else:
                        globals()[param] = new_val
                    applied.append(f"{param}: {old} → {new_val}")
                else:
                    skipped.append(param)

            if applied:
                print(f"🔄 HOT-RELOAD: {len(applied)} param aggiornati")
                for a in applied: print(f"   {a}")
                log.info(f"HOT-RELOAD applied: {applied}")
            if skipped:
                log.debug(f"HOT-RELOAD: param non nella whitelist ignorati: {skipped}")

        except Exception as e:
            log.warning(f"HOT-RELOAD ERROR: {e}")

    # ── PERSISTENZA STATO ─────────────────────────────────────────

    # ── [P1B] COMPUTE METRICS FROM LEDGER ──────────────────────
    def compute_metrics(self, daily_reset_ts: float = 0.0) -> dict:
        """Compute dedup-safe portfolio metrics from trades.jsonl.
        RESOLUTION events are deduplicated by (token_id, round(entry_ts,1)).
        Adds pnl_by_strategy, pnl_by_city, n_total_entries, daily_pnl_ledger."""
        import math as _m
        mx = {"win_rate": None, "sharpe": None, "sortino": None,
              "profit_factor": None, "expectancy": None,
              "avg_win": None, "avg_loss": None, "avg_win_loss_ratio": None,
              "calmar": None, "max_dd_usd": None, "total_pnl": None,
              "n_resolved": 0, "n_wins": 0, "n_losses": 0,
              "total_gross_win": 0.0, "total_gross_loss": 0.0,
              "n_total_entries": 0, "n_total_exits": 0,
              "pnl_by_strategy": {}, "pnl_by_city": {},
              "daily_pnl_ledger": 0.0,
              "last_update_ts": time.time(),
              "data_freshness_status": "ok",
              "sample_warning": None}
        try:
            if not os.path.exists(self.LEDGER_FILE):
                mx["sample_warning"] = "trades.jsonl missing"
                mx["data_freshness_status"] = "missing_ledger"
                return mx
            pnls = []
            _seen_resol = set()   # dedup key: (token_id, round(entry_ts,1))
            _by_strat = {}        # strategy -> [pnl]
            _by_city  = {}        # city -> [pnl]
            _by_city_strat = {}   # [PHASE4] "city_strategy" -> [pnl]
            n_entries = n_exits = 0
            daily_pnl_ledger = 0.0
            with open(self.LEDGER_FILE, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln: continue
                    try: obj = json.loads(ln)
                    except Exception: continue
                    evt = obj.get("event", "")
                    if evt == "ENTRY":   n_entries += 1; continue
                    if evt == "EXIT_FILL": n_exits += 1; continue
                    if evt != "RESOLUTION": continue
                    # --- dedup ---
                    _tid = obj.get("token_id", "")
                    _ets = round(float(obj.get("entry_ts") or obj.get("ts") or 0), 1)
                    _dk = (_tid, _ets)
                    if _dk in _seen_resol: continue
                    _seen_resol.add(_dk)
                    # --- accumulate ---
                    v = obj.get("pnl_usd")
                    if v is None: continue
                    pv = float(v)
                    pnls.append(pv)
                    strat = obj.get("strategy", "unknown")
                    city  = obj.get("city", "unknown")
                    _by_strat.setdefault(strat, []).append(pv)
                    _by_city.setdefault(city, []).append(pv)
                    _by_city_strat.setdefault(f"{city}_{strat}", []).append(pv)  # [PHASE4]
                    # daily pnl: ts is resolution time
                    if daily_reset_ts > 0 and float(obj.get("ts", 0)) >= daily_reset_ts:
                        daily_pnl_ledger += pv
            mx["n_total_entries"] = n_entries
            mx["n_total_exits"]   = n_exits
            mx["daily_pnl_ledger"] = round(daily_pnl_ledger, 6)
            # breakdowns
            mx["pnl_by_strategy"] = {s: round(sum(v), 6) for s, v in _by_strat.items()}
            mx["pnl_by_city"]     = {c: round(sum(v), 6) for c, v in _by_city.items()}
            # [PHASE4] City × Strategy breakdown for Seoul vs London diagnosis
            mx["pnl_by_city_strategy"] = {
                k: {"total_pnl": round(sum(v), 6), "n": len(v),
                     "win_rate": round(sum(1 for p in v if p > 0) / max(1, len(v)), 4)}
                for k, v in _by_city_strat.items()
            }
            if not pnls:
                mx["sample_warning"] = "No RESOLUTION events"; return mx
            n_ = len(pnls)
            if n_ < 30: mx["sample_warning"] = f"Small sample: {n_} trades (<30)"
            wins   = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            mx["n_resolved"]        = n_
            mx["n_wins"]            = len(wins)
            mx["n_losses"]          = len(losses)
            mx["win_rate"]          = round(len(wins) / n_, 4) if n_ else None
            mx["total_pnl"]         = round(sum(pnls), 6)
            mx["total_gross_win"]   = round(sum(wins), 6)
            mx["total_gross_loss"]  = round(sum(losses), 6)
            mx["avg_win"]           = round(sum(wins)/len(wins), 6) if wins else None
            mx["avg_loss"]          = round(sum(losses)/len(losses), 6) if losses else None
            if mx["avg_win"] and mx["avg_loss"]:
                mx["avg_win_loss_ratio"] = round(abs(mx["avg_win"]/mx["avg_loss"]), 4)
            gl = abs(sum(losses)) if losses else 0
            if gl > 0: mx["profit_factor"] = round(sum(wins)/gl, 4) if wins else 0.0
            if n_ > 0: mx["expectancy"] = round(sum(pnls)/n_, 6)
            if n_ > 1:
                _mean = sum(pnls)/n_
                _var  = sum((p-_mean)**2 for p in pnls)/(n_-1)
                _std  = _m.sqrt(_var) if _var > 0 else 0
                if _std > 0: mx["sharpe"] = round(_mean/_std, 4)
            if n_ > 1 and losses:
                _mean = sum(pnls)/n_
                _ds   = _m.sqrt(sum(p**2 for p in losses)/len(losses))
                if _ds > 0: mx["sortino"] = round(_mean/_ds, 4)
            cum = peak = max_dd = 0.0
            for p in pnls:
                cum += p
                if cum > peak: peak = cum
                dd = peak - cum
                if dd > max_dd: max_dd = dd
            mx["max_dd_usd"] = round(max_dd, 6)
            if max_dd > 0: mx["calmar"] = round(sum(pnls)/max_dd, 4)
        except Exception as _e:
            log.warning(f"COMPUTE_METRICS ERROR: {_e}")
            mx["sample_warning"] = f"Error: {_e}"
            mx["data_freshness_status"] = "error"
        return mx


    def _save_edge_cache(self):
        """[PHASE1B] Persist edge cache to disk after each new entry."""
        try:
            with open(self.EDGE_CACHE_FILE, "w") as f:
                json.dump(self._edge_cache, f)
        except Exception as e:
            log.warning(f"EDGE CACHE SAVE ERROR: {e}")

    def _save_state(self):
        try:
            _st = globals().get("STATE")
            # [FAMILY] Pre-compute positions dict for family_risk
            _live_pos = {
                tid: {
                    "size":       p.size,
                    "avg_price":  p.avg_price,
                    "notional":   p.notional,
                    "city":       p.city,
                    "title":      getattr(p, "title", ""),
                    "ts_updated": getattr(p, "ts_updated", 0.0),
                    "edge":       getattr(self, '_edge_cache', {}).get(tid, 0.0),
                }
                for tid, p in getattr(_st, "positions", {}).items()
                if (p.notional or 0.0) > 1e-6
            }
            _trades_summary = self.compute_metrics(daily_reset_ts=getattr(_st, "daily_reset_ts", 0.0))  # [PHASE1A] pre-compute
            state = {
                "killed": {k: v for k, v in self.killed.items()},
                "trades_summary": _trades_summary,
                "params": {
                    k: globals().get(k) for k in self.PARAM_BOUNDS
                },
                "ts": time.time(),
                # [PHASE1A] daily_pnl sourced from ledger (not runaway accumulator)
                "daily_pnl":           _trades_summary.get("daily_pnl_ledger", 0.0),
                "daily_reset_ts":      getattr(_st, "daily_reset_ts", 0.0),
                "daily_reset_capital": getattr(_st, "_daily_reset_capital", 0.0),
            "last_trade_ids": list(getattr(_st, "_last_trade_ids", set()))[-1000:],  # [P1A]
            # -- Live runtime snapshot (for dashboard) ----
            "capital":        getattr(_st, "capital", 0.0),
            "total_exposure": getattr(_st, "total_exposure", 0.0),
            "kill_until":     getattr(_st, "kill_until", 0.0),
            "positions": _live_pos,
            # -- KPI portfolio snapshot --
            "open_positions_count": sum(
                1 for p in getattr(_st, "positions", {}).values()
                if (p.notional or 0.0) > 1e-6),
            "exposure_by_city": (lambda pos: {
                city: round(sum(p.notional for p in pos.values()
                    if p.city == city and (p.notional or 0.0) > 1e-6), 6)
                for city in set(p.city for p in pos.values())
            })(getattr(_st, "positions", {})),
            "exposure_by_token": {
                tid: round(p.notional, 6)
                for tid, p in getattr(_st, "positions", {}).items()
                if (p.notional or 0.0) > 1e-6
            },
            "drawdown_pct": round(
                (getattr(_st, "peak_capital", 0.0) - getattr(_st, "capital", 0.0))
                / getattr(_st, "peak_capital", 1.0), 6)
                if getattr(_st, "peak_capital", 0.0) > 0 else 0.0,
            "peak_capital": getattr(_st, "peak_capital", 0.0),
            # -- Family-level risk snapshot --
            "family_positions": family_risk.build_families(_live_pos),
            "schema_version": 3,  # bump: added family_positions
            }
            with open(self.STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.warning(f"MONITOR SAVE ERROR: {e}")

    def _load_state(self):
        try:
            if not os.path.exists(self.STATE_FILE):
                return
            with open(self.STATE_FILE) as f:
                state = json.load(f)
            # Ripristina kills ancora attivi
            now = time.time()
            for k, v in state.get("killed", {}).items():
                if v > now:
                    self.killed[k] = v
                    log.info(f"MONITOR RESTORE KILL: {k} until {v-now:.0f}s")
            # Ripristina parametri tuned
            for param, val in state.get("params", {}).items():
                if val is not None and param in globals():
                    globals()[param] = val
            # [H4-FIX] Restore daily PnL if saved within current 24h window
            _st = globals().get("STATE")
            if _st is not None:
                _saved_reset_ts = float(state.get("daily_reset_ts", 0.0))
                _now_h4 = time.time()
                if _saved_reset_ts > 0 and (_now_h4 - _saved_reset_ts) < 86400:
                    # [PHASE1A] daily_pnl recomputed from ledger on save; restore 0 as placeholder
                    _st.daily_pnl            = float(state.get("daily_pnl", 0.0))  # ledger-sourced now
                    _st.daily_reset_ts       = _saved_reset_ts
                    _dc = state.get("daily_reset_capital")
                    if _dc:
                        _st._daily_reset_capital = float(_dc)
                    log.info(f"MONITOR RESTORE daily_pnl={_st.daily_pnl:.4f} "
                             f"reset_capital={_st._daily_reset_capital:.2f}")
            # [P1A] Restore _last_trade_ids
            _saved_tids = state.get("last_trade_ids")
            if _saved_tids and isinstance(_saved_tids, list) and _st is not None:
                _st._last_trade_ids = set(str(x) for x in _saved_tids)
                log.info(f"P1A RESTORE: {len(_st._last_trade_ids)} trade_ids from state")
            log.info(f"MONITOR STATE LOADED from {self.STATE_FILE}")
        except Exception as e:
            log.warning(f"MONITOR LOAD ERROR: {e}")

    # ── CICLO PRINCIPALE ─────────────────────────────────────────
    async def run_cycle(self, capital: float):
        """
        Chiamato ad ogni iteration del trading loop.
        Esegue i 4 sistemi in base ai loro intervalli.
        """
        now = time.time()

        # [SM-4] Hot-reload ogni 60s
        if now - self.last_reload > self.RELOAD_INTERVAL:
            self._hot_reload()
            self.last_reload = now

        # [SM-1] + [SM-2] + [SM-3] ogni 30 min
        if now - self.last_analysis > self.ANALYSIS_INTERVAL:
            results = self.analyze()
            self._print_analysis(results)
            self._check_kills(results)
            self.last_analysis = now

        # [SM-3] Auto-tune ogni 60 min
        if now - self.last_tune > self.TUNE_INTERVAL:
            results = self.analyze()
            if results:
                self._auto_tune(results, capital)
            self.last_tune = now
            self._save_state()

        # [DASH] Throttled state save every 30s for dashboard freshness
        if now - getattr(self, "_last_dash_save", 0) > 30:
            self._save_state()
            self._last_dash_save = now


# Istanza globale
MONITOR = SelfMonitor()

# [v4.2-H2] Adverse selection tracking: fill_direction per token
# Struttura: {token_id: deque([1=BUY_taker, 0=SELL_taker], maxlen=20)}
FILL_DIRECTION: Dict[str, deque] = {}  # [v4.13-IL9] tipo corretto: deque, non object

def record_fill_direction(token_id: str, side: str):
    """Registra direzione fill taker (BUY=informed buy, SELL=informed sell)."""
    if token_id not in FILL_DIRECTION:
        FILL_DIRECTION[token_id] = deque(maxlen=20)
    FILL_DIRECTION[token_id].append(1 if side=='BUY' else 0)

def adverse_selection_spread_mult(token_id: str) -> float:
    """[v4.2-H2] Se >75% taker sono BUY → spread x1.5 (informed buying)."""
    fills = FILL_DIRECTION.get(token_id)
    if not fills or len(fills) < 5:
        return 1.0
    ratio = sum(fills) / len(fills)
    if ratio > 0.75:
        log.warning(f'ADVERSE_SEL {token_id[:12]}: BUY_ratio={ratio:.0%} → spread x1.5')
        return 1.5
    if ratio < 0.25:
        log.warning(f'ADVERSE_SEL {token_id[:12]}: SELL_ratio={1-ratio:.0%} → spread x1.5')
        return 1.5
    return 1.0


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════
async def main_async(args):
    # [H1-FIX] Reuse module-level pool — avoid orphaned 200-worker pool on every restart
    asyncio.get_running_loop().set_default_executor(_exec_pool)
    global DRY_RUN
    print(f"🚨 DRY_RUN={DRY_RUN} | TempBot v4.22\n")

    # [FIX-C] Startup env sanity warnings — detect duplicate/conflicting variables.
    # These do NOT change any behavior; they only inform the operator.
    _env_cap_a = os.getenv("PM_CAPITAL_USDC", "")
    _env_cap_b = os.getenv("PMCAPITALUSDC", "")
    if _env_cap_b:
        if _env_cap_a != _env_cap_b:
            print(f"   ⚠️  CONFIG CONFLICT: PM_CAPITAL_USDC={_env_cap_a!r} vs "
                  f"PMCAPITALUSDC={_env_cap_b!r} — PMCAPITALUSDC is ignored by this bot")
        else:
            print(f"   ⚠️  CONFIG DUPLICATE: PMCAPITALUSDC={_env_cap_b!r} duplicates "
                  f"PM_CAPITAL_USDC — remove PMCAPITALUSDC from .env to avoid confusion")
    _env_botfn = os.getenv("BOT_FILENAME", "")
    if _env_botfn:
        print(f"   ℹ️  BOT_FILENAME={_env_botfn!r} (active; check .env for duplicate entries)")

    if not CLOB_AVAILABLE and not DRY_RUN:
        raise RuntimeError("py_clob_client non installato — usa DRY_RUN=true")

    pk = os.getenv("PM_PRIVATE_KEY")
    if not pk and not DRY_RUN:
        raise RuntimeError("PM_PRIVATE_KEY mancante")

    CAPITAL_ref  = [float(os.getenv("PM_CAPITAL_USDC") or 1000)]
    STATE.peak_capital = CAPITAL_ref[0]
    STATE._daily_reset_capital = CAPITAL_ref[0]  # [v4.22-fix-killswitch]
    _load_forecast_weights()  # [FEAT3] Load persisted EMA weights from disk

    # [v4.19-fix-funder] Derive EOA address from private key.
    # If PM_FUNDER_KEY is unset or equals EOA (no real separate proxy),
    # use EOA directly so SELL orders look for shares in the correct wallet.
    global RESOLVED_FUNDER
    _eoa_address = ""
    if pk:
        try:
            from eth_account import Account as _EthAccount
            _eoa_address = _EthAccount.from_key(pk).address
        except Exception as _ea_err:
            log.warning(f"EOA derivation failed: {_ea_err}")
    _funder_env = os.getenv("PM_FUNDER_KEY", "").strip()
    if _funder_env and _eoa_address and _funder_env.lower() != _eoa_address.lower():
        _funder = _funder_env
        log.info(f"FUNDER: using proxy wallet {_funder[:10]}...")
    else:
        _funder = _eoa_address or _funder_env
        log.info(f"FUNDER: using EOA as funder {_funder[:10]}... (PM_FUNDER_KEY same as EOA or unset)")
    RESOLVED_FUNDER = _funder

    STATE._last_redeem_check = time.time()  # [STABLE] skip primo redeem check all'avvio

    client = None
    if CLOB_AVAILABLE and pk:
        client = ClobClient(
            "https://clob.polymarket.com", key=pk,
            chain_id=POLYGON, funder=RESOLVED_FUNDER)  # [v4.19-fix-funder]
        client.set_api_creds(ApiCreds(
            api_key        = os.getenv("PM_API_KEY"),
            api_secret     = os.getenv("PM_API_SECRET"),
            api_passphrase = os.getenv("PM_API_PASSPHRASE"),
        ))
        asyncio.create_task(_latency_guard_loop(client))  # [CB-v1]
        log.info("[CB-LAT] LatencyGuard task avviato")

    # [v4.11-B1] Capital: verifica sempre il valore API reale all'avvio.
    # Il vecchio max(env, api) causava leva implicita 5.8x: con $13 reali
    # e PM_CAPITAL_USDC=$101 il bot operava con cap 75%=$75.75 su $13 reali.
    # [v4.14-C3] Aggiunto retry con backoff esponenziale: 3 tentativi (2s, 4s, 8s)
    # prima di valutare il fallback. Se dopo i retry l'API è ancora irraggiungibile:
    #   - capital < 5 (wallet vuoto):  halt secco, nessun fallback
    #   - API irraggiungibile:         fallback su PM_CAPITAL_USDC con DRY_RUN forzato
    # Il fallback su env NON attiva il trading reale (DRY_RUN=True): protegge dalla
    # leva implicita segnalando comunque il problema nei log senza spegnere il bot.
    real_cap = None
    for _retry in range(3):
        real_cap = await fetch_portfolio_capital(client, force=True)  # [v4.21-async-capital]
        if real_cap is not None:
            break
        wait = 2 ** (_retry + 1)  # 2s, 4s, 8s
        print(f"   ⚠️  Capital fetch tentativo {_retry+1}/3 fallito — retry in {wait}s...")
        await asyncio.sleep(wait)

    if real_cap is not None and real_cap >= 5.0:
        CAPITAL_ref[0] = real_cap
        STATE.peak_capital   = real_cap
        print(f"   💰 Capitale API: ${real_cap:.2f}")
        # FIX: inizializza daily tracking con capitale reale [v4.24-killfix]
        STATE.daily_pnl = 0.0
        STATE.daily_reset_ts = time.time()
        STATE._daily_reset_capital = real_cap   # FIX: era 0.0
        STATE.kill_until = 0.0                   # FIX: azzera kill residuo
        if not getattr(STATE, "_last_trade_ids", None): STATE._last_trade_ids = set()  # [P1A] preserve restored
        log.info(f"STARTUP: _daily_reset_capital={real_cap:.2f}, kill_until reset")
    elif real_cap is not None and real_cap < 5.0:
        if DRY_RUN:
            # In dry-run accettiamo qualsiasi capitale > 0: nessun ordine reale viene piazzato
            CAPITAL_ref[0] = real_cap
            STATE.peak_capital   = real_cap
            print(f"   💰 Capitale API: ${real_cap:.2f} (dry-run, sotto soglia $5 ma ok)")
        else:
            print(f"   ❌ HALT: capitale API ${real_cap:.2f} < $5.00 — ricaricare il wallet.")
            raise SystemExit("Capitale insufficiente per operare in sicurezza.")
    else:
        # API irraggiungibile dopo 3 retry: fallback su env ma forza DRY_RUN
        env_cap = float(os.getenv("PM_CAPITAL_USDC") or 0)
        if env_cap >= 5.0 and not args.dry_check:
            DRY_RUN = True
            CAPITAL_ref[0] = env_cap
            STATE.peak_capital   = env_cap
            print(f"   ⚠️  DEGRADED MODE: API capitale irraggiungibile dopo 3 retry.")
            print(f"   ⚠️  Usando PM_CAPITAL_USDC=${env_cap:.2f} — DRY_RUN forzato a True.")
            print(f"   ⚠️  Il bot NON eseguirà ordini reali finché l'API non torna disponibile.")
            log.warning(f"CAPITAL DEGRADED: API fallita, fallback env=${env_cap:.2f}, DRY_RUN=True")
        else:
            print(f"   ❌ HALT: impossibile leggere il capitale e PM_CAPITAL_USDC non impostato.")
            raise SystemExit("Capital fetch fallito e nessun fallback env disponibile.")

    # [v4.20-fix-cap] In DRY_RUN con capitale reale < $5, usa PM_CAPITAL_USDC o $100
    # Il capitale non viene mai speso in dry-run, serve solo per calibrare i size.
    if DRY_RUN and CAPITAL_ref[0] < 5.0:
        _dry_cap = float(os.getenv("PM_CAPITAL_USDC") or 0)
        if _dry_cap < 5.0:
            _dry_cap = 100.0
        CAPITAL_ref[0] = _dry_cap
        STATE.peak_capital   = _dry_cap
        print(f"   💾 DRY_RUN capital override: ${_dry_cap:.2f} (wallet vuoto)")
        log.info(f"DRY_RUN CAPITAL OVERRIDE: ${_dry_cap:.2f}")

    print(f"🚀 TEMPBOT v4.22 — Seoul/London Temperature Markets")
    print(f"   Capital: ${CAPITAL_ref[0]:.0f} | MinDepth: ${MIN_DEPTH_USD:.0f} "
          f"| MaxDD: {MAX_PORTFOLIO_DD:.0%} | MaxExp: {MAX_TOTAL_EXPOSURE_FRAC:.0%}")
    print(f"   Stack: WS + AsyncAPI + GARCH + FactorModel + GLS + PortfolioVaR")

    # Pre-carica GLS
    global GLS_CALIBRATION
    GLS_CALIBRATION = load_becker_gls()
    print(f"   GLS: {len(GLS_CALIBRATION)} bucket calibrati")

    # Fetch mercati iniziali per token_id WS subscription
    # (popola anche TOKEN_META con neg_risk e tick_size per ogni token)
    markets = await fetch_markets_async()
    token_ids = [
        clean_token_id(m.get("clobTokenIds") or m.get("clob_token_ids") or "")
        for m in markets
    ]
    token_ids = [t for t in token_ids if t]
    print(f"   WS: {len(token_ids)} token da sottoscrivere")
    print(f"   TOKEN_META: {len(TOKEN_META)} token con neg_risk/tick_size in cache")

    # [v4.12-S4] Verifica allowance CONDITIONAL token sul NegRisk Adapter.
    # Per VENDERE shares su mercati neg_risk, il CLOB Exchange ha bisogno di allowance
    # sul NegRiskAdapter (0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296) per il token
    # ConditionalToken. Senza questa allowance i SELL falliscono con
    # "not enough balance / allowance" anche con balance USDC sufficiente.
    # NB: questa è una verifica/warning — non corregge automaticamente (richiederebbe
    # una tx on-chain che è fuori scope del bot di trading).
    if client and CLOB_AVAILABLE:
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            # NEG_RISK_ADAPTER su Polygon (da py_clob_client/config.py)
            NEG_RISK_ADAPTER = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
            # Verifichiamo l'allowance USDC sul NegRisk exchange
            if hasattr(client, 'get_balance_allowance'):
                bal_resp = await _async_clob(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                if bal_resp and isinstance(bal_resp, dict):
                    allowances = bal_resp.get("allowances", {}) or {}
                    neg_risk_allowance = int(allowances.get(NEG_RISK_ADAPTER, 0) or 0)
                    if neg_risk_allowance < 10**6:  # meno di 1 USDC
                        print(f"   ⚠️  ALLOWANCE ALERT: NegRisk Adapter ({NEG_RISK_ADAPTER[:16]}...) "
                              f"ha allowance={neg_risk_allowance} — i SELL su neg_risk markets "
                              f"potrebbero fallire con 'not enough allowance'.")
                        print(f"      FIX: eseguire set_allowances.py o approvare manualmente "
                              f"il NegRisk Adapter sul wallet.")
                        log.warning(f"NEG_RISK ALLOWANCE BASSA: {neg_risk_allowance} — SELL potrebbero fallire")
                    else:
                        print(f"   ✅ NegRisk Adapter allowance OK: {neg_risk_allowance/1e6:.0f} USDC")
        except Exception as _e:
            log.debug(f"ALLOWANCE CHECK ERROR: {_e}")

    if args.dry_check:
        print(f"\n✅ DRY-CHECK — {len(markets)} mercati:")
        for m in markets:
            print(f"  [{m.get('active','?')}] {m.get('question','')[:70]}")
        print("\n✅ DRY-CHECK COMPLETE")
        return

    # [v4.14-WS] Lancia WS listener in un thread separato con il suo event loop.
    # websockets 16.0 con async for su connessioni che ricevono messaggi ad alta
    # frequenza (es. [] vuoti) satura l'event loop asyncio e impedisce al
    # trading_loop di girare. Soluzione: WS gira nel suo thread/loop isolato,
    # scrive su ORDERBOOK (dict condiviso thread-safe), trading_loop lo legge.
    import threading

    def ws_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ws_orderbook_listener(token_ids))
        except Exception as e:
            log.error(f"WS thread error: {e}")
        finally:
            loop.close()

    t = threading.Thread(target=ws_thread, daemon=True, name="ws-listener")
    t.start()
    log.info("WS listener avviato in thread separato")

    # Trading loop gira nell'event loop principale
    await trading_loop(client, args, CAPITAL_ref)

def main():
    parser = argparse.ArgumentParser(description="TempBot v4.22")
    parser.add_argument("--dry-check", action="store_true",
                        help="Fetch mercati, mostra ARB/forecast. Nessun ordine.")
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n⏹ TempBot fermato.")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"FATAL ERROR: {e}", flush=True)

if __name__ == "__main__":
    main()
