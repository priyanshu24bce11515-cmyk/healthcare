"""Health Risk Score — explainable, rules-based (docs/BLUEPRINT.md Part 8.1).

Every area starts at 100 and loses points for adverse signals. Each score
carries the single biggest contributing reason. This is a lifestyle/wellness
pattern score, NOT a clinical risk prediction or diagnosis.
"""
from datetime import datetime, timedelta, timezone

from .. import config, db

LOOKBACK_DAYS = 7

AREA_WEIGHTS = {"sleep": 0.25, "activity": 0.25, "adherence": 0.30, "heart": 0.20}


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _vitals(patient_id: int, vital_type: str, days: int = LOOKBACK_DAYS) -> list[dict]:
    """One row per calendar day, not one row per raw reading — a live wearable
    feed can post a vital many times a day, but the scoring rules below reason
    in day-granularity ("N nights", "N consecutive days"). Cumulative vitals
    (steps) are summed per day; point-in-time vitals (sleep/heartRate/bp/
    glucose) use the day's last reading.
    """
    if vital_type in config.CUMULATIVE_VITAL_TYPES:
        return db.query(
            """
            SELECT SUM(value) AS value, CAST(recordedAt AS DATE) AS recordedAt
            FROM Vitals
            WHERE patientId = ? AND type = ? AND recordedAt >= ?
            GROUP BY CAST(recordedAt AS DATE)
            ORDER BY recordedAt ASC
            """,
            (patient_id, vital_type, _since(days)),
        )

    return db.query(
        """
        SELECT value, recordedAt FROM (
            SELECT value, recordedAt,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST(recordedAt AS DATE) ORDER BY recordedAt DESC
                   ) AS rn
            FROM Vitals
            WHERE patientId = ? AND type = ? AND recordedAt >= ?
        ) AS daily_last
        WHERE rn = 1
        ORDER BY recordedAt ASC
        """,
        (patient_id, vital_type, _since(days)),
    )


def score_sleep(patient_id: int) -> tuple[int, str]:
    rows = _vitals(patient_id, "sleep")
    short_nights = [r for r in rows if r["value"] < config.SLEEP_MIN_HOURS]
    score = max(0, 100 - 10 * len(short_nights))
    if not rows:
        return 100, "No sleep data recorded yet"
    if short_nights:
        return score, f"Sleep below {config.SLEEP_MIN_HOURS:g} hours for {len(short_nights)} of last {len(rows)} nights"
    return score, "Sleep consistently at or above target"


def score_activity(patient_id: int) -> tuple[int, str]:
    rows = _vitals(patient_id, "steps")
    goal_row = db.query_one(
        "SELECT TOP 1 target FROM Goals WHERE patientId = ? AND kind = 'fitness' ORDER BY id DESC",
        (patient_id,),
    )
    goal = goal_row["target"] if goal_row else config.STEP_GOAL_DEFAULT
    missed_days = [r for r in rows if r["value"] < goal]
    score = max(0, 100 - 8 * len(missed_days))
    if not rows:
        return 100, "No activity data recorded yet"
    if missed_days:
        return score, f"Step goal ({goal:g}) missed {len(missed_days)} of last {len(rows)} days"
    return score, "Step goal met every day this week"


def score_adherence(patient_id: int) -> tuple[int, str]:
    row = db.query_one(
        """
        SELECT
          SUM(CASE WHEN a.status = 'taken' THEN 1 ELSE 0 END) AS taken,
          COUNT(*) AS total
        FROM AdherenceLog a
        JOIN Medications m ON m.id = a.medicationId
        WHERE m.patientId = ? AND a.dueAt >= ?
        """,
        (patient_id, _since(LOOKBACK_DAYS)),
    )
    total = row["total"] if row else 0
    taken = row["taken"] if row and row["taken"] else 0
    if not total:
        return 100, "No medications scheduled this week"
    score = round(100 * taken / total)
    missed = total - taken
    if missed:
        return score, f"Missed {missed} of {total} scheduled doses this week"
    return score, "All scheduled doses taken this week"


def score_heart(patient_id: int) -> tuple[int, str]:
    rows = _vitals(patient_id, "heartRate")
    if len(rows) < 2:
        return 100, "Not enough resting heart rate data yet"

    rising_streak = 0
    for prev, curr in zip(rows, rows[1:]):
        if curr["value"] > prev["value"]:
            rising_streak += 1
        else:
            rising_streak = 0

    score = max(0, 100 - 7 * rising_streak)
    if rising_streak >= 2:
        return score, f"Resting heart rate increased for {rising_streak} consecutive days"

    latest = rows[-1]["value"]
    if latest > config.HR_ALERT_MAX:
        return max(0, score - 15), f"Latest resting heart rate ({latest:g} bpm) above normal range"
    return score, "Heart rate trend stable"


def compute_overall(area_scores: dict[str, int]) -> int:
    total_weight = sum(AREA_WEIGHTS[a] for a in area_scores)
    if not total_weight:
        return 100
    weighted = sum(area_scores[a] * AREA_WEIGHTS[a] for a in area_scores)
    return round(weighted / total_weight)


# Wellness-score bands (this is a wellness score: HIGH = healthy). The lowest
# band is treated as "critical" and auto-raises an alert (see compute_risk).
def band(score: int) -> str:
    if score >= 76:
        return "good"
    if score >= 51:
        return "moderate"
    if score >= 26:
        return "high"
    return "critical"


def _overall_adjustments(patient_id: int) -> tuple[int, list[str]]:
    """Cross-cutting safety signals that lower the overall wellness score
    beyond the per-area averages: unacknowledged alerts pile up, and stale
    data means we may be flying blind. Returns (deduction, notes)."""
    deduction = 0
    notes: list[str] = []

    unacked = db.query_one(
        "SELECT COUNT(*) AS n FROM Alerts WHERE patientId = ? AND acknowledgedAt IS NULL AND raisedAt >= ?",
        (patient_id, _since(7)),
    )
    n_unacked = (unacked or {}).get("n", 0)
    if n_unacked:
        d = min(15, 5 * n_unacked)
        deduction += d
        notes.append(f"{n_unacked} unacknowledged alert(s) this week")

    last_vital = db.query_one(
        "SELECT MAX(recordedAt) AS last FROM Vitals WHERE patientId = ?", (patient_id,)
    )
    last = (last_vital or {}).get("last")
    if last is not None:
        # SQL Server DATETIME2 comes back tz-naive (stored as UTC); compare naive-to-naive.
        last_naive = last.replace(tzinfo=None) if last.tzinfo else last
        hours = (datetime.now(timezone.utc).replace(tzinfo=None) - last_naive).total_seconds() / 3600
        if hours > 48:
            deduction += 10
            notes.append(f"No vitals recorded in {hours / 24:.0f} days")

    return deduction, notes


def compute_and_store(patient_id: int) -> dict[str, dict]:
    """Computes all area scores, stores each to RiskScores, returns
    {area: {score, reason, band}}. The per-area rows ARE the component
    breakdown that trend reporting reads back."""
    scorers = {
        "sleep": score_sleep,
        "activity": score_activity,
        "adherence": score_adherence,
        "heart": score_heart,
    }
    results = {area: fn(patient_id) for area, fn in scorers.items()}

    area_scores = {area: score for area, (score, _reason) in results.items()}
    overall = compute_overall(area_scores)
    worst_area = min(area_scores, key=area_scores.get)
    overall_reason = f"Lowest-scoring area: {worst_area} ({results[worst_area][1]})"

    deduction, notes = _overall_adjustments(patient_id)
    if deduction:
        overall = max(0, overall - deduction)
        overall_reason += " · " + "; ".join(notes)
    results["overall"] = (overall, overall_reason)

    now = datetime.now(timezone.utc)
    for area, (score, reason) in results.items():
        db.execute(
            "INSERT INTO RiskScores (patientId, area, score, reason, computedAt) VALUES (?, ?, ?, ?, ?)",
            (patient_id, area, score, reason, now),
        )

    return {area: {"score": score, "reason": reason, "band": band(score)} for area, (score, reason) in results.items()}
