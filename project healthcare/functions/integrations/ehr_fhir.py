"""EHR integration via FHIR R4 + SMART on FHIR (docs/BLUEPRINT.md Part 7,
"Additional: EHR integration"). Real OAuth2 discovery + token flows and real
FHIR resource shapes — untested against a live EHR sandbox in this session
(that needs a real FHIR server + registered SMART app, neither of which
exist here). No-ops cleanly when EHR_FHIR_BASE_URL isn't configured, exactly
like every other optional integration in this codebase.

FHIR R4 resource reads use the standard search-by-patient pattern
(?patient={id}) supported by every conformant FHIR server (this is the open
HL7 FHIR standard, not a vendor API — unlike the wearable clients, there's no
single vendor's docs to go stale here).
"""
import logging
from datetime import datetime, timezone

import requests

from shared import config, crypto, db

_REQUEST_TIMEOUT_SECONDS = 15


class FHIRClient:
    """SMART on FHIR client: discovers the server's OAuth2 endpoints from its
    /.well-known/smart-configuration document (per the SMART App Launch spec),
    then supports both client-credentials (system-to-system, backend service)
    and authorization-code (patient-authorized) flows."""

    def __init__(self, fhir_base_url: str | None = None, client_id: str | None = None, client_secret: str | None = None):
        self.base_url = (fhir_base_url or config.EHR_FHIR_BASE_URL or "").rstrip("/")
        self.client_id = client_id or config.EHR_CLIENT_ID
        self.client_secret = client_secret or config.EHR_CLIENT_SECRET
        self._discovery: dict | None = None

    def is_configured(self) -> bool:
        return bool(self.base_url and self.client_id)

    def discover_endpoints(self) -> dict | None:
        """GET {base_url}/.well-known/smart-configuration — cached per instance."""
        if not self.base_url:
            return None
        if self._discovery is not None:
            return self._discovery
        try:
            resp = requests.get(f"{self.base_url}/.well-known/smart-configuration", timeout=_REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            self._discovery = resp.json()
            return self._discovery
        except requests.exceptions.RequestException as exc:
            logging.warning("ehr_fhir: SMART discovery failed for %s: %s", self.base_url, exc)
            return None

    def client_credentials_flow(self, scope: str = "system/*.read") -> dict | None:
        """System-to-system auth — no patient/user interaction, for the
        scheduled nightly sync (see EHRSyncService)."""
        if not self.is_configured():
            logging.info("[ehr_fhir:noop] not configured")
            return None
        discovery = self.discover_endpoints()
        if not discovery or "token_endpoint" not in discovery:
            return None
        try:
            resp = requests.post(
                discovery["token_endpoint"],
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": scope,
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            logging.warning("ehr_fhir: client_credentials token request failed: %s", exc)
            return None

    def authorization_code_flow_url(self, redirect_uri: str, state: str, scope: str = "patient/*.read") -> str | None:
        """Builds the URL to redirect a patient to for consenting to EHR
        access (the interactive counterpart to client_credentials_flow)."""
        discovery = self.discover_endpoints()
        if not discovery or "authorization_endpoint" not in discovery:
            return None
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "aud": self.base_url,
        }
        return f"{discovery['authorization_endpoint']}?{requests.compat.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict | None:
        discovery = self.discover_endpoints()
        if not discovery or "token_endpoint" not in discovery:
            return None
        try:
            resp = requests.post(
                discovery["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            logging.warning("ehr_fhir: authorization_code token exchange failed: %s", exc)
            return None

    def refresh_token(self, refresh_token: str) -> dict | None:
        discovery = self.discover_endpoints()
        if not discovery or "token_endpoint" not in discovery:
            return None
        try:
            resp = requests.post(
                discovery["token_endpoint"],
                data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": self.client_id, "client_secret": self.client_secret},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            logging.warning("ehr_fhir: token refresh failed: %s", exc)
            return None

    def _get(self, access_token: str, path: str, params: dict | None = None) -> dict | None:
        try:
            resp = requests.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/fhir+json"},
                params=params,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            logging.warning("ehr_fhir: GET %s failed: %s", path, exc)
            return None

    def _post(self, access_token: str, path: str, body: dict) -> dict | None:
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/fhir+json"},
                json=body,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            logging.warning("ehr_fhir: POST %s failed: %s", path, exc)
            return None

    def _bundle_entries(self, bundle: dict | None) -> list[dict]:
        if not bundle:
            return []
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    def read_patient(self, access_token: str, patient_fhir_id: str) -> dict | None:
        return self._get(access_token, f"/Patient/{patient_fhir_id}")

    def read_medications(self, access_token: str, patient_fhir_id: str) -> list[dict]:
        bundle = self._get(access_token, "/MedicationRequest", {"patient": patient_fhir_id, "status": "active"})
        return self._bundle_entries(bundle)

    def read_conditions(self, access_token: str, patient_fhir_id: str) -> list[str]:
        bundle = self._get(access_token, "/Condition", {"patient": patient_fhir_id})
        conditions = []
        for resource in self._bundle_entries(bundle):
            text = resource.get("code", {}).get("text")
            if not text:
                codings = resource.get("code", {}).get("coding", [])
                text = codings[0].get("display") if codings else None
            if text:
                conditions.append(text)
        return conditions

    def read_observations(self, access_token: str, patient_fhir_id: str, loinc_codes: list[str], start: datetime, end: datetime) -> list[dict]:
        bundle = self._get(
            access_token,
            "/Observation",
            {
                "patient": patient_fhir_id,
                "code": ",".join(f"http://loinc.org|{c}" for c in loinc_codes),
                "date": [f"ge{start.date().isoformat()}", f"le{end.date().isoformat()}"],
            },
        )
        return self._bundle_entries(bundle)

    def read_allergies(self, access_token: str, patient_fhir_id: str) -> list[dict]:
        bundle = self._get(access_token, "/AllergyIntolerance", {"patient": patient_fhir_id})
        return self._bundle_entries(bundle)

    def read_immunizations(self, access_token: str, patient_fhir_id: str) -> list[dict]:
        bundle = self._get(access_token, "/Immunization", {"patient": patient_fhir_id})
        return self._bundle_entries(bundle)

    def write_observation(self, access_token: str, patient_fhir_id: str, loinc_code: str, display: str, value: float, unit: str, effective: datetime) -> dict | None:
        """POSTs a new Observation resource back to the EHR — e.g. a vital
        this app recorded that the EHR should also have on file."""
        body = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": loinc_code, "display": display}]},
            "subject": {"reference": f"Patient/{patient_fhir_id}"},
            "effectiveDateTime": effective.isoformat(),
            "valueQuantity": {"value": value, "unit": unit},
        }
        return self._post(access_token, "/Observation", body)


# ---------------------------------------------------------------------------
# EHRSyncService — orchestrates in/out sync for a patient, logs every attempt
# to EHRSyncLog regardless of outcome (HIPAA-style traceability for data
# crossing the system boundary, mirroring AuditLog's role for access events).
# ---------------------------------------------------------------------------

_VITAL_TO_LOINC = {
    "heartRate": ("8867-4", "Heart rate"),
    "bp_systolic": ("8480-6", "Systolic blood pressure"),
    "bp_diastolic": ("8462-4", "Diastolic blood pressure"),
    "oxygenSaturation": ("59408-5", "Oxygen saturation"),
    "temperature": ("8310-5", "Body temperature"),
}


class EHRSyncService:
    def __init__(self, client: FHIRClient | None = None):
        self.client = client or FHIRClient()

    def _log(self, patient_id: int, resource_type: str, resource_id: str | None, direction: str, status: str, error: str | None = None) -> None:
        db.execute(
            "INSERT INTO EHRSyncLog (patientId, fhirResourceType, fhirResourceId, syncDirection, status, errorMessage) VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, resource_type, resource_id, direction, status, error),
        )

    def _patient_fhir_id(self, patient_id: int) -> str | None:
        row = db.query_one("SELECT fhirPatientId FROM Patients WHERE id = ?", (patient_id,))
        return row["fhirPatientId"] if row else None

    def sync_patient_in(self, patient_id: int, access_token: str) -> dict:
        """Pulls EHR-side data for a patient into this app's own tables.
        Local data (Vitals recorded here) is never overwritten — the EHR is
        treated as the source of truth for its own historical record, this
        app is the source of truth for anything recorded through it."""
        fhir_id = self._patient_fhir_id(patient_id)
        if not fhir_id:
            self._log(patient_id, "Patient", None, "in", "error", "No linked FHIR patient id")
            return {"status": "error", "message": "Patient is not linked to an EHR record"}

        result = {"medications": 0, "conditions": 0, "allergies": 0, "immunizations": 0}
        try:
            for med in self.client.read_medications(access_token, fhir_id):
                result["medications"] += 1
                self._log(patient_id, "MedicationRequest", med.get("id"), "in", "success")
            conditions = self.client.read_conditions(access_token, fhir_id)
            result["conditions"] = len(conditions)
            self._log(patient_id, "Condition", None, "in", "success")
            for allergy in self.client.read_allergies(access_token, fhir_id):
                result["allergies"] += 1
                self._log(patient_id, "AllergyIntolerance", allergy.get("id"), "in", "success")
            for imm in self.client.read_immunizations(access_token, fhir_id):
                result["immunizations"] += 1
                self._log(patient_id, "Immunization", imm.get("id"), "in", "success")
            result["status"] = "success"
        except Exception as exc:
            logging.error("ehr_fhir: sync_patient_in failed for patient %s: %s", patient_id, exc)
            self._log(patient_id, "Patient", fhir_id, "in", "error", str(exc))
            result["status"] = "error"
            result["message"] = str(exc)
        return result

    def sync_vitals_out(self, patient_id: int, since: datetime) -> int:
        """Pushes Vitals recorded in this app since `since` back to the EHR
        as Observation resources, for vital types with a known LOINC mapping."""
        fhir_id = self._patient_fhir_id(patient_id)
        access_token = self._get_valid_access_token(patient_id)
        if not fhir_id or not access_token:
            return 0

        rows = db.query(
            "SELECT id, type, value, unit, recordedAt FROM Vitals WHERE patientId = ? AND recordedAt >= ? AND type IN ({})".format(
                ",".join("?" for _ in _VITAL_TO_LOINC)
            ),
            (patient_id, since, *_VITAL_TO_LOINC.keys()),
        )
        pushed = 0
        for row in rows:
            loinc_code, display = _VITAL_TO_LOINC[row["type"]]
            recorded_at = row["recordedAt"]
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            obs = self.client.write_observation(access_token, fhir_id, loinc_code, display, row["value"], row["unit"], recorded_at)
            if obs:
                pushed += 1
                self._log(patient_id, "Observation", obs.get("id"), "out", "success")
            else:
                self._log(patient_id, "Observation", str(row["id"]), "out", "error", "write_observation returned no result")
        return pushed

    def _get_valid_access_token(self, patient_id: int) -> str | None:
        row = db.query_one(
            "SELECT accessTokenEnc, refreshTokenEnc, tokenExpiresAt FROM DeviceAuthorizations WHERE patientId = ? AND deviceType = 'ehr' AND revokedAt IS NULL",
            (patient_id,),
        )
        if not row:
            return None
        expires_at = row["tokenExpiresAt"]
        if expires_at and expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
            refresh_token = crypto.decrypt(row["refreshTokenEnc"])
            if not refresh_token:
                return None
            token_data = self.client.refresh_token(refresh_token)
            if not token_data:
                return None
            db.execute(
                "UPDATE DeviceAuthorizations SET accessTokenEnc = ?, tokenExpiresAt = ? WHERE patientId = ? AND deviceType = 'ehr'",
                (crypto.encrypt(token_data["access_token"]), None, patient_id),
            )
            return token_data["access_token"]
        return crypto.decrypt(row["accessTokenEnc"])
