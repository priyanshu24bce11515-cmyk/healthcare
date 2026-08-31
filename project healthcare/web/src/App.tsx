import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Navbar } from "./components/Navbar";
import { RequireActivePatient } from "./components/RequireActivePatient";
import { RoleGate } from "./components/RoleGate";
import { ActivePatientProvider } from "./lib/activePatient";
import { useAuth } from "./lib/auth";
import { getRoleTheme } from "./lib/theme";
import { Analytics } from "./pages/Analytics";
import { Caregiver } from "./pages/Caregiver";
import { ClaimAssistant } from "./pages/ClaimAssistant";
import { Dashboard } from "./pages/Dashboard";
import { Landing } from "./pages/Landing";
import { Meds } from "./pages/Meds";
import { Onboarding } from "./pages/Onboarding";
import { Patients } from "./pages/Patients";
import { Recommendations } from "./pages/Recommendations";
import { RiskScore } from "./pages/RiskScore";
import { Schedule } from "./pages/Schedule";

function FullPageStatus({ message, retry }: { message: string; retry?: boolean }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-plane px-6 text-center">
      <p className="text-sm text-ink-secondary">{message}</p>
      {retry && (
        <button
          onClick={() => window.location.reload()}
          className="rounded-md border border-line-border bg-surface px-3 py-1.5 text-sm text-ink-secondary transition-colors duration-150 hover:text-ink-primary"
        >
          Try signing in again
        </button>
      )}
    </div>
  );
}

export default function App() {
  const { status, principal, errorMessage } = useAuth();
  const roleTheme = getRoleTheme(principal);

  // "loading": MSAL is still checking for an existing session (brief).
  if (status === "loading") {
    return <FullPageStatus message="Loading…" />;
  }
  // "unauthenticated": no session — show the landing page with a Sign in button.
  if (status === "unauthenticated") {
    return <Landing />;
  }
  if (status === "error") {
    return <FullPageStatus message={`Sign-in failed: ${errorMessage ?? "unknown error"}`} retry />;
  }

  return (
    <BrowserRouter>
      <ActivePatientProvider>
        <div data-role-theme={roleTheme} className="min-h-screen bg-plane">
          <Navbar />
          <main className="mx-auto max-w-[1440px] px-6 py-8 lg:px-10">
            {status === "onboarding" ? (
              <Onboarding />
            ) : (
              <Routes>
                <Route
                  path="/"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <Dashboard />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/risk-score"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <RiskScore />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/recommendations"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <Recommendations />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/meds"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <Meds />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/schedule"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <Schedule />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/caregiver"
                  element={
                    <RoleGate>
                      <Caregiver />
                    </RoleGate>
                  }
                />
                <Route
                  path="/claims"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <ClaimAssistant />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/analytics"
                  element={
                    <RoleGate>
                      <RequireActivePatient>
                        <Analytics />
                      </RequireActivePatient>
                    </RoleGate>
                  }
                />
                <Route
                  path="/patients"
                  element={
                    <RoleGate>
                      <Patients />
                    </RoleGate>
                  }
                />
              </Routes>
            )}
          </main>
        </div>
      </ActivePatientProvider>
    </BrowserRouter>
  );
}
