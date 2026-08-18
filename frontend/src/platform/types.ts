export type Role = "SALESPERSON" | "SALES_MANAGER" | "OWNER";

export interface PlatformSession {
  token: string;
  role: Role;
  name: string;
  /** The address this account signs in with. Present so a change-password form
   *  can carry a `username` field — without one a password manager saves the new
   *  secret against nothing. Optional: a session stored before it was sent is
   *  still valid, and the field is simply omitted. */
  email?: string;
  user_id: string;
  organization_id: string;
  /** ISO code this organization trades in, from the sign-in response. Drives
   *  every money figure the client renders. */
  currency: string;
  /** IANA zone this organization's *day* is measured in. Drives every
   *  timestamp the client renders — see `src/when.ts` for why the browser's
   *  own zone is the wrong answer here. */
  timezone: string;
  /** True while this session is in the public demonstration workspace: made-up
   *  data, and the server refuses every write. Rendered on every screen rather
   *  than only where the numbers are — a visitor who does not know the figures
   *  are invented is being misled by a product whose whole argument is that its
   *  figures are real. */
  is_demo?: boolean;
  /** True while this account holds a password somebody else issued. The server
   *  refuses every request but the change itself, so the shell shows the change
   *  screen instead of the app. */
  must_change_password?: boolean;
}

/** What `GET /api/v1/signup` answers: whether this deployment has a front door.
 *
 *  A single-tenant install leaves `enabled` false — the default — and the
 *  landing page offers sign-in only. */
export interface SignupOffer {
  enabled: boolean;
  /** The plan a sign-up lands on. Free, and pinned server-side. */
  plan: string;
  trial_days: number;
  note: string;
}

/** Whether this deployment has a demonstration workspace a stranger can open.
 *
 *  Says yes or no and nothing else — not which organization it is, not who is
 *  in it. A single-tenant install leaves it false, which is the default, and
 *  the landing page then shows no such door rather than one that 404s. */
export interface DemoOffer {
  enabled: boolean;
  note: string;
}

/** What this organization's plan lets it use, and what it is about to lose.
 *
 *  `GET /api/v1/entitlements` has existed since plans landed and nothing called
 *  it, so the one thing a tenant most needed to be told — that its free month
 *  of Commercial Intelligence is running out — was computed correctly on the
 *  server and never reached a screen. */
export interface Entitlements {
  /** The plan licensed underneath any trial. */
  plan: string;
  plan_label: string;
  /** What is actually in force right now — a running trial lifts this. */
  effective_plan: string;
  effective_label: string;
  trial: {
    ends_at: string;
    /** The end date in the business's own zone, as a person would write it. */
    ends_on: string | null;
    /** Counted server-side, in the business's day. A browser subtracting dates
     *  would use the reader's zone and be off by one for anyone travelling. */
    days_remaining: number;
  } | null;
  features: Record<string, boolean>;
  /** Feature keys in force only because of the trial — what expiry costs.
   *  Derived from the server's plan map so the client holds no second copy. */
  loses_on_expiry: string[];
  /** What this organization has asked for and not yet been given. `null` when
   *  nothing is outstanding — the upgrade control keys off this so an owner who
   *  already pressed it is shown what they asked for rather than the button
   *  again. */
  pending_request: {
    request_id: string;
    requested_plan: string;
    requested_plan_label: string;
    requested_at: string;
    status: string;
  } | null;
}

/** One thing a new organization has or has not done.
 *
 *  `detail` is always populated, and that is the field worth reading: "not
 *  done" without a reason is what sends somebody to the wrong screen. `route`
 *  is a path from `route.ts`, so a step links to the screen that completes it. */
export interface OnboardingStep {
  key: "connect" | "history" | "policy" | "team";
  title: string;
  detail: string;
  done: boolean;
  /** False for the two steps a working platform does not need — a team, and a
   *  margin policy of one's own. They are worth doing and must not make the
   *  setup panel permanent. */
  required: boolean;
  route: string;
}

export interface OnboardingView {
  steps: OnboardingStep[];
  /** Every *required* step is done. What the panel keys off. */
  complete: boolean;
  remaining: number;
  remaining_required: number;
}

/** One entry in a decision's human trail.
 *
 *  `actor_name` is recorded at the moment of the action rather than resolved on
 *  read, so a past entry keeps saying who it actually was. Optional because rows
 *  written before the trail existed carry only the id. */
export interface HumanActionEntry {
  action: string;
  actor_user_id: string;
  actor_name?: string | null;
  acted_at: string;
  note?: string | null;
}

/** The latest action, with the full trail beside it.
 *
 *  The top level mirrors the most recent entry — a queue row wants "what
 *  happened last" and reads it without walking a list. `trail` is append-only
 *  and oldest-first: a reversal is recorded next to what it reversed rather than
 *  replacing it. Absent on rows last touched before the trail existed. */
export interface HumanAction extends HumanActionEntry {
  trail?: HumanActionEntry[];
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
  human_action: HumanAction | null;
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
  /** The assignee's name. Null *with* `assigned_role` set is not missing data:
   *  the decision belongs to a role rather than to a person. */
  assigned_to?: string | null;
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
  /** The assignee's name, resolved server-side. Null means unassigned — the
   *  client does not resolve this itself because it would need the user
   *  directory, which a salesperson cannot read. */
  assigned_to?: string | null;
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

/** One row a pull could not fully resolve, as it happened.
 *
 *  The evidence behind `UnresolvedReference`, which is the worklist: this is
 *  the individual bill or invoice line, named well enough to be found in Zoho
 *  and reconciled against it. */
export interface SkippedRow {
  skip_id: string;
  /** Position within the run — the order the pull met these rows. */
  seq: number;
  connection_id: string | null;
  /** The connected company's label, resolved server-side. Blank when the run
   *  recorded no connection (a pull from before per-company provenance). */
  company: string;
  kind: string;
  code: string;
  detail: string;
  ref: string;
  missing_id: string | null;
  /** The item as written on the *document*. The master has no such record, so
   *  the line is the only place its name survives — and the only thing anyone
   *  can search Zoho by. */
  label: string | null;
  sku: string | null;
  document: string | null;
  document_date: string | null;
  party: string | null;
  qty: number | null;
  line_value: number | null;
  fix: string | null;
}

/** Every skipped row of one run, with the run's own count beside what was kept.
 *
 *  Two numbers rather than one because they can legitimately differ — a run
 *  from before these rows were persisted counted skips it did not keep — and a
 *  reader comparing an export against the screen deserves to see why. */
export interface SkippedRows {
  sync_run_id: string;
  started_at: string | null;
  skipped_count: number;
  held: number;
  /** Set when `held` and `skipped_count` disagree: what the gap is, in words.
   *  Null means the list is complete. */
  incomplete: string | null;
  columns: { field: string; header: string }[];
  rows: SkippedRow[];
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

/** The automatic pull's schedule, as the server reports it.
 *
 *  `available` is false on a sample-data deployment — there is nothing to keep
 *  fresh, so the screen explains rather than offering a dead control. `hours`
 *  0 means off by choice. */
export interface AutoSync {
  hours: number;
  available: boolean;
  next_run_at: string | null;
  /** The window each scheduled run re-reads — the organization's existing
   *  coverage, so backdated entries are never missed. Null before the first
   *  completed sync. */
  covers_from: string | null;
}

export interface DataStatus {
  connection: ConnectionState;
  auto_sync: AutoSync;
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
  /** Why not, when `can_decide` is false — rendered in place of the button
   *  rather than inferred from `required_authority`. */
  cannot_decide_reason?: string | null;
  /** Whether approving must carry a note. True for a below-cost line; the server
   *  enforces it as well, so this only saves a round trip. */
  requires_rationale?: boolean;
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
  | "ratio" | "money" | "days" | "flag" | "band_edges" | "family_margins"
  /** Owner-confirmed retained profit: (entity, financial year, amount) rows.
   *  Its own editor, like `family_margins` — a row is three values and one of
   *  them names a company, which no scalar control can express. */
  | "retained_pat";

/** A retained-profit row as it crosses the wire: entity id, financial year,
 *  amount. The amount is a **string**, and deliberately: it is money, so a JSON
 *  number would already have been through a float before anything here read it.
 *  Mirrors `admin._PATCH_TYPE["retained_pat"]`. */
export type RetainedPatRow = [entity: string, financialYear: string, amount: string];

export interface PolicyField {
  field: string;
  label: string;
  help: string;
  value: number | boolean | number[] | Record<string, number> | RetainedPatRow[];
  default: number | boolean | number[] | Record<string, number> | RetainedPatRow[];
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
  retained_pat?: RetainedPatRow[];
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
  /** Which system this company is read from ("zoho", "netsuite",
   *  "dynamics365", "acumatica", "prophet21", "sagex3", "sage100"). */
  connector: string;
  connector_label: string;
  label: string;
  /** The company's id in its own system — a Zoho org id, a Business Central
   *  company GUID, an Acumatica tenant. The field name is historical. */
  zoho_organization_id: string;
  enabled: boolean;
  credential_id: string | null;
  client_id: string | null;
  credential_label: string;
  credential_rotated_at: string | null;
  accounts_base: string;
  api_base: string;
  /** A registered connector's non-secret settings (company GUID, branch,
   *  endpoint …). Null for Zoho rows. Secrets are never in any response. */
  config: Record<string, string> | null;
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

/** One scope, as the grant itself answered for it. `granted: null` means the
 *  probe could not get an answer — not a quiet pass. */
export interface ProbedScope {
  scope: string;
  endpoint: string;
  granted: boolean | null;
  detail: string | null;
}

/** A check result: the row as stored, plus what the grant could actually see.
 *
 *  `scopes` is the half a ping cannot answer. Zoho's `organizations` endpoint
 *  sits behind no scope, so a connection granted the login and nothing else
 *  passes a ping and then fails every sync — which is why the check probes each
 *  scope and reports them separately from reachability. */
export interface ConnectionCheck extends ZohoConnection {
  checked: boolean;
  ok?: boolean;
  detail?: string;
  organization_name?: string;
  currency?: string;
  visible_organizations?: { organization_id: string; name: string }[];
  scopes?: ProbedScope[];
  /** Refused scopes without which no sync can run. Non-empty means `ok` false. */
  missing_required_scopes?: string[];
  /** Scopes the probe could not reach a verdict on. Neither granted nor refused. */
  untested_scopes?: string[];
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
  connector: string;
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

/* ── registered ERP connectors (NetSuite, Business Central, Acumatica, P21,
 *    Sage) ──────────────────────────────────────────────────────────────────
 * The connect form for these renders from the catalog's field specs, so the
 * UI never hardcodes what one system needs — a new connector appears here the
 * day its backend module registers. Zoho keeps its richer bespoke flow.
 */
export interface ConnectorField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
  help: string;
}

export interface ConnectorCatalogEntry {
  key: string;
  label: string;
  /** What that system calls one set of books — "company", "tenant", "folder". */
  company_term: string;
  icon: string;
  setup_note: string;
  credential_fields: ConnectorField[];
  connection_fields: ConnectorField[];
  external_id_field: string;
  /** Whether entered credentials can list visible companies to pick from. */
  can_discover: boolean;
}

export interface ConnectorCatalog {
  connectors: ConnectorCatalogEntry[];
}

export interface ErpConnectInput {
  connector: string;
  values: Record<string, string>;
  label?: string;
  credential_label?: string;
}

export interface ErpDiscoveredCompany {
  id: string;
  name: string;
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
  /** Which level of configuration won: the organization's own key ("organization")
   *  or the deployment environment ("environment"). */
  source?: string;
  /** Why the two differ, in words, or null when they do not. */
  detail: string | null;
}

/* One provider an organization can bring its own key for. The key itself is
 * write-only server-side and never appears here — `key_hint` is the stored
 * last four characters, the most a read path ever sees. */
export interface AiByokProvider {
  provider: string;
  key_on_file: boolean;
  key_hint: string;
  /** The organization's model override; "" means `default_model` runs. */
  model: string;
  default_model: string;
  /** Whether the deployment itself also holds a key for this provider. */
  env_key_present: boolean;
  rotated_at: string | null;
}

export interface AiByokView {
  /** Which provider this organization chose, or "" for the deployment default. */
  active: string;
  /** What the deployment would run with no organization choice. */
  environment_provider: string;
  providers: AiByokProvider[];
}

export interface AiKeyTestResult {
  ok: boolean;
  provider: string;
  model?: string;
  detail: string;
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

/** One field class the key destruction reached: a column of ciphertext
 *  written under the tenant's DEK, now permanently unreadable. */
export interface ErasureDestroyedEntry {
  table: string;
  column: string;
  holds: string;
}

/** One column key destruction could not touch — held in plaintext, with the
 *  reason it is in the clear. The honest half of the receipt. */
export interface ErasureSurvivorEntry {
  table: string;
  column: string;
  why: string;
}

export interface ErasureReceipt {
  organization_id: string;
  erased_at: string | null;
  reason: string;
  actor_user_id: string | null;
  manifest: Record<string, unknown>;
  method: string;
  /** Stamped at erase time and covered by the signature, so the receipt keeps
   *  its story even after the code's own lists move on. */
  destroyed: ErasureDestroyedEntry[];
  survives_plaintext: ErasureSurvivorEntry[];
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

// ── what PIE changed: the value-attribution ledger ──────────────────────────
//
// Money on this surface arrives as a **string** ("12500.0000"), because it is
// `Decimal` on the server and FastAPI serializes it without a float round-trip.
// `money()` takes either, but the parsing matters for one thing this screen
// must never get wrong: `null` and `"0"` are different facts. A null amount is
// "nothing measurable was recorded"; a zero is a measurement. Typed as a named
// alias so a reader of these interfaces cannot mistake it for a number.
export type MoneyString = string;

/** One named hole in the evidence. Rendered, never swallowed — a figure with a
 *  gap behind it has to say so on the screen, not in a tooltip. */
export interface EvidenceGap {
  /** What could not be measured: a metric name, or a `ValueEventType`. */
  subject: string;
  /** A stable code — `NO_EVENTS_RECORDED`, `NOT_MEASURABLE`, … */
  reason: string;
  detail: string;
}

/** The trial's own facts. Separate from the window that was measured, because
 *  the two are only the same period while the trial is running — and while they
 *  were one object, a window frozen at a finished trial was indistinguishable
 *  from a live one. */
export interface AttributionTrial {
  trial_id: string;
  started_at: string | null;
  ends_at: string | null;
  /** Only while this is true do the countdown chips mean anything. */
  is_running: boolean;
  days_remaining: number;
}

/** The period a summary measured, and what put it there. `TRIAL` while a trial
 *  frames it (or while a lapsed plan holds the reader there), `RECENT` for the
 *  trailing window a paying customer's headline advances over. */
export interface AttributionWindow {
  basis: "TRIAL" | "RECENT";
  label: string;
  start: string | null;
  end: string | null;
  days: number;
  /** Set when the window was cut short by what the plan entitles this
   *  organization to read, rather than by the clock. */
  frozen_at: string | null;
}

/** One (event type × value class) cell of the window. Never added across
 *  classes — see `class_totals_are_not_summable`. */
export interface ValueClassBreakdown {
  event_type: string;
  value_class: string;
  events: number;
  amount: MoneyString | null;
}

/** Work the platform did, counted and never valued: this business holds no
 *  hourly rate, so none of these becomes a rupee. */
export interface AttributionProductivity {
  note: string;
  quotes_priced: number;
  lines_priced: number;
  approvals_turned_round: number;
}

/** `GET /api/attribution/summary`.
 *
 *  Everything below `evidence_gaps` is optional because an organization with no
 *  trial on record gets a three-field payload: the trial, a null headline and
 *  the gap that says why. */
export interface AttributionSummary {
  /** What was measured. `null` only on the evaluation report, which needs a
   *  trial and says so rather than drawing a comparison without a "before". */
  window: AttributionWindow | null;
  trial: AttributionTrial | null;
  /** The far end of the window, to the second. */
  measured_to?: string | null;
  /** ATTRIBUTED only. `null` means no detection run is on record — which is
   *  **not** a measured zero, and the screen must not render it as one. */
  attributed_value: MoneyString | null;
  attributed_events?: number;
  potential_value?: MoneyString | null;
  potential_events?: number;
  realized_value?: MoneyString | null;
  realized_events?: number;
  // No estimated_*: the server stopped sending it because no detector produces
  // ESTIMATED, and an optional field here would invite a tile that renders
  // "none recorded" as though a measurement had been attempted.
  class_totals_are_not_summable?: string;
  by_event_type?: ValueClassBreakdown[];
  productivity?: AttributionProductivity;
  currency?: string;
  evidence_gaps: EvidenceGap[];
  empty_reason: string | null;
}

/** One ledger row. `basis` holds the operands the amount was computed from and
 *  `evidence_refs` names the rows it was computed over, so the drill-down
 *  re-derives the figure instead of restating it. */
export interface ValueEventRow {
  value_event_id: string;
  event_type: string;
  value_class: string;
  /** `null` where the class carries no defensible money. Never coerced to 0. */
  amount: MoneyString | null;
  currency: string;
  basis: Record<string, unknown>;
  evidence_refs: Record<string, unknown>[];
  occurred_at: string | null;
  thresholds_version: string | null;
  created_at: string | null;
}

/** One reason a detector could not judge a subject, with how many it hit and
 *  the sentence that names the missing evidence. */
export interface RetrospectiveWithheld {
  reason: string;
  count: number;
  detail: string;
}

/** One detector's reach over the book, never its findings alone. */
export interface RetrospectiveDetector {
  detector: string;
  considered: number;
  found: number;
  clear: number;
  judged: number;
  /** `null` where nothing was considered — no share exists, and a 0% there
   *  would read as "judged none of many". */
  judged_share: number | null;
  withheld: RetrospectiveWithheld[];
}

/** How far back the record goes, read from the rows rather than from the
 *  configured history window. */
export interface RetrospectiveHistory {
  first_document: string | null;
  last_document: string | null;
  months: number | null;
  sales_lines: number;
  cost_lines: number;
  detail: string | null;
}

/** `GET /api/v1/retrospective`. What a book already held, and how much of it
 *  could be judged. Coverage before findings — the verdict turns on what was
 *  examined, never on what turned up. */
export interface Retrospective {
  as_of: string | null;
  history: RetrospectiveHistory;
  verdict: "UNEXAMINED" | "PARTIAL" | "EXAMINED";
  verdict_detail: string;
  considered: number;
  judged: number;
  judged_share: number | null;
  found: number;
  detectors: RetrospectiveDetector[];
  thresholds_version: string;
}

/** `GET /api/v1/admin/margin-policy/backtest`. What a different approval floor
 *  would have done to quotes already priced. Owner only — `shortfall` plus the
 *  margin the caller supplied yields cost in closed form. */
export interface FloorBacktest {
  policy: {
    baseline_version: string;
    variant_version: string;
    baseline_min_margin: number;
    variant_min_margin: number;
  };
  lines_examined: number;
  newly_requires_approval: number;
  no_longer_requires_approval: number;
  revenue_newly_gated: MoneyString | null;
  shortfall_newly_gated: MoneyString | null;
  /** Lines with no usable cost. Never counted as passing — a line whose
   *  economics are unknown has an UNKNOWN verdict, not a clean one. */
  unjudgeable_no_cost_on_record: number;
  /** Rows priced under a policy this replay cannot rebuild, so their baseline
   *  verdict does not match what was recorded. */
  baseline_disagreements: number;
  by_customer: { name: string; lines: number; revenue: MoneyString | null }[];
}

/** `GET /api/attribution/events`. A page of the ledger — never a rollup. */
export interface AttributionEvents {
  events: ValueEventRow[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  currency: string;
  /** The server's own warning that these rows must not be added up. */
  page_is_not_a_total: string;
  filters: { event_type: string | null; value_class: string | null };
  /** The server's vocabulary, so a filter control and a breakdown are built
   *  from it rather than from a copy in the client that then drifts. */
  event_types: string[];
  value_classes: string[];
  empty_reason: string | null;
  /** How far into the ledger this organization's plan lets it read, or `null`
   *  for unbounded. An organization whose intelligence plan has lapsed keeps
   *  the window it was entitled to — it reads up to the end of its trial and no
   *  further. */
  readable_until: string | null;
  /** Why the view stops where it does, when it stops. `null` when unbounded.
   *  Rendered rather than inferred: a truncated ledger that does not say it is
   *  truncated reads as the whole one, which is the same benign-default failure
   *  the rest of this surface is built to refuse. */
  frozen_reason: string | null;
}

/** What the book looked like before the trial, and what it looks like during. */
export interface AttributionWindowMetrics {
  priced_lines: number;
  costed_lines: number;
  uncostable_lines: number;
  approval_required_lines: number;
  approval_required_rate: number | null;
  quoted_revenue: MoneyString | null;
  gross_profit: MoneyString | null;
  /** A ratio (0.24), never a percentage. `null` where it could not be stated. */
  margin: number | null;
  quotes_won: number;
  quotes_lost: number;
  quotes_decided: number;
  quote_win_rate: number | null;
}

export interface AttributionBaseline {
  baseline_id: string;
  captured_at: string | null;
  window_start: string;
  window_end: string;
  metrics: Record<string, unknown>;
  evidence_gaps: EvidenceGap[];
  thresholds_version: string | null;
}

/** `GET /api/attribution/evaluation` — owner only. The summary, plus the
 *  before/during comparison and the return on what the platform costs. */
export interface AttributionEvaluation extends AttributionSummary {
  baseline: AttributionBaseline | null;
  comparison: {
    quoted_margin_before: number;
    quoted_margin_after: number;
    /** Percentage POINTS, per the house convention for a margin move. */
    quoted_margin_movement_pp: number;
  } | null;
  /** Attributed value per rupee of platform cost. `null` with
   *  `roi_is_unknown` true unless the caller supplied a cost — render that as
   *  UNKNOWN, never as 0x. */
  roi: MoneyString | null;
  roi_is_unknown: boolean;
  during?: AttributionWindowMetrics;
}
