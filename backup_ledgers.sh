#!/usr/bin/env bash
# TempBot daily ledger backup — runs at 23:59 UTC via cron
# Safe: cp -p only, never deletes, skips missing files
# Backs up: trades.jsonl, monitor_state.json, edge_cache.json,
#           config_override.json, secrets.env.template, and the active bot .py file
# NOTE: secrets.env (real credentials) is intentionally EXCLUDED from backups.

BOT_DIR=/home/ubuntu/polymarket-temp-bot
ARCH_DIR=/home/ubuntu/polymarket-temp-bot/archive_ledgers
TS=$(date +%Y%m%d_%H%M%S)
LOG=$ARCH_DIR/backup.log

mkdir -p "$ARCH_DIR"

# ── Detect active bot file ──────────────────────────────────────────────────
# Priority 1: BOTFILENAME env var on the live python process
BOT_PID=$(pgrep -f 'python3 -u tempbot' 2>/dev/null | head -1)
ACTIVE_BOT=""
if [ -n "$BOT_PID" ]; then
  ACTIVE_BOT=$(cat /proc/$BOT_PID/environ 2>/dev/null | tr '\0' '\n' | grep '^BOTFILENAME=' | cut -d= -f2)
fi
# Priority 2: parse filename from live process cmdline
if [ -z "$ACTIVE_BOT" ] && [ -n "$BOT_PID" ]; then
  ACTIVE_BOT=$(ps -p $BOT_PID -o args= 2>/dev/null | grep -oP 'tempbot\S+\.py' | head -1)
fi
# Priority 3: any python3 -u tempbot*.py in process list
if [ -z "$ACTIVE_BOT" ]; then
  ACTIVE_BOT=$(ps aux 2>/dev/null | grep 'python3 -u' | grep -v grep | grep -oP 'tempbot\S+\.py' | head -1)
fi
# Resolve to full path if relative
if [ -n "$ACTIVE_BOT" ] && [ "${ACTIVE_BOT:0:1}" != "/" ]; then
  ACTIVE_BOT="$BOT_DIR/$ACTIVE_BOT"
fi

# ── Static files ────────────────────────────────────────────────────────────
for ENTRY in "trades.jsonl:jsonl" "monitor_state.json:json" "edge_cache.json:json" "config_override.json:json" "secrets.env.template:template"; do
  SRC_FILE="${ENTRY%%:*}"
  EXT="${ENTRY##*:}"
  BASE="${SRC_FILE%%.*}"
  SRC="$BOT_DIR/$SRC_FILE"
  DEST="$ARCH_DIR/${BASE}_daily_${TS}.${EXT}"
  if [ -f "$SRC" ]; then
    cp -p "$SRC" "$DEST"
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') | OK   | $SRC_FILE -> $(basename $DEST) | $(stat -c%s $DEST) bytes" >> "$LOG"
  else
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') | SKIP | $SRC_FILE (not found)" >> "$LOG"
  fi
done

# ── Active bot .py file ─────────────────────────────────────────────────────
if [ -n "$ACTIVE_BOT" ] && [ -f "$ACTIVE_BOT" ]; then
  BOT_BASENAME=$(basename "$ACTIVE_BOT" .py)
  DEST="$ARCH_DIR/${BOT_BASENAME}_daily_${TS}.py"
  cp -p "$ACTIVE_BOT" "$DEST"
  echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') | OK   | $(basename $ACTIVE_BOT) [ACTIVE BOT] -> $(basename $DEST) | $(stat -c%s $DEST) bytes" >> "$LOG"
else
  echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') | SKIP | active_bot.py (not detected or not found) active_bot=${ACTIVE_BOT:-unset}" >> "$LOG"
fi
