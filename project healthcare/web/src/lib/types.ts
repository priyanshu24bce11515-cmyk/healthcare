export type Role = "patient" | "provider" | "caregiver";

export interface Principal {
  userId: string;
  role: Role | null; // null: verified identity, not yet linked to a role (real-auth onboarding state)
  patientId: number;
  /** The signed-in person's own name (their Patient/Provider/Caregiver row's
   * name) — who *they* are, not who they're currently viewing. Undefined in
   * demo mode (no real identity to name) or before onboarding. */
  name?: string | null;
}

export interface MeResponse {
  userId: string;
  role: Role | null;
  patientId: number | null;
  name: string | null;
}

export interface Patient {
  id: number;
  name: string;
  dob: string;
  sex: string;
}

export interface VitalReading {
  value: number;
  unit: string;
  recordedAt: string;
  source: string;
}

export interface AreaScore {
  area: string;
  score: number;
  reason: string;
  computedAt: string;
}

export interface Recommendation {
  id: number;
  text: string;
  reason: string;
  category: string;
  priority: "Low" | "Medium" | "High";
  priorityScore: number | null;
  generatedAt: string;
  actedOn: boolean;
  dismissedAt: string | null;
  disclaimer?: string;
}

export interface AlertItem {
  id: number;
  kind: "vital" | "adherence" | "risk";
  severity?: "info" | "warning" | "critical" | null;
  detail: string;
  value: string;
  raisedAt: string;
  acknowledgedBy?: string | null;
}

export interface Appointment {
  id: number;
  startsAt: string;
  type: string;
  status: string;
  providerName: string;
  specialty: string;
}

export interface Provider {
  id: number;
  name: string;
  specialty: string;
  contact: string;
}

export interface DashboardPayload {
  patient: Patient;
  latestVitals: Record<string, VitalReading>;
  riskScores: Record<string, AreaScore>;
  recommendations: Recommendation[];
  unacknowledgedAlerts: AlertItem[];
  upcomingAppointments: Appointment[];
  disclaimer: string;
}

export interface Goal {
  id: number;
  kind: string;
  target: number;
  progress: number;
  period: string;
}

export interface AnalyticsPayload {
  vitalsTrend: Record<string, { value: number; recordedAt: string }[]>;
  riskScoreHistory: { area: string; score: number; computedAt: string }[];
  goals: Goal[];
}

export interface Medication {
  id: number;
  name: string;
  dosage: string;
  schedule: string;
  startDate: string;
  endDate: string | null;
}

export interface MedicationAdherence {
  medicationId: number;
  name: string;
  schedule: string;
  taken: number;
  missed: number;
  total: number;
  adherencePct: number;
}

export interface AdherenceSummary {
  medications: MedicationAdherence[];
  patterns: string[];
}

export interface Caregiver {
  id: number;
  name: string;
  contact: string;
  relationship: string;
  accessScope: string;
  accessLevel: "view_only" | "full";
  accepted: boolean;
}

export type ClaimStatus = "draft" | "ready" | "submitted" | "processing" | "approved" | "denied";

export interface Claim {
  id: number;
  provider: string;
  amount: number | null;
  diagnosisCodes: string | null;
  status: ClaimStatus;
  denialReason?: string | null;
  appealGuidance?: string | null;
  extractedFields: Record<string, string[]>;
  missingFields: string[];
}

export interface ClaimHistoryEntry {
  status: ClaimStatus;
  changedBy: string;
  changedAt: string;
  note: string | null;
}
