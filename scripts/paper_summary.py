#!/usr/bin/env python3
"""Paper trading summary for EGLC V2.

Usage:
    python scripts/paper_summary.py [--json]
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategy.eglc_paper_trader import compute_paper_pnl


def print_paper_summary():
    pnl = compute_paper_pnl()

    print("=" * 60)
    print("EGLC V2 Paper Trading Summary")
    print("=" * 60)
    print()

    print(f"Total trades:        {pnl['total_trades']}")
    print(f"Open trades:         {pnl['open_trades']}")
    print(f"Settled trades:      {pnl['settled_trades']}")
    print()

    if pnl['settled_trades'] > 0:
        print(f"Won:                 {pnl['won']}")
        print(f"Lost:                {pnl['lost']}")
        print(f"Hit rate:            {pnl['hit_rate']:.1f}%")
        print(f"Total PnL:           ${pnl['total_pnl']:.2f}")
        print(f"Avg PnL/trade:       ${pnl['avg_pnl_per_trade']:.4f}")
        print(f"Max win:             ${pnl['max_win']:.4f}")
        print(f"Max loss:            ${pnl['max_loss']:.4f}")
        print()

        # PnL by regime
        pnl_by_regime = pnl.get('pnl_by_regime', {})
        if pnl_by_regime:
            print("PnL by Regime:")
            for regime, data in sorted(pnl_by_regime.items()):
                print(f"  {regime}: {data['count']} trades, PnL=${data['pnl']:.4f}")
            print()

        # PnL by confidence
        pnl_by_conf = pnl.get('pnl_by_confidence', {})
        if pnl_by_conf:
            print("PnL by Confidence:")
            for conf, data in sorted(pnl_by_conf.items()):
                print(f"  {conf}: {data['count']} trades, PnL=${data['pnl']:.4f}")
            print()

        # Daily PnL
        daily_pnl = pnl.get('daily_pnl', {})
        if daily_pnl:
            print("Daily PnL:")
            for day, p in sorted(daily_pnl.items()):
                icon = "+" if p >= 0 else ""
                print(f"  {day}: ${icon}{p:.4f}")
            print()

    # Open positions
    open_pos = pnl.get('open_positions', [])
    if open_pos:
        print(f"Open Positions ({len(open_pos)}):")
        for pos in open_pos[:10]:
            print(f"  {pos['ts_iso'][:19]} {pos['action']} {pos['bucket']} @ {pos['paper_price']:.4f}")
        if len(open_pos) > 10:
            print(f"  ... and {len(open_pos) - 10} more")
        print()

    print(f"Computed at: {pnl.get('computed_at', 'N/A')}")


def main():
    if "--json" in sys.argv:
        pnl = compute_paper_pnl()
        print(json.dumps(pnl, indent=2, default=str))
    else:
        print_paper_summary()


if __name__ == "__main__":
    main()
