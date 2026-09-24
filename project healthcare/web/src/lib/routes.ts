import type { Role } from "./types";

/** Single source of truth for "which roles can see this page" — used by both
 * the navbar (to decide which links to show) and the router (to actually
 * block/redirect a role from a page it has no link to, so typing the URL
 * directly — or a picker programmatically navigating there — can't land a
 * caregiver on the unscoped Dashboard, which doesn't respect accessScope the
 * way /caregiver-view does). */
export interface RouteDef {
  to: string;
  label: string;
  roles: Role[];
}

export const ROUTES: RouteDef[] = [
  { to: "/", label: "Dashboard", roles: ["patient", "provider"] },
  { to: "/risk-score", label: "Risk Score", roles: ["patient", "provider"] },
  { to: "/recommendations", label: "Recommendations", roles: ["patient"] },
  { to: "/meds", label: "Medications", roles: ["patient", "provider"] },
  { to: "/schedule", label: "Schedule", roles: ["patient", "provider"] },
  { to: "/caregiver", label: "Caregiver", roles: ["patient", "caregiver"] },
  { to: "/claims", label: "Claim Assistant", roles: ["provider"] },
  { to: "/analytics", label: "Analytics", roles: ["patient", "provider"] },
  { to: "/patients", label: "Patients", roles: ["provider", "caregiver"] },
];

/** Where to land a role that just picked/confirmed a patient, or hit a page
 * it isn't allowed to see. */
export function defaultPathFor(role: Role | null): string {
  if (role === "caregiver") return "/caregiver";
  return "/";
}
