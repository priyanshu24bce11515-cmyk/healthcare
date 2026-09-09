import { Activity, Droplet, Footprints, Heart, Moon, Watch } from "lucide-react";
import type { ComponentType, CSSProperties } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { DisclaimerBanner } from "../components/DisclaimerBanner";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { RiskScoreGauge } from "../components/RiskScoreGauge";
import { scoreToStatus, StatusPill } from "../components/StatusPill";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { DashboardPayload } from "../lib/types";
import { useFetch } from "../lib/useFetch";

const VITAL_LABELS: Record<string, string> = {
  heartRate: "Heart rate",
  steps: "Steps today",
  sleep: "Sleep last night",
  bp_systolic: "BP systolic",
  bp_diastolic: "BP diastolic",
  glucose: "Glucose",
};

const VITAL_ICONS: Record<string, { icon: ComponentType<{ className?: string; style?: CSSProperties }>; color: string }> = {
  heartRate: { icon: Heart, color: "var(--series-1)" },
  sleep: { icon: Moon, color: "var(--series-2)" },
  steps: { icon: Footprints, color: "var(--series-3)" },
  bp_systolic: { icon: Activity, color: "var(--series-4)" },
  bp_diastolic: { icon: Activity, color: "var(--series-4)" },
  glucose: { icon: Droplet, color: "var(--series-5)" },
};

const SOURCE_LABELS: Record<string, string> = {
  simulated: "Simulated wearable feed",
  "device-simulator": "Wearable device simulator",
};

export function Dashboard() {
  const { principal, authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const { data, loading, error, reload } = useFetch(
    (header) => api.get<DashboardPayload>(`/dashboard/${patientId}`, header),
    [patientId],
  );

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const overall = data.riskScores.overall;

  const vitalsList = Object.values(data.latestVitals);
  const lastSynced = vitalsList.length
    ? vitalsList.reduce((latest, v) => (new Date(v.recordedAt) > new Date(latest.recordedAt) ? v : latest))
    : null;

  const acknowledge = async (alertId: number) => {
    await api.post(`/alerts/${alertId}/acknowledge`, authHeader);
    reload();
  };

  const actOnRecommendation = async (recommendationId: number, action: "acted" | "dismissed") => {
    await api.patch(`/recommendations/${recommendationId}/action`, authHeader, { action });
    reload();
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{data.patient.name}</h1>
          <p className="text-sm text-ink-secondary">
            {data.patient.sex} · DOB {data.patient.dob}
          </p>
        </div>
      </div>

      <DisclaimerBanner />

      <Card title="Overall wellness" className="animate-fade-in">
        {overall ? (
          <div className="flex flex-col items-center gap-4 text-center sm:flex-row sm:items-center sm:gap-6 sm:text-left">
            <RiskScoreGauge score={overall.score} />
            <div className="flex flex-col items-center gap-1 sm:items-start">
              <StatusPill status={scoreToStatus(overall.score)} />
              <p className="text-sm text-ink-secondary">{overall.reason}</p>
              <p className="text-xs text-ink-muted">Last computed {new Date(overall.computedAt).toLocaleDateString()}</p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 py-2 text-center sm:flex-row sm:items-center sm:justify-between sm:text-left">
            <div>
              <p className="text-sm font-medium text-ink-primary">Your wellness score isn't ready yet</p>
              <p className="text-sm text-ink-secondary">Run your first Risk Score check to see a personalized score here.</p>
            </div>
            <Link
              to="/risk-score"
              className="rounded-md border border-line-border bg-surface px-3 py-1.5 text-sm text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
            >
              Go to Risk Score
            </Link>
          </div>
        )}
      </Card>

      {data.unacknowledgedAlerts.length > 0 && (
        <Card title="Active alerts" className="border-status-critical/40 animate-fade-in">
          <ul className="flex flex-col gap-2">
            {data.unacknowledgedAlerts.map((alert) => (
              <li key={alert.id} className="flex items-center justify-between gap-3 text-sm">
                <span>{alert.detail}</span>
                <button
                  onClick={() => acknowledge(alert.id)}
                  className="rounded-md border border-line-border px-2 py-1 text-xs text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
                >
                  Acknowledge
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card className="animate-fade-in">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full"
              style={{ backgroundColor: "var(--role-base)" }}
            >
              <Watch className="h-5 w-5" style={{ color: "var(--role-accent)" }} />
            </div>
            <div>
              <p className="text-sm font-medium text-ink-primary">Wearable Device</p>
              <p className="text-xs text-ink-secondary">
                {lastSynced ? (SOURCE_LABELS[lastSynced.source] ?? lastSynced.source) : "No device data yet"}
              </p>
            </div>
          </div>
          {lastSynced ? (
            <StatusPill status="good" text={`Connected · synced ${new Date(lastSynced.recordedAt).toLocaleString()}`} />
          ) : (
            <StatusPill status="critical" text="Not connected" />
          )}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {Object.entries(data.latestVitals).map(([type, reading], i) => {
          const iconMeta = VITAL_ICONS[type];
          const Icon = iconMeta?.icon;
          return (
            <Card
              key={type}
              className="animate-fade-in"
              style={{ animationDelay: `${Math.min(i * 40, 200)}ms` }}
              title={
                <span className="flex items-center gap-1.5">
                  {Icon && <Icon className="h-4 w-4" style={{ color: iconMeta.color }} />}
                  {VITAL_LABELS[type] ?? type}
                </span>
              }
            >
              <p className="text-2xl font-semibold text-ink-primary">
                {reading.value}
                <span className="ml-1 text-sm font-normal text-ink-muted">{reading.unit}</span>
              </p>
            </Card>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Recommendations" className="animate-fade-in">
          {data.recommendations.length === 0 && <p className="text-sm text-ink-secondary">None yet — check back after your next Risk Score run.</p>}
          <ul className="flex flex-col gap-3">
            {data.recommendations.map((rec) => (
              <li key={rec.id} className="flex items-start justify-between gap-3 border-l-2 border-series-1 pl-3 text-sm">
                <div>
                  <p className="font-medium text-ink-primary">{rec.text}</p>
                  <p className="text-ink-secondary">{rec.reason}</p>
                  {rec.actedOn && <p className="mt-1 text-xs text-status-good">Marked as done</p>}
                </div>
                {principal.role === "patient" && !rec.actedOn && (
                  <span className="flex shrink-0 items-center gap-1">
                    <button
                      onClick={() => actOnRecommendation(rec.id, "acted")}
                      className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
                    >
                      Done
                    </button>
                    <button
                      onClick={() => actOnRecommendation(rec.id, "dismissed")}
                      className="rounded-md border border-line-border px-2 py-1 text-xs text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
                    >
                      Dismiss
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Upcoming appointments" className="animate-fade-in">
          {data.upcomingAppointments.length === 0 && <p className="text-sm text-ink-secondary">No appointments scheduled.</p>}
          <ul className="flex flex-col gap-2">
            {data.upcomingAppointments.map((appt) => (
              <li key={appt.id} className="text-sm">
                <span className="font-medium">{appt.providerName}</span> ({appt.specialty}) —{" "}
                {new Date(appt.startsAt).toLocaleString()} · {appt.status}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
