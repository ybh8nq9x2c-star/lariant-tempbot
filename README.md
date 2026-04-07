# TempBot — Polymarket Weather Market Maker

> Quantitative market-making bot on Polymarket temperature markets, powered by ECMWF weather forecasts and LightGBM ML edge weighting.

**Status:** 🟢 Live | **Version:** v4.32 + CB-v1 | **Capital:** ~$34 USDC | **Markets:** London (EGLC) + Seoul (RKSI)

---

## System Goal

TempBot identifies mispriced binary temperature outcomes on Polymarket (e.g. "Will London's highest temperature exceed 18°C on April 9?") by comparing ECMWF ensemble weather forecasts against market-implied probabilities. When the edge exceeds a threshold, it places limit orders via the Polymarket CLOB API using Kelly-sized positions. A LightGBM classifier weights the entry edge by estimated P(win).

---

## Current State

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 — Infrastructure | ✅ Complete | Bot live, fill ledger, S3 sync, Telegram alerts |
| Phase 2 — Bias/Collection | ✅ Complete | 744+ fills tagged `v2-bias-patch-stable` |
| Phase 3 — ML Integration | ✅ Live | LightGBM v4.32 edge weighting active on EC2 |
| Phase 4 — V5 Dataset | 🔄 In progress | Pre-order book snapshots accumulating in `v5_book_data.jsonl` |
| Phase 5 — ML V5 Retrain | ⏳ Pending | Retrain on V5 dataset when 500+ rows collected |

**Circuit Breakers:** CB-v1 active (LatencyGuard + EdgeDecayGuard + ConsecutiveLossGuard)
**V5 Data:** ~90 rows collected, synced to S3 every 60s

---

## Directory Structure

```
botbackup/                              ← Mac local repo root (git)
├── tempbot_v427_stable_20260313.py     ← Production bot (mirror of EC2)
├── trades.jsonl                        ← Fill ledger — ML training data (read-only)
├── CLAUDE.md                           ← AI agent working rules (authoritative)
├── PHASE_STATE.md                      ← Project phase tracker
├── ARCHITECTURE.md                     ← Full system architecture (this ecosystem)
├── README.md                           ← This file
├── ml/
│   └── download_market_context.py      ← Telonex/Gamma market data pipeline
├── polymarket-temp-bot/                ← EC2 snapshot (Mar 18, slightly stale)
│   ├── config.json                     ← Live parameters (hot-reload 60s)
│   ├── config_override.json            ← Runtime overrides (gitignored)
│   ├── trades.jsonl                    ← EC2 fill ledger copy
│   ├── forecast_weights.json           ← ECMWF ensemble EMA weights
│   └── edge_cache.json                 ← Per-token edge cache
├── venv/                               ← Python venv (gitignored)
└── .gitignore

EC2: ~/polymarket-temp-bot/             ← Production directory
├── tempbot_v427_stable_20260313.py     ← LIVE BOT
├── modello_final_lightgbm.joblib       ← LightGBM model (8 features, AUC=0.574)
├── sync_to_s3.sh                       ← S3 upload script (cron: every 60s)
├── config.json                         ← Live config
├── config_override.json                ← Hot-reload overrides
├── trades.jsonl                        ← Authoritative fill ledger
├── v5_book_data.jsonl                  ← Pre-order book snapshots (V5)
├── monitor_state.json                  ← Bot state snapshot
├── positions.json                      ← Dashboard feed (extracted from monitor_state)
├── edge_cache.json                     ← Per-token edge cache
├── tempbot.log                         ← Main bot log
├── sync_s3.log                         ← S3 upload heartbeat
├── tempbot_telegram.py                 ← Telegram alert bot
├── s3_watcher.py                       ← S3 event watcher
├── data_collector.py                   ← Daily ground-truth weather collector
├── backup_ledgers.sh                   ← Nightly ledger backup (cron: 23:59 UTC)
├── family_risk.py                      ← Family-level risk gate module
└── archive_ledgers/                    ← Daily backup archives
```

---

## Data Flow

```
EC2 Bot → trades.jsonl + v5_book_data.jsonl + monitor_state.json
                    │
              sync_to_s3.sh (every 60s)
                    │
             Amazon S3 bucket:
        tempbot-dashboard-sync-d625153f
                    │
           Railway Dashboard (Next.js)
           reads: positions.json, trades.jsonl,
                  monitor_state.json, v5_book_data.jsonl
```

---

## Key Commands

```bash
# SSH to EC2
ssh -i tempbot-ireland.pem ubuntu@3.250.216.248

# Check bot running
ps aux | grep tempbot_v427_stable | grep -v grep

# Live log stream
ssh ... 'tail -f ~/polymarket-temp-bot/tempbot.log'

# Circuit breaker activity
ssh ... 'grep "\[CB-" ~/polymarket-temp-bot/tempbot.log | tail -20'

# V5 dataset size
ssh ... 'wc -l ~/polymarket-temp-bot/v5_book_data.jsonl'

# S3 sync health
ssh ... 'tail -5 ~/polymarket-temp-bot/sync_s3.log'
```

---

## ML Model

| Property | Value |
|----------|-------|
| Algorithm | LightGBM (LGBMClassifier) |
| Features | 8 (edge, abs_edge, mid, city_enc, T_days, edge/mid, city×edge, 1/mid) |
| Training samples | 158 |
| AUC (stratified CV) | 0.574 |
| Leakage audit | PASSED |
| Path on EC2 | `~/polymarket-temp-bot/modello_final_lightgbm.joblib` |
| Edge formula | `edge_weighted = edge × (0.5 + 0.5 × ml_proba)` |
| Fallback | Returns raw edge if model unavailable or crashes |

---

## 🤖 AI Agent Guidelines

> **These rules are absolute. Read before making any change.**

### Scope of operations

| Environment | Agent scope | Notes |
|-------------|-------------|-------|
| EC2 (bot) | `claude code` | Patch surgical only, confirm before restart |
| Railway (dashboard) | `claude cowork` | Frontend only, no bot code |
| Mac local | `claude code` | Research, ML, git sync |

### Hard rules

1. **Never modify `tempbot_v427_stable_20260313.py` without explicit user confirmation.** This is the live production bot.

2. **`trades.jsonl` is read-only everywhere.** No writes, no truncation, no overwrite.

3. **Do not modify log file paths or S3 key names without updating `sync_to_s3.sh`.**
   The dashboard depends on exact S3 keys: `positions.json`, `trades.jsonl`, `monitor_state.json`, `v5_book_data.jsonl`.

4. **Do not remove or bypass the Circuit Breaker guards (CB-v1).**
   The three guards (`_latency_blocked`, `_edge_decay_guard()`, `_consecutive_loss_guard()`) protect capital. Any modification requires explicit approval.

5. **Do not move executable files (`.py`, `.sh`) on EC2.** Absolute paths are hardcoded in crontab:
   - `* * * * * /home/ubuntu/polymarket-temp-bot/sync_to_s3.sh`
   - `0 23 * * * python3 /home/ubuntu/polymarket-temp-bot/data_collector.py`
   - `59 23 * * * /home/ubuntu/polymarket-temp-bot/backup_ledgers.sh`

6. **Follow the patch workflow:** AUDIT → PLAN → CONFIRM → PATCH → VERIFY.
   Never apply patches without showing the diff first.

7. **The `.pem` key must never be read, logged, or exposed in chat context.**

8. **The ML layer must remain optional with graceful fallback.**
   If `modello_final_lightgbm.joblib` is unavailable, the bot must continue with raw edge.

9. **Before any EC2 restart, run:** `python3 -m py_compile tempbot_v427_stable_20260313.py`

10. **Git branch is `master`** (not `main`). Always push to `origin master`.

### Gitignore policy

The following are intentionally **not tracked in git**:
- `*.jsonl` — raw trading data (too large, sensitive)
- `*.log` — runtime logs
- `config_override.json` — runtime hot-reload file
- `edge_cache.json` — runtime state
- `*.pem` — SSH key
- `*.bak*` — backup snapshots
- `venv/` — Python environment

---

## Architecture Reference

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for full system topology, node descriptions, and data pipeline diagram.

## Working Rules Reference

See [`CLAUDE.md`](./CLAUDE.md) for AI-specific working rules, security constraints, and patch workflow.

## Phase Tracker

See [`PHASE_STATE.md`](./PHASE_STATE.md) for current phase status and exit conditions.
