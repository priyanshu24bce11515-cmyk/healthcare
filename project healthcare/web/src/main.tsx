import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { DemoAuthProvider, IS_DEMO_MODE } from "./lib/auth";
import "./index.css";

function renderApp(children: React.ReactNode) {
  ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode>{children}</React.StrictMode>);
}

async function bootstrap() {
  if (IS_DEMO_MODE) {
    // Dev-only fast path — never reached in a real deployment. Skips loading
    // the MSAL bundle entirely (nothing here imports it), matching the
    // backend's ALLOW_DEMO_PRINCIPAL gate.
    renderApp(
      <DemoAuthProvider>
        <App />
      </DemoAuthProvider>,
    );
    return;
  }

  const [{ PublicClientApplication, EventType }, { MsalProvider }, { buildMsalConfig, OIDC_METADATA_URL }, { RealAuthProvider }] =
    await Promise.all([
      import("@azure/msal-browser"),
      import("@azure/msal-react"),
      import("./lib/msalConfig"),
      import("./lib/realAuth"),
    ]);

  // Pre-fetch the tenant's OIDC metadata ourselves and hand it to MSAL as
  // authorityMetadata — MSAL 5.x can't resolve *.ciamlogin.com endpoints on
  // its own (see msalConfig.ts). Failing here gives a clear message instead
  // of MSAL's opaque endpoints_resolution_error mid-login.
  let authorityMetadata: string;
  try {
    const resp = await fetch(OIDC_METADATA_URL);
    if (!resp.ok) throw new Error(`metadata fetch returned HTTP ${resp.status}`);
    authorityMetadata = await resp.text();
  } catch (err) {
    console.error("[auth] failed to fetch OIDC metadata from", OIDC_METADATA_URL, err);
    document.getElementById("root")!.innerHTML =
      `<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:sans-serif;color:#52514e;padding:2rem;text-align:center">Could not reach the sign-in service. Check your network and that VITE_EXTERNAL_ID_TENANT_SUBDOMAIN is correct, then reload.</div>`;
    return;
  }

  const msalConfig = buildMsalConfig(authorityMetadata);

  // A crashed/interrupted redirect (e.g. the tab was closed mid-login) can
  // leave MSAL's "interaction in progress" lock behind in sessionStorage,
  // deadlocking every future load of this tab on "Signing you in…". At cold
  // start, if the URL carries no auth response, no interaction can actually
  // be in progress — clear any stale lock. (https://aka.ms/msal.js.errors)
  const authResponseInUrl = /[#?].*(code=|error=|state=)/.test(window.location.href);
  if (!authResponseInUrl) {
    for (const key of Object.keys(sessionStorage)) {
      if (key.includes("interaction.status")) {
        console.warn("[auth] clearing stale MSAL interaction lock:", key);
        sessionStorage.removeItem(key);
      }
    }
  }

  const msalInstance = new PublicClientApplication(msalConfig);
  await msalInstance.initialize();

  const existingAccounts = msalInstance.getAllAccounts();
  if (existingAccounts.length > 0) {
    msalInstance.setActiveAccount(existingAccounts[0]);
  }
  msalInstance.addEventCallback((event) => {
    if (event.eventType === EventType.LOGIN_SUCCESS && event.payload && "account" in event.payload) {
      const account = event.payload.account;
      if (account) msalInstance.setActiveAccount(account);
    }
  });

  // Completes the redirect-back-from-sign-in navigation before first render.
  await msalInstance.handleRedirectPromise();

  renderApp(
    <MsalProvider instance={msalInstance}>
      <RealAuthProvider>
        <App />
      </RealAuthProvider>
    </MsalProvider>,
  );
}

bootstrap();
