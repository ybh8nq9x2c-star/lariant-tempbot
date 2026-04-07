# TempBot — System Architecture

> **Last updated:** 2026-04-07
> **Version:** v4.32 + CB-v1 + V5 Data Collector

---

## Overview

TempBot is a quantitative market-maker / arbitrageur on Polymarket weather temperature markets (London EGLC, Seoul RKSI). It combines an ECMWF ensemble weather forecast model with a LightGBM edge-weighting layer to identify mispriced binary outcomes and place limit orders via the Polymarket CLOB API.

The system runs across **3 nodes** with a unidirectional data pipeline:

```
EC2 (Execution) ──sync_to_s3.sh──► Amazon S3 ──► Railway (Dashboard)
     │
     └──► Mac Local (Research / ML training)
```

---

## Node 1 — EC2 Production (eu-west-1, Ireland)

**Host:** `ubuntu@3.250.216.248`
**Bot directory:** `~/polymarket-temp-bot/`
**Main process:** `python3 -u tempbot_v427_stable_20260313.py`

### What runs here

| Process | How started | Role |
|---------|-------------|------|
| `tempbot_v427_stable_20260313.py` | `nohup ... &` (manual) | Core trading bot |
| `tempbot_telegram.py` | background | Telegram alert bridge |
| `s3_watcher.py` | background | S3 event monitor |
| `sync_to_s3.sh` | crontab `* * * * *` | Push ledgers to S3 every 60s |
| `data_collector.py` | crontab `0 23 * * *` | Daily ground-truth weather fetch |
| `backup_ledgers.sh` | crontab `59 23 * * *` | Nightly ledger archive |

### Bot architecture (inside `tempbot_v427_stable_20260313.py`)

```
main_async()
├── fetch_portfolio_capital()          # capital sync every 60s
├── update_pnl_from_fills()            # fill confirmation + ledger
├── check_position_stoploss()          # TIER0/TIER1/TIER2 exits + SL
├── run_cycle()
│   ├── Phase ARB                      # cross-market arbitrage
│   └── Phase MM (_mm_task per market) # market making
│       ├── get_book()                 # WS cache → REST fallback
│       ├── ecmwf_true_prob()          # weather fair value
│       ├── ml_weight_edge()           # [v4.32-ML] LightGBM weighting
│       ├── avellaneda_stoikov_quote() # spread quoting
│       ├── kelly_and_mc()             # position sizing
│       ├── [CB-v1] Circuit Breakers   # latency / decay / loss guards
│       ├── [V5-DATA] book snapshot    # pre-order microstructure log
│       └── place_order()              # CLOB submit
└── _latency_guard_loop()              # [CB-v1] background ping task
```

### Circuit Breakers (CB-v1)

| Guard | Trigger | Effect | Reset |
|-------|---------|--------|-------|
| **LatencyGuard** | 3× ping >500ms or timeout | Blocks MM BUY | Auto on next successful ping |
| **EdgeDecayGuard** | 5 consecutive BUY fills with edge<0 | Blocks MM BUY | Auto when window improves |
| **ConsecutiveLossGuard** | 4 stoploss triggers (not during active block) | 1h MM BUY cooldown | Auto-reset when block expires |

### Key files on EC2

| File | Role | Writable? |
|------|------|-----------|
| `tempbot_v427_stable_20260313.py` | Production bot | Patch only (confirmed) |
| `config.json` | Live parameters (hot-reload 60s) | Yes |
| `config_override.json` | Runtime overrides (hot-reload) | Yes |
| `trades.jsonl` | Fill ledger — ML training source | **Read-only** |
| `v5_book_data.jsonl` | Pre-order book snapshots (V5 dataset) | Append-only |
| `monitor_state.json` | Bot state snapshot | Bot writes |
| `positions.json` | Extracted from monitor_state for dashboard | sync script writes |
| `edge_cache.json` | Per-token edge estimates | Bot writes |
| `modello_final_lightgbm.joblib` | LightGBM model (8 features, AUC=0.574) | Read-only |
| `sync_to_s3.sh` | S3 upload script | Edit with care |
| `sync_s3.log` | Upload heartbeat log | Append-only |
| `tempbot.log` | Main bot log | Append-only |

### Risk parameters (config.json)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `MAX_TOTAL_EXPOSURE_FRAC` | 0.75 | Max 75% capital deployed |
| `DAILY_STOPLOSS` | -2% | Kill-switch threshold |
| `BECKER_BUY_ZONE` | 0.06–0.30 | Only quote YES tokens <30¢ |
| `BECKER_SELL_ZONE` | 0.75–0.95 | Only quote NO tokens >75¢ |
| `STOPLOSS_POSITION_PCT` | 0.70 | Exit if position -70% |
| `MIN_FORECAST_EDGE` | 0.015 | Minimum edge to place order |
| `MIN_DEPTH_USD` | 100 | Minimum book depth |

---

## Node 2 — Railway (Dashboard UI)

**Platform:** Railway (Next.js)
**Data source:** Amazon S3 bucket `tempbot-dashboard-sync-d625153f`
**IAM user:** `tempbot-sync-writer` (PutObject only — no ListBucket, no GetObject from EC2)

### What it displays
- Real-time capital, PnL, drawdown
- Open positions with city/bucket breakdown
- V5 microstructure widget (spread, depth, edge per token)
- Market microstructure heatmap

### Data it reads from S3

| S3 key | Source on EC2 | Update freq |
|--------|--------------|-------------|
| `positions.json` | Extracted from `monitor_state.json` | Every 60s |
| `trades.jsonl` | `~/polymarket-temp-bot/trades.jsonl` | Every 60s |
| `monitor_state.json` | Bot state snapshot | Every 60s |
| `v5_book_data.jsonl` | Pre-order book snapshots | Every 60s |

---

## Node 3 — Mac Local (Research / Data Ops)

**Path:** `/Users/matteo/Desktop/botbackup/`
**Git remote:** `https://github.com/ybh8nq9x2c-star/lariant-tempbot.git` (branch: `master`)

### What lives here

| Path | Role |
|------|------|
| `tempbot_v427_stable_20260313.py` | Local mirror of production bot (for git tracking) |
| `trades.jsonl` | Latest local copy (443KB+) — primary ML training data |
| `ml/` | ML development: features, trainer, predictor scripts |
| `CLAUDE.md` | AI agent working rules (authoritative) |
| `PHASE_STATE.md` | Project phase tracker |
| `ARCHITECTURE.md` | This file |
| `polymarket-temp-bot/` | EC2 snapshot (Mar 18 — slightly stale) |

### ML model registry

| File | Status | AUC | Features | Notes |
|------|--------|-----|----------|-------|
| `modello_final_lightgbm.joblib` | **Live on EC2** | 0.574 | 8 | Leakage audit PASSED |
| `lariant_lightgbm_v4.pkl` | Rejected | — | 9 | Contains `size_contracts` (data leakage) |

### ML feature set (8 features, order-sensitive)
```python
[edge, abs(edge), mid, city_enc, T_days,
 edge/max(mid,0.01), city_enc*edge, 1/max(mid,0.01)]
```
Edge weighting: `edge_weighted = edge * (0.5 + 0.5 * ml_proba)`

---

## Data Pipeline

```
┌─────────────────────────────────────────────────────┐
│                    EC2 Production                    │
│                                                      │
│  tempbot_v427_stable_20260313.py                    │
│    │                                                 │
│    ├──► trades.jsonl          (fill ledger)         │
│    ├──► v5_book_data.jsonl    (book snapshots)      │
│    ├──► monitor_state.json    (bot state)           │
│    └──► edge_cache.json       (per-token edges)     │
│                                                      │
│  sync_to_s3.sh (cron: every 60s)                   │
│    ├── positions.json ──────────────────────────┐   │
│    ├── trades.jsonl ────────────────────────────┤   │
│    ├── monitor_state.json ──────────────────────┤   │
│    └── v5_book_data.jsonl ──────────────────────┤   │
└─────────────────────────────────────────────────┼───┘
                                                  │
                                            Amazon S3
                              tempbot-dashboard-sync-d625153f
                                                  │
                         ┌────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Railway Dashboard  │
              │  (Next.js frontend) │
              └─────────────────────┘
```

---

## SSH & Deployment

```bash
# Connect to EC2
ssh -i tempbot-ireland.pem ubuntu@3.250.216.248

# Check bot status
ps aux | grep tempbot_v427_stable | grep -v grep

# Tail live log
tail -f ~/polymarket-temp-bot/tempbot.log

# Manual S3 sync
bash ~/polymarket-temp-bot/sync_to_s3.sh

# Restart bot (after patch)
kill $(ps aux | grep tempbot_v427_stable_20260313 | grep -v grep | awk '{print $2}')
sleep 2
cd ~/polymarket-temp-bot && nohup python3 -u tempbot_v427_stable_20260313.py >> tempbot.log 2>&1 &
```

---

## Security Rules (absolute)

1. **Never modify the live bot without explicit user confirmation**
2. **`trades.jsonl` is read-only** — no writes, no truncation
3. **No structural refactor** without approved plan and test
4. **`phase2_refactor.py` must not be executed** until tested on isolated copy
5. **No deploy/restart/push to EC2** without explicit order
6. **No destructive commands** (`rm`, `git reset --hard`, `truncate`)
7. **The `.pem` key content must never be read, logged, or exposed in chat**
8. **ML layer is optional with graceful fallback** — if model crashes, bot continues with raw edge

---

## Patch Workflow

```
1. AUDIT   → read relevant files, map current state
2. PLAN    → propose approach, show draft
3. CONFIRM → wait for explicit user OK
4. PATCH   → apply surgically (Python script with old/new string replacement)
5. VERIFY  → syntax check, grep markers, check bot running
```
