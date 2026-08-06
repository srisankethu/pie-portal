// The two remaining Tier 1 views: one customer's history, and who changed size.
//
// Neither gets a nav entry, and that is the design rather than an omission.
// A health timeline is *about one customer*, so it belongs on that customer's
// page, next to the sentence that already promises "trading facts and what we
// read from them" — a standalone screen would have needed a customer picker
// duplicating the account list two clicks away. The migration matrix answers
// the same question as the journey chart beside it ("what happened to the
// customer base"), one period-over-period rather than month-by-month, so it
// sits under it. A sixteenth top-level nav item would have bought nothing.

import { MonthPicker } from "./Seg";
import { useState } from "react";
import { money } from "../../money";
import { Tip } from "../../Tip";
import { papi } from "../api";
import type { PlatformSession } from "../types";
import { Figure, Panel, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";
import { thinLabels, useMeasure } from "./useMeasure";

// ── One customer's health, month by month ───────────────────────────────────
//
// **Three rows sharing one time axis, never one chart with two y-axes.**
// Revenue is money, orders are a count and margin is a rate; putting a rupee
// scale and a percentage scale on the same frame lets the two lines cross
// wherever the axis ranges happen to put them, and readers reliably see a
// crossing as an event. Stacked rows keep every comparison within a measure and
// still let a reader run their eye down one month.
//
// The margin row is *absent* for a salesperson rather than blank: the server
// omits the field, and this renders what it is given.
export function CustomerHealthTimeline({
  session, customerId,
}: { session: PlatformSession; customerId: string }) {
  const [months, setMonths] = useState(18);
  const { data, loading, error, reload } = useInsight(
    () => papi.customerTimeline(session.token, customerId, months),
    [session.token, customerId, months]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const series = (data?.series as Record<string, unknown>[] | undefined) ?? [];
  const unavailable = (data?.unavailable as Record<string, string>[] | undefined) ?? [];
  const hasMargin = series.some((p) => "margin" in p);
  const labels = thinLabels(series, room, 46);

  const revenues = series.map((p) => Number(p.revenue) || 0);
  const orders = series.map((p) => Number(p.orders) || 0);
  const revMax = Math.max(...revenues, 1);
  const orderMax = Math.max(...orders, 1);
  const margins = series.map((p) => (p.margin == null ? null : Number(p.margin)));
  const known = margins.filter((m): m is number => m != null);
  // Padded around the data, not anchored at zero: a book trading between 24%
  // and 27% has all its movement in three points, and a 0–100% frame hides it.
  const mLo = known.length ? Math.min(...known) : 0;
  const mHi = known.length ? Math.max(...known) : 1;
  const mPad = Math.max((mHi - mLo) * 0.25, 0.02);

  const traded = series.filter((p) => Number(p.revenue) > 0).length;

  // Days-to-pay, present only once payments have synced. Same rule as margin:
  // the field is absent rather than null, and the screen renders what it is
  // given rather than deciding what the server should have sent.
  const hasPayments = series.some((p) => "median_days_to_pay" in p);
  const payDays = series.map((p) =>
    p.median_days_to_pay == null ? null : Number(p.median_days_to_pay));
  const knownPay = payDays.filter((d): d is number => d != null);
  const payMax = Math.max(...knownPay, 1);

  return (
    <Panel
      title="History"
      question="How has this account behaved over time"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <MonthPicker id="tl-months" value={months} onChange={setMonths}
                     options={[12, 18, 24, 36]} />
      }
    >
      <p className="viz-muted">
        Traded in {traded} of the last {series.length} months.
      </p>

      <div ref={ref}>
        <Figure
          caption={`One row per measure on a shared time axis${
            hasMargin ? ", because money, a count and a rate cannot honestly share one scale" : ""}.`}
          summary={series.map((p) =>
            `${p.label}: ${money(Number(p.revenue))}, ${p.orders} orders${
              hasMargin && p.margin != null ? `, margin ${pct(Number(p.margin))}` : ""}`,
          ).join("; ")}
          table={
            <table className="viz-table">
              <thead><tr>
                <th scope="col">Month</th><th scope="col">Revenue</th>
                <th scope="col">Orders</th>
                {hasMargin && <th scope="col">Margin</th>}
                {hasMargin && <th scope="col">Cost coverage</th>}
              </tr></thead>
              <tbody>
                {series.map((p, i) => (
                  <tr key={i}>
                    <th scope="row">{String(p.label)}</th>
                    <td>{money(Number(p.revenue))}</td>
                    <td>{String(p.orders)}</td>
                    {hasMargin && <td>{pct(p.margin as number | null)}</td>}
                    {hasMargin && (
                      <td>{p.cost_coverage == null ? "—" : pct(Number(p.cost_coverage), 0)}</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          <div className="tl-rows">
            <TimelineRow
              label="Revenue" peakLabel={money(revMax)}
              cells={series.map((p, i) => ({
                height: revenues[i] / revMax,
                title: `${p.label}: ${money(revenues[i])}`,
                dim: revenues[i] === 0,
              }))}
            />
            <TimelineRow
              label="Orders" peakLabel={`peak ${orderMax} order${orderMax === 1 ? "" : "s"}`} short
              cells={series.map((p, i) => ({
                height: orders[i] / orderMax,
                title: `${p.label}: ${orders[i]} order${orders[i] === 1 ? "" : "s"}`,
                dim: orders[i] === 0,
              }))}
            />

            {/* A rate does not start at zero, so it is not drawn as a bar from
                zero. A month with no revenue has no margin at all — the slot is
                left empty rather than plotted at 0%, which would read as a
                catastrophic month instead of a quiet one. */}
            {hasMargin && (
              <div className="tl-row">
                <div className="tl-row-head">
                  <span className="tl-row-label">Margin</span>
                  <span className="viz-muted">
                    {!known.length
                      ? "no month has both revenue and a cost"
                      // "26%–26%" is a range that is not a range. One price held
                      // all year is a finding; printing it twice hides it.
                      : mHi - mLo < 0.005
                        ? `${pct(mHi, 0)} every month it traded`
                        : `${pct(mLo, 0)}–${pct(mHi, 0)}`}
                  </span>
                </div>
                <div className="tl-track tl-track-rate">
                  {series.map((p, i) => {
                    const m = margins[i];
                    const coverage = p.cost_coverage == null ? null : Number(p.cost_coverage);
                    // A margin computed from a third of the lines is drawn
                    // hollow. Partial evidence shown as solid is the way a
                    // screen launders a guess into a fact.
                    const partial = coverage != null && coverage < 0.999;
                    return (
                      <span className="tl-slot" key={i}
                            title={m == null
                              ? `${p.label}: no margin — ${
                                Number(p.revenue) > 0 ? "no cost on record" : "no trade"}`
                              : `${p.label}: ${pct(m)}${
                                partial ? ` (from ${pct(coverage, 0)} of lines)` : ""}`}>
                        {m != null && (
                          <span
                            className={`tl-dot${partial ? " partial" : ""}`}
                            style={{
                              bottom: `${((m - (mLo - mPad)) /
                                ((mHi + mPad) - (mLo - mPad) || 1)) * 100}%`,
                            }}
                          />
                        )}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Keyed on the month the invoice was raised, so this row lines up
                with that month's own revenue and orders. Indexed by payment
                date instead, January's cash would sit on the March column and
                the three rows would stop describing the same month. */}
            {hasPayments && (
              <TimelineRow
                label="Days to pay" short
                peakLabel={knownPay.length
                  ? `${Math.min(...knownPay)}–${payMax} days`
                  : "nothing settled yet"}
                cells={series.map((p, i) => ({
                  height: payDays[i] == null ? 0 : payDays[i]! / payMax,
                  title: payDays[i] == null
                    ? `${p.label}: nothing invoiced this month has been settled`
                    : `${p.label}: ${payDays[i]} days typical, ${p.settled} settled`,
                  dim: payDays[i] == null,
                }))}
              />
            )}

            <div className="viz-time-axis" aria-hidden="true">
              {series.map((p, i) => (
                <span key={i}>
                  {labels[i] ? String(p.label).replace(/ \d{4}$/, "") : ""}
                </span>
              ))}
            </div>
          </div>
        </Figure>
      </div>

      {/* What this screen cannot show, named. The specification asked for a
          payment series; the platform holds invoice lines, not receipts. Saying
          so is the difference between a gap and a lie by omission. */}
      {unavailable.length > 0 && (
        <ul className="tl-unavailable">
          {unavailable.map((u, i) => (
            <li key={i}>
              <strong>{u.series.replace(/_/g, " ")}</strong> — not shown.{" "}
              <span className="viz-muted">{u.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function TimelineRow({
  label, peakLabel, cells, short,
}: {
  label: string;
  peakLabel: string;
  cells: { height: number; title: string; dim: boolean }[];
  short?: boolean;
}) {
  return (
    <div className="tl-row">
      <div className="tl-row-head">
        <span className="tl-row-label">{label}</span>
        <span className="viz-muted">{peakLabel}</span>
      </div>
      <div className={short ? "tl-track tl-track-short" : "tl-track"}>
        {cells.map((c, i) => (
          <span className="tl-slot" key={i} title={c.title}>
            <span className={`tl-bar${c.dim ? " zero" : ""}`}
                  style={{ height: `${Math.max(c.height * 100, c.dim ? 0 : 2)}%` }} />
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Who changed size, and in which direction ────────────────────────────────
//
// A 5×5 grid where most cells are empty is the right shape anyway: the empty
// cells are the finding. "Nobody moved up" is only visible when the space where
// they would have been is drawn. So empty cells recede rather than disappearing,
// and the diagonal — everyone who stayed put — is marked so the eye can read
// off-diagonal as movement without decoding a legend.
//
// Below ~640px the grid becomes a list of moves. A 5×5 matrix with money in
// every cell is unreadable on a phone, and a shrunken one is worse than a
// different form that answers the same question.
const BAND_MEANING: Record<string, string> = {
  NONE: "Did not trade",
  SMALL: "Bottom quarter",
  MID: "Lower middle",
  LARGE: "Upper middle",
  KEY: "Top fifth",
};

export function MigrationMatrix({
  session, months, onNavigate,
}: {
  session: PlatformSession;
  months: number;
  onNavigate: (route: string) => void;
}) {
  const { data, loading, error, reload } = useInsight(
    () => papi.migration(session.token, months), [session.token, months]);
  const [open, setOpen] = useState<string | null>(null);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const bands = (data?.bands as string[] | undefined) ?? [];
  const edges = (data?.band_edges as number[] | undefined) ?? [];
  const cells = (data?.cells as Record<string, unknown>[] | undefined) ?? [];
  const comparison = data?.comparison as Record<string, Record<string, string>> | undefined;

  const at = (from: string, to: string) =>
    cells.find((c) => c.from === from && c.to === to);

  const rank = (b: string) => bands.indexOf(b);
  const moved = cells.filter((c) => c.from !== c.to);
  const up = moved.filter((c) => rank(String(c.to)) > rank(String(c.from)));
  const down = moved.filter((c) => rank(String(c.to)) < rank(String(c.from)));
  const count = (list: Record<string, unknown>[]) =>
    list.reduce((a, c) => a + Number(c.count), 0);
  const netDelta = cells.reduce((a, c) => a + Number(c.revenue_delta), 0);
  const stayed = count(cells.filter((c) => c.from === c.to));

  const asList = room.width > 0 && room.width < 640;

  return (
    <Panel
      title="Who changed size"
      question={comparison
        ? `Which band each customer moved between, ${comparison.previous?.label} → ${comparison.current?.label}`
        : "Which band each customer moved between"}
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      <p className="viz-headline">
        <strong>{count(down)}</strong> moved down, <strong>{count(up)}</strong>{" "}
        moved up, <strong>{stayed}</strong> stayed. Net{" "}
        <span className={netDelta < 0 ? "neg" : "pos"}>
          {netDelta < 0 ? "−" : "+"}{money(Math.abs(netDelta))}
        </span>{" "}
        across the two periods.
      </p>

      <p className="viz-muted">
        Bands are ranks, not amounts — the cuts come from this book and move with
        it.{" "}
        <Tip
          label="How the bands are cut"
          text={`Quantiles of the two periods combined: bottom quarter, median, top fifth. One set of cuts for both periods, so nobody can "move up" while spending less because the cohort around them shrank.${
            edges.length === 3
              ? ` On this data: SMALL up to ${money(edges[0])}, MID to ${money(edges[1])}, LARGE to ${money(edges[2])}, KEY above it.`
              : " Too few trading customers to cut bands, so everyone is MID."}`}
        />
      </p>

      <div ref={ref}>
        <Figure
          caption={asList
            ? "Every move that happened, largest first. The grid form needs more width than this."
            : "Rows are where a customer was; columns are where they ended up. The shaded diagonal is everyone who stayed in the same band — anything off it is a move."}
          summary={cells.map((c) =>
            `${c.count} from ${c.from} to ${c.to}, ${money(Number(c.revenue_delta))}`,
          ).join("; ")}
        >
          {asList ? (
            <ol className="mig-list">
              {[...cells]
                .sort((a, b) => Math.abs(Number(b.revenue_delta)) - Math.abs(Number(a.revenue_delta)))
                .map((c, i) => (
                  <li key={i} className={`mig-move ${direction(String(c.from), String(c.to), rank)}`}>
                    <MoveButton cell={c} open={open} setOpen={setOpen}
                                onNavigate={onNavigate} />
                  </li>
                ))}
            </ol>
          ) : (
            <table className="mig-grid">
              <caption className="sr-only">
                Customers by band before and after
              </caption>
              <thead>
                <tr>
                  <th scope="col"><span className="sr-only">From band</span></th>
                  {bands.map((b) => (
                    <th key={b} scope="col">
                      {b}
                      <span className="mig-band-note">{BAND_MEANING[b]}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bands.map((from) => (
                  <tr key={from}>
                    <th scope="row">
                      {from}
                      <span className="mig-band-note">{BAND_MEANING[from]}</span>
                    </th>
                    {bands.map((to) => {
                      const cell = at(from, to);
                      const key = `${from}>${to}`;
                      if (!cell) {
                        // An empty diagonal cell is still the diagonal. Shading
                        // only the populated one made the caption ("the shaded
                        // diagonal is everyone who stayed") describe a single
                        // square, and lost the read that matters here: every
                        // mark sitting *below* the shaded line.
                        return (
                          <td key={to}
                              className={`mig-cell empty${from === to ? " same" : ""}`}
                              aria-label="none" />
                        );
                      }
                      return (
                        <td key={to}
                            className={`mig-cell ${direction(from, to, rank)}`}>
                          <button type="button" className="mig-hit"
                                  aria-expanded={open === key}
                                  onClick={() => setOpen(open === key ? null : key)}>
                            <span className="mig-count">{String(cell.count)}</span>
                            <span className="mig-delta">
                              {Number(cell.revenue_delta) < 0 ? "−" : "+"}
                              {money(Math.abs(Number(cell.revenue_delta)))}
                            </span>
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Figure>
      </div>

      {/* The members of the opened cell. A list, below, rather than a tooltip —
          these are names somebody is about to act on, and a hover bubble cannot
          be clicked through to the account. */}
      {!asList && open && (() => {
        const [from, to] = open.split(">");
        const cell = at(from, to);
        if (!cell) return null;
        const members = (cell.members as Record<string, unknown>[]) ?? [];
        return (
          <div className="mig-members">
            <h4>
              {from} → {to}: {String(cell.count)}{" "}
              {Number(cell.count) === 1 ? "customer" : "customers"}
              {members.length < Number(cell.count) &&
                ` (showing the ${members.length} largest moves)`}
            </h4>
            <ul>
              {members.map((m, i) => (
                <li key={i}>
                  <button type="button" className="link-btn"
                          onClick={() => onNavigate(`customer/${String(m.customer_id)}`)}>
                    {String(m.label)}
                  </button>
                  <span className="viz-muted">
                    {money(Number(m.previous))} → {money(Number(m.current))}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        );
      })()}
    </Panel>
  );
}

function direction(from: string, to: string, rank: (b: string) => number): string {
  if (from === to) return "same";
  return rank(to) > rank(from) ? "up" : "down";
}

function MoveButton({
  cell, open, setOpen, onNavigate,
}: {
  cell: Record<string, unknown>;
  open: string | null;
  setOpen: (v: string | null) => void;
  onNavigate: (route: string) => void;
}) {
  const key = `${cell.from}>${cell.to}`;
  const members = (cell.members as Record<string, unknown>[]) ?? [];
  return (
    <>
      <button type="button" className="mig-move-hit" aria-expanded={open === key}
              onClick={() => setOpen(open === key ? null : key)}>
        <span className="mig-move-bands">
          {String(cell.from)} → {String(cell.to)}
        </span>
        <span className="mig-move-figures">
          <span>{String(cell.count)} {Number(cell.count) === 1 ? "customer" : "customers"}</span>
          <span className="viz-muted">
            {Number(cell.revenue_delta) < 0 ? "−" : "+"}
            {money(Math.abs(Number(cell.revenue_delta)))}
          </span>
        </span>
      </button>
      {open === key && (
        <ul className="mig-members-inline">
          {members.map((m, i) => (
            <li key={i}>
              <button type="button" className="link-btn"
                      onClick={() => onNavigate(`customer/${String(m.customer_id)}`)}>
                {String(m.label)}
              </button>
              <span className="viz-muted">
                {money(Number(m.previous))} → {money(Number(m.current))}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
