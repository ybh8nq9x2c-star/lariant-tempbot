#!/usr/bin/env python3
"""
ml/download_market_context.py
Scarica i file quotes da Telonex per ogni (slug, outcome, date) trovato in trades.jsonl.
Output: ml/data/context/<date>__<slug>__<outcome>.parquet
"""

import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
TRADES_FILE  = Path(__file__).parent.parent / "trades.jsonl"
OUTPUT_DIR   = Path(__file__).parent / "data" / "context"
TELONEX_KEY  = "tlx_6f33194cdd80ee1f94202b536b02c173"
TELONEX_BASE = "https://api.telonex.io/v1/downloads/polymarket/quotes"
GAMMA_BASE   = "https://gamma-api.polymarket.com/markets"
GAMMA_SLEEP  = 0.3   # secondi tra chiamate Gamma API
TELONEX_SLEEP = 0.5  # secondi tra download Telonex

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Step 1: estrai token_id unici + date da ENTRY events ────────────────────
print("=== Step 1: lettura trades.jsonl ===")
token_date: dict[str, set[str]] = {}  # token_id → set of dates

with open(TRADES_FILE) as f:
    for line in f:
        rec = json.loads(line)
        if rec.get("event") != "ENTRY":
            continue
        token_id = rec.get("token_id")
        if not token_id:
            continue
        ts = rec.get("ts", 0)
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        token_date.setdefault(token_id, set()).add(date_str)

print(f"Token unici trovati: {len(token_date)}")

# ── Step 2: risolvi slug + outcome via Gamma API ─────────────────────────────
print("\n=== Step 2: risoluzione slug/outcome da Gamma API ===")
# (slug, outcome, date) → da scaricare
to_download: set[tuple[str, str, str]] = set()
failed_tokens: list[str] = []

for i, (token_id, dates) in enumerate(token_date.items()):
    url = f"{GAMMA_BASE}?clob_token_ids={token_id}&nested=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TempBot-ML/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        if not data:
            print(f"  [SKIP] token {token_id[:16]}... — nessun risultato Gamma")
            failed_tokens.append(token_id)
            continue

        market = data[0]
        slug = market.get("slug", "")
        # Gamma API returns these as JSON strings, not Python lists — must parse
        raw_clob = market.get("clobTokenIds", "[]")
        raw_out  = market.get("outcomes", '["Yes", "No"]')
        clob_ids = json.loads(raw_clob) if isinstance(raw_clob, str) else raw_clob
        outcomes = json.loads(raw_out)  if isinstance(raw_out,  str) else raw_out

        if not slug or not clob_ids:
            print(f"  [SKIP] token {token_id[:16]}... — slug o clobTokenIds mancanti")
            failed_tokens.append(token_id)
            continue

        # outcome: index nel clob_ids array
        try:
            idx = clob_ids.index(token_id)
            outcome = outcomes[idx] if idx < len(outcomes) else "Yes"
        except ValueError:
            outcome = "Yes"  # fallback

        for date_str in dates:
            to_download.add((slug, outcome, date_str))

        print(f"  [{i+1}/{len(token_date)}] {slug} | {outcome} | dates={sorted(dates)}")

    except Exception as e:
        print(f"  [ERR] token {token_id[:16]}...: {e}")
        failed_tokens.append(token_id)

    time.sleep(GAMMA_SLEEP)

print(f"\nCombinazioni (slug, outcome, date) da scaricare: {len(to_download)}")
if failed_tokens:
    print(f"Token non risolti: {len(failed_tokens)}")

# ── Step 3: download da Telonex ──────────────────────────────────────────────
print("\n=== Step 3: download Telonex ===")
downloaded = 0
skipped    = 0
errors     = 0

for slug, outcome, date_str in sorted(to_download):
    safe_slug    = slug.replace("/", "_")
    safe_outcome = outcome.replace(" ", "_")
    out_file = OUTPUT_DIR / f"{date_str}__{safe_slug}__{safe_outcome}.parquet"

    if out_file.exists():
        print(f"  [SKIP] già presente: {out_file.name}")
        skipped += 1
        continue

    params = urllib.parse.urlencode({"slug": slug, "outcome": outcome})
    url    = f"{TELONEX_BASE}/{date_str}?{params}"
    headers = {
        "Authorization": f"Bearer {TELONEX_KEY}",
        "User-Agent": "TempBot-ML/1.0",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read()

        out_file.write_bytes(content)
        size_kb = len(content) / 1024
        print(f"  [OK] {out_file.name} ({size_kb:.1f} KB)")
        downloaded += 1

    except urllib.error.HTTPError as e:
        print(f"  [HTTP {e.code}] {date_str} | {slug} | {outcome}")
        errors += 1
    except Exception as e:
        print(f"  [ERR] {date_str} | {slug}: {e}")
        errors += 1

    time.sleep(TELONEX_SLEEP)

# ── Riepilogo ────────────────────────────────────────────────────────────────
print(f"""
=== RIEPILOGO ===
Scaricati:  {downloaded}
Già presenti (skip): {skipped}
Errori:     {errors}
Output dir: {OUTPUT_DIR}
""")
