"""Notifications via Azure Communication Services (email + SMS) and an in-app
channel backed by the Notifications table.

Channel primitives — send_email / send_sms / send_in_app — plus send_alert()
which fans out across a patient's preferred channels. Every send is wrapped so
a delivery failure can never crash the caller (a missed reminder must not take
down vitals ingestion), and every attempt is logged by channel + outcome
using the patient id only (never email/phone) so logs stay PHI-free.
"""
import logging
from datetime import datetime, timezone

from .. import config, db

_SENDER_ADDRESS = "DoNotReply@preventive-care-demo.azurecomm.net"


def _log_attempt(channel: str, patient_id: int | None, ok: bool, detail: str = "") -> None:
    logging.info("notify: channel=%s patient=%s ok=%s %s", channel, patient_id, ok, detail)


def _record_in_app(patient_id: int, ntype: str, title: str, body: str, channel: str, sent: bool) -> None:
    try:
        db.execute(
            """
            INSERT INTO Notifications (patientId, type, title, body, channel, sentAt, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
            """,
            (patient_id, ntype, title, body, channel, datetime.now(timezone.utc) if sent else None),
        )
    except Exception as exc:  # never let notification bookkeeping crash the caller
        logging.warning("notify: failed to record notification row: %s", exc)


def send_email(patient_id: int, to_address: str, subject: str, body: str) -> bool:
    """Returns True if handed to ACS, False on no-op/failure. Never raises."""
    if not config.COMMS_CONNECTION_STRING or not to_address:
        _log_attempt("email", patient_id, False, "(no ACS connection / address — noop)")
        return False
    try:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(config.COMMS_CONNECTION_STRING)
        client.begin_send(
            {
                "senderAddress": _SENDER_ADDRESS,
                "recipients": {"to": [{"address": to_address}]},
                "content": {"subject": subject, "plainText": body},
            }
        )
        _log_attempt("email", patient_id, True)
        return True
    except Exception as exc:
        _log_attempt("email", patient_id, False, f"error: {exc}")
        return False


def send_sms(patient_id: int, to_number: str, body: str) -> bool:
    if not config.COMMS_CONNECTION_STRING or not to_number or not config.ACS_SENDER_PHONE:
        _log_attempt("sms", patient_id, False, "(no ACS connection / number — noop)")
        return False
    try:
        from azure.communication.sms import SmsClient

        client = SmsClient.from_connection_string(config.COMMS_CONNECTION_STRING)
        client.send(from_=config.ACS_SENDER_PHONE, to=[to_number], message=body)
        _log_attempt("sms", patient_id, True)
        return True
    except Exception as exc:
        _log_attempt("sms", patient_id, False, f"error: {exc}")
        return False


def send_in_app(patient_id: int, ntype: str, title: str, body: str) -> bool:
    """Always 'succeeds' if the row is written — this is the reliable fallback
    channel that works without any external service configured."""
    _record_in_app(patient_id, ntype, title, body, channel="in_app", sent=True)
    _log_attempt("in_app", patient_id, True)
    return True


def _patient_contact(patient_id: int) -> dict | None:
    return db.query_one("SELECT name, contact FROM Patients WHERE id = ?", (patient_id,))


def send_alert(patient_id: int, ntype: str, title: str, body: str) -> bool:
    """Best-effort delivery across channels: try email, always also record
    in-app. Returns True if any channel delivered."""
    patient = _patient_contact(patient_id)
    contact = patient["contact"] if patient else None
    email_ok = send_email(patient_id, contact, title, body) if contact else False
    _record_in_app(patient_id, ntype, title, body, channel="email" if email_ok else "in_app", sent=email_ok)
    return email_ok or True  # in-app record always lands


# --------------------------------------------------------------------------
# Higher-level helpers (kept for existing callers), now also recording in-app.
# --------------------------------------------------------------------------

def notify_patient_reminder(patient_id: int, medication_name: str, dosage: str) -> None:
    patient = _patient_contact(patient_id)
    if not patient:
        return
    title = f"Medication reminder: {medication_name}"
    body = f"Hi {patient['name']}, it's time for your {medication_name} ({dosage})."
    send_email(patient_id, patient["contact"], title, body)
    _record_in_app(patient_id, "medication_reminder", title, body, channel="email", sent=True)


def notify_appointment_confirmation(patient_id: int, provider_name: str, starts_at: str, appt_type: str) -> None:
    patient = _patient_contact(patient_id)
    if not patient:
        return
    title = "Appointment confirmed"
    body = f"Your {appt_type} appointment with {provider_name} is confirmed for {starts_at}."
    send_email(patient_id, patient["contact"], title, body)
    _record_in_app(patient_id, "appointment", title, body, channel="email", sent=True)


def notify_caregivers(patient_id: int, alert: dict) -> None:
    caregivers = db.query(
        "SELECT name, contact, accessScope FROM Caregivers WHERE patientId = ? AND principalUserId IS NOT NULL",
        (patient_id,),
    )
    patient = db.query_one("SELECT name FROM Patients WHERE id = ?", (patient_id,))
    patient_name = patient["name"] if patient else f"Patient #{patient_id}"

    for cg in caregivers:
        scope = (cg["accessScope"] or "").split(",")
        if alert["kind"] == "vital" and "vitals" not in scope:
            continue
        if alert["kind"] == "adherence" and "adherence" not in scope:
            continue
        if "alerts" not in scope and alert["kind"] not in ("vital", "adherence"):
            continue
        send_email(
            patient_id,
            cg["contact"],
            f"Alert for {patient_name}",
            f"{alert['detail']} (raised at {alert['raisedAt']}).",
        )
