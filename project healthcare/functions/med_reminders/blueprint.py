"""Timer: medication reminders + due-dose logging, missed-dose detection,
plus adherence marking, summary endpoints, and medication management
(docs/BLUEPRINT.md Part 6.D, Part 8.4).

Schedule types: morning/afternoon/night (single fixed slot), daily (one
slot/day), twice_daily (two slots/day), custom (patient/provider-defined
HH:MM times via Medications.customTimes, comma-separated), as_needed (no
automatic reminders or adherence tracking at all — there's no "due" time to
be adherent to).

A due dose is logged as 'pending' (not 'missed') when the reminder fires;
a separate pass marks it 'missed' only after a 2-hour grace window with no
patient action, so a dose isn't judged before the patient had a chance to log it.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone

import azure.functions as func

from shared import db
from shared.adherence.patterns import summary as adherence_summary
from shared.alerts import rules as alert_rules
from shared.audit import audit_read, audit_write
from shared.audit import record as audit_record
from shared.auth import (
    BadRequest,
    error_response,
    parse_int_param,
    require_caregiver_write_access,
    require_own_patient,
    require_role,
)
from shared.notify import notifier
from shared.responses import not_found, success
from shared.validation import require_string

bp = func.Blueprint()

_FIXED_SLOT_TIMES = {
    "morning": [time(8, 0)],
    "afternoon": [time(13, 0)],
    "night": [time(20, 0)],
    "daily": [time(9, 0)],
    "twice_daily": [time(8, 0), time(20, 0)],
}
_VALID_SCHEDULES = set(_FIXED_SLOT_TIMES) | {"as_needed", "custom"}
_REMINDER_WINDOW_MINUTES = 30
_MISSED_GRACE_HOURS = 2


def _parse_custom_times(raw: str | None) -> list[time]:
    if not raw:
        return []
    out = []
    for token in raw.split(","):
        token = token.strip()
        try:
            hh, mm = token.split(":")
            out.append(time(int(hh), int(mm)))
        except (ValueError, TypeError):
            logging.warning("med_reminders: skipping unparseable custom time %r", token)
    return out


def _due_times_today(schedule: str, custom_times_raw: str | None, today: date) -> list[datetime]:
    if schedule == "as_needed":
        return []
    if schedule == "custom":
        slot_times = _parse_custom_times(custom_times_raw)
    else:
        slot_times = _FIXED_SLOT_TIMES.get(schedule, [time(9, 0)])
    return [datetime.combine(today, t, tzinfo=timezone.utc) for t in slot_times]


@bp.timer_trigger(schedule="0 */30 * * * *", arg_name="timer", run_on_startup=False)
def med_reminders_timer(timer: func.TimerRequest) -> None:
    now = datetime.now(timezone.utc)
    today = now.date()

    active_meds = db.query(
        """
        SELECT id, patientId, name, dosage, schedule, customTimes FROM Medications
        WHERE startDate <= ? AND (endDate IS NULL OR endDate >= ?)
        """,
        (today, today),
    )

    created = 0
    for med in active_meds:
        for due_at in _due_times_today(med["schedule"], med.get("customTimes"), today):
            if abs((now - due_at).total_seconds()) > _REMINDER_WINDOW_MINUTES * 60:
                continue

            existing = db.query_one(
                "SELECT id FROM AdherenceLog WHERE medicationId = ? AND dueAt = ?", (med["id"], due_at)
            )
            if existing:
                continue

            db.execute(
                "INSERT INTO AdherenceLog (medicationId, dueAt, takenAt, status) VALUES (?, ?, NULL, 'pending')",
                (med["id"], due_at),
            )
            created += 1
            try:
                notifier.notify_patient_reminder(med["patientId"], med["name"], med["dosage"])
            except Exception as exc:
                logging.warning("med_reminders: notify failed for medication %s: %s", med["id"], exc)

    # Missed-dose detection: a 'pending' dose more than MISSED_GRACE_HOURS past
    # due, with no patient action, is now decided as missed.
    missed_cutoff = now - timedelta(hours=_MISSED_GRACE_HOURS)
    missed_count = db.execute(
        "UPDATE AdherenceLog SET status = 'missed' WHERE status = 'pending' AND dueAt <= ?", (missed_cutoff,)
    )

    logging.info("med_reminders: logged %d due doses, marked %d as missed", created, missed_count)


@bp.route(route="adherence/{logId}", methods=["PATCH"])
def mark_dose(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        log_id = parse_int_param(req, "logId")
        row = db.query_one(
            "SELECT m.patientId FROM AdherenceLog a JOIN Medications m ON m.id = a.medicationId WHERE a.id = ?",
            (log_id,),
        )
        if not row:
            return not_found("Adherence log entry")
        require_own_patient(principal, row["patientId"])
        require_caregiver_write_access(principal, row["patientId"])

        try:
            body = req.get_json()
        except ValueError:
            body = {}
        status = body.get("status")
        if status not in ("taken", "missed"):
            raise BadRequest("status must be 'taken' or 'missed'")

        taken_at = datetime.now(timezone.utc) if status == "taken" else None
        db.execute("UPDATE AdherenceLog SET status = ?, takenAt = ? WHERE id = ?", (status, taken_at, log_id))
        audit_write(req, "AdherenceLog", log_id, action=f"mark_dose:{status}")

        med_summary = adherence_summary(row["patientId"])
        overall = med_summary["medications"]
        if overall:
            avg_adherence = sum(m["adherencePct"] for m in overall) / len(overall)
            try:
                alert_rules.check_adherence(row["patientId"], avg_adherence)
            except Exception as exc:
                logging.warning("mark_dose: adherence alert check failed: %s", exc)

        return success({"id": log_id, "status": status})
    except Exception as exc:
        return error_response(exc)


@bp.route(route="adherence/{patientId}/summary", methods=["GET"])
def get_adherence_summary(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "AdherenceLog", patient_id, action="view_adherence_summary")
        return success(adherence_summary(patient_id))
    except Exception as exc:
        return error_response(exc)


@bp.route(route="adherence/{patientId}/pending", methods=["GET"])
def get_pending_doses(req: func.HttpRequest) -> func.HttpResponse:
    """Recent due doses (today +/- a day) for the patient to mark taken/missed."""
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        since = datetime.now(timezone.utc) - timedelta(days=1)
        rows = db.query(
            """
            SELECT a.id, a.dueAt, a.takenAt, a.status, m.name, m.dosage, m.schedule
            FROM AdherenceLog a JOIN Medications m ON m.id = a.medicationId
            WHERE m.patientId = ? AND a.dueAt >= ?
            ORDER BY a.dueAt DESC
            """,
            (patient_id, since),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="medications/{patientId}", methods=["GET"])
def list_medications(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "Medication", patient_id, action="list_medications")
        rows = db.query(
            "SELECT id, name, dosage, schedule, customTimes, startDate, endDate FROM Medications WHERE patientId = ? ORDER BY startDate DESC",
            (patient_id,),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="medications", methods=["POST"])
def create_medication(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "provider")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        patient_id = body.get("patientId")
        name = require_string(body.get("name"), "name")
        dosage = require_string(body.get("dosage"), "dosage", max_length=50)
        schedule = body.get("schedule")
        custom_times = body.get("customTimes")
        start_date = body.get("startDate")

        if not all([patient_id, start_date]) or schedule not in _VALID_SCHEDULES:
            raise BadRequest(f"patientId, name, dosage, startDate, and schedule (one of {sorted(_VALID_SCHEDULES)}) are required")
        if schedule == "custom" and not _parse_custom_times(custom_times):
            raise BadRequest("customTimes must be provided as comma-separated HH:MM values when schedule is 'custom'")

        med_id = db.execute_returning_id(
            """
            INSERT INTO Medications (patientId, name, dosage, schedule, customTimes, startDate)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (patient_id, name, dosage, schedule, custom_times, start_date),
        )
        audit_record(principal, action="prescribe_medication", target_type="Medication", target_id=med_id)
        return success({"id": med_id}, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="medications/{medicationId}", methods=["PATCH"])
def update_medication(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "provider")
        med_id = parse_int_param(req, "medicationId")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        dosage = body.get("dosage")
        schedule = body.get("schedule")
        end_date = body.get("endDate")

        if schedule is not None and schedule not in _VALID_SCHEDULES:
            raise BadRequest(f"schedule must be one of {sorted(_VALID_SCHEDULES)}")
        if dosage is None and schedule is None and end_date is None:
            raise BadRequest("dosage, schedule, and/or endDate must be provided")

        if dosage is not None:
            db.execute("UPDATE Medications SET dosage = ? WHERE id = ?", (require_string(dosage, "dosage", max_length=50), med_id))
        if schedule is not None:
            db.execute("UPDATE Medications SET schedule = ? WHERE id = ?", (schedule, med_id))
        if end_date is not None:
            db.execute("UPDATE Medications SET endDate = ? WHERE id = ?", (end_date, med_id))

        audit_record(principal, action="update_medication", target_type="Medication", target_id=med_id)
        return success({"id": med_id})
    except Exception as exc:
        return error_response(exc)
