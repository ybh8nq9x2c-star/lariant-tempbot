#!/usr/bin/env python3
"""inject_phase2a.py — Apply Phase 2a pricing changes to tempbot_v427_stable_20260313.py"""

import sys

path = '/home/ubuntu/polymarket-temp-bot/tempbot_v427_stable_20260313.py'
src  = open(path, encoding='utf-8').read()
original_len = len(src)

changes_applied = []

# ── CHANGE 1: remove skewnorm from scipy import ──────────────────────────────
old1 = 'from scipy.stats  import norm, skewnorm'
new1 = 'from scipy.stats  import norm              # [v4.3-Phase2a] skewnorm dropped'
assert old1 in src, 'ABORT: CHANGE 1 anchor not found'
src = src.replace(old1, new1, 1)
changes_applied.append('1: scipy import — skewnorm removed')

# ── CHANGE 2: comment out CITY_SKEW_ALPHA ────────────────────────────────────
old2 = 'CITY_SKEW_ALPHA      = {"seoul": -1.5, "london": -1.5}  # [FEAT1] negative = left-skewed max temps'
new2 = (
    '# [v4.3-Phase2a] CITY_SKEW_ALPHA dropped -- replaced by bias in _CALIB_MATRIX\n'
    '# CITY_SKEW_ALPHA = {"seoul": -1.5, "london": -1.5}'
)
assert old2 in src, 'ABORT: CHANGE 2 anchor not found'
src = src.replace(old2, new2, 1)
changes_applied.append('2: CITY_SKEW_ALPHA commented out')

# ── CHANGE 3: inject _CALIB_MATRIX + get_dynamic_calibration() ───────────────
# Anchor = the line we just wrote in change 2 + inject below it
old3 = (
    '# [v4.3-Phase2a] CITY_SKEW_ALPHA dropped -- replaced by bias in _CALIB_MATRIX\n'
    '# CITY_SKEW_ALPHA = {"seoul": -1.5, "london": -1.5}'
)
new3 = (
    '# [v4.3-Phase2a] CITY_SKEW_ALPHA dropped -- replaced by bias in _CALIB_MATRIX\n'
    '# CITY_SKEW_ALPHA = {"seoul": -1.5, "london": -1.5}\n'
    '\n'
    '# [v4.3-Phase2a] Dynamic calibration matrix: (city, month) -> {t_day: (bias, sigma)}\n'
    '# Source: empirical Feb/Mar data matrix. Nearest T-day node lookup (1, 3, 5).\n'
    '_CALIB_MATRIX: dict = {\n'
    '    ("london", 2): {1: ( 0.14, 0.85), 3: ( 0.61, 1.93), 5: (-0.43, 3.24)},\n'
    '    ("london", 3): {1: ( 0.08, 0.67), 3: (-0.06, 1.96), 5: ( 0.31, 3.23)},\n'
    '    ("seoul",  2): {1: (-0.18, 1.37), 3: (-0.39, 3.39), 5: (-1.28, 5.89)},\n'
    '    ("seoul",  3): {1: ( 0.03, 0.85), 3: (-0.13, 1.78), 5: ( 0.69, 3.08)},\n'
    '}\n'
    '_SIGMA_FALLBACK: dict = {\n'
    '    ("london", None): 2.6,\n'
    '    ("seoul",  2):    4.2,\n'
    '    ("seoul",  3):    2.8,\n'
    '    ("seoul",  None): 3.5,\n'
    '}\n'
    '_TDAYS_NODES = [1, 3, 5]\n'
    '\n'
    'def get_dynamic_calibration(city: str, month: int, tdays: float):\n'
    '    """[v4.3-Phase2a] Return (bias, sigma) from empirical calibration matrix.\n'
    '    Nearest T-day node (1/3/5). Falls back to (bias=0.0, fallback_sigma) if not in matrix."""\n'
    '    nearest_t = min(_TDAYS_NODES, key=lambda t: abs(tdays - t))\n'
    '    row = _CALIB_MATRIX.get((city.lower(), month))\n'
    '    if row is not None:\n'
    '        return row[nearest_t]\n'
    '    sigma_fb = _SIGMA_FALLBACK.get(\n'
    '        (city.lower(), month),\n'
    '        _SIGMA_FALLBACK.get((city.lower(), None), 2.6),\n'
    '    )\n'
    '    return (0.0, sigma_fb)\n'
)
assert old3 in src, 'ABORT: CHANGE 3 anchor not found'
src = src.replace(old3, new3, 1)
changes_applied.append('3: _CALIB_MATRIX + get_dynamic_calibration injected')

# ── CHANGE 4: replace ecmwf_true_prob body ───────────────────────────────────
old4 = (
    'def ecmwf_true_prob(forecast_temp: float, bucket_type: str,\n'
    '                    threshold: float, T_days: float = None,\n'
    '                    city: str = "london", is_fallback: bool = False,\n'
    '                    observed_max: "float | None" = None) -> float:  # [v4.25-eglc]\n'
    '    """\n'
    '    [v4.2-C2] sigma per città: CITY_SIGMA_BASE[city] + 0.20 * T_days\n'
    '    RMSE ECMWF reale: Seoul 2.8°C, London 2.2°C base.\n'
    '    Era: sigma=1.8 fisso → edge sopravvalutato 15-25% su T<4d.\n'
    '\n'
    '    [v4.14-M1] Parametro is_fallback: quando il forecast proviene dal\n'
    '    fallback climatologico (medie mensili storiche, non previsione numerica)\n'
    '    aggiungiamo una penality sigma esplicita di +1.5°C oltre al _T_adj=5.0\n'
    '    già applicato dai caller. Questo garantisce che la probabilità calcolata\n'
    '    sia sistematicamente più piatta (verso 0.5) quando non abbiamo un\n'
    '    forecast reale, riducendo il kelly_size proporzionalmente via edge minore.\n'
    '    """\n'
    '    base_sigma = CITY_SIGMA_BASE.get(city, 2.5)\n'
    '    # [v4.24-fix-sigma-scaling] Scala sub-lineare: sigma cresce come sqrt(T/7)\n'
    '    # BUG: sigma lineare (base + 0.20*T) sovrastimava di 4x a T<1d\n'
    '    # → a T=6h: bot calcolava sigma=2.3°C vs reale ~0.6°C → probabilità troppo piatte\n'
    '    # → edge invertito: bot vendeva contratti quasi-certi come se fossero incerti\n'
    '    # FIX: sigma = base * sqrt(T/7) calibrata su RMSE ECMWF (7d = baseline)\n'
    '    _T_eff = T_days if T_days is not None else 7.0\n'
    '    sigma = base_sigma * math.sqrt(max(_T_eff, 0.04) / 7.0)\n'
    '    # [v4.14-M1] Fallback climatologico: sigma aggiuntiva esplicita\n'
    '    # La climatologia ha incertezza ~2-3°C in più rispetto a ECMWF 5d.\n'
    '    if is_fallback:\n'
    '        sigma += 1.5\n'
    '        log.debug(f"ecmwf_true_prob FALLBACK: sigma aumentata a {sigma:.2f}°C "\n'
    '                  f"(city={city}, T={T_days:.1f}d)")\n'
    '    # [FEAT1] skew-normal: alpha<0 = left-skewed (hard ceiling on max temps)\n'
    '    alpha = CITY_SKEW_ALPHA.get(city, -1.5)\n'
    '    if   bucket_type == "exact":\n'
    '        return (skewnorm.cdf(threshold+0.5, alpha, loc=forecast_temp, scale=sigma)\n'
    '                - skewnorm.cdf(threshold-0.5, alpha, loc=forecast_temp, scale=sigma))\n'
    '    elif bucket_type == "or_higher":\n'
    '        # [v4.6-audit5] "X or higher" = P(T >= X) → include X with -0.5\n'
    '        # [v4.25-eglc] If observed intraday max already >= threshold, outcome is certain.\n'
    '        # Only for or_higher: mathematically correct (monotone CDF property).\n'
    '        # exact/or_lower left untouched — semantics differ.\n'
    '        if observed_max is not None and observed_max >= threshold:\n'
    '            return 0.995  # ~1.0 but never exact 1 to avoid division issues downstream\n'
    '        return 1.0 - skewnorm.cdf(threshold - 0.5, alpha, loc=forecast_temp, scale=sigma)\n'
    '    elif bucket_type == "or_lower":\n'
    '        # [v4.6-audit5] "X or lower/below" = P(T <= X) → include X with +0.5\n'
    '        return skewnorm.cdf(threshold + 0.5, alpha, loc=forecast_temp, scale=sigma)\n'
    '    return 0.5'
)

new4 = (
    'def ecmwf_true_prob(forecast_temp: float, bucket_type: str,\n'
    '                    threshold: float, T_days: float = None,\n'
    '                    city: str = "london", is_fallback: bool = False,\n'
    '                    observed_max: "float | None" = None) -> float:  # [v4.25-eglc]\n'
    '    """\n'
    '    [v4.3-Phase2a] Dynamic bias/sigma normal CDF pricing.\n'
    '    Replaces static skewnorm + CITY_SIGMA_BASE + CITY_SKEW_ALPHA.\n'
    '\n'
    '    Derivation:\n'
    '      1. Derive market month from today + T_days\n'
    '      2. Look up (bias, sigma) via get_dynamic_calibration (nearest T-node 1/3/5)\n'
    '      3. adj_fcst = forecast_temp - bias  (removes systematic model bias)\n'
    '      4. P = norm.cdf(threshold +/- 0.5, loc=adj_fcst, scale=sigma)\n'
    '\n'
    '    Preserved: is_fallback +1.5 sigma [v4.14-M1], observed_max shortcut [v4.25-eglc],\n'
    '               +/-0.5 half-unit boundary convention [v4.6-audit5].\n'
    '    """\n'
    '    _T_eff = T_days if T_days is not None else 7.0\n'
    '    market_dt = datetime.now(timezone.utc) + timedelta(days=_T_eff)\n'
    '    month = market_dt.month\n'
    '    bias, sigma = get_dynamic_calibration(city, month, _T_eff)\n'
    '    # [v4.14-M1] Climatological fallback: explicit sigma penalty\n'
    '    if is_fallback:\n'
    '        sigma += 1.5\n'
    '        log.debug(f"ecmwf_true_prob FALLBACK: sigma={sigma:.2f}C (city={city}, T={_T_eff:.1f}d)")\n'
    '    # Bias-corrected forecast\n'
    '    adj_fcst = forecast_temp - bias\n'
    '    log.debug(f"ecmwf_true_prob: city={city} month={month} T={_T_eff:.1f}d "\n'
    '              f"bias={bias:+.2f} sigma={sigma:.2f} adj_fc={adj_fcst:.2f} thr={threshold}")\n'
    '    if   bucket_type == "exact":\n'
    '        return (norm.cdf(threshold + 0.5, loc=adj_fcst, scale=sigma)\n'
    '                - norm.cdf(threshold - 0.5, loc=adj_fcst, scale=sigma))\n'
    '    elif bucket_type == "or_higher":\n'
    '        # [v4.6-audit5] "X or higher" = P(T >= X) → include X with -0.5\n'
    '        # [v4.25-eglc] Observed intraday max certainty shortcut (monotone CDF).\n'
    '        if observed_max is not None and observed_max >= threshold:\n'
    '            return 0.995  # ~1.0 but never exact 1 to avoid division issues downstream\n'
    '        return 1.0 - norm.cdf(threshold - 0.5, loc=adj_fcst, scale=sigma)\n'
    '    elif bucket_type == "or_lower":\n'
    '        # [v4.6-audit5] "X or lower/below" = P(T <= X) → include X with +0.5\n'
    '        return norm.cdf(threshold + 0.5, loc=adj_fcst, scale=sigma)\n'
    '    return 0.5'
)
assert old4 in src, 'ABORT: CHANGE 4 anchor not found'
src = src.replace(old4, new4, 1)
changes_applied.append('4: ecmwf_true_prob body replaced with norm.cdf + dynamic calibration')

# ── CHANGE 5: update stale debug print referencing CITY_SIGMA_BASE ───────────
old5 = 'f"sigma={CITY_SIGMA_BASE.get(city,2.5)+0.20*T:.2f}"'
# check exact form from grep output
if old5 not in src:
    old5 = 'f"\u03c3={CITY_SIGMA_BASE.get(city,2.5)+0.20*T:.2f}"'
if old5 not in src:
    old5 = 'f"σ={CITY_SIGMA_BASE.get(city,2.5)+0.20*T:.2f}"'
assert old5 in src, f'ABORT: CHANGE 5 anchor not found (tried 3 variants)'
new5 = 'f"sigma={get_dynamic_calibration(city, (datetime.now(timezone.utc)+timedelta(days=T)).month, T)[1]:.2f}"'
src = src.replace(old5, new5, 1)
changes_applied.append('5: debug print sigma updated to use get_dynamic_calibration')

# ── Verify no remaining skewnorm / CITY_SKEW_ALPHA usages ────────────────────
remaining_skewnorm = src.count('skewnorm.cdf')
remaining_alpha    = src.count('CITY_SKEW_ALPHA.get')
assert remaining_skewnorm == 0, f'ABORT: {remaining_skewnorm} skewnorm.cdf still in code'
assert remaining_alpha    == 0, f'ABORT: {remaining_alpha} CITY_SKEW_ALPHA.get still in code'

# ── Write ─────────────────────────────────────────────────────────────────────
with open(path, 'w', encoding='utf-8') as f:
    f.write(src)

print(f'Wrote {len(src)} bytes (was {original_len})')
print(f'Changes applied ({len(changes_applied)}):')
for c in changes_applied:
    print(f'  OK  {c}')
print(f'skewnorm.cdf remaining: {remaining_skewnorm}')
print(f'CITY_SKEW_ALPHA.get remaining: {remaining_alpha}')
print('INJECTION COMPLETE')
