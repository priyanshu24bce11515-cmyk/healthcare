"""HTTP + timer entry points for the wearable/EHR integrations
(integrations/wearables.py, integrations/ehr_fhir.py). Kept as their own
blueprint file, colocated with the integration clients they call, since
neither is a patient-facing CRUD route in the sense the other blueprints are.
"""
import logging
from datetime import datetime, timedelta, timezone

import azure.functions as func

from integrations.ehr_fhir import EHRSyncService
from integrations.wearables import WearableService, verify_fitbit_signature
from shared import config, db

bp = func.Blueprint()


@bp.route(route="integrations/wearable-webhook/{deviceType}", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
def wearable_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """Public webhook endpoint — called by the vendor's own servers, not by
    this app's authenticated frontend, so it can't go through require_role.
    Trust is instead established per-vendor (Fitbit: HMAC signature over the
    raw body; see verify_fitbit_signature)."""
    device_type = req.route_params.get("deviceType", "")

    if req.method == "GET":
        # Fitbit's one-time subscriber verification handshake: respond 204 if
        # the challenge code matches, 404 otherwise (Fitbit requires exactly
        # this distinction, not a generic 200/400).
        if device_type == "fitbit":
            verify = req.params.get("verify")
            if verify and config.FITBIT_VERIFICATION_CODE and verify == config.FITBIT_VERIFICATION_CODE:
                return func.HttpResponse(status_code=204)
            return func.HttpResponse(status_code=404)
        return func.HttpResponse(status_code=404)

    body = req.get_body()
    if device_type == "fitbit":
        signature = req.headers.get("X-Fitbit-Signature")
        if not verify_fitbit_signature(config.FITBIT_CLIENT_SECRET, body, signature):
            logging.warning("wearable_webhook: rejected fitbit payload with invalid/missing signature")
            return func.HttpResponse(status_code=404)

    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(status_code=400)

    try:
        WearableService().handle_webhook(device_type, payload)
    except Exception as exc:
        logging.error("wearable_webhook: handling failed for %s: %s", device_type, exc)
        # Still 204 — Fitbit retries aggressively on non-2xx and this failure
        # is already logged; a stuck bad payload shouldn't blow the retry budget.

    return func.HttpResponse(status_code=204)


@bp.timer_trigger(schedule="0 0 2 * * *", arg_name="timer", run_on_startup=False)
def ehr_sync_scheduled(timer: func.TimerRequest) -> None:
    """Nightly pull-sync for every patient with an active EHR connection.
    Uses the client-credentials (system-to-system) flow, not a per-patient
    interactive token — appropriate for an unattended background job."""
    if not config.EHR_FHIR_BASE_URL or not config.EHR_CLIENT_ID:
        logging.info("ehr_sync_scheduled: EHR integration not configured — skipping")
        return

    patients = db.query(
        "SELECT DISTINCT patientId FROM DeviceAuthorizations WHERE deviceType = 'ehr' AND revokedAt IS NULL"
    )
    if not patients:
        logging.info("ehr_sync_scheduled: no patients with an active EHR connection")
        return

    service = EHRSyncService()
    token_data = service.client.client_credentials_flow()
    if not token_data:
        logging.warning("ehr_sync_scheduled: could not obtain an access token — skipping this run")
        return
    access_token = token_data["access_token"]

    synced, failed = 0, 0
    since = datetime.now(timezone.utc) - timedelta(days=1)
    for row in patients:
        try:
            result = service.sync_patient_in(row["patientId"], access_token)
            if result.get("status") == "success":
                synced += 1
            else:
                failed += 1
            service.sync_vitals_out(row["patientId"], since)
        except Exception as exc:
            failed += 1
            logging.error("ehr_sync_scheduled: sync failed for patient %s: %s", row["patientId"], exc)

    logging.info("ehr_sync_scheduled: synced %d patient(s), %d failure(s)", synced, failed)
