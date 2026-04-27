#!/usr/bin/env python3
"""
Shadow V1/V2 Comparison Summary

Reads v1_v2_shadow_comparison.jsonl and prints aggregate metrics.
Uses enhanced metrics from eglc_shadow_metrics module.

Usage:
    python scripts/shadow_summary.py [--json] [--hours 48] [--report reports/shadow_summary.md]
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategy.eglc_shadow_metrics import compute_shadow_metrics, write_shadow_report, write_shadow_json
from strategy.eglc_shadow_log import V1_V2_LOG_PATH


def print_summary(hours: int = 48):
    metrics = compute_shadow_metrics(hours=hours)

    print("=" * 60)
    print(f"V1/V2 Shadow Comparison Summary ({hours}h window)")
    print("=" * 60)
    print()

    if metrics["total_evals"] == 0:
        print("No shadow comparison records found.")
        print(f"Log path: {V1_V2_LOG_PATH}")
        return

    total = metrics["total_evals"]

    print(f"Total evaluations:     {total}")
    print(f"Total eligible:        {metrics['total_eligible']}")
    print(f"Computed at:           {metrics['computed_at']}")
    print()

    # Decision changes
    print(f"Bucket changed:        {metrics['bucket_changed_pct']:.1f}%")
    print(f"Action changed:        {metrics['action_changed_pct']:.1f}%")
    print(f"V2 trades / V1 skips:  {metrics['v2_trade_v1_skip']}")
    print(f"V2 skips / V1 trades:  {metrics['v2_skip_v1_trade']}")
    print()

    # Feature quality
    print(f"Neutral fallback:     {metrics['neutral_fallback_pct']:.1f}%")
    print(f"Missing wind:         {metrics['missing_wind_pct']:.1f}%")
    print(f"Near boundary:        {metrics['near_boundary_pct']:.1f}%")
    print()

    # Regime distribution
    regime_dist = metrics.get("regime_code_dist", {})
    if regime_dist:
        print("Regime Distribution:")
        for regime, count in sorted(regime_dist.items()):
            print(f"  {regime}: {count} ({count/total*100:.1f}%)")
        print()

    # Confidence distribution
    conf_dist = metrics.get("confidence_dist", {})
    if conf_dist:
        print("Confidence Distribution:")
        for conf, count in sorted(conf_dist.items()):
            print(f"  {conf}: {count} ({count/total*100:.1f}%)")
        print()

    # Divergence by bucket
    div_by_bucket = metrics.get("divergence_by_bucket", {})
    if div_by_bucket:
        print("Divergence by Bucket:")
        for thr, d in sorted(div_by_bucket.items()):
            print(f"  {thr}°C: {d['count']} evals, {d['divergence_pct']:.1f}% divergence, avg edge Δ={d['avg_edge_delta']:+.4f}")
        print()

    # Reason code distribution
    reason_dist = metrics.get("reason_code_dist", {})
    if reason_dist:
        print("Reason Code Distribution:")
        for reason, count in sorted(reason_dist.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")
        print()

    print(f"Log file: {V1_V2_LOG_PATH}")


def main():
    hours = 48
    report_path = None

    if "--hours" in sys.argv:
        idx = sys.argv.index("--hours")
        if idx + 1 < len(sys.argv):
            try:
                hours = int(sys.argv[idx + 1])
            except ValueError:
                pass

    if "--report" in sys.argv:
        idx = sys.argv.index("--report")
        if idx + 1 < len(sys.argv):
            report_path = sys.argv[idx + 1]

    if "--json" in sys.argv:
        metrics = compute_shadow_metrics(hours=hours)
        print(json.dumps(metrics, indent=2, default=str))
    else:
        print_summary(hours=hours)

    # Optionally write report files
    if report_path or "--report" in sys.argv:
        metrics = compute_shadow_metrics(hours=hours)
        md_path = report_path or "reports/shadow_summary.md"
        json_path = md_path.replace(".md", ".json")
        write_shadow_report(metrics, md_path)
        write_shadow_json(metrics, json_path)
        print(f"\nReports written to {md_path} and {json_path}")


if __name__ == "__main__":
    main()
