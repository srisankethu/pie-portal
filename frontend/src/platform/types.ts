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
  state: "CONNECTED" | "SAMPLE_DATA" | "ERROR" | "UNREACHABLE" | "WRONG_ORG" | "NOT_CONFIGURED";
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

/** What an owner submits to connect (or replace) this organization's own
 *  Zoho Books account. Always a full replace — there is no partial update. */
export interface ZohoConnectionInput {
  zoho_organization_id: string;
  client_id: string;
  client_secret: string;
  refresh_token: string;
  accounts_base?: string;
  api_base?: string;
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
  since: string | null;
  documents_fetched: number;
  documents_resumed: number;
  assignments: number;
}

/** What to pull. `since` is the operator's judgement about how far back the
 *  books are worth reading; `full` discards the resume cursor. `connection_id`
 *  names one company; omitted means every enabled connection in turn. */
export interface SyncOptions {
  since?: string;
  full?: boolean;
  connection_id?: string;
}

export interface DataStatus {
  connection: ConnectionState;
  last_sync: SyncRun | null;
  read_model: Record<string, number>;
  can_sync: boolean;
  can_manage_connection: boolean;
}

// ── Customer × Item commercial intelligence ─────────────────────────────────
// All of it is cost/margin, so every one of these surfaces is manager/owner
// only — there is no salesperson-safe projection of a margin analysis.

/** A margin is a ratio (0.261); it becomes a percentage at the render edge.
 *  A `_pp` value is a percentage-POINT difference, never a percent change. */
export interface CustomerItemRow {
  product_id: string;
  item_name: string;
  item_code: string | null;
  revenue_12m: number | null;
  current_margin: number | null;
  historical_margin: number | null;
  peer_median_margin: number | null;
  peer_count: number;
  margin_change_pp: number | null;
  cost_change_pct: number | null;
  price_change_pct: number | null;
  volume_change_pct: number | null;
  erosion_kind: string | null;
  historical_margin_gap: number | null;
  peer_margin_gap: number | null;
  annualized_historical_margin_gap: number | null;
  signals: string[];
  data_sufficiency: "SUFFICIENT" | "PARTIAL" | "INSUFFICIENT";
  sufficiency_reasons: string[];
  last_transaction_date: string | null;
  transaction_count: number;
}

export interface PortfolioSummary {
  customer_id: string;
  revenue_12m: number | null;
  gross_profit_12m: number | null;
  gross_margin_12m: number | null;
  active_items: number;
  items_with_margin_erosion: number;
  items_below_peer_benchmark: number;
  items_cost_not_passed: number;
  items_margin_down_volume_up: number;
  material_gap_items: number;
  historical_margin_gap: number | null;
  peer_benchmark_gap: number | null;
  items_without_cost: number;
}

export interface CustomerPortfolio {
  customer: { customer_id: string; name: string };
  summary: PortfolioSummary;
  /** Already ranked by economic materiality — do not re-sort by percentage. */
  items_requiring_attention: CustomerItemRow[];
  all_items: CustomerItemRow[];
  computed_at: string | null;
}

export interface PeerRow {
  customer_id: string;
  name: string;
  net_sell_price: number | null;
  margin: number | null;
  qty: number | null;
  txn_count: number;
  last_transaction_date: string | null;
  is_subject: boolean;
}

export interface CustomerItemDetail {
  customer: { customer_id: string; name: string };
  item: { product_id: string; name: string; code: string | null; uom: string | null };
  as_of: string;
  headline: {
    revenue_recent: number | null;
    revenue_12m: number | null;
    gross_profit_recent: number | null;
    gross_profit_12m: number | null;
    current_margin: number | null;
    historical_margin: number | null;
    margin_change_pp: number | null;
    qty_recent: number | null;
    current_sell_price: number | null;
    current_effective_cost: number | null;
    historical_margin_gap: number | null;
    annualized_historical_margin_gap: number | null;
    peer_median_margin: number | null;
    peer_count: number;
  };
  /** Deterministic prose rendered from computed values — never AI-generated. */
  diagnosis: string[];
  data_quality: {
    data_sufficiency: "SUFFICIENT" | "PARTIAL" | "INSUFFICIENT";
    reasons: string[];
    transaction_count: number;
    cost_covered_txns: number;
    cost_missing_txns: number;
    history_months: number;
  };
  series: { date: string; net_sell_price: number | null; effective_cost: number | null;
            margin: number | null; qty: number | null }[];
  margin_periods: {
    current: number | null; previous: number | null; m3: number | null;
    m6: number | null; m12: number | null; historical: number | null;
  };
  peers: {
    median_price: number | null;
    median_margin: number | null;
    price_deviation_pct: number | null;
    margin_deviation_pp: number | null;
    peer_count: number;
    is_reliable: boolean;
    window_days: number;
    rows: PeerRow[];
    subject: PeerRow | null;
  };
  volume_vs_margin: { period_start: string; period_end: string; qty: number | null;
                      revenue: number | null; margin: number | null; txn_count: number }[];
  transactions: {
    date: string; invoice_id: string | null; external_ref: string; qty: number | null;
    rate: number | null; discount_percent: number | null; net_sell_price: number | null;
    effective_cost: number | null; revenue: number | null; cogs: number | null;
    gross_profit: number | null; margin: number | null; cost_source: string | null;
  }[];
}

/* ── users, approvals and policy ──────────────────────────────────────────── */
export type ApprovalKindName = "QUOTE_LINE_PRICE" | "QUOTE_SUBMISSION" | "DECISION_ESCALATION";
export type ApprovalStatusName =
  | "PENDING" | "APPROVED" | "REJECTED" | "CHANGES_REQUESTED" | "WITHDRAWN";

export interface ApprovalThreadEntry {
  at: string;
  user_id: string;
  name: string;
  action: string;
  note: string | null;
}

export interface ApprovalRequest {
  approval_request_id: string;
  kind: ApprovalKindName;
  status: ApprovalStatusName;
  required_authority: "MANAGER" | "OWNER";
  subject_id: string;
  subject_line_id: string | null;
  title: string;
  summary: string;
  reason: string | null;
  reason_code: string | null;
  requested_by: string;
  requested_by_user_id: string;
  requested_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  thread: ApprovalThreadEntry[];
  can_decide: boolean;
  is_open: boolean;
  /** Carries cost and margin — absent for a salesperson, even on their own request. */
  subject?: Record<string, unknown>;
}

export interface QuoteGate {
  quote_id: string;
  can_submit: boolean;
  blocked_reason: string | null;
  outcome: string;
  requests: ApprovalRequest[];
  policy: { require_approval_for_quotes: boolean };
}

export interface PlatformUser {
  user_id: string;
  email: string | null;
  name: string;
  role: Role;
  active: boolean;
  has_password: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string | null;
  created_by: string | null;
  role_changed_by: string | null;
  role_changed_at: string | null;
}

export interface OrgPolicy {
  require_approval_for_quotes: boolean;
  require_approval_below_review_floor: boolean;
  below_cost_requires_owner: boolean;
  allow_self_approval: boolean;
  escalation_creates_approval: boolean;
  updated_at: string | null;
}

export interface ThresholdView {
  version: string;
  target_margin_default: number;
  target_margin_by_family: Record<string, number>;
  min_margin: number;
  margin_floor: number;
  sales_discretion_band: number;
  quantity_band_edges: number[];
  min_quote_exception_impact_rupees: number;
  recent_days: number;
  min_transactions: number;
  min_peer_customers: number;
}

/* ── margin policy ─────────────────────────────────────────────────────────
 * Editable by an owner. It was read-only on the grounds that a silently
 * editable threshold cannot be reproduced against, which the version hash
 * answers: every metric row, signal and quote snapshot records the threshold
 * version that produced it, and an edit produces a new one.
 *
 * A `ratio` is a fraction (0.24), never a percentage — the input renders it as
 * one and converts back, because typing 24 into a field that means 0.24 is the
 * single easiest way to set a 2400% target.
 */
export type PolicyKind = "ratio" | "rupees" | "band_edges" | "family_margins";

export interface PolicyField {
  field: string;
  label: string;
  help: string;
  value: number | number[] | Record<string, number>;
  default: number | number[] | Record<string, number>;
  overridden: boolean;
  kind: PolicyKind;
}

export interface MarginPolicy {
  version: string;
  default_version: string;
  fields: PolicyField[];
  updated_at: string | null;
}

/** Analysis internals: shown for context, deliberately not editable here. */
export interface FixedThresholds {
  recent_days: number;
  previous_days: number;
  historical_lookback_days: number;
  min_transactions: number;
  min_peer_customers: number;
  min_cost_coverage: number;
  peer_recency_days: number;
}

/** Any subset of the editable fields; `clear` resets a field to its default. */
export interface MarginPolicyPatch {
  target_margin_default?: number;
  target_margin_by_family?: Record<string, number>;
  min_margin?: number;
  margin_floor?: number;
  sales_discretion_band?: number;
  quantity_band_edges?: number[];
  min_quote_exception_impact_rupees?: number;
  min_material_gap_rupees?: number;
  min_margin_deterioration_pp?: number;
  clear?: string[];
}

/* ── Zoho credentials ──────────────────────────────────────────────────────
 * One OAuth grant, usable by several organizations. A refresh token belongs to
 * a Zoho user rather than a company, so one grant already reaches every company
 * that user can see — separate legal entities do not need separate secrets, and
 * pretending otherwise turns one rotation into N.
 */
export interface ZohoCredential {
  credential_id: string;
  label: string;
  client_id: string;
  owner_organization_id: string;
  is_owner: boolean;
  shared_with_organization_ids: string[];
  accounts_base: string;
  api_base: string;
  rotated_at: string | null;
  created_at: string | null;
  used_by: { organization_id: string; zoho_organization_id: string }[];
}

export interface ZohoVisibleOrg {
  organization_id: string;
  name: string;
  already_connected: boolean;
}

/* ── connections ───────────────────────────────────────────────────────────
 * One organization, as many Zoho companies as it has books to read. Health is
 * per connection, because "the organization is connected" stops meaning
 * anything once there are three and one has a revoked token.
 */
export interface ZohoConnection {
  connection_id: string;
  label: string;
  zoho_organization_id: string;
  enabled: boolean;
  credential_id: string | null;
  client_id: string | null;
  credential_label: string;
  credential_rotated_at: string | null;
  accounts_base: string;
  api_base: string;
  last_checked_at: string | null;
  last_check_ok: boolean | null;
  last_check_detail: string | null;
  created_at: string | null;
}

/** A check result: the row as stored, plus what the grant could actually see. */
export interface ConnectionCheck extends ZohoConnection {
  checked: boolean;
  ok?: boolean;
  detail?: string;
  organization_name?: string;
  currency?: string;
  visible_organizations?: { organization_id: string; name: string }[];
}

/** A scope, and what the platform loses without it. */
export interface RequiredScope {
  scope: string;
  why: string;
  required: boolean;
}

/** The credential summary carried by the connections view — `used_by` here is
 *  a count, unlike the fuller `ZohoCredential` returned by the credentials
 *  endpoint. */
export interface ConnectionCredential {
  credential_id: string;
  label: string;
  client_id: string;
  is_owner: boolean;
  rotated_at: string | null;
  used_by: number;
}

export interface ConnectionsView {
  connections: ZohoConnection[];
  credentials: ConnectionCredential[];
  required_scopes: RequiredScope[];
  scope_string: string;
  can_manage: boolean;
  source_mode: string;
  /** The consequence of sharing an organization between companies, said out loud. */
  pooling_note: string;
}

/** What an owner submits to add a company: either a credential already on file,
 *  or a fresh set of secrets. */
export interface NewConnectionInput {
  zoho_organization_id: string;
  label?: string;
  credential_id?: string;
  client_id?: string;
  client_secret?: string;
  refresh_token?: string;
  accounts_base?: string;
  api_base?: string;
}
