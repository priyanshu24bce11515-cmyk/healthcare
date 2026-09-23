import type { Principal } from "./types";

const PATIENT_COLOR_CYCLE = ["blue", "green", "red"] as const;

export type RoleTheme = "patient-blue" | "patient-green" | "patient-red" | "provider" | "caregiver";

export function getRoleTheme(principal: Principal): RoleTheme {
  if (principal.role === "provider") return "provider";
  if (principal.role === "caregiver") return "caregiver";

  const id = Number.isFinite(principal.patientId) ? Math.floor(principal.patientId) : 0;
  const idx = ((id % PATIENT_COLOR_CYCLE.length) + PATIENT_COLOR_CYCLE.length) % PATIENT_COLOR_CYCLE.length;
  return `patient-${PATIENT_COLOR_CYCLE[idx]}` as RoleTheme;
}
