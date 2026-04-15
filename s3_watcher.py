#!/usr/bin/env python3
"""
s3_watcher.py — Non-invasive S3 sidecar for TempBot dashboard sync.
=====================================================================
Polls monitor_state.json and trades.jsonl every 15 seconds.
When either file's mtime changes, extracts a clean positions.json
snapshot and pushes all three objects to S3.

Zero imports from tempbot.  Zero writes to any bot file.
Only READS monitor_state.json + trades.jsonl,
     WRITES positions.json (derived snapshot) + watcher_s3.log.

Usage:
    nohup python3 -u s3_watcher.py >> watcher_s3.log 2>&1 &
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BUCKET        = "tempbot-dashboard-sync-d625153f"
REGION        = "eu-west-1"
POLL_INTERVAL = 15          # seconds
BOT_DIR       = Path(__file__).parent.resolve()

WATCHED_FILES = {
    "monitor_state": BOT_DIR / "monitor_state.json",
    "trades":        BOT_DIR / "trades.jsonl",
}
POSITIONS_OUT = BOT_DIR / "positions.json"
LOG_FILE      = BOT_DIR / "watcher_s3.log"

# ── Logging (to separate file — never touches bot logs) ──────────────────────
log = logging.getLogger("s3_watcher")
log.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s",
                                    datefmt="%Y-%m-%dT%H:%M:%SZ"))
_fh.formatter.converter = time.gmtime
log.addHandler(_fh)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(logging.Formatter("%(asctime)s | %(message)s",
                                    datefmt="%H:%M:%S"))
_sh.formatter.converter = time.gmtime
log.addHandler(_sh)

# ── Lazy boto3 import (crash-safe) ───────────────────────────────────────────
_s3 = None

def get_s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", region_name=REGION)
        log.info("boto3 S3 client initialised  (region=%s)", REGION)
    return _s3

# ── Positions extractor (read-only from monitor_state.json) ──────────────────

def extract_positions(src: Path, dst: Path) -> bool:
    """Read monitor_state.json → write a clean positions.json snapshot.
    Returns True on success, False on error."""
    try:
        with open(src, "r", encoding="utf-8") as f:
            state = json.load(f)

        snapshot = {
            "ts":               state.get("ts"),
            "capital":          state.get("capital"),
            "total_exposure":   state.get("total_exposure"),
            "daily_pnl":        state.get("daily_pnl"),
            "drawdown_pct":     state.get("drawdown_pct"),
            "peak_capital":     state.get("peak_capital"),
            "open_positions":   state.get("open_positions_count"),
            "exposure_by_city": state.get("exposure_by_city", {}),
            "positions":        state.get("positions", {}),
            "trades_summary":   state.get("trades_summary", {}),
            "params":           state.get("params", {}),
            "_source":          "s3_watcher.py",
            "_extracted_utc":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        tmp = str(dst) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, dst)       # atomic rename
        return True
    except Exception as exc:
        log.error("extract_positions failed: %s", exc)
        return False

# ── S3 upload (single file) ──────────────────────────────────────────────────

def upload(local_path: Path, s3_key: str) -> bool:
    """Upload a single file to S3.  Returns True on success."""
    try:
        s3 = get_s3()
        s3.upload_file(
            str(local_path),
            BUCKET,
            s3_key,
            ExtraArgs={"ServerSideEncryption": "AES256"},
        )
        return True
    except Exception as exc:
        log.error("S3 upload FAILED  %s -> s3://%s/%s : %s",
                  local_path.name, BUCKET, s3_key, exc)
        return False

# ── Mtime tracker ────────────────────────────────────────────────────────────

def get_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0

# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("s3_watcher started  |  bucket=%s  poll=%ds", BUCKET, POLL_INTERVAL)
    log.info("watched: %s", {k: str(v) for k, v in WATCHED_FILES.items()})
    log.info("=" * 60)

    # Seed mtimes so the first cycle always uploads (cold start)
    last_mtimes = {name: 0.0 for name in WATCHED_FILES}
    uploads_ok   = 0
    uploads_fail = 0
    cycles       = 0

    while True:
        try:
            cycles += 1
            changed = False

            # Check mtimes
            for name, path in WATCHED_FILES.items():
                mt = get_mtime(path)
                if mt != last_mtimes[name]:
                    last_mtimes[name] = mt
                    changed = True

            if not changed:
                time.sleep(POLL_INTERVAL)
                continue

            ts_tag = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 1. Extract positions.json from monitor_state.json
            mon_path = WATCHED_FILES["monitor_state"]
            if mon_path.exists():
                extract_positions(mon_path, POSITIONS_OUT)

            # 2. Upload all three files
            results = []
            for s3_key, local in [
                ("positions.json",      POSITIONS_OUT),
                ("trades.jsonl",        WATCHED_FILES["trades"]),
                ("monitor_state.json",  WATCHED_FILES["monitor_state"]),
            ]:
                if local.exists():
                    ok = upload(local, s3_key)
                    results.append((s3_key, ok))
                else:
                    log.warning("SKIP %s — file missing", local.name)

            ok_count   = sum(1 for _, ok in results if ok)
            fail_count = sum(1 for _, ok in results if not ok)
            uploads_ok   += ok_count
            uploads_fail += fail_count

            if fail_count == 0:
                log.info("SYNC %s  |  %d files pushed  "
                         "|  cycle=%d  total_ok=%d",
                         ts_tag, ok_count, cycles, uploads_ok)
            else:
                log.warning("SYNC %s  |  ok=%d fail=%d  "
                            "|  cycle=%d  total_fail=%d",
                            ts_tag, ok_count, fail_count,
                            cycles, uploads_fail)

        except KeyboardInterrupt:
            log.info("Shutting down (KeyboardInterrupt).")
            break
        except Exception as exc:
            log.error("LOOP ERROR (non-fatal): %s", exc, exc_info=True)

        time.sleep(POLL_INTERVAL)

    log.info("s3_watcher stopped  |  cycles=%d  ok=%d  fail=%d",
             cycles, uploads_ok, uploads_fail)


if __name__ == "__main__":
    main()
