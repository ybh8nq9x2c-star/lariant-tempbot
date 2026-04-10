# V5 Model — Architectural Decision Record

**Date:** 2026-04-09  
**Status:** APPROVED  
**Authors:** Lariant Quant Team  
**ADR-ID:** ADR-001

---

## Decision: Triple Asynchronous Merge (Offline)

The `sigma_ens` (Ensemble Spread) field will **NOT** be added to the real-time
logging pipeline on EC2. The V5 training dataset will be assembled exclusively
via an offline **Triple Merge** using `pandas.merge_asof`.

### Rationale

1. **Zero Production Risk** — No modifications to the live execution engine.
2. **Temporal Precision** — `merge_asof` matches each trade to the nearest
   microstructure snapshot within a configurable tolerance (e.g. ±5s).
3. **Data Completeness** — Ensemble spread data from local climate models is
   generated asynchronously and cannot be synchronized with the 3-second
   trading loop without introducing latency.

---

## Data Sources (Triple Merge)

| Source | File | Frequency | Key Fields |
|--------|------|-----------|------------|
| **Microstructure** | `v5_book_data.jsonl` | ~3s/cycle | `ts`, `token_id`, `city`, `mid`, `spread_bp`, `bid_depth_usd`, `ask_depth_usd`, `edge_fc`, `true_p`, `T_days` |
| **Executions** | `trades.jsonl` | Event-driven | `ts`, `token_id`, `city`, `side`, `price`, `size_contracts`, `notional_usd`, `edge_entry`, `fair_prob`, `mkt_prob`, `kelly_frac`, `exit_reason`, `strategy` |
| **Ensemble Spread** | `ensemble_spread_cache.csv` | Hourly | `ts`, `city`, `sigma_ens`, `fc_temp`, `spread_ens` |

---

## Merge Logic — Time-Travel Merge

```
Step 1: Load v5_book_data.jsonl        → df_book
Step 2: Load trades.jsonl               → df_trades
Step 3: Load ensemble_spread_cache.csv  → df_ens

Step 4: pd.merge_asof(
    df_trades.sort_values("ts"),
    df_book.sort_values("ts"),
    on="ts",
    by="token_id",
    direction="backward",
    tolerance=pd.Timedelta("5s")
) → df_merged_1

Step 5: pd.merge_asof(
    df_merged_1.sort_values("ts"),
    df_ens.sort_values("ts"),
    on="ts",
    by="city",
    direction="nearest",
    tolerance=pd.Timedelta("1h")
) → df_v5_training

Step 6: Feature engineering → v5_training_set.parquet
```

---

## Schema Reconciliation (Key Mapping)

| V5 Feature | v5_book_data | trades.jsonl | ensemble |
|------------|-------------|-------------|----------|
| alpha (edge) | `edge_fc` | `edge_entry` | — |
| p_mkt | `mid` | `mkt_prob` | — |
| p_true | `true_p` | `fair_prob` | — |
| sigma_ens | — | — | `sigma_ens` |
| spread | `spread_bp` | — | — |
| depth_bid | `bid_depth_usd` | — | — |
| depth_ask | `ask_depth_usd` | — | — |
| time_decay | `T_days` | `Tdays` | — |

---

## Output

**Final artifact:** `v5_training_set.parquet`
- One row per trade event (ENTRY or EXIT)
- Enriched with microstructure snapshot at execution time
- Enriched with ensemble spread at prediction time
- Ready for LightGBM / XGBoost training

---

## Constraints

- EC2 execution engine: **NO CHANGES** (frozen)
- `v5_book_data.jsonl` logging: **ALWAYS-ON** (regardless of trade execution)
- `sigma_ens`: computed offline only, never on EC2
- Merge tolerance: 5s for book data, 1h for ensemble data

---

## Timeline

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ Complete | V5 book data logging (always-on) |
| Phase 2 | ✅ Complete | CB-EDGE fix + city isolation + hard guard |
| Phase 2a | ✅ Complete | Dashboard SWR cache + tail-reading |
| Phase 3 | 🔄 In Progress | Triple Merge pipeline + feature engineering |
| Phase 4 | 📋 Planned | LightGBM V5 model training |
| Phase 5 | 📋 Planned | V5 model deployment to EC2 |

---

*"The Time-Travel Merge: because real-time is for execution, offline is for intelligence."*
