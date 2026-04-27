import os, json, logging

log = logging.getLogger("tempbot")

# Feature flags
def _cfg(section, key, default):
    return os.getenv(key, str(default))

EGLC_V2_ENABLED = os.getenv("EGLC_V2_ENABLED", "false").lower() == "true"
EGLC_V2_SHADOW = os.getenv("EGLC_V2_SHADOW", "true").lower() == "true"
P0_VALIDATION_STATUS = os.getenv("P0_VALIDATION_STATUS", "PENDING")  # PENDING | PASS | FAIL

# Promotion modes
EGLC_V2_MODE = os.getenv("EGLC_V2_MODE", "shadow")  # shadow | paper | tiny_live | live
HUMAN_PROMOTION_APPROVED = os.getenv("HUMAN_PROMOTION_APPROVED", "false").lower() == "true"
EGLC_V2_MAX_POSITION_USD_TINY = float(os.getenv("EGLC_V2_MAX_POSITION_USD_TINY", "3"))
EGLC_V2_MAX_DAILY_RISK_USD = float(os.getenv("EGLC_V2_MAX_DAILY_RISK_USD", "15"))
EGLC_V2_MAX_CONCURRENT_EXPOSURE = int(os.getenv("EGLC_V2_MAX_CONCURRENT_EXPOSURE", "3"))
EGLC_V2_REQUIRE_WIND_FOR_REGIME = os.getenv("EGLC_V2_REQUIRE_WIND_FOR_REGIME", "true").lower() == "true"
EGLC_V2_COOLDOWN_AFTER_LOSS_MIN = int(os.getenv("EGLC_V2_COOLDOWN_AFTER_LOSS_MIN", "60"))
EGLC_V2_MAX_ERRORS_PER_HOUR = int(os.getenv("EGLC_V2_MAX_ERRORS_PER_HOUR", "5"))

# Real-data validation flags
REAL_DATA_VALIDATION_ENABLED = os.getenv("REAL_DATA_VALIDATION_ENABLED", "true").lower() == "true"
VALIDATION_DATA_DIR = os.getenv("VALIDATION_DATA_DIR", "/a0/usr/projects/tempbot")

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


# === Promotion mode safety invariants ===

VALID_MODES = ("shadow", "paper", "tiny_live", "live")


def get_effective_mode() -> str:
    """Returns the effective V2 mode with safety fallbacks."""
    mode = EGLC_V2_MODE
    if mode not in VALID_MODES:
        log.warning(f"Invalid EGLC_V2_MODE={mode}, falling back to shadow")
        return "shadow"
    if mode in ("tiny_live", "live") and not is_p0_passed():
        log.warning(f"P0 not passed, cannot use {mode}, falling back to paper")
        return "paper"
    if mode in ("tiny_live", "live") and not HUMAN_PROMOTION_APPROVED:
        log.warning(f"No human promotion approval, cannot use {mode}, falling back to paper")
        return "paper"
    if mode == "live":
        log.warning("LIVE mode not yet supported, falling back to tiny_live")
        return "tiny_live"
    return mode


def can_execute_v2() -> bool:
    """Whether V2 can generate real orders."""
    return get_effective_mode() == "tiny_live"


def can_paper_trade_v2() -> bool:
    """Whether V2 can generate paper trades."""
    return get_effective_mode() in ("paper", "tiny_live")
