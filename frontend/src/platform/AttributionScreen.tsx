/**
 * What PIE changed — the value the platform can actually evidence.
 *
 * This is the screen a renewal is argued from, which is exactly why it is
 * written to be *unimpressive when the evidence is thin*. Four rules shape
 * every line of it, and each one is a place a screen like this normally lies.
 *
 * **The headline is ATTRIBUTED and nothing else.** POTENTIAL and REALIZED are
 * real and are shown — in their own section, under their own labels, with the
 * server's own sentence saying the classes must never be added together.
 * ESTIMATED is not shown at all: no detector produces it, and a tile reading
 * "Estimated — none recorded" claims a measurement nobody attempted. They describe overlapping facts about the same quote line on
 * purpose (one as the flag, one as the win that followed it), so a total that
 * summed them would double count the line while looking generous.
 *
 * **Nothing on this screen is summed by the browser.** `/summary` is the only
 * legitimate rollup; the ledger rows are rows. The one piece of arithmetic here
 * is net value, and it is called out where it happens.
 *
 * **Unknown is rendered as unknown.** A null attributed value means no
 * detection run is on record — it is *not* ₹0, and the two are shown as
 * different sentences because they mean opposite things. ROI with no cost
 * supplied is UNKNOWN, never "0x". No figure on this screen falls back to a
 * dash that could be read as a zero.
 *
 * **The evidence gaps are the product, not the fine print.** They sit directly
 * under the headline, spelled out, before anything favourable. Two of the five
 * event types have no detector at all and one finds nothing today; all three
 * arrive as named gaps from the server and are printed as named gaps here.
 *
 * Role: manager or owner for the ledger — every row of it is gross-profit
 * arithmetic, so there is no salesperson-safe projection and the server refuses
 * them outright. The 30-day report against the pre-trial baseline is owner
 * only, which is a panel gate rather than a screen gate.
 */
import { useCallback, useMemo, useState } from "react";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { money, moneySymbol, count as counted } from "../money";
import { formatDate, formatDateTime } from "../when";
import { abilityFor } from "./ability";
import { papi } from "./api";
import { DataGrid, type ColDef } from "./DataGrid";
import {
  EmptyState, ErrorState, LoadingState, MetricCard, SectionHeader, StatusChip,
  TOUCH, type Tone,
} from "./kit";
import type {
  AttributionEvaluation, AttributionEvents, AttributionPeriod,
  AttributionRollup, AttributionSummary, EvidenceGap, PlatformSession,
  ValueClassBreakdown, ValueEventRow,
} from "./types";
import { pct, pp, useInsight } from "./viz/useInsight";

/* ── the vocabulary ───────────────────────────────────────────────────────── */

/** What each `ValueEventType` is called on screen, and what it means.
 *
 *  Keyed off the server's own list rather than a copy of the enum: `/events`
 *  returns `event_types`, so a sixth type appears here as its raw code — ugly,
 *  and visible — instead of silently vanishing from the breakdown. */
const EVENT_TYPE: Record<string, { label: string; what: string }> = {
  MARGIN_PROTECTED: {
    label: "Margin protected",
    what: "A line was priced below its floor, the platform flagged it, the "
      + "price then moved up, the price that went out cleared the floor, and "
      + "the quote was not lost. The amount is the price movement × quantity, "
      + "capped at the gap to the floor — clearing it by more than it asked "
      + "for is commercial judgement, not something the guardrail did. A line "
      + "that shipped below its floor anyway counts nothing.",
  },
  DISCOUNT_LEAKAGE_PREVENTED: {
    label: "Discount recovered",
    what: "A line was repriced upward between the first snapshot and the one "
      + "that went out, with a flag on record before the move. The amount is "
      + "(final price − opening price) × quantity.",
  },
  EQUIVALENT_SAVING: {
    label: "Equivalent saving",
    what: "A cheaper equivalent was substituted for the item specified. The "
      + "amount is (original cost − alternative cost) × quantity.",
  },
  LOST_SALE_RECOVERED: {
    label: "Lost sale recovered",
    what: "A quote lost and later won back.",
  },
  PROCUREMENT_OPPORTUNITY: {
    label: "Procurement opportunity",
    what: "A purchasing recommendation acted on.",
  },
};

function eventTypeLabel(code: string): string {
  return EVENT_TYPE[code]?.label ?? code.replace(/_/g, " ").toLowerCase();
}

/** How strong the evidence behind an amount is.
 *
 *  A `Chip` with a word in it, never a colour on a number: ATTRIBUTED and
 *  POTENTIAL sitting in one column as two shades of the same figure is exactly
 *  how a reader ends up adding them. */
const VALUE_CLASS: Record<string, { label: string; tone: Tone; tip: string }> = {
  ATTRIBUTED: {
    label: "Attributed", tone: "good",
    tip: "The money moved and the intervention is on record as preceding it. "
      + "The only class in the headline.",
  },
  REALIZED: {
    label: "Realized", tone: "info",
    tip: "The money moved, but nothing proves the platform caused it. As far "
      + "as the evidence goes, the business would have made it anyway.",
  },
  POTENTIAL: {
    label: "Potential", tone: "warn",
    tip: "An opportunity was identified. Nothing has happened yet — this is "
      + "not money earned and is never added to the headline.",
  },
};

function classOf(code: string) {
  return VALUE_CLASS[code]
    ?? { label: code, tone: "neutral" as Tone,
         tip: "A value class this screen has no wording for yet." };
}

/** The evidence-gap reasons worth a stronger word than "not measured".
 *
 *  `NO_EVENTS_RECORDED` is the one that must never read as good news: it means
 *  no detection run is on record, which is indistinguishable from "the
 *  detectors ran and found nothing" and means the opposite thing. */
const GAP_TONE: Record<string, "warning" | "info"> = {
  NO_EVENTS_RECORDED: "warning",
  NO_TRIAL_ON_RECORD: "warning",
  NO_BASELINE_ON_RECORD: "warning",
};

/* ── money, and the difference between nothing and zero ───────────────────── */

/** A serialized `Decimal` as a number, or `null` for "no figure".
 *
 *  Amounts arrive as strings ("12500.0000") because money is `Decimal` on the
 *  server and a float round-trip would lose the last paisa. `Number` is used
 *  for *display and sorting only* — nothing on this screen prices anything. */
function amountOf(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** An amount, or the reason there is not one — in words.
 *
 *  Deliberately not `CurrencyValue`, which renders an em dash for `null`. On
 *  every other screen that is right; here it is the one mistake this screen
 *  exists to avoid, because a dash next to a rupee figure reads as zero and a
 *  missing measurement and a measured zero mean opposite things. */
function Amount({ value, unknown = "Not measured" }: {
  value: string | null | undefined;
  unknown?: string;
}) {
  const n = amountOf(value);
  if (n === null) {
    return (
      <Box component="span" sx={{ color: "text.secondary", fontSize: "0.6em" }}>
        {unknown}
      </Box>
    );
  }
  return <Box component="span" sx={{ fontVariantNumeric: "tabular-nums" }}>{money(n)}</Box>;
}

/* ── the evidence gaps, printed rather than tucked away ───────────────────── */

/** What could not be measured, in plain statements, above the good news.
 *
 *  `subject` is either a metric name or a `ValueEventType`, so it is given the
 *  event type's label where there is one — "Lost sale recovered — no evidence
 *  links a lost quote to a later recovered order" is a sentence somebody can
 *  act on, and `LOST_SALE_RECOVERED` is not. */
function EvidenceGaps({ gaps, title }: { gaps: EvidenceGap[]; title: string }) {
  if (!gaps.length) return null;
  const severity = gaps.some((g) => GAP_TONE[g.reason] === "warning")
    ? "warning" : "info";
  return (
    <Alert severity={severity} icon={false} sx={{ mb: 3 }}>
      <AlertTitle>{title}</AlertTitle>
      <Typography variant="body2" sx={{ mb: 1, maxWidth: "80ch" }}>
        Each line below is something this window could not measure, and why. A
        figure with a gap behind it is not a smaller figure — it is a figure
        that does not cover the thing named here.
      </Typography>
      <Stack component="ul" spacing={0.75} sx={{ pl: 3, m: 0 }}>
        {gaps.map((gap, i) => (
          <Typography component="li" variant="body2" key={`${gap.subject}-${gap.reason}-${i}`}>
            <strong>{eventTypeLabel(gap.subject)}</strong> — {gap.detail}
          </Typography>
        ))}
      </Stack>
    </Alert>
  );
}

/* ── the headline ─────────────────────────────────────────────────────────── */

/** The one sentence this screen owes the reader when the number is weak.
 *
 *  No dark patterns means the honest outcome is *required output*, not an edge
 *  case to hide: an unmeasured window says nothing was measured, and a measured
 *  zero says the platform has not demonstrated value yet. Neither is softened,
 *  and neither is topped up from a weaker class. */
function Verdict({ attributed, events }: {
  attributed: string | null;
  events: number | undefined;
}) {
  const value = amountOf(attributed);
  if (value === null) {
    return (
      <Alert severity="warning" sx={{ mt: 2 }}>
        <AlertTitle>Nothing has been measured in this window</AlertTitle>
        No value events are recorded, so there is no attributed figure — and
        this is not a measured zero. It means no detection run is on record,
        and from here that cannot be told apart from a run that found nothing.
        Until it can, this evaluation has demonstrated no value.
      </Alert>
    );
  }
  if (value <= 0) {
    return (
      <Alert severity="warning" sx={{ mt: 2 }}>
        <AlertTitle>This evaluation has not demonstrated value yet</AlertTitle>
        Events were recorded in this window and none of them are attributed, so
        the measured attributed value is {money(value)} — a real zero, not a
        missing figure. It is deliberately not topped up from the potential and
        estimated figures below, which describe opportunities and models rather
        than money earned.
      </Alert>
    );
  }
  return (
    <Alert severity="success" sx={{ mt: 2 }}>
      <AlertTitle>{money(value)} attributed, from {counted(events ?? 0)} event(s)</AlertTitle>
      Every rupee here comes from a line where the money moved <em>and</em> an
      intervention is on record as preceding it. Open any row in the ledger
      below to re-derive it from its own operands.
    </Alert>
  );
}

/** The (event type × class) figures for one class, as a fact panel.
 *
 *  A `<table>` and not a `DataGrid` on the test `DataGrid.tsx` states: the row
 *  count is the number of event types the server knows about, which is set by
 *  the code and not by the size of the business.
 *
 *  Three states per row, and keeping them apart is the whole point of the
 *  panel. A type with events shows them. A type the detectors looked for and
 *  did not find shows a real zero count and "no events recorded" — never ₹0,
 *  because a count and an amount are different measurements. A type nothing
 *  can measure at all (two of the five have no detector, and the server says
 *  which in `evidence_gaps`) shows neither: it is named as unmeasurable, so it
 *  cannot be read as a zero somebody went looking for. */
function ClassBreakdown({ rows, types, valueClass, unmeasurable }: {
  rows: ValueClassBreakdown[];
  types: string[];
  valueClass: string;
  /** Event types the server named as not measurable, by their own code. */
  unmeasurable: Set<string>;
}) {
  return (
    <Box component="table" className="facttable" sx={{ width: "100%" }}>
      <thead>
        <tr>
          <th>What was measured</th>
          <th className="fv">Events</th>
          <th className="fv">Amount</th>
        </tr>
      </thead>
      <tbody>
        {types.map((type) => {
          // A lookup, never a sum: two classes of the same event type can
          // describe one quote line, so adding down this column would double
          // count it. The summary is the only legitimate rollup.
          const row = rows.find(
            (r) => r.event_type === type && r.value_class === valueClass);
          return (
            <tr key={type}>
              <td>
                {eventTypeLabel(type)}
                {EVENT_TYPE[type] && (
                  <div className="fsrc">{EVENT_TYPE[type].what}</div>
                )}
              </td>
              <td className="fv">
                {row ? counted(row.events)
                  : unmeasurable.has(type) ? "—" : counted(0)}
              </td>
              <td className="fv">
                {row ? <Amount value={row.amount} unknown="no amount" />
                  : unmeasurable.has(type)
                    ? <span className="text-muted">
                        nothing measures this — see the gaps above
                      </span>
                    : <span className="text-muted">no events recorded</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </Box>
  );
}

/* ── the ledger, and what is behind one row ───────────────────────────────── */

/** One event's operands and the rows it was computed over.
 *
 *  This is the drill-down the headline is only trustworthy because of: `basis`
 *  holds the values the amount came from and `evidence_refs` names the records
 *  it came from, so the figure is re-derivable rather than asserted. Both are
 *  printed as they were stored — reformatting an operand here would put a
 *  second version of the number next to the first. */
function EventDrilldown({ row, onClose }: {
  row: ValueEventRow | null;
  onClose: () => void;
}) {
  if (!row) return null;
  const cls = classOf(row.value_class);
  const basis = Object.entries(row.basis ?? {});
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
          <span>{eventTypeLabel(row.event_type)}</span>
          <StatusChip label={cls.label} tone={cls.tone} tip={cls.tip} />
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          <Amount value={row.amount} unknown="No amount — this class carries none" />
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {row.currency} · occurred {formatDateTime(row.occurred_at)}
        </Typography>

        <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>
          How it was computed
        </Typography>
        {basis.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            This row carries no basis. Treat the amount as unsupported.
          </Typography>
        ) : (
          <Box component="table" className="facttable" sx={{ width: "100%" }}>
            <tbody>
              {basis.map(([key, value]) => (
                <tr key={key}>
                  <td>{key.replace(/_/g, " ")}</td>
                  <td className="fv mono">
                    {Array.isArray(value) ? value.join(", ") : String(value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Box>
        )}

        <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>
          The records it was computed over
        </Typography>
        {row.evidence_refs.length === 0 ? (
          // The ledger refuses to write an event without evidence, so this is
          // a defect report rather than an empty state.
          <Alert severity="error">
            This event names no evidence. The ledger refuses to write one, so
            seeing this is a fault worth reporting.
          </Alert>
        ) : (
          <Box component="table" className="facttable" sx={{ width: "100%" }}>
            <tbody>
              {row.evidence_refs.map((ref, i) => (
                <tr key={i}>
                  <td>{String(ref.record_type ?? "record").replace(/_/g, " ")}</td>
                  <td className="fv mono" style={{ wordBreak: "break-all" }}>
                    {String(ref.record_id ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </Box>
        )}

        <Typography variant="caption" color="text.secondary"
                    sx={{ display: "block", mt: 3 }}>
          Judged under thresholds {row.thresholds_version || "—"} · recorded{" "}
          {formatDateTime(row.created_at)}. The version is per row, not per
          screen: the ledger is append-only, so one window can legitimately span
          two policies.
        </Typography>
      </DialogContent>
    </Dialog>
  );
}

/* ── the screen ───────────────────────────────────────────────────────────── */

/** The door. The nav item is manager-and-above, and the nav is not the only way
 *  in — a URL that was bookmarked, shared or typed reaches this whatever the
 *  role, and a salesperson who follows one should meet a closed door rather
 *  than two failed requests and a red box.
 *
 *  A wrapper rather than an early return inside the screen, because the
 *  requests live in hooks: hooks run before any `return`, so the only way not
 *  to fire them is not to mount the component that holds them. */
export function AttributionScreen({ session }: { session: PlatformSession }) {
  // Mirrors `require_manager_or_owner` on every attribution route. The server
  // is the authority; this only decides what is worth offering.
  if (!abilityFor(session).can("read", "economics")) {
    return (
      <Stack spacing={3}>
        <SectionHeader
          title="What PIE changed"
          sub="What the platform can evidence it was worth, over the trial window." />
        <EmptyState
          title="This screen belongs to management"
          reason="Every row of this ledger is gross-profit arithmetic over quote lines — a margin-protected event names a line that was priced below its floor, so the event type is the below-floor flag and the count is the below-floor count. There is no version of this screen with the economics taken out, so the server refuses it for this role rather than serving a redacted copy." />
      </Stack>
    );
  }
  return <ValueLedger session={session} />;
}

function ValueLedger({ session }: { session: PlatformSession }) {
  // The report against the pre-trial baseline is owner-only, where the ledger
  // it is built from is manager-or-owner. Two server gates, so two questions.
  const mayReadReport = abilityFor(session).can("read", "evaluation");

  // The shared loader, not a second one: `useInsight` is the four-state query
  // hook every screen in this app reads through, and the `view` string it takes
  // is what namespaces the cache. Its name says "insight" because that is the
  // surface it was written for; nothing in it is specific to those endpoints.
  const summary = useInsight<AttributionSummary>(
    "attribution-summary",
    () => papi.attributionSummary(session.token),
    [session.token]);

  const ledger = useInsight<AttributionEvents>(
    "attribution-events",
    () => papi.attributionEvents(session.token, { limit: 200 }),
    [session.token]);

  const data = summary.data;
  const events = ledger.data;

  const [open, setOpen] = useState<ValueEventRow | null>(null);

  const columns = useMemo<ColDef<ValueEventRow>[]>(() => [
    {
      field: "occurred_at", headerName: "Occurred", width: 150, flex: 0,
      sort: "desc", filter: "agTextColumnFilter",
      valueFormatter: (p) => formatDate(p.value as string | null),
      headerTooltip: "When the business fact happened — not when it was detected.",
    },
    {
      field: "event_type", headerName: "What was measured", flex: 1.2, minWidth: 200,
      filter: "agTextColumnFilter",
      valueFormatter: (p) => eventTypeLabel(String(p.value)),
    },
    {
      field: "value_class", headerName: "Evidence", width: 150, flex: 0,
      filter: "agTextColumnFilter",
      cellRenderer: (p: { value?: string }) => {
        const cls = classOf(String(p.value));
        return <StatusChip label={cls.label} tone={cls.tone} tip={cls.tip} dense />;
      },
    },
    {
      headerName: "Amount", width: 150, flex: 0,
      type: "numericColumn", cellClass: "ag-num", filter: "agNumberColumnFilter",
      // Sorted on the parsed number, rendered as money: a column that sorted
      // the string "9" above "12500.0000" would look like it worked.
      valueGetter: (p) => amountOf(p.data?.amount),
      valueFormatter: (p) =>
        p.value === null || p.value === undefined
          ? "no amount" : money(Number(p.value)),
      headerTooltip: "Blank is 'this class carries no defensible amount', "
        + "never zero.",
    },
    {
      headerName: "How", flex: 1.4, minWidth: 240, sortable: false,
      valueGetter: (p) => String(p.data?.basis?.formula ?? ""),
      valueFormatter: (p) => (p.value ? String(p.value) : "no basis recorded"),
      headerTooltip: "The formula behind the amount. Open the row for its "
        + "operands and the records they came from.",
    },
    {
      headerName: "Evidence rows", width: 140, flex: 0,
      type: "numericColumn", cellClass: "ag-num", sortable: false, filter: false,
      valueGetter: (p) => p.data?.evidence_refs.length ?? 0,
    },
  ], []);

  const gaps = data?.evidence_gaps ?? [];
  const breakdown = data?.by_event_type ?? [];
  // The server's own vocabulary where it is available, so an event type this
  // window holds nothing for still gets a row saying so — the alternative is a
  // measurable-but-unmeasured type quietly disappearing, which is the shape of
  // absence this screen exists to refuse. Falls back to what the summary
  // actually carries if the ledger request has not landed or failed.
  const types = events?.event_types?.length
    ? events.event_types
    : Array.from(new Set(breakdown.map((r) => r.event_type)));
  // Which event types nothing can measure, named by the server rather than
  // listed here. Two of the five have no detector at all — that is a finding,
  // not an omission, and it must never be rendered as a zero somebody looked
  // for. Hard-coding the pair would put a second, staler copy of that fact in
  // the browser.
  const unmeasurable = new Set(
    gaps.filter((g) => g.reason === "NOT_MEASURABLE").map((g) => g.subject));

  return (
    <Stack spacing={3}>
      <SectionHeader
        title="What PIE changed"
        sub="What this platform can evidence it was worth — measured from rows the quote desk already writes, never modelled and never estimated into the headline." />

      {summary.error ? (
        <ErrorState
          title="The value ledger could not be read"
          error={summary.error}
          onRetry={summary.reload} />
      ) : summary.loading || !data ? (
        <LoadingState rows={3} height={110} label="Reading the value ledger…" />
      ) : (
        <>
          {/* The window, named. Two figures over different periods look
              identical on a screen, and while the window was implicitly the
              trial a frozen headline looked exactly like a live one — which is
              how a paying customer's value screen sat at their trial month
              without anyone noticing. The countdown chip appears only while a
              trial is actually running; after that "day 30 of 30" is a caption
              on a window that has moved on. */}
          {data.window && (
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
              <StatusChip label={data.window.label} tone="neutral" />
              <StatusChip
                label={`${formatDate(data.window.start)} – ${formatDate(data.window.end)}`}
                tone="neutral"
                tip={`Measured to ${formatDateTime(data.measured_to ?? data.window.end)}.`} />
              {data.trial?.is_running && (
                <StatusChip label={`${data.trial.days_remaining} day(s) left`}
                            tone="info" />
              )}
              {data.window.frozen_at && (
                <StatusChip
                  label="Frozen at trial end" tone="warn"
                  tip="This organization is on the free Quote Desk. Detection has kept running; reading past the trial is what the plan restores." />
              )}
            </Stack>
          )}

          {/* Above the figures, deliberately. If the evidence is thin this
              screen says so before it says anything favourable. */}
          <EvidenceGaps gaps={gaps} title="What this window could not measure" />

          {/* Gated on the *window*, not on the trial. It was the trial, and
              that turned an organization an operator provisioned — no trial
              row, and never will have one — into a permanent "connect your
              books", which is a dead end for a book connected a year ago. Only
              the evaluation report legitimately has no window, and it says so
              in `empty_reason`; the screen shows that sentence rather than a
              headline made of blanks, because a row of "Not measured" tiles
              reads as a window that was measured and came to nothing. */}
          {!data.window ? (
            <EmptyState
              title="There is no window to measure"
              reason={data.empty_reason
                ?? "No intelligence trial is on record for this organization."} />
          ) : (
          <>
          {/* ── the headline ── */}
          <Paper variant="outlined" sx={{ p: 3 }}>
            <SectionHeader
              level="section"
              title="Attributed value"
              sub="The money moved and an intervention is on record as preceding it. This is the only figure on the screen that may be called what the platform was worth." />

            <Stack direction="row" spacing={2} useFlexGap
                   sx={{ flexWrap: "wrap", mb: 1 }}>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Attributed value"
                  value={<Amount value={data.attributed_value} />}
                  sub={`${counted(data.attributed_events ?? 0)} attributed event(s) · ${data.currency ?? "INR"}`}
                  tip="ATTRIBUTED events only. Potential and estimated figures are never added into this." />
              </Box>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Opportunities identified"
                  value={counted(data.potential_events ?? 0)}
                  sub="flagged during a live quote; nothing has happened yet"
                  tip="POTENTIAL events. An identified opportunity is not money, and the count is shown beside the realized one rather than as a share of it — the same line can appear in both." />
              </Box>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Opportunities realized"
                  value={counted(data.attributed_events ?? 0)}
                  sub="the money moved, with the intervention on record first"
                  tip="ATTRIBUTED events. Deliberately not expressed as a percentage of the identified count: the two sets overlap rather than nest, so a ratio would be a number the evidence does not support." />
              </Box>
            </Stack>

            <Stack direction="row" spacing={2} useFlexGap
                   sx={{ flexWrap: "wrap", mb: 2 }}>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Margin protected"
                  value={
                    <Amount
                      value={cellOf(breakdown, "MARGIN_PROTECTED", "ATTRIBUTED")?.amount}
                      unknown="Not measured" />
                  }
                  sub="below-floor lines flagged, repriced above the floor, and won"
                  tip={EVENT_TYPE.MARGIN_PROTECTED.what} />
              </Box>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Discount recovered"
                  value={
                    <Amount
                      value={cellOf(breakdown, "DISCOUNT_LEAKAGE_PREVENTED", "ATTRIBUTED")?.amount}
                      unknown="Not measured" />
                  }
                  sub="price a flagged line regained between snapshots"
                  tip={EVENT_TYPE.DISCOUNT_LEAKAGE_PREVENTED.what} />
              </Box>
            </Stack>

            <Verdict attributed={data.attributed_value}
                     events={data.attributed_events} />

            {types.length > 0 && (
              <Box sx={{ mt: 3 }}>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Attributed, by what was measured
                </Typography>
                <ClassBreakdown rows={breakdown} types={types}
                                unmeasurable={unmeasurable}
                                valueClass="ATTRIBUTED" />
              </Box>
            )}
          </Paper>

          {/* ── everything that is NOT money earned ── */}
          <Paper variant="outlined" sx={{ p: 3 }}>
            <SectionHeader
              level="section"
              title="Identified, observed and modelled — not money earned"
              sub="Kept apart from the headline on purpose. These are real figures about real lines, and none of them is a claim that the platform earned anything." />

            <Alert severity="info" icon={false} sx={{ mb: 2 }}>
              {data.class_totals_are_not_summable
                ?? "These classes describe overlapping facts about the same "
                 + "lines and must never be added together."}
            </Alert>

            <Stack direction="row" spacing={2} useFlexGap
                   sx={{ flexWrap: "wrap", mb: 3 }}>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Potential"
                  value={<Amount value={data.potential_value} unknown="None recorded" />}
                  sub={`${counted(data.potential_events ?? 0)} opportunity event(s)`}
                  tip={VALUE_CLASS.POTENTIAL.tip} />
              </Box>
              <Box sx={{ flex: "1 1 240px", minWidth: 240 }}>
                <MetricCard
                  label="Realized, not attributed"
                  value={<Amount value={data.realized_value} unknown="None recorded" />}
                  sub={`${counted(data.realized_events ?? 0)} event(s)`}
                  tip={VALUE_CLASS.REALIZED.tip} />
              </Box>
              {/* No "Estimated" tile. Nothing in the evidence produces that
                  class today, and a card reading "Estimated — None recorded"
                  states a measurement that was never attempted. The server
                  stopped sending the field for the same reason. */}
            </Stack>

            {types.length > 0 && (
              <>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Identified, by what was measured
                </Typography>
                <ClassBreakdown rows={breakdown} types={types}
                                unmeasurable={unmeasurable}
                                valueClass="POTENTIAL" />
              </>
            )}
          </Paper>

          {/* ── work done, counted and never valued ── */}
          {data.productivity && (
            <Paper variant="outlined" sx={{ p: 3 }}>
              <SectionHeader
                level="section"
                title="Work the platform did"
                sub={data.productivity.note} />
              <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
                <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
                  <MetricCard label="Quotes priced"
                              value={counted(data.productivity.quotes_priced)} />
                </Box>
                <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
                  <MetricCard label="Lines priced"
                              value={counted(data.productivity.lines_priced)} />
                </Box>
                <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
                  <MetricCard label="Approvals turned round"
                              value={counted(data.productivity.approvals_turned_round)} />
                </Box>
              </Stack>
            </Paper>
          )}
          </>
          )}
        </>
      )}

      {/* ── the ledger ── */}
      <Paper variant="outlined" sx={{ p: 3 }}>
        <SectionHeader
          level="section"
          title="The ledger"
          sub="Every event behind the figures above, each one opening onto the operands it was computed from and the records those came from." />

        {ledger.error ? (
          <ErrorState title="The ledger could not be read" error={ledger.error}
                      onRetry={ledger.reload} />
        ) : ledger.loading || !events ? (
          <LoadingState rows={4} height={44} label="Reading the ledger…" />
        ) : (
          <>
            {events.frozen_reason && (
              /* The plan lapsed, so the ledger stops at the end of the window
                 this organization was entitled to. Said plainly and *above* the
                 rows, because a reader who scrolls a short ledger without being
                 told it is capped concludes PIE stopped finding things — which
                 is the benign default this whole screen refuses everywhere
                 else. Detection did not stop; reading past that point is what
                 the plan restores. */
              <Alert severity="warning" sx={{ mb: 2 }}>
                <AlertTitle>This ledger stops at the end of your trial</AlertTitle>
                {events.frozen_reason}
              </Alert>
            )}
            <Alert severity="info" icon={false} sx={{ mb: 2 }}>
              {events.page_is_not_a_total}
            </Alert>
            <DataGrid<ValueEventRow>
              ariaLabel="Value events, newest business fact first"
              rows={events.events}
              columns={columns}
              getRowId={(r) => r.value_event_id}
              onRowClick={(r) => setOpen(r)}
              onRowActivate={(r) => setOpen(r)}
              pageSize={25}
              empty={
                <EmptyState
                  title="No value event is recorded"
                  reason={events.empty_reason
                    ?? "An empty ledger is not a measured zero — check the evidence gaps above for whether detection has run at all."} />
              }
              renderNarrow={(r) => {
                const cls = classOf(r.value_class);
                return (
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Stack direction="row" spacing={1}
                           sx={{ mb: 0.5, alignItems: "center", flexWrap: "wrap" }}>
                      <StatusChip label={cls.label} tone={cls.tone} dense />
                      <Typography variant="caption" color="text.secondary">
                        {formatDate(r.occurred_at)}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {eventTypeLabel(r.event_type)}
                    </Typography>
                    <Typography variant="body2">
                      <Amount value={r.amount} unknown="no amount" />
                    </Typography>
                    <Typography variant="caption" color="text.secondary"
                                sx={{ display: "block", mb: 1 }}>
                      {String(r.basis?.formula ?? "no basis recorded")}
                    </Typography>
                    {/* A named control rather than a tap anywhere on the card:
                        a clickable `Paper` is invisible to a keyboard and
                        announces nothing, and the drill-down is the whole
                        reason this row is worth opening. */}
                    <Button size="small" variant="outlined" sx={TOUCH}
                            onClick={() => setOpen(r)}>
                      Show its basis
                    </Button>
                  </Paper>
                );
              }}
            />
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: "block", mt: 1 }}>
              {events.has_more
                ? `Showing the ${counted(events.events.length)} most recent of `
                  + `${counted(events.total)} event(s).`
                : `${counted(events.total)} event(s) recorded.`}
              {" "}Open a row for its basis and its evidence. This is a list of
              rows, not a total — the headline is above.
            </Typography>
          </>
        )}
      </Paper>

      {/* ── the 30-day report, then the span it cannot cover ── */}
      {mayReadReport ? (
        <>
          <EvaluationPanel session={session} />
          <RollupPanel session={session} />
        </>
      ) : (
        <Paper variant="outlined" sx={{ p: 3 }}>
          <SectionHeader
            level="section"
            title="The 30-day report"
            sub="What the platform was worth against what it costs, set beside how the business ran before it." />
          <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "80ch" }}>
            Shown to the owner only. It compares one book&rsquo;s performance
            before the platform against its performance during, and the cost
            side of it is a figure only the owner holds — so the server answers
            it for that role alone. The ledger above is the evidence it is built
            from, and none of it is withheld from you.
          </Typography>
        </Paper>
      )}

      <EventDrilldown row={open} onClose={() => setOpen(null)} />
    </Stack>
  );
}

/** One (event type × class) cell, by lookup. Never a sum — see
 *  `ClassBreakdown`. */
function cellOf(rows: ValueClassBreakdown[], type: string, valueClass: string) {
  return rows.find((r) => r.event_type === type && r.value_class === valueClass);
}

/* ── the owner's report ───────────────────────────────────────────────────── */

/**
 * Trial progress against the pre-trial baseline, and the return on what the
 * platform costs.
 *
 * The cost is typed in because this platform holds no price for its own plans.
 * That is the honest arrangement and it is also the safe one: a default would
 * put a return figure nobody entered on the screen a renewal is signed against.
 * Until a cost is supplied the multiple is UNKNOWN — never 0x, and never a dash
 * that could be read as "no return".
 */
function EvaluationPanel({ session }: { session: PlatformSession }) {
  // What has been asked of the server, and what is in the field. Separate, so
  // a refetch happens when the owner submits rather than on every keystroke.
  const [cost, setCost] = useState("");
  const [applied, setApplied] = useState<string | null>(null);

  const { data: report, loading, error, reload } = useInsight<AttributionEvaluation>(
    "attribution-evaluation",
    () => papi.attributionEvaluation(session.token, applied),
    [session.token, applied]);

  // Checked before it is sent, and against the server's own rule (`ge=0`).
  // Not defensive tidiness: an unparseable cost would come back as a 422 and
  // put "the report could not be read" where a mistyped figure belongs, which
  // reads as the report being unavailable rather than as a typo.
  const typed = cost.trim();
  const costInvalid = typed !== ""
    && !(Number.isFinite(Number(typed)) && Number(typed) >= 0);

  const submit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = cost.trim();
    if (trimmed !== ""
        && !(Number.isFinite(Number(trimmed)) && Number(trimmed) >= 0)) return;
    setApplied(trimmed === "" ? null : trimmed);
  }, [cost]);

  const attributed = amountOf(report?.attributed_value ?? null);
  const costValue = amountOf(applied);
  // The only arithmetic on this screen, and it is here rather than on the
  // server for a reason worth stating: the platform holds no price for its own
  // plans, so there is no server-side operand to compute against — the cost is
  // the reader's own figure, entered a moment ago. Both operands are on the
  // screen beside the result. Anything else here would be a second opinion
  // about a number `attribution/` already computed.
  const net = attributed !== null && costValue !== null
    ? attributed - costValue : null;

  return (
    <Paper variant="outlined" sx={{ p: 3 }}>
      <SectionHeader
        level="section"
        title="The 30-day report"
        sub="What the platform was worth against what it costs, and how this book ran in the 90 days before the trial started." />

      <Box component="form" onSubmit={submit} sx={{ mb: 3 }}>
        <Stack direction="row" spacing={1.5} useFlexGap
               sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
          <TextField
            size="small"
            label="What PIE costs you for this window"
            value={cost}
            onChange={(e) => setCost(e.target.value)}
            inputMode="decimal"
            error={costInvalid}
            sx={{ minWidth: 280 }}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">{moneySymbol()}</InputAdornment>
                ),
              },
            }}
            helperText={costInvalid
              ? "A number, and not a negative one."
              : "This platform holds no price for its own plans, so the return is UNKNOWN until you enter one. Nothing is stored."} />
          <Button type="submit" variant="outlined" disabled={costInvalid}
                  sx={{ mt: 0.25 }}>
            {applied === null ? "Show the return" : "Update"}
          </Button>
        </Stack>
      </Box>

      {error ? (
        <ErrorState title="The report could not be read" error={error}
                    onRetry={reload} />
      ) : loading || !report ? (
        <LoadingState rows={2} height={110} label="Building the report…" />
      ) : (
        <>
          <Stack direction="row" spacing={2} useFlexGap
                 sx={{ flexWrap: "wrap", mb: 3 }}>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="Attributed value"
                value={<Amount value={report.attributed_value} />}
                sub="ATTRIBUTED events only" />
            </Box>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="What PIE costs you"
                value={costValue === null
                  ? <UnknownValue>Not supplied</UnknownValue>
                  : money(costValue)}
                sub="your figure, for this window" />
            </Box>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="Net value"
                value={net === null
                  ? <UnknownValue>Unknown</UnknownValue>
                  : money(net)}
                sub={net === null
                  ? "needs both an attributed figure and a cost"
                  : "attributed value less the cost you entered"}
                tip="Attributed value minus the cost above. Unknown while either side is unknown — a missing cost does not make the net equal to the value." />
            </Box>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="Value per rupee of cost"
                value={report.roi_is_unknown || report.roi === null
                  ? <UnknownValue>Unknown</UnknownValue>
                  : `${Number(report.roi).toFixed(2)}×`}
                sub={report.roi_is_unknown
                  ? "no cost supplied, or no attributed value is measurable"
                  : "attributed value ÷ the cost you entered"}
                tip="Computed on the server from the cost you supplied. Unknown is rendered as unknown — a return of 0x would be a claim, and nobody has supplied the evidence for one." />
            </Box>
          </Stack>

          {/* The honest verdict on the value itself is stated once, at the
              headline, and deliberately not repeated here: two alerts saying
              the same paragraph on one page is how a page teaches people to
              scroll past both. What this panel owes on top of it is the *cost*
              side, and the tiles above say UNKNOWN where it is unknown. */}

          <Box sx={{ mt: 3 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Before the trial, and during it
            </Typography>
            {report.baseline === null ? (
              <Alert severity="warning">
                <AlertTitle>There is nothing to compare against</AlertTitle>
                No baseline was captured for this trial, so nothing here is set
                beside how the business ran before. A comparison drawn without
                one would be a figure with one side missing.
              </Alert>
            ) : report.comparison === null ? (
              <Alert severity="warning">
                <AlertTitle>The comparison is not available</AlertTitle>
                A baseline exists for{" "}
                {formatDate(report.baseline.window_start)} –{" "}
                {formatDate(report.baseline.window_end)}, but quoted margin is
                missing on one side of it, so no movement is stated. A one-sided
                figure is not an improvement.
              </Alert>
            ) : (
              <Box component="table" className="facttable" sx={{ width: "100%" }}>
                <tbody>
                  <tr>
                    <td>
                      Quoted margin before
                      <div className="fsrc">
                        {formatDate(report.baseline.window_start)} –{" "}
                        {formatDate(report.baseline.window_end)}
                      </div>
                    </td>
                    <td className="fv">{pct(report.comparison.quoted_margin_before)}</td>
                  </tr>
                  <tr>
                    <td>Quoted margin during the trial</td>
                    <td className="fv">{pct(report.comparison.quoted_margin_after)}</td>
                  </tr>
                  <tr>
                    <td>
                      Movement
                      <div className="fsrc">
                        Percentage points, not percent — 24% to 20% is −4 pp.
                      </div>
                    </td>
                    <td className="fv">
                      {/* The server sends this already in percentage points;
                          `pp` takes a ratio, so it is divided back. The shared
                          formatter rather than a fourth private one — three
                          copies of it existed before it was moved to
                          `useInsight`, and they had drifted. */}
                      {pp(report.comparison.quoted_margin_movement_pp / 100)}
                    </td>
                  </tr>
                </tbody>
              </Box>
            )}
          </Box>

          {report.during && (
            <Box sx={{ mt: 3 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                The window itself
              </Typography>
              <Box component="table" className="facttable" sx={{ width: "100%" }}>
                <tbody>
                  <tr>
                    <td>Lines priced through the platform</td>
                    <td className="fv">{counted(report.during.priced_lines)}</td>
                  </tr>
                  <tr>
                    <td>
                      Of those, carrying a purchase cost
                      <div className="fsrc">
                        A line with no cost is excluded from the margin above
                        rather than counted as a zero-margin one.
                      </div>
                    </td>
                    <td className="fv">
                      {counted(report.during.costed_lines)}
                      {report.during.uncostable_lines > 0
                        && ` · ${counted(report.during.uncostable_lines)} without`}
                    </td>
                  </tr>
                  <tr>
                    <td>Lines that needed approval</td>
                    <td className="fv">
                      {counted(report.during.approval_required_lines)}
                      {report.during.approval_required_rate !== null
                        && ` · ${pct(report.during.approval_required_rate)} of costed lines`}
                    </td>
                  </tr>
                  <tr>
                    <td>Quotes decided</td>
                    <td className="fv">
                      {report.during.quotes_decided === 0
                        ? "none decided in this window"
                        : `${counted(report.during.quotes_won)} won · `
                          + `${counted(report.during.quotes_lost)} lost · `
                          + `${pct(report.during.quote_win_rate)} won`}
                    </td>
                  </tr>
                </tbody>
              </Box>
            </Box>
          )}

          {report.baseline && report.baseline.evidence_gaps.length > 0 && (
            <Box sx={{ mt: 3 }}>
              <EvidenceGaps
                gaps={report.baseline.evidence_gaps}
                title="What the pre-trial baseline could not measure" />
            </Box>
          )}
        </>
      )}
    </Paper>
  );
}

/** The spans an owner can ask for. Bounded by the server's own `le=36`, and
 *  offered as choices rather than as a free number because every one of them
 *  has to mean a whole number of billed months for the ratio below to be
 *  comparable to anything. */
const ROLLUP_SPANS = [3, 6, 12, 24] as const;

/** How one month reads in the series, and the two `null`s it must keep apart.
 *
 *  A month with nothing on record is UNKNOWN and says so in words; a month that
 *  was measured and attributed nothing is a real ₹0 and is printed as one.
 *  Collapsing them into a dash is the whole failure this ledger is written
 *  against, and it is a one-line temptation in a table renderer.
 */
function PeriodAmount({ row }: { row: AttributionPeriod }) {
  if (!row.measured) {
    return <UnknownValue>Not measured</UnknownValue>;
  }
  return <>{<Amount value={row.attributed_value} />}</>;
}

/** Value month by month, and the return over the span. Owner only.
 *
 *  `EvaluationPanel` above answers this for the trial and cannot answer it
 *  afterwards, so the one return figure this platform states used to disappear
 *  on the day a trial ended — leaving the owner deciding whether to keep paying
 *  in month fourteen with strictly less evidence than the one deciding in month
 *  one. This panel is that figure over a span they choose.
 *
 *  Two things here are deliberately unhelpful, and both are the server's rules
 *  rendered rather than softened. The month **in progress** is drawn under the
 *  table with its own caption and never inside the total, because a month of
 *  cost is not comparable to seventeen days of value. And a span containing a
 *  month with nothing on record shows **no return at all** — not a smaller one.
 *  A numerator covering eight months over a denominator covering twelve errs
 *  low, which is exactly why a screen would wave it through.
 */
function RollupPanel({ session }: { session: PlatformSession }) {
  const [months, setMonths] = useState<number>(12);
  const [cost, setCost] = useState("");
  const [applied, setApplied] = useState<string | null>(null);

  const { data, loading, error, reload } = useInsight<AttributionRollup>(
    "attribution-rollup",
    () => papi.attributionRollup(session.token,
                                 { months, monthlyCost: applied }),
    [session.token, months, applied]);

  // The same guard `EvaluationPanel` applies, and for the same reason: a
  // mistyped cost would come back a 422 and put "the roll-up could not be read"
  // where a typo belongs.
  const typed = cost.trim();
  const costInvalid = typed !== ""
    && !(Number.isFinite(Number(typed)) && Number(typed) >= 0);

  const submit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = cost.trim();
    if (trimmed !== ""
        && !(Number.isFinite(Number(trimmed)) && Number(trimmed) >= 0)) return;
    setApplied(trimmed === "" ? null : trimmed);
  }, [cost]);

  const columns = useMemo<ColDef<AttributionPeriod>[]>(() => [
    { field: "label", headerName: "Month", width: 140, flex: 0 },
    {
      field: "attributed_value", headerName: "Attributed", width: 170, flex: 0,
      type: "rightAligned",
      cellRenderer: (p: { data?: AttributionPeriod }) =>
        p.data ? <PeriodAmount row={p.data} /> : null,
    },
    { field: "attributed_events", headerName: "Events", width: 110, flex: 0,
      type: "rightAligned" },
    {
      field: "measured", headerName: "Evidence", width: 190, flex: 1,
      cellRenderer: (p: { data?: AttributionPeriod }) => {
        if (!p.data) return null;
        if (!p.data.measured) {
          return <StatusChip label="No detection on record" tone="warn" />;
        }
        if (p.data.amounts_missing > 0) {
          return (
            <StatusChip
              label={`${counted(p.data.amounts_missing)} without an amount`}
              tone="warn" />
          );
        }
        return <StatusChip label="Measured" tone="good" />;
      },
    },
  ], []);

  return (
    <Paper variant="outlined" sx={{ p: 3 }}>
      <SectionHeader
        level="section"
        title="Month by month"
        sub="What the platform has been worth over a longer span, and the return on it — the figure the 30-day report stops giving once a trial ends." />

      <Box component="form" onSubmit={submit} sx={{ mb: 3 }}>
        <Stack direction="row" spacing={1.5} useFlexGap
               sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
          <TextField
            select
            size="small"
            label="Span"
            value={String(months)}
            onChange={(e) => setMonths(Number(e.target.value))}
            sx={{ minWidth: 160 }}
            slotProps={{ select: { native: true } }}
            helperText="Complete calendar months only.">
            {ROLLUP_SPANS.map((n) => (
              <option key={n} value={n}>{n} months</option>
            ))}
          </TextField>
          <TextField
            size="small"
            label="What PIE costs you per month"
            value={cost}
            onChange={(e) => setCost(e.target.value)}
            inputMode="decimal"
            error={costInvalid}
            sx={{ minWidth: 300 }}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">{moneySymbol()}</InputAdornment>
                ),
              },
            }}
            helperText={costInvalid
              ? "A number, and not a negative one."
              : "A monthly rate, not a total — the span is many months. Nothing is stored."} />
          <Button type="submit" variant="outlined" disabled={costInvalid}
                  sx={{ mt: 0.25 }}>
            {applied === null ? "Show the return" : "Update"}
          </Button>
        </Stack>
      </Box>

      {error ? (
        <ErrorState title="The roll-up could not be read" error={error}
                    onRetry={reload} />
      ) : loading || !data ? (
        <LoadingState rows={2} height={110} label="Rolling up the months…" />
      ) : (
        <>
          <Stack direction="row" spacing={2} useFlexGap
                 sx={{ flexWrap: "wrap", mb: 3 }}>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="Attributed value"
                value={<Amount value={data.attributed_value} />}
                sub={data.span.label
                  ? `${data.span.label} · complete months only`
                  : "no complete month in this span"} />
            </Box>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="What PIE cost you"
                value={data.platform_cost === null
                  ? <UnknownValue>Not supplied</UnknownValue>
                  : <Amount value={data.platform_cost} />}
                sub={data.monthly_cost === null
                  ? "your monthly figure, for this span"
                  : `your rate × ${counted(data.span.complete_months)} complete month(s)`} />
            </Box>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="Value per rupee of cost"
                value={data.roi_is_unknown || data.roi === null
                  ? <UnknownValue>Unknown</UnknownValue>
                  : `${Number(data.roi).toFixed(2)}×`}
                sub={data.roi_is_unknown
                  ? "no monthly cost supplied, or a month in this span was never measured"
                  : "attributed value ÷ the cost above"}
                tip="Refused outright where any complete month in the span has nothing on record. The value would cover fewer months than the cost does, and the ratio would read low — which is still a number nobody measured." />
            </Box>
            <Box sx={{ flex: "1 1 220px", minWidth: 220 }}>
              <MetricCard
                label="Months measured"
                value={`${counted(data.span.measured_months)} of ${counted(data.span.complete_months)}`}
                sub="complete months with any event on record"
                tip="A month with no event is not a month worth nothing. It is a month this platform cannot speak for, and the return is withheld while one is in the span." />
            </Box>
          </Stack>

          <EvidenceGaps gaps={data.evidence_gaps} title="What is not measured" />

          {data.span.frozen_at ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              <AlertTitle>This span stops where your entitlement does</AlertTitle>
              The roll-up reaches to{" "}
              {formatDateTime(data.span.frozen_at)} and no further, because this
              organization is on the free Quote Desk. Detection has kept running;
              reading past that point is what the plan restores.
            </Alert>
          ) : null}

          {data.empty_reason ? (
            <Alert severity="warning" sx={{ mb: 2 }}>
              <AlertTitle>There is no span to roll up yet</AlertTitle>
              {data.empty_reason}
            </Alert>
          ) : (
            <Box sx={{ mt: 2 }}>
              <DataGrid<AttributionPeriod>
                ariaLabel="Attributed value by calendar month, oldest first"
                rows={data.periods}
                columns={columns}
                getRowId={(r) => r.period}
                pageSize={12} />
            </Box>
          )}

          {data.in_progress ? (
            <Box sx={{ mt: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                {data.in_progress.label} — still running
              </Typography>
              <Typography variant="body2" color="text.secondary"
                          sx={{ maxWidth: "80ch" }}>
                <PeriodAmount row={data.in_progress} /> attributed so far, from{" "}
                {counted(data.in_progress.attributed_events)} event(s). This
                month is <strong>not</strong> in the total or the return above.
                A month of subscription buys a whole month; part of one is not
                comparable to it, and including it would move the ratio every
                time this page was opened.
              </Typography>
            </Box>
          ) : null}
        </>
      )}
    </Paper>
  );
}


/** UNKNOWN, in the value slot of a tile, at a size that does not pretend to be
 *  a figure. A word rather than a dash, for the reason `Amount` gives. */
function UnknownValue({ children }: { children: React.ReactNode }) {
  return (
    <Box component="span" sx={{ color: "text.secondary", fontSize: "0.6em" }}>
      {children}
    </Box>
  );
}
