import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { AnalyticsPayload, Goal } from "../lib/types";
import { useFetch } from "../lib/useFetch";

const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

const VITAL_CHARTS: { key: string; label: string; unit: string }[] = [
  { key: "heartRate", label: "Resting heart rate", unit: "bpm" },
  { key: "sleep", label: "Sleep", unit: "hours" },
  { key: "steps", label: "Steps", unit: "count" },
  { key: "bp_systolic", label: "Blood pressure (systolic)", unit: "mmHg" },
  { key: "bp_diastolic", label: "Blood pressure (diastolic)", unit: "mmHg" },
  { key: "glucose", label: "Glucose", unit: "mg/dL" },
  { key: "oxygenSaturation", label: "Oxygen saturation", unit: "%" },
  { key: "temperature", label: "Temperature", unit: "°C" },
];

const AREAS = ["overall", "sleep", "activity", "adherence", "heart"];
const GOAL_KINDS = ["fitness", "nutrition"] as const;
const RANGE_OPTIONS = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
] as const;

function VitalTrendChart({ label, unit, points }: { label: string; unit: string; points: { value: number; recordedAt: string }[] }) {
  const chartData = points.map((p) => ({ date: new Date(p.recordedAt).toLocaleDateString(), value: p.value }));
  return (
    <Card title={`${label} (${unit})`}>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" stroke="var(--baseline)" tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <YAxis stroke="var(--baseline)" tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <Tooltip
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", fontSize: 12 }}
            labelStyle={{ color: "var(--text-primary)" }}
          />
          <Line type="monotone" dataKey="value" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

function RiskScoreHistoryChart({ history }: { history: { area: string; score: number; computedAt: string }[] }) {
  const byDate = new Map<string, Record<string, number | string>>();
  for (const row of history) {
    const dateKey = new Date(row.computedAt).toLocaleDateString();
    const entry = byDate.get(dateKey) ?? { date: dateKey };
    entry[row.area] = row.score;
    byDate.set(dateKey, entry);
  }
  const chartData = Array.from(byDate.values());

  return (
    <Card title="Risk Score history">
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" stroke="var(--baseline)" tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <YAxis domain={[0, 100]} stroke="var(--baseline)" tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <Tooltip
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", fontSize: 12 }}
            labelStyle={{ color: "var(--text-primary)" }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }} />
          {AREAS.map((area, i) => (
            <Line key={area} type="monotone" dataKey={area} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={2} dot={{ r: 2 }} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

function GoalsCard({ goals, onChanged }: { goals: Goal[]; onChanged: () => void }) {
  const { principal, authHeader } = useAuth();
  const [kind, setKind] = useState<(typeof GOAL_KINDS)[number]>("fitness");
  const [target, setTarget] = useState("8000");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isPatient = principal.role === "patient";

  const addGoal = async () => {
    const parsedTarget = Number(target);
    if (Number.isNaN(parsedTarget)) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/goals", authHeader, { kind, target: parsedTarget, period: "weekly" });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const bumpProgress = async (goal: Goal, delta: number) => {
    setError(null);
    try {
      await api.patch(`/goals/${goal.id}`, authHeader, { progress: Math.max(0, goal.progress + delta) });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Card title="Goals">
      <ul className="flex flex-col gap-2 text-sm">
        {goals.map((g) => (
          <li key={g.id} className="flex items-center justify-between gap-3">
            <span>
              {g.kind}: {g.progress}/{g.target} ({g.period})
            </span>
            {isPatient && (
              <span className="flex items-center gap-1">
                <button
                  onClick={() => bumpProgress(g, -1)}
                  className="rounded-md border border-line-border px-2 py-0.5 text-xs transition-colors duration-150 hover:text-ink-primary"
                >
                  −
                </button>
                <button
                  onClick={() => bumpProgress(g, 1)}
                  className="rounded-md border border-line-border px-2 py-0.5 text-xs transition-colors duration-150 hover:text-ink-primary"
                >
                  +
                </button>
              </span>
            )}
          </li>
        ))}
        {goals.length === 0 && <p className="text-ink-secondary">No goals set yet.</p>}
      </ul>

      {isPatient && (
        <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-line-grid pt-3">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as (typeof GOAL_KINDS)[number])}
            className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm capitalize"
          >
            {GOAL_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            type="number"
            placeholder="Target"
            className="w-28 rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          />
          <button
            onClick={addGoal}
            disabled={submitting}
            className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
          >
            {submitting ? "Adding…" : "Add goal"}
          </button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-status-critical">{error}</p>}
    </Card>
  );
}

export function Analytics() {
  const { patientId } = useActivePatient();
  const [days, setDays] = useState<(typeof RANGE_OPTIONS)[number]["days"]>(30);
  const { data, loading, error, reload } = useFetch(
    (header) => api.get<AnalyticsPayload>(`/analytics/${patientId}?days=${days}`, header),
    [patientId, days],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Analytics &amp; Trends</h1>
        <div className="flex items-center gap-1 rounded-md border border-line-border bg-surface p-1">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              onClick={() => setDays(opt.days)}
              className={`rounded px-3 py-1 text-sm transition-colors duration-150 ${
                days === opt.days ? "bg-role-base font-medium text-ink-primary" : "text-ink-secondary hover:text-ink-primary"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}

      {data && (
        <>
          <RiskScoreHistoryChart history={data.riskScoreHistory} />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {VITAL_CHARTS.map(
              (chart) =>
                data.vitalsTrend[chart.key]?.length > 0 && (
                  <VitalTrendChart key={chart.key} label={chart.label} unit={chart.unit} points={data.vitalsTrend[chart.key]} />
                ),
            )}
            {VITAL_CHARTS.every((chart) => !data.vitalsTrend[chart.key]?.length) && (
              <p className="text-sm text-ink-secondary">No vitals recorded in this range yet.</p>
            )}
          </div>

          <GoalsCard goals={data.goals} onChanged={reload} />
        </>
      )}
    </div>
  );
}
