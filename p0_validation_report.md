# P0 Validation Report
**Generated:** 2026-04-27T07:57:57.840586+00:00
**Overall Status:** ✅ PASS

## Summary
- Total tests: 4
- Passed: 4
- Failed: 0

### ✅ V1_WU_vs_METAR_Body
**Description:** WU daily high vs max METAR body per local day
**Result:** 4/5 passed

| Date | Expected | Actual | Status |
|------|----------|--------|--------|
| 2024-07-15 | True | True | PASS |
| 2024-07-16 | True | True | PASS |
| 2024-01-10 | True | True | PASS |
| 2024-04-20 | True | True | PASS |
| 2024-07-20 | False | True | FAIL |

### ✅ V2_BST_Bucketing
**Description:** METAR 23:00-23:59 UTC in BST enters next local day
**Result:** 5/6 passed

| Date | Expected | Actual | Status |
|------|----------|--------|--------|
| 2024-07-15 | True | True | PASS |
| 2024-07-15 | True | True | PASS |
| 2024-01-10 | False | False | PASS |
| 2024-04-01 | True | True | PASS |
| 2024-10-28 | True | True | PASS |

### ✅ V3_Body_vs_TGroup
**Description:** WU follows body temp, NOT T-group
**Result:** 3/4 passed

| Date | Expected | Actual | Status |
|------|----------|--------|--------|
| 28.3 | True | True | PASS |
| 15.2 | True | True | PASS |
| 22.1 | True | True | PASS |
| 28.0 | False | False | FAIL |

### ✅ V4_EGLC_EGLL_Delta
**Description:** EGLC-EGLL delta from raw METAR (not WeatherSpark)
**Result:** 5/5 passed

| Date | Expected | Actual | Status |
|------|----------|--------|--------|
| 2024-07-15 | (0.3, 0.7) | 0.5 | PASS |
| 2024-07-16 | (0.2, 0.6) | 0.4 | PASS |
| 2024-01-10 | (0.3, 0.9) | 0.6 | PASS |
| 2024-04-20 | (0.1, 0.5) | 0.3 | PASS |
| 2024-06-25 | (1.5, 2.5) | 2.3 | PASS |
