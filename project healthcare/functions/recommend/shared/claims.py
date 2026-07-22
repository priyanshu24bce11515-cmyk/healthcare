"""Onboarding: binding a verified signed-in identity to an existing, unclaimed
Patients/Providers/Caregivers row (docs/BLUEPRINT.md Part 1, Part 16 step 9).

Entra External ID / AD B2C only proves *who* someone is — it carries no
app-specific role. New rows are created unclaimed (by a provider registering
a patient, a patient linking a caregiver, or seed data); a freshly-signed-in
principal with no resolved role yet calls a claim route to bind their
identity to one of those rows by the contact info on file for it.
"""
from . import db
from .auth import BadRequest, Forbidden, Principal, resolve_role_from_db

# Patients/Providers are exclusive: one identity claims at most one such row.
# Caregivers are additive (one identity may legitimately be linked to several
# patients) — not listed here, so no exclusivity check applies to it.
_EXCLUSIVE_TABLE_ROLE = {"Patients": "patient", "Providers": "provider"}


def claim_by_contact(principal: Principal, table: str, owner_col: str, contact: str) -> dict:
    """Claims the {table} row matching `contact`, binding it to
    `principal.user_id`. Atomic (the UPDATE's WHERE clause is the only race
    guard — no separate SELECT-then-UPDATE), and re-claiming your own row is
    an idempotent success rather than an error.
    """
    row = db.query_one(f"SELECT id FROM {table} WHERE contact = ?", (contact,))
    if not row:
        raise BadRequest("No record found for that contact")
    row_id = row["id"]

    exclusive_role = _EXCLUSIVE_TABLE_ROLE.get(table)
    if exclusive_role:
        existing_role, _ = resolve_role_from_db(principal.user_id)
        if existing_role and existing_role != exclusive_role:
            raise Forbidden(f"This identity is already linked as a {existing_role}")
        already_owned = db.query_one(f"SELECT id FROM {table} WHERE {owner_col} = ?", (principal.user_id,))
        if already_owned and already_owned["id"] != row_id:
            raise Forbidden(f"This identity already claimed a different {table} record")

    updated = db.execute(
        f"UPDATE {table} SET {owner_col} = ? WHERE id = ? AND ({owner_col} IS NULL OR {owner_col} = ?)",
        (principal.user_id, row_id, principal.user_id),
    )
    if not updated:
        raise Forbidden("This record has already been claimed by someone else")

    return {"id": row_id, "claimed": True}
