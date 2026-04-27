#!/usr/bin/env python3
"""
METAR T-Group Parser

Extracts T-group temperature data from METAR remarks section.
T-group format: T[01]\\d{3}[01]\\d{3}
Example: T02480122 = temp +24.8C, dewpoint +12.2C
         T10031022 → temp -3.1°C, dewpoint +12.2°C

NOTE: T-groups are primarily found in US ASOS METARs.
UK/European stations (EGLC, EGLL) typically do NOT include T-group remarks.
"""

import re
from typing import Dict, Any, Optional

# Standard ICAO T-group pattern: T + sign(0=pos,1=neg) + 3 digits temp + sign + 3 digits dew
TG_PATTERN = re.compile(r'\bT([01])(\d{3})([01])(\d{3})\b')


def parse_tgroup(metar_raw: str) -> Dict[str, Any]:
    """
    Extract T-group temperature from a raw METAR string.
    
    Args:
        metar_raw: Full raw METAR string
    
    Returns:
        dict with:
            t_group_present (bool): Whether T-group was found
            t_group_temp_c (float|None): Temperature in °C (tenths precision)
            t_group_dewpoint_c (float|None): Dewpoint in °C (tenths precision)
            t_group_raw (str|None): The matched T-group string
            parse_error (str|None): Error message if parsing failed
    """
    result = {
        't_group_present': False,
        't_group_temp_c': None,
        't_group_dewpoint_c': None,
        't_group_raw': None,
        'parse_error': None,
    }
    
    if not metar_raw or not isinstance(metar_raw, str):
        result['parse_error'] = 'empty or non-string input'
        return result
    
    match = TG_PATTERN.search(metar_raw)
    if not match:
        return result  # No T-group present - normal for UK/EU stations
    
    result['t_group_present'] = True
    result['t_group_raw'] = match.group(0)
    
    try:
        temp_sign = -1 if match.group(1) == '1' else 1
        temp_val = int(match.group(2)) / 10.0
        result['t_group_temp_c'] = round(temp_sign * temp_val, 1)
        
        dew_sign = -1 if match.group(3) == '1' else 1
        dew_val = int(match.group(4)) / 10.0
        result['t_group_dewpoint_c'] = round(dew_sign * dew_val, 1)
    except (ValueError, IndexError) as e:
        result['parse_error'] = str(e)
    
    return result


def batch_parse_tgroup(metar_strings) -> Dict[str, Any]:
    """
    Parse T-groups from a collection of METAR strings.
    
    Returns aggregate statistics.
    """
    total = 0
    found = 0
    errors = 0
    diffs = []
    
    for raw in metar_strings:
        total += 1
        result = parse_tgroup(raw)
        if result['parse_error']:
            errors += 1
        if result['t_group_present']:
            found += 1
    
    return {
        'total_metars': total,
        't_group_present': found,
        't_group_rate': round(found / total * 100, 2) if total > 0 else 0,
        'parse_errors': errors,
        'has_tgroup_data': found > 0,
    }


if __name__ == '__main__':
    # Quick test
    test_cases = [
        'KJFK 010051Z 32015G25KT 10SM FEW050 BKN100 22/15 A2992 RMK T02220150',
        'EGLC 010020Z AUTO 24014KT 200V260 9999 BKN024 BKN040 09/05 Q0996',
        'KORD 010051Z 27015G20KT 10SM CLR 18/12 A3010 RMK T01780122',
        'EGLL 010020Z AUTO 26010KT 9999 SCT035 12/08 Q1015',
        'KSEA 010053Z 16008KT 10SM FEW008 BKN030 OVC070 10/07 A3002 RMK SLP170 T01000072',
    ]
    for m in test_cases:
        r = parse_tgroup(m)
        print(f"{'FOUND' if r['t_group_present'] else 'NONE':5s} | {r.get('t_group_temp_c','N/A'):>6} | {m[:60]}")
