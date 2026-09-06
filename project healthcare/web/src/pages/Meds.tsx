import { useState } from "react";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { ProgressBar } from "../components/ProgressBar";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { AdherenceSummary } from "../lib/types";
import { useFetch } from "../lib/useFetch";

interface PendingDose {
  id: number;
  dueAt: string;
  takenAt: string | null;
  status: "taken" | "missed";
  name: string;
  dosage: string;
  schedule: string;
}

const SCHEDULES = ["morning", "afternoon", "night"] as const;

function PrescribeMedication({ patientId, onPrescribed }: { patientId: number; onPrescribed: () => void }) {
  const { authHeader } = useAuth();
  const [name, setName] = useState("");
  const [dosage, setDosage] = useState("");
  const [schedule, setSchedule] = useState<(typeof SCHEDULES)[number]>("morning");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const prescribe = async () => {
    if (!name || !dosage) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/medications", authHeader, {
        patientId,
        name,
        dosage,
        schedule,
        startDate: new Date().toISOString().slice(0, 10),
      });
      setName("");
      setDosage("");
      onPrescribed();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card title="Prescribe a medication" className="animate-fade-in">
      <div className="flex flex-wrap items-end gap-2">
        <input
          placeholder="Medication name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
        />
        <input
          placeholder="Dosage (e.g. 500mg)"
          value={dosage}
          onChange={(e) => setDosage(e.target.value)}
          className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
        />
        <select
          value={schedule}
          onChange={(e) => setSchedule(e.target.value as (typeof SCHEDULES)[number])}
          className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm capitalize"
        >
          {SCHEDULES.map((s) => (
            <option key={s} value={s} className="capitalize">
              {s}
            </option>
          ))}
        </select>
        <button
          onClick={prescribe}
          disabled={submitting}
          className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
        >
          {submitting ? "Prescribing…" : "Prescribe"}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-status-critical">{error}</p>}
      <p className="mt-2 text-xs text-ink-muted">New doses start appearing here on the next reminder cycle.</p>
    </Card>
  );
}

export function Meds() {
  const { principal, authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const summary = useFetch(
    (header) => api.get<AdherenceSummary>(`/adherence/${patientId}/summary`, header),
    [patientId],
  );
  const pending = useFetch(
    (header) => api.get<PendingDose[]>(`/adherence/${patientId}/pending`, header),
    [patientId],
  );

  const markDose = async (logId: number, status: "taken" | "missed") => {
    await api.patch(`/adherence/${logId}`, authHeader, { status });
    pending.reload();
    summary.reload();
  };

  if (summary.loading) return <LoadingState />;
  if (summary.error) return <ErrorState message={summary.error} onRetry={summary.reload} />;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Medications &amp; Adherence</h1>

      {principal.role === "provider" && patientId !== null && (
        <PrescribeMedication patientId={patientId} onPrescribed={() => summary.reload()} />
      )}

      <Card title="Recent doses" className="animate-fade-in">
        {pending.loading && <p className="text-sm text-ink-secondary">Loading…</p>}
        <ul className="flex flex-col gap-2">
          {pending.data?.map((dose) => (
            <li key={dose.id} className="flex items-center justify-between gap-3 text-sm">
              <span>
                <span className="font-medium">{dose.name}</span> ({dose.dosage}, {dose.schedule}) —{" "}
                {new Date(dose.dueAt).toLocaleString()}
              </span>
              <span className="flex items-center gap-2">
                <span className={dose.status === "taken" ? "text-status-good" : "text-status-warning"}>{dose.status}</span>
                {dose.status !== "taken" && (
                  <button
                    onClick={() => markDose(dose.id, "taken")}
                    className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
                  >
                    Mark taken
                  </button>
                )}
                {dose.status !== "missed" && (
                  <button
                    onClick={() => markDose(dose.id, "missed")}
                    className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
                  >
                    Mark missed
                  </button>
                )}
              </span>
            </li>
          ))}
          {pending.data?.length === 0 && <p className="text-sm text-ink-secondary">No doses logged in the last day.</p>}
        </ul>
      </Card>

      <Card title="Adherence by medication" className="animate-fade-in" style={{ animationDelay: "60ms" }}>
        <table className="w-full text-left text-sm">
          <thead className="text-ink-muted">
            <tr>
              <th className="pb-2 font-medium">Medicine</th>
              <th className="pb-2 font-medium">Schedule</th>
              <th className="pb-2 font-medium">Taken</th>
              <th className="pb-2 font-medium">Missed</th>
              <th className="pb-2 font-medium">Adherence</th>
            </tr>
          </thead>
          <tbody>
            {summary.data?.medications.map((m) => (
              <tr key={m.medicationId} className="border-t border-line-grid">
                <td className="py-2">{m.name}</td>
                <td className="py-2 capitalize">{m.schedule}</td>
                <td className="py-2">{m.taken}</td>
                <td className="py-2">{m.missed}</td>
                <td className="py-2">
                  <ProgressBar pct={m.adherencePct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {summary.data && summary.data.patterns.length > 0 && (
        <Card title="Pattern detected" className="border-status-warning/40 animate-fade-in" style={{ animationDelay: "120ms" }}>
          <ul className="flex flex-col gap-1 text-sm text-ink-secondary">
            {summary.data.patterns.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
