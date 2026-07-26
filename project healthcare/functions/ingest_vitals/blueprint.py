"""HTTP: accept wearable vitals — single reading or batch (docs/BLUEPRINT.md
Part 6.A). Validates clinical plausibility, flags out-of-threshold readings
as anomalies, deduplicates same-source readings within a short window, and
runs the alert-rules threshold check on every accepted reading.
"""
import logging
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared import db
from shared.alerts import rules as alert_rules
from shared.audit import audit_write
from shared.auth import BadRequest, error_response, require_own_patient, require_role
from shared.responses import error, success
from shared.validation import validate_vital_value

bp = func.Blueprint()

_VALID_TYPES = {
    "heartRate", "steps", "sleep", "bp_systolic", "bp_diastolic", "glucose",
    "oxygenSaturation", "temperature",
}
_DUPLICATE_WINDOW_SECONDS = 60


def _is_duplicate(patient_id: int, vital_type: str, source: str, recorded_at: datetime) -> bool:
    """Same source posting the same metric type within the window — a device
    retry/webhook redelivery, not a second real reading."""
    since = recorded_at - timedelta(seconds=_DUPLICATE_WINDOW_SECONDS)
    until = recorded_at + timedelta(seconds=_DUPLICATE_WINDOW_SECONDS)
    row = db.query_one(
        "SELECT TOP 1 id FROM Vitals WHERE patientId = ? AND type = ? AND source = ? AND recordedAt BETWEEN ? AND ?",
        (patient_id, vital_type, source, since, until),
    )
    return row is not None


def _ingest_one(principal, item: dict) -> dict:
    """Validates and stores one reading. Per-item validation problems (bad
    type/value/duplicate) are returned as a status dict, not raised, so a
    batch's other items still get processed. Authorization failures
    (require_own_patient) DO raise — an unauthorized patientId in a batch
    fails the whole request, not just that item."""
    patient_id = item.get("patientId")
    vital_type = item.get("type")
    value = item.get("value")
    unit = item.get("unit", "")
    source = item.get("source", "simulated")
    recorded_at = item.get("recordedAt")

    if not patient_id or vital_type not in _VALID_TYPES or value is None:
        return {
            "status": "error",
            "type": vital_type,
            "message": f"patientId, a valid type (one of {sorted(_VALID_TYPES)}), and value are required",
        }
    require_own_patient(principal, patient_id)  # raises Forbidden — not caught here, by design

    try:
        numeric_value, _ = validate_vital_value(vital_type, value)
    except BadRequest as exc:
        return {"status": "error", "type": vital_type, "message": str(exc)}

    recorded_at_dt = datetime.fromisoformat(recorded_at) if recorded_at else datetime.now(timezone.utc)

    if _is_duplicate(patient_id, vital_type, source, recorded_at_dt):
        return {"status": "duplicate", "type": vital_type, "message": "Duplicate reading within 60s of an existing one"}

    alert = None
    try:
        alert = alert_rules.check_vital(patient_id, vital_type, numeric_value)
    except Exception as exc:
        logging.warning("Threshold check failed for a new vital: %s", exc)
    is_anomaly = alert is not None

    vital_id = db.execute_returning_id(
        """
        INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source, isAnomaly)
        OUTPUT INSERTED.id
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (patient_id, vital_type, numeric_value, unit, recorded_at_dt, source, 1 if is_anomaly else 0),
    )
    return {"status": "success", "id": vital_id, "type": vital_type, "isAnomaly": is_anomaly, "alert": alert}


@bp.route(route="vitals", methods=["POST"])
def ingest_vitals(req: func.HttpRequest) -> func.HttpResponse:
    """Accepts either a single vital object or a JSON array for batch
    ingestion. Single-item requests keep simple status-code semantics
    (201/400/409); batches return 201 with a per-item results array."""
    try:
        principal = require_role(req, "patient", "provider")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        items = body if isinstance(body, list) else [body]
        if not items:
            raise BadRequest("At least one vital reading is required")

        # An unauthorized patientId anywhere in the batch (require_own_patient
        # raising Forbidden) propagates to the outer handler and fails the
        # whole request — a batch is all-or-nothing on authorization.
        results = [_ingest_one(principal, item) for item in items]

        stored_ids = [str(r["id"]) for r in results if r.get("id")]
        if stored_ids:
            audit_write(req, "Vital", ",".join(stored_ids), action="ingest_vitals")

        if len(items) == 1:
            only = results[0]
            if only["status"] == "duplicate":
                return error("DUPLICATE", only["message"], 409)
            if only["status"] == "error":
                return error("BAD_REQUEST", only["message"], 400)
            return success(only, status_code=201)

        all_failed = all(r["status"] == "error" for r in results)
        return success({"results": results}, status_code=400 if all_failed else 201)
    except Exception as exc:
        return error_response(exc)
