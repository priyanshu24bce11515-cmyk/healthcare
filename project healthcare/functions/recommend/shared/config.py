"""Central place to read Function App settings (env vars). No secrets hardcoded."""
import os


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# development | staging | production. Controls prod-only hardening — e.g. the
# dev auth escape hatches (x-demo-principal, unsigned bearer) are hard-blocked
# when this is "production", regardless of ALLOW_DEMO_PRINCIPAL.
ENVIRONMENT = _get("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

SQL_CONNECTION_STRING = _get("SQL_CONNECTION_STRING")
LANGUAGE_ENDPOINT = _get("LANGUAGE_ENDPOINT")
LANGUAGE_KEY = _get("LANGUAGE_KEY")
AZURE_OPENAI_ENDPOINT = _get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_CHAT_DEPLOYMENT = _get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
AZURE_OPENAI_KEY = _get("AZURE_OPENAI_KEY")
COMMS_CONNECTION_STRING = _get("COMMS_CONNECTION_STRING")
ACS_SENDER_PHONE = _get("ACS_SENDER_PHONE")  # ACS-provisioned number for SMS; empty = SMS disabled
KEYVAULT_URI = _get("KEYVAULT_URI")
USE_AOAI_RECOMMENDATIONS = _get("USE_AOAI_RECOMMENDATIONS", "false").lower() == "true"

# Local/dev-only escape hatch: accept the unsigned `x-demo-principal` header in
# place of the AD B2C-issued `x-ms-client-principal`. Must stay unset/false in
# every deployed environment (infra/main.bicep does not set it) — anyone who
# can reach the Function App could otherwise forge any role/patientId.
ALLOW_DEMO_PRINCIPAL = _get("ALLOW_DEMO_PRINCIPAL", "false").lower() == "true"

# Vital types that accumulate over a day (a wearable posts many small deltas)
# and so must be SUMmed per calendar day, vs. point-in-time readings that use
# the latest value. Single source of truth — read by the dashboard tile logic
# and the risk-score daily bucketing.
CUMULATIVE_VITAL_TYPES = {"steps"}

# Clinical/wellness thresholds — tune freely, keep every score explainable.
# "warning" bounds are the everyday out-of-range markers; "critical" bounds are
# the emergency thresholds that page a provider. All configurable via app
# settings so thresholds are data-driven, not scattered magic numbers.
HR_ALERT_MAX = _get_float("HR_ALERT_MAX", 120)          # warning
HR_ALERT_MIN = _get_float("HR_ALERT_MIN", 45)           # critical (brady)
HR_CRITICAL_MAX = _get_float("HR_CRITICAL_MAX", 150)    # critical (tachy)
BP_SYS_ALERT_MAX = _get_float("BP_SYS_ALERT_MAX", 140)  # warning
BP_SYS_CRITICAL_MAX = _get_float("BP_SYS_CRITICAL_MAX", 180)  # critical
BP_SYS_ALERT_MIN = _get_float("BP_SYS_ALERT_MIN", 80)   # critical (hypotension)
BP_DIA_ALERT_MAX = _get_float("BP_DIA_ALERT_MAX", 90)   # warning
GLUCOSE_ALERT_MAX = _get_float("GLUCOSE_ALERT_MAX", 180)  # warning
O2_SAT_ALERT_MIN = _get_float("O2_SAT_ALERT_MIN", 92)   # critical
TEMP_ALERT_MAX = _get_float("TEMP_ALERT_MAX", 39.5)     # warning (fever)
TEMP_ALERT_MIN = _get_float("TEMP_ALERT_MIN", 35.0)     # warning (hypothermia)
SLEEP_MIN_HOURS = _get_float("SLEEP_MIN_HOURS", 6)
STEP_GOAL_DEFAULT = _get_float("STEP_GOAL_DEFAULT", 6000)
ADHERENCE_ALERT_MIN = _get_float("ADHERENCE_ALERT_MIN", 80)
# How long the same (patient, alert-kind, vital-type) is suppressed after firing.
ALERT_DEDUPE_HOURS = _get_float("ALERT_DEDUPE_HOURS", 4)

# --- Wearable device integrations (functions/integrations/wearables.py) ---
# Each is entirely optional — an unset client ID means that integration
# no-ops (logs and returns), exactly like LANGUAGE_ENDPOINT/COMMS_CONNECTION_STRING
# already do above. Redirect URIs point back at this Function App's own
# oauth-callback route.
FITBIT_CLIENT_ID = _get("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = _get("FITBIT_CLIENT_SECRET")
FITBIT_REDIRECT_URI = _get("FITBIT_REDIRECT_URI")

# NOTE: the Google Fit REST API has been closed to new developer sign-ups
# since 1 May 2024 and is being fully retired in 2026 (Google's own
# announcement) — this client only works for a project with pre-existing
# API access; Google's replacement (Health Connect) is an on-device Android
# API, not a cloud REST API, and needs a companion mobile app to relay data
# here. Documented, not hidden — see functions/integrations/wearables.py.
GOOGLE_FIT_CLIENT_ID = _get("GOOGLE_FIT_CLIENT_ID")
GOOGLE_FIT_CLIENT_SECRET = _get("GOOGLE_FIT_CLIENT_SECRET")
GOOGLE_FIT_REDIRECT_URI = _get("GOOGLE_FIT_REDIRECT_URI")

# --- EHR / FHIR integration (functions/integrations/ehr_fhir.py) ---
EHR_FHIR_BASE_URL = _get("EHR_FHIR_BASE_URL")
EHR_CLIENT_ID = _get("EHR_CLIENT_ID")
EHR_CLIENT_SECRET = _get("EHR_CLIENT_SECRET")

# Fitbit webhook subscriber verification code (set once, at subscription setup
# time, in the Fitbit app management console) — proves this endpoint to
# Fitbit before it starts sending real notifications. Not a secret in the
# same sense as CLIENT_SECRET; just a shared setup token.
FITBIT_VERIFICATION_CODE = _get("FITBIT_VERIFICATION_CODE")

# Application-layer encryption key (Fernet/AES-128-CBC+HMAC) for third-party
# OAuth tokens this app stores at rest (DeviceAuthorizations table) — a real
# deployment sources this from Key Vault (secret name "encryption-key-phi"),
# never hardcodes it. Unset in local/demo runs; shared/crypto.py no-ops
# (stores nothing) rather than silently storing plaintext when it's missing.
PHI_ENCRYPTION_KEY = _get("PHI_ENCRYPTION_KEY")

WELLNESS_DISCLAIMER = (
    "General wellness suggestion, not medical advice. This is a lifestyle "
    "pattern indicator, not a clinical diagnosis. Consult a healthcare "
    "professional for medical decisions."
)
