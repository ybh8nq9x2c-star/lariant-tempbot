#!/usr/bin/env python3
"""
TempBot Dashboard v3 — Clean rich TUI
Every number sourced from trades.jsonl / monitor_state.json / tempbot.log.
Zero mock data.  Zero hallucinated numbers.  Optimised for t3.micro.

Performance:
  - os.stat().st_mtime gating: files only re-parsed on actual change
  - tail_bytes(): O(8KB) for log reads, never loads full log
  - refresh_per_second=1, sleep(1) main loop
  - deque(maxlen=N) for fixed-memory log buffer
  - _recompute() runs in bg thread; UI reads snapshots under lock
"""

import os, sys, json, time, math, threading, collections, subprocess
from datetime import datetime
from pathlib import Path

try:
    from rich.live    import Live
    from rich.layout  import Layout
    from rich.panel   import Panel
    from rich.table   import Table
    from rich.text    import Text
    from rich.console import Console
    from rich         import box
except ImportError:
    sys.exit("rich not installed.  Run: pip3 install rich")

# ── paths ─────────────────────────────────────────────────────────────────────
BOT_DIR     = Path(__file__).parent
STATE_FILE  = BOT_DIR / "monitor_state.json"
TRADES_FILE = BOT_DIR / "trades.jsonl"
LOG_FILE    = BOT_DIR / "tempbot.log"

# ── tuning (t3.micro safe) ────────────────────────────────────────────────────
POLL_STATE_S  = 2       # state file check interval
POLL_TRADES_S = 5       # trades file check interval
POLL_LOG_S    = 1       # log file check interval
TAIL_LOG_B    = 8_192   # 8 KB log tail
MAX_TRADES_B  = 2 << 20 # 2 MB — read full trades.jsonl below this
LOG_ROWS      = 14      # visible log lines
EQUITY_PTS    = 50      # max points in PnL sparkline
FILL_HISTORY  = 8       # recent fills shown
SPARK         = "▁▂▃▄▅▆▇█"

# ── palette ───────────────────────────────────────────────────────────────────
S_HEAD = "bold cyan"
S_OK   = "bold green"
S_BAD  = "bold red"
S_WARN = "yellow"
S_DIM  = "dim"
S_WHT  = "bold white"
S_CYAN = "cyan"
S_BORD = "dim cyan"


# ── helpers ───────────────────────────────────────────────────────────────────
def sparkline(vals: list, w: int) -> str:
    """Block-char sparkline.  Pure function, no side effects."""
    if not vals or len(vals) < 2:
        return "─" * w
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return "─" * w
    step = max(1, len(vals) / w)
    return "".join(
        SPARK[int((vals[min(int(i * step), len(vals) - 1)] - mn) / (mx - mn) * 7)]
        for i in range(w)
    )


def tail_bytes(path: Path, n: int) -> str:
    """Read last *n* bytes of a file.  O(n), not O(filesize)."""
    try:
        sz = path.stat().st_size
        with open(path, "rb") as f:
            if sz > n:
                f.seek(-n, 2)
                raw = f.read()
                nl = raw.find(b"\n")
                raw = raw[nl + 1 :] if nl != -1 else raw
            else:
                raw = f.read()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def ts_to_hms(ts: float) -> str:
    """Unix ts → HH:MM:SS local, or '—' if zero."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ── thread-safe data store ────────────────────────────────────────────────────
class _Store:
    def __init__(self):
        self.lk = threading.Lock()
        # --- raw state (from monitor_state.json) ---
        self.state:      dict  = {}
        self.state_mt:   float = 0.0
        # --- computed from trades.jsonl ---
        self.trades_mt:  float = 0.0
        self.metrics:    dict  = {}
        self.recent_fills: list = []   # last N ENTRY/EXIT_FILL from FULL parse
        # edge_by_token: token_id -> edge_entry (from ENTRY events)
        self.edge_by_token: dict = {}
        self.equity_curve: list = []   # cumulative PnL after each RESOLUTION
        self.daily_pnl:  float = 0.0  # computed: sum(pnl_usd) where ts >= reset_ts
        # --- log ---
        self.log_mt:     float = 0.0
        self.log_lines         = collections.deque(maxlen=LOG_ROWS)
        # --- process ---
        self.bot_running: bool = False
        self.bot_pid: int | None = None


DS = _Store()


# ── background pollers ────────────────────────────────────────────────────────
def _poll_state():
    while True:
        try:
            if STATE_FILE.exists():
                mt = STATE_FILE.stat().st_mtime
                if mt != DS.state_mt:
                    data = json.loads(STATE_FILE.read_text())
                    with DS.lk:
                        DS.state    = data
                        DS.state_mt = mt
        except Exception:
            pass
        time.sleep(POLL_STATE_S)


def _poll_trades():
    while True:
        try:
            if TRADES_FILE.exists():
                mt = TRADES_FILE.stat().st_mtime
                if mt != DS.trades_mt:
                    DS.trades_mt = mt
                    _recompute()
        except Exception:
            pass
        time.sleep(POLL_TRADES_S)


def _recompute():
    """Full-file metrics pass.  Only called when trades.jsonl mtime changes."""
    try:
        fsize = TRADES_FILE.stat().st_size
        if fsize <= MAX_TRADES_B:
            raw = TRADES_FILE.read_text(errors="replace").splitlines()
        else:
            raw = tail_bytes(TRADES_FILE, MAX_TRADES_B).splitlines()

        # ── get daily_reset_ts from state ─────────────────────────────────────
        with DS.lk:
            daily_reset_ts = DS.state.get("daily_reset_ts", 0.0)

        resolutions = []
        fills       = []          # all ENTRY + EXIT_FILL
        equity      = 0.0
        eq_pts      = []
        daily_pnl   = 0.0
        edge_by_tok = {}          # token_id -> edge_entry (latest ENTRY)

        for line in raw:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            evt = ev.get("event", "")
            if evt == "RESOLUTION":
                pnl = ev.get("pnl_usd") or 0.0
                equity += pnl
                eq_pts.append(equity)
                resolutions.append(ev)
                if ev.get("ts", 0) >= daily_reset_ts:
                    daily_pnl += pnl
            elif evt in ("ENTRY", "EXIT_FILL"):
                fills.append(ev)
                # Build edge lookup: keep latest edge for each token
                if evt == "ENTRY":
                    tid = ev.get("token_id", "")
                    edge = ev.get("edge_entry")
                    if tid and edge is not None:
                        edge_by_tok[tid] = float(edge)

        # ── performance metrics (RESOLUTION-only) ─────────────────────────────
        m: dict = {}
        if resolutions:
            pnls   = [r.get("pnl_usd") or 0.0 for r in resolutions]
            wins   = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            n      = len(pnls)
            mu     = sum(pnls) / n

            m["n_trades"]      = n
            m["win_rate"]      = 100.0 * len(wins) / n
            m["total_pnl"]     = sum(pnls)
            m["avg_win"]       = sum(wins)   / len(wins)   if wins   else 0.0
            m["avg_loss"]      = sum(losses) / len(losses) if losses else 0.0

            gross_loss = abs(sum(losses))
            m["profit_factor"] = (
                abs(sum(wins)) / gross_loss if gross_loss > 1e-9 else float("inf")
            )

            if n > 1:
                var   = sum((p - mu) ** 2 for p in pnls) / n
                std   = math.sqrt(var) if var > 1e-12 else 1e-9
                neg   = [p - mu for p in pnls if p < mu]
                d_var = sum(x ** 2 for x in neg) / n if neg else 1e-12
                d_std = math.sqrt(d_var) if d_var > 1e-12 else 1e-9
                m["sharpe"]  = mu / std   * math.sqrt(252)
                m["sortino"] = mu / d_std * math.sqrt(252)
            else:
                m["sharpe"] = m["sortino"] = 0.0

            # max drawdown
            peak = dd_max = running = 0.0
            for p in pnls:
                running += p
                peak   = max(peak, running)
                if peak > 1e-9:
                    dd_max = max(dd_max, (peak - running) / peak)
            m["max_dd"]     = dd_max
            m["expectancy"] = mu

        m["n_entries"] = sum(1 for f in fills if f.get("event") == "ENTRY")
        m["n_exits"]   = sum(1 for f in fills if f.get("event") == "EXIT_FILL")

        # ── recent fills: last N from the FULL parse (not tail) ───────────────
        recent = fills[-FILL_HISTORY:]

        with DS.lk:
            DS.metrics        = m
            DS.equity_curve   = eq_pts[-EQUITY_PTS:]
            DS.recent_fills   = recent
            DS.daily_pnl      = daily_pnl
            DS.edge_by_token  = edge_by_tok

    except Exception:
        pass


def _poll_log():
    while True:
        try:
            if LOG_FILE.exists():
                mt = LOG_FILE.stat().st_mtime
                if mt != DS.log_mt:
                    DS.log_mt = mt
                    raw   = tail_bytes(LOG_FILE, TAIL_LOG_B)
                    lines = [l for l in raw.splitlines() if l.strip()][-LOG_ROWS:]
                    with DS.lk:
                        DS.log_lines.clear()
                        DS.log_lines.extend(lines)
        except Exception:
            pass
        time.sleep(POLL_LOG_S)


def _poll_pid():
    while True:
        try:
            r = subprocess.run(
                ["pgrep", "-f", "tempbot_v4"],
                capture_output=True, text=True,
            )
            pids = r.stdout.strip().splitlines()
            with DS.lk:
                DS.bot_running = bool(pids)
                DS.bot_pid     = int(pids[0]) if pids else None
        except Exception:
            pass
        time.sleep(5)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PANEL BUILDERS — every number sourced from DS (which reads real files)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _header() -> Panel:
    now = datetime.now().strftime("%H:%M:%S")
    with DS.lk:
        running   = DS.bot_running
        pid       = DS.bot_pid
        state     = DS.state.copy()
        m         = DS.metrics.copy()
        daily_pnl = DS.daily_pnl
        n_fills   = len(state.get("last_trade_ids", []))

    # Capital: live from STATE.capital (API-synced) — fall back to daily_reset_capital
    capital = state.get("capital") or state.get("daily_reset_capital", 0.0)

    # Exposure: sum only positions with notional > 0 (filter ghost-resolved positions)
    raw_positions = state.get("positions", {})
    exposure = sum(
        p.get("notional", 0.0) for p in raw_positions.values()
        if (p.get("notional") or 0) > 1e-6
    )

    # Realized PnL: lifetime total from trades.jsonl (ground truth ledger)
    total_pnl = m.get("total_pnl", 0.0)

    kill = state.get("kill_until", 0.0) or 0.0

    # ── status chip ───────────────────────────────────────────────────────────
    if kill > time.time():
        mins   = int((kill - time.time()) / 60)
        status = Text(" ● KILL-SWITCH (%dm) " % mins, style="bold white on red")
    elif running:
        status = Text(" ● LIVE ", style="bold white on green")
    else:
        status = Text(" ● STOPPED ", style="bold white on red")

    g = Table.grid(expand=True)
    g.add_column(ratio=3)
    g.add_column(ratio=7, justify="center")
    g.add_column(ratio=2, justify="right")

    # left: name + status + PID
    left = Text()
    left.append("◆ TEMPBOT ", style=S_HEAD)
    left.append("v4.27  ", style="dim cyan")
    left.append_text(status)
    if pid:
        left.append("  PID %d" % pid, style=S_DIM)

    # centre: real numbers
    c = Text(justify="center")
    c.append("CAPITAL ", style=S_DIM)
    c.append("$%.2f" % capital, style=S_WHT)
    c.append("  │  ", style=S_DIM)
    c.append("EXPOSURE ", style=S_DIM)
    c.append("$%.2f" % exposure, style=S_WARN if exposure > 0 else S_DIM)
    c.append("  │  ", style=S_DIM)
    c.append("REALIZED ", style=S_DIM)
    c.append("$%+.2f" % total_pnl, style=S_OK if total_pnl >= 0 else S_BAD)
    c.append("  │  ", style=S_DIM)
    c.append("DAILY ", style=S_DIM)
    c.append("$%+.2f" % daily_pnl, style=S_OK if daily_pnl >= 0 else S_BAD)
    c.append("  │  ", style=S_DIM)
    c.append("DEDUPED ", style=S_DIM)
    c.append(str(n_fills), style=S_CYAN)

    # right: clock
    right = Text(justify="right")
    right.append(now, style=S_HEAD)

    g.add_row(left, c, right)
    return Panel(g, box=box.ROUNDED, style=S_BORD, padding=(0, 1))


# ── middle-left: ACTIVE POSITIONS ─────────────────────────────────────────────
def _panel_positions() -> Panel:
    with DS.lk:
        state        = DS.state.copy()
        m            = DS.metrics.copy()
        edge_by_tok  = DS.edge_by_token.copy()

    # Source of truth: state["positions"] written by bot every 30s from STATE.capital
    raw_pos = state.get("positions", {})

    # Filter ghost positions (price=0 / resolved-on-chain but still in API)
    active = [
        {"token_id": tid, **p}
        for tid, p in raw_pos.items()
        if (p.get("notional") or 0) > 1e-6
    ]

    tbl = Table(
        box=box.SIMPLE, show_header=True, header_style=S_HEAD,
        expand=True, show_edge=False, padding=(0, 1),
    )
    tbl.add_column("MARKET",   ratio=4)
    tbl.add_column("SIDE",     justify="center", ratio=1)
    tbl.add_column("SIZE",     justify="right",  ratio=2)
    tbl.add_column("AVG",      justify="right",  ratio=2)
    tbl.add_column("NOTIONAL", justify="right",  ratio=2)
    tbl.add_column("EDGE",     justify="right",  ratio=2)

    n_total   = len(raw_pos)
    n_active  = len(active)
    n_ghost   = n_total - n_active

    if not active:
        msg = ("No live positions (state has %d ghost-resolved)" % n_ghost
               if n_total else "Awaiting first fill or state update")
        tbl.add_row(Text(msg, style="dim italic"), "", "", "", "", "")
    else:
        by_notl = sorted(active, key=lambda x: x.get("notional", 0), reverse=True)[:12]
        for p in by_notl:
            tid   = p.get("token_id", "")
            city  = str(p.get("city", "?"))[:22].upper()
            size  = p.get("size", 0)
            avg   = p.get("avg_price", 0)
            notl  = p.get("notional", 0)

            # Edge: prefer state["positions"]["edge"] (live), fall back to trades.jsonl
            edge  = p.get("edge") or edge_by_tok.get(tid)
            if edge is not None and edge != 0.0:
                edge_str  = "%+.3f" % edge
                edge_sty  = S_OK if edge > 0 else S_BAD
            else:
                edge_str  = "—"
                edge_sty  = S_DIM

            tbl.add_row(
                city,
                Text("BUY", style=S_OK),   # positions are always BUY (long-only)
                "%.2f" % size,
                "%.3f" % avg,
                "$%.2f" % notl,
                Text(edge_str, style=edge_sty),
            )

    total_notl = sum(p.get("notional", 0) for p in active)
    return Panel(
        tbl,
        title="[bold cyan]◆ ACTIVE POSITIONS[/]",
        subtitle="[dim]%d live  %d ghost  exposure $%.2f[/]" % (n_active, n_ghost, total_notl),
        box=box.ROUNDED, border_style=S_BORD,
    )


# ── middle-right: RECENT FILLS ───────────────────────────────────────────────
def _panel_fills() -> Panel:
    with DS.lk:
        fills = list(DS.recent_fills)
        m     = DS.metrics.copy()

    tbl = Table(
        box=box.SIMPLE, show_header=True, header_style=S_HEAD,
        expand=True, show_edge=False, padding=(0, 1),
    )
    tbl.add_column("TYPE",  justify="center", ratio=2)
    tbl.add_column("CITY",  ratio=3)
    tbl.add_column("SIDE",  justify="center", ratio=1)
    tbl.add_column("PRICE", justify="right",  ratio=2)
    tbl.add_column("SIZE",  justify="right",  ratio=2)
    tbl.add_column("EDGE",  justify="right",  ratio=2)
    tbl.add_column("TIME",  justify="right",  ratio=3)

    if not fills:
        if not TRADES_FILE.exists():
            msg = "trades.jsonl not found — AWAITING FILLS"
        elif not m:
            msg = "SYSTEM READY — AWAITING DATA"
        else:
            msg = "No ENTRY/EXIT_FILL events in ledger"
        tbl.add_row(
            Text("—", style=S_DIM), Text(msg, style="dim italic"),
            "", "", "", "", "",
        )
    else:
        for f in reversed(fills):           # newest first
            evt   = f.get("event", "?")
            city  = str(f.get("city", "?"))[:18].upper()
            side  = f.get("side", "?")
            price = f.get("price", 0)
            size  = f.get("size_contracts", 0)
            ts    = f.get("ts", 0)
            edge  = f.get("edge_entry")
            if edge is not None and edge != 0.0:
                edge_str = "%+.3f" % edge
                edge_sty = S_OK if edge > 0 else S_BAD
            else:
                edge_str = "—"
                edge_sty = S_DIM
            tbl.add_row(
                Text(evt, style="bold green" if evt == "ENTRY" else "bold cyan"),
                city,
                Text(side, style=S_OK if side == "BUY" else S_BAD),
                "%.3f" % price,
                "%.2f" % size,
                Text(edge_str, style=edge_sty),
                Text(ts_to_hms(ts), style=S_DIM),
            )

    n_e = m.get("n_entries", 0)
    n_x = m.get("n_exits", 0)
    n_r = m.get("n_trades", 0)
    return Panel(
        tbl,
        title="[bold cyan]◆ RECENT FILLS[/]",
        subtitle="[dim]%d entries  %d exits  %d resolutions[/]" % (n_e, n_x, n_r),
        box=box.ROUNDED, border_style=S_BORD,
    )


# ── bottom-left: EXECUTION LOG ───────────────────────────────────────────────
def _panel_log() -> Panel:
    with DS.lk:
        lines = list(DS.log_lines)

    t = Text()
    if not lines:
        t.append("  Waiting for log data…", style="dim italic")
    else:
        for line in lines:
            # color by content
            if any(k in line for k in ("ERROR", "CRIT", "FATAL")):
                s = "red"
            elif "WARN" in line:
                s = "yellow"
            elif "KILL" in line:
                s = "bold red"
            elif any(k in line for k in ("ENTRY", " BUY")):
                s = "green"
            elif any(k in line for k in ("EXIT", " SELL", "FILL")):
                s = "cyan"
            elif line.lstrip().startswith("[DBG"):
                s = "dim"
            else:
                s = "dim white"
            t.append("  %s\n" % line[-80:], style=s)

    return Panel(
        t,
        title="[bold cyan]◆ EXECUTION LOG[/]",
        subtitle="[dim]tail %s[/]" % LOG_FILE.name,
        box=box.ROUNDED, border_style=S_BORD,
    )


# ── bottom-centre: TRADER METRICS ────────────────────────────────────────────
def _panel_metrics() -> Panel:
    with DS.lk:
        m         = DS.metrics.copy()
        state     = DS.state.copy()
        daily_pnl = DS.daily_pnl
        n_ids     = len(state.get("last_trade_ids", []))

    # Capital: live from STATE.capital (API-synced by bot every 30s)
    capital   = state.get("capital") or state.get("daily_reset_capital", 0.0)
    total_pnl = m.get("total_pnl", 0.0)  # lifetime from trades.jsonl

    tbl = Table(
        box=None, show_header=False, expand=True,
        show_edge=False, padding=(0, 1),
    )
    tbl.add_column("Metric", style="dim white", ratio=5)
    tbl.add_column("Value",  justify="right",   ratio=4)

    def row(lbl, val, sty=S_WHT):
        tbl.add_row(lbl, Text(val, style=sty))

    def sep():
        tbl.add_row(Text("─" * 15, style=S_DIM), Text("─" * 10, style=S_DIM))

    if not m:
        tbl.add_row(
            Text("SYSTEM READY", style=S_HEAD),
            Text("AWAITING DATA", style="dim yellow"),
        )
    else:
        raw_pos  = state.get("positions", {})
        exposure = sum(
            p.get("notional", 0) for p in raw_pos.values()
            if (p.get("notional") or 0) > 1e-6
        )
        row("Capital (live)", "$%.2f" % capital)
        row("Exposure",
            "$%.2f" % exposure,
            S_WARN if exposure > capital * 0.5 else "white")
        row("Realized PnL",
            "$%+.2f" % total_pnl,
            S_OK if total_pnl >= 0 else S_BAD)
        row("Daily PnL",
            "$%+.2f" % daily_pnl,
            S_OK if daily_pnl >= 0 else S_BAD)
        sep()

        wr = m.get("win_rate", 0.0)
        row("Win Rate", "%.1f%%" % wr, S_OK if wr > 50 else S_WARN)

        sh = m.get("sharpe", 0.0)
        row("Sharpe", "%.2f" % sh, S_OK if sh > 1 else "white")

        so = m.get("sortino", 0.0)
        row("Sortino", "%.2f" % so, S_OK if so > 1 else "white")

        pf = m.get("profit_factor", 0.0)
        row("Profit Factor",
            "∞" if pf == float("inf") else "%.2f" % pf,
            S_OK if pf > 1.5 else S_WARN)

        dd = m.get("max_dd", 0.0)
        row("Max Drawdown",
            "%.1f%%" % (dd * 100),
            S_BAD if dd > 0.1 else "white")

        sep()
        row("Resolutions",
            str(m.get("n_trades", 0)), S_CYAN)
        row("Avg Win",
            "$%+.4f" % m.get("avg_win", 0), S_OK)
        row("Avg Loss",
            "$%+.4f" % m.get("avg_loss", 0), S_BAD)
        row("Expectancy",
            "$%+.4f" % m.get("expectancy", 0),
            S_OK if m.get("expectancy", 0) > 0 else S_BAD)
        row("Dedup IDs",
            str(n_ids), S_CYAN)

    return Panel(
        tbl,
        title="[bold cyan]◆ TRADER METRICS[/]",
        subtitle="[dim]src: trades.jsonl[/]",
        box=box.ROUNDED, border_style=S_BORD,
    )


# ── bottom-right: PnL CURVE ──────────────────────────────────────────────────
def _panel_pnl() -> Panel:
    with DS.lk:
        eq = list(DS.equity_curve)
        m  = DS.metrics.copy()

    total_pnl = m.get("total_pnl", 0.0)
    pnl_s     = S_OK if total_pnl >= 0 else S_BAD

    t = Text()

    if not eq:
        t.append("\n  AWAITING RESOLUTION DATA\n", style=S_HEAD)
        t.append("  (trades.jsonl → RESOLUTION events)\n", style=S_DIM)
        return Panel(
            t, title="[bold cyan]◆ PnL CURVE[/]",
            box=box.ROUNDED, border_style=S_BORD,
        )

    W, H = 34, 8
    mn, mx = min(eq), max(eq)
    rng    = mx - mn
    step   = max(1, len(eq) / W)
    samp   = [eq[min(int(i * step), len(eq) - 1)] for i in range(W)]

    t.append("  $%+.2f\n" % total_pnl, style="bold %s" % pnl_s)

    if rng > 1e-9:
        for ri in range(H, 0, -1):
            lo = mn + ((ri - 1) / H) * rng
            hi = mn + (ri       / H) * rng
            row_str = "  "
            for v in samp:
                if   v >= hi: row_str += "█"
                elif v >= lo: row_str += "▄"
                else:         row_str += " "
            t.append(row_str + "\n", style=pnl_s)
        t.append("  " + "─" * W + "\n", style=S_DIM)
        t.append(
            "  $%+.2f" % mn + " " * max(0, W - 14) + "$%+.2f\n" % mx,
            style=S_DIM,
        )
    else:
        t.append("  %s\n" % ("─" * W), style=pnl_s)

    n  = m.get("n_trades", 0)
    wr = m.get("win_rate", 0)
    pf = m.get("profit_factor", 0)
    t.append(
        "\n  %d resolutions  WR %.0f%%  PF %.2f" % (n, wr, pf),
        style=S_DIM,
    )

    return Panel(
        t, title="[bold cyan]◆ PnL CURVE[/]",
        subtitle="[dim]cumulative RESOLUTION pnl_usd[/]",
        box=box.ROUNDED, border_style=S_BORD,
    )


# ── layout assembly ───────────────────────────────────────────────────────────
def _make_layout() -> Layout:
    L = Layout(name="root")
    L.split_column(
        Layout(name="header", size=3),
        Layout(name="middle", ratio=3),
        Layout(name="bottom", ratio=3),
    )
    L["middle"].split_row(
        Layout(name="positions", ratio=3),
        Layout(name="fills",     ratio=2),
    )
    L["bottom"].split_row(
        Layout(name="log",     ratio=3),
        Layout(name="metrics", ratio=2),
        Layout(name="pnl",     ratio=2),
    )
    return L


def _update(L: Layout):
    L["header"].update(_header())
    L["positions"].update(_panel_positions())
    L["fills"].update(_panel_fills())
    L["log"].update(_panel_log())
    L["metrics"].update(_panel_metrics())
    L["pnl"].update(_panel_pnl())


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    for fn in (_poll_state, _poll_trades, _poll_log, _poll_pid):
        threading.Thread(target=fn, daemon=True).start()

    time.sleep(0.4)       # let state thread load before first compute
    _recompute()

    layout  = _make_layout()
    console = Console()

    with Live(layout, console=console, refresh_per_second=1, screen=True):
        while True:
            _update(layout)
            time.sleep(1)


if __name__ == "__main__":
    main()
