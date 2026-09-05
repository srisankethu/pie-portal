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
  /** The workspace this session is acting for, by name. Rendered wherever the
   *  session is: one login can reach two customers, and a screen that does not
   *  say whose numbers it is showing is a screen somebody will misread. */
  organization_name?: string;
  /** Every workspace this identity may open, with the role held in each.
   *
   *  Usually one, and the shell renders no switcher for one. Optional because
   *  a session stored before the field existed is still valid; treat a missing
   *  value as "just this one" rather than as "none", since the alternative
   *  hides the workspace the person is standing in.
   *
   *  **Not an authorization input.** Switching posts to the server, which
   *  looks the membership up inside the target tenant and refuses without one.
   *  This list only decides what the menu offers. */
  organizations?: OrganizationMembershipView[];
}

/** One workspace this identity can open, and the role it holds there.
 *
 *  `role` is per organization on purpose — the same person can own the company
 *  they run and read a group company as a salesperson, and the switcher has to
 *  be able to say so. */
export interface OrganizationMembershipView {
  organization_id: string;
  name: string;
  role: Role;
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
  /** The plans that exist, cheapest first, with what each adds. Sent by the
   *  server so the form does not hold a second copy of the plan map — the copy
   *  would go stale the first time a feature moved between tiers. No prices:
   *  those are marketing copy and live only on the landing page. */
  plans: PlanOption[];
}

/** One rung of the plan ladder, as `GET /api/v1/signup` describes it. */
export interface PlanOption {
  plan: string;
  label: string;
  summary: string;
  /** Whether this is somewhere an organization can move *to*. False for the
   *  unsubscribed floor, which is where one lands rather than something one
   *  chooses — the ladder still lists it so a screen can name what an
   *  organization currently has. */
  purchasable: boolean;
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
  /** The organization's commercial state, named by the server rather than
   *  inferred here from three other fields. `TRIALING` and `EXPIRED` are the
   *  two halves of one trial; `ACTIVE` is a paid subscription and `CANCELLED`
   *  one that ended. The client could not previously tell "trial ended" from
   *  "never had one", which is why the locked screen had nothing to say. */
  status: "TRIALING" | "ACTIVE" | "EXPIRED" | "CANCELLED";
  /** The trial, running or finished. Present whenever there has ever been one,
   *  so the notice can say "ended on the 3rd" rather than going quiet on the
   *  day it matters most. `null` only when this organization never had one. */
  trial: {
    /** Whether it is still running. False for one that has ended. */
    active: boolean;
    started_at: string;
    ends_at: string;
    /** The end date in the business's own zone, as a person would write it. */
    ends_on: string | null;
    /** Counted server-side, in the business's day. A browser subtracting dates
     *  would use the reader's zone and be off by one for anyone travelling.
     *  Zero once the trial has ended — never negative. */
    days_remaining: number;
    /** Why it ended before its date, when something did — today only the
     *  books-already-trialled case. Empty for a trial that ran its course.
     *  Rendered verbatim: it is written to be read. */
    ended_reason: string;
  } | null;
  features: Record<string, boolean>;
  /** Feature keys in force only because of the trial — what expiry costs.
   *  Derived from the server's plan map so the client holds no second copy. */
  loses_on_expiry: string[];
  /** Feature keys this organization does *not* currently have. The same list
   *  after the fact, so a locked state can name what subscribing brings back
   *  instead of describing the plans in the abstract. */
  locked: string[];
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
  /** Set by the server when this card's detail could not be built, so the
   *  screen can say so instead of rendering empty panels.
   *
   *  The queue folds detail into the list, and a row whose detail failed used
   *  to arrive as a summary in a detail's shape: every panel blank, with no way
   *  to tell "this decision has no interpretation" from "loading it failed". */
  detail_unavailable?: boolean;
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
/** One line of a sync's own log, as the server kept it. */
export interface SyncLogLine {
  /** Position within the run. Two lines inside one millisecond are not rare in
   *  a tight loop, so the order cannot rest on the timestamp. */
  seq: number;
  at: string | null;
  level: string;
  /** Which logger wrote it — half of reading an interleaved log. */
  logger: string;
  message: string;
}

/** One page of a run's log, and enough state to keep following it. */
export interface SyncRunLogPage {
  sync_run_id: string;
  status: string;
  phase: string | null;
  /** Still going, so an empty tail means "nothing new" rather than "the end". */
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  total: number;
  lines: SyncLogLine[];
  /** Where to resume from on the next poll. */
  next_seq: number;
  /** Why the log is empty, when it is. Absence needs a reason, not a blank box. */
  note: string | null;
}

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

// ── the decoded product catalogue ───────────────────────────────────────────

/** The parser's own run report, stored beside the catalogue at build time and
 *  served verbatim — the portal never recomputes a census or a parse rate. */
export interface CatalogReport {
  total: number;
  by_family: Record<string, number>;
  by_grammar: Record<string, number>;
  by_flag: Record<string, number>;
  /** Per family, as a ratio; null where the family had no rows. */
  parse_rates: Record<string, number | null>;
  unresolved_family: number;
  new_tokens_top: Record<string, number>;
}

/** Provenance stamped on every decoded record: which nomenclature grammar,
 *  which version, which ruleset checksum — the facts that say WHICH build
 *  answered a resolution.
 *
 *  These are pie-parser's own field names (`StampInfo`), served verbatim, so
 *  `pack_id` here is the engine's word for the grammar that decoded a row and
 *  not the portal's — a file's decoder is its `rule_set`. */
export interface CatalogStamp {
  pack_id?: string;
  pack_version?: string;
  org_id?: string;
  org_version?: string;
  ruleset_checksum?: string;
  run_id?: string;
  engine_version?: string;
  schema_version?: string;
}

/** Whether the corpus that ships in the pinned engine is present. It is the
 *  SEED a first company inherits, not something resolution reads: once a
 *  company has uploaded its own export, nothing here is consulted. */
export interface CatalogSource {
  available: boolean;
  /** Exactly why a seed is unavailable, when it is — submodule not
   *  initialised vs corpus file missing are different fixes. */
  reason: string | null;
  pie_parser_root: string;
  corpus: string;
  /** The rule set that seed file is decoded through — the one written against
   *  it. Never a default for anything a company uploads. */
  seed_rule_set: string;
}

/** Which of one uploaded file's columns hold the three things a rule set
 *  reads.
 *
 *  A fact about that FILE, not about the rule set: two exports of the same
 *  manufacturer's range call the part number `MM#` and `Part No`, and this is
 *  what lets both decode without either being edited. */
export interface SourceColumnMapping {
  record_id: string | null;
  description: string | null;
  grade: string | null;
}

/** What reading one file kept, and what it left out.
 *
 *  The dropped columns are NAMED rather than counted on purpose. The catalogue
 *  is nomenclature only — a price column is absent from it because it was never
 *  written, not because something filtered it — and "12 columns ignored" is not
 *  a claim anybody can check against their own spreadsheet. */
export interface SourceIngest {
  columns: string[];
  mapped: SourceColumnMapping;
  dropped_columns: string[];
  /** The dropped columns that looked commercial: price, cost, stock, discount.
   *  Reporting only — every unmapped column is dropped either way. */
  commercial_columns_dropped: string[];
  rows_read?: number;
  /** Rows with a part number and a description — what this file can contribute. */
  rows_kept?: number;
  /** Rows with no part number or no description. A large number here usually
   *  means the wrong column is mapped, which is why it is shown. */
  rows_skipped_blank_key?: number;
  sampled?: boolean;
  source_key?: string;
  filename?: string;
  sha256?: string;
}

/** One of the files a company's catalogue is built from. */
export interface CompanySource {
  /** What this file IS, for replacing or removing it. Uploading under the same
   *  key replaces that one file and leaves the others alone. */
  source_key: string;
  corpus_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_at: string;
  uploaded_by: string | null;
  /** How THIS file is read and decoded. Every file carries its own — there
   *  is no default decoder to fall back to — and it decodes nothing until a
   *  person has saved it. */
  decoding: SourceDecoding;
  ingest: SourceIngest | null;
}

/** Every shipped rule set run over the first rows of ONE file, with the
 *  parser's own counts for each.
 *
 *  Every upload starts as an unknown format, so this is the evidence a person
 *  confirms a decoding config against. Counts, never a score: ranking rule
 *  sets by a number the portal invented would be the second parse-rate
 *  calculation `catalog.run_parse` refuses to have, and one figure would hide
 *  the distinction that actually decides the choice — a rule set that
 *  classifies few rows is wrong for this file, one that errors could not read
 *  it at all, and none of them reading it means a rule set has to be written
 *  before this manufacturer can be decoded. */
export interface SourceAnalysis {
  sample_rows: number;
  candidates: {
    rule_set: string;
    rows_read?: number;
    classified?: number;
    quarantined?: number;
    report?: CatalogReport;
    /** This rule set could not read this file. A result, not a failure of the
     *  analysis — it is one of the answers the analysis exists to give. */
    error?: string;
  }[];
  /** The rule set this file's own evidence chose, or null where several read
   *  it or none did. A proposal, never a config: it decodes nothing until
   *  somebody saves it. */
  proposed: string | null;
  /** Why there is no proposal, or why one is not the whole answer. Never an
   *  empty candidate list that reads as "nothing fits". */
  reason: string | null;
}

/** How one uploaded price list is read and decoded — its own, not the
 *  catalogue's.
 *
 *  Two exports of the same range call the part number `MM#` and `Part No`,
 *  and two manufacturers phrase a description differently, so both halves
 *  belong to the FILE: which of its columns are read, and which shipped rule
 *  set decodes its descriptions. `ready` is the one question a build asks —
 *  saved, with a rule set this engine still ships. A proposal that was never
 *  saved is not ready, however good it looked. */
export interface SourceDecoding {
  columns: SourceColumnMapping | null;
  rule_set: string | null;
  /** Whether the pinned engine still ships the rule set named. A stored id
   *  the pin no longer has resolves to nothing rather than to a guess. */
  rule_set_resolved: boolean;
  /** The content id of the decoder built for this file, when that is what
   *  decodes it. Sixteen hex characters, and what a screen names rather than
   *  re-hashing the artifact. */
  decoder_id: string | null;
  /** That decoder as stored — its decimal convention and its segments, each
   *  with a pattern, its bindings and the real rows it was validated against.
   *  Carries its own `decoder_id`, so an edited copy is refused rather than
   *  saved under an id that no longer describes it. */
  decoder: DecoderArtifact | null;
  /** Which of the two decode paths this config names, or null for a file
   *  nothing reads yet. Not a preference and a fallback: `rule_set` is a
   *  grammar pie-parser ships, `decoder` was built from this file, and a
   *  config naming both is refused. */
  path: 'rule_set' | 'decoder' | null;
  /** The evidence the proposal rested on, kept so a person's choice can be
   *  read against what they saw. Null for a file stored before it. */
  analysis: SourceAnalysis | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
  ready: boolean;
}

/** What each capture group of a decoder actually captured, over the whole
 *  file. The evidence a binding is reviewed against.
 *
 *  Measured from the matches, never from the pattern: a group whose pattern
 *  accepts any number and which in this file only ever holds `0` is the
 *  finding, and reading the pattern would hide it behind "accepts any
 *  number". */
export interface GroupEvidence {
  segment: string;
  group: string;
  /** `number` for a group built around a numeric position, `optional` for one
   *  built around a part that is sometimes absent. An optional part that is
   *  present or not is the shape of a flag; a number never is. */
  kind: string;
  rows_matched: number;
  /** Rows where this group participated. Less than `rows_matched` for an
   *  optional group, and the gap is what says how optional it really is. */
  occurrences: number;
  distinct: number;
  /** The most frequent distinct values, as they appear in the file. */
  samples: string[];
  /** The adjacent tokens, and **only when every occurrence agrees**. A group
   *  whose right-hand neighbour is `mm` in some rows and `xD` in others has no
   *  unit, and an empty string says exactly that. */
  left: string;
  right: string;
  all_integer: boolean;
  all_numeric: boolean;
}

/** One group, and what was proposed for it — including nothing.
 *
 *  `slot` is null for a group neither step could name, and that is a
 *  first-class outcome rather than a gap: `reason` says which way it declined
 *  and `candidates` says what may still go there. An empty `candidates` means
 *  nothing may be bound at all — a group no row uses, or one holding two
 *  different words that one binding cannot express. */
export interface BindingSuggestion {
  segment: string;
  group: string;
  slot: string | null;
  type: 'text' | 'integer' | 'number' | 'flag' | null;
  /** `surface` — the file's own text settled it. `model` — a model named it
   *  and the gate accepted it. `none` — nobody has. Shown, because "a machine
   *  judged this" is what a reviewer is being asked to check. */
  source: 'surface' | 'model' | 'none';
  candidates: string[];
  reason: string;
  detail: string;
  evidence?: GroupEvidence | null;
}

/** One segment's groups, in the order its pattern declares them. */
export interface SegmentEvidence {
  segment: string;
  pattern: string;
  rows_matched: number;
  groups: GroupEvidence[];
  examples: string[];
}

/** Everything needed to confirm a binding set, with nothing applied. */
export interface BindingReview {
  decoder_id: string;
  segments: SegmentEvidence[];
  suggestions: BindingSuggestion[];
  from_surface: number;
  from_model: number;
  unnamed: number;
  /** Entries of a model's reply the gate refused, as reason → count. A reply
   *  that is mostly refused is a finding about the prompt. */
  refused: Record<string, number>;
  provider: string;
  model: string;
  reason: string | null;
}

/** What inference found in a file: a decoder to review, and what it does to
 *  the file. `decoder` is null when nothing could be proposed — a file of
 *  one-off descriptions with no shape in it, which is a real answer about the
 *  file and reported as one. */
export interface DecoderProposal {
  decoder: DecoderArtifact | null;
  decoder_id: string | null;
  rows_read: number;
  claimed: number;
  unclaimed: number;
  /** Rows no proposed segment matched. The most useful part of the output on
   *  a file this does not understand. */
  unclaimed_samples: string[];
  coverage: Record<string, number>;
  overlaps: Record<string, unknown>[];
  reason: string | null;
}

/** What `POST .../propose-decoder` returns: the proposal, and the review of
 *  its groups. `review` is null when there was no decoder to review. */
export interface DecoderProposalResponse {
  proposal: DecoderProposal;
  review: BindingReview | null;
}

/** One confirmed answer from a review, as `PUT .../decoding` takes it.
 *
 *  Carries its `segment`, which `DecoderBinding` does not: inside the artifact
 *  a binding already sits under the segment it belongs to, but a review sends
 *  a flat list and each entry has to say where it goes. */
export interface BindingChoice {
  segment: string;
  group: string;
  slot: string;
  type: 'text' | 'integer' | 'number' | 'flag';
}

/** One capture group of one segment, and the slot it fills. */
export interface DecoderBinding {
  group: string;
  slot: string;
  type: 'text' | 'integer' | 'number' | 'flag';
}

/** One shape of description a file contains, and how to read it.
 *
 *  `examples` and `counterexamples` are real rows from the file. They are not
 *  documentation: freezing refuses a segment whose pattern does not match
 *  every example or matches any counterexample. */
export interface DecoderSegment {
  id: string;
  pattern: string;
  fields: DecoderBinding[];
  examples: string[];
  counterexamples: string[];
  label?: string | null;
}

/** A frozen decoder for one file: content-addressed, self-contained, and
 *  carrying the executor version it was frozen against.
 *
 *  `decoder_id` is the sha256 of the artifact's canonical JSON, so "which
 *  decoder produced this row" is answerable from the row alone. It is a
 *  sibling of the hashed content and never part of it. */
export interface DecoderArtifact {
  schema_version: number;
  decimal: 'dot' | 'comma' | 'either';
  segments: DecoderSegment[];
  decoder_id: string;
}

/** One file as the last build read it: through which rule set, with its own
 *  stamp, counts and report.
 *
 *  Each file is decoded on its own and the results are merged, so a stamp
 *  belongs to a FILE rather than to a catalogue. Where several files disagree
 *  the catalogue-level stamp and report are null and these are where the
 *  facts are. */
export interface BuiltFile {
  source_key: string;
  corpus_id: string;
  filename: string;
  sha256: string;
  rule_set: string | null;
  stamp: CatalogStamp;
  records: number;
  rows_read: number;
  quarantined: number;
  report: CatalogReport | null;
  rows_kept?: number;
  rows_skipped_blank_key?: number;
  /** Rows this file contributed to the merged catalogue. Lower than
   *  `rows_kept` where a newer file already carried the same part numbers,
   *  and zero for a file every row of which is superseded — which is worth
   *  seeing, since such a file is dead weight on every rebuild. */
  rows_emitted: number;
  sampled?: boolean;
}

/** What merging a company's files into one corpus did. */
export interface CompanyCombine {
  sources: BuiltFile[];
  rows_kept: number;
  /** Rows read across every file, before de-duplication and before rows with
   *  no part number were left out. */
  rows_in: number;
  rows_skipped_blank_key: number;
  /** Part numbers that appeared in more than one file. The newest file wins,
   *  and the count is here because that is a policy somebody has to know about
   *  — two price lists disagreeing about one product is a real disagreement. */
  collisions: number;
  collision_examples: string[];
  sampled: boolean;
}

/** One of a company's decoded catalogues: one manufacturer's files, each
 *  decoded through its own config.
 *
 *  A distributor sells Kennametal and YG-1 and more, and each one's price
 *  lists read differently — so a company keeps one catalogue per
 *  manufacturer, each with its own files, build and stamp, and resolves
 *  against the union of them (`CatalogueUnion`). The key is the
 *  catalogue's address on disk and in every union row; the name is only what
 *  the screen calls it, and may be empty for a catalogue migrated from before
 *  names existed. */
export interface CompanyCatalogueEntry {
  connection_id: string;
  catalogue_key: string;
  name: string;
  scope: string;
  /** Whether every file here has a saved decoding config the engine can run
   *  — the one question the build asks. False for a catalogue with no files
   *  at all, because there is nothing to decode either way. */
  decoding_ready: boolean;
  /** The `source_key` of each file that has no runnable config yet. Named
   *  rather than counted: a build refused over six files is not something a
   *  person can act on until they know which one is waiting. */
  awaiting_decoding: string[];
  exists: boolean;
  /** A catalogue row whose file is gone: a rebuild waiting to happen, not an
   *  absent catalogue. Different fix, so it is its own field. */
  built_but_missing_on_disk: boolean;
  /** null, never 0, when nothing is built — a catalogue that is not built says
   *  nothing about coverage. */
  records: number | null;
  rows_read: number | null;
  quarantined: number | null;
  duration_s: number | null;
  built_at: string | null;
  built_by: string | null;
  report: CatalogReport | null;
  stamp: CatalogStamp;
  corpus: {
    corpus_id: string;
    filename: string;
    size_bytes: number;
    sha256: string;
    uploaded_at: string;
    uploaded_by: string | null;
  } | null;
  /** Every file this catalogue would be built from. One catalogue can keep
   *  several — an ERP item master, a manufacturer's range extension, a price
   *  list — and the build merges them. */
  sources: CompanySource[];
  /** What merging them did, at the last build. Null before the first one. */
  ingest: CompanyCombine | null;
  /** Which files the built catalogue actually read, as they were then —
   *  each with the rule set that decoded it, its own stamp and its own
   *  counts. With several merged, one filename does not answer "what is in
   *  this", and no single stamp speaks for all of them. */
  built_from: BuiltFile[] | null;
  /** Built from a set of sources that has since changed — one replaced, added
   *  or removed. Still a real catalogue with a real stamp, just not built from
   *  what this catalogue now holds. Out of date, not wrong. */
  stale: boolean;
}

/** What one company actually resolves against: every built catalogue it
 *  keeps, merged. Null when none is built, which is the honest shape for a
 *  company that resolves nothing.
 *
 *  Described from the union's own manifest, never recounted here. A part
 *  number two catalogues both claimed is a `duplicate` — the most recently
 *  built catalogue's row is the one kept, and the count is reported because
 *  that is a policy somebody has to know about. */
export interface CatalogueUnion {
  records: number;
  version: string | null;
  duplicates: number;
  duplicate_examples: string[];
  /** The built catalogues the union holds, with what each contributed after
   *  duplicates were resolved — so a catalogue can be absent from here while
   *  present on the company, if it is not built. */
  catalogues: {
    catalogue_key: string;
    name: string;
    /** The rule sets its files were decoded through — one catalogue can hold
     *  files that needed different ones. */
    rule_sets: string[];
    built_at: string | null;
    records: number;
    ruleset_checksum?: string;
    run_id?: string;
    pack_version?: string;
    org_id?: string;
  }[];
  /** The nearest-neighbour index beside the union: which embedding model
   *  built it, over how many records, and whether it still describes the
   *  file on disk. Null before the first build, or when the build could not
   *  write it — the next resolution builds one. The union is unaffected
   *  either way; retrieval only ever adds options beneath the engine's own. */
  retrieval: {
    model_id: string;
    dim: number;
    records: number;
    current: boolean;
    min_similarity: number;
  } | null;
}

/** One connected company: its catalogues, and the union they make.
 *
 *  Every mutating catalogue endpoint returns this whole envelope rather than
 *  the one catalogue it touched, because a build or a removal changes the
 *  union too — a screen that swapped in one entry would show a union from
 *  before the change. */
export interface CompanyCatalogue {
  connection_id: string;
  label: string;
  enabled: boolean;
  scope: string;
  catalogues: CompanyCatalogueEntry[];
  union: CatalogueUnion | null;
}

/** One remembered phrase: what a customer asked for in their own words and
 *  the product a person put on the quote for it. Offered back beneath the
 *  engine's ranking on a similar line; never an identity. */
export interface PhraseAlias {
  alias_id: string;
  identity_id: string;
  customer: string;
  phrase: string;
  target_record_id: string;
  source_ref: string;
  recorded_by: string | null;
  created_at: string;
}

export interface PhraseAliases {
  aliases: PhraseAlias[];
  can_manage: boolean;
}

/** How the suggestion layers are doing, counted from this organization's
 *  stored quotes. Every share is null, never 0, when there is nothing to take
 *  a share of. See backend `app/retrieval/report.py`. */
export interface RetrievalReport {
  organization_id: string;
  since: string | null;
  drafts: number;
  counts: {
    lines: number; auto_selected: number; chosen_by_person: number;
    chosen_from_ranking: number; chosen_from_retrieval: number;
    chosen_from_confirmed_code: number; chosen_from_phrase: number;
    typed_unoffered: number; left_open: number;
  };
  shares: {
    found_beneath_ranking: number | null;
    typed_unoffered: number | null;
    auto_selected: number | null;
  };
  learned: {
    phrase_aliases: number; confirmed_codes: number;
    customers_with_aliases: number; training_pairs_floor: number;
  };
  triggers: { ranking: number; meaning: number };
  readings: string[];
}

export interface CompanyCatalogues {
  scope: string;
  companies: CompanyCatalogue[];
  /** The rule sets the pinned engine ships, which a file's decoding config
   *  may name. Chosen, never uploaded — a rule set is regexes the engine runs
   *  over every row, and accepting one from a tenant is accepting arbitrary
   *  patterns to execute. */
  rule_sets: { id: string; path: string }[];
  source: CatalogSource;
  max_corpus_bytes: number;
  /** How many catalogues one company may keep. A ceiling, not a target — the
   *  add control says when it is reached rather than failing on submit. */
  max_catalogues: number;
  can_manage: boolean;
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

/** One member of this organization: an identity, plus the grant that admits it.
 *
 *  The two halves answer different questions and the screen needs both.
 *  `active` is whether this login works *anywhere* — deactivating somebody
 *  closes every door they have. `membership_status` is whether *this*
 *  workspace admits them, which is what ends when a person leaves the company
 *  and keeps their own account elsewhere. Either being off means no access
 *  here, and the grid has to be able to say which. */
export interface PlatformUser {
  user_id: string;
  email: string | null;
  name: string;
  /** The role held **here**. Per organization, so the same person can appear
   *  as an owner on one workspace's screen and a salesperson on another's. */
  role: Role;
  active: boolean;
  /** `ACTIVE` | `INVITED` | `REMOVED`. Optional so a client talking to a
   *  server from before memberships landed renders rather than blanking the
   *  column; treat a missing value as ACTIVE, which is what it was. */
  membership_status?: "ACTIVE" | "INVITED" | "REMOVED";
  has_password: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string | null;
  /** When this person was granted access here — which is not when their
   *  account was created, for anybody who joined an existing workspace. */
  joined_at?: string | null;
  created_by: string | null;
  /** Who granted the membership. Null for the founding one: nobody invited the
   *  person who created the organization. */
  invited_by?: string | null;
  role_changed_by: string | null;
  role_changed_at: string | null;
}

export interface OrgPolicy {
  require_approval_for_quotes: boolean;
  require_approval_below_review_floor: boolean;
  below_cost_requires_owner: boolean;
  allow_self_approval: boolean;
  escalation_creates_approval: boolean;
  /** Every quote has an owner and only the owner changes it; this is the one
   *  widening — managers and owners may change any quote. */
  managers_may_edit_any_quote: boolean;
  updated_at: string | null;
}

/** The organization's quote-level fields as the settings screen edits them.
 *  Same shape the builder reads (`types.QuoteFieldDefinition`); `key` is
 *  absent on a field being added, and the server mints one from the label. */
export interface QuoteFieldSpec {
  key?: string;
  label: string;
  kind: "TEXT" | "MULTILINE" | "NUMBER" | "DATE" | "CHOICE";
  required: boolean;
  choices: string[];
  builtin?: boolean;
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
  /** What this system's sign-in must already be granted — named the way that
   *  system's own admin console names it. Per connector, because the answer is:
   *  a screen showing Zoho scope strings while NetSuite is selected is telling
   *  an owner to grant something that does not exist where they are looking. */
  permissions: ConnectorPermission[];
  /** Where those grants are made, in that console's own navigation. */
  permission_note: string;
  /** The grants as one pasteable string, for the systems that take one (Zoho's
   *  scope field). Empty where access is clicked rather than typed. */
  permission_string: string;
  /** The subset of that string without which no sync runs at all. Offered
   *  beside the full set, never instead of it: a scope ungranted does not fail
   *  loudly, it fails quietly and later. Empty wherever `permission_string` is. */
  permission_string_minimum: string;
  /** Whether this deployment can run the customer-facing authorization for this
   *  system — false where no application is registered, so the screen omits the
   *  button rather than offering one that cannot complete. */
  can_authorize: boolean;
  /** What this platform can *create* in the system, as opposed to read. Empty
   *  for a connection that is read-only here — which is every ERP but Zoho
   *  today, and is a fact an owner granting access deserves to be told rather
   *  than to discover at the moment a send refuses. */
  writes: string[];
  /** The one capability a screen asks about directly: whether a quote built
   *  here can be pushed into that system at all. */
  can_write_quotes: boolean;
}

/** One grant a connector's sign-in needs, and what the platform loses without it. */
export interface ConnectorPermission {
  name: string;
  why: string;
  required: boolean;
  /** The sync stages it feeds — empty for one that gates the sign-in itself. */
  reads: string[];
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
  /** How many payloads this organization has logged, against the page of them
   *  `payloads` holds — the endpoint caps at 50. Without it the screen could
   *  not tell a complete log from the head of a longer one, so every sentence
   *  on it had to be worded as a claim about the rows in hand. */
  total: number;
  /** Counts of the rows in *this response*, not of everything logged — the set
   *  the checker's findings were read off. Not a fraction of `total`:
   *  `flagged` is a defect report, and a defect count needs no denominator. */
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
  /** Every event recorded against this organization, against the page of them
   *  `events` holds — the endpoint caps at 200. Truncation is fine; truncation
   *  a reader cannot see is not, and on this surface "everything" and "the last
   *  two hundred" are different promises. */
  total: number;
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

/** `GET /api/v1/attribution/summary`.
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

/** `GET /api/v1/attribution/events`. A page of the ledger — never a rollup. */
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

/** One calendar month of the roll-up.
 *
 *  `attributed_value` is `null` — not `0` — for a month the ledger holds no
 *  events for, and a chart must draw that as a **break in the line, never a
 *  point at zero**: an empty month of value events cannot be told apart from a
 *  month detection never ran over. `measured` is the field that says which of
 *  the two a `null` is, so nothing has to infer it from the amount. */
export interface AttributionPeriod {
  /** `YYYY-MM`, for keys and sorting. */
  period: string;
  /** `Mar 2026`, for reading. */
  label: string;
  start: string;
  end: string;
  /** How far into the month was actually measured — the month's own end, or
   *  the moment a lapsed plan's window stops. */
  measured_to: string | null;
  /** Whether the whole month has happened *and* is inside the readable window.
   *  Only complete months are in the total and in the ratio. */
  complete: boolean;
  /** Whether any event at all is on record for the month. `false` with a `null`
   *  amount is UNKNOWN; `true` with a `0` amount is a measured zero. */
  measured: boolean;
  events: number;
  attributed_value: MoneyString | null;
  attributed_events: number;
  /** Attributed events in the month carrying no defensible amount, so a reader
   *  can see the total covers fewer rows than the month holds. */
  amounts_missing: number;
}

/** `GET /api/v1/attribution/rollup` — owner only. Value month by month over a
 *  span the caller chooses, and the return on it.
 *
 *  Two rules the screen must not soften. The month **in progress** is
 *  `in_progress` and is never inside `attributed_value`: a subscription bills a
 *  whole month, and dividing part of one by all of it understates the return by
 *  however far through the month the page happened to be opened. And a span
 *  containing a month with nothing on record states **no return at all** —
 *  `roi` is `null` and `evidence_gaps` names the months, because a numerator
 *  covering eight months over a denominator covering twelve is a fabricated
 *  number even though it errs low. */
export interface AttributionRollup {
  span: {
    months_requested: number;
    complete_months: number;
    measured_months: number;
    start: string | null;
    end: string | null;
    label: string | null;
    /** Set where a lapsed plan's entitlement, rather than the clock, decided
     *  how far the span reaches. */
    frozen_at: string | null;
  };
  /** Complete months only, oldest first. */
  periods: AttributionPeriod[];
  /** The month still running, beside the total and never in it. */
  in_progress: AttributionPeriod | null;
  /** ATTRIBUTED over the complete months. `null` where no complete month
   *  exists — which is not a zero. */
  attributed_value: MoneyString | null;
  attributed_events: number;
  /** What one month of the plan costs, echoed back. The caller's own figure —
   *  this platform holds no price for its own plans. */
  monthly_cost: MoneyString | null;
  /** `monthly_cost` x the complete months in the span. */
  platform_cost: MoneyString | null;
  roi: MoneyString | null;
  roi_is_unknown: boolean;
  currency?: string;
  evidence_gaps: EvidenceGap[];
  empty_reason: string | null;
}

/** `GET /api/v1/attribution/evaluation` — owner only. The summary, plus the
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

// ── the quotes nobody wrote an outcome on ────────────────────────────────────
// `GET /api/v1/insight/unrecorded-quotes`, and the one insight response on this
// surface that is typed rather than read as `Record<string, unknown>`.
//
// That is a deliberate exception to the convention beside it, and the reason is
// the reason the endpoint exists. Every other insight screen reaches its figures
// through a local `num = (v: unknown) => Number(v ?? 0)` — the shape
// `viz/QuoteOutcomes.tsx` opens with — which is exactly the `sum(... or 0)` tell
// CLAUDE.md §1 names, wearing a TypeScript hat: an untyped response makes
// inventing a zero the path of least resistance, and *only* the untyped ones
// need it. This payload's whole argument is that a missing expiry date and a
// missing total are facts rather than zeroes; `unrecorded.py` refuses to fold
// them at some length. A type that forces `=== null` at the use site is how that
// refusal survives the trip into a component.

/** Which of the three named piles a quote sits in.
 *
 *  Not a ranking, not a score: `unrecorded.py` sorts lexicographically over two
 *  published quantities inside these groups, and both are on every row, so a
 *  reader can recompute the order by hand. `EXPIRY_NOT_RECORDED` is the one
 *  that carries an argument — a quote with no expiry date is not known to be
 *  lapsed and not known to be live, so it is filed as neither rather than
 *  ranked as one. */
export type UnrecordedQuoteGroup =
  | "PAST_EXPIRY"
  | "EXPIRY_NOT_RECORDED"
  | "STILL_OPEN";

/** One unanswered quote — `commercial/insight/unrecorded.PendingQuote`.
 *
 *  Every nullable field below is `| null` rather than `?`, and the difference is
 *  not stylistic. `?` says the server may omit the key; it never does —
 *  `to_dict` writes all eleven every time. What the payload carries is a
 *  recorded *absence*, which is a fact, and `| null` is the spelling that makes
 *  a reader handle it instead of letting `undefined` slide into `?? 0`. */
export interface UnrecordedQuote {
  /** The source system's own id, which is what an outcome is recorded against.
   *  Never `quote_documents.quote_document_id` — that surrogate is re-minted by
   *  a full re-sync and a pointer written to it would not survive one. */
  quote_document_ref: string;
  /** The human-facing number the ERP printed on it. Null where it has none. */
  number: string | null;
  /** Null where the quote's customer never resolved to a platform record — a
   *  walk-in, or a spelling the contact pull did not match. `customer_label`
   *  still names somebody: the ERP's own typed customer name. */
  customer_id: string | null;
  customer_label: string;
  /** The ERP's own word for this quote's state, verbatim and unmapped, so a
   *  reader can tell a draft from a sent quote without this client owning a
   *  vocabulary that belongs to `ingestion.normalize`. */
  source_status: string;
  /** ISO date the quote was raised. The one date the ERP always supplies. */
  raised_on: string;
  /** ISO date the offer lapses. Null is why `EXPIRY_NOT_RECORDED` exists. */
  expires_on: string | null;
  group: UnrecordedQuoteGroup;
  /** Days since the offer lapsed. Null wherever `expires_on` is absent *or*
   *  still ahead — an unanswerable age and a future one are both refused rather
   *  than folded to zero, and folding either would file the row with the
   *  freshest quotes in the book, where nobody would see it again. */
  days_past_expiry: number | null;
  /** The quote's own selling total: the number that was put in front of the
   *  customer, which is neither cost nor margin and is why every role may read
   *  this list. Null where the ERP gave none — such a row is kept and counted,
   *  because "why did this go quiet" is worth the same on it, and only its
   *  ranking key is missing. */
  value: number | null;
  /** ISO timestamp of an open the ERP saw. Null means **no open was recorded**,
   *  which is not "the customer never opened it": the quote may never have been
   *  sent, tracking may be off, or they may have read a forwarded PDF. Render
   *  the absence as an absence. */
  opened_at: string | null;
}

/** The headline counts, each a count of something stated exactly.
 *
 *  Taken over the whole pile rather than over the page, so the screen can say
 *  how much of it is not on screen. Flattened into the response beside the rows
 *  rather than nested — see `UnrecordedQuotes`. */
export interface UnrecordedQuoteTotals {
  /** How many unanswered quotes this reader may see, in total. */
  count: number;
  by_group: Record<UnrecordedQuoteGroup, number>;
  /** The sum of the rows that carry a total, and of no others.
   *  `quotes_without_a_value` says how many it left out — read the two
   *  together or the figure looks complete and is not. */
  value_at_stake: number;
  quotes_without_a_value: number;
  /** The oldest lapse on the list, so a thin-looking screen can still say how
   *  long the pile has been growing. Null when nothing on it has an expiry date
   *  to have passed — no lapse exists, and a 0 there would read as "the oldest
   *  one lapsed today". */
  longest_lapse_days: number | null;
  /** Quotes the ERP recorded an open on. A positive fact. */
  opened: number;
  /** Quotes with no open on record, named as the absence it is. Deliberately
   *  not `never_opened`, which is a claim the data cannot support. */
  opening_not_recorded: number;
}

/** `GET /api/v1/insight/unrecorded-quotes`.
 *
 *  Extends the totals rather than nesting them because the server spreads them
 *  into the envelope: `_envelope(result, …)` flattens `totals(rows)` alongside
 *  `quotes`, so `count` and `value_at_stake` sit at the top level. Modelled the
 *  way it arrives — a nested `totals` here would be a shape the client invents
 *  and then has to build on every fetch. */
export interface UnrecordedQuotes extends UnrecordedQuoteTotals {
  /** The page. At most `limit` rows, ranked; `count` is the whole pile. */
  quotes: UnrecordedQuote[];
  /** How many rows are on this page. `listed < count` means the rest are real
   *  and unshown, never that they do not exist. */
  listed: number;
  /** The business day the ages were measured against, in the organization's
   *  own timezone rather than the browser's. */
  as_of: string;
  /** The order the groups are in, published by the server so a reader can
   *  recompute the list rather than infer the ranking. Iterate this for
   *  section order instead of hardcoding it — a second copy is one that
   *  disagrees the day the ordering argument changes. */
  group_order: UnrecordedQuoteGroup[];
  /** Why the list is empty, written where the query happened. Null when it is
   *  not empty. The client never decides this — it could only guess. */
  empty_reason: string | null;
  currency: string;
  thresholds_version: string;
}

// ── the monetization console ────────────────────────────────────────────────
//
// PIE's own pricing model, not a tenant's. Typed loosely on purpose in two
// places — `waterfall` and the ladder rows carry a deep, evolving shape the
// server owns entirely, and a hand-mirrored interface for every nested field
// would be a second schema that drifts. What *is* typed is everything the
// screen actually reads, so a rename on the server breaks the build.

/** Money and ratios as they arrive: money is a decimal string, never a number,
 *  so nothing is lost between the server's `Decimal` and the screen. */
export interface MonetizationFee {
  strategy: string;
  label: string;
  metric: string;
  annual_fee: string;
  fixed_component: string;
  variable_component: string;
  basis: Record<string, string>;
  bound_applied: string | null;
}

export interface MonetizationEvaluation {
  customer: string;
  fee: MonetizationFee;
  economic_value: string;
  incremental_gross_profit: string;
  value_multiple: string | null;
  customer_roi: number | null;
  retained_value: string;
  value_capture_pct: number | null;
  payback_months: number | null;
  fee_pct_of_incremental_value: number | null;
  fee_pct_of_gross_profit: number | null;
  fee_pct_of_gmv: number | null;
  revenue_per_rfq: string | null;
  revenue_per_order: string | null;
  clears_min_roi: boolean;
  clears_payback: boolean;
  thresholds_cleared: Record<string, boolean>;
  /** Why this fee should not be offered. Null when it clears everything — and
   *  rendered whenever it is not, because a row failing its own threshold that
   *  looks like every other row is the whole reason a bad price gets quoted. */
  refusal: string | null;
}

export interface MonetizationFunnel {
  rfqs: string; quotes: string; orders: string;
  revenue: string; cogs: string; gross_profit: string;
}

export interface MonetizationWaterfall {
  profile: Record<string, unknown>;
  baseline: MonetizationFunnel;
  with_pie: MonetizationFunnel;
  covered_baseline: MonetizationFunnel;
  covered_with_pie: MonetizationFunnel;
  incremental_orders: string;
  incremental_revenue: string;
  incremental_gross_profit: string;
  components: {
    gp_from_conversion: string;
    gp_from_order_value: string;
    gp_from_margin: string;
    procurement_savings: string;
    /** Null is UNKNOWN, never ₹0 — the screen must render it as UNKNOWN with
     *  the reason beside it, exactly as the attribution screens do. */
    productivity_savings: string | null;
  };
  productivity_excluded_reason: string | null;
  hours_saved: string;
  total_economic_value: string;
  bases: Record<string, string>;
  substitution: { opportunities: string; successful: string };
}

export interface MonetizationHypothesisReading {
  annual_fee: string;
  covers_cost_to_serve: boolean;
  shortfall_multiple: number | null;
  value_capture_pct: number | null;
  customer_roi: number | null;
}

export interface MonetizationHypothesis {
  hypothesis_rate: number;
  cost_floor: string;
  ladder: Record<string, MonetizationEvaluation[]>;
  verdict: {
    viable: boolean;
    readings: Record<string, MonetizationHypothesisReading>;
    statement: string;
  };
}

export interface MonetizationRecommendation {
  band: {
    cost_floor: string;
    roi_ceiling: string | null;
    target_at_capture: string;
    is_empty: boolean;
    note: string;
  };
  recommended_annual_fee: string;
  /** The recommendation: a flat annual fee, banded by turnover. `variable_rate`
   *  is 0 by construction — the customer's turnover exists whether or not they
   *  use PIE, so a fee that moves with it claims credit the platform cannot
   *  defend at renewal. */
  structure: {
    kind: string;
    metric: string;
    turnover_band: string;
    platform_fee: string;
    variable_rate: number;
    variable_rate_pct: string;
    scorecard_key: string;
    weighted_score: number | null;
    why: string;
    expansion: {
      long_run_uplift: number;
      long_run_uplift_note: string;
      years_between_re_ratings: number;
      band_width_sensitivity: {
        band_width: string;
        years_between_re_ratings: number;
        accounts_re_rating_per_year: number;
      }[];
      levers: { lever: string; automatic: boolean; mechanism: string;
                yields: string }[];
      note: string;
    };
  };
  /** The metered structure, priced and scored rather than hidden. It collects
   *  the same money; a buyer who wants a smaller committed cheque can have it,
   *  and the score says what accepting that costs. */
  alternative: {
    kind: string;
    metric: string;
    platform_fee: string;
    variable_base: string;
    variable_base_measurability: string;
    variable_base_why: string;
    variable_rate: number;
    variable_rate_pct: string;
    variable_cap: string;
    weighted_score: number | null;
    evaluation: MonetizationEvaluation;
    why_not_chosen: string;
  };
  evaluation: MonetizationEvaluation;
  pie_unit_economics: MonetizationUnitEconomics;
  design_partner_offer: {
    annual_fee: string;
    discount_vs_list: number | null;
    conditions: string[];
    evaluation: MonetizationEvaluation;
  };
}

export interface MonetizationUnitEconomics {
  annual_revenue: string;
  cost: Record<string, string>;
  gross_profit: string;
  gross_margin: number | null;
  contribution: string;
  contribution_margin: number | null;
  cac: string;
  ltv: string;
  ltv_cac: number | null;
  cac_payback_months: number | null;
  revenue_per_rfq: string | null;
  revenue_per_order: string | null;
  expected_life_years: number | null;
  evidence_grade: string;
  warnings: string[];
}

export interface MonetizationHybridRow extends MonetizationEvaluation {
  structure: string;
  scorecard_key: string;
  weighted_score: number | null;
}

/** What a turnover band cannot see: the same turnover at a different
 *  adoption creates very different value, and PIE cannot measure adoption
 *  from synced rows to correct for it. */
export interface MonetizationAdoptionSensitivity {
  baseline_revenue: string;
  reference_pie_rfq_share: number;
  rows: {
    pie_rfq_share: number;
    is_reference: boolean;
    total_economic_value: string;
    connected_book_revenue: string;
  }[];
  value_spread_across_sweep: number | null;
  connected_book_revenue_spread_across_sweep: number | null;
  value_sensitivity_relative_to_revenue: number | null;
  reading: string;
}

export interface MonetizationCalculation {
  parameters_version: string;
  waterfall: MonetizationWaterfall;
  margin_hypothesis: MonetizationHypothesis;
  transaction_ladder: {
    cost_floor: string;
    ladder: Record<string, MonetizationEvaluation[]>;
  };
  subscription_ladder: {
    rows: MonetizationEvaluation[];
    capture_band: number[];
    defensible_range: { low: string | null; high: string | null };
  };
  hybrids: {
    target_fee: string; platform_fee: string; variable_cap: string;
    rows: MonetizationHybridRow[];
  };
  all_strategies: MonetizationEvaluation[];
  recommendation: MonetizationRecommendation;
  cost_floor: string;
  pie_unit_economics: MonetizationUnitEconomics;
  adoption_sensitivity: MonetizationAdoptionSensitivity;
}

export interface MonetizationScorecardRow {
  rank: number;
  key: string;
  label: string;
  weighted_score: number;
  note: string;
  scores: Record<string, number>;
  game_theory: Record<string, unknown> | null;
}

export interface MonetizationScorecard {
  evidence_grade: string;
  evidence_note: string;
  weights: Record<string, number>;
  criteria: string[];
  ranking: MonetizationScorecardRow[];
}

/** An archetype as the server states it, for seeding the calculator form. */
export interface MonetizationSegments {
  archetypes: Record<string, Record<string, unknown>>;
  impacts: Record<string, Record<string, number>>;
}

/* ── the operations dashboard (manager or owner) ─────────────────────────────
 * Mirrors `backend/app/observability/dashboard.py`, `health.py` and
 * `capacity.py`.
 *
 * **The nulls are the contract, not an oversight.** Every optional field below
 * is a value that module deliberately refuses to invent: an unmeasured capacity
 * component, an error rate over a worker that has served no requests, a
 * throughput with no finished run to derive it from, a bottleneck ranked over
 * nothing. Each is a `null` with a `basis` sentence beside it, written to the
 * rule in CLAUDE.md §1 — absence of evidence is not a pass.
 *
 * They are typed `| null` here so the screen cannot quietly undo that. The
 * previous shape declared them required, and the component rendered
 * `error_rate?.toFixed(2) || "0"` — an idle worker whose rate the server had
 * declined to state, drawn as a green 0%.
 */

/** One registered health check, as `HealthRegistry.to_dict` states it. */
export interface HealthComponent {
  name: string;
  /** `healthy` | `degraded` | `unhealthy` | `unknown`. A bare string because
   *  it is `HealthStatus.value` and a union here would need updating in two
   *  places to add one. */
  status: string;
  timestamp: string;
  message: string | null;
  details: Record<string, unknown>;
}

export interface SystemHealth {
  timestamp: string;
  /** The worst any component reports, where `unknown` outranks `healthy`. */
  status: string;
  components: Record<string, HealthComponent>;
}

export interface CapacityComponent {
  name: string;
  /** Utilization as a ratio, or null where the component was not measured. */
  current: number | null;
  /** `healthy` | `warning` | `critical` | `unknown`. */
  status: string;
  percentage: number | null;
  /** How much more load fits before critical. Null when `current` is —
   *  headroom over an unmeasured base is a reassuring number meaning nothing. */
  safe_capacity_multiplier: number | null;
  threshold_warning: number;
  threshold_critical: number;
  basis: string;
}

export interface Capacity {
  timestamp: string;
  components: CapacityComponent[];
  /** Null when no component reported a usable figure: an unmeasured component
   *  cannot be ranked, so there is no most-saturated one to name. */
  bottleneck: { component: string; current: number; status: string } | null;
  /** Null for the same reason, and never to be read as "plenty of room". */
  safe_capacity_headroom: { multiplier: number; message: string } | null;
  /** The components the headroom claim does *not* rest on, by name. */
  unmeasured: string[];
  recommended_action: string;
}

export interface CurrentLoad {
  timestamp: string;
  /** `active_requests` is null, not 0: nothing tracks in-flight requests, and
   *  a placeholder zero is indistinguishable from a genuinely idle server. */
  api: { requests_total: number; active_requests: number | null; basis: string };
  database: { queries_total: number; basis: string };
  background: {
    active_jobs: number;
    active_syncs: number;
    stalled: number;
    total_active: number;
    basis: string;
  };
}

/** p50/p95/p99 in milliseconds. The block is null when nothing instrumented
 *  the histogram at all; a percentile inside it is null when the histogram
 *  exists and has seen nothing. Two different facts, and the screen says so. */
export type Latencies = { p50: number | null; p95: number | null; p99: number | null };

export interface ApiPerformance {
  timestamp: string;
  /** Always `"worker"` — these counters are one process's, not the
   *  deployment's, and the payload says so rather than letting a reader add
   *  them up. */
  scope: string;
  worker: string;
  counting_since: string;
  observed_minutes: number;
  basis: string;
  requests_total: number;
  errors_total: number;
  /** Null over zero requests: a rate with no denominator is undefined, and 0%
   *  on an idle worker reads as healthy rather than as silent. */
  error_rate: number | null;
  latency_ms: Latencies | null;
}

export interface DatabaseStatus {
  timestamp: string;
  connections: { active: number };
  queries: { total: number; errors: number };
  latency_ms: Latencies | null;
}

/** Dates that tell a quiet window from a dead one. An all-zero, all-green
 *  payload is what a revoked refresh token looks like too. */
export interface RunHistory {
  last_run_at: string | null;
  last_successful_run_at: string | null;
  ever_run: boolean;
  basis: string;
}

export interface StalledRuns {
  count: number;
  by_phase: Record<string, number>;
  /** Empty when nothing is stalled. */
  detail: string;
}

/** A run that failed or half-finished. The server sends the ten most recent
 *  under `jobs` and the five most recent under `syncs`; `recent_24h` carries
 *  the true totals, so a screen can say which of the two it is showing. */
export interface RunIssue {
  sync_run_id: string;
  connection_id: string | null;
  /** `FAILED` or `PARTIAL`. Partial is its own outcome: it wrote rows and did
   *  not finish, so counting it either way misstates what arrived. */
  status: string;
  error: string | null;
  timestamp: string | null;
}

export interface JobFailure extends RunIssue {
  job_kind: string;
}

export interface BackgroundJobs {
  timestamp: string;
  source: string;
  /** Every job kind this deployment records. There is one, and publishing it
   *  is what makes an empty `active` block mean "idle" rather than "unmeasured". */
  job_kinds: string[];
  active: { count: number; by_phase: Record<string, number> };
  stalled: StalledRuns;
  history: RunHistory;
  recent_24h: {
    basis: string;
    completed: number;
    partial: number;
    failed: number;
    total_records_processed: number;
  };
  failures: JobFailure[];
}

export interface ErpSyncStatus {
  timestamp: string;
  source: string;
  active: {
    count: number;
    by_phase: Record<string, number>;
    by_connection: Record<string, number>;
  };
  stalled: StalledRuns;
  history: RunHistory;
  recent_24h: {
    basis: string;
    completed: number;
    partial: number;
    failed: number;
    total_records_fetched: number;
    total_records_processed: number;
    /** Null where no finished run could answer it. Never 0 — that is what a
     *  sync moving no data looks like. */
    throughput_records_per_sec: number | null;
    throughput_basis: string;
  };
  issues: RunIssue[];
}

export interface TenantUsageRow {
  organization_id: string;
  signals_generated: number;
}

export interface TenantUsage {
  timestamp: string;
  tenants: TenantUsageRow[];
  total_tenants?: number;
  /** Which of the two lists this is. The query carries no organization
   *  predicate on purpose — row-level security scopes it where the policy
   *  binds, and this field is how a reader tells a scoped result from an
   *  unscoped one instead of guessing from the row count. */
  scope?: "OWN_ORGANIZATION" | "ALL_ORGANIZATIONS";
  /** Present instead of a list when the query itself failed. */
  error?: string;
}

export interface ObservabilityDashboard {
  timestamp: string;
  health: SystemHealth;
  load: CurrentLoad;
  api: ApiPerformance;
  database: DatabaseStatus;
  jobs: BackgroundJobs;
  syncs: ErpSyncStatus;
  capacity: Capacity;
  tenants: TenantUsage;
}
