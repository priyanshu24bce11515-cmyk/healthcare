import { HeartHandshake } from "lucide-react";
import { useState } from "react";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { RequireActivePatient } from "../components/RequireActivePatient";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Caregiver as CaregiverType } from "../lib/types";
import { useFetch } from "../lib/useFetch";

interface CaregiverView {
  patientId: number;
  accessScope: string[];
  accessLevel: "view_only" | "full";
  recentVitals?: { type: string; value: number; unit: string; recordedAt: string }[];
  adherence?: { name: string; schedule: string; taken: number; total: number }[];
  alerts?: { id: number; kind: string; detail: string; raisedAt: string; acknowledgedBy: string | null }[];
}

function PatientCaregiverManager() {
  const { principal, authHeader } = useAuth();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [relationship, setRelationship] = useState("family");
  const [accessLevel, setAccessLevel] = useState<"view_only" | "full">("view_only");
  const [submitting, setSubmitting] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const { data, loading, error, reload } = useFetch(
    (header) => api.get<CaregiverType[]>(`/caregivers/${principal.patientId}`, header),
    [principal.patientId],
  );

  const linkCaregiver = async () => {
    if (!name || !contact) return;
    setSubmitting(true);
    setLinkError(null);
    try {
      await api.post("/caregivers", authHeader, {
        name,
        contact,
        relationship,
        accessScope: "vitals,adherence,alerts",
        accessLevel,
      });
      setName("");
      setContact("");
      reload();
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Card title="Link a caregiver">
        <div className="flex flex-wrap items-end gap-2">
          <input
            placeholder="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          />
          <input
            placeholder="Email"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          />
          <select
            value={relationship}
            onChange={(e) => setRelationship(e.target.value)}
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          >
            <option value="family">Family</option>
            <option value="son">Son</option>
            <option value="daughter">Daughter</option>
            <option value="spouse">Spouse</option>
          </select>
          <select
            value={accessLevel}
            onChange={(e) => setAccessLevel(e.target.value as "view_only" | "full")}
            title="Whether this caregiver can only view your data, or also act on your behalf (acknowledge alerts, manage appointments)"
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          >
            <option value="view_only">View only</option>
            <option value="full">Full (can act on my behalf)</option>
          </select>
          <button
            onClick={linkCaregiver}
            disabled={submitting}
            className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
          >
            {submitting ? "Linking…" : "Link caregiver"}
          </button>
        </div>
        {linkError && <p className="mt-2 text-sm text-status-critical">{linkError}</p>}
      </Card>

      <Card title="Linked caregivers">
        {loading && <p className="text-sm text-ink-secondary">Loading…</p>}
        {error && <ErrorState message={error} onRetry={reload} />}
        <ul className="flex flex-col gap-2 text-sm">
          {data?.map((cg) => (
            <li key={cg.id} className="flex items-center justify-between gap-3">
              <span>
                <span className="font-medium">{cg.name}</span> ({cg.relationship}) — access: {cg.accessScope} ·{" "}
                {cg.accessLevel === "full" ? "full" : "view only"}
              </span>
              <span className={cg.accepted ? "text-xs text-status-good" : "text-xs text-status-warning"}>
                {cg.accepted ? "Accepted" : "Invitation pending"}
              </span>
            </li>
          ))}
          {data?.length === 0 && <p className="text-ink-secondary">No caregivers linked yet.</p>}
        </ul>
      </Card>
    </div>
  );
}

function CaregiverScopedView() {
  const { authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const { data, loading, error, reload } = useFetch(
    (header) => api.get<CaregiverView>(`/caregiver-view/${patientId}`, header),
    [patientId],
  );

  const acknowledge = async (alertId: number) => {
    await api.post(`/alerts/${alertId}/acknowledge`, authHeader);
    reload();
  };

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const canAct = data.accessLevel === "full";

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-ink-muted">
        Scoped access: {data.accessScope.join(", ")} · {canAct ? "full (can act on the patient's behalf)" : "view only"}
      </p>

      {data.alerts && (
        <Card title="Alerts">
          <ul className="flex flex-col gap-2 text-sm">
            {data.alerts.map((a) => (
              <li key={a.id} className="flex items-center justify-between">
                <span>{a.detail}</span>
                {a.acknowledgedBy ? (
                  <span className="text-xs text-status-good">Acknowledged</span>
                ) : canAct ? (
                  <button
                    onClick={() => acknowledge(a.id)}
                    className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
                  >
                    Acknowledge
                  </button>
                ) : (
                  <span className="text-xs text-ink-muted">View only</span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data.recentVitals && (
        <Card title="Recent vitals (7 days)">
          <ul className="grid grid-cols-2 gap-2 text-sm md:grid-cols-3">
            {data.recentVitals.slice(0, 12).map((v, i) => (
              <li key={i}>
                {v.type}: {v.value}
                {v.unit} <span className="text-ink-muted">({new Date(v.recordedAt).toLocaleDateString()})</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data.adherence && (
        <Card title="Adherence">
          <ul className="flex flex-col gap-1 text-sm">
            {data.adherence.map((m, i) => (
              <li key={i}>
                {m.name} ({m.schedule}): {m.taken}/{m.total} taken
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export function Caregiver() {
  const { principal } = useAuth();
  return (
    <div className="flex flex-col gap-4">
      <h1 className="flex items-center gap-2 text-2xl font-semibold">
        <HeartHandshake className="h-6 w-6" style={{ color: "var(--role-accent)" }} />
        Caregiver &amp; Family
      </h1>
      {principal.role === "caregiver" ? (
        <RequireActivePatient>
          <CaregiverScopedView />
        </RequireActivePatient>
      ) : (
        <PatientCaregiverManager />
      )}
    </div>
  );
}
