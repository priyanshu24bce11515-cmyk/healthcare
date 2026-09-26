import type { Configuration } from "@azure/msal-browser";

// Microsoft Entra External ID — a documented substitution for the brief's
// "Azure AD B2C" (classic B2C closed to new customers May 2025).
// Filled in from web/.env once the tenant + app registrations exist
// (infra/README.md "What's deliberately manual").
const tenantSubdomain = import.meta.env.VITE_EXTERNAL_ID_TENANT_SUBDOMAIN ?? "";

const authority = `https://${tenantSubdomain}.ciamlogin.com/${tenantSubdomain}.onmicrosoft.com`;

// MSAL 5.x has no built-in knowledge of *.ciamlogin.com hosts, so its network
// endpoint discovery (instance discovery + OIDC metadata) fails for this
// tenant — the metadata's issuer host ({guid}.ciamlogin.com) doesn't match the
// authority host, and instance discovery 400s. We sidestep all of it by
// pre-fetching the OIDC document ourselves (see main.tsx) and handing it to
// MSAL as authorityMetadata, so it never does network resolution.
export const OIDC_METADATA_URL = `${authority}/v2.0/.well-known/openid-configuration`;

export function buildMsalConfig(authorityMetadata: string): Configuration {
  return {
    auth: {
      clientId: import.meta.env.VITE_EXTERNAL_ID_SPA_CLIENT_ID ?? "",
      authority,
      knownAuthorities: [`${tenantSubdomain}.ciamlogin.com`],
      authorityMetadata,
      redirectUri: "/",
      postLogoutRedirectUri: "/",
    },
    cache: {
      // sessionStorage over localStorage: the cached tokens grant access to
      // PHI, so we trade "signed in across every open tab" for a smaller
      // XSS blast radius (a compromised script can only lift the current
      // tab's session, not every tab's, and nothing survives the browser
      // closing). Each new tab does its own silent/redirect sign-in.
      cacheLocation: "sessionStorage",
    },
  };
}

// The Web API app's exposed scope (exact string shown in the portal under
// "Expose an API" on the API app registration — do not guess the shape).
export const apiTokenRequest = {
  scopes: [import.meta.env.VITE_EXTERNAL_ID_API_SCOPE ?? ""],
};
