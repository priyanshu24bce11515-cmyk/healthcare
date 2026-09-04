import { Users } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { scoreToStatus, StatusPill } from "../components/StatusPill";
import { useActivePatient } from "../lib/activePatient";
import { useAuth } from "../lib/auth";
import { defaultPathFor } from "../lib/routes";

export function Patients() {
  const { principal } = useAuth();
  const { options, loading, error, patientId, setPatientId } = useActivePatient();
  const navigate = useNavigate();

  const title = principal.role === "provider" ? "Patient Roster" : "Your Linked Patients";

  const choose = (id: number) => {
    setPatientId(id);
    navigate(defaultPathFor(principal.role));
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Users className="h-5 w-5" style={{ color: "var(--role-accent)" }} />
        <h1 className="text-2xl font-semibold">{title}</h1>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {options && options.length === 0 && (
        <Card>
          <p className="text-sm text-ink-secondary">
            {principal.role === "provider"
              ? "No patients registered yet."
              : "No patients are linked to your caregiver account yet. Ask the patient to link you from their Caregiver page."}
          </p>
        </Card>
      )}

      {options && options.length > 0 && (
        <Card>
          <ul className="flex flex-col divide-y divide-line-grid">
            {options.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <div>
                  <p className="text-sm font-medium text-ink-primary">
                    {p.name}
                    {p.id === patientId && (
                      <span className="ml-2 text-xs font-normal text-ink-muted">(currently viewing)</span>
                    )}
                  </p>
                  <p className="text-xs text-ink-secondary">
                    {p.sex} · DOB {p.dob}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  {typeof p.overallScore === "number" ? (
                    <StatusPill status={scoreToStatus(p.overallScore)} text={`${p.overallScore}/100`} />
                  ) : (
                    <span className="text-xs text-ink-muted">No score yet</span>
                  )}
                  <button
                    onClick={() => choose(p.id)}
                    className="rounded-md border border-line-border px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
                    style={p.id === patientId ? { borderColor: "var(--role-accent)", color: "var(--role-accent)" } : undefined}
                  >
                    {p.id === patientId ? "Viewing" : "View"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
