export type Role = "SALESPERSON" | "SALES_MANAGER" | "OWNER";

export interface PlatformSession {
  token: string;
  role: Role;
  name: string;
  user_id: string;
  organization_id: string;
  /** ISO code this organization trades in, from the sign-in response. Drives
   *  every money figure the client renders. */
  currency: string;
  /** IANA zone this organization's *day* is measured in. Drives every
   *  timestamp the client renders — see `src/when.ts` for why the browser's
   *  own zone is the wrong answer here. */
  timezone: string;
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
  /** Which connected company this decision's *subject* belongs to. Not
   *  `origin` — a decision's origin is STATE or SIGNAL, and one key cannot
   *  carry both meanings. */
  subject_origin?: EntityOrigin | null;
  sources_differ?: boolean;
}

export interface Fact {
  label: string;
  value: string | number | boolean;
  restricted: boolean;
  source: string;
}

export interface Interpretation {
  status: string; // OK | DEGRADED | FAILED | SUPPRESSED | PENDING | NOT_APPLICABLE
  title: string | null;
  recommendation: string | null;
  explanation: string | null;
  caveat: string | null;
  should_surface: boolean;
  model: string | null;
}

/** What a situation is worth. Present on a decision folded from Business
 *  State; empty on one raised from a signal, which measures the shape of the
 *  evidence rather than money. */
export interface DecisionImpact {
  /** The ranking input, as a decimal string — never a float. */
  financial?: string;
  /** What the number *is*, in words. ₹4,00,000 of capital locked and ₹4,00,000
   *  of annual holding cost are different claims, and a figure without this
   *  sentence invites the reader to assume the wrong one. */
  basis?: string;
  /** A recurring bleed, where the situation has one. */
  monthly?: string | null;
  operational?: Record<string, unknown>;
}

/** A business action this situation permits. Presented, never chosen. */
export interface DecisionAction {
  key: string;
  label: string;
}

/** The ranking, shown working, so a reader can check a row's position rather
 *  than trusting it. */
export interface DecisionRanking {
  score?: number;
  money_points?: number;
  urgency_points?: number;
  financial?: string;
  rupees_per_point?: string;
  days_past_due?: number | null;
  money_cap?: number;
  urgency_cap?: number;
}

/** Why a decision exists, all the way down:
 *  decision → impact → state → transition → event → ERP record. */
export interface DecisionTrace {
  decision_id: string;
  decision_type: string;
  origin: string;
  subject_label: string;
  impact: DecisionImpact;
  rationale: string | null;
  ranking: DecisionRanking;
  states: {
    state: string;
    key: string;
    label: string;
    as_of: string;
    value: Record<string, unknown>;
    event_count: number;
    thresholds_version: string | null;
    transitions_total: number;
    /** Where this page starts. The chain is paged newest-first: the
     *  transitions of one state key are a bounded set re-derived by each fold,
     *  not a growing feed, so an offset is a page number rather than a
     *  place-holder in a stream. */
    transitions_offset: number;
    has_more: boolean;
    transitions: {
      event_seq: number;
      event_type: string;
      occurred_on: string;
      /** `[op, field, value]` — the arithmetic this event performed. */
      changes: [string, string, unknown][];
      erp: { system: string; record_type: string; record_id: string;
             line_id: string | null; modified_at: string | null } | null;
    }[];
  }[];
  /** Set when there is no chain to walk, with the reason. A signal decision
   *  has no state to drill into, and saying so beats an empty list that reads
   *  like missing data. */
  unavailable: string | null;
}

export interface DecisionDetail {
  /** Which connected company the *subject* belongs to. Distinct from `origin`
   *  below, which says whether this decision was folded from state or raised
   *  from a signal — two different questions, so two different keys. */
  subject_origin?: EntityOrigin | null;
  sources_differ?: boolean;
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
  /** Which producer made this. "SIGNAL" is a detector over sales and cost
   *  lines, interpreted by a model; "STATE" is arithmetic over folded Business
   *  State with no model involved. The card renders two different things, and
   *  asking the row beats sniffing which fields are populated. */
  origin: string;
  impact: DecisionImpact;
  rationale: string | null;
  actions: DecisionAction[];
  ranking: DecisionRanking;
  /** The state fields the impact was computed from — the reader's audit trail.
   *  Every number on the card must be reproducible from these. */
  state_evidence: Record<string, unknown>;
  state: { keys: string[]; as_of: string | null; thresholds_version?: string | null };
}

/** Where an imported record came from.
 *
 *  The hierarchy is connector → connected company → record, and it is the same
 *  for customers, items, vendors and everything a future connector imports.
 *  One shape, so a customer picker and an item picker cannot describe their
 *  source two different ways. See `backend/app/domain/origin.py`. */
export interface EntityOrigin {
  connector: string | null;
  connector_label: string;
  connector_short: string;
  /** A text mark, not a colour: a colour-only badge is unreadable to a
   *  substantial minority of users and meaningless in print. */
  icon: string;
  connection_id: string | null;
  company: string;
  /** The id the source system gave this record — a lookup key, not a name. */
  external_id: string;
  /** Nothing recorded where this came from. Rendered as such, never guessed. */
  unknown: boolean;
}

/** Carried on every list of imported entities. Provenance is only *information*
 *  when there is more than one source; below that every badge says the same
 *  thing and costs width on every screen. */
export interface Sourced {
  origin?: EntityOrigin | null;
  sources_differ?: boolean;
}

export interface Account extends Sourced {
  customer_id: string;
  name: string;
  status: string;
  assigned_user_id: string | null;
  /** Operational trade, so the directory can be chosen from rather than only
   *  searched. No cost, no margin — those live behind the Customer × Item
   *  surface where the permission gating is. */
  last_order: string | null;
  orders_12m: number;
  revenue_12m: number;
}

/** Which slice of a master list to show. Active by default everywhere: the
 *  pull reads inactive rows because their history has to resolve, which is not
 *  a reason to put a retired item or a dormant 2019 account in front of
 *  somebody about to quote. */
export type StatusFilter = "active" | "inactive" | "all";

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

/* ── sync as a background job ───────────────────────────────────────────────
 * A pull reads every invoice and bill individually and takes minutes, so it
 * runs in the background and the screen renders from this state rather than
 * from whatever the last request happened to return.
 */
export type SyncStatus = "IDLE" | "QUEUED" | "RUNNING" | "OK" | "PARTIAL" | "FAILED";

export interface SyncRun {
  sync_run_id: string;
  status: SyncStatus;
  /** What the run is doing right now, in words. Null once it is over. */
  phase: string | null;
  active: boolean;
  /** Calendar slices of the requested window. A real denominator: the months
   *  between the start date and today are known before the first API call,
   *  unlike a document count Zoho will not reveal in advance. */
  windows_total: number;
  windows_done: number;
  source: string;
  connection_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  since: string | null;
  customers: number;
  products: number;
  sales_txns: number;
  cost_records: number;
  vendors: number;
  stock_snapshots: number;
  payments: number;
  purchase_orders: number;
  sales_orders: number;
  vendor_payments: number;
  documents_fetched: number;
  documents_resumed: number;
  assignments: number;
  signals_emitted: number;
  decisions_created: number;
  skipped_count: number;
  skipped_sample: { kind: string; ref: string; code: string; detail: string;
                    context?: Record<string, unknown> }[];
  /** One row per thing to fix, not per row skipped. See `SyncReport.unresolved`. */
  unresolved: UnresolvedReference[];
  error: string | null;
  /** What the finished run wants to report — cleared sample data, metric rebuild. */
  notes: { demo_data_removed?: Record<string, number>; commercial?: Record<string, unknown> };
}

/** A reference the pull could not resolve, folded across every line it blocked.
 *
 *  `lines` is the point: one discontinued item on four hundred bill lines is
 *  one problem, and a list that shows it four hundred times describes the
 *  symptom instead of the cause. */
export interface UnresolvedReference {
  kind: string;
  code: string;
  missing_id: string | null;
  /** The item or customer name as written on the document — the master has no
   *  such record, so the document line is the only place the name survives. */
  label?: string | null;
  sku?: string | null;
  fix?: string | null;
  lines: number;
  value: number;
  first_seen?: string | null;
  last_seen?: string | null;
  examples: { document?: string; date?: string; party?: string;
              qty?: number | string; value?: number }[];
}

/** One item an account has bought. Identity only — no price, no cost. The date
 *  is carried because two inserts with near-identical names are told apart by
 *  when they were last bought, not by their ids. */
export interface AccountItem extends Sourced {
  product_id: string;
  name: string;
  sku: string;
  active: boolean;
  last_bought: string | null;
}

export interface SyncState {
  state: SyncStatus;
  /** The job in flight, or null. Null is what re-enables the button. */
  active: SyncRun | null;
  /** The most recent run that actually ended, whatever the outcome. */
  last: SyncRun | null;
  /** Excludes PARTIAL — it wrote rows but did not finish. */
  last_successful_at: string | null;
  /** Every pull in flight, one per company. `active` is the newest of these,
   *  kept for the headline; this is what lets the screen show two at once. */
  active_runs: SyncRun[];
  /** The connection ids in `active_runs`. Each company's button gates on its
   *  own membership, not on whether *some* sync is running. */
  busy_connections: string[];
  /** Whether an organization-wide pull — every company, one job — may start.
   *  Not a gate on a single company's button. */
  can_start: boolean;
}

export interface SyncStartResponse extends SyncState {
  /** False when an existing job was handed back instead of a new one. */
  started: boolean;
  run: SyncRun;
  note: string;
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
  transactions: CustomerItemTxn[];
}

/** One line of one customer's history with one item.
 *
 * Named rather than left inline because the grid that renders it is typed on
 * it — an anonymous shape means the columns fall back to `unknown` and a
 * mistyped field name compiles.
 */
export interface CustomerItemTxn {
  date: string;
  invoice_id: string | null;
  external_ref: string;
  qty: number | null;
  rate: number | null;
  discount_percent: number | null;
  net_sell_price: number | null;
  /** Null where no bill covers this sale. A gap in the purchase history, not a
   *  zero — the screen says "no cost" rather than showing a dash. */
  effective_cost: number | null;
  revenue: number | null;
  cogs: number | null;
  gross_profit: number | null;
  margin: number | null;
  cost_source: string | null;
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
  min_quote_exception_impact: number;
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
/** How the settings screen renders a policy field, and how it sends it back.
 *
 * Mirrors `commercial/policy._kind`. `ratio` is the server's fall-through, and
 * anything landing there is scaled by 100 and given a percent sign — which is
 * why `days` and `flag` are named rather than left to it. A 365-day threshold
 * rendered as a ratio reads "36500 %". */
export type PolicyKind =
  | "ratio" | "money" | "days" | "flag" | "band_edges" | "family_margins";

export interface PolicyField {
  field: string;
  label: string;
  help: string;
  value: number | boolean | number[] | Record<string, number>;
  default: number | boolean | number[] | Record<string, number>;
  overridden: boolean;
  kind: PolicyKind;
}

export interface MarginPolicy {
  version: string;
  default_version: string;
  /** ISO code the money-kind fields are denominated in. The screen is told
   *  rather than assuming — the same 10,000 means different things in INR and
   *  USD, which is why it is inside the version hash server-side too. */
  currency: string;
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
  min_quote_exception_impact?: number;
  min_material_gap?: number;
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
  /** The last pull aimed at this company specifically. Null if never. */
  last_sync: {
    status: string;
    started_at: string | null;
    since: string | null;
    sales_txns: number;
    cost_records: number;
    error: string | null;
  } | null;
  /** The date to offer next: what this company was last read from, or a
   *  first-pull default. Per connection because the answer genuinely differs —
   *  one entity may have four years of books worth reading and another four
   *  months. */
  suggested_since: string;
  /** How far back this company has actually been listed — not what the last
   *  run asked for. Null until a run has finished. Picking a date before this
   *  lists those months in full; a date at or after it is a cheap incremental. */
  covered_from: string | null;
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


/* ── identity layer ─────────────────────────────────────────────────────────
 * Records from different connectors are linked, never merged. Each connector
 * stays the source of truth for its own data; an identity only says which
 * records describe the same business entity.
 */
export type EntityKind = "customers" | "items";

export interface ConnectorRecord extends Sourced {
  record_id: string;
  connector: string;
  connection_id: string | null;
  external_id: string;
  last_synced_at: string | null;
  /** Exactly what the connector supplied. Visible because the whole point of
   *  not merging is being able to see what each system actually said. */
  source_ref: Record<string, unknown>;
  name?: string;
  gstin?: string | null;
  customer_id?: string | null;
  sku?: string | null;
  description?: string;
  product_id?: string | null;
}

export interface Identity {
  identity_id: string;
  label: string | null;
  /** Derived from the linked records, not stored — so no one connector becomes
   *  the authority on what the entity is called. */
  display_name: string;
  active: boolean;
  connector_count: number;
  record_count: number;
  created_at: string | null;
  records: ConnectorRecord[];
  history?: IdentityEvent[];
}

export interface IdentityEvent {
  action: string;
  actor: string;
  detail: string;
  record_id: string | null;
  at: string | null;
}

export interface IdentitySuggestion {
  /** Which rule proposed it — GSTIN, SKU, NAME. `NAME` is the weak one: it only
   *  runs where no exact identifier exists, and it is never auto-linked. */
  strategy: string;
  suggestion_id: string;
  /** The value it matched on, so a reviewer judges the match not a score. */
  evidence: string;
  created_at: string | null;
  incoming: ConnectorRecord;
  incoming_identity_id: string;
  target: Identity;
}

/** How far the matcher can even see, so an empty review queue can say which
 *  kind of empty it is. Nothing found and nothing *lookable-at* were one
 *  sentence, and the screen chose the reassuring reading of both. */
export interface IdentityCoverage {
  records: number;
  /** Records carrying an identifier a strong strategy can compare. */
  with_key: number;
  without_key: number;
  /** Records still alone on their identity — nothing has been linked to them. */
  unlinked: number;
  /** "GSTIN" or "SKU", so the screen names the right one. */
  key_name: string;
}

export interface IdentityPolicy {
  auto_link_customers: boolean;
  auto_link_items: boolean;
  can_manage: boolean;
  note: string;
}

/** Whether the AI is actually on, and what the next decision run would cost.
 *
 *  Both halves come from `/internal/ai-readiness`, which builds the prompts a
 *  run would send and then stops — nothing here costs a provider call. */
export interface AiProviderStatus {
  /** What `AI_PROVIDER` says. */
  configured: string;
  /** What will really run. Differs from `configured` when the live provider
   *  could not be built — a missing key is the usual reason. */
  effective: string;
  model: string;
  api_key_present: boolean;
  live: boolean;
  /** Why the two differ, in words, or null when they do not. */
  detail: string | null;
}

export interface AiReadiness {
  provider: AiProviderStatus;
  signals_considered: number;
  would_call_provider: number;
  would_reuse_cached: number;
  would_suppress_up_front: number;
  by_type: Record<string, number>;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  rates: {
    currency: string;
    per_mtok_input: number;
    per_mtok_output: number;
    max_output_tokens_per_call: number;
  };
  estimated_cost_usd: number;
  estimated_cost_per_decision_usd: number;
  note: string;
}

export interface AiWindowSummary {
  calls: number;
  provider_calls: number;
  by_status: Record<string, number>;
  rates: Record<string, number>;
  failure_reasons: Record<string, number>;
  cost: { currency: string; total_estimated: number; per_decision: number; per_day: number };
  latency_ms: { median: number | null; max: number | null };
  health: { degraded_rate: number; band: string; note: string };
}

export interface AiMetricsReport {
  generated_at: string;
  windows: Record<string, AiWindowSummary>;
}

/* ── the trust surface (owner only) ──────────────────────────────────────────
 * What leaves for a model, who has opened this tenant, and the two irreversible
 * things an owner can do with their own data. Shapes mirror `routers/trust.py`;
 * every one of them is the server's own words, because the point of the screen
 * is that the promise is checkable rather than restated by the client.
 */

/** One category of fact the model is allowed to receive, and why. */
export interface DisclosureAllowed {
  category: string;
  example: string;
  why: string;
}

export interface DisclosureStatement {
  provider: string;
  /** The configured model. Note that the server reports this whatever the
   *  provider is, so with `provider: "mock"` it names a model nothing calls —
   *  the screen says which provider is running rather than asserting this. */
  model: string;
  training_on_customer_data: boolean;
  zero_retention_requested: boolean;
  allowed: DisclosureAllowed[];
  never_sent: string[];
  notes: string;
}

export interface ModelPayloadRow {
  payload_id: string;
  decision_type: string | null;
  provider: string;
  model: string;
  created_at: string | null;
  /** Anything the outbound checker found that the disclosure says never leaves.
   *  Non-empty is a defect report, not a statistic. */
  findings: string[];
  payload: string | null;
}

export interface PayloadsReport {
  summary: { payloads: number; flagged: number };
  payloads: ModelPayloadRow[];
}

/** One entry in the break-glass log. `GRANTED` and `REVOKED` bracket a window;
 *  `ACCESSED` is one use inside it, and there is one row per use. */
export interface AccessEventRow {
  event_id: string;
  staff_user_id: string;
  action: string;
  detail: string | null;
  at: string | null;
}

export interface AccessReport {
  events: AccessEventRow[];
  note: string;
}

export interface ErasureReceipt {
  organization_id: string;
  erased_at: string | null;
  reason: string;
  actor_user_id: string | null;
  manifest: Record<string, unknown>;
  method: string;
  signature: string;
  /** Re-checked server-side on every read, so a receipt cannot be believed on
   *  the strength of its own presence. */
  verified: boolean;
}

export interface ErasureState {
  erased: boolean;
  receipt: ErasureReceipt | null;
  key_destroyed?: boolean;
}
