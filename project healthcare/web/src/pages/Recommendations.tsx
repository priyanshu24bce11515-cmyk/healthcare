import { Footprints, Heart, Moon, Pill, Sparkles } from "lucide-react";
import { useState } from "react";
import type { ComponentType, CSSProperties } from "react";
import { Card } from "../components/Card";
import { DisclaimerBanner } from "../components/DisclaimerBanner";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Recommendation } from "../lib/types";
import { useFetch } from "../lib/useFetch";

const PRIORITY_COLOR: Record<Recommendation["priority"], string> = {
  Low: "text-ink-secondary",
  Medium: "text-status-warning",
  High: "text-status-critical",
};

const CATEGORY_ICONS: Record<string, ComponentType<{ className?: string; style?: CSSProperties }>> = {
  Sleep: Moon,
  Activity: Footprints,
  Heart: Heart,
  Medication: Pill,
  General: Sparkles,
};

export function Recommendations() {
  const { principal, authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const [generating, setGenerating] = useState(false);
  const { data, loading, error, reload } = useFetch(
    (header) => api.get<Recommendation[]>(`/recommendations/${patientId}`, header),
    [patientId],
  );

  const generate = async () => {
    setGenerating(true);
    try {
      await api.post(`/recommendations/${patientId}/generate`, authHeader);
      reload();
    } finally {
      setGenerating(false);
    }
  };

  const actOn = async (recommendationId: number, action: "acted" | "dismissed") => {
    await api.patch(`/recommendations/${recommendationId}/action`, authHeader, { action });
    reload();
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Personalized Recommendations</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Generated from your own recent vitals, adherence, and risk scores — each one below shows the specific data
            that produced it.
          </p>
        </div>
        <button
          onClick={generate}
          disabled={generating}
          className="rounded-md border border-line-border bg-surface px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
        >
          {generating ? "Generating…" : "Generate new"}
        </button>
      </div>
      <DisclaimerBanner />

      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}

      <div className="flex flex-col gap-3">
        {data?.map((rec, i) => {
          const Icon = CATEGORY_ICONS[rec.category] ?? Sparkles;
          const settled = rec.actedOn || !!rec.dismissedAt;
          return (
            <Card
              key={rec.id}
              className={`animate-fade-in ${rec.dismissedAt ? "opacity-60" : ""}`}
              style={{ animationDelay: `${Math.min(i * 60, 240)}ms` }}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <div
                    className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                    style={{ backgroundColor: "var(--role-base)" }}
                  >
                    <Icon className="h-4 w-4" style={{ color: "var(--role-accent)" }} />
                  </div>
                  <div>
                    <p className="font-medium text-ink-primary">{rec.text}</p>
                    <p className="mt-1 text-sm text-ink-secondary">{rec.reason}</p>
                    <p className="mt-1 text-xs text-ink-muted">
                      {rec.category} · {new Date(rec.generatedAt).toLocaleString()}
                    </p>
                    {rec.actedOn && <p className="mt-1 text-xs text-status-good">Marked as done</p>}
                    {rec.dismissedAt && <p className="mt-1 text-xs text-ink-muted">Dismissed</p>}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-2">
                  <span className={`text-xs font-semibold ${PRIORITY_COLOR[rec.priority]}`}>{rec.priority}</span>
                  {!settled && principal.role === "patient" && (
                    <span className="flex items-center gap-1">
                      <button
                        onClick={() => actOn(rec.id, "acted")}
                        className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
                      >
                        Done
                      </button>
                      <button
                        onClick={() => actOn(rec.id, "dismissed")}
                        className="rounded-md border border-line-border px-2 py-1 text-xs text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
                      >
                        Dismiss
                      </button>
                    </span>
                  )}
                </div>
              </div>
            </Card>
          );
        })}
        {data?.length === 0 && <p className="text-sm text-ink-secondary">No recommendations yet — generate one above.</p>}
      </div>
    </div>
  );
}
