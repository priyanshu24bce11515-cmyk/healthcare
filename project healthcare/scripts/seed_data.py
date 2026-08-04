"""Generates synthetic patients, providers, caregivers, vitals, medications,
adherence history, and goals so the demo looks alive on first run
(docs/BLUEPRINT.md Appendix B). Synthetic data only — never real PHI.

Usage:
    python scripts/seed_data.py --reset

Requires SQL_CONNECTION_STRING to be set in the environment (same variable
the Function App reads) and pyodbc's ODBC Driver 18 for SQL Server installed.
"""
import argparse
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))
from shared import db  # noqa: E402

random.seed(42)

VITAL_DAYS = 14
HISTORY_DAYS = 14  # days of back-dated Risk Score history so trend charts draw real lines
SLOT_TIME = {"morning": time(8, 0), "afternoon": time(13, 0), "night": time(20, 0)}

# AuditLog is intentionally NOT reset — it is append-only (a DB trigger blocks
# DELETE) and HIPAA requires audit records be retained, not wiped between runs.
TABLES_IN_DELETE_ORDER = [
    "Notifications", "Alerts", "Claims", "RiskScores", "Recommendations", "Goals",
    "Appointments", "AdherenceLog", "Medications", "Vitals", "Caregivers",
    "Providers", "Patients",
]

# Curated Telugu names (synthetic — never real PHI). Full names are stored in
# the single `name` column; deterministic email is derived from the name so it
# is easy to type into the real-auth "claim by contact" onboarding form.
# Ten providers, ten patients, ten caregivers — one linkable email of each kind.
PROVIDERS = [
    ("Dr. Ramesh Kondaveeti", "General Medicine"),
    ("Dr. Lakshmi Nalluri", "Cardiology"),
    ("Dr. Srinivas Gollapudi", "Endocrinology"),
    ("Dr. Padmavathi Devarakonda", "Psychiatry"),
    ("Dr. Naveen Tummala", "General Medicine"),
    ("Dr. Swathi Vempati", "Cardiology"),
    ("Dr. Bhaskar Alluri", "Endocrinology"),
    ("Dr. Sirisha Kolli", "Psychiatry"),
    ("Dr. Mohan Bandari", "General Medicine"),
    ("Dr. Divya Medapati", "Cardiology"),
]

# (name, sex, dob) — dob is fixed so the demo is reproducible run to run.
PATIENTS = [
    ("Saikiran Vanaparthi", "male", date(1958, 4, 12)),
    ("Anusha Kambhampati", "female", date(1963, 9, 3)),
    ("Venkata Ramana Pallapothu", "male", date(1971, 1, 27)),
    ("Sravani Yellapragada", "female", date(1988, 6, 15)),
    ("Karthik Bhogaraju", "male", date(1995, 11, 8)),
    ("Haritha Chintalapati", "female", date(1966, 2, 21)),
    ("Praveen Duvvuri", "male", date(1979, 8, 30)),
    ("Sailaja Gadiraju", "female", date(1954, 12, 5)),
    ("Nagarjuna Indukuri", "male", date(1983, 3, 19)),
    ("Kavya Jasti", "female", date(1992, 7, 24)),
]

# (name, relationship) — one caregiver linked to each patient, in order.
CAREGIVERS = [
    ("Ravi Teja Vanaparthi", "son"),
    ("Padma Kambhampati", "daughter"),
    ("Sujatha Pallapothu", "spouse"),
    ("Manohar Yellapragada", "son"),
    ("Deepthi Bhogaraju", "daughter"),
    ("Suresh Chintalapati", "spouse"),
    ("Navya Duvvuri", "daughter"),
    ("Chandra Sekhar Gadiraju", "son"),
    ("Bhavani Indukuri", "spouse"),
    ("Vijaya Jasti", "daughter"),
]

PROVIDER_SPECIALTIES = ["General Medicine", "Cardiology", "Endocrinology", "Psychiatry"]
MED_POOL = [
    ("Metformin", "500mg", "morning"),
    ("Vitamin D", "1000IU", "night"),
    ("Lisinopril", "10mg", "morning"),
    ("Atorvastatin", "20mg", "night"),
]


def _email(name: str) -> str:
    """Deterministic, easy-to-type Indian-domain email from a full name, e.g.
    'Dr. Ramesh Kondaveeti' -> 'ramesh.kondaveeti@p63care.in'."""
    slug = name.lower().replace("dr. ", "").strip()
    slug = ".".join(slug.split())
    return f"{slug}@p63care.in"


def reset_tables() -> None:
    print("Clearing existing demo data...")
    for table in TABLES_IN_DELETE_ORDER:
        db.execute(f"DELETE FROM {table}")
        # DELETE doesn't reset the IDENTITY seed, so without this a second
        # --reset run would produce patients starting at id 6, 11, ... instead
        # of always restarting at 1 (which the frontend's demo login assumes).
        db.execute(f"DBCC CHECKIDENT ('{table}', RESEED, 0)")


def seed_providers(n: int) -> list[tuple[int, str]]:
    """Contacts are deterministic (name-based, not random) so they're easy to
    type into the real-auth onboarding screen's "claim by contact" form during
    a live demo."""
    rows = []
    for name, specialty in PROVIDERS[:n]:
        contact = _email(name)
        pid = db.execute_returning_id(
            "INSERT INTO Providers (name, specialty, contact) OUTPUT INSERTED.id VALUES (?, ?, ?)",
            (name, specialty, contact),
        )
        rows.append((pid, contact))
    print(f"Seeded {len(rows)} providers (all unclaimed - claim via POST /providers/claim)")
    return rows


def seed_patients(n: int) -> list[tuple[int, str]]:
    rows = []
    for name, sex, dob in PATIENTS[:n]:
        contact = _email(name)
        pid = db.execute_returning_id(
            """
            INSERT INTO Patients (name, dob, sex, contact)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?)
            """,
            (name, dob, sex, contact),
        )
        rows.append((pid, contact))
    print(f"Seeded {len(rows)} patients (all unclaimed - claim via POST /patients/claim)")
    return rows


def seed_caregivers(patient_ids: list[int]) -> list[tuple[int, str]]:
    """One caregiver per patient. Only the first is pre-claimed, for the
    demo-mode role switcher (web/src/lib/auth.tsx always sends userId
    "demo-caregiver-1" for the caregiver role) — the rest stay unclaimed so the
    real-auth claim-by-contact flow has plenty to exercise end to end."""
    rows = []
    for i, pid in enumerate(patient_ids):
        name, relationship = CAREGIVERS[i]
        contact = _email(name)
        principal_user_id = "demo-caregiver-1" if i == 0 else None
        caregiver_id = db.execute_returning_id(
            """
            INSERT INTO Caregivers (name, contact, patientId, relationship, accessScope, principalUserId)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, contact, pid, relationship, "vitals,adherence,alerts", principal_user_id),
        )
        rows.append((caregiver_id, contact))
    print(f"Linked caregivers for {len(rows)} patients (first pre-claimed for demo mode, rest unclaimed)")
    return rows


def seed_goals(patient_ids: list[int]) -> None:
    for pid in patient_ids:
        db.execute(
            "INSERT INTO Goals (patientId, kind, target, progress, period) VALUES (?, 'fitness', ?, ?, 'weekly')",
            (pid, 8000, random.randint(3000, 8000)),
        )
        db.execute(
            "INSERT INTO Goals (patientId, kind, target, progress, period) VALUES (?, 'nutrition', ?, ?, 'daily')",
            (pid, 8, random.randint(3, 8)),
        )


def seed_vitals(patient_ids: list[int]) -> None:
    now = datetime.now(timezone.utc)
    for idx, pid in enumerate(patient_ids):
        # First patient gets a rough week (short sleep, missed steps, rising HR,
        # BP/glucose spikes) so alerts, risk scores, and recommendations are all visible.
        rough_week = idx == 0
        resting_hr = 68.0

        for day_offset in range(VITAL_DAYS, -1, -1):
            day = now - timedelta(days=day_offset)
            recent = day_offset <= 6

            sleep_hours = round(random.uniform(5.0 if (rough_week and recent) else 6.5, 6.8 if (rough_week and recent) else 8.3), 1)
            db.execute(
                "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source) VALUES (?, 'sleep', ?, 'hours', ?, 'simulated')",
                (pid, sleep_hours, day.replace(hour=7, minute=0)),
            )

            steps = random.randint(2500, 5500) if (rough_week and recent) else random.randint(5000, 11000)
            db.execute(
                "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source) VALUES (?, 'steps', ?, 'count', ?, 'simulated')",
                (pid, steps, day.replace(hour=21, minute=0)),
            )

            if rough_week and recent:
                resting_hr += random.uniform(0.5, 2.5)  # trending up
            else:
                resting_hr = 68.0 + random.uniform(-3, 3)
            db.execute(
                "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source) VALUES (?, 'heartRate', ?, 'bpm', ?, 'simulated')",
                (pid, round(resting_hr, 1), day.replace(hour=6, minute=30)),
            )

            sys_bp = random.randint(142, 152) if (rough_week and day_offset == 1) else random.randint(110, 128)
            dia_bp = random.randint(90, 98) if (rough_week and day_offset == 1) else random.randint(70, 84)
            db.execute(
                "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source) VALUES (?, 'bp_systolic', ?, 'mmHg', ?, 'simulated')",
                (pid, sys_bp, day.replace(hour=9, minute=0)),
            )
            db.execute(
                "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source) VALUES (?, 'bp_diastolic', ?, 'mmHg', ?, 'simulated')",
                (pid, dia_bp, day.replace(hour=9, minute=0)),
            )

            glucose = random.randint(185, 205) if (rough_week and day_offset == 2) else random.randint(85, 115)
            db.execute(
                "INSERT INTO Vitals (patientId, type, value, unit, recordedAt, source) VALUES (?, 'glucose', ?, 'mg/dL', ?, 'simulated')",
                (pid, glucose, day.replace(hour=8, minute=0)),
            )
    print(f"Seeded {VITAL_DAYS + 1} days of vitals for {len(patient_ids)} patients")


def seed_medications_and_adherence(patient_ids: list[int]) -> None:
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=VITAL_DAYS)).date()

    for idx, pid in enumerate(patient_ids):
        meds = random.sample(MED_POOL, k=2)
        # Patient 0 gets the "night doses missed more than morning" pattern from Part 8.4.
        night_miss_bias = idx == 0

        for name, dosage, schedule in meds:
            med_id = db.execute_returning_id(
                """
                INSERT INTO Medications (patientId, name, dosage, schedule, startDate)
                OUTPUT INSERTED.id
                VALUES (?, ?, ?, ?, ?)
                """,
                (pid, name, dosage, schedule, start_date),
            )

            for day_offset in range(VITAL_DAYS, -1, -1):
                day = (now - timedelta(days=day_offset)).date()
                due_at = datetime.combine(day, SLOT_TIME[schedule], tzinfo=timezone.utc)

                miss_chance = 0.05
                if night_miss_bias and schedule == "night":
                    miss_chance = 0.30
                elif not night_miss_bias:
                    miss_chance = 0.12

                taken = random.random() > miss_chance
                status = "taken" if taken else "missed"
                taken_at = due_at + timedelta(minutes=random.randint(0, 45)) if taken else None
                db.execute(
                    "INSERT INTO AdherenceLog (medicationId, dueAt, takenAt, status) VALUES (?, ?, ?, ?)",
                    (med_id, due_at, taken_at, status),
                )
    print(f"Seeded medications + {VITAL_DAYS + 1} days of adherence history for {len(patient_ids)} patients")


def seed_scores_and_history(patient_ids: list[int]) -> None:
    """Computes today's Risk Scores + recommendations for each patient, then
    back-dates HISTORY_DAYS of daily scores (a gentle random walk anchored on
    today's value) so the Analytics trend charts render real lines instead of a
    single dot. Also computes recommendations from the latest scores."""
    from shared.recommend import generate_and_store
    from shared.scoring.risk_score import compute_and_store

    now = datetime.now(timezone.utc)
    for pid in patient_ids:
        scores = compute_and_store(pid)  # today's row per area
        for area, info in scores.items():
            current = info["score"]
            val = float(current)
            # Walk backwards day by day with small steps for a natural trend.
            for day_offset in range(1, HISTORY_DAYS + 1):
                val = max(2.0, min(100.0, val + random.gauss(0, 3.0)))
                day = (now - timedelta(days=day_offset)).replace(hour=2, minute=0, second=0, microsecond=0)
                db.execute(
                    "INSERT INTO RiskScores (patientId, area, score, reason, computedAt) VALUES (?, ?, ?, ?, ?)",
                    (pid, area, round(val), "Daily wellness score", day),
                )
        latest = {area: {"score": info["score"], "reason": info["reason"]} for area, info in scores.items()}
        generate_and_store(pid, latest)
    print(f"Computed scores + {HISTORY_DAYS}d history + recommendations for {len(patient_ids)} patients")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic demo data for the P63 platform")
    parser.add_argument("--reset", action="store_true", help="Delete existing demo data before seeding")
    parser.add_argument("--patients", type=int, default=10, help="Number of synthetic patients to create (max 10)")
    args = parser.parse_args()

    n = max(1, min(args.patients, len(PATIENTS)))
    if n != args.patients:
        print(f"Clamping --patients {args.patients} -> {n} (only {len(PATIENTS)} curated Telugu names available)")

    if args.reset:
        reset_tables()

    providers = seed_providers(n)
    patients = seed_patients(n)
    patient_ids = [pid for pid, _ in patients]
    caregivers = seed_caregivers(patient_ids)
    seed_goals(patient_ids)
    seed_vitals(patient_ids)
    seed_medications_and_adherence(patient_ids)
    seed_scores_and_history(patient_ids)

    print("\nDone. Patient IDs:", patient_ids)
    print("Provider IDs:", [pid for pid, _ in providers])
    print('\nClaim-by-contact cheat sheet (real-auth onboarding - POST {table}/claim {"contact": ...}):')
    for pid, contact in providers:
        print(f"  provider #{pid}: {contact}")
    for pid, contact in patients:
        print(f"  patient  #{pid}: {contact}")
    for cid, contact in caregivers:
        print(f"  caregiver #{cid}: {contact}")
    print("\nNext: run `python scripts/device_simulator.py` to keep streaming live vitals,")
    print("and POST a sample note from scripts/sample_clinical_notes.py to /claims.")


if __name__ == "__main__":
    main()
