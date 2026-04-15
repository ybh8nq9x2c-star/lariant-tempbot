# Phase 1 Quant Audit Report
**Generated:** 2026-03-20 19:29:31 UTC  
**Bot file:** `tempbot_v427_stable_20260313.py`  
**Ledger:** `/home/ubuntu/polymarket-temp-bot/trades.jsonl`  

## 1. File Discovery
| File | Size (bytes) | Last Modified | Trade Events Found |
| ---- | ------------ | ------------- | ------------------ |
| `/home/ubuntu/test_run.log` | 88 | 2026-03-07 12:11:53 | 0 |
| `/home/ubuntu/polymarket-temp-bot/tempbot_v2.log` | 2268 | 2026-03-12 16:14:06 | 0 |
| `/home/ubuntu/bot_last5000.log` | 0 | 2026-03-14 10:03:17 | 0 |
| `/home/ubuntu/polymarket-temp-bot/tempbot_final.log` | 24269060 | 2026-03-15 08:42:51 | 0 |
| `/home/ubuntu/polymarket-temp-bot/tg_monitor.log` | 736804 | 2026-03-17 15:00:28 | 0 |
| `/home/ubuntu/polymarket-temp-bot/dashboard.log` | 88 | 2026-03-17 15:25:37 | 0 |
| `/home/ubuntu/polymarket-temp-bot/tgmon.log` | 7245840 | 2026-03-19 09:10:47 | 0 |
| `/home/ubuntu/polymarket-temp-bot/trades.jsonl` | 54639 | 2026-03-20 17:59:44 | 82 |
| `/home/ubuntu/polymarket-temp-bot/redeemer.log` | 267304 | 2026-03-20 18:36:07 | 0 |
| `/home/ubuntu/polymarket-temp-bot/events.jsonl` | 1386288 | 2026-03-20 19:28:28 | 0 |
| `/home/ubuntu/polymarket-temp-bot/tempbot.log` | 62110889 | 2026-03-20 19:29:27 | 609 |

## 2. Event Stream Summary
| Metric | Value |
| ------ | ----- |
| Total events | 82 |
| ENTRY events | 32 |
| EXIT_FILL events | 50 |
| RESOLUTION events | 0 |
| Unique token_ids | 20 |
| FIFO matched pairs | 31 |
| Orphan exits (no matching ENTRY) | 32 |
| Unclosed entries (no EXIT yet) | 11 |

## 3. PnL Reconciliation
### 3a. FIFO Realized PnL
| Metric | Value |
| ------ | ----- |
| Total FIFO matched contracts | 118.4500 |
| **FIFO Total Realized PnL** | **$2.6199** |
| Winning matches | 27 |
| Losing matches | 3 |
| Average PnL per match | $0.0845 |

**FIFO PnL by exit_reason:**

| exit_reason | count | total_pnl_usd | avg_pnl_usd |
| ----------- | ----- | ------------- | ----------- |
| MM | 26 | $1.7954 | $0.0691 |
| STOPLOSS | 5 | $0.8245 | $0.1649 |

**FIFO PnL by entry strategy:**

| strategy | count | total_pnl_usd | avg_pnl_usd |
| -------- | ----- | ------------- | ----------- |
| MM | 31 | $2.6199 | $0.0845 |

### 3b. Resolution PnL (RESOLUTION events)
| Metric | Value |
| ------ | ----- |
| Resolution events (deduplicated) | 0 |
| **Resolution Total PnL** | **$0.0000** |

## 4. Orphan Exit Analysis
**32 orphan EXIT_FILL records** (SELL fills with no matching BUY ENTRY in ledger)

| token_id[:16] | ts_human | strategy | side | price | size_contracts | exit_reason |
| ------------- | -------- | -------- | ---- | ----- | -------------- | ----------- |
| 9314339693996990 | 2026-03-19 09:04:44 UTC | STOPLOSS | SELL | 0.0330 | 14.57 | STOPLOSS |
| 4253168050566639 | 2026-03-19 15:06:34 UTC | STOPLOSS | SELL | 0.2000 | 1.57 | STOPLOSS |
| 4253168050566639 | 2026-03-19 15:08:38 UTC | STOPLOSS | SELL | 0.2000 | 1.18 | STOPLOSS |
| 4253168050566639 | 2026-03-20 07:50:19 UTC | STOPLOSS | SELL | 0.2800 | 3.52 | STOPLOSS |
| 7119423264439199 | 2026-03-19 11:09:36 UTC | STOPLOSS | SELL | 0.2600 | 1.63 | STOPLOSS |
| 7119423264439199 | 2026-03-19 11:11:40 UTC | STOPLOSS | SELL | 0.2600 | 1.22 | STOPLOSS |
| 7119423264439199 | 2026-03-19 13:41:25 UTC | STOPLOSS | SELL | 0.3000 | 3.66 | STOPLOSS |
| 2352347794337032 | 2026-03-19 13:19:09 UTC | STOPLOSS | SELL | 0.1300 | 5.15 | STOPLOSS |
| 2352347794337032 | 2026-03-19 13:32:37 UTC | STOPLOSS | SELL | 0.1200 | 4.40 | STOPLOSS |
| 2352347794337032 | 2026-03-19 13:32:37 UTC | STOPLOSS | SELL | 0.1200 | 5.00 | STOPLOSS |
| 2352347794337032 | 2026-03-20 15:05:51 UTC | STOPLOSS | SELL | 0.1600 | 9.40 | STOPLOSS |
| 2352347794337032 | 2026-03-20 15:22:04 UTC | STOPLOSS | SELL | 0.1400 | 7.97 | STOPLOSS |
| 2352347794337032 | 2026-03-20 17:59:44 UTC | STOPLOSS | SELL | 0.1700 | 0.87 | STOPLOSS |
| 1068191041333252 | 2026-03-19 22:48:35 UTC | MM | SELL | 0.2400 | 1.10 | MM |
| 1068191041333252 | 2026-03-20 02:41:31 UTC | MM | SELL | 0.2800 | 3.31 | MM |
| 5255430606332321 | 2026-03-19 16:07:14 UTC | STOPLOSS | SELL | 0.2000 | 4.80 | STOPLOSS |
| 7312773187760481 | 2026-03-19 18:04:20 UTC | STOPLOSS | SELL | 0.2200 | 1.83 | STOPLOSS |
| 7312773187760481 | 2026-03-19 18:06:25 UTC | STOPLOSS | SELL | 0.2200 | 1.38 | STOPLOSS |
| 7312773187760481 | 2026-03-19 18:08:29 UTC | STOPLOSS | SELL | 0.2200 | 1.03 | STOPLOSS |
| 7312773187760481 | 2026-03-20 00:52:05 UTC | STOPLOSS | SELL | 0.2900 | 3.09 | STOPLOSS |
| 1138719340875054 | 2026-03-20 08:58:37 UTC | STOPLOSS | SELL | 0.1800 | 15.98 | STOPLOSS |
| 3352464679060950 | 2026-03-20 00:01:09 UTC | STOPLOSS | SELL | 0.0110 | 20.61 | STOPLOSS |
| 4403855275094301 | 2026-03-20 00:03:14 UTC | STOPLOSS | SELL | 0.0170 | 21.07 | STOPLOSS |
| 8375065339010937 | 2026-03-20 02:43:35 UTC | MM | SELL | 0.0800 | 2.04 | MM |
| 8375065339010937 | 2026-03-20 02:45:40 UTC | MM | SELL | 0.0800 | 1.54 | MM |
| 8375065339010937 | 2026-03-20 03:56:30 UTC | MM | SELL | 0.1000 | 4.61 | MM |
| 1208288560953564 | 2026-03-20 14:01:31 UTC | STOPLOSS | SELL | 0.2500 | 2.08 | STOPLOSS |
| 1208288560953564 | 2026-03-20 14:03:04 UTC | STOPLOSS | SELL | 0.2500 | 1.56 | STOPLOSS |
| 5775288717898840 | 2026-03-20 07:23:56 UTC | STOPLOSS | SELL | 0.0820 | 5.17 | STOPLOSS |
| 5775288717898840 | 2026-03-20 07:26:00 UTC | STOPLOSS | SELL | 0.0830 | 3.88 | STOPLOSS |
... and 2 more (see /home/ubuntu/polymarket-temp-bot/unresolved_orphans.csv)

## 5. Unclosed Entries (Open Positions)
**11 ENTRY records** have no matching EXIT_FILL (still open or lost)

| token_id[:16] | ts_human | strategy | price | remaining_contracts | notional_usd | edge_entry |
| ------------- | -------- | -------- | ----- | ------------------- | ------------ | ---------- |
| 1525012993298941 | 2026-03-19 10:02:52 UTC | MM | 0.1900 | 6.22 | 1.1818 | -0.2046 |
| 1007901258010183 | 2026-03-19 23:34:45 UTC | MM | 0.1100 | 4.34 | 0.4873 | -0.1201 |
| 1007901258010183 | 2026-03-20 02:33:44 UTC | MM | 0.0400 | 9.30 | 0.3720 | -0.0609 |
| 1007901258010183 | 2026-03-20 02:36:51 UTC | MM | 0.0400 | 3.44 | 0.1376 | -0.0609 |
| 4455432358033848 | 2026-03-20 00:58:48 UTC | MM | 0.1200 | 10.90 | 1.3080 | -0.1193 |
| 4455432358033848 | 2026-03-20 01:00:21 UTC | MM | 0.1200 | 0.72 | 0.0864 | -0.1193 |
| 4455432358033848 | 2026-03-20 16:19:05 UTC | MM | 0.0500 | 15.63 | 0.7815 | -0.0609 |
| 4455432358033848 | 2026-03-20 16:34:05 UTC | MM | 0.0500 | 6.95 | 0.3475 | -0.0609 |
| 8530339607052605 | 2026-03-20 02:35:48 UTC | MM | 0.2300 | 5.05 | 1.1615 | -0.2382 |
| 8530339607052605 | 2026-03-20 07:54:58 UTC | MM | 0.1700 | 7.01 | 1.1917 | -0.2382 |
| 1989498624646088 | 2026-03-20 02:45:08 UTC | MM | 0.1500 | 7.73 | 1.1595 | 0.0491 |

## 6. Source Trace Findings
### 6a. record_trade() — bucket field bug

**Root cause:** `portfolio_state.py:get_order_meta()` always returns
`"bucket_key": "fill_confirmed"` regardless of the actual market bucket.
The real market bucket computed by `parse_bucket(q_text)` in the MM loop
(line ~3363 of the bot) is **never stored** in `STATE.orders[order_id]`
(which is an `OrderRecord` dataclass with no `bucket` field) and therefore
never recoverable at fill time.

**Impact:** Every ENTRY and EXIT_FILL event in `trades.jsonl` has
`bucket = "fill_confirmed"` — this field carries zero informational value.

### 6b. exit_tier / exit_channel — missing fields

`check_position_exit()` computes `exit_tier` (e.g. `TIER0_TIME`, `TIER1_PROFIT`,
`TIER1B_DECAY`, `TIER2_PARTIAL`) but passes `strategy='STOPLOSS'` to `place_order()`
unconditionally (`[C1-FIX]` comment). The `exit_tier` string is **never passed** to
`record_trade()` / `_ledger_write()`. The EXIT_FILL events therefore carry no
`exit_channel` or `exit_tier` fields.

**Impact:** Cannot distinguish TIER0_TIME vs TIER1_PROFIT vs TIER1B_DECAY from the
ledger alone. All position-exit sells appear as `exit_reason=STOPLOSS`.

### 6c. Data quality notes

- **BUCKET FIELD CONTAMINATED**: all 82 events have `bucket='fill_confirmed'` — the real market bucket (or_higher/or_lower/exact) is never stored. Patch required: store `parse_bucket()` result in `OrderRecord`.
- `exit_tier` populated in 0/50 EXIT_FILL events (MISSING — patch required).
- `exit_channel` populated in 0/50 EXIT_FILL events (MISSING — patch required).
- 27 ENTRY events have `edge_entry < 0` (min=-0.2400). These may indicate stale forecast data at order time.
- 7 token_ids have EXIT_FILL records but NO ENTRY in ledger: 2352347794337032, 3352464679060950, 4403855275094301, 5255430606332321, 5775288717898840... These are pre-ledger trades (bot ran before trades.jsonl was initialised).

## 7. Logging Patch Plan (data hygiene only)
The following changes are applied to `tempbot_v427_stable_20260313.py`:

1. **`portfolio_state.py` — `OrderRecord`**: add `bucket: str = 'fill_confirmed'`
   field. `place_order()` will populate it from `parse_bucket()` result.

2. **`portfolio_state.py` — `get_order_meta()`**: return `bucket_key=o.bucket`
   instead of the hardcoded `'fill_confirmed'`.

3. **`tempbot_v427_stable_20260313.py` — `check_position_exit()`**: pass
   `exit_tier` string to `place_order()` via a new kwarg, stored in
   `STATE` under `_exit_tier_cache[token_id]`.

4. **`_ledger_write` / `record_trade()`**: add `exit_channel` and `exit_tier`
   fields to the EXIT_FILL JSON payload (read from `STATE._exit_tier_cache`).

**None of these changes touch edge formula, Kelly sizing, stoploss thresholds,
or any trading logic.**

## 8. Output Artefacts
| File | Description |
| ---- | ----------- |
| `/home/ubuntu/polymarket-temp-bot/full_reconciled_events.csv` | All normalised events, sorted by ts |
| `/home/ubuntu/polymarket-temp-bot/reconciled_fifo_matches.csv` | FIFO matched ENTRY→EXIT pairs with PnL |
| `/home/ubuntu/polymarket-temp-bot/unresolved_orphans.csv` | EXIT_FILL records with no matching ENTRY |
| `/home/ubuntu/polymarket-temp-bot/resolution_pnl.csv` | Deduplicated RESOLUTION PnL events |
| `/home/ubuntu/polymarket-temp-bot/quant_audit_report.md` | This report |
