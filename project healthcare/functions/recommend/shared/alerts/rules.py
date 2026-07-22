"""Vital threshold + adherence + risk alert rules, with severity, dedup, and
notification (docs/BLUEPRINT.md Part 6.A, Part 8.3).

Thresholds are data-driven (config.py app settings, not scattered magic
numbers). Each vital can have multiple bounds at different severities; the
worst crossed bound wins. Alerts are deduplicated — the same
(patient, kind, vitalType) won't fire again within ALERT_DEDUPE_HOURS — and
critical alerts additionally notify the patient/provider channel.
"""
from datetime import datetime, timedelta, timezone

from .. import config, db
from ..notify import notifier

# type -> list of (direction, limit, label, severity). Evaluated in order;
# the first crossed bound of the highest severity is what fires.
def _thresholds() -> dict[str, list[tuple]]:
    return {
        "heartRate": [
            ("below", config.HR_ALERT_MIN, "Resting heart rate", "critical"),
            ("above", config.HR_CRITICAL_MAX, "Resting heart rate", "critical"),
            ("above", config.HR_ALERT_MAX, "Resting heart rate", "warning"),
        ],
        "bp_systolic": [
            ("above", config.BP_SYS_CRITICAL_MAX, "Systolic blood pressure", "critical"),
            ("below", config.BP_SYS_ALERT_MIN, "Systolic blood pressure", "critical"),
            ("above", config.BP_SYS_ALERT_MAX, "Systolic blood pressure", "warning"),
        ],
        "bp_diastolic": [
            ("above", config.BP_DIA_ALERT_MAX, "Diastolic blood pressure", "warning"),
        ],
        "glucose": [
            ("above", config.GLUCOSE_ALERT_MAX, "Glucose", "warning"),
        ],
        "oxygenSaturation": [
            ("below", config.O2_SAT_ALERT_MIN, "Oxygen saturation", "critical"),
        ],
        "temperature": [
            ("above", config.TEMP_ALERT_MAX, "Temperature", "warning"),
            ("below", config.TEMP_ALERT_MIN, "Temperature", "warning"),
        ],
    }


_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def check_vital(patient_id: int, vital_type: str, value: float) -> dict | None:
    """Raises the most severe Alert whose bound `value` crosses, or None."""
    rules = _thresholds().get(vital_type)
    if not rules:
        return None

    crossed = []
    for direction, limit, label, severity in rules:
        if (direction == "above" and value > limit) or (direction == "below" and value < limit):
            crossed.append((direction, limit, label, severity))
    if not crossed:
        return None

    direction, limit, label, severity = max(crossed, key=lambda r: _SEVERITY_RANK[r[3]])
    word = "above" if direction == "above" else "below"
    detail = f"{label} reading of {value:g} is {word} the {severity} threshold ({limit:g})"
    return _raise_alert(patient_id, kind="vital", detail=detail, value=str(value), vital_type=vital_type, severity=severity)


def check_adherence(patient_id: int, adherence_pct: float) -> dict | None:
    if adherence_pct >= config.ADHERENCE_ALERT_MIN:
        return None
    detail = f"Medication adherence dropped to {adherence_pct:.0f}% (below {config.ADHERENCE_ALERT_MIN:g}% target)"
    return _raise_alert(patient_id, kind="adherence", detail=detail, value=f"{adherence_pct:.0f}", severity="warning")


def check_risk_score(patient_id: int, area: str, score: int) -> dict | None:
    """Wellness score is high=good; the lowest band is 'critical'. Fires a
    warning below 50 and a critical alert below 26."""
    if score >= 50:
        return None
    severity = "critical" if score < 26 else "warning"
    detail = f"{area.capitalize()} wellness score dropped to {score}/100"
    return _raise_alert(patient_id, kind="risk", detail=detail, value=str(score), vital_type=None, severity=severity)


def _recently_alerted(patient_id: int, kind: str, vital_type: str | None) -> bool:
    since = datetime.now(timezone.utc) - timedelta(hours=config.ALERT_DEDUPE_HOURS)
    row = db.query_one(
        """
        SELECT TOP 1 id FROM Alerts
        WHERE patientId = ? AND kind = ? AND raisedAt >= ?
          AND ((? IS NULL AND vitalType IS NULL) OR vitalType = ?)
        """,
        (patient_id, kind, since, vital_type, vital_type),
    )
    return row is not None


def _raise_alert(
    patient_id: int, kind: str, detail: str, value: str, vital_type: str | None = None, severity: str = "warning"
) -> dict | None:
    # Dedup: don't re-fire the same (patient, kind, vitalType) within the window.
    if _recently_alerted(patient_id, kind, vital_type):
        return None

    now = datetime.now(timezone.utc)
    alert_id = db.execute_returning_id(
        """
        INSERT INTO Alerts (patientId, kind, vitalType, severity, detail, value, raisedAt)
        OUTPUT INSERTED.id
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (patient_id, kind, vital_type, severity, detail, value, now),
    )
    alert = {
        "id": alert_id,
        "patientId": patient_id,
        "kind": kind,
        "vitalType": vital_type,
        "severity": severity,
        "detail": detail,
        "value": value,
        "raisedAt": now,
    }

    # Always inform linked caregivers; critical additionally pings the
    # patient/provider channel and records that a provider was notified.
    notifier.notify_caregivers(patient_id, alert)
    if severity == "critical":
        notifier.send_alert(patient_id, ntype="clinical_alert", title="Health alert", body=detail)
        db.execute("UPDATE Alerts SET providerNotifiedAt = ? WHERE id = ?", (now, alert_id))
    return alert


def acknowledge(alert_id: int, acknowledged_by: str) -> None:
    db.execute(
        "UPDATE Alerts SET acknowledgedBy = ?, acknowledgedAt = ? WHERE id = ?",
        (acknowledged_by, datetime.now(timezone.utc), alert_id),
    )
