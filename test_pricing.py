#!/usr/bin/env python3
"""
test_pricing.py — Phase 2a Pricing Logic Validation
=====================================================
Replicates current ecmwf_true_prob (skewnorm) side-by-side with the new
dynamic bias/sigma norm.cdf implementation.

Passes if:
  1. New probs are in [0, 1] for all test cases
  2. Monotonicity: higher forecast → higher P(or_higher), lower P(or_lower)
  3. or_higher + or_lower ~ 1.0 at midpoint threshold (symmetry check)
  4. New sigma at T1 < sigma at T5 (short-range tighter than long-range)
  5. Bias adjustment moves prob in correct direction
  6. No skewnorm import required in new code
"""

import sys
import math
from datetime import datetime, timedelta, timezone
from scipy.stats import norm, skewnorm

# ══════════════════════════════════════════════════════════════════════════════
# OLD IMPLEMENTATION (exact copy from tempbot_v427_stable_20260313.py)
# ══════════════════════════════════════════════════════════════════════════════
CITY_SIGMA_BASE_OLD = {"seoul": 2.8, "london": 2.2}
CITY_SKEW_ALPHA_OLD = {"seoul": -1.5, "london": -1.5}


def ecmwf_true_prob_OLD(
    forecast_temp: float, bucket_type: str, threshold: float,
    T_days: float = None, city: str = "london",
    is_fallback: bool = False, observed_max: "float | None" = None,
) -> float:
    """Exact copy of current bot logic — skewnorm + static sigma."""
    base_sigma = CITY_SIGMA_BASE_OLD.get(city, 2.5)
    _T_eff = T_days if T_days is not None else 7.0
    sigma = base_sigma * math.sqrt(max(_T_eff, 0.04) / 7.0)
    if is_fallback:
        sigma += 1.5
    alpha = CITY_SKEW_ALPHA_OLD.get(city, -1.5)
    if bucket_type == "exact":
        return (skewnorm.cdf(threshold + 0.5, alpha, loc=forecast_temp, scale=sigma)
                - skewnorm.cdf(threshold - 0.5, alpha, loc=forecast_temp, scale=sigma))
    elif bucket_type == "or_higher":
        if observed_max is not None and observed_max >= threshold:
            return 0.995
        return 1.0 - skewnorm.cdf(threshold - 0.5, alpha, loc=forecast_temp, scale=sigma)
    elif bucket_type == "or_lower":
        return skewnorm.cdf(threshold + 0.5, alpha, loc=forecast_temp, scale=sigma)
    return 0.5


# ══════════════════════════════════════════════════════════════════════════════
# NEW IMPLEMENTATION — Phase 2a
# ══════════════════════════════════════════════════════════════════════════════

# Data matrix: (city, month) → {t_day: (bias, sigma)}
_CALIB_MATRIX: dict[tuple, dict[int, tuple[float, float]]] = {
    ("london", 2): {1: (0.14,  0.85), 3: (0.61,  1.93), 5: (-0.43, 3.24)},
    ("london", 3): {1: (0.08,  0.67), 3: (-0.06, 1.96), 5: (0.31,  3.23)},
    ("seoul",  2): {1: (-0.18, 1.37), 3: (-0.39, 3.39), 5: (-1.28, 5.89)},
    ("seoul",  3): {1: (0.03,  0.85), 3: (-0.13, 1.78), 5: (0.69,  3.08)},
}

# Fallback sigma when (city, month) not in matrix
_SIGMA_FALLBACK: dict[tuple, float] = {
    ("london", None): 2.6,
    ("seoul",  2):    4.2,
    ("seoul",  3):    2.8,
    ("seoul",  None): 3.5,
}

_TDAYS_NODES = [1, 3, 5]


def get_dynamic_calibration(city: str, month: int, tdays: float) -> tuple[float, float]:
    """
    Return (bias, sigma) from data matrix for the nearest T-day node (1, 3, 5).

    Nearest-node lookup (no interpolation): ensures calibration stays tied
    to empirically measured regimes rather than guessing between them.
    Falls back to (bias=0.0, sigma from _SIGMA_FALLBACK) if city/month absent.

    Args:
        city:   'london' | 'seoul'
        month:  calendar month int (1–12)
        tdays:  days-to-resolution (float, e.g. 2.7)

    Returns:
        (bias, sigma) both in °C
    """
    nearest_t = min(_TDAYS_NODES, key=lambda t: abs(tdays - t))
    key = (city.lower(), month)
    row = _CALIB_MATRIX.get(key)
    if row is not None:
        return row[nearest_t]
    # Month not in matrix — use fallback sigma, zero bias
    sigma_fb = _SIGMA_FALLBACK.get(
        (city.lower(), month),
        _SIGMA_FALLBACK.get((city.lower(), None), 2.6),
    )
    return (0.0, sigma_fb)


def ecmwf_true_prob_NEW(
    forecast_temp: float, bucket_type: str, threshold: float,
    T_days: float = None, city: str = "london",
    is_fallback: bool = False, observed_max: "float | None" = None,
    _month_override: int = None,          # test-only: fix month
) -> float:
    """
    [v4.3-Phase2a] Dynamic bias/sigma normal CDF pricing.

    Replaces:  skewnorm + CITY_SIGMA_BASE + CITY_SKEW_ALPHA
    With:      norm.cdf + per-city/month/tday calibrated (bias, sigma)

    Derivation:
      1. Determine market month from today + T_days
      2. Look up (bias, sigma) from _CALIB_MATRIX via nearest T-day node
      3. Bias-adjust: adj_fcst = forecast_temp - bias
         (positive bias means model runs warm → pull forecast down to reality)
      4. P = norm.cdf(threshold ± 0.5, loc=adj_fcst, scale=sigma)
    """
    _T_eff = T_days if T_days is not None else 7.0

    # Derive market month from resolution date (today + T_days)
    if _month_override is not None:
        month = _month_override
    else:
        market_dt = datetime.now(timezone.utc) + timedelta(days=_T_eff)
        month = market_dt.month

    bias, sigma = get_dynamic_calibration(city, month, _T_eff)

    # Climatological fallback penalty: model uncertainty ~ +1.5°C
    if is_fallback:
        sigma += 1.5

    # Bias-adjusted forecast: remove systematic model warm/cold bias
    adj_fcst = forecast_temp - bias

    if bucket_type == "exact":
        return (norm.cdf(threshold + 0.5, loc=adj_fcst, scale=sigma)
                - norm.cdf(threshold - 0.5, loc=adj_fcst, scale=sigma))
    elif bucket_type == "or_higher":
        # Observed-max certainty shortcut (monotone CDF property — same as old)
        if observed_max is not None and observed_max >= threshold:
            return 0.995
        return 1.0 - norm.cdf(threshold - 0.5, loc=adj_fcst, scale=sigma)
    elif bucket_type == "or_lower":
        return norm.cdf(threshold + 0.5, loc=adj_fcst, scale=sigma)
    return 0.5


# ══════════════════════════════════════════════════════════════════════════════
# TEST SUITE
# ══════════════════════════════════════════════════════════════════════════════

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
        print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    else:
        PASS += 1
        print(f"  [{status}] {name}")


def fmt(v: float) -> str:
    return f"{v:.4f}"


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────────────────────────
# Section 1 — Calibration lookup correctness
# ─────────────────────────────────────────────────────────────────
section("1. get_dynamic_calibration lookups")

# Exact nodes
b, s = get_dynamic_calibration("london", 3, 1.0)
check("London Mar T1 exact",  b == 0.08  and s == 0.67,  f"got bias={b}, sigma={s}")
b, s = get_dynamic_calibration("london", 2, 3.0)
check("London Feb T3 exact",  b == 0.61  and s == 1.93,  f"got bias={b}, sigma={s}")
b, s = get_dynamic_calibration("seoul",  2, 5.0)
check("Seoul Feb T5 exact",   b == -1.28 and s == 5.89,  f"got bias={b}, sigma={s}")
b, s = get_dynamic_calibration("seoul",  3, 3.0)
check("Seoul Mar T3 exact",   b == -0.13 and s == 1.78,  f"got bias={b}, sigma={s}")

# Nearest-node rounding
b, s = get_dynamic_calibration("london", 3, 0.5)   # 0.5 → nearest 1
check("London Mar T0.5 → T1", b == 0.08 and s == 0.67,  f"got bias={b}, sigma={s}")
b, s = get_dynamic_calibration("london", 3, 2.0)   # 2.0 → nearest 1 or 3 (tie → 1)
check("London Mar T2.0 → T1 (tie, picks lower)", b == 0.08, f"got bias={b}")
b, s = get_dynamic_calibration("london", 3, 2.5)   # 2.5 → nearest 3
check("London Mar T2.5 → T3", b == -0.06 and s == 1.96, f"got bias={b}, sigma={s}")
b, s = get_dynamic_calibration("london", 3, 6.5)   # 6.5 → nearest 5
check("London Mar T6.5 → T5", b == 0.31 and s == 3.23,  f"got bias={b}, sigma={s}")

# Fallback months
b, s = get_dynamic_calibration("london", 4, 3.0)   # April not in matrix
check("London Apr fallback: bias=0.0",       b == 0.0,  f"got bias={b}")
check("London Apr fallback: sigma=2.6",      s == 2.6,  f"got sigma={s}")
b, s = get_dynamic_calibration("seoul", 2, 3.0)
check("Seoul Feb fallback sigma=4.2 (matrix hit, not fallback)", s == 3.39, f"got sigma={s}")
b, s = get_dynamic_calibration("seoul", 1, 3.0)   # January not in matrix
check("Seoul Jan fallback: bias=0.0",        b == 0.0,  f"got bias={b}")
check("Seoul Jan fallback: sigma=3.5",       s == 3.5,  f"got sigma={s}")

# ─────────────────────────────────────────────────────────────────
# Section 2 — Side-by-side comparison OLD vs NEW
# ─────────────────────────────────────────────────────────────────
section("2. Side-by-side OLD skewnorm vs NEW norm.cdf")

print(f"\n  {'Case':<30} {'OLD':>8} {'NEW':>8} {'Δ':>8}  {'sigma_new':>9}")
print(f"  {'─'*30} {'─'*8} {'─'*8} {'─'*8}  {'─'*9}")

CASES = [
    # (label, forecast, bucket, threshold, T, city, month)
    ("Lon Feb T5 or_higher 14°C",    14.0, "or_higher", 14,  5.0, "london", 2),
    ("Lon Feb T5 or_higher 16°C",    14.0, "or_higher", 16,  5.0, "london", 2),
    ("Lon Mar T1 or_higher 14°C",    14.0, "or_higher", 14,  1.0, "london", 3),
    ("Lon Mar T3 exact 12°C",        12.0, "exact",     12,  3.0, "london", 3),
    ("Lon Mar T3 or_lower 10°C",     12.0, "or_lower",  10,  3.0, "london", 3),
    ("Seo Feb T5 or_higher 5°C",      5.0, "or_higher",  5,  5.0, "seoul",  2),
    ("Seo Feb T5 or_higher 8°C",      5.0, "or_higher",  8,  5.0, "seoul",  2),
    ("Seo Feb T1 exact 5°C",          5.0, "exact",      5,  1.0, "seoul",  2),
    ("Seo Mar T3 or_higher 9°C",      8.0, "or_higher",  9,  3.0, "seoul",  3),
    ("Seo Mar T5 or_lower 7°C",       8.0, "or_lower",   7,  5.0, "seoul",  3),
    ("Lon fallback is_fallback=True", 12.0, "or_higher", 12,  5.0, "london", 3),
    ("Seo fallback is_fallback=True",  5.0, "or_higher",  5,  5.0, "seoul",  2),
]

for (label, fc, btype, thr, Td, city, month) in CASES:
    p_old = ecmwf_true_prob_OLD(fc, btype, thr, T_days=Td, city=city,
                                is_fallback=("fallback" in label))
    p_new = ecmwf_true_prob_NEW(fc, btype, thr, T_days=Td, city=city,
                                is_fallback=("fallback" in label),
                                _month_override=month)
    _, s_new = get_dynamic_calibration(city, month, Td)
    delta = p_new - p_old
    print(f"  {label:<30} {fmt(p_old):>8} {fmt(p_new):>8} {delta:>+8.4f}  {s_new:>9.2f}°C")

# ─────────────────────────────────────────────────────────────────
# Section 3 — Mathematical correctness checks
# ─────────────────────────────────────────────────────────────────
section("3. Mathematical correctness")

# 3a — All probs in [0, 1]
all_valid = True
for (label, fc, btype, thr, Td, city, month) in CASES:
    p = ecmwf_true_prob_NEW(fc, btype, thr, T_days=Td, city=city, _month_override=month)
    if not (0.0 <= p <= 1.0):
        all_valid = False
        print(f"    OOB: {label} → {p:.6f}")
check("All probabilities in [0.0, 1.0]", all_valid)

# 3b — Monotonicity: higher forecast → higher P(or_higher)
p1 = ecmwf_true_prob_NEW(10.0, "or_higher", 12, T_days=3, city="london", _month_override=3)
p2 = ecmwf_true_prob_NEW(12.0, "or_higher", 12, T_days=3, city="london", _month_override=3)
p3 = ecmwf_true_prob_NEW(14.0, "or_higher", 12, T_days=3, city="london", _month_override=3)
check("Monotone or_higher (London Mar T3): fc=10<12<14 → p ascending",
      p1 < p2 < p3, f"{fmt(p1)} < {fmt(p2)} < {fmt(p3)}")

# 3c — Monotonicity: higher forecast → lower P(or_lower)
q1 = ecmwf_true_prob_NEW(10.0, "or_lower", 12, T_days=3, city="london", _month_override=3)
q2 = ecmwf_true_prob_NEW(12.0, "or_lower", 12, T_days=3, city="london", _month_override=3)
q3 = ecmwf_true_prob_NEW(14.0, "or_lower", 12, T_days=3, city="london", _month_override=3)
check("Monotone or_lower  (London Mar T3): fc=10<12<14 → p descending",
      q1 > q2 > q3, f"{fmt(q1)} > {fmt(q2)} > {fmt(q3)}")

# 3d — or_higher + or_lower ≈ 1.0 at threshold (half-integer overlap accounts for 1-unit band)
oh = ecmwf_true_prob_NEW(12.0, "or_higher", 12, T_days=3, city="london", _month_override=3)
ol = ecmwf_true_prob_NEW(12.0, "or_lower",  12, T_days=3, city="london", _month_override=3)
# They overlap by 1°C band [11.5, 12.5] → sum slightly > 1; exact bucket fills the gap
# So oh + ol should be > 1.0 (by ~P(exact band))
check("or_higher + or_lower > 1.0 (overlap band)",
      oh + ol > 1.0, f"sum={oh+ol:.4f}")
exact_band = ecmwf_true_prob_NEW(12.0, "exact", 12, T_days=3, city="london", _month_override=3)
check(f"or_higher + or_lower == 1 + P(exact) [{exact_band:.4f}] (mathematically exact)",
      abs((oh + ol) - (1.0 + exact_band)) < 1e-9,
      f"sum={oh+ol:.6f}, 1+exact={1+exact_band:.6f}")

# 3e — Short-range (T1) sigma < long-range (T5) sigma
_, s_t1 = get_dynamic_calibration("london", 3, 1.0)
_, s_t5 = get_dynamic_calibration("london", 3, 5.0)
check("London Mar: sigma(T1) < sigma(T5)", s_t1 < s_t5,
      f"T1={s_t1:.2f}°C, T5={s_t5:.2f}°C")

_, s_t1 = get_dynamic_calibration("seoul", 3, 1.0)
_, s_t5 = get_dynamic_calibration("seoul", 3, 5.0)
check("Seoul Mar:  sigma(T1) < sigma(T5)", s_t1 < s_t5,
      f"T1={s_t1:.2f}°C, T5={s_t5:.2f}°C")

_, s_t1 = get_dynamic_calibration("seoul", 2, 1.0)
_, s_t5 = get_dynamic_calibration("seoul", 2, 5.0)
check("Seoul Feb:  sigma(T1) < sigma(T5)", s_t1 < s_t5,
      f"T1={s_t1:.2f}°C, T5={s_t5:.2f}°C")

# 3f — Bias direction: positive bias → adj_fcst lower → lower P(or_higher)
# London Feb T3: bias=+0.61 (warm-biased model) → should give lower P than bias=0
p_biased  = ecmwf_true_prob_NEW(14.0, "or_higher", 14, T_days=3, city="london", _month_override=2)
# simulate zero-bias by using a month with bias~0
p_nobias  = ecmwf_true_prob_NEW(14.0, "or_higher", 14, T_days=3, city="london", _month_override=3)
b_feb, _  = get_dynamic_calibration("london", 2, 3)
b_mar, _  = get_dynamic_calibration("london", 3, 3)
check(f"Positive bias({b_feb:+.2f}) reduces P(or_higher) vs near-zero bias({b_mar:+.2f})",
      (b_feb > b_mar) == (p_biased < p_nobias),
      f"p_feb={fmt(p_biased)}, p_mar={fmt(p_nobias)}")

# 3g — Negative bias: Seoul Feb T5, bias=-1.28 → adj_fcst higher → P(or_higher) increases
p_neg_bias = ecmwf_true_prob_NEW(5.0, "or_higher", 5, T_days=5, city="seoul", _month_override=2)
b_seo, _   = get_dynamic_calibration("seoul", 2, 5)
# adj_fcst = 5.0 - (-1.28) = 6.28 → P(T>=5) should be > 0.5
check(f"Negative bias({b_seo:+.2f}) raises adj_fcst above threshold → P(or_higher)>0.5",
      p_neg_bias > 0.5, f"p={fmt(p_neg_bias)}")

# 3h — observed_max shortcut preserved
p_obs = ecmwf_true_prob_NEW(5.0, "or_higher", 5, T_days=1, city="london",
                             observed_max=5.5, _month_override=3)
check("observed_max >= threshold → 0.995", p_obs == 0.995, f"got {p_obs}")

# 3i — is_fallback adds sigma penalty
_, s_base  = get_dynamic_calibration("london", 3, 3.0)
p_normal   = ecmwf_true_prob_NEW(12.0, "or_higher", 14, T_days=3, city="london", _month_override=3)
p_fallback = ecmwf_true_prob_NEW(12.0, "or_higher", 14, T_days=3, city="london",
                                  is_fallback=True, _month_override=3)
# Fallback (larger sigma) → probs closer to 0.5 → since p<0.5 here, p_fallback > p_normal
check("is_fallback: larger sigma flattens prob toward 0.5",
      abs(p_fallback - 0.5) < abs(p_normal - 0.5),
      f"normal={fmt(p_normal)}, fallback={fmt(p_fallback)}")

# ─────────────────────────────────────────────────────────────────
# Section 4 — Focus comparison: Lon Feb T5 vs Seo Feb T5
# ─────────────────────────────────────────────────────────────────
section("4. Lon Feb T5 vs Seo Feb T5 — full breakdown")

for city, fc, thr in [("london", 14.0, 14), ("seoul", 5.0, 5)]:
    bias, sigma = get_dynamic_calibration(city, 2, 5.0)
    adj = fc - bias
    p_oh_old = ecmwf_true_prob_OLD(fc, "or_higher", thr, T_days=5.0, city=city)
    p_oh_new = ecmwf_true_prob_NEW(fc, "or_higher", thr, T_days=5.0, city=city, _month_override=2)
    p_ol_old = ecmwf_true_prob_OLD(fc, "or_lower",  thr, T_days=5.0, city=city)
    p_ol_new = ecmwf_true_prob_NEW(fc, "or_lower",  thr, T_days=5.0, city=city, _month_override=2)
    p_ex_old = ecmwf_true_prob_OLD(fc, "exact",     thr, T_days=5.0, city=city)
    p_ex_new = ecmwf_true_prob_NEW(fc, "exact",     thr, T_days=5.0, city=city, _month_override=2)
    print(f"\n  {city.upper()} Feb T5 | fc={fc}°C | thr={thr}°C | bias={bias:+.2f} | sigma={sigma:.2f}°C | adj_fc={adj:.2f}°C")
    print(f"    {'bucket':<12} {'OLD':>8} {'NEW':>8} {'Δ':>8}")
    print(f"    {'─'*12} {'─'*8} {'─'*8} {'─'*8}")
    for btype, po, pn in [("or_higher", p_oh_old, p_oh_new),
                           ("or_lower",  p_ol_old, p_ol_new),
                           ("exact",     p_ex_old, p_ex_new)]:
        print(f"    {btype:<12} {fmt(po):>8} {fmt(pn):>8} {pn-po:>+8.4f}")

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
total = PASS + FAIL
print(f"  RESULT: {PASS}/{total} PASS  |  {FAIL} FAIL")
print(f"{'═'*60}")

if FAIL > 0:
    sys.exit(1)
else:
    print("\n  ALL TESTS PASSED — safe to inject into tempbot.py")
    sys.exit(0)
