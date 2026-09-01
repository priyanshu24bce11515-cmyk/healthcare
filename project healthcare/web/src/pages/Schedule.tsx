import { useState } from "react";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { useActivePatient } from "../lib/activePatient";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Appointment, Provider } from "../lib/types";
import { useFetch } from "../lib/useFetch";

function ProviderTools({ onProviderAdded }: { onProviderAdded: () => void }) {
  const { authHeader } = useAuth();
  const [patientName, setPatientName] = useState("");
  const [patientDob, setPatientDob] = useState("");
  const [patientContact, setPatientContact] = useState("");
  const [patientSubmitting, setPatientSubmitting] = useState(false);
  const [patientResult, setPatientResult] = useState<string | null>(null);
  const [patientError, setPatientError] = useState<string | null>(null);

  const [providerName, setProviderName] = useState("");
  const [specialty, setSpecialty] = useState("");
  const [providerContact, setProviderContact] = useState("");
  const [providerSubmitting, setProviderSubmitting] = useState(false);
  const [providerError, setProviderError] = useState<string | null>(null);

  const addPatient = async () => {
    if (!patientName || !patientDob || !patientContact) return;
    setPatientSubmitting(true);
    setPatientError(null);
    setPatientResult(null);
    try {
      const result = await api.post<{ id: number }>("/patients", authHeader, {
        name: patientName,
        dob: patientDob,
        contact: patientContact,
      });
      setPatientResult(`Registered patient #${result.id}`);
      setPatientName("");
      setPatientDob("");
      setPatientContact("");
    } catch (err) {
      setPatientError(err instanceof Error ? err.message : String(err));
    } finally {
      setPatientSubmitting(false);
    }
  };

  const addProvider = async () => {
    if (!providerName || !specialty || !providerContact) return;
    setProviderSubmitting(true);
    setProviderError(null);
    try {
      await api.post("/providers", authHeader, { name: providerName, specialty, contact: providerContact });
      setProviderName("");
      setSpecialty("");
      setProviderContact("");
      onProviderAdded();
    } catch (err) {
      setProviderError(err instanceof Error ? err.message : String(err));
    } finally {
      setProviderSubmitting(false);
    }
  };

  return (
    <Card title="Provider tools" className="animate-fade-in">
      <div className="flex flex-col gap-4">
        <div>
          <p className="mb-2 text-sm font-medium text-ink-primary">Register a new patient</p>
          <div className="flex flex-wrap items-end gap-2">
            <input
              placeholder="Name"
              value={patientName}
              onChange={(e) => setPatientName(e.target.value)}
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              type="date"
              value={patientDob}
              onChange={(e) => setPatientDob(e.target.value)}
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              placeholder="Email"
              value={patientContact}
              onChange={(e) => setPatientContact(e.target.value)}
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <button
              onClick={addPatient}
              disabled={patientSubmitting}
              className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
            >
              {patientSubmitting ? "Registering…" : "Register patient"}
            </button>
          </div>
          {patientResult && <p className="mt-1 text-sm text-status-good">{patientResult}</p>}
          {patientError && <p className="mt-1 text-sm text-status-critical">{patientError}</p>}
        </div>

        <div className="border-t border-line-grid pt-4">
          <p className="mb-2 text-sm font-medium text-ink-primary">Add a provider</p>
          <div className="flex flex-wrap items-end gap-2">
            <input
              placeholder="Name"
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              placeholder="Specialty"
              value={specialty}
              onChange={(e) => setSpecialty(e.target.value)}
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <input
              placeholder="Email"
              value={providerContact}
              onChange={(e) => setProviderContact(e.target.value)}
              className="rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            />
            <button
              onClick={addProvider}
              disabled={providerSubmitting}
              className="rounded-md border border-line-border px-3 py-1.5 text-sm transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
            >
              {providerSubmitting ? "Adding…" : "Add provider"}
            </button>
          </div>
          {providerError && <p className="mt-1 text-sm text-status-critical">{providerError}</p>}
        </div>
      </div>
    </Card>
  );
}

export function Schedule() {
  const { principal, authHeader } = useAuth();
  const { patientId } = useActivePatient();
  const [selectedProvider, setSelectedProvider] = useState<number | null>(null);
  const [booking, setBooking] = useState(false);

  const providers = useFetch((header) => api.get<Provider[]>("/providers", header), []);
  const slots = useFetch(
    (header) => (selectedProvider ? api.get<string[]>(`/providers/${selectedProvider}/slots`, header) : Promise.resolve([])),
    [selectedProvider],
  );
  const appointments = useFetch(
    (header) => api.get<Appointment[]>(`/appointments/${patientId}`, header),
    [patientId],
  );

  const book = async (startsAt: string) => {
    if (!selectedProvider) return;
    setBooking(true);
    try {
      await api.post("/appointments", authHeader, {
        patientId,
        providerId: selectedProvider,
        startsAt,
        type: "telemed",
      });
      slots.reload();
      appointments.reload();
    } finally {
      setBooking(false);
    }
  };

  const cancelAppointment = async (appointmentId: number) => {
    await api.patch(`/appointments/${appointmentId}/status`, authHeader, { status: "cancelled" });
    appointments.reload();
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Telemedicine Scheduling</h1>

      {appointments.error && <ErrorState message={appointments.error} onRetry={appointments.reload} />}

      {principal.role === "provider" && <ProviderTools onProviderAdded={() => providers.reload()} />}

      <Card title="Book a slot" className="animate-fade-in">
        <div className="flex flex-col gap-3">
          <select
            className="w-full max-w-sm rounded-md border border-line-border bg-surface px-2 py-1.5 text-sm"
            value={selectedProvider ?? ""}
            onChange={(e) => setSelectedProvider(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Select a provider…</option>
            {providers.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.specialty}
              </option>
            ))}
          </select>

          {selectedProvider && (
            <div className="flex flex-wrap gap-2">
              {slots.data?.slice(0, 12).map((slot) => (
                <button
                  key={slot}
                  onClick={() => book(slot)}
                  disabled={booking}
                  className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary disabled:opacity-50"
                >
                  {new Date(slot).toLocaleString()}
                </button>
              ))}
              {slots.data?.length === 0 && <p className="text-sm text-ink-secondary">No open slots in the next 7 days.</p>}
            </div>
          )}
        </div>
      </Card>

      <Card title="Your appointments" className="animate-fade-in" style={{ animationDelay: "60ms" }}>
        <ul className="flex flex-col gap-2 text-sm">
          {appointments.data?.map((appt) => (
            <li key={appt.id} className="flex items-center justify-between">
              <span>
                {appt.providerName} ({appt.specialty}) — {new Date(appt.startsAt).toLocaleString()}
              </span>
              <span className="flex items-center gap-2">
                <span className="text-ink-secondary">{appt.status}</span>
                {appt.status !== "cancelled" && appt.status !== "completed" && (
                  <button
                    onClick={() => cancelAppointment(appt.id)}
                    className="rounded-md border border-line-border px-2 py-1 text-xs transition-colors duration-150 hover:text-ink-primary"
                  >
                    Cancel
                  </button>
                )}
              </span>
            </li>
          ))}
          {appointments.data?.length === 0 && <p className="text-ink-secondary">No appointments yet.</p>}
        </ul>
      </Card>
    </div>
  );
}
