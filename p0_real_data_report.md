# P0 Real-Data Validation Report
**Generated:** 2026-04-27T08:25:20.911442+00:00
**Data Dir:** /a0/usr/projects/tempbot
**Overall Status:** ⚠️ PARTIAL

## Summary
- Passed: 0
- Failed: 0
- Skipped: 4

### ⏭️ V1_WU_vs_METAR_Body_REAL
**Description:** WU daily high vs max METAR body (real data)
**Skip Reason:** N/A
- data_source: /a0/usr/projects/tempbot/EGLC_WU_METAR_DAILY_COMPARISON.csv
- match_rate: N/A

### ⏭️ V2_BST_Bucketing_REAL
**Description:** BST 23xx UTC inheritance (real data)
**Skip Reason:** N/A
- data_source: /a0/usr/projects/tempbot/EGLC_BST_BUCKETING_TEST.csv

### ⏭️ V3_Body_vs_TGroup_REAL
**Description:** WU follows body temp not T-group (real data)
**Skip Reason:** N/A
- discriminating_cases: 0
- data_source: /a0/usr/projects/tempbot/EGLC_WU_METAR_DAILY_COMPARISON.csv

### ⏭️ V4_EGLC_EGLL_Delta_REAL
**Description:** EGLC-EGLL delta from raw METAR (real data)
**Skip Reason:** No paired EGLC/EGLL data available
