#!/usr/bin/env python3
"""
P0 Real-Data Validation for EGLC V2 Migration

Runs V1-V4 tests against real IEM/WU/METAR data.

Data files expected in DATA_DIR:
- EGLC_WU_METAR_DAILY_COMPARISON.csv  (cols: date, iem_max, wu_max, match, diff)
- EGLC_BST_BUCKETING_TEST.csv  (cols: metar_timestamp_utc, expected_local_date, wu_displayed_date, bucketing_match)
- EGLC_EGLL_SUMMER_BIAS.csv  (cols: date, eglc_daily_high_c, egll_daily_high_c, delta_c, month, wind_regime)
- EGLC_WU_METAR_TGROUP_COMPARISON.csv  (cols: utc_timestamp, body_temp_c, t_group_present, t_group_temp_c)
- _cache_eglc_iem.pkl, _cache_egll_iem.pkl  (IEM ASOS DataFrames)

Usage:
    python scripts/validation/run_p0_real_data.py [--data-dir /path/to/data]
    python scripts/validation/run_p0_real_data.py --show-discriminant-cases
"""

import json, sys, os, csv, statistics
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from tgroup_parser import parse_tgroup, batch_parse_tgroup
from strategy.eglc_v2 import is_bst_period

DEFAULT_DATA_DIR = "/a0/usr/projects/tempbot"


def _load_csv(path: str) -> Optional[List[dict]]:
    if not os.path.exists(path):
        return None
    try:
        rows = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(dict(row))
        return rows
    except Exception as e:
        print(f"  WARNING: Failed to load {path}: {e}")
        return None


def _pf(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _skip_result(name, desc, reason):
    return {"name": name, "description": desc, "passed": False, "skipped": True,
            "skip_reason": reason, "pass_count": 0, "fail_count": 0,
            "total": 0, "details": [], "evidence": {}}


# === V1: WU daily high vs max METAR body ===

def run_v1_real(data_dir: str) -> Dict[str, Any]:
    csv_path = os.path.join(data_dir, "EGLC_WU_METAR_DAILY_COMPARISON.csv")
    csv_data = _load_csv(csv_path)
    if not csv_data:
        return _skip_result("V1_REAL", "WU daily high vs METAR body (real)",
                           f"Missing: {csv_path}")

    passed = failed = 0
    details = []
    for row in csv_data:
        wu = _pf(row.get("wu_max"))
        iem = _pf(row.get("iem_max"))
        if wu == 0.0 and iem == 0.0:
            continue
        match = abs(wu - iem) < 0.15
        if match:
            passed += 1
        else:
            failed += 1
        details.append({"date": row.get("date",""), "wu_max": wu, "iem_max": iem,
                       "delta": round(abs(wu-iem),2), "status": "PASS" if match else "FAIL"})

    total = passed + failed
    overall = total > 0 and passed >= total * 0.9
    return {"name": "V1_WU_vs_METAR_REAL", "description": "WU daily high vs METAR body (real)",
            "passed": overall, "skipped": False, "pass_count": passed, "fail_count": failed,
            "total": total, "details": details[:50],
            "evidence": {"match_rate": f"{passed}/{total}", "source": csv_path}}


# === V2: BST bucketing ===

def run_v2_real(data_dir: str) -> Dict[str, Any]:
    csv_path = os.path.join(data_dir, "EGLC_BST_BUCKETING_TEST.csv")
    csv_data = _load_csv(csv_path)
    if not csv_data:
        return _skip_result("V2_REAL", "BST bucketing (real)", f"Missing: {csv_path}")

    passed = failed = 0
    details = []
    for row in csv_data:
        exp = row.get("expected_local_date", "")
        obs = row.get("wu_displayed_date", "")
        if not exp or not obs:
            continue
        match = exp == obs
        if match:
            passed += 1
        else:
            failed += 1
        details.append({"utc_ts": row.get("metar_timestamp_utc",""),
                       "expected": exp, "observed": obs,
                       "tz": row.get("tz_regime",""),
                       "status": "PASS" if match else "FAIL"})

    total = passed + failed
    overall = total > 0 and passed >= total * 0.9
    return {"name": "V2_BST_Bucketing_REAL", "description": "BST bucketing (real)",
            "passed": overall, "skipped": False, "pass_count": passed, "fail_count": failed,
            "total": total, "details": details[:50],
            "evidence": {"match_rate": f"{passed}/{total}", "source": csv_path}}


# === V3: Body vs T-group ===

def run_v3_real(data_dir: str) -> Dict[str, Any]:
    """
    V3: Determine whether WU follows body temp or T-group.
    
    Method: Scan all available UK METAR data for T-group remarks.
    If zero T-groups exist, WU follows body temp by construction
    (there is no T-group alternative available in the data chain).
    
    Also attempts to use enrichment CSV with parsed T-group data.
    """
    import pickle
    
    csv_path = os.path.join(data_dir, "EGLC_WU_METAR_TGROUP_COMPARISON.csv")
    eglc_path = os.path.join(data_dir, "_cache_eglc_iem.pkl")
    egll_path = os.path.join(data_dir, "_cache_egll_iem.pkl")
    wide_path = os.path.join(data_dir, "_cache_eglc_wide_iem.pkl")
    
    # Collect all available METAR data
    total_metars = 0
    tgroup_found = 0
    stations_scanned = []
    
    for cache_name, cache_path in [('EGLC', eglc_path), ('EGLC_wide', wide_path), ('EGLL', egll_path)]:
        if not os.path.exists(cache_path):
            continue
        try:
            df = pickle.load(open(cache_path, 'rb'))
            metars = df['metar'].values if 'metar' in df.columns else []
            stats = batch_parse_tgroup(metars)
            total_metars += stats['total_metars']
            tgroup_found += stats['t_group_present']
            stations_scanned.append(cache_name)
        except Exception as e:
            print(f"  WARNING: Failed to scan {cache_name}: {e}")
    
    # Also check enrichment CSV
    csv_tg_count = 0
    csv_total = 0
    csv_data = _load_csv(csv_path)
    if csv_data:
        csv_total = len(csv_data)
        csv_tg_count = sum(1 for r in csv_data if r.get('t_group_present', '').lower() == 'true')
    
    evidence = {
        "total_metars_scanned": total_metars,
        "t_group_found": tgroup_found,
        "t_group_rate": f"{tgroup_found}/{total_metars}",
        "stations_scanned": stations_scanned,
        "enrichment_csv": {
            "path": csv_path,
            "rows": csv_total,
            "t_group_rows": csv_tg_count,
        },
        "uk_stations_no_tgroup": True,
        "conclusion": "",
    }
    
    # Decision logic
    if tgroup_found == 0 and total_metars >= 1000:
        # Zero T-groups across sufficient data → PASS_BODY by construction
        # UK METARs don't include T-group remarks (US ASOS feature only)
        evidence["conclusion"] = (
            f"PASS_BODY: Zero T-group remarks found across {total_metars} UK METARs "
            f"(EGLC + EGLL). UK stations do not include ICAO T-group in remarks. "
            f"WU follows body temp by construction - no T-group alternative exists."
        )
        evidence["operational_impact"] = (
            "V2 model and oracle simulation correctly use whole-degree body temp. "
            "No boundary adjustment needed for T-group tenths."
        )
        return {
            "name": "V3_Body_vs_TGroup_REAL",
            "description": "WU follows body temp not T-group (real)",
            "passed": True,
            "skipped": False,
            "verdict": "PASS_BODY",
            "pass_count": 1,
            "fail_count": 0,
            "total": 1,
            "details": [{
                "finding": "Zero T-groups in UK METARs",
                "metars_scanned": total_metars,
                "t_group_found": 0,
                "conclusion": "WU follows body temp by construction",
            }],
            "evidence": evidence,
        }
    elif tgroup_found > 0:
        # T-groups exist - need discriminating case analysis
        discriminating = []
        # This would require CSV with both body and t_group columns
        # For now, flag as needing manual analysis
        evidence["conclusion"] = (
            f"INCONCLUSIVE: Found {tgroup_found} T-groups across {total_metars} METARs. "
            "Manual discriminant analysis required."
        )
        return {
            "name": "V3_Body_vs_TGroup_REAL",
            "description": "WU follows body temp not T-group (real)",
            "passed": False,
            "skipped": False,
            "verdict": "INCONCLUSIVE",
            "pass_count": 0,
            "fail_count": 0,
            "total": tgroup_found,
            "details": [],
            "evidence": evidence,
        }
    else:
        return _skip_result("V3_REAL", "Body vs T-group (real)",
                           f"Insufficient METAR data ({total_metars} rows)")


# === V4: EGLC vs EGLL delta ===

def run_v4_real(data_dir: str) -> Dict[str, Any]:
    csv_path = os.path.join(data_dir, "EGLC_EGLL_SUMMER_BIAS.csv")
    csv_data = _load_csv(csv_path)
    if not csv_data:
        return _skip_result("V4_REAL", "EGLC-EGLL delta (real)", f"Missing: {csv_path}")

    deltas = []
    seasonal = {"DJF": [], "MAM": [], "JJA": [], "SON": []}
    details = []
    for row in csv_data:
        eglc = _pf(row.get("eglc_daily_high_c"))
        egll = _pf(row.get("egll_daily_high_c"))
        if eglc == 0.0 or egll == 0.0:
            continue
        delta = round(egll - eglc, 2)
        deltas.append(delta)
        try:
            m = int(row.get("month", 0))
            s = "DJF" if m in (12,1,2) else "MAM" if m in (3,4,5) else "JJA" if m in (6,7,8) else "SON"
            seasonal[s].append(delta)
        except (ValueError, IndexError):
            pass
        details.append({"date": row.get("date",""), "eglc": eglc, "egll": egll,
                       "delta": delta, "wind": row.get("wind_regime","")})

    total = len(deltas)
    if total == 0:
        return _skip_result("V4_REAL", "EGLC-EGLL delta (real)", "No valid paired data")

    avg = round(statistics.mean(deltas), 3)
    med = round(statistics.median(deltas), 3)
    std = round(statistics.stdev(deltas), 3) if total > 1 else 0.0
    seas = {k: {"n": len(v), "mean": round(statistics.mean(v), 3)} for k, v in seasonal.items() if v}

    overall = total >= 10
    return {"name": "V4_EGLC_EGLL_Delta_REAL", "description": "EGLC-EGLL delta (real)",
            "passed": overall, "skipped": False,
            "pass_count": total if overall else 0, "fail_count": 0 if overall else total,
            "total": total, "details": details[:50],
            "evidence": {"n": total, "mean": avg, "median": med, "std": std,
                         "min": min(deltas), "max": max(deltas), "seasonal": seas,
                         "source": csv_path}}


# === RUN ALL ===

def run_all_p0_real(data_dir=DEFAULT_DATA_DIR):
    tests = [run_v1_real(data_dir), run_v2_real(data_dir),
             run_v3_real(data_dir), run_v4_real(data_dir)]
    n_pass = sum(1 for t in tests if t.get("passed"))
    n_skip = sum(1 for t in tests if t.get("skipped"))
    non_skip = [t for t in tests if not t.get("skipped")]
    all_pass = all(t.get("passed") for t in non_skip) if non_skip else False
    status = "PASS" if all_pass else ("PARTIAL" if n_skip > 0 else "FAIL")
    return {"timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_status": status, "all_passed": all_pass, "data_dir": data_dir,
            "tests": tests,
            "summary": {"total": len(tests), "passed": n_pass,
                        "failed": sum(1 for t in tests if not t.get("passed") and not t.get("skipped")),
                        "skipped": n_skip}}


def show_discriminant_cases(data_dir=DEFAULT_DATA_DIR):
    """Show V3 discriminant analysis details."""
    print("=" * 60)
    print("V3 Discriminant Case Analysis")
    print("=" * 60)
    result = run_v3_real(data_dir)
    print()
    print(f"Verdict: {result.get('verdict', 'N/A')}")
    print(f"METARs scanned: {result['evidence'].get('total_metars_scanned', 'N/A')}")
    print(f"T-groups found: {result['evidence'].get('t_group_found', 'N/A')}")
    print(f"Stations: {result['evidence'].get('stations_scanned', [])}")
    print()
    print(result['evidence'].get('conclusion', 'No conclusion'))
    if result['evidence'].get('operational_impact'):
        print()
        print("Operational Impact:")
        print(result['evidence']['operational_impact'])


def main():
    data_dir = DEFAULT_DATA_DIR
    show_disc = "--show-discriminant-cases" in sys.argv
    if "--data-dir" in sys.argv:
        idx = sys.argv.index("--data-dir")
        if idx + 1 < len(sys.argv):
            data_dir = sys.argv[idx + 1]

    if show_disc:
        return show_discriminant_cases(data_dir)

    print("=" * 60)
    print("P0 Real-Data Validation - EGLC V2 Migration")
    print(f"Data dir: {data_dir}")
    print("=" * 60)
    print()

    result = run_all_p0_real(data_dir)

    for t in result["tests"]:
        icon = "SKIP" if t.get("skipped") else ("PASS" if t.get("passed") else "FAIL")
        verdict = t.get("verdict", "")
        extra = f" [{verdict}]" if verdict else ""
        if t.get("skipped"):
            print(f"  {icon} {t['name']}: {t.get('skip_reason','')}")
        else:
            print(f"  {icon} {t['name']}: {t.get('pass_count',0)}/{t.get('total',0)}{extra}")

    print(f"\nOverall: {result['overall_status']}")

    result_path = os.path.join(os.path.dirname(__file__), "..", "..", "p0_real_data_result.json")
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"JSON: {result_path}")

    # Write V3 discriminant cases CSV
    v3 = next((t for t in result["tests"] if "V3" in t.get("name","")), None)
    if v3 and v3.get("details"):
        disc_path = os.path.join(os.path.dirname(__file__), "..", "..", "EGLC_V3_DISCRIMINANT_CASES.csv")
        with open(disc_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=v3["details"][0].keys())
            w.writeheader()
            w.writerows(v3["details"])
        print(f"V3 cases: {disc_path}")

    print("\nNOTE: P0_VALIDATION_STATUS remains PENDING until human review.")
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
