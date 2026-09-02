import { useState } from "react";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { RiskScoreGauge } from "../components/RiskScoreGauge";
import { StatusPill, scoreToStatus } from "../components/StatusPill";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { AreaScore, DashboardPayload } from "../lib/types";
import { useFetch } from "../lib/useFetch";

const AREA_LABELS: Record<string, string> = {
  overall: "Overall",
  sleep: "Sleep health",
  activity: "Activity",
  adherence: "Medication adherence",
  heart: "Heart trend",
};

export function RiskScore() {
  const { authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const [recomputing, setRecomputing] = useState(false);
  const { data, loading, error, reload } = useFetch(
    (header) => api.get<DashboardPayload>(`/dashboard/${patientId}`, header),
    [patientId],
  );

  const recompute = async () => {
    setRecomputing(true);
    try {
      await api.post(`/risk-score/${patientId}/compute`, authHeader);
      reload();
    } finally {
      setRecomputing(false);
    }
  };

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const areas = Object.entries(data.riskScores) as [string, AreaScore][];
  const orderedAreas = ["overall", "sleep", "activity", "adherence", "heart"].filter((a) =>
    areas.some(([area]) => area === a),
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Health Risk Score</h1>
        <button
          onClick={recompute}
          disabled={recomputing}
          className="rounded-md border border-line-border bg-surface px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
        >
          {recomputing ? "Recomputing…" : "Recompute now"}
        </button>
      </div>
      <p className="text-sm text-ink-secondary">
        A lifestyle/wellness pattern indicator, not a clinical risk prediction or diagnosis. Each score is
        explainable — see its reason below.
      </p>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {orderedAreas.map((area, i) => {
          const score = data.riskScores[area];
          return (
            <Card key={area} title={AREA_LABELS[area] ?? area} className="animate-fade-in" style={{ animationDelay: `${i * 60}ms` }}>
              <div className="flex items-center gap-4">
                <RiskScoreGauge score={score.score} size={88} />
                <div className="flex flex-col items-start gap-1">
                  <StatusPill status={scoreToStatus(score.score)} />
                  <p className="text-sm text-ink-secondary">{score.reason}</p>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
