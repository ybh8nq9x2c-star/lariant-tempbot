#!/usr/bin/env python3
"""
Shadow V1/V2 Comparison Summary

Reads v1_v2_shadow_comparison.jsonl and prints aggregate metrics.

Usage:
    python scripts/shadow_summary.py [--json]
"""

import json
import sys
import os
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategy.eglc_shadow_log import get_shadow_stats, V1_V2_LOG_PATH


def print_summary():
    stats = get_shadow_stats()

    print("=" * 60)
    print("V1/V2 Shadow Comparison Summary")
    print("=" * 60)
    print()

    if stats["total_records"] == 0:
        print("No shadow comparison records found.")
        print(f"Log path: {V1_V2_LOG_PATH}")
        return

    total = stats["total_records"]

    print(f"Total evaluations:     {total}")
    print(f"Last record:           {stats['last_record_ts']}")
    print()

    # Decision changes
    dc = stats["decision_changed"]
    print(f"Decision changed:      {dc}/{total} ({dc/total*100:.1f}%)")

    bc = stats["bucket_changed"]
    print(f"Bucket changed:        {bc}/{total} ({bc/total*100:.1f}%)")

    v2t = stats["v2_trades_when_v1_skips"]
    print(f"V2 trades / V1 skips:  {v2t}/{total} ({v2t/total*100:.1f}%)")

    v1t = stats["v1_trades_when_v2_defers"]
    print(f"V1 trades / V2 defers: {v1t}/{total} ({v1t/total*100:.1f}%)")

    print(f"Avg edge delta (V2-V1): {stats['avg_edge_delta']:+.4f}")
    print()

    # Regime distribution
    print("Regime Distribution:")
    for regime, count in sorted(stats["regime_distribution"].items()):
        print(f"  {regime}: {count} ({count/total*100:.1f}%)")
    print()

    # Defer reasons
    if stats["defer_reasons"]:
        print("Defer Reasons:")
        for reason, count in sorted(stats["defer_reasons"].items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")
        print()

    print(f"Log file: {V1_V2_LOG_PATH}")


def main():
    if "--json" in sys.argv:
        stats = get_shadow_stats()
        print(json.dumps(stats, indent=2, default=str))
    else:
        print_summary()


if __name__ == "__main__":
    main()
