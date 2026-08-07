// The five Tier 2 views, as three screens.
//
// Two of the specified five are the same chart as another with a different
// measure — "Product Momentum Galaxy" is the margin landscape with volume on
// the vertical, and "Order Flow River" is the revenue composition counting
// invoices instead of money. They are built as one component each with the
// measure as a control, because two components would mean two quadrant rules
// and two top-N rules drifting apart.
//
// **The composition is small multiples, not a stacked river, and that is a
// deliberate departure from the specification.** A stack needs one distinguishable
// hue per band, and the palette validator fails at six: the worst adjacent pair
// measures ΔE 2.9 under deuteranopia, where 8 is the floor. Six bands would have
// meant either colours a reader cannot separate or generated hues that stop
// identifying anyone. Small multiples need no categorical colour at all — each
// row carries its own label — and comparing seven small charts on a shared scale
// is easier than tracking seven bands through a stack. The question ("did the
// mix change?") is answered better, not merely differently.

import { useEffect, useMemo, useState } from "react";
import { scaleLinear, scaleSqrt } from "d3-scale";
import { MonthPicker, Seg } from "./Seg";
import { money } from "../../money";
import { Tip } from "../../Tip";
import { ChartTip, InlineLink, StatusChip } from "../kit";
import { DataGrid, numeric } from "../DataGrid";
import { EntityName } from "../EntityName";
import { papi } from "../api";
import type { EntityOrigin, PlatformSession } from "../types";
import { Figure, Panel, ValueAxis, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";
import { compactMoney, thinLabels, useMeasure } from "./useMeasure";

// ── Landscape (margin vs revenue · product momentum) ────────────────────────
//
// **The dots are one ink, and the quadrant is drawn as a shaded region.** The
// obvious design — a hue per quadrant — was built first and then measured, and
// it fails: four slots on a scatter means every pair is adjacent, and the worst
// pair (`#b0473d` vs `#9a5f14`) is ΔE 8.5 under *normal* vision against a floor
// of 15, ΔE 3.0 under deuteranopia against a floor of 6. The neutral also falls
// to 2.58:1 contrast on this surface.
//
// Colouring the dots was redundant anyway: a quadrant chart already encodes the
// quadrant as *position*, which is what the two split lines are for. So the
// region carries a faint wash and its own in-place label with a count — a
// large-area tint next to its own words, never a swatch a reader has to match
// against a legend — and the marks are a single high-contrast ink. One fewer
// encoding, and the one that remains is readable.
const QUADRANT_TONE: Record<string, string> = {
  FIX_FIRST: "bad", REVIEW: "warn", PROTECT: "good", LEAVE: "flat",
};

/** Where each quadrant sits, given "large" is right and "healthy" is up. */
const QUADRANT_CORNER: Record<string, { right: boolean; top: boolean }> = {
  PROTECT: { right: true, top: true },
  FIX_FIRST: { right: true, top: false },
  LEAVE: { right: false, top: true },
  REVIEW: { right: false, top: false },
};

export function LandscapeScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const [subject, setSubject] = useState("relationship");
  const [measure, setMeasure] = useState("margin");
  const { data, loading, error, reload } = useInsight(
    "landscape",
    () => papi.landscape(session.token, subject, measure),
    [session.token, subject, measure]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const all = (data?.points as Record<string, unknown>[] | undefined) ?? [];
  // Which quadrant the reader has narrowed to, if any.
  //
  // The counts above were readable and useless: "118 Fix first" names a set you
  // could see the size of and could not find, because 800 points overlap into
  // one mass at the left edge. Selecting a quadrant is the way in — the plot
  // shows only those, the axis rescales to *their* range so they spread out,
  // and the table opens beneath sorted by revenue. Cleared when the subject or
  // measure changes, because the quadrants mean something else by then.
  const [focus, setFocus] = useState<string | null>(null);
  useEffect(() => { setFocus(null); }, [subject, measure]);
  const points = focus ? all.filter((p) => String(p.quadrant) === focus) : all;
  const quadrants = (data?.quadrants as Record<string, Record<string, string>>) ?? {};
  const counts = (data?.counts as Record<string, number>) ?? {};
  const currency = String(data?.currency ?? "INR");
  const xSplit = Number(data?.x_split ?? 0);
  const ySplit = Number(data?.y_split ?? 0);

  const xs = points.map((p) => Number(p.x));
  // Filter before converting, not after: `Number(null)` is 0, not NaN, so a
  // NaN guard silently let every point with no margin vote for a domain that
  // reaches zero — which is exactly the assertion the rail exists to avoid.
  const ys = points.filter((p) => p.y != null).map((p) => Number(p.y));
  const xMax = Math.max(...xs, 1);
  // The vertical domain covers the data and the split, padded — it does NOT
  // force zero in. On this book every margin sits between 12% and 21%, and
  // anchoring at 0% pushed all seven points into the top third of an otherwise
  // empty rectangle. The split line is what a reader measures against here, and
  // it is always in frame; the distance to zero is not the question the chart
  // is asking.
  const yLo = Math.min(...ys, ySplit);
  const yHi = Math.max(...ys, ySplit);
  const yPad = Math.max((yHi - yLo) * 0.18, 0.02);
  const sizes = points.map((p) => Number(p.size) || 1);
  const sizeMax = Math.max(...sizes, 1);

  // A point with no vertical value gets its own rail *below* the plot, not a
  // seat on the axis. On the axis it lands inside a quadrant and reads as a
  // margin of zero, which is the opposite of "we do not know" — and on this
  // book that put three unknown relationships inside "Review" and "Fix first"
  // as if somebody had measured them.
  const unknowns = points.filter((p) => p.y == null);
  const RAIL = unknowns.length ? 40 : 0;

  const PLOT_H = room.cramped ? 240 : 320;
  const H = PLOT_H + RAIL;
  const PAD = { t: 18, r: 18, b: 44, l: room.cramped ? 44 : 62 };
  const rMax = room.cramped ? 11 : 15;
  // Radius by area, not by diameter — scaling the radius makes a point with
  // twice the transactions look four times as important. `scaleSqrt` *is* that
  // rule, which is better than a comment claiming a `Math.sqrt` implements it.
  const pr = scaleSqrt().domain([0, sizeMax]).range([4, rMax]);

  // The plot box, and inside it the box a dot's *centre* may occupy. Without the
  // inset the largest value sits exactly on the frame and half the mark is
  // clipped away — which is worst for precisely the biggest relationship. The
  // `Math.max` keeps the inner box from inverting in a panel too narrow to hold
  // two radii; a reversed range would mirror every point left-to-right.
  const x0 = PAD.l, x1 = Math.max(PAD.l + 1, room.width - PAD.r);
  const y0 = PAD.t, y1 = PLOT_H - PAD.b;
  const px = scaleLinear()
    .domain([0, xMax])
    .range([x0 + rMax, Math.max(x0 + rMax, x1 - rMax)]);
  // `.nice()` after the padding, so the gridlines land on round percentages
  // instead of on wherever 18% of the observed spread happened to fall. It
  // widens the domain a little and still does not force zero in — which is the
  // property the padding exists to protect.
  const py = scaleLinear()
    .domain([yLo - yPad, yHi + yPad])
    .range([Math.max(y0 + rMax, y1 - rMax), y0 + rMax])
    .nice();
  // A split can land on or past an edge — every point above the median, an empty
  // book — and a region rectangle drawn from an unclamped split has a negative
  // width, which browsers refuse to render at all.
  const clampX = (v: number) => Math.min(Math.max(v, x0), x1);
  const clampY = (v: number) => Math.min(Math.max(v, y0), y1);

  return (
    <Panel
      title={subject === "product" ? "Product landscape" : "Margin landscape"}
      question={measure === "margin"
        ? "Which relationships are large and priced below the floor"
        : "Which items are growing, and which are large and shrinking"}
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          <Seg label="Subject" value={subject} onChange={setSubject}
               options={[["relationship", "Customer × item"], ["product", "Item"]]} />
          <Seg label="Vertical" value={measure} onChange={setMeasure}
               options={[["margin", "Margin"], ["momentum", "Growth"]]} />
        </div>
      }
    >
      {/* The quadrant legend is the point of the chart, so it leads rather than
          sitting under it as a key. Each is a job, with its own count. */}
      <ul className="quad-legend">
        {["FIX_FIRST", "REVIEW", "PROTECT", "LEAVE"].map((q) => {
          const n = counts[q] ?? 0;
          const on = focus === q;
          return (
            <li key={q} className={`quad quad-${QUADRANT_TONE[q]}${on ? " quad-on" : ""}`}>
              <button type="button" className="quad-hit" aria-pressed={on}
                      disabled={n === 0} onClick={() => setFocus(on ? null : q)}>
                <span className="quad-count">{n}</span>
                <span className="quad-body">
                  <strong>{quadrants[q]?.label ?? q}</strong>
                  <span className="viz-muted">{quadrants[q]?.meaning}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {focus && (
        <p className="quad-focus">
          Showing the <strong>{points.length}</strong>{" "}
          {quadrants[focus]?.label ?? focus} of {all.length}. The axis is scaled
          to these, so positions are not comparable with the whole book.{" "}
          <InlineLink onClick={() => setFocus(null)}>Show everything</InlineLink>
        </p>
      )}

      <div ref={ref}>
        <Figure
          caption={`Horizontal: ${String(data?.x_label ?? "")}. Vertical: ${String(data?.y_label ?? "")}. Dot size is transaction count.`}
          summary={points.map((p) =>
            `${p.label}${p.sublabel ? " / " + p.sublabel : ""}: ${money(Number(p.x))}, ${pct(p.y as number)}, ${quadrants[String(p.quadrant)]?.label}`,
          ).join("; ")}
          // Open, and sorted by revenue, the moment a quadrant is selected:
          // at that point the reader is not looking at a chart any more, they
          // are working a list, and the largest money is where to start.
          tableOpen={Boolean(focus)}
          tableLabel={focus
            ? `${points.length} ${quadrants[focus]?.label ?? focus}, largest first`
            : "View as a table"}
          table={
            <table className="viz-table">
              <thead><tr>
                <th scope="col">Subject</th><th scope="col">Revenue</th>
                <th scope="col">{String(data?.y_label ?? "")}</th>
                <th scope="col">Quadrant</th>
              </tr></thead>
              <tbody>
                {[...points]
                  .sort((a, b) => Number(b.x) - Number(a.x))
                  .map((p, i) => (
                  <tr key={i}>
                    <td>{String(p.label)}{p.sublabel ? ` / ${p.sublabel}` : ""}</td>
                    <td>{money(Number(p.x))}</td>
                    <td>{pct(p.y as number)}</td>
                    <td>{quadrants[String(p.quadrant)]?.label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          {room.width > 0 && (
            <svg width={room.width} height={H} viewBox={`0 0 ${room.width} ${H}`}
                 className="viz-svg" role="presentation">
              {/* The four regions, washed and labelled in place. A reader learns
                  which quadrant a dot is in by looking at where it is, which is
                  the only encoding here that does not depend on colour. */}
              {(["PROTECT", "FIX_FIRST", "LEAVE", "REVIEW"] as const).map((q) => {
                const c = QUADRANT_CORNER[q];
                const xa = c.right ? clampX(px(xSplit)) : x0;
                const xb = c.right ? x1 : clampX(px(xSplit));
                const ya = c.top ? y0 : clampY(py(ySplit));
                const yb = c.top ? clampY(py(ySplit)) : y1;
                if (xb - xa < 2 || yb - ya < 2) return null;
                return (
                  <g key={q}>
                    <rect x={xa} y={ya} width={xb - xa} height={yb - ya}
                          className={`quad-wash quad-wash-${QUADRANT_TONE[q]}`} />
                    {/* Anchored to the region's outer corner, so the label never
                        lands on top of the split lines the dots cluster around.
                        A dot can still reach a corner, so the label is painted
                        stroke-first against the surface — a halo, because a
                        legible label mattered more than a pristine mark. */}
                    <text
                      x={c.right ? xb - 6 : xa + 6}
                      y={c.top ? ya + 14 : yb - 6}
                      textAnchor={c.right ? "end" : "start"}
                      className="quad-wash-label"
                    >
                      {counts[q] ?? 0}
                      {!room.cramped && ` · ${quadrants[q]?.label ?? q}`}
                    </text>
                  </g>
                );
              })}

              {/* The vertical scale, over the washes and under the marks. This
                  used to be two labels on the padded domain endpoints — 10% and
                  23%, numbers nobody chose and that move whenever the data does
                  — with nothing between them, so a dot's margin could only be
                  read by hovering it. */}
              <ValueAxis
                scale={py}
                x0={x0}
                x1={x1}
                count={room.cramped ? 3 : 4}
                format={(v) => pct(v, 0)}
              />

              {/* Split lines, labelled. An unlabelled reference line is a line
                  the reader has to guess the meaning of. */}
              <line x1={clampX(px(xSplit))} x2={clampX(px(xSplit))} y1={y0} y2={y1}
                    stroke="var(--viz-rule)" strokeDasharray="4 4" />
              <line x1={x0} x2={x1} y1={clampY(py(ySplit))} y2={clampY(py(ySplit))}
                    stroke="var(--viz-rule)" strokeDasharray="4 4" />
              {/* The number belongs on the line. "review floor" alone makes a
                  reader go to Settings to find out what it is. */}
              <text x={x0 + 3} y={clampY(py(ySplit)) - 5} className="viz-axis-note">
                {measure === "margin" ? `review floor ${pct(ySplit, 0)}` : "flat"}
              </text>

              {/* Axes */}
              <line x1={x0} x2={x1} y1={y1} y2={y1} stroke="var(--viz-rule)" />
              <line x1={x0} x2={x0} y1={y0} y2={y1} stroke="var(--viz-rule)" />
              <text x={x0} y={y1 + 18} className="viz-axis">0</text>
              <text x={clampX(px(xSplit))} y={y1 + 18} textAnchor="middle"
                    className="viz-axis-note">
                {room.cramped ? "median" : "median revenue"}
              </text>
              <text x={x1} y={y1 + 18} textAnchor="end"
                    className="viz-axis">{compactMoney(xMax, currency)}</text>

              {/* The rail for points with no vertical value, fenced off from the
                  plot so nothing about their margin is implied by where they
                  sit. Horizontal position still means revenue, which is known. */}
              {RAIL > 0 && (
                <g>
                  <line x1={x0} x2={x1} y1={H - RAIL + 2} y2={H - RAIL + 2}
                        stroke="var(--viz-rule)" strokeDasharray="2 4" />
                  <text x={x0} y={H - RAIL + 15} className="viz-axis-note">
                    {unknowns.length} with no{" "}
                    {measure === "margin" ? "margin" : "prior volume"} on record
                    {!room.cramped && " — placed by revenue only"}
                  </text>
                </g>
              )}

              {points.map((p, i) => {
                const y = p.y == null ? null : Number(p.y);
                const cy = y == null ? H - 12 : py(y);
                // In Item mode a point is a product, and there is no product
                // screen — so these dots did nothing at all when clicked, while
                // looking identical to the ones that work. Stock is the item
                // view this product already has: on hand, age, what it costs to
                // hold, and the customers who buy it. Send them there focused
                // on the item rather than leaving a dead control on the page.
                const target = p.customer_id
                  ? `customer/${String(p.customer_id)}`
                  : p.product_id
                    ? `stock?item=${encodeURIComponent(String(p.product_id))}`
                    : null;
                return (
                  <g key={i} className={target ? "dot dot-clickable" : "dot"}
                     role={target ? "button" : undefined}
                     tabIndex={target ? 0 : undefined}
                     onClick={target ? () => onNavigate(target) : undefined}
                     onKeyDown={target ? (e) => {
                       if (e.key === "Enter" || e.key === " ") {
                         e.preventDefault(); onNavigate(target);
                       }
                     } : undefined}
                     aria-label={target
                       ? `${p.label}, ${money(Number(p.x))}, ${pct(y)}. Open.`
                       : undefined}>
                    <title>
                      {`${p.label}${p.sublabel ? " / " + p.sublabel : ""}\n`}
                      {`${money(Number(p.x))} · ${y == null ? "no margin on record" : pct(y)}\n`}
                      {`${quadrants[String(p.quadrant)]?.label} · ${p.size} transactions`}
                    </title>
                    {/* A point with no vertical value sits on the axis as a
                        hollow ring: it is placed but not asserted. Shape, not
                        colour, because "unknown" must survive greyscale. */}
                    <circle cx={px(Number(p.x))} cy={cy}
                            r={y == null
                              ? Math.min(pr(Number(p.size) || 1), 10)
                              : pr(Number(p.size) || 1)}
                            className={`dot-mark${y == null ? " dot-unknown" : ""}`} />
                  </g>
                );
              })}
            </svg>
          )}
        </Figure>
      </div>
      <p className="viz-muted viz-footnote">{String(data?.y_split_meaning ?? "")}</p>
    </Panel>
  );
}

// ── Composition (revenue mix · order flow) as small multiples ───────────────
export function CompositionScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const [dimension, setDimension] = useState("customer");
  const [measure, setMeasure] = useState("revenue");
  const [months, setMonths] = useState(12);
  const { data, loading, error, reload } = useInsight(
    "composition",
    () => papi.composition(session.token, dimension, measure, months),
    [session.token, dimension, measure, months]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const series = (data?.series as Record<string, unknown>[] | undefined) ?? [];
  const periods = (data?.periods as Record<string, string>[] | undefined) ?? [];
  const movement = data?.movement as Record<string, unknown> | undefined;
  // No local currency here: nothing on this screen is compacted, so `money()`
  // — which carries the session's currency — formats every figure in full.
  const labels = useMemo(() => thinLabels(periods, room, 52), [periods, room]);

  // One shared scale across every row — the whole point of small multiples is
  // that the panels are comparable, and a per-row scale destroys that.
  //
  // Deliberately not a d3 scale. These rows are CSS bars whose height is a
  // percentage of their track, so the "scale" is `v / peak` and the range is
  // literally 0–100%: a `scaleLinear` here would produce the same number
  // through an object, which is the abstraction-for-its-own-sake CLAUDE.md §2
  // names. d3-scale is used where there are pixels and ticks to compute.
  const peak = Math.max(
    ...series.flatMap((s) => (s.values as number[]).map(Number)), 1);
  const mover = movement?.biggest_mover as Record<string, unknown> | undefined;

  return (
    <Panel
      title={measure === "orders" ? "Order flow" : "Revenue mix"}
      question="Has the mix shifted, and towards whom"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          <Seg label="By" value={dimension} onChange={setDimension}
               options={[["customer", "Customer"], ["product", "Item"]]} />
          <Seg label="Measure" value={measure} onChange={setMeasure}
               options={[["revenue", "Revenue"], ["orders", "Orders"]]} />
          <MonthPicker id="comp-months" value={months} onChange={setMonths}
                       options={[6, 12, 24]} />
        </div>
      }
    >
      {mover && (
        <p className="viz-headline">
          Biggest shift: <strong>{String(mover.label)}</strong> held{" "}
          {pct(Number(mover.from), 0)} of the first half of this window and{" "}
          {pct(Number(mover.to), 0)} of the second — {Number(mover.change) >= 0 ? "up" : "down"}{" "}
          {pct(Math.abs(Number(mover.change)), 0)} of share.
        </p>
      )}

      <div ref={ref}>
        <Figure
          caption="One row per contributor, all on the same scale so the rows are comparable. Small multiples rather than a stacked band — six bands is more colours than a reader can separate."
          summary={series.map((s) =>
            `${s.label}: ${(s.values as number[]).map((v, i) => `${periods[i]?.label} ${measure === "orders" ? v : money(v)}`).join(", ")}`,
          ).join("; ")}
          table={
            <DataGrid<Record<string, unknown>>
              ariaLabel={measure === "orders" ? "Order flow by month" : "Revenue mix by month"}
              pageSize={10}
              filters={false}
              rows={series.map((s) => ({
                label: String(s.label),
                total: Number(s.total),
                ...Object.fromEntries(
                  (s.values as number[]).map((v, j) => [`m${j}`, Number(v)])),
              }))}
              columns={[
                { field: "label", headerName: "Contributor", flex: 1, minWidth: 220,
                  filter: "agTextColumnFilter", pinned: "left" },
                numeric<Record<string, unknown>>(
                  "total", "Total",
                  (v) => (measure === "orders" ? String(v) : money(v)),
                  { width: 150, flex: 0, sort: "desc" }),
                // One column per month, in the chart's own order, so the table
                // and the bars are the same object read two ways.
                ...periods.map((p, j) => numeric<Record<string, unknown>>(
                  `m${j}`, String(p.label),
                  (v) => (measure === "orders" ? String(v) : money(v)),
                  { width: 140, flex: 0 })),
              ]}
            />
          }
        >
          <div className="multiples">
            {series.map((s, i) => {
              const values = (s.values as number[]).map(Number);
              const isOther = String(s.key) === "__other__";
              // There is no item screen to open, so an item row must not be
              // dressed as a link. A control that looks live and does nothing
              // teaches people to stop trying the ones that work.
              const openable = !isOther && dimension === "customer";
              const total = Number(s.total);
              const first = values.find((v) => v > 0) ?? 0;
              const last = values[values.length - 1] ?? 0;
              const dir = last > first ? "up" : last < first ? "down" : "flat";
              return (
                <div className="multiple" key={i}>
                  <div className="multiple-head">
                    <InlineLink
                      bold
                      disabled={!openable}
                      onClick={() => onNavigate(`customer/${String(s.key)}`)}
                    >
                      {String(s.label)}
                    </InlineLink>
                    <span className="multiple-total">
                      {measure === "orders" ? `${total} orders` : money(total)}
                      <span className={`multiple-dir ${dir}`}>
                        {dir === "up" ? "▲" : dir === "down" ? "▼" : "—"}
                      </span>
                    </span>
                  </div>
                  {/* Not `aria-hidden` any more: these carry real tooltips
                      now, and hiding the row from assistive tech also hid the
                      only place the monthly figures were readable. */}
                  <div className="multiple-bars">
                    {values.map((v, j) => {
                      const prev = j > 0 ? values[j - 1] : null;
                      const change = prev && prev > 0 ? (v - prev) / prev : null;
                      return (
                        <ChartTip
                          key={j}
                          title={
                            <>
                              <strong>{String(s.label)}</strong>
                              <br />
                              {periods[j]?.label}:{" "}
                              {measure === "orders"
                                ? `${v} order${v === 1 ? "" : "s"}`
                                : money(v)}
                              {change != null && (
                                <>
                                  <br />
                                  <span style={{ opacity: 0.85 }}>
                                    {change >= 0 ? "▲" : "▼"}{" "}
                                    {Math.abs(Math.round(change * 100))}% on{" "}
                                    {periods[j - 1]?.label}
                                  </span>
                                </>
                              )}
                              <br />
                              <span style={{ opacity: 0.8 }}>
                                Bars share one scale across every row, so
                                heights compare between customers
                              </span>
                            </>
                          }
                        >
                          <span className="multiple-slot">
                            <span
                              className={`multiple-bar${isOther ? " other" : ""}`}
                              style={{ height: `${peak ? (v / peak) * 100 : 0}%` }}
                            />
                          </span>
                        </ChartTip>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            <div className="viz-time-axis" aria-hidden="true">
              {periods.map((p, i) => (
                <span key={i}>{labels[i] ? p.label.replace(/ \d{4}$/, "") : ""}</span>
              ))}
            </div>
          </div>
        </Figure>
      </div>

      {Number(data?.folded_count ?? 0) > 0 && (
        <p className="viz-muted viz-footnote">
          Everything outside the top six is folded into one row.
          <Tip
            label="Why the tail is folded"
            text="A stacked chart needs one distinguishable colour per band and the palette check fails at six — the worst adjacent pair is indistinguishable under deuteranopia. Folding the tail is honest; generating more hues would not be."
          />
        </p>
      )}
    </Panel>
  );
}

// ── Buying cadence (the cycle wheel, plus who is overdue) ───────────────────
export function CadenceScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const { data, loading, error, reload } = useInsight(
    "cadence",
    () => papi.cadence(session.token), [session.token]);

  const wheel = (data?.wheel as Record<string, number>[] | undefined) ?? [];
  const customers = (data?.customers as Record<string, unknown>[] | undefined) ?? [];
  const overdue = customers.filter((c) => c.overdue);
  const peak = Math.max(...wheel.map((d) => Number(d.orders)), 1);
  const totalOrders = wheel.reduce((a, d) => a + Number(d.orders), 0);
  // Named in the tooltip so the wheel says which spoke is the long one without
  // the reader having to eyeball lengths against an unlabelled rim.
  const peakDay = wheel.find((d) => Number(d.orders) === peak)?.day ?? null;

  const R = 118, CX = 140, CY = 140, INNER = 46;

  return (
    <Panel
      title="Buying rhythm"
      question="When do orders land, and who has missed their own cycle"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      <div className="cadence-grid">
        <Figure
          caption={`Each spoke is a day of the month; the longest is ${peak} order${peak === 1 ? "" : "s"}. Radial because the month is a cycle — the 31st sits next to the 1st, and a bar chart would cut that join.`}
          summary={wheel.filter((d) => d.orders > 0)
            .map((d) => `day ${d.day}: ${d.orders} orders`).join(", ")}
          table={
            <table className="viz-table">
              <thead><tr><th scope="col">Day</th><th scope="col">Orders</th></tr></thead>
              <tbody>
                {wheel.filter((d) => d.orders > 0).map((d) => (
                  <tr key={d.day}><th scope="row">{d.day}</th><td>{d.orders}</td></tr>
                ))}
              </tbody>
            </table>
          }
        >
          {/* The one chart in this file that may safely scale rather than be
              measured: a wheel is square, so `preserveAspectRatio` defaults to
              uniform and nothing — glyphs included — is stretched. */}
          <svg viewBox="0 0 280 280" className="cadence-wheel"
               role="presentation">
            <circle cx={CX} cy={CY} r={INNER} className="wheel-hub" />
            <circle cx={CX} cy={CY} r={R} className="wheel-rim" />
            {wheel.map((d) => {
              const angle = ((d.day - 1) / 31) * Math.PI * 2 - Math.PI / 2;
              const len = INNER + (Number(d.orders) / peak) * (R - INNER);
              const x1 = CX + Math.cos(angle) * INNER;
              const y1 = CY + Math.sin(angle) * INNER;
              const x2 = CX + Math.cos(angle) * len;
              const y2 = CY + Math.sin(angle) * len;
              const share = Number(d.orders) / totalOrders;
              return (
                <ChartTip
                  key={d.day}
                  title={
                    <>
                      <strong>Day {d.day} of the month</strong>
                      <br />
                      {d.orders} order{d.orders === 1 ? "" : "s"} landed on this
                      day across the window
                      {totalOrders > 0 && <> · {Math.round(share * 100)}% of all orders</>}
                      <br />
                      <span style={{ opacity: 0.8 }}>
                        Busiest day is the {peakDay}
                        {peakDay ? ` with ${peak}` : ""}
                      </span>
                    </>
                  }
                >
                  <line x1={x1} y1={y1} x2={x2} y2={y2}
                        className={Number(d.orders) ? "wheel-spoke" : "wheel-spoke empty"}
                        strokeWidth={5} strokeLinecap="round" />
                </ChartTip>
              );
            })}
            {[1, 8, 15, 22].map((day) => {
              const angle = ((day - 1) / 31) * Math.PI * 2 - Math.PI / 2;
              return (
                <text key={day} className="wheel-label"
                      x={CX + Math.cos(angle) * (R + 14)}
                      y={CY + Math.sin(angle) * (R + 14) + 4}
                      textAnchor="middle">{day}</text>
              );
            })}
            <text x={CX} y={CY - 4} textAnchor="middle" className="wheel-centre">
              {String(data?.median_interval_days ?? "—")}
            </text>
            <text x={CX} y={CY + 12} textAnchor="middle" className="wheel-centre-sub">
              median days
            </text>
          </svg>
        </Figure>

        <div className="cadence-list">
          {/* The tip carries no child: its own "?" mark is the affordance.
              Prose passed as a child inherits the heading's font and renders as
              a sentence fragment — "2 past their own cycle how this is judged". */}
          <h4>
            {overdue.length} past their own cycle
            <Tip
              label="How overdue is judged"
              text="Measured against each customer's median gap between orders, not one company-wide interval — a quarterly buyer is not late in month two. Customers with too few orders to establish a rhythm are counted separately rather than assumed regular."
            />
          </h4>
          {/* A grid, and every customer rather than the first twelve. The list
              showed a fixed dozen in server order, so "who else is close to
              overdue" was unanswerable without leaving the screen — and the
              answer is a sort, which a list cannot do. */}
          <DataGrid<Record<string, unknown>>
            ariaLabel="Buying rhythm by customer"
            twoLineRows
            pageSize={12}
            rows={customers}
            onRowClick={(c) => onNavigate(`customer/${String(c.customer_id)}`)}
            columns={[
              {
                field: "label", headerName: "Customer", flex: 1, minWidth: 240,
                filter: "agTextColumnFilter",
                cellRenderer: (p: { data?: Record<string, unknown> }) => (
                  <EntityName
                    name={String(p.data?.label ?? "")}
                    sub={p.data?.typical_interval_days
                      ? `usually every ${String(p.data.typical_interval_days)}d`
                      : `only ${String(p.data?.order_count)} orders — no rhythm yet`}
                    origin={p.data?.origin as EntityOrigin | undefined}
                    show={Boolean(data?.sources_differ)}
                  />
                ),
              },
              numeric<Record<string, unknown>>(
                "days_since_last", "Since last", (v) => `${v} d`,
                {
                  width: 140, flex: 0, sort: "desc",
                  headerTooltip: "Days since this customer's most recent order.",
                }),
              numeric<Record<string, unknown>>(
                "typical_interval_days", "Usual gap",
                (v) => `${v} d`,
                {
                  width: 140, flex: 0,
                  headerTooltip: "This customer's own median gap between "
                    + "orders. Blank where they have too few orders for a "
                    + "median to mean anything.",
                }),
              {
                field: "overdue", headerName: "Status", width: 140, flex: 0,
                filter: "agTextColumnFilter",
                // A Chip, per the UI standard — status is never bare coloured
                // text. Only rendered when true: "not overdue" is the resting
                // state and a column of grey "fine" chips is noise.
                cellRenderer: (p: { data?: Record<string, unknown> }) =>
                  (p.data?.overdue
                    ? <StatusChip label="Overdue" tone="bad"
                                  tip="Past this customer's own median gap between orders." />
                    : null),
                valueFormatter: (p) => (p.value ? "Overdue" : ""),
              },
            ]}
          />
          {Number(data?.unestimable_count ?? 0) > 0 && (
            <p className="viz-muted viz-footnote">
              {String(data?.unestimable_count)} customer(s) have fewer than{" "}
              {String(data?.min_orders_for_cadence)} orders, so they have no
              estimable rhythm and are never called overdue.
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}

// ── shared ──────────────────────────────────────────────────────────────────
