#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# sync_to_s3.sh  —  Push bot ledgers to S3 every minute (1-way, write-only)
# Runs as: ubuntu crontab  |  * * * * *
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BUCKET="tempbot-dashboard-sync-d625153f"
BOT_DIR="/home/ubuntu/polymarket-temp-bot"
LOG="$BOT_DIR/sync_s3.log"
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# ── 1. Extract positions.json from monitor_state.json ─────────────────────────
python3 - << 'PYEOF'
import json, os, sys
src = "/home/ubuntu/polymarket-temp-bot/monitor_state.json"
dst = "/home/ubuntu/polymarket-temp-bot/positions.json"
try:
    with open(src) as f:
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
        "_source":          "monitor_state.json",
        "_extracted_utc":   __import__('datetime').datetime.utcnow().isoformat() + "Z",
    }
    with open(dst, "w") as f:
        json.dump(snapshot, f, indent=2)
except Exception as e:
    print(f"positions.json extraction failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

# ── 2. Push files to S3 ───────────────────────────────────────────────────────
ERRORS=0

aws s3 cp "$BOT_DIR/positions.json" "s3://$BUCKET/positions.json" \
    --only-show-errors \
    --metadata "sync_ts=$TS" 2>> "$LOG" || { echo "$TS  ERROR: positions.json upload failed" >> "$LOG"; ERRORS=$((ERRORS+1)); }

aws s3 cp "$BOT_DIR/trades.jsonl" "s3://$BUCKET/trades.jsonl" \
    --only-show-errors \
    --metadata "sync_ts=$TS" 2>> "$LOG" || { echo "$TS  ERROR: trades.jsonl upload failed" >> "$LOG"; ERRORS=$((ERRORS+1)); }

aws s3 cp "$BOT_DIR/monitor_state.json" "s3://$BUCKET/monitor_state.json" \
    --only-show-errors \
    --metadata "sync_ts=$TS" 2>> "$LOG" || { echo "$TS  ERROR: monitor_state.json upload failed" >> "$LOG"; ERRORS=$((ERRORS+1)); }

aws s3 cp "$BOT_DIR/v5_book_data.jsonl" "s3://$BUCKET/v5_book_data.jsonl" \
    --only-show-errors \
    --metadata "sync_ts=$TS" 2>> "$LOG" || { echo "$TS  ERROR: v5_book_data.jsonl upload failed" >> "$LOG"; ERRORS=$((ERRORS+1)); }

# ── 3. Log heartbeat ──────────────────────────────────────────────────────────
if [ $ERRORS -eq 0 ]; then
    echo "$TS  OK  4 files synced -> s3://$BUCKET/" >> "$LOG"
else
    echo "$TS  WARN  $ERRORS file(s) failed" >> "$LOG"
fi

# Keep log trimmed to last 500 lines
tail -500 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
