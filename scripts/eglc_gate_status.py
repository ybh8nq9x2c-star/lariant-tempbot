#!/usr/bin/env python3
"""CLI to show EGLC V2 gate status, metrics, and promotion eligibility.

Usage:
    python scripts/eglc_gate_status.py [--target paper|tiny_live|live] [--json]
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategy.eglc_config import (
    get_effective_mode, EGLC_V2_MODE, is_p0_passed,
    HUMAN_PROMOTION_APPROVED, VALID_MODES
)
from strategy.eglc_promotion_gate import evaluate_gate, check_tiny_live_safety
from strategy.eglc_shadow_metrics import compute_shadow_metrics


def print_gate_status(target_mode=None):
    print("=" * 60)
    print("EGLC V2 Gate Status")
    print("=" * 60)
    print()

    # Current configuration
    print("Configuration:")
    print(f"  EGLC_V2_MODE (env):       {EGLC_V2_MODE}")
    print(f"  Effective mode:            {get_effective_mode()}")
    print(f"  P0 validation passed:      {is_p0_passed()}")
    print(f"  Human promotion approved:  {HUMAN_PROMOTION_APPROVED}")
    print()

    # Evaluate gate
    target = target_mode or "paper"
    gate = evaluate_gate(target_mode=target)

    print(f"Promotion Evaluation -> {target.upper()}:")
    print(f"  Current mode:     {gate['current_mode']}")
    print(f"  Target mode:      {gate['target_mode']}")
    print(f"  Eligible:         {'YES' if gate['eligible'] else 'NO'}")
    print(f"  Recommendation:   {gate['recommendation']}")
    print()

    # Metrics
    metrics = gate.get("metrics", {})
    print("Metrics:")
    print(f"  Shadow hours:     {metrics.get('shadow_hours', 0):.1f}")
    print(f"  Shadow evals:     {metrics.get('shadow_evals', 0)}")
    print(f"  Error rate/hr:    {metrics.get('error_rate_per_hour', 0)}")
    print()

    # Blockers
    blockers = gate.get("blockers", [])
    if blockers:
        print("Blockers:")
        for b in blockers:
            status_icon = "BLOCKED" if b["status"] == "BLOCKED" else "WARN"
            print(f"  [{status_icon}] {b['gate']}: {b['detail']}")
        print()
    else:
        print("  No blockers!")
        print()

    # Safety check (if in tiny_live mode)
    effective = get_effective_mode()
    if effective in ("tiny_live", "live"):
        safety = check_tiny_live_safety()
        print("Tiny Live Safety Check:")
        print(f"  Safe:             {'YES' if safety['safe'] else 'NO'}")
        print(f"  Reason:           {safety['reason']}")
        if safety['should_downgrade']:
            print(f"  Should downgrade: YES -> {safety['suggested_mode']}")
        for check_name, passed in safety['checks'].items():
            icon = "OK" if passed else "FAIL"
            print(f"  [{icon}] {check_name}")
        print()

    # Shadow metrics summary
    shadow = compute_shadow_metrics(hours=48)
    if shadow["total_evals"] > 0:
        print("Shadow Metrics (48h):")
        print(f"  Total evals:      {shadow['total_evals']}")
        print(f"  Action changed:   {shadow['action_changed_pct']:.1f}%")
        print(f"  Bucket changed:   {shadow['bucket_changed_pct']:.1f}%")
        print(f"  Missing wind:     {shadow['missing_wind_pct']:.1f}%")
        print(f"  Near boundary:    {shadow['near_boundary_pct']:.1f}%")
    else:
        print("Shadow Metrics: No data available")
    print()

    print(f"Evaluated at: {gate.get('evaluated_at', 'N/A')}")


def main():
    target = None
    if "--target" in sys.argv:
        idx = sys.argv.index("--target")
        if idx + 1 < len(sys.argv):
            target = sys.argv[idx + 1]

    if "--json" in sys.argv:
        gate = evaluate_gate(target_mode=target)
        import json
        print(json.dumps(gate, indent=2, default=str))
    else:
        print_gate_status(target_mode=target)


if __name__ == "__main__":
    main()
