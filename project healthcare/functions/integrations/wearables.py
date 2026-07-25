"""Wearable device integrations: Fitbit, Google Fit, and any generic FHIR R4
device endpoint. Real OAuth2 flows and real vendor API shapes (verified
against each vendor's current published docs where noted below) — but none
of this has been exercised against a live account in this session, since
that requires a registered developer app + a real user's OAuth consent,
neither of which exist here. Each client no-ops cleanly (logs and returns
None/[]) when its config isn't set, exactly like the AI Language/ACS/AOAI
integrations already in this codebase — it never pretends to have data it
doesn't have.

*** Google Fit status, read before wiring this up ***
The Google Fit REST API has been closed to NEW developer sign-ups since
1 May 2024 and is being fully retired in 2026 (Google's own announcement).
GoogleFitClient below is real, correct code — but it only works for a Google
Cloud project that already had Fitness API access before that cutoff. For a
brand-new integration, Google's own recommended replacement is Health
Connect — an on-device Android API, not a cloud REST API, which needs a
companion mobile app to relay data to this backend (a materially different
architecture, out of scope here). Don't discover this the hard way at
integration time — decide up front whether Fitbit + generic FHIR devices are
sufficient, or whether a Health Connect companion app is worth building.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import date, datetime, timedelta, timezone

import requests

from shared import config, crypto, db
from shared.alerts import rules as alert_rules
from shared.validation import validate_vital_value

_REQUEST_TIMEOUT_SECONDS = 10
_MAX_RETRIES = 3
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response | None:
    """Exponential backoff (0.5s, 1s, 2s) on timeout / 429 / 5xx. Returns None
    (never raises) if all attempts are exhausted — callers must treat a sync
    failure as "try again next cycle", not crash the caller."""
    kwargs.setdefault("timeout", _REQUEST_TIMEOUT_SECONDS)
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in _RETRYABLE_STATUS:
                logging.warning("wearables: %s %s returned %d (attempt %d/%d)", method, url, resp.status_code, attempt + 1, _MAX_RETRIES)
                time.sleep(0.5 * (2**attempt))
                continue
            return resp
        except requests.exceptions.RequestException as exc:
            logging.warning("wearables: %s %s failed: %s (attempt %d/%d)", method, url, exc, attempt + 1, _MAX_RETRIES)
            time.sleep(0.5 * (2**attempt))
    logging.error("wearables: %s %s exhausted %d retries", method, url, _MAX_RETRIES)
    return None


def verify_fitbit_signature(client_secret: str, request_body: bytes, signature_header: str | None) -> bool:
    """Fitbit signs webhook deliveries as base64(HMAC-SHA1(client_secret + '&', body)).
    Must be verified before trusting a webhook payload — anyone can POST to a
    public HTTP endpoint, only a valid signature proves it came from Fitbit."""
    if not signature_header or not client_secret:
        return False
    key = f"{client_secret}&".encode()
    expected = base64.b64encode(hmac.new(key, request_body, hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature_header)


def _pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) for OAuth2 Authorization Code + PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Fitbit — https://dev.fitbit.com/build/reference/web-api/
# OAuth2 endpoints and scope names confirmed against Fitbit's current docs.
# ---------------------------------------------------------------------------

class FitbitClient:
    AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"
    TOKEN_URL = "https://api.fitbit.com/oauth2/token"
    API_BASE = "https://api.fitbit.com"
    SCOPES = "activity heartrate sleep profile"

    def __init__(self):
        self.client_id = config.FITBIT_CLIENT_ID
        self.client_secret = config.FITBIT_CLIENT_SECRET
        self.redirect_uri = config.FITBIT_REDIRECT_URI

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def generate_auth_url(self, state: str) -> tuple[str, str] | None:
        """Returns (auth_url, code_verifier) — the caller must persist
        code_verifier (e.g. in a short-lived server-side session keyed by
        `state`) to pass back into exchange_code()."""
        if not self.is_configured():
            logging.info("[fitbit:noop] not configured")
            return None
        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.AUTHORIZE_URL}?{requests.compat.urlencode(params)}"
        return url, verifier

    def exchange_code(self, code: str, code_verifier: str) -> dict | None:
        if not self.is_configured():
            return None
        resp = _request_with_retry(
            "POST",
            self.TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
        )
        if resp is None or not resp.ok:
            logging.warning("fitbit: token exchange failed: %s", resp.text if resp is not None else "no response")
            return None
        return resp.json()  # {access_token, refresh_token, expires_in, user_id, ...}

    def refresh_access_token(self, refresh_token: str) -> dict | None:
        if not self.is_configured():
            return None
        resp = _request_with_retry(
            "POST",
            self.TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        if resp is None or not resp.ok:
            logging.warning("fitbit: token refresh failed")
            return None
        return resp.json()

    def _get(self, access_token: str, path: str) -> dict | None:
        resp = _request_with_retry(
            "GET", f"{self.API_BASE}{path}", headers={"Authorization": f"Bearer {access_token}"}
        )
        if resp is None or not resp.ok:
            return None
        return resp.json()

    def fetch_heart_rate(self, access_token: str, for_date: date) -> list[dict]:
        """Daily resting-heart-rate summary (not the 1-second intraday feed —
        this app models one point-in-time vital, not a raw waveform)."""
        data = self._get(access_token, f"/1/user/-/activities/heart/date/{for_date.isoformat()}/1d.json")
        if not data:
            return []
        readings = []
        for day in data.get("activities-heart", []):
            resting = day.get("value", {}).get("restingHeartRate")
            if resting is not None:
                readings.append({"type": "heartRate", "value": float(resting), "unit": "bpm", "recordedAt": for_date, "source": "fitbit"})
        return readings

    def fetch_steps(self, access_token: str, for_date: date) -> list[dict]:
        data = self._get(access_token, f"/1/user/-/activities/steps/date/{for_date.isoformat()}/1d.json")
        if not data:
            return []
        readings = []
        for day in data.get("activities-steps", []):
            if day.get("value") is not None:
                readings.append({"type": "steps", "value": float(day["value"]), "unit": "count", "recordedAt": for_date, "source": "fitbit"})
        return readings

    def fetch_sleep(self, access_token: str, for_date: date) -> list[dict]:
        # Fitbit's Sleep API is versioned separately (v1.2).
        data = self._get(access_token, f"/1.2/user/-/sleep/date/{for_date.isoformat()}.json")
        if not data:
            return []
        readings = []
        for entry in data.get("sleep", []):
            minutes_asleep = entry.get("minutesAsleep")
            if minutes_asleep is not None:
                readings.append({"type": "sleep", "value": round(minutes_asleep / 60.0, 2), "unit": "hours", "recordedAt": for_date, "source": "fitbit"})
        return readings

    def register_webhook(self, access_token: str, subscriber_id: str) -> bool:
        """Fitbit's Subscriptions API — registers this app to receive push
        notifications when new data is available (the app must then call
        fetch_* to actually retrieve it; Fitbit webhooks are a "come get it"
        ping, not a payload push)."""
        resp = _request_with_retry(
            "POST",
            f"{self.API_BASE}/1/user/-/apiSubscriptions/{subscriber_id}.json",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return resp is not None and resp.ok


# ---------------------------------------------------------------------------
# Google Fit — see module docstring: closed to new sign-ups since 2024-05-01.
# OAuth2 endpoints below are Google's standard (stable) endpoints, shared
# across all Google APIs; the Fitness-specific aggregate endpoint is real and
# confirmed against current docs, but only reachable with pre-existing access.
# ---------------------------------------------------------------------------

class GoogleFitClient:
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_BASE = "https://www.googleapis.com/fitness/v1"
    SCOPES = (
        "https://www.googleapis.com/auth/fitness.heart_rate.read "
        "https://www.googleapis.com/auth/fitness.activity.read "
        "https://www.googleapis.com/auth/fitness.body.read"
    )
    _DATA_TYPES = {
        "heartRate": "com.google.heart_rate.bpm",
        "steps": "com.google.step_count.delta",
        "weight": "com.google.weight",
    }

    def __init__(self):
        self.client_id = config.GOOGLE_FIT_CLIENT_ID
        self.client_secret = config.GOOGLE_FIT_CLIENT_SECRET
        self.redirect_uri = config.GOOGLE_FIT_REDIRECT_URI

    def is_configured(self) -> bool:
        if self.client_id and self.client_secret and self.redirect_uri:
            return True
        logging.info("[google_fit:noop] not configured (also: closed to new sign-ups since 2024-05-01 — see module docstring)")
        return False

    def generate_auth_url(self, state: str) -> str | None:
        if not self.is_configured():
            return None
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self.AUTHORIZE_URL}?{requests.compat.urlencode(params)}"

    def exchange_code(self, code: str) -> dict | None:
        if not self.is_configured():
            return None
        resp = _request_with_retry(
            "POST",
            self.TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
        )
        if resp is None or not resp.ok:
            return None
        return resp.json()

    def refresh_access_token(self, refresh_token: str) -> dict | None:
        if not self.is_configured():
            return None
        resp = _request_with_retry(
            "POST",
            self.TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            },
        )
        if resp is None or not resp.ok:
            return None
        return resp.json()

    def _aggregate(self, access_token: str, data_type_name: str, start: datetime, end: datetime) -> dict | None:
        body = {
            "aggregateBy": [{"dataTypeName": data_type_name}],
            "bucketByTime": {"durationMillis": 86400000},  # daily buckets
            "startTimeMillis": int(start.timestamp() * 1000),
            "endTimeMillis": int(end.timestamp() * 1000),
        }
        resp = _request_with_retry(
            "POST",
            f"{self.API_BASE}/users/me/dataset:aggregate",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=body,
        )
        if resp is None or not resp.ok:
            return None
        return resp.json()

    def _extract_points(self, agg: dict | None, field: str) -> list[float]:
        if not agg:
            return []
        values = []
        for bucket in agg.get("bucket", []):
            for dataset in bucket.get("dataset", []):
                for point in dataset.get("point", []):
                    for v in point.get("value", []):
                        if field in v:
                            values.append(v[field])
        return values

    def fetch_heart_rate(self, access_token: str, for_date: date) -> list[dict]:
        start = datetime.combine(for_date, datetime.min.time(), tzinfo=timezone.utc)
        agg = self._aggregate(access_token, self._DATA_TYPES["heartRate"], start, start + timedelta(days=1))
        values = self._extract_points(agg, "fpVal")
        if not values:
            return []
        avg = sum(values) / len(values)
        return [{"type": "heartRate", "value": round(avg, 1), "unit": "bpm", "recordedAt": for_date, "source": "google_fit"}]

    def fetch_steps(self, access_token: str, for_date: date) -> list[dict]:
        start = datetime.combine(for_date, datetime.min.time(), tzinfo=timezone.utc)
        agg = self._aggregate(access_token, self._DATA_TYPES["steps"], start, start + timedelta(days=1))
        values = self._extract_points(agg, "intVal")
        if not values:
            return []
        return [{"type": "steps", "value": float(sum(values)), "unit": "count", "recordedAt": for_date, "source": "google_fit"}]

    def fetch_weight(self, access_token: str, for_date: date) -> list[dict]:
        start = datetime.combine(for_date, datetime.min.time(), tzinfo=timezone.utc)
        agg = self._aggregate(access_token, self._DATA_TYPES["weight"], start, start + timedelta(days=1))
        values = self._extract_points(agg, "fpVal")
        if not values:
            return []
        return [{"type": "weight", "value": round(values[-1], 1), "unit": "kg", "recordedAt": for_date, "source": "google_fit"}]


# ---------------------------------------------------------------------------
# Generic FHIR R4 device — for any wearable/monitor vendor that exposes a
# standard FHIR Observation API rather than a proprietary one. LOINC codes
# are a stable open standard (not vendor-specific, so not subject to the
# same "verify against current docs" caveat as the vendor APIs above).
# ---------------------------------------------------------------------------

class GenericFHIRDeviceClient:
    LOINC_HEART_RATE = "8867-4"
    LOINC_OXYGEN_SATURATION = "59408-5"
    LOINC_BP_SYSTOLIC = "8480-6"
    LOINC_BP_DIASTOLIC = "8462-4"
    LOINC_BODY_TEMPERATURE = "8310-5"

    _LOINC_TO_VITAL_TYPE = {
        LOINC_HEART_RATE: "heartRate",
        LOINC_OXYGEN_SATURATION: "oxygenSaturation",
        LOINC_BP_SYSTOLIC: "bp_systolic",
        LOINC_BP_DIASTOLIC: "bp_diastolic",
        LOINC_BODY_TEMPERATURE: "temperature",
    }

    def __init__(self, fhir_base_url: str):
        self.base_url = fhir_base_url.rstrip("/")

    def fetch_observations(self, access_token: str, patient_fhir_id: str, loinc_code: str, start: datetime, end: datetime) -> list[dict]:
        params = {
            "patient": patient_fhir_id,
            "code": f"http://loinc.org|{loinc_code}",
            "date": [f"ge{start.date().isoformat()}", f"le{end.date().isoformat()}"],
        }
        resp = _request_with_retry(
            "GET",
            f"{self.base_url}/Observation",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/fhir+json"},
            params=params,
        )
        if resp is None or not resp.ok:
            return []
        bundle = resp.json()
        vital_type = self._LOINC_TO_VITAL_TYPE.get(loinc_code, loinc_code)
        readings = []
        for entry in bundle.get("entry", []):
            obs = entry.get("resource", {})
            quantity = obs.get("valueQuantity", {})
            if "value" not in quantity:
                continue
            effective = obs.get("effectiveDateTime")
            readings.append(
                {
                    "type": vital_type,
                    "value": float(quantity["value"]),
                    "unit": quantity.get("unit", ""),
                    "recordedAt": datetime.fromisoformat(effective) if effective else datetime.now(timezone.utc),
                    "source": "fhir_device",
                }
            )
        return readings


# ---------------------------------------------------------------------------
# WearableService — orchestrates authorized devices for a patient, dedupes
# and stores fetched readings via the same Vitals table (and the same
# threshold-check path) that direct ingest_vitals POSTs use.
# ---------------------------------------------------------------------------

class WearableService:
    def get_authorized_devices(self, patient_id: int) -> list[dict]:
        return db.query(
            "SELECT id, deviceType, deviceId, accessTokenEnc, refreshTokenEnc, tokenExpiresAt FROM DeviceAuthorizations WHERE patientId = ? AND revokedAt IS NULL",
            (patient_id,),
        )

    def authorize_device(self, patient_id: int, device_type: str, device_id: str, access_token: str, refresh_token: str | None, expires_at: datetime | None) -> int:
        return db.execute_returning_id(
            """
            INSERT INTO DeviceAuthorizations (patientId, deviceType, deviceId, accessTokenEnc, refreshTokenEnc, tokenExpiresAt)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (patient_id, device_type, device_id, crypto.encrypt(access_token), crypto.encrypt(refresh_token), expires_at),
        )

    def revoke_device(self, authorization_id: int) -> None:
        db.execute("UPDATE DeviceAuthorizations SET revokedAt = SYSUTCDATETIME() WHERE id = ?", (authorization_id,))

    def _store_reading(self, patient_id: int, reading: dict) -> None:
        recorded_at = reading["recordedAt"]
        if isinstance(recorded_at, date) and not isinstance(recorded_at, datetime):
            recorded_at = datetime.combine(recorded_at, datetime.min.time(), tzinfo=timezone.utc)

        # Same duplicate guard as POST /vitals (same source+type within 60s).
        since = recorded_at - timedelta(seconds=60)
        until = recorded_at + timedelta(seconds=60)
        existing = db.query_one(
            "SELECT TOP 1 id FROM Vitals WHERE patientId = ? AND type = ? AND source = ? AND recordedAt BETWEEN ? AND ?",
            (patient_id, reading["type"], reading["source"], since, until),
        )
        if existing:
            return

        try:
            numeric_value, _ = validate_vital_value(reading["type"], reading["value"])
        except Exception as exc:
            logging.warning("wearables: rejected implausible reading %s=%s: %s", reading["type"], reading["value"], exc)
            return

        alert = None
        try:
            alert = alert_rules.check_vital(patient_id, reading["type"], numeric_value)
        except Exception as exc:
            logging.warning("wearables: threshold check failed: %s", exc)

        db.execute(
            "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source, isAnomaly) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (patient_id, reading["type"], numeric_value, reading.get("unit", ""), recorded_at, reading["source"], 1 if alert else 0),
        )

    def sync_patient(self, patient_id: int, for_date: date | None = None) -> int:
        """Pulls the latest day's data from every authorized device for a
        patient, dedupes, and writes to Vitals. Returns the count stored."""
        for_date = for_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
        stored = 0
        for device in self.get_authorized_devices(patient_id):
            access_token = crypto.decrypt(device["accessTokenEnc"])
            if not access_token:
                logging.warning("wearables: could not decrypt token for device %s (patient %s) — skipping", device["id"], patient_id)
                continue

            readings: list[dict] = []
            if device["deviceType"] == "fitbit":
                client = FitbitClient()
                readings = client.fetch_heart_rate(access_token, for_date) + client.fetch_steps(access_token, for_date) + client.fetch_sleep(access_token, for_date)
            elif device["deviceType"] == "google_fit":
                client = GoogleFitClient()
                readings = client.fetch_heart_rate(access_token, for_date) + client.fetch_steps(access_token, for_date)
            elif device["deviceType"] == "fhir_device":
                logging.info("wearables: fhir_device sync requires a base URL per-device — not auto-synced here; call fetch_observations directly")
                continue
            else:
                logging.warning("wearables: unknown deviceType %r for device %s", device["deviceType"], device["id"])
                continue

            for reading in readings:
                self._store_reading(patient_id, reading)
                stored += 1
        return stored

    def handle_webhook(self, device_type: str, payload: dict) -> None:
        """Processes a real-time push notification. Fitbit's webhook payload
        is a "data changed" ping (a list of {collectionType, ownerId, date}),
        not the data itself — this triggers a targeted sync for that owner
        rather than trying to parse vitals out of the ping."""
        if device_type != "fitbit":
            logging.info("wearables: webhook for unhandled device type %r", device_type)
            return
        for notice in payload if isinstance(payload, list) else [payload]:
            owner_id = notice.get("ownerId")
            notice_date = notice.get("date")
            device = db.query_one(
                "SELECT patientId FROM DeviceAuthorizations WHERE deviceType='fitbit' AND deviceId = ? AND revokedAt IS NULL",
                (owner_id,),
            )
            if not device:
                logging.warning("wearables: webhook for unknown/revoked Fitbit owner %r", owner_id)
                continue
            for_date = datetime.fromisoformat(notice_date).date() if notice_date else None
            self.sync_patient(device["patientId"], for_date)
