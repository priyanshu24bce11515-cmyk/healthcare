"""DB-backed attempt throttling for the account-claim routes (POST
/patients/claim, /providers/claim, /caregivers/claim) — the closest thing
this app has to a login endpoint. A freshly-authenticated-but-unclaimed
Entra External ID identity calls these with a guessed `contact` email to
bind itself to an existing record; without a limit, that's an email-
enumeration/account-takeover oracle (each response reveals whether a given
contact exists and is unclaimed).

Azure Functions on the Consumption plan has no shared in-process memory
across instances (each cold start is a fresh process, and load can fan out
to many concurrent instances), so an in-memory counter would silently
under-count. AuditLog is already the durable, shared store every instance
reads/writes to, and every claim attempt (success or failure) is recorded
there — so we count recent attempts from it instead of standing up a new
table for this alone.
"""
from datetime import datetime, timedelta, timezone

from . import db
from .auth import RateLimited

# 10 attempts per 15 minutes per signed-in identity is generous enough for a
# genuine user who mistyped their contact email a few times, tight enough to
# make guessing another person's contact impractical.
MAX_CLAIM_ATTEMPTS = 10
CLAIM_WINDOW_MINUTES = 15


def enforce_claim_attempts(user_id: str) -> None:
    since = datetime.now(timezone.utc) - timedelta(minutes=CLAIM_WINDOW_MINUTES)
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM AuditLog WHERE actorId = ? AND action LIKE 'claim_%' AND at >= ?",
        (user_id, since),
    )
    if row and row["n"] >= MAX_CLAIM_ATTEMPTS:
        raise RateLimited("Too many account-claim attempts — please wait a few minutes and try again")
