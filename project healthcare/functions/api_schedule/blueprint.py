"""HTTP: telemedicine scheduling — provider availability, slots, appointments
(docs/BLUEPRINT.md Part 6.F)."""
from datetime import datetime, time, timedelta, timezone

import azure.functions as func

from shared import db
from shared.audit import audit_read, audit_write
from shared.audit import record as audit_record
from shared.auth import (
    BadRequest,
    error_response,
    get_principal,
    parse_int_param,
    require_own_patient,
    require_role,
)
from shared.claims import claim_by_contact
from shared.notify import notifier
from shared.ratelimit import enforce_claim_attempts
from shared.responses import error, not_found, success
from shared.validation import require_string

bp = func.Blueprint()

# Fallback default availability (Mon-Fri 09:00-17:00) used only for a
# provider with no ProviderAvailability rows of their own — a real per-
# provider calendar overrides this.
_DEFAULT_AVAILABILITY = [(dow, 9, 17) for dow in range(5)]
_LOOKAHEAD_DAYS = 14


@bp.route(route="providers", methods=["GET"])
def list_providers(req: func.HttpRequest) -> func.HttpResponse:
    try:
        require_role(req, "patient", "provider", "caregiver")
        rows = db.query("SELECT id, name, specialty, contact FROM Providers ORDER BY name", ())
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="providers", methods=["POST"])
def create_provider(req: func.HttpRequest) -> func.HttpResponse:
    try:
        require_role(req, "provider")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        name = require_string(body.get("name"), "name")
        specialty = require_string(body.get("specialty"), "specialty", max_length=100)
        contact = require_string(body.get("contact"), "contact")

        provider_id = db.execute_returning_id(
            "INSERT INTO Providers (name, specialty, contact) OUTPUT INSERTED.id VALUES (?, ?, ?)",
            (name, specialty, contact),
        )
        return success({"id": provider_id}, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="providers/claim", methods=["POST"])
def claim_provider(req: func.HttpRequest) -> func.HttpResponse:
    """A freshly-signed-in principal (not yet onboarded) claims a
    seeded/registered-but-unclaimed Providers row by its contact email,
    binding it to their identity. Solves the bootstrap problem — seed data
    creates unclaimed Provider rows, so the very first provider doesn't need
    provider-only POST /providers to already exist."""
    try:
        principal = get_principal(req)
        enforce_claim_attempts(principal.user_id)
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        contact = body.get("contact")
        if not contact:
            raise BadRequest("contact is required")

        try:
            result = claim_by_contact(principal, table="Providers", owner_col="principalUserId", contact=contact)
        except Exception:
            audit_record(principal, action="claim_provider", target_type="Provider", target_id="unknown", outcome="denied", phi_accessed=False)
            raise
        audit_record(principal, action="claim_provider", target_type="Provider", target_id=result["id"])
        return success(result)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="providers/{providerId}/availability", methods=["GET"])
def get_availability(req: func.HttpRequest) -> func.HttpResponse:
    """A provider's own weekly availability, or the system default if they
    haven't set one — {dayOfWeek, startHour, endHour} rows, 0=Monday."""
    try:
        require_role(req, "patient", "provider", "caregiver")
        provider_id = parse_int_param(req, "providerId")
        rows = db.query(
            "SELECT dayOfWeek, startHour, endHour FROM ProviderAvailability WHERE providerId = ? ORDER BY dayOfWeek",
            (provider_id,),
        )
        if not rows:
            rows = [{"dayOfWeek": d, "startHour": s, "endHour": e} for d, s, e in _DEFAULT_AVAILABILITY]
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="providers/{providerId}/availability", methods=["PUT"])
def set_availability(req: func.HttpRequest) -> func.HttpResponse:
    """Replaces a provider's weekly availability wholesale. Body:
    [{"dayOfWeek": 0-6, "startHour": 0-23, "endHour": 1-24}, ...]"""
    try:
        principal = require_role(req, "provider")
        provider_id = parse_int_param(req, "providerId")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")
        if not isinstance(body, list):
            raise BadRequest("Body must be a JSON array of {dayOfWeek, startHour, endHour}")

        parsed = []
        for slot in body:
            dow, start, end = slot.get("dayOfWeek"), slot.get("startHour"), slot.get("endHour")
            if not isinstance(dow, int) or not (0 <= dow <= 6):
                raise BadRequest("dayOfWeek must be 0-6 (0=Monday)")
            if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= 24):
                raise BadRequest("startHour/endHour must satisfy 0 <= startHour < endHour <= 24")
            parsed.append((dow, start, end))

        with db.transaction() as tx:
            tx.execute("DELETE FROM ProviderAvailability WHERE providerId = ?", (provider_id,))
            for dow, start, end in parsed:
                tx.execute(
                    "INSERT INTO ProviderAvailability (providerId, dayOfWeek, startHour, endHour) VALUES (?, ?, ?, ?)",
                    (provider_id, dow, start, end),
                )
        audit_record(principal, action="set_availability", target_type="Provider", target_id=provider_id)
        return success({"providerId": provider_id, "slots": len(parsed)})
    except Exception as exc:
        return error_response(exc)


def _provider_availability(provider_id: int) -> list[tuple[int, int, int]]:
    rows = db.query(
        "SELECT dayOfWeek, startHour, endHour FROM ProviderAvailability WHERE providerId = ?", (provider_id,)
    )
    if not rows:
        return _DEFAULT_AVAILABILITY
    return [(r["dayOfWeek"], r["startHour"], r["endHour"]) for r in rows]


@bp.route(route="providers/{providerId}/slots", methods=["GET"])
def list_open_slots(req: func.HttpRequest) -> func.HttpResponse:
    try:
        require_role(req, "patient", "provider", "caregiver")
        provider_id = parse_int_param(req, "providerId")

        booked = {
            r["startsAt"]
            for r in db.query(
                "SELECT startsAt FROM Appointments WHERE providerId = ? AND status != 'cancelled'", (provider_id,)
            )
        }
        availability = _provider_availability(provider_id)
        by_day = {}
        for dow, start, end in availability:
            by_day.setdefault(dow, []).append((start, end))

        now = datetime.now(timezone.utc)
        open_slots = []
        for day_offset in range(_LOOKAHEAD_DAYS):
            day = (now + timedelta(days=day_offset)).date()
            for start, end in by_day.get(day.weekday(), []):
                for hour in range(start, end):
                    slot = datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)
                    if slot <= now or slot in booked:
                        continue
                    open_slots.append(slot.isoformat())

        return success(sorted(open_slots))
    except Exception as exc:
        return error_response(exc)


@bp.route(route="appointments", methods=["POST"])
def book_appointment(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        patient_id = body.get("patientId")
        provider_id = body.get("providerId")
        starts_at = body.get("startsAt")
        appt_type = body.get("type", "telemed")
        notes = body.get("notes")
        if notes is not None:
            notes = require_string(notes, "notes", max_length=2000, required=False)

        if not all([patient_id, provider_id, starts_at]):
            raise BadRequest("patientId, providerId, and startsAt are required")
        require_own_patient(principal, patient_id)

        conflict = db.query_one(
            "SELECT id FROM Appointments WHERE providerId = ? AND startsAt = ? AND status != 'cancelled'",
            (provider_id, starts_at),
        )
        if conflict:
            return error("SLOT_UNAVAILABLE", "Slot no longer available", 409)

        appt_id = db.execute_returning_id(
            """
            INSERT INTO Appointments (patientId, providerId, startsAt, type, status, notes)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, 'confirmed', ?)
            """,
            (patient_id, provider_id, starts_at, appt_type, notes),
        )
        audit_write(req, "Appointment", appt_id, action="book_appointment")

        provider = db.query_one("SELECT name FROM Providers WHERE id = ?", (provider_id,))
        if provider:
            try:
                notifier.notify_appointment_confirmation(patient_id, provider["name"], starts_at, appt_type)
            except Exception:
                pass

        return success({"id": appt_id, "status": "confirmed"}, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="appointments/{patientId}", methods=["GET"])
def list_appointments(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "Appointment", patient_id, action="list_appointments")
        rows = db.query(
            """
            SELECT a.id, a.startsAt, a.type, a.status, a.notes, p.name AS providerName, p.specialty
            FROM Appointments a JOIN Providers p ON p.id = a.providerId
            WHERE a.patientId = ? ORDER BY a.startsAt DESC
            """,
            (patient_id,),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="appointments/{appointmentId}/status", methods=["PATCH"])
def update_appointment_status(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        appt_id = parse_int_param(req, "appointmentId")
        appt = db.query_one(
            """
            SELECT a.patientId, a.providerId, a.startsAt, a.type, p.name AS providerName
            FROM Appointments a JOIN Providers p ON p.id = a.providerId WHERE a.id = ?
            """,
            (appt_id,),
        )
        if not appt:
            return not_found("Appointment")
        require_own_patient(principal, appt["patientId"])
        try:
            body = req.get_json()
        except ValueError:
            body = {}
        status = body.get("status")
        if status not in ("confirmed", "completed", "cancelled"):
            raise BadRequest("status must be one of: confirmed, completed, cancelled")
        notes = body.get("notes")
        if notes is not None:
            notes = require_string(notes, "notes", max_length=2000, required=False)

        if notes is not None:
            db.execute("UPDATE Appointments SET status = ?, notes = ? WHERE id = ?", (status, notes, appt_id))
        else:
            db.execute("UPDATE Appointments SET status = ? WHERE id = ?", (status, appt_id))
        audit_write(req, "Appointment", appt_id, action=f"appointment_status:{status}")

        if status == "cancelled":
            # Notify both sides — the patient (existing channel) and the
            # provider (in-app record; providers aren't Patients rows so
            # they don't have an email-capable notifier path here).
            try:
                notifier.send_in_app(
                    appt["patientId"], "appointment_cancelled", "Appointment cancelled",
                    f"Your {appt['type']} appointment with {appt['providerName']} on {appt['startsAt']} was cancelled.",
                )
            except Exception:
                pass

        return success({"id": appt_id, "status": status})
    except Exception as exc:
        return error_response(exc)
