import { createContext, useContext, useMemo, useState, type FC, type ReactNode } from "react";
import type { Principal, Role } from "./types";

const STORAGE_KEY = "p63-demo-principal";

// Real Entra External ID sign-in is the default; the raw role/patientId
// switcher only exists for fast local iteration, gated behind this flag so
// it can never appear in a real deployment (mirrors the backend's
// ALLOW_DEMO_PRINCIPAL gate — see functions/shared/config.py). Deliberately
// no MSAL imports anywhere in this file — main.tsx dynamically imports
// ./realAuth (which does) only when this is false, so demo/dev builds never
// pull the MSAL bundle in at all.
export const IS_DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

export type AuthStatus = "loading" | "unauthenticated" | "onboarding" | "ready" | "error";

export interface AuthContextValue {
  status: AuthStatus;
  principal: Principal;
  /** Headers to attach to every API call — shape varies by mode (see api.ts). */
  authHeader: Record<string, string>;
  /** Demo mode only; a no-op in real mode (role/patientId are server-resolved there). */
  setPrincipal: (p: Principal) => void;
  /** Real mode: start the Microsoft sign-in redirect (from the landing page). No-op in demo mode. */
  login: () => void;
  logout: () => void;
  /** Real mode only; re-resolves /me (e.g. right after claiming an account in onboarding). */
  refreshMe: () => void;
  errorMessage?: string;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export const ROLES: Role[] = ["patient", "provider", "caregiver"];

// ---------------------------------------------------------------------------
// Demo mode — today's exact behavior, unchanged: a client-side role/patientId
// switcher persisted in localStorage, sent as x-demo-principal.
// ---------------------------------------------------------------------------

function encodeDemoPrincipal(p: Principal): string {
  return btoa(JSON.stringify({ userId: p.userId, userRoles: [p.role], patientId: p.patientId }));
}

function loadDemoPrincipal(): Principal {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as Principal;
      if (Number.isInteger(parsed.patientId) && parsed.patientId >= 1) {
        return parsed;
      }
    } catch {
      // fall through to default
    }
  }
  return { userId: "demo-patient-1", role: "patient", patientId: 1 };
}

export const DemoAuthProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [principal, setPrincipalState] = useState<Principal>(loadDemoPrincipal);

  const setPrincipal = (p: Principal) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
    setPrincipalState(p);
  };

  const authHeader = useMemo(() => ({ "x-demo-principal": encodeDemoPrincipal(principal) }), [principal]);

  const value: AuthContextValue = {
    status: "ready",
    principal,
    authHeader,
    setPrincipal,
    login: () => {},
    logout: () => {
      localStorage.removeItem(STORAGE_KEY);
      setPrincipalState(loadDemoPrincipal());
    },
    refreshMe: () => {},
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
