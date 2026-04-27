import os, json, logging

log = logging.getLogger("tempbot")

# Feature flags
def _cfg(section, key, default):
    return os.getenv(key, str(default))

EGLC_V2_ENABLED = os.getenv("EGLC_V2_ENABLED", "false").lower() == "true"
EGLC_V2_SHADOW = os.getenv("EGLC_V2_SHADOW", "true").lower() == "true"
P0_VALIDATION_STATUS = os.getenv("P0_VALIDATION_STATUS", "PENDING")  # PENDING | PASS | FAIL

# Regime deltas from P0 validation (°C)
REGIME_DELTAS = {
    "E_SE": -0.76,
    "S_SW": +0.25,
    "W_NW": +0.43,
    "N_NE": 0.00,
}

# Hot day adjustments
HOT_DAY_ADJ = {">=30": -0.45, ">=28": -0.30}
REGIME_CAP = 1.0  # max adjustment magnitude °C
BOUNDARY_PROXIMITY = 1.0  # °C from edge to apply regime
MIN_CONFIDENCE = "MEDIUM"  # LOW | MEDIUM | HIGH
MIN_EDGE = 0.05  # 5%
ORACLE_SIGMA = 0.7  # oracle simulation spread
MIN_REGIME_EFFECT = 0.3  # minimum adjustment to consider
WIND_SPREAD_LIMIT = 90  # degrees - defer if wider
MODEL_SPREAD_LIMIT = 3.0  # °C - defer if larger

# Wind direction ranges
WIND_RANGES = {
    "E_SE": (60, 150),
    "S_SW": (150, 270),
    "W_NW": None,  # special: 270-360 OR 0-30
    "N_NE": (30, 60),
}

# BST period (approximate)
BST_START_MONTH = 3  # March
BST_END_MONTH = 10   # October

CONFIDENCE_LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

# P0 gate file path
P0_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "p0_validation_status.json")

def is_p0_passed() -> bool:
    """Check if P0 validation has passed."""
    if P0_VALIDATION_STATUS == "PASS":
        return True
    try:
        with open(P0_STATUS_FILE) as f:
            data = json.load(f)
            return data.get("status") == "PASS"
    except (FileNotFoundError, json.JSONDecodeError):
        return False

def can_trade_v2_live() -> bool:
    """V2 can only trade live if enabled AND P0 passed."""
    return EGLC_V2_ENABLED and is_p0_passed()
