#!/usr/bin/env python3
"""
data_collector.py — TempBot Ground Truth Dataset Collector
===========================================================
Standalone script — NO imports from tempbot.py, no side-effects on live bot.

Collects per-day, per-city ground-truth rows into append-safe CSVs:
  /home/ubuntu/polymarket-temp-bot/output/rksi_history.csv
  /home/ubuntu/polymarket-temp-bot/output/eglc_history.csv

CSV columns:
  date, city, station, wu_final_max_c, metar_max_c,
  ecmwf_c, gfs_c, icon_c, meteofrance_c,
  local_hour_utc, polymarket_buckets (JSON)

Usage:
  python3 data_collector.py --today                     # today both cities
  python3 data_collector.py --retro --days 60           # last 60 days
  python3 data_collector.py --date 2026-03-20           # specific date
  python3 data_collector.py --date 2026-03-20 --city london  # dry-run test

Env vars (optional — place in .env alongside this script):
  WU_API_KEY   Weather Underground / weather.com v1 API key
               Endpoint: api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json
               If unset, wu_final_max_c is recorded as None.
"""

# ── stdlib ────────────────────────────────────────────────────────────────────
import os
import sys
import json
import re
import logging
import argparse
import time
import functools
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── third-party ───────────────────────────────────────────────────────────────
import requests
import pandas as pd
from dotenv import load_dotenv

# ── Bootstrap ─────────────────────────────────────────────────────────────────
BOT_DIR    = Path(__file__).parent.resolve()
OUTPUT_DIR = BOT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BOT_DIR / ".env", override=False)   # never overrides real env vars

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_FILE = OUTPUT_DIR / "collector.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("data_collector")

# ── Config ────────────────────────────────────────────────────────────────────
WU_API_KEY: str = os.getenv("WU_API_KEY", "").strip()

STATIONS: dict[str, dict] = {
    "london": {
        "station":  "EGLC",
        "country":  "GB",
        "lat":       51.5048,
        "lon":        0.0495,
        "tz":        "Europe/London",
        "csv":       "eglc_history.csv",
    },
    "seoul": {
        "station":  "RKSI",
        "country":  "KR",
        "lat":       37.4691,
        "lon":      126.4510,
        "tz":        "Asia/Seoul",
        "csv":       "rksi_history.csv",
    },
}

FORECAST_MODELS: dict[str, str] = {
    "ecmwf_c":       "ecmwf_ifs04",
    "gfs_c":         "gfs_seamless",
    "icon_c":        "icon_seamless",
    "meteofrance_c": "meteofrance_seamless",
}

CSV_COLUMNS: list[str] = [
    "date", "city", "station",
    "wu_final_max_c", "metar_max_c",
    "ecmwf_c", "gfs_c", "icon_c", "meteofrance_c",
    "local_hour_utc", "polymarket_buckets",
]

# ── Retry decorator ───────────────────────────────────────────────────────────
def retry(max_attempts: int = 3, backoff_base: float = 2.0):
    """
    Retry on any Exception with exponential backoff.
    Returns None after max_attempts failures.
    Does NOT retry on HTTP 4xx (client errors — not worth retrying).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except requests.HTTPError as exc:
                    last_exc = exc
                    code = exc.response.status_code if exc.response is not None else 0
                    if 400 <= code < 500:
                        log.warning(
                            "%s HTTP %d (client error) — not retrying",
                            func.__name__, code,
                        )
                        return None
                    wait = backoff_base ** attempt
                    log.warning(
                        "%s attempt %d/%d: HTTP %d — retry in %.0fs",
                        func.__name__, attempt + 1, max_attempts, code, wait,
                    )
                    time.sleep(wait)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = backoff_base ** attempt
                    log.warning(
                        "%s attempt %d/%d: %s — retry in %.0fs",
                        func.__name__, attempt + 1, max_attempts, exc, wait,
                    )
                    time.sleep(wait)
            log.error("%s failed after %d attempts: %s", func.__name__, max_attempts, last_exc)
            return None
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════════════
# 1. WU FINAL MAX
# ══════════════════════════════════════════════════════════════════════════════
@retry(max_attempts=3, backoff_base=2.0)
def get_wu_final_max(station: str, country: str, date_str: str) -> Optional[float]:
    """
    Fetch official daily maximum temperature from Weather Underground.

    WARNING: WU history *pages* are JS-rendered HTML — useless with plain requests.
    This function reverse-engineers the hidden JSON API that the WU page calls:
      https://api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json
    All hourly temps for the day are fetched (units=m → °C) and the daily max returned.

    Requires WU_API_KEY in environment (set in .env).
    Returns None if the key is absent — row will have NaN for this column.
    """
    if not WU_API_KEY:
        log.warning("WU_API_KEY not set — wu_final_max_c will be None (set WU_API_KEY in .env)")
        return None

    date_compact = date_str.replace("-", "")   # YYYYMMDD
    url = (
        f"https://api.weather.com/v1/location/{station}:9:{country}"
        f"/observations/historical.json"
    )
    params: dict = {
        "apiKey":    WU_API_KEY,
        "units":     "m",           # metric — temp in °C
        "startDate": date_compact,
        "endDate":   date_compact,
    }
    resp = requests.get(url, params=params, timeout=12)
    resp.raise_for_status()
    data = resp.json()

    observations: list[dict] = data.get("observations", [])
    if not observations:
        log.warning("WU %s %s: empty observations list", station, date_str)
        return None

    temps: list[float] = []
    for obs in observations:
        raw = obs.get("temp")
        if raw is not None:
            try:
                temps.append(float(raw))
            except (ValueError, TypeError):
                pass

    if not temps:
        log.warning("WU %s %s: no valid temp in %d obs", station, date_str, len(observations))
        return None

    daily_max = max(temps)
    log.info("WU %s %s: max=%.1f°C from %d obs", station, date_str, daily_max, len(temps))
    return round(daily_max, 1)


# ══════════════════════════════════════════════════════════════════════════════
# 2. METAR INTRADAY MAX
# ══════════════════════════════════════════════════════════════════════════════
@retry(max_attempts=3, backoff_base=2.0)
def _metar_iem(station: str, date_str: str) -> Optional[list[float]]:
    """
    Iowa State Mesonet ASOS archive — unlimited historical depth, no auth.
    Returns list of Celsius temps for the UTC day, or None on error.
    Source: https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
    """
    dt      = datetime.strptime(date_str, "%Y-%m-%d")
    dt_next = dt + timedelta(days=1)
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
    params: dict = {
        "station":    station,
        "data":       "tmpf",
        "year1":      dt.year,      "month1": dt.month,      "day1": dt.day,
        "year2":      dt_next.year, "month2": dt_next.month, "day2": dt_next.day,
        "tz":         "UTC",
        "format":     "onlycomma",
        "latlon":     "no",
        "elev":       "no",
        "missing":    "M",
        "trace":      "T",
        "direct":     "no",
        "report_type": "3,4",      # 3 = METAR, 4 = SPECI
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()

    temps: list[float] = []
    for line in resp.text.strip().splitlines():
        if not line or line.startswith("#") or line.startswith("station"):
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        valid_ts = parts[1].strip() if len(parts) > 1 else ""
        tmpf_str = parts[2].strip()
        if tmpf_str in ("M", "T", "", "tmpf") or not valid_ts.startswith(date_str):
            continue
        try:
            tempc = (float(tmpf_str) - 32.0) * 5.0 / 9.0
            temps.append(tempc)
        except (ValueError, TypeError):
            pass

    return temps if temps else None


@retry(max_attempts=2, backoff_base=3.0)
def _metar_avwx(station: str, date_str: str) -> Optional[list[float]]:
    """
    Fallback: aviationweather.gov JSON API (bot's primary source).
    Replicates bot's fetch_metar_max() without importing tempbot.py.
    Reliable for ~30 days back; use IEM for older dates.
    Source: https://aviationweather.gov/api/data/metar
    """
    date_compact = date_str.replace("-", "")
    url = "https://aviationweather.gov/api/data/metar"
    params: dict = {
        "ids":    station,
        "format": "json",
        "hours":  "24",
        # Request 24h ending at 23:59 UTC on the target date
        "date":   f"{date_compact}T2359Z",
    }
    resp = requests.get(url, params=params, timeout=12)
    resp.raise_for_status()
    obs_list: list[dict] = resp.json()
    if not obs_list:
        return None

    temps: list[float] = []
    for obs in obs_list:
        raw = obs.get("temp")
        if raw is not None:
            try:
                temps.append(float(raw))
            except (ValueError, TypeError):
                pass

    return temps if temps else None


def get_metar_max_intraday(station: str, date_str: str) -> Optional[float]:
    """
    Return intraday max METAR temperature (°C) for station on date_str (UTC).

    Strategy:
      1. Iowa State Mesonet (IEM) — unlimited historical depth, no auth
      2. aviationweather.gov — bot's live source, ~30 days back (fallback)

    Does NOT import tempbot.py. No bot side-effects.
    """
    # Primary: IEM archive
    temps = _metar_iem(station, date_str)
    source = "IEM"

    # Fallback: aviationweather.gov
    if not temps:
        log.info("METAR %s %s: IEM empty, falling back to aviationweather.gov", station, date_str)
        temps = _metar_avwx(station, date_str)
        source = "avwx"

    if not temps:
        log.warning("METAR %s %s: no data from any source", station, date_str)
        return None

    daily_max = max(temps)
    log.info("METAR[%s] %s %s: max=%.1f°C from %d obs", source, station, date_str, daily_max, len(temps))
    return round(daily_max, 1)


# ══════════════════════════════════════════════════════════════════════════════
# 3. FORECAST SNAPSHOT
# ══════════════════════════════════════════════════════════════════════════════
@retry(max_attempts=3, backoff_base=2.0)
def _fetch_model_forecast(
    lat: float, lon: float, tz: str,
    model: str, date_str: str,
) -> Optional[float]:
    """
    Fetch single-model daily max temperature for one date.

    WARNING: For retroactive dates use historical-forecast-api.open-meteo.com.
    The live api.open-meteo.com endpoint returns ERA5 reanalysis (actual observed
    values) for past dates — NOT the forecast that existed at the time. These are
    fundamentally different and must NOT be mixed. The historical forecast archive
    preserves what each model predicted, enabling apples-to-apples edge analysis.

    Routing:
      past  → historical-forecast-api.open-meteo.com (model archive)
      today → api.open-meteo.com                     (live forecast, same models)
    """
    today_str = date.today().isoformat()
    if date_str < today_str:
        base_url = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    else:
        base_url = "https://api.open-meteo.com/v1/forecast"

    params: dict = {
        "latitude":   lat,
        "longitude":  lon,
        "daily":      "temperature_2m_max",
        "timezone":   tz,
        "models":     model,
        "start_date": date_str,
        "end_date":   date_str,
    }
    resp = requests.get(base_url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    temps: list = data.get("daily", {}).get("temperature_2m_max", [])
    val = temps[0] if temps else None
    return round(float(val), 2) if val is not None else None


def get_forecast_snapshot(city: str, date_str: str) -> dict[str, Optional[float]]:
    """
    Fetch all 4 model forecasts (ecmwf, gfs, icon, meteofrance) for a city/date.
    Each model queried independently (mirrors bot's parallel fetch pattern).
    Returns dict with None for any model that fails.
    """
    cfg = STATIONS[city]
    result: dict[str, Optional[float]] = {}

    for col_key, model_name in FORECAST_MODELS.items():
        val = _fetch_model_forecast(
            cfg["lat"], cfg["lon"], cfg["tz"],
            model_name, date_str,
        )
        result[col_key] = val
        # Small inter-model delay to avoid rate-limiting
        time.sleep(0.25)

    log.info(
        "FORECAST %s %s: ecmwf=%.1f gfs=%.1f icon=%.1f mf=%.1f",
        city, date_str,
        result.get("ecmwf_c") or float("nan"),
        result.get("gfs_c")   or float("nan"),
        result.get("icon_c")  or float("nan"),
        result.get("meteofrance_c") or float("nan"),
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. POLYMARKET BUCKETS
# ══════════════════════════════════════════════════════════════════════════════
def _extract_threshold_c(text: str) -> Optional[float]:
    """
    Extract numeric temperature threshold from a market question string.
    Handles: "14°C or higher", "be 14 degrees", "≥14°C", "above 14C".
    """
    patterns = [
        r"(\d+(?:\.\d+)?)\s*°\s*[Cc]",
        r"(\d+(?:\.\d+)?)\s*degrees?",
        r"(?:above|exceed|over|higher than|≥|>=)\s*(\d+(?:\.\d+)?)",
        r"\btemperature.*?(\d+(?:\.\d+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


@retry(max_attempts=3, backoff_base=2.0)
def get_polymarket_buckets(city: str, date_str: str) -> list[dict]:
    """
    Fetch Polymarket temperature buckets (thresholds) for a city/date.
    Uses Gamma API — works for both active AND resolved markets (retroactive).
    Slug pattern: highest-temperature-in-{city}-on-{YYYY-MM-DD}

    Returns list of dicts:
      {"threshold_c": float, "question": str, "resolved": bool, "outcome": str|None}
    """
    slug = f"highest-temperature-in-{city}-on-{date_str}"
    url  = "https://gamma-api.polymarket.com/events"
    params: dict = {"slug": slug}

    resp = requests.get(url, params=params, timeout=12)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        log.info("PM %s %s: no event for slug=%s", city, date_str, slug)
        return []

    event   = data[0] if isinstance(data, list) else data
    markets = event.get("markets") or []
    buckets: list[dict] = []

    for m in markets:
        if not isinstance(m, dict):
            continue
        question  = (m.get("question") or event.get("title") or "").strip()
        threshold = _extract_threshold_c(question)
        resolved  = not m.get("acceptingOrders", True)
        outcome   = m.get("outcome") if resolved else None

        buckets.append({
            "threshold_c": threshold,
            "question":    question,
            "resolved":    resolved,
            "outcome":     outcome,
        })

    # Sort by threshold ascending for readability
    buckets.sort(key=lambda b: (b["threshold_c"] is None, b["threshold_c"] or 0))
    log.info("PM %s %s: %d buckets (%d resolved)", city, date_str, len(buckets),
             sum(1 for b in buckets if b["resolved"]))
    return buckets


# ══════════════════════════════════════════════════════════════════════════════
# 5. COLLECT DAILY ROW
# ══════════════════════════════════════════════════════════════════════════════
def collect_daily(city: str, date_str: str) -> Optional[dict]:
    """
    Assemble one CSV row for (city, date_str).
    Returns None only if city is unknown; partial rows (some None fields) are valid.
    """
    cfg = STATIONS.get(city.lower())
    if not cfg:
        log.error("Unknown city '%s'. Valid: %s", city, list(STATIONS))
        return None

    station = cfg["station"]
    log.info("=== Collecting %s (%s) for %s ===", city.upper(), station, date_str)

    # 1. WU final official daily max
    wu_max = get_wu_final_max(station, cfg["country"], date_str)

    # 2. METAR intraday max (replicates bot's live fetch, no bot import)
    metar_max = get_metar_max_intraday(station, date_str)

    # 3. Per-model forecast snapshot (retroactive archive, NOT live reanalysis)
    forecasts = get_forecast_snapshot(city, date_str)

    # 4. Polymarket bucket thresholds (active + resolved)
    pm_buckets = get_polymarket_buckets(city, date_str)

    # 5. UTC hour at collection time (23 for retro = end-of-day final)
    local_hour_utc = datetime.now(timezone.utc).hour

    row = {
        "date":               date_str,
        "city":               city.lower(),
        "station":            station,
        "wu_final_max_c":     wu_max,
        "metar_max_c":        metar_max,
        "ecmwf_c":            forecasts.get("ecmwf_c"),
        "gfs_c":              forecasts.get("gfs_c"),
        "icon_c":             forecasts.get("icon_c"),
        "meteofrance_c":      forecasts.get("meteofrance_c"),
        "local_hour_utc":     local_hour_utc,
        "polymarket_buckets": json.dumps(pm_buckets, ensure_ascii=False),
    }

    log.info(
        "DONE %s %s | wu=%-5s metar=%-5s ecmwf=%-5s gfs=%-5s icon=%-5s mf=%-5s | pm_buckets=%d",
        city, date_str,
        f"{wu_max:.1f}"               if wu_max      is not None else "None",
        f"{metar_max:.1f}"            if metar_max   is not None else "None",
        f"{forecasts.get('ecmwf_c'):.1f}"       if forecasts.get("ecmwf_c")      is not None else "None",
        f"{forecasts.get('gfs_c'):.1f}"          if forecasts.get("gfs_c")        is not None else "None",
        f"{forecasts.get('icon_c'):.1f}"         if forecasts.get("icon_c")       is not None else "None",
        f"{forecasts.get('meteofrance_c'):.1f}"  if forecasts.get("meteofrance_c") is not None else "None",
        len(pm_buckets),
    )
    return row


# ══════════════════════════════════════════════════════════════════════════════
# 6. CSV APPEND (dedup on date+city)
# ══════════════════════════════════════════════════════════════════════════════
def append_to_csv(row: dict, output_path: Path) -> None:
    """
    Append one row to CSV. Deduplicates on (date, city):
    if a row for that date+city already exists it is replaced.
    Columns are fixed to CSV_COLUMNS; extra keys are silently dropped.
    """
    df_new = pd.DataFrame([{col: row.get(col) for col in CSV_COLUMNS}])

    if output_path.exists() and output_path.stat().st_size > 0:
        df_existing = pd.read_csv(output_path, dtype=str)
        # Remove stale row for same (date, city)
        keep = ~(
            (df_existing["date"] == str(row["date"])) &
            (df_existing["city"] == str(row["city"]))
        )
        df_existing = df_existing[keep]
        df_out = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_out = df_new

    df_out.to_csv(output_path, index=False)
    log.info("CSV %s: %d rows total", output_path.name, len(df_out))


# ══════════════════════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════════════════════
def build_date_list(args) -> list[str]:
    today = date.today()
    if args.today:
        return [today.isoformat()]
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            log.error("Invalid --date format '%s'. Use YYYY-MM-DD.", args.date)
            sys.exit(1)
        return [args.date]
    if args.retro:
        n = max(1, args.days)
        return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TempBot Ground Truth Dataset Collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--today",  action="store_true", help="Collect today (both cities)")
    mode.add_argument("--retro",  action="store_true", help="Collect last N days (default 60)")
    mode.add_argument("--date",   type=str, metavar="YYYY-MM-DD", help="Collect one specific date")
    parser.add_argument("--days", type=int, default=60, help="Days for --retro (default: 60)")
    parser.add_argument(
        "--city", type=str, default=None,
        choices=list(STATIONS.keys()),
        help="Limit to one city (default: both london + seoul)",
    )
    args = parser.parse_args()

    dates = build_date_list(args)
    if not dates:
        parser.print_help()
        sys.exit(0)

    cities = [args.city] if args.city else list(STATIONS.keys())
    mode_str = (
        "today" if args.today else
        f"retro {args.days}d" if args.retro else
        f"date={args.date}"
    )

    log.info("━━━ data_collector START | mode=%s | dates=%d | cities=%s ━━━",
             mode_str, len(dates), cities)
    if not WU_API_KEY:
        log.warning("WU_API_KEY not set — wu_final_max_c will be None for all rows")

    total_rows = 0
    errors     = 0

    for date_str in dates:
        for city in cities:
            try:
                row = collect_daily(city, date_str)
                if row is None:
                    errors += 1
                    continue
                out_path = OUTPUT_DIR / STATIONS[city]["csv"]
                append_to_csv(row, out_path)
                total_rows += 1
            except Exception as exc:  # noqa: BLE001
                log.exception("FATAL collect_daily(%s, %s): %s", city, date_str, exc)
                errors += 1

            # Polite inter-request pause
            time.sleep(0.5)

    log.info(
        "━━━ data_collector DONE | rows_written=%d errors=%d output=%s ━━━",
        total_rows, errors, OUTPUT_DIR,
    )


if __name__ == "__main__":
    main()
