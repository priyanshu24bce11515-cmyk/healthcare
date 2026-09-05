import { useState } from "react";
import { Card } from "../components/Card";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Role } from "../lib/types";

const CLAIM_ROUTES: Record<Role, string> = {
  patient: "/patients/claim",
  provider: "/providers/claim",
  caregiver: "/caregivers/claim",
};

const ROLE_LABELS: Record<Role, string> = {
  patient: "Patient",
  provider: "Provider",
  caregiver: "Caregiver",
};

export function Onboarding() {
  const { authHeader, refreshMe, logout } = useAuth();
  const [role, setRole] = useState<Role>("patient");
  const [contact, setContact] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const claim = async () => {
    if (!contact) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.post(CLAIM_ROUTES[role], authHeader, { contact });
      refreshMe();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-4 pt-8">
      <div>
        <h1 className="text-2xl font-semibold">Link your account</h1>
        <p className="mt-2 text-sm text-ink-secondary">
          You're signed in, but this identity isn't linked to a patient, provider, or caregiver record yet. Enter
          the contact email your care team has on file to link it.
        </p>
      </div>
      <Card>
        <div className="flex flex-col gap-3">
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          >
            {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
          <input
            placeholder="Contact email on file"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          />
          <button
            onClick={claim}
            disabled={submitting || !contact}
            className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
          >
            {submitting ? "Linking…" : "Link account"}
          </button>
          {error && <p className="text-sm text-status-critical">{error}</p>}
        </div>
      </Card>
      <button onClick={logout} className="self-start text-sm text-ink-secondary underline hover:text-ink-primary">
        Signed into the wrong account? Sign out
      </button>
    </div>
  );
}
