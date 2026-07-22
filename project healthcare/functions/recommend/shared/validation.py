"""Server-side input validation helpers — never trust client-side validation
alone. Every field a route accepts from a request body should pass through
one of these before touching the database.
"""
from .auth import BadRequest

# Clinical plausibility ranges (not diagnostic bounds — just "this cannot be a
# real reading", to catch obviously bad sensor/manual-entry data before it
# pollutes trends/alerts). Values outside these are rejected outright (400);
# values inside but past the *alert* thresholds in config.py are accepted and
# flagged is_anomaly (see ingest_vitals).
VITAL_RANGES: dict[str, tuple[float, float]] = {
    "heartRate": (30, 250),
    "bp_systolic": (60, 250),
    "bp_diastolic": (40, 150),
    "oxygenSaturation": (70, 100),
    "temperature": (34.0, 42.0),
    "glucose": (20, 600),
    "sleep": (0, 24),
    "steps": (0, 100_000),
}

MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 5000


def require_string(value, field: str, max_length: int = MAX_NAME_LENGTH, required: bool = True) -> str | None:
    if value is None or value == "":
        if required:
            raise BadRequest(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise BadRequest(f"{field} must be a string")
    if len(value) > max_length:
        raise BadRequest(f"{field} must be at most {max_length} characters")
    return value


def require_choice(value, field: str, choices) -> str:
    if value not in choices:
        raise BadRequest(f"{field} must be one of {sorted(choices)}")
    return value


def require_number(value, field: str, min_value: float | None = None, max_value: float | None = None) -> float:
    if value is None:
        raise BadRequest(f"{field} is required")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{field} must be a number")
    if min_value is not None and num < min_value:
        raise BadRequest(f"{field} must be >= {min_value}")
    if max_value is not None and num > max_value:
        raise BadRequest(f"{field} must be <= {max_value}")
    return num


def validate_vital_value(vital_type: str, value) -> tuple[float, bool]:
    """Returns (numeric_value, is_anomaly_within_plausible_range). Raises
    BadRequest if the value is outside the plausible physiological range
    entirely (a sensor error / bad input, not a clinical anomaly to record)."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"value must be a number, got: {value!r}")
    bounds = VITAL_RANGES.get(vital_type)
    if bounds is None:
        return num, False
    lo, hi = bounds
    if num < lo or num > hi:
        raise BadRequest(f"{vital_type} value {num:g} is outside the plausible range [{lo:g}, {hi:g}]")
    return num, False
