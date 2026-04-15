#!/usr/bin/env python3
"""
TempBot Monitor — Telegram Control Panel v2
============================================
Robust redeem engine + UX redesign.
Requires: pip install python-telegram-bot requests python-dotenv
"""

import os
import sys
import json
import time
import asyncio
import functools
import subprocess
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ── Config ──────────────────────────────────────────────────────────────────
load_dotenv(Path.home() / "polymarket-temp-bot" / ".env")

BOT_TOKEN    = os.getenv("TG_BOT_TOKEN", "").strip()
CHAT_ID      = int(os.getenv("TG_CHAT_ID", "0"))
FUNDER       = os.getenv("PM_FUNDER_KEY", "").strip()
BOT_DIR      = Path(os.getenv("BOT_DIR", str(Path.home() / "polymarket-temp-bot")))
LOG_FILE     = BOT_DIR / os.getenv("BOT_LOGFILE", "tempbot_final.log")
BOT_FILENAME = os.getenv("BOT_FILENAME", "tempbot_v427_stable_20260313.py")
TMUX_SESSION = os.getenv("TMUX_SESSION", "bot")
REDEEM_HISTORY_FILE = BOT_DIR / "redeem_history.json"

log = logging.getLogger("tg-monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

if not BOT_TOKEN:
    raise ValueError("TG_BOT_TOKEN not set in .env")

# ── pyclob availability ──────────────────────────────────────────────────────
PYCLOB_AVAILABLE = False
_ClobClient = None
try:
    from py_clob_client.client import ClobClient as _ClobClient  # type: ignore
    PYCLOB_AVAILABLE = True
except ImportError:
    pass

# ── Redeem History (lightweight persistent JSON) ─────────────────────────────
def _load_redeem_history() -> dict:
    try:
        if REDEEM_HISTORY_FILE.exists():
            return json.loads(REDEEM_HISTORY_FILE.read_text())
    except Exception:
        pass
    return {"success": [], "failed": [], "redeemed_ids": []}

def _save_redeem_history(h: dict) -> None:
    try:
        REDEEM_HISTORY_FILE.write_text(json.dumps(h, indent=2))
    except Exception as e:
        log.warning(f"Cannot save redeem history: {e}")

# In-memory dedup set, seeded from disk on startup
_rh_boot = _load_redeem_history()
_redeemed_ids: set = set(_rh_boot.get("redeemed_ids", []))

# Position cache for inline detail callbacks (index-based, refreshed per fetch)
_pos_cache: list = []

# ── Auth ─────────────────────────────────────────────────────────────────────
def is_authorized(chat_id: int) -> bool:
    if CHAT_ID == 0:
        log.warning(f"First access from chat_id={chat_id}. Set TG_CHAT_ID in .env!")
        return True
    return chat_id == CHAT_ID

# ── Async executor ───────────────────────────────────────────────────────────
async def _run_in_executor(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

# ── Formatting helpers ───────────────────────────────────────────────────────
def compact_title(title: str, max_len: int = 38) -> str:
    t = (title
         .replace("Will the highest temperature in ", "")
         .replace("Will the temperature in ", "")
         .replace("reach or exceed ", "≥")
         .replace("be below ", "<")
         .replace("degrees Celsius", "°C")
         .replace("degrees Fahrenheit", "°F")
         .replace("?", "").strip())
    return (t[:max_len - 1] + "…") if len(t) > max_len else t

def fmt_money(v: float) -> str:
    return f"${v:.2f}"

def fmt_pct(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.1f}%"

def fmt_ts(ts: float = 0.0) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    return dt.strftime("%m-%d %H:%M UTC")

# ── Terminal display helpers (monospace-aligned for <code> blocks) ────────────
def fmt_usd(v: float) -> str:
    """Right-aligned unsigned USD: "$  22.95" — columns align in <code>."""
    return f"$ {abs(v):>7.2f}"

def fmt_pnl(v: float) -> str:
    """Signed PnL: "+$  0.56" / "-$  0.12" — fixed width for alignment."""
    sign = "+" if v >= 0 else "-"
    return f"{sign}$ {abs(v):>6.2f}"

def fmt_wr(win_rate) -> str:
    """Win rate percentage or dash placeholder."""
    return f"{win_rate*100:>5.1f}%" if win_rate is not None else "  n/a "

# ── Bot process helpers ──────────────────────────────────────────────────────
def is_bot_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", f"python3.*{BOT_FILENAME}"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

def get_bot_pid() -> str:
    try:
        r = subprocess.run(["pgrep", "-f", f"python3.*{BOT_FILENAME}"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "N/A"
    except Exception:
        return "N/A"

def get_uptime() -> str:
    try:
        pid = get_bot_pid().split("\n")[0]
        if pid == "N/A":
            return "N/A"
        r = subprocess.run(["ps", "-o", "etime=", "-p", pid],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "N/A"
    except Exception:
        return "N/A"

def tail_log(n: int = 15, grep_filter: str = "") -> str:
    try:
        if grep_filter:
            cmd = f"grep -E '{grep_filter}' '{LOG_FILE}' | tail -{n}"
        else:
            cmd = f"tail -{n} '{LOG_FILE}'"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "— no logs —"
    except Exception:
        return "— log read error —"

# ── Polymarket API helpers ───────────────────────────────────────────────────
def get_portfolio_value() -> dict:
    if not FUNDER:
        return {"value": 0.0, "error": "FUNDER not set"}
    try:
        r = requests.get(f"https://data-api.polymarket.com/value?user={FUNDER}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return {"value": float(data[0].get("value", 0))}
        return {"value": 0.0, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"value": 0.0, "error": str(e)[:60]}

def get_clob_balance() -> float:
    """Return USDC cash balance from CLOB (separate from position value)."""
    try:
        _, stdout, _ = run_polymarket_cmd(
            ["clob", "balance", "--asset-type", "collateral"], timeout=12
        )
        # Output: "Balance: $15.70\n..."
        import re
        m = re.search(r"Balance:\s*\$?([\d.]+)", stdout)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return 0.0

def load_monitor_state() -> dict:
    """Load monitor_state.json from BOT_DIR. Returns empty dict on any error."""
    try:
        p = BOT_DIR / "monitor_state.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}

def qlib_dataset_count() -> int:
    """Return number of logged trades in trades.jsonl."""
    try:
        p = BOT_DIR / "trades.jsonl"
        if p.exists():
            return sum(1 for _ in p.open())
    except Exception:
        pass
    return 0

def get_positions() -> list:
    if not FUNDER:
        return []
    try:
        r = requests.get(f"https://data-api.polymarket.com/positions?user={FUNDER}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
        return []
    except Exception:
        return []

def _cli_env() -> dict:
    env = os.environ.copy()
    for key in ("PM_PRIVATE_KEY", "PM_FUNDER_KEY", "PM_API_KEY",
                "PM_API_SECRET", "PM_API_PASSPHRASE", "POLYGON_RPC_URL"):
        val = os.getenv(key, "")
        if val:
            env[key] = val
    return env

def run_polymarket_cmd(args: list, timeout: int = 30) -> tuple:
    """Returns (returncode, stdout, stderr)."""
    _cwd = str(BOT_DIR) if BOT_DIR.is_dir() else os.getcwd()
    try:
        r = subprocess.run(
            ["polymarket", "--signature-type", "eoa"] + args,
            capture_output=True, text=True, timeout=timeout,
            cwd=_cwd, stdin=subprocess.DEVNULL, env=_cli_env()
        )
        return r.returncode, r.stdout.strip()[:3000], r.stderr.strip()[:1000]
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout after {timeout}s"
    except Exception as e:
        return -2, "", str(e)[:200]

# ── Redeem Engine ────────────────────────────────────────────────────────────
def extract_condition_id(p: dict) -> str:
    """Try all known field name variants for conditionId."""
    for key in ("conditionId", "condition_id", "marketConditionId",
                "market_condition_id", "marketconditionid"):
        val = p.get(key, "")
        if val:
            return str(val)
    return ""

def classify_redeem_candidate(p: dict) -> str:
    """
    Returns one of:
      redeemable_ok    — redeemable=True, conditionId found
      redeemable_nocid — redeemable=True, conditionId missing
      closed_pending   — market end date passed, not yet resolved
      active           — open market
    """
    if p.get("redeemable", False):
        return "redeemable_ok" if extract_condition_id(p) else "redeemable_nocid"
    end_date = p.get("endDate", "")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if end_dt < datetime.now(timezone.utc):
                return "closed_pending"
        except Exception:
            pass
    return "active"

def _build_pyclob_client():
    if not PYCLOB_AVAILABLE or _ClobClient is None:
        return None
    pk = os.getenv("PM_PRIVATE_KEY", "").strip()
    if not pk or not FUNDER:
        return None
    try:
        POLYGON = 137
        return _ClobClient("https://clob.polymarket.com", key=pk,
                           chain_id=POLYGON, funder=FUNDER)
    except Exception as e:
        log.debug(f"pyclob client init error: {e}")
        return None

async def execute_redeem_batch(cids: list, titles: dict,
                               pos_map: dict = None) -> dict:
    """
    Attempt redeem for each conditionId.
    pos_map: optional {cid: position_dict} for NegRisk routing.
    pyclob first (if available), CLI fallback.
    Returns: {success, failed, skipped, errors}
    """
    results = {"success": [], "failed": [], "skipped": [], "errors": {}}

    to_do = [cid for cid in cids if cid not in _redeemed_ids]
    results["skipped"] = [cid for cid in cids if cid in _redeemed_ids]

    if not to_do:
        return results

    client = _build_pyclob_client()
    rh = _load_redeem_history()
    now_ts = time.time()

    for cid in to_do:
        title = titles.get(cid, cid[:16])
        success = False
        err_parts = []

        # Attempt 1: pyclob
        if client is not None:
            try:
                await asyncio.wait_for(
                    _run_in_executor(client.redeem_positions, condition_id=cid),
                    timeout=30.0
                )
                success = True
                log.info(f"REDEEM OK (pyclob) {cid[:16]}")
            except asyncio.TimeoutError:
                err_parts.append("pyclob timeout")
            except Exception as e:
                err_parts.append(f"pyclob: {str(e)[:80]}")

        # Attempt 2: CLI fallback — NegRisk vs standard
        if not success:
            try:
                pos = (pos_map or {}).get(cid, {})
                if pos.get("negRisk", False):
                    yes_wei = int(float(pos.get("size", 0)) * 1_000_000)
                    amounts_str = f"{yes_wei},0"
                    cli_args = ["ctf", "redeem-neg-risk",
                                "--condition", cid, "--amounts", amounts_str]
                else:
                    cli_args = ["ctf", "redeem",
                                "--condition", cid, "--index-sets", "1"]
                rc, stdout, stderr = await asyncio.wait_for(
                    _run_in_executor(run_polymarket_cmd, cli_args, 60),
                    timeout=65.0
                )
                if rc == 0:
                    success = True
                    log.info(f"REDEEM OK (cli) {cid[:16]}")
                else:
                    cli_err = (stderr or stdout)[:180]
                    err_parts.append(f"cli rc={rc}: {cli_err}")
            except asyncio.TimeoutError:
                err_parts.append("cli timeout")
            except Exception as e:
                err_parts.append(f"cli exc: {str(e)[:80]}")

        err_msg = " | ".join(err_parts)

        if success:
            results["success"].append(cid)
            _redeemed_ids.add(cid)
            rh["success"].append({"cid": cid, "title": title, "ts": now_ts})
        else:
            results["failed"].append(cid)
            results["errors"][cid] = err_msg
            rh["failed"].append({"cid": cid, "title": title, "ts": now_ts, "reason": err_msg})
            log.warning(f"REDEEM FAIL {cid[:16]}: {err_msg}")

    rh["redeemed_ids"] = list(_redeemed_ids)
    _save_redeem_history(rh)
    return results

def classify_positions(positions: list) -> dict:
    cats = {
        "redeemable_ok": [], "redeemable_nocid": [], "closed_pending": [],
        "winners": [], "losers": [], "dust": [],
        "redeemable_win": [],   # resolved market, bot WON (cashPnl > 0, has value)
        "redeemable_loss": [],  # resolved market, bot LOST (value = 0)
    }
    for p in positions:
        cls = classify_redeem_candidate(p)
        if cls in ("redeemable_ok", "redeemable_nocid"):
            cats[cls].append(p)
            # Also split into won/lost for display purposes
            pnl = float(p.get("cashPnl", 0))
            val = float(p.get("currentValue", 0))
            if pnl > 0 or val > 0.05:
                cats["redeemable_win"].append(p)
            else:
                cats["redeemable_loss"].append(p)
        elif cls == "closed_pending":
            cats["closed_pending"].append(p)
        else:
            val = float(p.get("currentValue", 0))
            pnl = float(p.get("cashPnl", 0))
            if val < 0.10:
                cats["dust"].append(p)
            elif pnl >= 0:
                cats["winners"].append(p)
            else:
                cats["losers"].append(p)
    for k in cats:
        cats[k].sort(key=lambda x: float(x.get("currentValue", 0)), reverse=True)
    return cats

# ── Keyboard builders ────────────────────────────────────────────────────────
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Execute Redeem",  callback_data="redeem_menu"),
         InlineKeyboardButton("⚙️ Bot Control",     callback_data="bot_control")],
        [InlineKeyboardButton("System Status",      callback_data="status"),
         InlineKeyboardButton("Portfolio",          callback_data="portfolio")],
        [InlineKeyboardButton("Positions",          callback_data="positions"),
         InlineKeyboardButton("Orders",             callback_data="orders")],
        [InlineKeyboardButton("Logs",               callback_data="logs_menu"),
         InlineKeyboardButton("Diagnostics",        callback_data="diagnostics")],
    ])

def back_btn(dest="menu", label="◀️ Menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=dest)]])

def confirm_kb(action: str, cancel_dest: str = "menu"):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma", callback_data=f"confirm_{action}"),
        InlineKeyboardButton("❌ Annulla",  callback_data=cancel_dest),
    ]])

# ── Command handlers ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return
    log.info(f"Start from chat_id={chat_id}")
    wallet = f"{FUNDER[:8]}…{FUNDER[-6:]}" if FUNDER else "not set"
    text = (
        f"⚡ <b>TEMPBOT TERMINAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"CHAT ID   <code>{chat_id}</code>\n"
        f"WALLET    <code>{wallet}</code>\n"
        f"ENGINE    <code>{BOT_FILENAME}</code>\n\n"
        "Select a module:"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=main_menu_keyboard())

async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "⚡ <b>TEMPBOT TERMINAL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nSelect a module:",
        parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
    )

# ── Button dispatcher ────────────────────────────────────────────────────────
_DISPATCH: dict = {}  # populated after handler definitions

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(update.effective_chat.id):
        await query.answer("⛔ Non autorizzato!", show_alert=True)
        return
    await query.answer()
    data = query.data

    handler = _DISPATCH.get(data)
    if handler:
        try:
            await handler(query, ctx)
        except Exception as e:
            log.error(f"Handler error [{data}]: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ <b>Errore</b>\n<code>{str(e)[:200]}</code>",
                    parse_mode=ParseMode.HTML, reply_markup=back_btn()
                )
            except Exception:
                pass
    elif data.startswith("pos_page:"):
        try:
            await _handle_positions_paged(query, ctx, int(data[9:]))
        except Exception as e:
            log.error(f"pos_page error: {e}")
    else:
        log.warning(f"Unknown callback: {data}")

# ── Status ───────────────────────────────────────────────────────────────────
async def handle_status(query, ctx):
    running  = is_bot_running()
    pid, uptime = get_bot_pid(), get_uptime()
    # [UPGRADE] Primary source: monitor_state.json (zero API calls)
    ms        = load_monitor_state()
    capital   = ms.get("capital", 0.0)
    exposure  = ms.get("total_exposure", 0.0)
    daily_pnl = ms.get("daily_pnl", 0.0)
    ts_obj    = ms.get("trades_summary", {})
    win_rate  = ts_obj.get("win_rate")   # None before first resolution
    n_res     = ts_obj.get("n_resolved", 0)
    # Fallback to live API only if monitor_state is empty/fresh
    if capital == 0.0:
        pv        = get_portfolio_value()
        clob_cash = get_clob_balance()
        capital   = pv["value"] + clob_cash
    qlib_n    = qlib_dataset_count()
    icon     = "🟢" if running else "🔴"
    pnl_dot  = "🟩" if daily_pnl >= 0 else "🟥"
    last     = tail_log(4, "ARB|ORDER|CAPITAL|EXPOSURE")
    text = (
        f"⚡ <b>SYSTEM STATUS</b>  │  <code>{fmt_ts()}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{icon} <b>{'RUNNING' if running else 'HALTED'}</b>   "
        f"PID <code>{pid:>6}</code>   Up <code>{uptime}</code>\n\n"
        f"<b>CAPITAL </b>  <code>{fmt_usd(capital)}</code>\n"
        f"<b>EXPOSURE</b>  <code>{fmt_usd(exposure)}</code>\n\n"
        f"<b>DAILY PnL</b>  {pnl_dot} <code>{fmt_pnl(daily_pnl)}</code>   "
        f"<b>WIN RATE</b>  <code>{fmt_wr(win_rate)}</code>\n"
        f"<b>RESOLVED</b>  <code>{n_res:>4}</code>\n\n"
        f"🧠 <b>QLIB</b>  <code>{qlib_n:>4}</code> records\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<pre>{last[:700]}</pre>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Execute Redeem",  callback_data="redeem_menu"),
         InlineKeyboardButton("⚙️ Bot Control",     callback_data="bot_control")],
        [InlineKeyboardButton("Portfolio",          callback_data="portfolio"),
         InlineKeyboardButton("Positions",          callback_data="positions")],
        [InlineKeyboardButton("🔄 Refresh",         callback_data="status"),
         InlineKeyboardButton("◀️ Main Menu",        callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# ── Portfolio ────────────────────────────────────────────────────────────────
async def handle_portfolio(query, ctx):
    pv        = get_portfolio_value()
    clob_cash = get_clob_balance()
    positions = get_positions()
    cats      = classify_positions(positions)
    total_invested = sum(float(p.get("initialValue", 0)) for p in positions)
    total_current  = sum(float(p.get("currentValue", 0)) for p in positions)
    total_pnl      = sum(float(p.get("cashPnl", 0)) for p in positions)
    pnl_icon      = "📈" if total_pnl >= 0 else "📉"
    total_assets   = total_current + clob_cash
    # wins = open winners + redeemable wins (resolved market, bot won)
    n_wins        = len(cats["winners"]) + len(cats["redeemable_win"])
    n_redeem_win  = len(cats["redeemable_win"])
    n_redeem_loss = len(cats["redeemable_loss"])
    # [UPGRADE] Quant metrics from monitor_state.json (zero API overhead)
    ms        = load_monitor_state()
    capital   = ms.get("capital", total_assets)
    exposure  = ms.get("total_exposure", 0.0)
    daily_pnl = ms.get("daily_pnl", 0.0)
    ts_obj    = ms.get("trades_summary", {})
    win_rate  = ts_obj.get("win_rate")
    n_res     = ts_obj.get("n_resolved", 0)
    pnl_dot   = "🟩" if total_pnl  >= 0 else "🟥"
    dpnl_dot  = "🟩" if daily_pnl  >= 0 else "🟥"
    text = (
        f"⚡ <b>PORTFOLIO OVERVIEW</b>  │  <code>{fmt_ts()}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>CAPITAL </b>  <code>{fmt_usd(capital)}</code>\n"
        f"<b>EXPOSURE</b>  <code>{fmt_usd(exposure)}</code>\n"
        f"<b>CASH    </b>  <code>{fmt_usd(clob_cash)}</code>\n"
        f"<b>OPEN POS</b>  <code>{fmt_usd(total_current)}</code>   "
        f"PnL {pnl_dot} <code>{fmt_pnl(total_pnl)}</code>\n\n"
        f"<b>DAILY PnL</b>  {dpnl_dot} <code>{fmt_pnl(daily_pnl)}</code>   "
        f"<b>WIN RATE</b>  <code>{fmt_wr(win_rate)}</code>   "
        f"RESOLVED  <code>{n_res:>3}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ REDEEM WIN    <code>{n_redeem_win:>3}</code>\n"
        f"  REDEEM LOSS   <code>{n_redeem_loss:>3}</code>\n"
        f"  CLOSED/PEND   <code>{len(cats['closed_pending']):>3}</code>\n"
        f"  OPEN WIN      <code>{len(cats['winners']):>3}</code>   "
        f"LOSS  <code>{len(cats['losers']):>3}</code>   "
        f"DUST  <code>{len(cats['dust']):>3}</code>"
    )
    if pv.get("error"):
        text += f"\n\n🔴 API ERR: <code>{pv['error'][:80]}</code>"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Execute Redeem",  callback_data="redeem_menu"),
         InlineKeyboardButton("Positions",          callback_data="positions")],
        [InlineKeyboardButton("Orders",             callback_data="orders"),
         InlineKeyboardButton("System Status",      callback_data="status")],
        [InlineKeyboardButton("🔄 Refresh",         callback_data="portfolio"),
         InlineKeyboardButton("◀️ Main Menu",        callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# ── Positions (paginated, categorized) ───────────────────────────────────────
_PAGE_SIZE = 6

def _pos_icon(p: dict) -> str:
    """Sober terminal palette: ⚡ redeemable-win, 🟥 loss, 🟩 win, ⏳ pending."""
    if p.get("redeemable", False):
        pnl = float(p.get("cashPnl", 0))
        val = float(p.get("currentValue", 0))
        return "⚡" if (pnl > 0 or val > 0.05) else "🟥"
    end_date = p.get("endDate", "")
    if end_date:
        try:
            from datetime import timezone
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if end_dt < datetime.now(timezone.utc):
                return "⏳"
        except Exception:
            pass
    pnl = float(p.get("cashPnl", 0))
    return "🟩" if pnl >= 0 else "🟥"

def _build_pos_page(cats: dict, page: int) -> tuple:
    global _pos_cache
    # Show: won redeemables first, then open winners, then losers, then lost redeemables, then pending
    ordered = (
        cats["redeemable_win"] + cats["winners"] +
        cats["losers"] + cats["redeemable_loss"] + cats["closed_pending"]
    )
    # Deduplicate (a position may appear in redeemable_win AND redeemable_ok)
    seen = set()
    deduped = []
    for p in ordered:
        pid = p.get("asset", id(p))
        if pid not in seen:
            seen.add(pid)
            deduped.append(p)
    ordered = deduped
    _pos_cache = ordered
    total = len(ordered)
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    total_pnl = sum(float(p.get("cashPnl", 0)) for p in ordered)
    total_val = sum(float(p.get("currentValue", 0)) for p in ordered)
    pnl_dot   = "🟩" if total_pnl >= 0 else "🟥"
    text = (
        f"⚡ <b>OPEN POSITIONS</b>  <code>{total}</code> total  "
        f"pg <code>{page+1}/{total_pages}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"VAL <code>{fmt_usd(total_val)}</code>   "
        f"PnL {pnl_dot} <code>{fmt_pnl(total_pnl)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for p in ordered[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]:
        icon  = _pos_icon(p)
        title = compact_title(p.get("title", "???"), 28)
        val   = float(p.get("currentValue", 0))
        pnl   = float(p.get("cashPnl", 0))
        pct   = float(p.get("percentPnl", 0))
        text += (
            f"{icon} <code>{fmt_pnl(pnl)}</code>│<code>{fmt_usd(val)}</code> "
            f"<code>{fmt_pct(pct):>7}</code>\n"
            f"   <i>{title}</i>\n"
        )
    return text, total_pages, page

async def handle_positions(query, ctx):
    await _handle_positions_paged(query, ctx, 0)

async def _handle_positions_paged(query, ctx, page: int):
    positions = get_positions()
    if not positions:
        await query.edit_message_text(
            "📈 <b>Posizioni</b>\n━━━\n\n📭 Nessuna posizione.",
            parse_mode=ParseMode.HTML, reply_markup=back_btn()
        )
        return
    cats = classify_positions(positions)
    text, total_pages, page = _build_pos_page(cats, page)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"pos_page:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"pos_page:{page+1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.extend([
        [InlineKeyboardButton("🔄 Refresh",  callback_data="positions"),
         InlineKeyboardButton("💸 Redeem",   callback_data="redeem_menu")],
        [InlineKeyboardButton("◀️ Menu",      callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup(rows))

# ── Orders ───────────────────────────────────────────────────────────────────
async def handle_orders(query, ctx):
    _, stdout, stderr = run_polymarket_cmd(["clob", "orders"])
    raw = stdout or stderr or "no output"
    orders = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or any(line.startswith(c) for c in ["╭", "╰", "├", "│ ID", "─", "+"]):
            continue
        parts = [p.strip() for p in line.strip("│").split("│")]
        if len(parts) >= 4:
            orders.append(parts)

    if not orders:
        text = "📋 <b>Ordini Aperti</b>\n━━━\n\n📭 Nessun ordine sul CLOB"
    else:
        buy_orders  = [o for o in orders if len(o) > 1 and "BUY"  in o[1].upper()]
        sell_orders = [o for o in orders if len(o) > 1 and "SELL" in o[1].upper()]

        def fmt_o(o):
            oid   = (o[0][:10] + "…") if len(o[0]) > 10 else o[0]
            price = o[2] if len(o) > 2 else "?"
            size  = o[3] if len(o) > 3 else "?"
            side  = o[1] if len(o) > 1 else "?"
            icon  = "🟢" if "BUY" in side.upper() else "🔴"
            try:
                notional = f" ≈{fmt_money(float(size) * float(price))}"
            except Exception:
                notional = ""
            return f"{icon} <code>{oid}</code> {price}¢×{size}{notional}"

        text = f"📋 <b>Ordini Aperti</b> ({len(orders)})\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        if buy_orders:
            text += f"🟢 <b>BUY ({len(buy_orders)})</b>\n"
            for o in buy_orders[:6]:
                text += f"  {fmt_o(o)}\n"
            if len(buy_orders) > 6:
                text += f"  <i>+{len(buy_orders)-6}…</i>\n"
            text += "\n"
        if sell_orders:
            text += f"🔴 <b>SELL ({len(sell_orders)})</b>\n"
            for o in sell_orders[:6]:
                text += f"  {fmt_o(o)}\n"
            if len(sell_orders) > 6:
                text += f"  <i>+{len(sell_orders)-6}…</i>\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancella Tutti", callback_data="cancel_all"),
         InlineKeyboardButton("🔄 Refresh",        callback_data="orders")],
        [InlineKeyboardButton("◀️ Menu",             callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# ── Redeem Menu ──────────────────────────────────────────────────────────────
async def handle_redeem_menu(query, ctx):
    positions = get_positions()
    cats = classify_positions(positions)
    r_ok    = cats["redeemable_ok"]
    r_nocid = cats["redeemable_nocid"]
    total_val = sum(float(p.get("currentValue", 0)) for p in r_ok)
    text = (
        f"💸 <b>Redeem Center</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Pronte (CID ok): <b>{len(r_ok)}</b> — {fmt_money(total_val)}\n"
        f"⚠️ Manca CID:       <b>{len(r_nocid)}</b>\n"
        f"⏳ Closed/pending:  <b>{len(cats['closed_pending'])}</b>\n"
    )
    if r_ok:
        text += "\n<b>Da riscattare:</b>\n"
        for p in r_ok[:7]:
            val = float(p.get("currentValue", 0))
            text += f"  • {compact_title(p.get('title', '?'), 34)} <b>{fmt_money(val)}</b>\n"
        if len(r_ok) > 7:
            text += f"  <i>+{len(r_ok)-7} altre…</i>\n"
    else:
        text += "\n📭 Nessuna posizione redeemable con CID."

    rows = []
    if r_ok:
        rows.append([InlineKeyboardButton(
            f"💸 Redeem {len(r_ok)} posizioni", callback_data="do_redeem"
        )])
    rows.extend([
        [InlineKeyboardButton("📋 Cronologia",  callback_data="redeem_history"),
         InlineKeyboardButton("🔍 Diagnostics", callback_data="diagnostics")],
        [InlineKeyboardButton("🔄 Refresh",     callback_data="redeem_menu"),
         InlineKeyboardButton("◀️ Menu",         callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup(rows))

async def handle_do_redeem(query, ctx):
    """Show preview and ask for confirmation."""
    positions = get_positions()
    cats = classify_positions(positions)
    r_ok = cats["redeemable_ok"]
    if not r_ok:
        await query.edit_message_text(
            "💸 <b>Redeem</b>\n━━━\n\n✅ Nessuna posizione redeemable con CID.",
            parse_mode=ParseMode.HTML, reply_markup=back_btn("redeem_menu", "◀️ Redeem")
        )
        return
    total_val = sum(float(p.get("currentValue", 0)) for p in r_ok)
    text = (
        f"💸 <b>Conferma Redeem</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Stai per riscattare <b>{len(r_ok)} posizioni</b> per "
        f"un valore totale di <b>{fmt_money(total_val)}</b>.\n\n"
        f"<b>Posizioni:</b>\n"
    )
    for p in r_ok[:8]:
        val = float(p.get("currentValue", 0))
        cid = extract_condition_id(p)
        text += (f"  • {compact_title(p.get('title','?'), 32)} "
                 f"{fmt_money(val)} <code>{cid[:12]}…</code>\n")
    if len(r_ok) > 8:
        text += f"  <i>+{len(r_ok)-8} altre…</i>\n"
    text += "\nProcedere?"
    ctx.bot_data["pending_redeem"] = r_ok
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=confirm_kb("do_redeem", "redeem_menu"))

async def handle_confirm_redeem(query, ctx):
    """Execute the confirmed redeem batch."""
    r_ok = ctx.bot_data.pop("pending_redeem", None)
    if not r_ok:
        positions = get_positions()
        r_ok = classify_positions(positions)["redeemable_ok"]
    if not r_ok:
        await query.edit_message_text(
            "⚠️ Nessuna posizione da riscattare.",
            parse_mode=ParseMode.HTML, reply_markup=back_btn("redeem_menu", "◀️ Redeem")
        )
        return
    await query.edit_message_text(
        f"⏳ <b>Redeem in corso…</b>\n\nSto riscattando {len(r_ok)} posizioni.",
        parse_mode=ParseMode.HTML
    )
    # Build deduped cid→title map and cid→position map
    seen: dict = {}
    pos_map: dict = {}
    for p in r_ok:
        cid = extract_condition_id(p)
        if cid and cid not in seen:
            seen[cid] = compact_title(p.get("title", "?"))
            pos_map[cid] = p
    results = await execute_redeem_batch(list(seen.keys()), seen, pos_map=pos_map)

    n_ok   = len(results["success"])
    n_fail = len(results["failed"])
    n_skip = len(results["skipped"])
    text = (
        f"💸 <b>Redeem Completato</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Successo:       <b>{n_ok}</b>\n"
        f"❌ Falliti:        <b>{n_fail}</b>\n"
        f"⏭ Già riscattati: <b>{n_skip}</b>\n"
    )
    if results["success"]:
        text += "\n<b>OK:</b>\n"
        for cid in results["success"][:5]:
            text += f"  ✅ {seen.get(cid, cid[:14])}…\n"
        if n_ok > 5:
            text += f"  <i>+{n_ok-5}…</i>\n"
    if results["failed"]:
        text += "\n<b>Errori:</b>\n"
        for cid in results["failed"][:4]:
            err = results["errors"].get(cid, "?")[:60]
            text += f"  ❌ {seen.get(cid, cid[:12])}: <i>{err}</i>\n"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Redeem", callback_data="redeem_menu"),
         InlineKeyboardButton("◀️ Menu",           callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def handle_redeem_history(query, ctx):
    rh = _load_redeem_history()
    successes = rh.get("success", [])
    failures  = rh.get("failed", [])
    text = (
        f"📋 <b>Redeem History</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Totali: {len(successes)} ✅ / {len(failures)} ❌\n\n"
    )
    if successes:
        text += "<b>Ultimi successi:</b>\n"
        for e in reversed(successes[-8:]):
            text += f"  {fmt_ts(e.get('ts',0))}  {e.get('title','?')[:30]}\n"
        text += "\n"
    if failures:
        text += "<b>Ultimi errori:</b>\n"
        for e in reversed(failures[-5:]):
            reason = e.get("reason", "?")[:50]
            text += f"  {fmt_ts(e.get('ts',0))}  {e.get('title','?')[:22]}: <i>{reason}</i>\n"
    if not successes and not failures:
        text += "📭 Nessun redeem registrato."
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=back_btn("redeem_menu", "◀️ Redeem"))

# ── Logs ─────────────────────────────────────────────────────────────────────
async def handle_logs_menu(query, ctx):
    text = "📜 <b>Logs</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nScegli categoria:"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Recenti",  callback_data="logs_recent"),
         InlineKeyboardButton("🎯 ARB",      callback_data="logs_arb")],
        [InlineKeyboardButton("⚠️ Errori",   callback_data="logs_errors"),
         InlineKeyboardButton("📊 Exposure", callback_data="logs_exposure")],
        [InlineKeyboardButton("◀️ Menu",      callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def _handle_logs(query, grep_filter: str, title: str, refresh_cb: str):
    logs = tail_log(20, grep_filter)
    truncated = [l[:115] + "…" if len(l) > 115 else l for l in logs.split("\n")[-15:]]
    text = f"{title}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n<pre>{chr(10).join(truncated)[:3400]}</pre>"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Aggiorna", callback_data=refresh_cb),
         InlineKeyboardButton("◀️ Logs",     callback_data="logs_menu")],
        [InlineKeyboardButton("◀️ Menu",      callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def handle_logs_recent(query, ctx):
    await _handle_logs(query, "", "📜 <b>Log Recenti</b>", "logs_recent")

async def handle_logs_arb(query, ctx):
    await _handle_logs(query, "ARB|🎯|ORDER", "🎯 <b>ARB & Orders</b>", "logs_arb")

async def handle_logs_errors(query, ctx):
    await _handle_logs(query, "ERROR|WARNING|KILL|RUIN|Traceback",
                       "⚠️ <b>Errori & Warning</b>", "logs_errors")

async def handle_logs_exposure(query, ctx):
    await _handle_logs(query, "EXPOSURE|CAPITAL|BALANCE|CAP",
                       "📊 <b>Exposure & Capital</b>", "logs_exposure")

# ── Balance ───────────────────────────────────────────────────────────────────
async def handle_balance(query, ctx):
    _, stdout, stderr = run_polymarket_cmd(["clob", "balance", "--asset-type", "collateral"])
    pv = get_portfolio_value()
    output = stdout or stderr or "no output"
    text = (
        f"🏦 <b>Balance CLOB</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Portfolio Value: <b>{fmt_money(pv['value'])}</b>\n\n"
        f"<pre>{output[:2000]}</pre>"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_btn())

# ── Bot Control ───────────────────────────────────────────────────────────────
async def handle_bot_control(query, ctx):
    running = is_bot_running()
    pid, uptime = get_bot_pid(), get_uptime()
    icon = "🟢" if running else "🔴"
    text = (
        f"🔧 <b>Bot Control</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{icon} <b>{'ATTIVO' if running else 'FERMO'}</b>  "
        f"PID: <code>{pid}</code>  Up: {uptime}\n"
        f"File: <code>{BOT_FILENAME}</code>"
    )
    rows = []
    if running:
        rows.append([InlineKeyboardButton("🔴 Stop",    callback_data="stop_bot"),
                     InlineKeyboardButton("🔄 Restart", callback_data="restart_bot")])
        rows.append([InlineKeyboardButton("❌ Cancel Orders", callback_data="cancel_all"),
                     InlineKeyboardButton("🏦 Balance",        callback_data="balance")])
    else:
        rows.append([InlineKeyboardButton("🟢 Start", callback_data="start_bot")])
    rows.append([InlineKeyboardButton("◀️ Menu", callback_data="menu")])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup(rows))

async def handle_start_bot(query, ctx):
    if is_bot_running():
        await query.edit_message_text(
            f"🟢 <b>Bot già attivo</b>\nPID: <code>{get_bot_pid()}</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_btn("bot_control", "◀️ Control")
        )
        return
    try:
        subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    cmd = (f"cd {BOT_DIR} && source ~/venv/bin/activate && "
           f"python3 -u {BOT_FILENAME} 2>&1 | tee -a tempbot.log")
    subprocess.Popen(["tmux", "new-session", "-d", "-s", TMUX_SESSION, cmd],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(3)
    running = is_bot_running()
    text = (
        f"🟢 <b>Start Bot</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{'🟢 AVVIATO' if running else '🔴 ERRORE AVVIO'}\n"
    )
    if running:
        text += f"\n<pre>{tail_log(4)[:1000]}</pre>"
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=back_btn("bot_control", "◀️ Control"))

async def handle_stop_bot_prompt(query, ctx):
    if not is_bot_running():
        await query.edit_message_text(
            "🔴 <b>Bot già fermo</b>", parse_mode=ParseMode.HTML,
            reply_markup=back_btn("bot_control", "◀️ Control")
        )
        return
    await query.edit_message_text(
        "🔴 <b>Fermare il bot?</b>\n\nCancellerà anche gli ordini aperti.",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb("stop_bot", "bot_control")
    )

async def handle_stop_bot(query, ctx):
    await query.edit_message_text("⏳ Stop in corso…", parse_mode=ParseMode.HTML)
    run_polymarket_cmd(["clob", "cancel-all"])
    try:
        subprocess.run(["pkill", "-f", f"python3.*{BOT_FILENAME}"],
                       capture_output=True, timeout=10)
        subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    await asyncio.sleep(2)
    running = is_bot_running()
    text = (
        f"🔴 <b>Stop Bot</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{'⚠️ Ancora attivo!' if running else '✅ Fermato con successo'}\n"
        f"📋 Ordini cancellati"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=back_btn("bot_control", "◀️ Control"))

async def handle_restart_bot_prompt(query, ctx):
    await query.edit_message_text(
        "🔄 <b>Riavviare il bot?</b>\n\nStop → Cancel Orders → Start",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb("restart_bot", "bot_control")
    )

async def handle_restart_bot(query, ctx):
    await query.edit_message_text("⏳ Riavvio in corso…", parse_mode=ParseMode.HTML)
    run_polymarket_cmd(["clob", "cancel-all"])
    try:
        subprocess.run(["pkill", "-f", f"python3.*{BOT_FILENAME}"],
                       capture_output=True, timeout=10)
        subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    await asyncio.sleep(3)
    cmd = (f"cd {BOT_DIR} && source ~/venv/bin/activate && "
           f"python3 -u {BOT_FILENAME} 2>&1 | tee -a tempbot.log")
    subprocess.Popen(["tmux", "new-session", "-d", "-s", TMUX_SESSION, cmd],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(5)
    running = is_bot_running()
    text = (
        f"🔄 <b>Restart Bot</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{'🟢 RIAVVIATO' if running else '🔴 ERRORE RIAVVIO'}\n\n"
        f"<pre>{tail_log(4)[:1000]}</pre>"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=back_btn("bot_control", "◀️ Control"))

async def handle_cancel_all_prompt(query, ctx):
    await query.edit_message_text(
        "❌ <b>Cancellare tutti gli ordini aperti?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb("cancel_all", "orders")
    )

async def handle_cancel_all(query, ctx):
    await query.edit_message_text("⏳ Cancellazione ordini…", parse_mode=ParseMode.HTML)
    _, stdout, stderr = run_polymarket_cmd(["clob", "cancel-all"])
    output = stdout or stderr or "no output"
    text = (
        f"❌ <b>Cancel All Orders</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<pre>{output[:1800]}</pre>"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=back_btn("orders", "◀️ Ordini"))

# ── Diagnostics ───────────────────────────────────────────────────────────────
async def handle_diagnostics(query, ctx):
    positions = get_positions()
    cats = classify_positions(positions)
    rh = _load_redeem_history()
    pyclob_str = "✅ Disponibile" if PYCLOB_AVAILABLE else "❌ Non installato (CLI fallback)"
    text = (
        f"🔍 <b>Diagnostics</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Redeem Engine:</b>\n"
        f"  py_clob_client: {pyclob_str}\n"
        f"  CIDs riscattati (sessione): {len(_redeemed_ids)}\n"
        f"  Storico: {len(rh.get('success',[]))} OK / {len(rh.get('failed',[]))} errori\n\n"
        f"<b>Posizioni API:</b>\n"
        f"  Totali:               {len(positions)}\n"
        f"  Redeemable + CID:     <b>{len(cats['redeemable_ok'])}</b>\n"
        f"  Redeemable senza CID: {len(cats['redeemable_nocid'])}\n"
        f"  Closed/pending:       {len(cats['closed_pending'])}\n"
        f"  Winners:              {len(cats['winners'])}\n"
        f"  Losers:               {len(cats['losers'])}\n"
        f"  Dust (<$0.10):        {len(cats['dust'])}\n"
    )
    if cats["redeemable_nocid"]:
        text += "\n<b>Senza CID (campione):</b>\n"
        for p in cats["redeemable_nocid"][:3]:
            text += f"  ⚠️ {compact_title(p.get('title','?'), 35)}\n"
            log.warning(f"DIAG no-CID: {json.dumps({k: p.get(k) for k in p if 'id' in k.lower()})[:200]}")
    recent_fails = rh.get("failed", [])[-3:]
    if recent_fails:
        text += "\n<b>Ultimi errori redeem:</b>\n"
        for e in recent_fails:
            text += f"  ❌ {e.get('title','?')[:25]}: <i>{e.get('reason','?')[:45]}</i>\n"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Vai a Redeem", callback_data="redeem_menu"),
         InlineKeyboardButton("🔄 Refresh",      callback_data="diagnostics")],
        [InlineKeyboardButton("◀️ Menu",           callback_data="menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# ── Main menu helper ──────────────────────────────────────────────────────────
async def handle_main_menu(query, ctx):
    await query.edit_message_text(
        "⚡ <b>TEMPBOT TERMINAL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nSelect a module:",
        parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
    )

# ── Dispatch table ────────────────────────────────────────────────────────────
_DISPATCH.update({
    "menu":               handle_main_menu,
    "status":             handle_status,
    "portfolio":          handle_portfolio,
    "positions":          handle_positions,
    "orders":             handle_orders,
    "redeem_menu":        handle_redeem_menu,
    "do_redeem":          handle_do_redeem,
    "confirm_do_redeem":  handle_confirm_redeem,
    "redeem_history":     handle_redeem_history,
    "logs_menu":          handle_logs_menu,
    "logs_recent":        handle_logs_recent,
    "logs_arb":           handle_logs_arb,
    "logs_errors":        handle_logs_errors,
    "logs_exposure":      handle_logs_exposure,
    "balance":            handle_balance,
    "bot_control":        handle_bot_control,
    "start_bot":          handle_start_bot,
    "stop_bot":           handle_stop_bot_prompt,
    "confirm_stop_bot":   handle_stop_bot,
    "restart_bot":        handle_restart_bot_prompt,
    "confirm_restart_bot": handle_restart_bot,
    "cancel_all":         handle_cancel_all_prompt,
    "confirm_cancel_all": handle_cancel_all,
    "diagnostics":        handle_diagnostics,
})

# ── Scheduled Tasks ───────────────────────────────────────────────────────────
async def scheduled_status(ctx: ContextTypes.DEFAULT_TYPE):
    if CHAT_ID == 0:
        return
    running = is_bot_running()
    pv = get_portfolio_value()
    clob_cash = get_clob_balance()
    positions = get_positions()
    cats = classify_positions(positions)
    total_pnl    = sum(float(p.get("cashPnl", 0)) for p in positions)
    total_current = sum(float(p.get("currentValue", 0)) for p in positions)
    total_assets = total_current + clob_cash
    n_redeemable = len(cats["redeemable_ok"])
    n_wins_redeem = len(cats["redeemable_win"])
    # [FIX-B] Compute redeemable $ value from already-fetched positions.
    # redeemable_win = resolved markets where we won (currentValue > 0, redeemable=True).
    redeem_value = sum(
        float(p.get("currentValue", 0))
        for p in positions
        if p.get("redeemable") and float(p.get("cashPnl", 0)) > 0
    )
    icon    = "🟢" if running else "🔴"
    pnl_dot = "🟩" if total_pnl >= 0 else "🟥"
    # [FIX-B] Clearly separate: cash | open positions | redeemable | total
    text = (
        f"⚡ <b>PERIODIC REPORT</b>  │  <code>{fmt_ts()}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{icon} ENGINE  <b>{'RUNNING' if running else 'HALTED'}</b>\n\n"
        f"<b>CAPITAL </b>  <code>{fmt_usd(total_assets)}</code>\n"
        f"<b>CASH    </b>  <code>{fmt_usd(clob_cash)}</code>\n"
        f"<b>OPEN POS</b>  <code>{fmt_usd(total_current)}</code>   "
        f"PnL {pnl_dot} <code>{fmt_pnl(total_pnl)}</code>\n"
        f"<b>REDEEM  </b>  <code>{fmt_usd(redeem_value)}</code>   "
        f"({n_wins_redeem} wins pending)\n\n"
        f"POSITIONS  <code>{len(positions):>4}</code> open"
    )
    if not running:
        text += "\n\n🔴 <b>ALERT: ENGINE NOT RUNNING</b>"
    if n_wins_redeem > 0:
        text += f"\n⚡ <b>{n_wins_redeem} WIN(S) PENDING REDEMPTION</b>"
    elif n_redeemable > 0:
        text += f"\n⚡ <b>{n_redeemable} position(s) redeemable</b>"
    await ctx.bot.send_message(CHAT_ID, text, parse_mode=ParseMode.HTML,
                               reply_markup=main_menu_keyboard())

async def crash_watchdog(ctx: ContextTypes.DEFAULT_TYPE):
    if CHAT_ID == 0:
        return
    if not is_bot_running():
        last_err = tail_log(5, "ERROR|Traceback|Exception")
        text = (
            "🚨 <b>ALERT: TempBot CRASHATO/FERMO!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<pre>{last_err[:1800]}</pre>\n\nRiavviare?"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟢 Start",   callback_data="start_bot"),
            InlineKeyboardButton("🔄 Restart", callback_data="restart_bot"),
        ]])
        await ctx.bot.send_message(CHAT_ID, text, parse_mode=ParseMode.HTML,
                                   reply_markup=keyboard)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("⚡  TempBot Telegram Monitor — Terminal Edition")
    print("━" * 42)
    print(f"Bot:    {BOT_FILENAME}")
    print(f"Dir:    {BOT_DIR}")
    print(f"Log:    {LOG_FILE}")
    print(f"pyclob: {'✅ disponibile' if PYCLOB_AVAILABLE else '❌ non installato (CLI fallback)'}")
    print(f"Funder: {FUNDER[:10]}…" if FUNDER else "Funder: NOT SET")
    print()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("menu",   cmd_menu))
    app.add_handler(CommandHandler("status", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))

    if CHAT_ID != 0:
        jq = app.job_queue
        jq.run_repeating(scheduled_status, interval=6 * 3600, first=60)
        jq.run_repeating(crash_watchdog,   interval=120,      first=30)
        print(f"Alerts attivi → chat_id={CHAT_ID}")
    else:
        print("⚠️  TG_CHAT_ID non impostato — sicurezza e alert disabilitati")
        print("   Invia /start al bot per vedere il tuo chat_id nei log.")

    print("🚀 Polling avviato. Ctrl+C per fermare.\n")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
