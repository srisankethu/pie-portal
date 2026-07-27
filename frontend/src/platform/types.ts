export type Role = "SALESPERSON" | "SALES_MANAGER" | "OWNER";

export interface PlatformSession {
  token: string;
  role: Role;
  name: string;
  user_id: string;
  organization_id: string;
}

export interface DecisionSummary {
  decision_id: string;
  decision_type: string;
  subject_entity_type: string;
  subject_entity_id: string;
  assigned_user_id: string | null;
  assigned_role: string;
  detected_at: string;
  priority_band: "LOW" | "MEDIUM" | "HIGH";
  priority_score: number;
  status: string;
  ai_status: string;
  human_action: { action: string; actor_user_id: string; acted_at: string; note?: string } | null;
  created_at: string;
  updated_at: string;
}

export interface Fact {
  label: string;
  value: string | number | boolean;
  restricted: boolean;
  source: string;
}

export interface Interpretation {
  status: string; // OK | DEGRADED | FAILED | SUPPRESSED | PENDING
  title: string | null;
  recommendation: string | null;
  explanation: string | null;
  caveat: string | null;
  should_surface: boolean;
  model: string | null;
}

export interface DecisionDetail {
  decision_id: string;
  decision_type: string;
  subject_entity_type: string;
  subject_entity_id: string;
  subject_label: string;
  assigned_user_id: string | null;
  assigned_role: string;
  detected_at: string | null;
  priority: { band: string; score: number; deterministic_base: number; ai_adjustment: number };
  status: string;
  facts: Fact[];
  evidence: { source_system?: string; record_type?: string; record_id?: string }[];
  signal: { type: string; severity_base: number; window: unknown; sufficiency: Record<string, unknown> } | null;
  interpretation: Interpretation;
  confidence: { evidence_sufficiency?: string; reasons?: string[] };
  human_action: DecisionSummary["human_action"];
  outcome: unknown | null;
}

export interface Account {
  customer_id: string;
  name: string;
  status: string;
  assigned_user_id: string | null;
}

export interface ConnectionState {
  state: "CONNECTED" | "SAMPLE_DATA" | "ERROR" | "UNREACHABLE" | "WRONG_ORG";
  headline: string;
  detail: string | null;
  source: string;
  organization_id?: string;
  organization_name?: string;
  currency?: string;
  api_base?: string;
  history_days?: number;
  visible_organizations?: { organization_id: string; name: string }[];
}

export interface SyncRun {
  status: string;
  source: string;
  started_at: string | null;
  finished_at: string | null;
  customers: number;
  products: number;
  sales_txns: number;
  cost_records: number;
  skipped_count: number;
  skipped_sample: { kind?: string; ref?: string; code?: string; detail?: string }[];
  signals_emitted: number;
  decisions_created: number;
  error: string | null;
}

export interface DataStatus {
  connection: ConnectionState;
  last_sync: SyncRun | null;
  read_model: Record<string, number>;
  can_sync: boolean;
}
