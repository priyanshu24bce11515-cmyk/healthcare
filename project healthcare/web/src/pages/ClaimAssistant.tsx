import { Sparkles } from "lucide-react";
import { useState } from "react";
import { Card } from "../components/Card";
import { DisclaimerBanner } from "../components/DisclaimerBanner";
import { ErrorState } from "../components/ErrorState";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Claim, ClaimHistoryEntry, ClaimStatus } from "../lib/types";
import { useFetch } from "../lib/useFetch";

const PROVIDER_NEXT_STATUS: Partial<Record<ClaimStatus, ClaimStatus[]>> = {
  submitted: ["processing"],
  processing: ["approved", "denied"],
};

function ClaimRow({ claim, onChanged }: { claim: Claim; onChanged: () => void }) {
  const { authHeader } = useAuth();
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<ClaimHistoryEntry[] | null>(null);
  const [denyReason, setDenyReason] = useState("");
  const [showDenyInput, setShowDenyInput] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const toggleHistory = async () => {
    if (showHistory) {
      setShowHistory(false);
      return;
    }
    setShowHistory(true);
    if (!history) {
      const rows = await api.get<ClaimHistoryEntry[]>(`/claims/${claim.id}/history`, authHeader);
      setHistory(rows);
    }
  };

  const setStatus = async (status: ClaimStatus, reason?: string) => {
    setActionError(null);
    try {
      await api.patch(`/claims/${claim.id}/status`, authHeader, { status, denialReason: reason });
      setShowDenyInput(false);
      setDenyReason("");
      setHistory(null);
      onChanged();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const nextStatuses = PROVIDER_NEXT_STATUS[claim.status] ?? [];

  return (
    <li className="flex flex-col gap-2 border-b border-line-grid pb-3 last:border-0 last:pb-0">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span>
          #{claim.id} — {claim.provider} — {claim.amount != null ? `$${claim.amount}` : "no amount"} —{" "}
          <span className={claim.status === "denied" ? "text-status-critical" : claim.status === "approved" ? "text-status-good" : ""}>
            {claim.status}
          </span>{" "}
          {claim.missingFields.length > 0 && `(missing: ${claim.missingFields.join(", ")})`}
        </span>
        <span className="flex shrink-0 items-center gap-1">
          {claim.status === "ready" && (
            <button
              onClick={() => setStatus("submitted")}
              className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
            >
              Submit
            </button>
          )}
          {nextStatuses.map((next) =>
            next === "denied" ? (
              <button
                key={next}
                onClick={() => setShowDenyInput((v) => !v)}
                className="rounded-md border border-line-border px-2 py-1 text-xs text-status-critical transition-colors duration-150 hover:opacity-80"
              >
                Deny
              </button>
            ) : (
              <button
                key={next}
                onClick={() => setStatus(next)}
                className="rounded-md border border-line-border px-2 py-1 text-xs capitalize transition-colors duration-150 hover:text-ink-primary"
              >
                Mark {next}
              </button>
            ),
          )}
          <button
            onClick={toggleHistory}
            className="rounded-md border border-line-border px-2 py-1 text-xs text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
          >
            {showHistory ? "Hide history" : "History"}
          </button>
        </span>
      </div>

      {showDenyInput && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={denyReason}
            onChange={(e) => setDenyReason(e.target.value)}
            placeholder="Denial reason (required)"
            className="min-w-[16rem] flex-1 rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
          />
          <button
            onClick={() => denyReason && setStatus("denied", denyReason)}
            disabled={!denyReason}
            className="rounded-md border border-line-border px-3 py-1.5 text-xs text-status-critical transition-colors duration-150 hover:opacity-80 disabled:opacity-50"
          >
            Confirm denial
          </button>
        </div>
      )}

      {claim.status === "denied" && (
        <div className="rounded-md border border-status-critical/30 bg-surface px-3 py-2 text-xs">
          <p className="text-status-critical">Denied: {claim.denialReason ?? "No reason on file"}</p>
          {claim.appealGuidance && <p className="mt-1 text-ink-secondary">{claim.appealGuidance}</p>}
        </div>
      )}

      {actionError && <p className="text-xs text-status-critical">{actionError}</p>}

      {showHistory && (
        <ul className="flex flex-col gap-1 border-l border-line-grid pl-3 text-xs text-ink-secondary">
          {history === null && <li>Loading…</li>}
          {history?.map((h, i) => (
            <li key={i}>
              {new Date(h.changedAt).toLocaleString()} — {h.status} {h.note && `(${h.note})`}
            </li>
          ))}
          {history?.length === 0 && <li>No history recorded.</li>}
        </ul>
      )}
    </li>
  );
}

const SAMPLE_NOTES = [
  {
    label: "Fever and cough",
    text: "Patient reports fever and cough for the past 3 days. Temperature 100.9F. Prescribed paracetamol 500mg twice daily for 5 days. Diagnosis: acute upper respiratory infection (J06.9).",
  },
  {
    label: "Type 2 diabetes follow-up",
    text: "Follow-up visit for type 2 diabetes mellitus (E11.9). Fasting glucose 162 mg/dL. Continuing metformin 500mg twice daily. Follow-up in 3 months.",
  },
  {
    label: "Hypertension check-in",
    text: "Routine check-in for essential hypertension (I10). Blood pressure 148/94 mmHg. Prescribed lisinopril 10mg once daily.",
  },
];

export function ClaimAssistant() {
  const { authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const [providerName, setProviderName] = useState("Dr. Rao");
  const [amount, setAmount] = useState("150");
  const [noteText, setNoteText] = useState(SAMPLE_NOTES[0].text);
  const [submitting, setSubmitting] = useState(false);
  const [lastResult, setLastResult] = useState<Claim | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: claims, error: claimsError, reload } = useFetch(
    (header) => api.get<Claim[]>(`/claims/${patientId}`, header),
    [patientId],
  );

  const submitNote = async () => {
    setSubmitting(true);
    setActionError(null);
    try {
      const parsedAmount = Number(amount);
      const claim = await api.post<Claim>("/claims", authHeader, {
        patientId,
        provider: providerName,
        noteText,
        amount: Number.isNaN(parsedAmount) ? undefined : parsedAmount,
      });
      setLastResult(claim);
      reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Clinical Note → Claim Assistant</h1>
      <div className="flex items-center gap-2 rounded-lg border border-line-border bg-surface px-4 py-2 text-sm text-ink-secondary">
        <Sparkles className="h-4 w-4 shrink-0" style={{ color: "var(--role-accent)" }} />
        <span>
          Powered by <strong className="text-ink-primary">Azure AI Language for Health</strong> — automatically extracts
          diagnoses, medications, symptoms, and dosages from the clinical note below to pre-fill the claim.
        </span>
      </div>
      <DisclaimerBanner />

      {actionError && <ErrorState message={actionError} />}

      <Card title="Paste or pick a clinical note" className="animate-fade-in">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {SAMPLE_NOTES.map((n) => (
              <button
                key={n.label}
                onClick={() => setNoteText(n.text)}
                className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
              >
                {n.label}
              </button>
            ))}
          </div>
          <textarea
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            rows={5}
            className="w-full rounded-md border border-line-border bg-surface px-3 py-2 text-sm"
          />
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              placeholder="Provider"
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="Amount"
              type="number"
              className="w-28 rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <button
              onClick={submitNote}
              disabled={submitting}
              className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
            >
              {submitting ? "Extracting…" : "Extract & pre-fill claim"}
            </button>
          </div>
        </div>
      </Card>

      {lastResult && (
        <Card title={`Claim #${lastResult.id} — ${lastResult.status}`} className="animate-fade-in">
          <p className="text-sm">
            Diagnosis codes: <span className="font-medium">{lastResult.diagnosisCodes ?? "—"}</span>
          </p>
          <p className="mt-1 text-sm">
            Amount: <span className="font-medium">{lastResult.amount != null ? `$${lastResult.amount}` : "—"}</span>
          </p>
          <p className="mt-1 text-sm text-ink-secondary">
            Extracted: {JSON.stringify(lastResult.extractedFields)}
          </p>
          {lastResult.missingFields.length > 0 && (
            <p className="mt-1 text-sm text-status-warning">Missing: {lastResult.missingFields.join(", ")}</p>
          )}
        </Card>
      )}

      {claimsError && <ErrorState message={claimsError} onRetry={reload} />}

      <Card title="Claims for this patient" className="animate-fade-in" style={{ animationDelay: "60ms" }}>
        <ul className="flex flex-col gap-3 text-sm">
          {claims?.map((c) => (
            <ClaimRow key={c.id} claim={c} onChanged={reload} />
          ))}
          {claims?.length === 0 && <p className="text-ink-secondary">No claims yet.</p>}
        </ul>
      </Card>
    </div>
  );
}
