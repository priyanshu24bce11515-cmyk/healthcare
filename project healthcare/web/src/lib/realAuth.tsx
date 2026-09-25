import { useEffect, useMemo, useState, type FC, type ReactNode } from "react";
import { InteractionRequiredAuthError, InteractionStatus } from "@azure/msal-browser";
import { useMsal } from "@azure/msal-react";
import { api } from "./api";
import { AuthContext, type AuthContextValue, type AuthStatus } from "./auth";
import { apiTokenRequest } from "./msalConfig";
import type { MeResponse, Principal } from "./types";

// Real mode — Entra External ID via MSAL. Identity comes from the token;
// role/patientId are resolved server-side (GET /me) against the database,
// never asserted by the client. Only reachable via main.tsx's dynamic
// import(), so this (and its MSAL imports) never load in demo mode. Assumes
// it's already rendered inside an <MsalProvider> with an initialized
// PublicClientApplication (see main.tsx).

const UNRESOLVED_PRINCIPAL: Principal = { userId: "", role: null, patientId: 0 };

export const RealAuthProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const { instance, accounts, inProgress } = useMsal();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [principal, setPrincipal] = useState<Principal>(UNRESOLVED_PRINCIPAL);
  const [token, setToken] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    console.debug("[auth] effect: inProgress =", inProgress, "| accounts =", accounts.length);
    // Only start/continue auth work when MSAL is idle. This guards against
    // React 18 StrictMode's double-effect in dev (second run would call
    // loginRedirect while the first is mid-flight -> interaction_in_progress)
    // and against racing MsalProvider's own startup/redirect processing.
    // The effect re-runs when inProgress transitions back to None.
    if (inProgress !== InteractionStatus.None) {
      return;
    }

    let cancelled = false;

    // MSAL cannot perform a top-level redirect from inside an iframe (it aborts
    // silently). The usual culprit in dev is VS Code's embedded "Simple
    // Browser" — sign-in only works in a real, standalone browser window/tab.
    if (window.self !== window.top) {
      setErrorMessage(
        "Sign-in can't run inside an embedded browser frame. Open http://localhost:5173 in a real browser window (Chrome/Edge), not VS Code's built-in preview.",
      );
      setStatus("error");
      return;
    }

    async function resolve() {
      if (accounts.length === 0) {
        // No signed-in account: show the landing page and wait for the user to
        // click "Sign in" (which calls login() below), rather than yanking them
        // straight to Microsoft. This is the "unauthenticated" state App renders
        // as <Landing/>.
        console.debug("[auth] no signed-in account — showing landing page");
        setStatus("unauthenticated");
        return;
      }

      let accessToken: string;
      try {
        const result = await instance.acquireTokenSilent({ ...apiTokenRequest, account: accounts[0] });
        accessToken = result.accessToken;
      } catch (err) {
        if (err instanceof InteractionRequiredAuthError) {
          await instance.acquireTokenRedirect({ ...apiTokenRequest, account: accounts[0] });
          return; // navigates away; nothing else to do here
        }
        throw err;
      }
      if (cancelled) return;
      setToken(accessToken);

      const me = await api.get<MeResponse>("/me", { Authorization: `Bearer ${accessToken}` });
      if (cancelled) return;

      setPrincipal({ userId: me.userId, role: me.role, patientId: me.patientId ?? 0, name: me.name });
      setStatus(me.role ? "ready" : "onboarding");
    }

    resolve().catch((err: unknown) => {
      if (cancelled) return;
      console.error("Auth resolution failed", err);
      setErrorMessage(err instanceof Error ? err.message : String(err));
      setStatus("error");
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instance, accounts, inProgress, tick]);

  const authHeader = useMemo<Record<string, string>>(() => {
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }, [token]);

  const value: AuthContextValue = {
    status,
    principal,
    authHeader,
    setPrincipal: () => {
      // No-op: role/patientId are resolved server-side in real mode.
    },
    login: () => {
      // Triggered by the landing page's "Sign in" button.
      instance.loginRedirect(apiTokenRequest).catch((err) => {
        console.error("[auth] loginRedirect failed", err);
        setErrorMessage(err instanceof Error ? err.message : String(err));
        setStatus("error");
      });
    },
    logout: () => instance.logoutRedirect(),
    refreshMe: () => setTick((t) => t + 1),
    errorMessage,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
