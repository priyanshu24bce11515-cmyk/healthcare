"""Medication adherence intelligence (docs/BLUEPRINT.md Part 8.4).

Beyond simple reminders: computes per-medication adherence % and detects
schedule-slot patterns (e.g. "night doses missed more often than morning").

Adherence is taken / (taken + missed) over decided doses only — 'pending'
rows (due but not yet resolved, see med_reminders' 2-hour grace window) are
excluded from both numerator and denominator, and 'as_needed' medications are
excluded entirely (they have no due schedule to be adherent to).
"""
from .. import db

_DECIDED_STATUSES = ("taken", "missed")


def per_medication_adherence(patient_id: int) -> list[dict]:
    rows = db.query(
        """
        SELECT
          m.id AS medicationId, m.name, m.schedule,
          SUM(CASE WHEN a.status = 'taken' THEN 1 ELSE 0 END) AS taken,
          SUM(CASE WHEN a.status = 'missed' THEN 1 ELSE 0 END) AS missed
        FROM Medications m
        JOIN AdherenceLog a ON a.medicationId = m.id
        WHERE m.patientId = ? AND m.schedule != 'as_needed' AND a.status IN ('taken', 'missed')
        GROUP BY m.id, m.name, m.schedule
        """,
        (patient_id,),
    )
    for r in rows:
        total = (r["taken"] or 0) + (r["missed"] or 0)
        r["total"] = total
        r["adherencePct"] = round(100 * r["taken"] / total, 1) if total else 100.0
    return rows


def detect_schedule_slot_pattern(patient_id: int) -> str | None:
    """Compares adherence by schedule slot (morning/afternoon/night/daily/
    twice_daily) and surfaces the weakest slot if it's meaningfully worse."""
    rows = db.query(
        """
        SELECT
          m.schedule,
          SUM(CASE WHEN a.status = 'taken' THEN 1 ELSE 0 END) AS taken,
          COUNT(*) AS total
        FROM Medications m
        JOIN AdherenceLog a ON a.medicationId = m.id
        WHERE m.patientId = ? AND m.schedule != 'as_needed' AND a.status IN ('taken', 'missed')
        GROUP BY m.schedule
        """,
        (patient_id,),
    )
    slot_rates = {
        r["schedule"]: 100 * r["taken"] / r["total"]
        for r in rows
        if r["total"] >= 3  # enough samples to call it a pattern
    }
    if len(slot_rates) < 2:
        return None

    worst_slot = min(slot_rates, key=slot_rates.get)
    best_slot = max(slot_rates, key=slot_rates.get)
    gap = slot_rates[best_slot] - slot_rates[worst_slot]
    if gap < 15:  # not a meaningful pattern
        return None

    return (
        f"{worst_slot.capitalize()} medications are missed more often than "
        f"{best_slot} ones ({slot_rates[worst_slot]:.0f}% vs {slot_rates[best_slot]:.0f}% adherence)."
    )


def detect_weekday_pattern(patient_id: int) -> str | None:
    rows = db.query(
        """
        SELECT
          DATENAME(WEEKDAY, a.dueAt) AS weekday,
          SUM(CASE WHEN a.status = 'taken' THEN 1 ELSE 0 END) AS taken,
          COUNT(*) AS total
        FROM Medications m
        JOIN AdherenceLog a ON a.medicationId = m.id
        WHERE m.patientId = ? AND m.schedule != 'as_needed' AND a.status IN ('taken', 'missed')
        GROUP BY DATENAME(WEEKDAY, a.dueAt)
        """,
        (patient_id,),
    )
    day_rates = {r["weekday"]: 100 * r["taken"] / r["total"] for r in rows if r["total"] >= 2}
    if len(day_rates) < 3:
        return None

    worst_day = min(day_rates, key=day_rates.get)
    if day_rates[worst_day] >= 70:
        return None
    return f"Doses are most often missed on {worst_day}s ({day_rates[worst_day]:.0f}% adherence)."


def summary(patient_id: int) -> dict:
    return {
        "medications": per_medication_adherence(patient_id),
        "patterns": [
            p
            for p in (detect_schedule_slot_pattern(patient_id), detect_weekday_pattern(patient_id))
            if p
        ],
    }
