"""Explainable, data-driven wellness recommendation engine (docs/BLUEPRINT.md
Part 8.0/8.2) — the deliberate, non-black-box replacement for the retired
Azure Personalizer.

Every recommendation is produced by transparent rules over the patient's own
data (risk scores, vital trends, adherence, goals, and prior feedback), each
carrying a 0-1 priority_score and a plain-English reason. A lightweight
feedback loop stands in for Personalizer's reward signal: a category the
patient recently dismissed is damped, one they acted on is boosted, so rankings
genuinely differ per patient and adapt over time. Optionally an Azure OpenAI
pass can rephrase the top items (non-diagnostic system prompt); the rules
remain the fallback and the source of truth.

This gives general wellness guidance only — never diagnosis, dosing, or
treatment (config.WELLNESS_DISCLAIMER is attached to every result).
"""
import logging
from datetime import datetime, timedelta, timezone

from . import config, db

# The eight wellness domains the engine can speak to.
CATEGORIES = (
    "Exercise",
    "Nutrition",
    "Sleep",
    "Stress",
    "Medication",
    "Hydration",
    "Screening",
    "MentalHealth",
)

# How a stored priority_score maps to the Low/Medium/High band the UI shows.
def _band(score: float) -> str:
    if score >= 0.66:
        return "High"
    if score >= 0.33:
        return "Medium"
    return "Low"


# --------------------------------------------------------------------------
# Context: everything the rules reason over, gathered once per patient.
# --------------------------------------------------------------------------

def _latest_area_scores(patient_id: int) -> dict[str, dict]:
    rows = db.query(
        """
        SELECT area, score, reason FROM RiskScores r
        WHERE patientId = ? AND computedAt = (
          SELECT MAX(computedAt) FROM RiskScores WHERE patientId = r.patientId AND area = r.area
        )
        """,
        (patient_id,),
    )
    return {r["area"]: {"score": r["score"], "reason": r["reason"]} for r in rows}


def _vital_trend(patient_id: int, vital_type: str) -> float | None:
    """Percent change of the last 7 days' average vs the prior 7 days.
    Positive = rising. None when there isn't enough data to compare."""
    rows = db.query(
        """
        SELECT CAST(recordedAt AS DATE) AS d, AVG(value) AS v
        FROM Vitals
        WHERE patientId = ? AND type = ? AND recordedAt >= ?
        GROUP BY CAST(recordedAt AS DATE) ORDER BY d
        """,
        (patient_id, vital_type, datetime.now(timezone.utc) - timedelta(days=14)),
    )
    if len(rows) < 4:
        return None
    mid = len(rows) // 2
    prior = [r["v"] for r in rows[:mid]]
    recent = [r["v"] for r in rows[mid:]]
    prior_avg = sum(prior) / len(prior)
    recent_avg = sum(recent) / len(recent)
    if prior_avg == 0:
        return None
    return (recent_avg - prior_avg) / prior_avg * 100.0


def _adherence_rate(patient_id: int, days: int = 14) -> float | None:
    row = db.query_one(
        """
        SELECT SUM(CASE WHEN a.status='taken' THEN 1 ELSE 0 END) AS taken, COUNT(*) AS total
        FROM AdherenceLog a JOIN Medications m ON m.id = a.medicationId
        WHERE m.patientId = ? AND a.dueAt >= ?
        """,
        (patient_id, datetime.now(timezone.utc) - timedelta(days=days)),
    )
    if not row or not row["total"]:
        return None
    return 100.0 * (row["taken"] or 0) / row["total"]


def _active_goals(patient_id: int) -> list[dict]:
    return db.query(
        "SELECT kind, target, progress FROM Goals WHERE patientId = ? AND target > 0", (patient_id,)
    )


def _recent_feedback(patient_id: int, days: int = 14) -> dict[str, dict]:
    """Per-category: was the most recent recommendation acted on or dismissed?
    Drives the reward-style damping/boosting below."""
    rows = db.query(
        """
        SELECT category, actedOn, dismissedAt, generatedAt FROM Recommendations r
        WHERE patientId = ? AND generatedAt >= ? AND generatedAt = (
          SELECT MAX(generatedAt) FROM Recommendations
          WHERE patientId = r.patientId AND category = r.category
        )
        """,
        (patient_id, datetime.now(timezone.utc) - timedelta(days=days)),
    )
    return {r["category"]: {"acted_on": bool(r["actedOn"]), "dismissed": r["dismissedAt"] is not None} for r in rows}


def _patient_age(patient_id: int) -> int | None:
    row = db.query_one("SELECT dob FROM Patients WHERE id = ?", (patient_id,))
    if not row or not row.get("dob"):
        return None
    dob = row["dob"]
    if isinstance(dob, str):
        try:
            dob = datetime.fromisoformat(dob).date()
        except ValueError:
            return None
    today = datetime.now(timezone.utc).date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _build_context(patient_id: int, area_scores: dict[str, dict] | None) -> dict:
    hour = datetime.now(timezone.utc).hour
    return {
        "patient_id": patient_id,
        "area_scores": area_scores or _latest_area_scores(patient_id),
        "hr_trend": _vital_trend(patient_id, "heartRate"),
        "sleep_trend": _vital_trend(patient_id, "sleep"),
        "steps_trend": _vital_trend(patient_id, "steps"),
        "adherence_rate": _adherence_rate(patient_id),
        "goals": _active_goals(patient_id),
        "feedback": _recent_feedback(patient_id),
        "age": _patient_age(patient_id),
        "time_of_day": "morning" if hour < 12 else "evening" if hour >= 17 else "afternoon",
    }


# --------------------------------------------------------------------------
# Rules: each returns (base_priority 0-1, text, reason) or None.
# --------------------------------------------------------------------------

def _rule_sleep(ctx):
    area = ctx["area_scores"].get("sleep")
    if area and area["score"] < 70:
        sev = (70 - area["score"]) / 70
        return 0.4 + 0.5 * sev, f"Aim for a consistent bedtime tonight to get back above {config.SLEEP_MIN_HOURS:g} hours.", area["reason"]
    if ctx["sleep_trend"] is not None and ctx["sleep_trend"] < -10:
        return 0.55, "Wind down 30 minutes earlier tonight — your sleep has been trending shorter.", f"Sleep down {abs(ctx['sleep_trend']):.0f}% vs the prior week"
    return None


def _rule_exercise(ctx):
    area = ctx["area_scores"].get("activity")
    step_goal = next((g for g in ctx["goals"] if g["kind"] == "fitness"), None)
    if area and area["score"] < 70:
        sev = (70 - area["score"]) / 70
        text = "Take a 15-minute walk today to help close your step goal gap."
        if ctx["time_of_day"] == "evening":
            text = "A short evening walk would help close today's step gap."
        return 0.35 + 0.5 * sev, text, area["reason"]
    if step_goal and step_goal["progress"] < step_goal["target"] * 0.6:
        return 0.5, "You're behind on your step goal — a brisk 20-minute walk would close the gap.", f"Steps at {step_goal['progress']:.0f} of {step_goal['target']:.0f} target"
    return None


def _rule_medication(ctx):
    rate = ctx["adherence_rate"]
    area = ctx["area_scores"].get("adherence")
    if rate is not None and rate < config.ADHERENCE_ALERT_MIN:
        sev = (config.ADHERENCE_ALERT_MIN - rate) / config.ADHERENCE_ALERT_MIN
        return 0.6 + 0.4 * sev, "Set a reminder for your next dose — recent doses were missed.", f"Adherence at {rate:.0f}% over the last 14 days"
    if area and area["score"] < 80:
        return 0.6, "Set a reminder for your next dose — a few missed doses were logged.", area["reason"]
    return None


def _rule_heart_stress(ctx):
    area = ctx["area_scores"].get("heart")
    if ctx["hr_trend"] is not None and ctx["hr_trend"] > 8:
        return 0.7, "Try a few minutes of paced breathing today — your resting heart rate has been climbing.", f"Resting HR up {ctx['hr_trend']:.0f}% vs the prior week", "Stress"
    if area and area["score"] < 70:
        return 0.55, "Consider lighter activity and extra hydration today.", area["reason"], "Stress"
    return None


def _rule_hydration(ctx):
    # A gentle, always-eligible low-priority nudge, stronger in the afternoon.
    base = 0.3 if ctx["time_of_day"] == "afternoon" else 0.2
    return base, "Keep a water bottle nearby — steady hydration supports energy and focus.", "General preventive-care guidance"


def _rule_nutrition(ctx):
    nutrition_goal = next((g for g in ctx["goals"] if g["kind"] == "nutrition"), None)
    if nutrition_goal and nutrition_goal["progress"] < nutrition_goal["target"] * 0.7:
        return 0.45, "Add a serving of vegetables or fruit to your next meal to move toward your nutrition goal.", f"Nutrition goal at {nutrition_goal['progress']:.0f} of {nutrition_goal['target']:.0f}"
    return 0.2, "Aim for a colourful plate at your next meal — variety covers more micronutrients.", "General preventive-care guidance"


def _rule_screening(ctx):
    age = ctx["age"]
    if age is not None and age >= 45:
        return 0.4, "You're in an age range where routine preventive screenings are recommended — consider booking a check-up.", f"Age {age}: preventive screening guidance"
    return None


def _rule_mental_health(ctx):
    # Surfaces when several wellness areas are low at once (a stress proxy).
    low_areas = [a for a, r in ctx["area_scores"].items() if a != "overall" and r["score"] < 60]
    if len(low_areas) >= 2:
        return 0.5, "Several wellness signals are low this week — a short mindfulness break or a chat with someone you trust can help.", f"{len(low_areas)} areas below target: {', '.join(low_areas)}"
    return None


# rule fn -> default category (a rule may override the category as a 4th tuple item)
_RULES = [
    (_rule_sleep, "Sleep"),
    (_rule_exercise, "Exercise"),
    (_rule_medication, "Medication"),
    (_rule_heart_stress, "Stress"),
    (_rule_nutrition, "Nutrition"),
    (_rule_hydration, "Hydration"),
    (_rule_screening, "Screening"),
    (_rule_mental_health, "MentalHealth"),
]


def _evaluate(ctx: dict) -> list[dict]:
    out = []
    for fn, default_category in _RULES:
        result = fn(ctx)
        if not result:
            continue
        score, text, reason = result[0], result[1], result[2]
        category = result[3] if len(result) > 3 else default_category

        # Feedback loop (Personalizer-reward stand-in): damp a category the
        # patient just dismissed, boost one they acted on.
        fb = ctx["feedback"].get(category)
        if fb:
            if fb["dismissed"]:
                score *= 0.4
            elif fb["acted_on"]:
                score = min(1.0, score * 1.15)

        out.append(
            {
                "patient_id": ctx["patient_id"],
                "category": category,
                "text": text,
                "reason": reason,
                "priority_score": round(max(0.0, min(1.0, score)), 3),
                "priority": _band(score),
                "source": "rule",
            }
        )
    out.sort(key=lambda r: r["priority_score"], reverse=True)
    return out


# --------------------------------------------------------------------------
# Optional Azure OpenAI rephrasing pass (rules stay the source of truth).
# --------------------------------------------------------------------------

_AOAI_SYSTEM_PROMPT = (
    "You are a general wellness assistant for a preventive-care demo app. "
    "You ONLY rephrase the provided lifestyle suggestions to be warmer and more "
    "specific. You MUST NOT diagnose, name diseases, recommend medication changes, "
    "dosing, or any clinical action. Keep each suggestion to one sentence and keep "
    "its meaning. Return one suggestion per line, same order, no numbering."
)


def _aoai_rephrase(recs: list[dict]) -> None:
    if not config.AZURE_OPENAI_ENDPOINT or not recs:
        return
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.AZURE_OPENAI_KEY,
            api_version="2024-10-21",
        )
        joined = "\n".join(r["text"] for r in recs)
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _AOAI_SYSTEM_PROMPT},
                {"role": "user", "content": joined},
            ],
            max_tokens=300,
            temperature=0.4,
        )
        lines = [ln.strip() for ln in response.choices[0].message.content.splitlines() if ln.strip()]
        for rec, line in zip(recs, lines):
            rec["text"] = line
            rec["source"] = "llm"
    except Exception as exc:  # AOAI unavailable/misconfigured — keep rules text
        logging.warning("AOAI rephrase failed, keeping rules text: %s", exc)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def get_recommendations(patient_id: int, limit: int = 5, area_scores: dict[str, dict] | None = None) -> list[dict]:
    """Ranked, personalized recommendations for a patient (no persistence)."""
    ctx = _build_context(patient_id, area_scores)
    recs = _evaluate(ctx)[:limit]
    if not recs:
        recs = [
            {
                "patient_id": patient_id,
                "category": "Exercise",
                "text": "Keep up your current routine — your wellness signals look steady this week.",
                "reason": "All tracked areas are at or above target this week",
                "priority_score": 0.1,
                "priority": "Low",
                "source": "rule",
            }
        ]
    if config.USE_AOAI_RECOMMENDATIONS:
        _aoai_rephrase(recs)
    for r in recs:
        r["disclaimer"] = config.WELLNESS_DISCLAIMER
    return recs


def generate_and_store(patient_id: int, area_scores: dict[str, dict] | None = None, limit: int = 5) -> list[dict]:
    """Compute ranked recommendations and persist them (called by compute_risk
    and the regenerate endpoint). Returns the stored recommendations."""
    recs = get_recommendations(patient_id, limit=limit, area_scores=area_scores)
    now = datetime.now(timezone.utc)
    for rec in recs:
        db.execute(
            """
            INSERT INTO Recommendations (patientId, text, reason, category, priority, priorityScore, source, generatedAt, actedOn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (rec["patient_id"], rec["text"], rec["reason"], rec["category"], rec["priority"], rec["priority_score"], rec["source"], now),
        )
        rec["generated_at"] = now
    return recs
