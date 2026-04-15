#!/usr/bin/env python3
import json
from datetime import datetime, timezone

entries = []
exits = []
with open("trades.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        t = json.loads(line)
        if t.get("version") == "v2-bias-patch-stable":
            if t.get("event") == "ENTRY":
                entries.append(t)
            elif t.get("event") == "EXIT_FILL":
                exits.append(t)

print("=== v2-bias-patch-stable TAGGED TRADES ===")
print(f"ENTRY count     : {len(entries)}")
print(f"EXIT_FILL count : {len(exits)}")

if entries:
    notionals = [t.get("notional_usd", 0) for t in entries]
    avg_notional = sum(notionals) / len(notionals)
    min_notional = min(notionals)
    max_notional = max(notionals)
    print(f"Avg notional    : ${avg_notional:.4f}")
    print(f"Min notional    : ${min_notional:.4f}")
    print(f"Max notional    : ${max_notional:.4f}")
    print(f"Below $1.00     : {sum(1 for n in notionals if n < 1.0)}")

    toxic = [t for t in entries if t.get("city") == "seoul" and t.get("bucket") == "fill_confirmed"]
    print(f"Seoul/fill_confirmed entries: {len(toxic)}  (should be 0)")

    seoul  = sum(1 for t in entries if t.get("city") == "seoul")
    london = sum(1 for t in entries if t.get("city") == "london")
    print(f"City mix        : Seoul={seoul}  London={london}")

    edges = [t.get("edge_entry", 0) for t in entries]
    pos_edge = sum(1 for e in edges if e > 0)
    neg_edge = sum(1 for e in edges if e <= 0)
    avg_edge = sum(edges) / len(edges)
    print(f"Avg edge        : {avg_edge:+.4f}")
    print(f"Positive edge   : {pos_edge}  Negative/zero: {neg_edge}")

    print()
    print("=== ENTRY DETAIL ===")
    for t in entries:
        ts = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
        city   = t.get("city", "?")
        bucket = t.get("bucket", "?")
        px     = t.get("price", 0)
        notional = t.get("notional_usd", 0)
        fair   = t.get("fair_prob", 0)
        mkt    = t.get("mkt_prob", 0)
        edge   = t.get("edge_entry", 0)
        print(f"  {ts} | {city:6s} | {bucket:14s} | px={px:.4f} | not={notional:.4f} | fair={fair:.4f} | mkt={mkt:.4f} | edge={edge:+.4f}")
else:
    print("No tagged ENTRY events found yet.")
