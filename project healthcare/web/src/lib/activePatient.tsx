import { createContext, useContext, useEffect, useMemo, useState, type FC, type ReactNode } from "react";
import { api } from "./api";
import { IS_DEMO_MODE, useAuth } from "./auth";

const STORAGE_KEY = "p63-active-patient-id";

export interface PatientOption {
  id: number;
  name: string;
  dob: string;
  sex: string;
  overallScore?: number | null;
  riskBand?: string | null;
}

interface ActivePatientContextValue {
  /** The patient ID every patient-scoped page should call the API with.
   * null means "provider/caregiver hasn't picked one yet". */
  patientId: number | null;
  setPatientId: (id: number) => void;
  /** For patient role: always null (nothing to pick from). For
   * provider/caregiver: the list to choose from once loaded. */
  options: PatientOption[] | null;
  loading: boolean;
  error: string | null;
  /** True once options are known to have loaded (even if patientId is still null). */
  needsSelection: boolean;
}

const ActivePatientContext = createContext<ActivePatientContextValue | null>(null);

export function useActivePatient(): ActivePatientContextValue {
  const ctx = useContext(ActivePatientContext);
  if (!ctx) throw new Error("useActivePatient must be used within ActivePatientProvider");
  return ctx;
}

/** Resolves "which patient's data should the current screen show":
 * - patient role: always their own principal.patientId, no picker.
 * - provider role: the full roster (GET /patients) to pick from.
 * - caregiver role: their linked patients (GET /caregivers/me/patients);
 *   auto-selected when there's exactly one.
 * Selection persists per-tab in sessionStorage so navigating between pages
 * (Dashboard -> Meds -> Schedule ...) keeps showing the same patient. */
export const ActivePatientProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const { principal, authHeader } = useAuth();
  const [options, setOptions] = useState<PatientOption[] | null>(null);
  const [selected, setSelected] = useState<number | null>(() => {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? Number(raw) : null;
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Demo mode already has a direct role/patientId switcher in the navbar
    // (principal.patientId is freely settable there for every role) — the
    // roster/linked-patient resolution below is only needed in real-auth
    // mode, where a provider/caregiver identity carries no patientId at all.
    if (IS_DEMO_MODE || principal.role === "patient") {
      setOptions(null);
      return;
    }
    if (principal.role !== "provider" && principal.role !== "caregiver") return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    const path = principal.role === "provider" ? "/patients?pageSize=100" : "/caregivers/me/patients";
    api
      .get<PatientOption[] | { items: PatientOption[] }>(path, authHeader)
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res) ? res : res.items;
        setOptions(list);
        // Auto-select when there's exactly one (typical caregiver case), or
        // re-validate a previously-selected id is still in the list.
        setSelected((prev) => {
          if (prev && list.some((p) => p.id === prev)) return prev;
          return list.length === 1 ? list[0].id : null;
        });
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [principal.role, authHeader]);

  const setPatientId = (id: number) => {
    sessionStorage.setItem(STORAGE_KEY, String(id));
    setSelected(id);
  };

  const patientId = IS_DEMO_MODE || principal.role === "patient" ? principal.patientId : selected;

  const value = useMemo<ActivePatientContextValue>(
    () => ({
      patientId,
      setPatientId,
      options: IS_DEMO_MODE || principal.role === "patient" ? null : options,
      loading,
      error,
      needsSelection: !IS_DEMO_MODE && principal.role !== "patient" && patientId === null,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [patientId, options, loading, error, principal.role],
  );

  return <ActivePatientContext.Provider value={value}>{children}</ActivePatientContext.Provider>;
};
