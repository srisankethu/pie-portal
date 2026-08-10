// What this book leans on, at both ends, facing each other.
//
// A distributor is exposed in two directions and they are almost never looked
// at together. Lose a principal and the customers buying their product are
// exposed with you; lose the customer who takes most of a principal's line and
// the target goes too. One screen, one encoding, mirrored — so the two halves
// are comparable at a glance rather than being two screens somebody remembers
// to open in sequence.
//
// **A principal's weight is the revenue riding on them, not the spend.** Both
// bars are drawn, and the gap between them is the finding: "12% of what we buy,
// 34% of what we sell" is the sentence somebody leaves the screen with. Bars
// rather than a pie for the usual reason — a pie asks a reader to compare
// angles, and the whole question here is "how much bigger is this one".
//
// **Targets sit on the vendor row, with pace beside achievement.** 60% of a
// number with 80% of the quarter gone is behind, and an achievement figure on
// its own hides that until the last week. Both are drawn on one track.
//
// **There is a third end, and it is the same names weighed differently.** What
// a customer is worth and what they still owe are two facts, and they come
// apart exactly where it matters: the customer who buys the most and pays on
// the day is a big share of revenue and a small share of what we are waiting
// on. The two top-five shares are therefore drawn beside each other at the top
// of the screen, because the *gap* is the finding — the same reason the vendor
// row draws spend and downstream revenue on one track. Neither is a proxy for
// the other and the screen never prints one as the other.
//
// The screen never says a customer depends on us — see the server's own
// `unavailable`, rendered at the bottom. We see what they buy here and nothing
// of what they buy elsewhere.

import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useState, type ReactNode } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { EmptyState, InlineLink, MetricCard, StatusChip, Unavailable } from "../kit";
import { CompanyScope } from "../CompanyFilter";
import type { CompanyScopeOption } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { EntityOrigin, PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";
import { TargetEditor } from "./Targets";
import { BookFlow } from "./BookFlow";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

export function DependencyScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  // Server-side, because this screen's output is shares of a total: a row
  // filter would put one company's list under three companies' arithmetic.
  const [scope, setScope] = useState("");
  const { data, loading, error, reload } = useInsight(
    "dependency", () => papi.dependency(session.token, scope || undefined),
    [session.token, scope]);

  const customers = (data?.customers as Row | undefined) ?? {};
  const receivables = (data?.receivables as Row | undefined) ?? {};
  const vendors = (data?.vendors as Row | null | undefined) ?? null;
  const supplierSide = Boolean(data?.supplier_side_visible) && vendors !== null;
  const attribution = (data?.attribution as Row | undefined) ?? {};
  const sourcesDiffer = Boolean(data?.sources_differ);

  const [editing, setEditing] = useState(false);

  const customerRows = rows(customers.rows);
  const receivableRows = rows(receivables.rows);
  const vendorRows = rows(vendors?.rows);
  const companies = rows(data?.companies);

  const attributed = attribution.share as number | null | undefined;

  return (
    <Panel
      title="Dependency"
      question="What this book leans on — at both ends"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={supplierSide ? (
        <Button type="button" size="small" variant="outlined"
                onClick={() => setEditing(true)}>
          Targets
        </Button>
      ) : undefined}
    >
      <CompanyScope options={companies as unknown as CompanyScopeOption[]}
                    value={scope} onChange={setScope} />

      {/* How much of revenue could be traced to a principal at all. A share of
          a fraction of the book, shown without saying so, is the single most
          misleading thing this screen could do. */}
      {supplierSide && attributed != null && attributed < 0.99 && (
        <p className="bond-unscored">
          <strong>{pct(attributed, 0)}</strong> of revenue could be traced to a
          principal ({money(num(attribution.revenue_total) - num(attribution.revenue_attributed))}{" "}
          could not). A sale is attributed through the bills that bought the
          item, so this fills in after a full sync — until then the supplier
          shares below are computed over the traced part only.
        </p>
      )}

      {/* The two top-five shares, adjacent, because the gap between them is
          the finding and neither may be read as the other. */}
      <TopFive customers={(customers.concentration as Row) ?? {}}
               receivables={(receivables.concentration as Row) ?? {}}
               daysToPay={(receivables.days_to_pay as Row) ?? {}} />

      {/* The picture first. The two lists below answer "how exposed are we to
          this name"; this answers "what does the business look like", which is
          the question somebody opens the screen with. */}
      {supplierSide && data?.flow != null && (
        <BookFlow flow={data.flow as Row} />
      )}

      <div className="dep-halves">
        {supplierSide && (
          <Side
            title="Suppliers we lean on"
            question="Spend, and the revenue riding on their product"
            rows={vendorRows}
            concentration={(vendors?.concentration as Row) ?? {}}
            sourcesDiffer={sourcesDiffer}
            side="vendor"
            onOpen={() => undefined}
          />
        )}
        <Side
          title="Customers we lean on"
          question="Revenue, and how many principals their spend runs through"
          rows={customerRows}
          concentration={(customers.concentration as Row) ?? {}}
          sourcesDiffer={sourcesDiffer}
          side="customer"
          onOpen={(id) => onNavigate(`customer/${id}`)}
        />
        <Side
          title="Customers we are waiting on"
          question="What is still owed, and how long that money has been out"
          rows={receivableRows}
          concentration={(receivables.concentration as Row) ?? {}}
          sourcesDiffer={sourcesDiffer}
          side="receivable"
          onOpen={(id) => onNavigate(`customer/${id}`)}
          // An unfolded state and a fully-collected book both have no rows.
          // The server says which, because rendered the same way the first
          // would read as "nobody owes us anything".
          emptyReason={receivables.empty_reason as string | undefined}
        >
          <DaysToPay figure={(receivables.days_to_pay as Row) ?? {}} />
        </Side>
      </div>

      <Unavailable items={rows(data?.unavailable)} verb="not claimed" />

      {editing && (
        <TargetEditor session={session} onClose={() => setEditing(false)}
                      onSaved={reload} />
      )}
    </Panel>
  );
}

/** The two top-five shares, side by side.
 *
 *  They answer different questions about the same names and they diverge — a
 *  large customer who settles on the day is most of what we sell and little of
 *  what we are waiting on. Drawn adjacent so a reader compares them rather than
 *  carrying one figure to the other end of the screen; the caption says they
 *  are two facts, because the failure mode here is reading one as a stand-in
 *  for the other. */
function TopFive({
  customers, receivables, daysToPay,
}: { customers: Row; receivables: Row; daysToPay: Row }) {
  if (customers.top_n_share == null && receivables.top_n_share == null) return null;
  const weighted = daysToPay.weighted_days as number | null | undefined;
  return (
    <>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mt: 2 }}>
        {/* The count only appears beside a share that exists. A "0 customers"
            caption under a "—" would read as a measured emptiness, and for the
            receivables half the far more likely cause is a fold that has never
            run — which is not the same claim at all. */}
        <MetricCard
          label="Largest five, by revenue"
          value={pct(customers.top_n_share as number | null, 0)}
          sub={customers.top_n_share != null
            ? `of ${num(customers.count)} customers who bought`
            : undefined}
          tip="Their combined share of what this book sold over the window."
        />
        <MetricCard
          label="Largest five, by what is owed"
          value={pct(receivables.top_n_share as number | null, 0)}
          sub={receivables.top_n_share != null
            ? `of ${num(receivables.count)} customers who owe anything`
            : undefined}
          tip="Their combined share of the outstanding book — every invoice still unpaid, due or not."
        />
        <MetricCard
          label="Those five take"
          // Never a bare number: "—" is what an unmeasured set renders as, and
          // the sentence under it says which.
          value={weighted == null ? "—" : `${weighted} days`}
          sub="weighted by what each of them owes"
          tip="Average days from invoice to settlement, weighted by outstanding balance. Not days sales outstanding — that is a ratio against a revenue window this fold does not carry."
        />
      </Stack>
      <Typography variant="caption" color="text.secondary"
                  component="p" sx={{ mt: 1 }}>
        Two different facts about the same names, not one measured twice. They
        come apart when a large customer pays promptly, and neither stands in
        for the other.
      </Typography>
    </>
  );
}

/** The weighted figure, and how much of the top five it actually spans.
 *
 *  The coverage never leaves the number's side. A weighted average over one of
 *  five accounts is true of that account and says nothing about the other four,
 *  and a bare "18 days" would be read as saying something about all of them. */
function DaysToPay({ figure }: { figure: Row }) {
  const weighted = figure.weighted_days as number | null | undefined;
  const covers = figure.covers_share as number | null | undefined;
  const unmeasured = rows(figure.unmeasured);
  const floor = num(figure.min_settlements);

  if (weighted == null) {
    return (
      <p className="viz-muted">
        How long that money has been out is not measured: none of the largest
        five has {floor} settled invoices with terms on record. Fewer than that
        is one payment wearing a suit, so no figure is stated.
      </p>
    );
  }
  return (
    <p className="viz-muted">
      <strong>{weighted} days</strong> from invoice to settlement, weighted by
      what each of them owes.
      {covers != null && covers < 1 && (
        <>
          {" "}
          <StatusChip label={`covers ${pct(covers, 0)} of their balance`}
                      tone="warn" dense
                      tip="The rest of the top five have too few settled invoices to measure, so the figure says nothing about them." />
          {" "}Not measured: {unmeasured.map((u) => String(u.label)).join(", ")}.
        </>
      )}
    </p>
  );
}

function Side({
  title, question, rows: list, concentration, sourcesDiffer, side, onOpen,
  children, emptyReason,
}: {
  title: string;
  question: string;
  rows: Row[];
  concentration: Row;
  sourcesDiffer: boolean;
  side: "vendor" | "customer" | "receivable";
  onOpen: (id: string) => void;
  children?: ReactNode;
  /** Why this half has no rows, when that is a claim rather than a blank. */
  emptyReason?: string;
}) {
  const isVendor = side === "vendor";
  const isOwed = side === "receivable";
  // Bars are drawn against the largest row *in view*, which is a comparison
  // between the rows on screen and not a claim about the book. The share
  // figures beside them are the server's, computed over the whole total — the
  // rule `CompanyFilter` states: narrowing a list hides rows, it does not
  // restate a total.
  const widest = Math.max(
    ...list.map((r) => (isVendor ? num(r.downstream_revenue) : num(r.money))), 1);

  return (
    <section className="dep-side">
      <h4>{title}</h4>
      <p className="viz-question">{question}</p>

      {concentration.top_share != null && (
        <p className="viz-headline">
          <strong>{String(concentration.top_label)}</strong> is{" "}
          {pct(num(concentration.top_share), 0)}
          {" "}· the largest five are {pct(num(concentration.top_n_share), 0)} of{" "}
          {num(concentration.count)}.
        </p>
      )}

      {list.length === 0 && emptyReason
        ? <EmptyState title="Nothing to measure" reason={emptyReason} />
        : children}

      <ul className="dep-rows">
        {list.slice(0, 12).map((r) => {
          const money_ = isVendor ? num(r.downstream_revenue) : num(r.money);
          const target = r.target as Row | null | undefined;
          return (
            <li key={String(r.entity_id)} className="dep-row">
              <span className="dep-name">
                <EntityName name={String(r.label)}
                            origin={r.origin as EntityOrigin | undefined}
                            show={sourcesDiffer} strong={false} />
                {num(r.sole_source_items) > 0 && (
                  <StatusChip label={`only source for ${num(r.sole_source_items)}`}
                              tone="warn" dense
                              tip="No other supplier has ever sold us these items. That is not the same as no other supplier existing." />
                )}
              </span>

              <span className="dep-track">
                <span className="dep-fill"
                      style={{ width: `${(money_ / widest) * 100}%` }} />
                {/* The second bar, vendor side only: what they cost against
                    what rides on them. Two bars on one track rather than two
                    columns of numbers, because the gap is the point. */}
                {isVendor && (
                  <span className="dep-fill dep-spend"
                        style={{ width: `${(num(r.money) / widest) * 100}%` }} />
                )}
              </span>

              <span className="dep-figures">
                <strong>{money(money_)}</strong>
                <span className="viz-muted">
                  {isVendor
                    ? `${pct(r.downstream_share as number, 0)} of revenue · ${pct(r.share as number, 0)} of spend`
                    : isOwed
                      // "not measured" rather than a dash on its own: a blank
                      // beside a number reads as zero days, which is the one
                      // thing this row must not say.
                      ? `${pct(r.share as number, 0)} of what is owed · ${
                          r.days_to_pay == null
                            ? "days-to-pay not measured"
                            : `${num(r.days_to_pay)} days to pay`}`
                      : `${pct(r.share as number, 0)} of revenue · ${num(r.counterparties)} principal${num(r.counterparties) === 1 ? "" : "s"}`}
                </span>
              </span>

              {target && <TargetTrack target={target} />}

              {!isVendor && (
                <InlineLink onClick={() => onOpen(String(r.entity_id))}>
                  Open
                </InlineLink>
              )}
            </li>
          );
        })}
      </ul>

      {list.length > 12 && (
        <div className="tier3-list">
          <DataGrid<Row>
            ariaLabel={title}
            rows={list}
            pageSize={15}
            twoLineRows
            getRowId={(r) => String(r.entity_id)}
            onRowClick={isVendor ? undefined
                                 : (r) => onOpen(String(r.entity_id))}
            columns={[
              {
                field: "label", headerName: isVendor ? "Supplier" : "Customer",
                flex: 1, minWidth: 200, filter: "agTextColumnFilter",
                cellRenderer: (p: { data: Row }) => (
                  <EntityName name={String(p.data.label)}
                              origin={p.data.origin as EntityOrigin | undefined}
                              show={sourcesDiffer} strong={false} />
                ),
              },
              ...(isVendor
                ? [numeric<Row>("downstream_revenue", "Revenue on them",
                                (v) => money(v), { width: 170, flex: 0 }),
                   numeric<Row>("money", "Spend", (v) => money(v),
                                { width: 140, flex: 0 })]
                : [numeric<Row>("money", isOwed ? "Outstanding" : "Revenue",
                                (v) => money(v), { width: 150, flex: 0 })]),
              // Null below the settlement floor, which `numeric` renders as
              // "—" rather than as a zero-day payer.
              ...(isOwed
                ? [numeric<Row>("days_to_pay", "Days to pay",
                                (v) => String(v), { width: 140, flex: 0 })]
                : [numeric<Row>("counterparties",
                                isVendor ? "Customers" : "Principals",
                                (v) => String(v), { width: 130, flex: 0 })]),
            ]}
            empty={<p className="viz-muted">Nothing here yet.</p>}
          />
        </div>
      )}
    </section>
  );
}

/** Achievement and elapsed period on one track.
 *
 *  Two marks, not one: an achievement bar alone says 60% and sounds fine, and
 *  the thing that decides whether it is fine is how much of the quarter is
 *  gone. The pace marker is where the bar would have to reach to be on time. */
function TargetTrack({ target }: { target: Row }) {
  const achieved = num(target.achieved);
  const elapsed = num(target.period_elapsed);
  const onPace = target.on_pace === true;
  return (
    <span className="dep-target">
      <span className="dep-target-track">
        <span className={`dep-target-fill${onPace ? " ok" : " behind"}`}
              style={{ width: `${Math.min(100, achieved * 100)}%` }} />
        <span className="dep-target-pace"
              style={{ left: `${Math.min(100, elapsed * 100)}%` }}
              title={`${pct(elapsed, 0)} of the period gone`} />
      </span>
      <span className="viz-muted">
        {pct(achieved, 0)} of {money(num(target.amount))}{" "}
        {String(target.basis_label)} · {pct(elapsed, 0)} of the period gone
        {num(target.days_left) > 0 && target.required_run_rate != null && (
          <> · {money(num(target.required_run_rate))}/day to close it</>
        )}
        {" ("}
        {formatDate(String(target.period_start))}–{formatDate(String(target.period_end))}
        {")"}
      </span>
    </span>
  );
}

