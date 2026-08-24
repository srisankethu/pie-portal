// The working capital journey: where the book has been, where it is, and what
// kind of passage the money it has already promised makes next.
//
// This is the cash projection redrawn, not a second reading of it. Every figure
// on the sheet comes from `/insight/cashflow` — the same endpoint, the same
// arithmetic, the same refusals — and the panel computes nothing the server did
// not send. What changed is the form, and the form is doing work:
//
// **A channel, because the width is the finding.** The old chart drew two
// running lines and a shaded band between them, which is the same information
// and reads as a chart with an error bar. Drawn as a navigable channel with the
// route inside it, the question a reader actually has — "is the space I have to
// steer in getting wider or narrower" — is answered by the shape rather than by
// comparing two lines at a point. The edges keep their meanings exactly:
// upper is `best` (customers at their fastest, us paying at our slowest), lower
// is `worst` (the reverse, and the one a week is funded against), centre is
// `expected` (everybody at their own median).
//
// **The datum, because the two halves are different kinds of fact.** Left of
// it is money that moved: receipts and supplier payments, at the payment grain,
// nothing shifted or weighted. Right of it is money that has been promised and
// has not moved yet. An engineering datum is exactly the right mark for that
// boundary — it is a reference the drawing is dimensioned *from*, not an event
// on the timeline — and it stops the past being read as a forecast that
// happened to come true.
//
// **It is still movement, never a position.** Both halves run from zero at the
// datum because there is no opening balance in this platform to run either
// from; PIE reads payments, not bank balances. The sheet says so in three
// places, and the vertical axis is labelled movement rather than cash.
//
// **No score, no verdict, no recommendation.** Nothing here ranks a week,
// grades the passage or tells anybody what to do about it. The deepest point of
// the committed book is an argmin the server already computed; the envelope is
// a subtraction. Every other mark on the sheet is a measurement with its date
// on it. What the drawing is for is seeing the shape of the quarter, and a
// score would replace the thing it is showing with a summary of it.

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import { useMemo, useState } from "react";
import { scaleLinear } from "d3-scale";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { ChartTip, MetricCard, StatusChip } from "../kit";
import { DataGrid, numeric } from "../DataGrid";
import type { ColDef } from "../DataGrid";
import type { PlatformSession } from "../types";
import type { Room } from "./useMeasure";
import { Figure, Panel, stateOf } from "./Panel";
import { Seg } from "./Seg";
import { pct, useInsight } from "./useInsight";
import { compactMoney, thinLabels, useMeasure } from "./useMeasure";
import {
  SECTION_LETTERS, channelOutline, extentOf, futureLegs, indexOfWeek, num,
  pastLegs, peakFlow, rows, routePoints, sectionIndices, widestEnvelope,
} from "./channel-layout";
import type { FutureLeg, PastLeg, Row } from "./channel-layout";

const HORIZONS: [string, string][] = [["13", "13 weeks"], ["26", "26 weeks"]];

/** A signed amount, in the drawing's voice: the sign is a word-length glyph
 *  rather than a colour, so it survives greyscale and a printed sheet. */
const signed = (v: number): string =>
  `${v >= 0 ? "+" : "−"}${money(Math.abs(v))}`;

/** The same figure, kept on one line.
 *
 *  A narrow metric card broke "−₹95,64,000" after the minus, which reads as a
 *  dash and a positive number — the sign is the half of that figure that
 *  matters most, and a line break is not a place to leave it. */
const NoWrap = ({ children }: { children: string }) => (
  <Box component="span" sx={{ whiteSpace: "nowrap" }}>{children}</Box>
);

export function CashJourney({ session }: { session: PlatformSession }) {
  const [weeks, setWeeks] = useState("13");
  const { data, loading, error, reload } = useInsight(
    "cashflow",
    () => papi.cashflow(session.token, Number(weeks)), [session.token, weeks]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const committed = rows(data?.buckets);
  const scenarios = (data?.scenarios ?? {}) as Record<string, Row>;
  const basis = (data?.basis ?? {}) as Row;
  const actual = (data?.actual ?? null) as Row | null;
  // A refused history is not a quiet one. `cashflow.actual` returns its weeks
  // whether or not any payment landed in them, and every one of those weeks is
  // a real zero — so drawing them when the server has said it has nothing
  // would put a flat, confident route across the left half of the sheet
  // asserting that no cash moved all quarter. There is no route to draw, and
  // the reason is printed instead.
  const noHistory = (actual?.empty_reason ?? null) as string | null;
  const travelled = noHistory ? null : actual;

  const past = useMemo(() => pastLegs(travelled), [travelled]);
  const future = useMemo(
    () => futureLegs(committed, rows(scenarios.best?.buckets),
                     rows(scenarios.expected?.buckets),
                     rows(scenarios.worst?.buckets)),
    // Keyed on the response rather than on the four arrays: those are read
    // out of `data` on every render, so they are new objects each time and a
    // memo over them would never hit.
    [data],
  );

  const hasChannel = future.some((f) => f.envelope != null);
  const widest = useMemo(() => widestEnvelope(future), [future]);
  // The server's own deepest week, located on the drawing rather than
  // recomputed — see `indexOfWeek`.
  const pressureWeek = String(scenarios.worst?.lowest_week_starts_on
    ?? data?.lowest_week_starts_on ?? "");
  const pressureAt = indexOfWeek(future, pressureWeek);
  const requirement = num(data?.requirement);
  const lowest = num(data?.lowest_cumulative);

  // Which week the callout is describing. Negative indices are past weeks, so
  // one number addresses the whole drawing; `null` means the datum itself.
  // It opens on the deepest committed week because that is the one the sheet
  // exists to make findable — not a judgement about it, just where to look.
  const [pinned, setPinned] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const opening = pressureAt >= 0 ? pressureAt : (future.length ? 0 : null);
  const at = hovered ?? pinned ?? opening;

  const currency = String(data?.currency ?? "INR");
  const state = stateOf(loading, error, data?.empty_reason as string);
  const measuredShare = num(basis.share_measured);
  const outflowShare = num(basis.outflow_share_measured);
  const outflowShifted = Boolean(basis.outflow_shifted);

  // Two on a full sheet, one where there is no room for a pair. Chosen from
  // the horizon's length rather than from where the channel looks interesting
  // — see `sectionIndices`.
  const sections = useMemo(
    () => sectionIndices(future, room.tight ? 1 : 2), [future, room.tight]);

  const endsOn = future.length ? future[future.length - 1] : null;
  const destination = endsOn
    ? { best: endsOn.best, expected: endsOn.expected, worst: endsOn.worst,
        onTerms: endsOn.onTerms }
    : null;

  return (
    <Panel
      title="Working capital journey"
      question="Where the book has been, where it is now, and what passage the money it has already promised makes next"
      state={state}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload}
      wide
      actions={
        <div className="seg-controls">
          <Seg label="Horizon" value={weeks} onChange={setWeeks}
               options={HORIZONS} />
        </div>
      }
    >
      <DrawingDefs />

      <p className="viz-headline">
        Over {String(data?.weeks ?? "")} weeks the committed book moves cash by{" "}
        <strong>{signed(num(data?.net_over_horizon))}</strong>
        {lowest < 0 && Boolean(data?.lowest_week_starts_on) && (
          <> · deepest at <strong>−{money(Math.abs(lowest))}</strong> in the
          week of {formatDate(String(data?.lowest_week_starts_on))} if everyone
          pays to terms</>
        )}
        {hasChannel && requirement < lowest && (
          <> · <strong>−{money(Math.abs(requirement))}</strong> at the speed
          money has actually moved</>
        )}.{" "}
        <span className="viz-muted">
          Movement, not a balance — the platform reads payments, never bank
          balances, so there is no position to run this from. The drawing is
          dimensioned from the datum in both directions and states no level at
          any point on it.
          {hasChannel && (
            <>
              {" "}The channel covers {pct(measuredShare)} of the money coming in
              {outflowShifted && <> and {pct(outflowShare)} of the money going
              out</>}: the rest has too little settled history to measure and is
              left on its due date rather than given a made-up one.
            </>
          )}
        </span>
      </p>

      <Metrics
        destination={destination} hasChannel={hasChannel} widest={widest}
        requirement={requirement} pressureWeek={pressureWeek}
        weeks={String(data?.weeks ?? "")} />

      <div ref={ref}>
        <Figure
          caption={
            "A plan view of the quarter, time running left to right. The solid "
            + "route behind the datum is cash that actually moved — receipts and "
            + "supplier payments, on the day they were made. In front of it the "
            + "hatched channel is the committed book at three timings: the upper "
            + "edge is customers at their fastest and us paying at our slowest, "
            + "the lower edge the reverse, and the line inside is everybody at "
            + "their own median. The fine line is the same book on its due "
            + "dates. Arrows crossing the sheet are invoices falling due above "
            + "and bills falling due below, scaled to the week's amount. Both "
            + "halves are measured from the datum and neither states a cash "
            + "position."
          }
          summary={describe(past, future)}
          table={<JourneyTable past={past} future={future} />}
        >
          {room.width > 0 && (past.length > 0 || future.length > 0) && (
            <JourneyDrawing
              past={past} future={future} room={room}
              currency={currency}
              asOf={String(data?.as_of ?? "")}
              horizonEndsOn={String(data?.horizon_ends_on ?? "")}
              observedFrom={travelled?.observed_from as string | null}
              pressureAt={pressureAt}
              at={at}
              onHover={setHovered}
              onPin={setPinned}
              sections={sections}
            />
          )}
        </Figure>
      </div>

      <Stack direction={{ xs: "column", md: "row" }} spacing={2}
             sx={{ alignItems: "stretch" }}>
        <Box sx={{ flex: "1 1 0", minWidth: 0 }}>
          <Callout past={past} future={future} at={at}
                   asOf={String(data?.as_of ?? "")} />
        </Box>
        {sections.length > 0 && (
          <Sections future={future} indices={sections} currency={currency} />
        )}
      </Stack>

      {noHistory && (
        <p className="viz-muted viz-footnote">
          Nothing is drawn behind the datum. {noHistory}
        </p>
      )}

      {!hasChannel && future.length > 0 && (
        <p className="viz-muted viz-footnote">
          No channel is drawn. Too little has settled against these parties to
          measure how their money actually moves, so every timing collapses onto
          the due dates and the committed book is one route rather than a space
          between two. A width invented from that would be a funding figure
          somebody could act on.
        </p>
      )}

      <Driving data={data} />
    </Panel>
  );
}

// ── the compact metrics ─────────────────────────────────────────────────────
//
// Five figures, and every one of them is either a number the server computed or
// a subtraction of two of them. Nothing here is a grade: "cash envelope" is a
// width in rupees, and "next pressure point" is the argmin the projection
// already publishes, printed with its date so it can be checked.

function Metrics({
  destination, hasChannel, widest, requirement, pressureWeek, weeks,
}: {
  destination: { best: number | null; expected: number | null;
                 worst: number | null; onTerms: number } | null;
  hasChannel: boolean;
  widest: { index: number; width: number; startsOn: string } | null;
  requirement: number;
  pressureWeek: string;
  weeks: string;
}) {
  const end = destination;
  return (
    <Box sx={{
      display: "grid", gap: 2,
      gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)",
                             lg: "repeat(5, 1fr)" },
    }}>
      <MetricCard
        label="Expected"
        value={end == null ? "—"
          : <NoWrap>{signed(end.expected ?? end.onTerms)}</NoWrap>}
        sub={`Where ${weeks} weeks of committed money leaves the book, with every party at their own median.`}
        tip="Movement over the horizon, not a closing balance. Everybody on the middle of their own measured behaviour."
      />
      <MetricCard
        label="Best"
        value={end?.best == null ? "—"
          : <NoWrap>{signed(end.best)}</NoWrap>}
        sub="Customers at their fastest, us paying at our slowest."
        tip="A corner, not a hope: both ends are speeds this book has actually settled at."
      />
      <MetricCard
        label="Worst"
        value={end?.worst == null ? "—"
          : <NoWrap>{signed(end.worst)}</NoWrap>}
        sub="Customers at their slowest, us paying at our fastest."
        tip="The genuine corner rather than an 'everybody is slow' line — money leaving later is what a week needs less of."
      />
      <MetricCard
        label="Cash envelope"
        value={widest ? <NoWrap>{money(widest.width)}</NoWrap> : "—"}
        sub={widest
          ? `Widest in the week of ${formatDate(widest.startsOn)}. How much room the two corners leave between them.`
          : "No channel: too little has settled to measure how these parties move."}
        tip="The distance between the best and worst corner at its widest point in the horizon. A width, never a probability."
        variance={hasChannel
          ? undefined
          : <StatusChip label="unmeasured" tone="neutral" dense
                        tip="Every timing collapses onto the due dates." />}
      />
      <MetricCard
        label="Next pressure point"
        value={requirement < 0
          ? <NoWrap>{`−${money(Math.abs(requirement))}`}</NoWrap> : "—"}
        sub={pressureWeek
          ? `Deepest in the week of ${formatDate(pressureWeek)}, under the worst timing.`
          : "The committed book does not go below the datum in this horizon."}
        tip="The deepest the running movement goes, under the timing that makes it deepest. An argmin over the cumulative column, not a judgement about it."
      />
    </Box>
  );
}

// ── the sheet ───────────────────────────────────────────────────────────────

/** Where the drawing's ink goes, in one place.
 *
 *  Margins are wide for a chart and correct for a drawing: the flow lanes, the
 *  datum label, the section marks and the title block all live in them, and a
 *  drawing whose annotations overlap its own plot is one nobody trusts. */
const PAD = { top: 26, right: 118, bottom: 84, left: 66 };
/** The margins, once the sheet knows how much room it has.
 *
 *  The right margin exists for the title block, so on a panel with no room for
 *  one it is not a margin, it is wasted plot. Measured rather than guessed at a
 *  breakpoint: a narrow panel on a wide screen has the same problem a phone
 *  does. */
const marginsFor = (room: Room) => ({
  ...PAD,
  right: room.tight ? 14 : PAD.right,
  left: room.cramped ? 48 : PAD.left,
});
/** How much of the plot each flow lane takes. The lanes carry a magnitude by
 *  arrow length, so they need real room; the channel still keeps the middle. */
const LANE = 0.19;
/** The construction grid's pitch, in device pixels. A drawing-sheet grid is a
 *  property of the sheet rather than of the data — it does not move when the
 *  numbers do, which is exactly what makes it read as a sheet. */
const GRID = 22;

/** The sheet's ink, defined once for every drawing on the panel.
 *
 *  In its own zero-sized SVG rather than inside the plan view, because the
 *  cross-sections use the same hatch and the same dimension arrowheads and a
 *  second copy of the definitions would be a second set of ids — which
 *  resolves to whichever one the browser saw first, silently, and only when
 *  both happen to be mounted. */
function DrawingDefs() {
  return (
    <svg className="wcj-defs" width="0" height="0" aria-hidden="true"
         focusable="false">
      <defs>
        <pattern id="wcj-hatch" width="7" height="7"
                 patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="7" className="wcj-hatch-line" />
        </pattern>
        <pattern id="wcj-grid" width={GRID} height={GRID}
                 patternUnits="userSpaceOnUse">
          <path d={`M${GRID},0 L0,0 L0,${GRID}`} className="wcj-grid-line" />
        </pattern>
        {/* One head per direction, each with its own fill. A single head
            taking `currentColor` looks right until you notice it resolves
            against the marker's own colour rather than the line's — which is
            black, on every arrow, in every browser. */}
        <marker id="wcj-arrow-in" viewBox="0 0 8 8" refX="7" refY="4"
                markerWidth="4" markerHeight="4" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 Z" className="wcj-head-in" />
        </marker>
        <marker id="wcj-arrow-out" viewBox="0 0 8 8" refX="7" refY="4"
                markerWidth="4" markerHeight="4" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 Z" className="wcj-head-out" />
        </marker>
        <marker id="wcj-dim" viewBox="0 0 8 8" refX="7" refY="4"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,1 L8,4 L0,7" className="wcj-dim-head" />
        </marker>
      </defs>
    </svg>
  );
}

function JourneyDrawing({
  past, future, room, currency, asOf, horizonEndsOn, observedFrom,
  pressureAt, at, onHover, onPin, sections,
}: {
  past: PastLeg[];
  future: FutureLeg[];
  room: Room;
  currency: string;
  asOf: string;
  horizonEndsOn: string;
  observedFrom: string | null;
  pressureAt: number;
  at: number | null;
  onHover: (at: number | null) => void;
  onPin: (at: number | null) => void;
  sections: number[];
}) {
  const width = room.width;
  const pad = marginsFor(room);
  const H = Math.max(340, Math.min(520, Math.round(width * 0.46)));
  const plotTop = pad.top;
  const plotBottom = H - pad.bottom;
  const plotHeight = plotBottom - plotTop;
  const laneHeight = plotHeight * LANE;
  const routeTop = plotTop + laneHeight;
  const routeBottom = plotBottom - laneHeight;

  const stations = past.length + future.length;
  const x0 = pad.left;
  const x1 = Math.max(x0 + 1, width - pad.right);
  // Station centres, evenly spaced across the sheet. Half a step of margin at
  // each end so the first and last week have room for their own marks.
  const step = (x1 - x0) / Math.max(1, stations);
  const centre = (station: number) => x0 + step * (station + 0.5);
  const pastX = (i: number) => centre(i);
  const futureX = (i: number) => centre(past.length + i);
  // The reference line sits between the last week that has happened and the
  // first that has not — the boundary itself, not either week.
  const datumX = past.length ? centre(past.length) - step / 2 : x0;

  const [low, high] = extentOf(past, future);
  const y = scaleLinear()
    .domain([low, high])
    .range([routeBottom, routeTop])
    .nice();
  const zero = y(0);
  const peak = peakFlow(past, future);
  // Arrow length from amount, linear and shared by both lanes and both halves
  // of the drawing. Linear rather than square-rooted because these are read
  // against each other week by week, not compared across a scatter.
  const arrow = scaleLinear().domain([0, peak])
    .range([0, Math.max(6, laneHeight - 14)]);

  const outline = channelOutline(future, futureX, y, datumX);
  const expectedRoute = routePoints(future.map((f) => f.expected), futureX, y,
                                    { x: datumX, value: 0 });
  const termsRoute = routePoints(future.map((f) => f.onTerms), futureX, y,
                                 { x: datumX, value: 0 });
  const pastRoute = past.length
    ? [...past.map((p, i) => `${pastX(i)},${y(p.movement)}`),
       `${datumX},${zero}`].join(" ")
    : "";

  const selected = at == null ? null : legAt(past, future, at);
  const selectedX = at == null ? null
    : at < 0 ? pastX(past.length + at) : futureX(at);

  // Every week, in drawing order, with the dates thinned to what fits. The
  // shared thinner rather than a modulo written here: it keeps the first and
  // the last, and dropping the final date is how a time axis stops saying
  // where it ends.
  const labelled = [
    ...past.map((leg, i) => ({ leg, x: pastX(i), station: i - past.length })),
    ...future.map((leg, i) => ({ leg, x: futureX(i), station: i })),
  ];
  const dated = thinLabels(labelled, room, 104).map((v) => v !== null);

  // Ticks the drawing dimensions itself against. `nice()` above is what makes
  // them round; the axis is drawn in the sheet's own hand rather than through
  // `ValueAxis`, because a drawing labels its scale on the left rule with tick
  // marks rather than running gridlines under the work.
  const ticks = y.ticks(5);

  return (
    <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`}
         className="viz-svg wcj-sheet" role="presentation">
      {/* The sheet, then the construction grid on it. */}
      <rect x="0" y="0" width={width} height={H} className="wcj-ground" />
      <rect x={x0} y={plotTop} width={Math.max(1, x1 - x0)} height={plotHeight}
            fill="url(#wcj-grid)" />
      <rect x={x0} y={plotTop} width={Math.max(1, x1 - x0)} height={plotHeight}
            className="wcj-frame" />

      {/* The vertical scale: movement either side of the datum, dimensioned on
          the left rule. Zero is drawn heavier — it is the line the whole sheet
          is measured from. */}
      {ticks.map((t) => (
        <g key={t}>
          <line x1={x0 - 5} x2={x0} y1={y(t)} y2={y(t)} className="wcj-tick" />
          <line x1={x0} x2={x1} y1={y(t)} y2={y(t)}
                className={t === 0 ? "wcj-zero" : "wcj-tick-rule"} />
          <text x={x0 - 9} y={y(t) + 3.5} textAnchor="end" className="viz-axis">
            {compactMoney(t, currency)}
          </text>
        </g>
      ))}
      <text x="2" y={plotTop - 10} className="wcj-label">MOVEMENT</text>

      {/* The lanes the flows cross, labelled so the direction is a word rather
          than a hue. Money in enters from above; money out leaves below. */}
      <line x1={x0} x2={x1} y1={routeTop} y2={routeTop} className="wcj-lane-rule" />
      <line x1={x0} x2={x1} y1={routeBottom} y2={routeBottom}
            className="wcj-lane-rule" />
      <text x={x0 + 4} y={plotTop + 11} className="wcj-lane-label">
        RECEIVABLES · IN ▼
      </text>
      <text x={x0 + 4} y={plotBottom - 4} className="wcj-lane-label">
        PAYABLES · OUT ▼
      </text>

      {/* The channel, then the routes inside it. Fill first so no line is
          obscured by the shape it belongs to. */}
      {outline && <path d={outline} className="wcj-channel" />}
      {outline && <path d={outline} className="wcj-channel-hatch"
                        fill="url(#wcj-hatch)" />}
      {outline && (
        <>
          <polyline className="wcj-edge"
                    points={routePoints(future.map((f) => f.best), futureX, y,
                                        { x: datumX, value: 0 })} />
          <polyline className="wcj-edge"
                    points={routePoints(future.map((f) => f.worst), futureX, y,
                                        { x: datumX, value: 0 })} />
        </>
      )}
      {termsRoute && <polyline className="wcj-terms" points={termsRoute} />}
      {expectedRoute && (
        <polyline className="wcj-expected" points={expectedRoute} />
      )}
      {pastRoute && <polyline className="wcj-travelled" points={pastRoute} />}

      {/* Every week's flows, on both sides of the datum. */}
      {past.map((leg, i) => (
        <Flows key={`p${i}`} leg={leg} x={pastX(i)} step={step}
               top={plotTop} bottom={plotBottom} routeTop={routeTop}
               routeBottom={routeBottom} arrow={arrow} committed={false} />
      ))}
      {future.map((leg, i) => (
        <Flows key={`f${i}`} leg={leg} x={futureX(i)} step={step}
               top={plotTop} bottom={plotBottom} routeTop={routeTop}
               routeBottom={routeBottom} arrow={arrow} committed />
      ))}

      {/* The week the datum falls inside is short — it runs to today rather
          than to Sunday — so its arrows are drawn from four days against
          neighbours drawn from seven. Said on the sheet rather than only in
          the tooltip: a quiet-looking column is exactly the shape a reader
          takes at face value. */}
      {!room.cramped && past.map((leg, i) => (leg.partial ? (
        // Anchored to the datum and set leftward, over the column it
        // describes: centred on that column it collides with the datum mark,
        // which is the one annotation on the sheet nothing may sit on.
        <text key={`part${i}`} x={datumX - 9} y={plotTop - 4} textAnchor="end"
              className="wcj-note">part week</text>
      ) : null))}

      {/* The datum. A reference the sheet is dimensioned from, drawn as one:
          chain line, filled triangle, the letter and the date it stands at. */}
      <line x1={datumX} x2={datumX} y1={plotTop - 14} y2={plotBottom + 16}
            className="wcj-datum" />
      <path d={`M${datumX - 6},${plotTop - 14} L${datumX + 6},${plotTop - 14} L${datumX},${plotTop - 4} Z`}
            className="wcj-datum-mark" />
      <text x={datumX} y={plotBottom + 30} textAnchor="middle"
            className="wcj-datum-label">NOW</text>
      {asOf && (
        <text x={datumX} y={plotBottom + 42} textAnchor="middle"
              className="wcj-note">{formatDate(asOf)}</text>
      )}
      <Boat x={datumX} y={zero} />

      {/* Cross-section marks, and the deepest committed week. */}
      {sections.map((index, n) => (
        <g key={index} className="wcj-section-mark">
          <line x1={futureX(index)} x2={futureX(index)}
                y1={plotTop - 6} y2={plotTop + 12} />
          <text x={futureX(index)} y={plotTop - 10} textAnchor="middle"
                className="wcj-label">
            {SECTION_LETTERS[n]}–{SECTION_LETTERS[n]}
          </text>
        </g>
      ))}
      {pressureAt >= 0 && future[pressureAt]?.worst != null && (
        <g className="wcj-pressure">
          <circle cx={futureX(pressureAt)}
                  cy={y(future[pressureAt].worst as number)} r="4.5" />
          <text x={futureX(pressureAt)}
                y={y(future[pressureAt].worst as number) + 17}
                textAnchor="middle" className="wcj-note viz-mark-halo">
            deepest
          </text>
        </g>
      )}

      {/* The selected week: an extension line across the sheet, and — where the
          channel exists there — a dimension line across its width. */}
      {selectedX != null && selected && (
        <g>
          <line x1={selectedX} x2={selectedX} y1={plotTop} y2={plotBottom}
                className="wcj-selected" />
          {selected.kind === "future" && selected.leg.envelope != null && (
            <Dimension
              x={selectedX}
              top={y(selected.leg.best as number)}
              bottom={y(selected.leg.worst as number)}
              label={money(selected.leg.envelope)} />
          )}
        </g>
      )}

      {/* Dates along the bottom rule. Thinned to whatever fits: an unreadable
          label still costs the space it is drawn in. */}
      {labelled.map((entry, n) => {
        const { leg, x, station } = entry;
        return (
          <g key={`t${n}`}>
            {dated[n] && (
              // The end labels are anchored to their own end of the sheet.
              // Centred, the last date runs off the right edge of a narrow
              // panel — and the date the axis ends at is the one worth
              // keeping whole.
              <text x={x} y={plotBottom + 16}
                    textAnchor={n === 0 ? "start"
                      : n === labelled.length - 1 ? "end" : "middle"}
                    className="viz-axis">{formatDate(leg.startsOn)}</text>
            )}
            {/* One hit strip per week, covering the whole sheet height so a
                week can be picked anywhere in its column. Focusable, so the
                callout is reachable without a pointer. */}
            <ChartTip title={<WeekTip leg={leg} committed={station >= 0} />}>
              <rect
                x={x - step / 2} y={plotTop} width={step} height={plotHeight}
                className="cash-hit"
                tabIndex={0}
                role="button"
                aria-label={`Week of ${formatDate(leg.startsOn)}`}
                onFocus={() => onHover(station)}
                onBlur={() => onHover(null)}
                onPointerEnter={() => onHover(station)}
                onPointerLeave={() => onHover(null)}
                onClick={() => onPin(station)}
              />
            </ChartTip>
          </g>
        );
      })}

      {/* The title block. A drawing carries its own provenance in the corner:
          what is drawn, what it is drawn in, and between which two dates. */}
      {!room.tight && (
      <g className="wcj-title-block">
        <rect x={x1 + 8} y={plotTop} width={Math.max(0, width - x1 - 10)}
              height="76" />
        <text x={x1 + 15} y={plotTop + 15} className="wcj-label">CASH CHANNEL</text>
        <text x={x1 + 15} y={plotTop + 30} className="wcj-note">
          {currency} · movement
        </text>
        <text x={x1 + 15} y={plotTop + 45} className="wcj-note">
          {observedFrom ? `from ${formatDate(observedFrom)}` : "no history"}
        </text>
        <text x={x1 + 15} y={plotTop + 60} className="wcj-note">
          {horizonEndsOn ? `to ${formatDate(horizonEndsOn)}` : ""}
        </text>
      </g>
      )}

      {/* The key, at whatever length fits. A legend that runs off the sheet is
          not a shorter legend, it is a truncated sentence. */}
      <text x={x0} y={H - 10} className="viz-axis-note">
        {room.tight
          ? "solid: cash that moved · hatched: the committed channel · datum: now"
          : "solid: cash that moved · hatched: the committed channel, best edge "
            + "above and worst below · dashed: everybody at their own median · "
            + "fine: on their due dates · datum: now"}
      </text>
    </svg>
  );
}

/** One week's money crossing the sheet: invoices above, bills below.
 *
 *  Arrow *length* carries the amount, and the lane carries the direction —
 *  neither depends on colour, which is what keeps the sheet readable in
 *  greyscale and under forced colours. */
function Flows({
  leg, x, step, top, bottom, routeTop, routeBottom, arrow, committed,
}: {
  leg: { inflow: number; outflow: number; startsOn: string;
         inflowDocuments: number; outflowDocuments: number; partial: boolean };
  x: number; step: number;
  top: number; bottom: number; routeTop: number; routeBottom: number;
  arrow: (v: number) => number;
  committed: boolean;
}) {
  const klass = `wcj-flow${committed ? " wcj-flow-committed" : ""}`;
  return (
    <g>
      {leg.inflow > 0 && (
        <ChartTip title={
          <>
            <strong>Money in · week of {formatDate(leg.startsOn)}</strong>
            <br />
            {money(leg.inflow)} across {leg.inflowDocuments}{" "}
            {committed ? "invoice" : "receipt"}
            {leg.inflowDocuments === 1 ? "" : "s"}
            <br />
            <span style={{ opacity: 0.85 }}>
              {committed
                ? "Raised and unpaid, on the date the invoice itself names."
                : "Money that actually arrived, on the day it was received."}
            </span>
          </>
        }>
          <g>
            <line x1={x} x2={x} y1={routeTop - arrow(leg.inflow)} y2={routeTop}
                  className={`${klass} wcj-flow-in`}
                  markerEnd="url(#wcj-arrow-in)" />
            <rect x={x - step / 2} y={top} width={step}
                  height={Math.max(1, routeTop - top)} className="cash-hit" />
          </g>
        </ChartTip>
      )}
      {leg.outflow > 0 && (
        <ChartTip title={
          <>
            <strong>Money out · week of {formatDate(leg.startsOn)}</strong>
            <br />
            {money(leg.outflow)} across {leg.outflowDocuments}{" "}
            {committed ? "bill" : "payment"}
            {leg.outflowDocuments === 1 ? "" : "s"}
            <br />
            <span style={{ opacity: 0.85 }}>
              {committed
                ? "Received and unpaid, on the date the bill itself names."
                : "Money that actually left, on the day it was paid."}
            </span>
          </>
        }>
          <g>
            <line x1={x} x2={x} y1={routeBottom}
                  y2={routeBottom + arrow(leg.outflow)}
                  className={`${klass} wcj-flow-out`}
                  markerEnd="url(#wcj-arrow-out)" />
            <rect x={x - step / 2} y={routeBottom} width={step}
                  height={Math.max(1, bottom - routeBottom)}
                  className="cash-hit" />
          </g>
        </ChartTip>
      )}
    </g>
  );
}

/** A dimension line in the drawing convention: extension lines, arrowheads
 *  turned inward, and the figure sitting in a break in the line. */
function Dimension({ x, top, bottom, label }: {
  x: number; top: number; bottom: number; label: string;
}) {
  const mid = (top + bottom) / 2;
  const room = Math.abs(bottom - top);
  const gap = Math.min(18, room / 2.6);
  return (
    <g className="wcj-dimension">
      <line x1={x - 9} x2={x + 9} y1={top} y2={top} />
      <line x1={x - 9} x2={x + 9} y1={bottom} y2={bottom} />
      {room > 26 ? (
        <>
          <line x1={x} x2={x} y1={top} y2={mid - gap}
                markerStart="url(#wcj-dim)" />
          <line x1={x} x2={x} y1={mid + gap} y2={bottom}
                markerEnd="url(#wcj-dim)" />
          <text x={x + 6} y={mid + 3.5} className="wcj-dim-text viz-mark-halo">
            {label}
          </text>
        </>
      ) : (
        <>
          <line x1={x} x2={x} y1={top} y2={bottom} />
          <text x={x + 8} y={mid - 5} className="wcj-dim-text viz-mark-halo">
            {label}
          </text>
        </>
      )}
    </g>
  );
}

/** The business at the datum, in plan view.
 *
 *  Small, thin-lined and pointed the way time runs. It is the one mark on the
 *  sheet that is not a measurement, and it is drawn as a construction symbol
 *  rather than an illustration — a hull outline and a centreline, in the same
 *  ink as the datum it sits on. */
function Boat({ x, y }: { x: number; y: number }) {
  return (
    <g className="wcj-boat" transform={`translate(${x} ${y})`}
       aria-hidden="true">
      <path d="M25,0 L7,-11 L-15,-11 L-19,-7 L-19,7 L-15,11 L7,11 Z" />
      <line x1="-19" x2="21" y1="0" y2="0" className="wcj-boat-centre" />
      <line x1="-4" x2="-4" y1="-10" y2="10" className="wcj-boat-centre" />
      <line x1="-12" x2="-12" y1="-11" y2="11" className="wcj-boat-centre" />
    </g>
  );
}

// ── the selected-date callout, and the cross-sections ───────────────────────

type Selected =
  | { kind: "past"; leg: PastLeg }
  | { kind: "future"; leg: FutureLeg };

/** Which week a station index addresses. Negative counts back from the datum,
 *  so one number addresses the whole drawing rather than a pair of them. */
function legAt(past: PastLeg[], future: FutureLeg[],
               at: number): Selected | null {
  if (at < 0) {
    const leg = past[past.length + at];
    return leg ? { kind: "past", leg } : null;
  }
  const leg = future[at];
  return leg ? { kind: "future", leg } : null;
}

function WeekTip({ leg, committed }: {
  leg: PastLeg | FutureLeg; committed: boolean;
}) {
  return (
    <>
      <strong>Week of {formatDate(leg.startsOn)}</strong>
      {leg.partial && <> · to the datum</>}
      <br />
      In {money(leg.inflow)} · out {money(leg.outflow)}
      <br />
      Net {signed(leg.net)} this week
      <br />
      <span style={{ opacity: 0.85 }}>
        {committed
          ? "Committed — invoices and bills already raised, not a forecast"
          : "Cash that actually moved"}
      </span>
    </>
  );
}

/** The technical callout for whichever week the reader is on.
 *
 *  Deliberately outside the SVG: a callout box inside the drawing would either
 *  cover the channel or clip at the sheet edge, and its content is text with a
 *  reading order. The leader line is in the drawing; this is the balloon it
 *  points to. */
function Callout({ past, future, at, asOf }: {
  past: PastLeg[]; future: FutureLeg[]; at: number | null; asOf: string;
}) {
  const selected = at == null ? null : legAt(past, future, at);
  if (!selected) return null;
  const { leg } = selected;
  const committed = selected.kind === "future";
  const stated = (v: number | null | undefined, format = signed) =>
    (v == null ? "—" : format(v));
  const facts: [string, string][] = selected.kind === "future"
    ? [
      ["Expected", stated(selected.leg.expected)],
      ["Best", stated(selected.leg.best)],
      ["Worst", stated(selected.leg.worst)],
      ["Envelope", stated(selected.leg.envelope, money)],
      ["On terms", signed(selected.leg.onTerms)],
    ]
    : [
      ["Moved since", signed(selected.leg.movement)],
      ["In", money(leg.inflow)],
      ["Out", money(leg.outflow)],
      ["Net that week", signed(leg.net)],
    ];

  return (
    <div className="wcj-callout">
      <div className="wcj-callout-head">
        <span className="wcj-label">
          {committed ? "COMMITTED" : "TRAVELLED"} · WEEK OF{" "}
          {formatDate(leg.startsOn)}
        </span>
        <span className="viz-muted">
          {committed
            ? `Money already promised, ${plural(leg.inflowDocuments, "invoice")} in and ${plural(leg.outflowDocuments, "bill")} out.`
            : `${plural(leg.inflowDocuments, "receipt")} in and ${plural(leg.outflowDocuments, "payment")} out.`}
          {leg.partial && ` This week runs to the datum on ${formatDate(asOf)}, not to Sunday.`}
        </span>
      </div>
      <dl className="wcj-callout-facts">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const plural = (n: number, noun: string) =>
  `${n} ${noun}${n === 1 ? "" : "s"}`;

/** A–A and B–B: the channel cut across, at two dates in the horizon.
 *
 *  A cross-section answers a question the plan view can only imply — *how much
 *  room is there at this date* — because reading a width off a shape means
 *  comparing two edges at a point by eye, and the answer is in rupees. Cut
 *  where the plan view says they are cut, drawn on one shared scale so A–A and
 *  B–B can be read against each other, and dimensioned rather than shaded.
 *
 *  They carry no verdict. A wide section is not a warning and a narrow one is
 *  not an approval; both are a distance between two measured corners. */
function Sections({ future, indices, currency }: {
  future: FutureLeg[]; indices: number[]; currency: string;
}) {
  const widths = indices
    .map((i) => future[i]?.envelope ?? 0);
  const span = Math.max(1, ...indices.flatMap((i) => {
    const leg = future[i];
    return leg ? [Math.abs(leg.best ?? 0), Math.abs(leg.worst ?? 0)] : [];
  }));
  const W = 154;
  const H = 162;
  const top = 30;
  const bottom = H - 38;
  // One scale across every section, centred on the datum's zero, so the two
  // cuts are comparable. A per-section scale would draw a narrow channel and a
  // wide one identically, which is the one thing these must not do.
  const y = scaleLinear().domain([-span, span]).range([bottom, top]);

  return (
    <Box className="wcj-sections">
      {indices.map((index, n) => {
        const leg = future[index];
        if (!leg || leg.best == null || leg.worst == null) return null;
        const letter = SECTION_LETTERS[n];
        return (
          <figure key={index} className="wcj-section">
            <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
                 className="wcj-sheet wcj-section-svg" role="img"
                 aria-label={`Section ${letter}–${letter} at the week of ${formatDate(leg.startsOn)}: the channel is ${money(widths[n])} wide, between ${money(leg.worst)} and ${money(leg.best)}.`}>
              <line x1="14" x2={W / 2 + 22} y1={y(0)} y2={y(0)}
                    className="wcj-zero" />
              <rect x={W / 2 - 17} y={y(leg.best)} width="34"
                    height={Math.max(1, y(leg.worst) - y(leg.best))}
                    className="wcj-channel" />
              <rect x={W / 2 - 17} y={y(leg.best)} width="34"
                    height={Math.max(1, y(leg.worst) - y(leg.best))}
                    fill="url(#wcj-hatch)" className="wcj-channel-hatch" />
              {leg.expected != null && (
                <line x1={W / 2 - 21} x2={W / 2 + 21}
                      y1={y(leg.expected)} y2={y(leg.expected)}
                      className="wcj-expected" />
              )}
              <Dimension x={W / 2 + 31} top={y(leg.best)} bottom={y(leg.worst)}
                         label={compactMoney(widths[n], currency)} />
              <text x="14" y="15" className="wcj-label">
                {letter}–{letter}
              </text>
              <text x="14" y={H - 16} className="wcj-note">
                {formatDate(leg.startsOn)}
              </text>
            </svg>
            <figcaption className="viz-muted">
              {money(widths[n])} between the corners
            </figcaption>
          </figure>
        );
      })}
    </Box>
  );
}

// ── what is driving it ──────────────────────────────────────────────────────
//
// The same four totals the projection has always reported beside the chart, and
// for the same reason: each is real money that cannot honestly be drawn as a
// mark in a particular week, and a drawing whose parts do not add up to the
// book is one people stop trusting. The evidence note beside them is what says
// whether a narrow channel means punctual parties or thin measurement.

function Driving({ data }: { data: Record<string, unknown> | null }) {
  const overdue = (data?.overdue ?? {}) as Row;
  const undated = (data?.undated ?? {}) as Row;
  const beyond = (data?.beyond_horizon ?? {}) as Row;
  const unscheduled = (data?.unscheduled ?? {}) as Row;
  const unattributed = (data?.unattributed ?? {}) as Row;
  const basis = (data?.basis ?? {}) as Row;
  const actual = ((data?.actual ?? null) as Row | null);
  const before = (actual?.empty_reason
    ? undefined
    : actual?.before_window as Row | undefined);

  const items = [
    { key: "overdue", label: "Already due, not settled",
      why: "Real, and not week-one movement — being overdue is what disproves that.",
      inflow: num(overdue.inflow), outflow: num(overdue.outflow) },
    { key: "beyond", label: `Dated past ${formatDate(data?.horizon_ends_on as string)}`,
      why: "Counted so the parts still add up to the book.",
      inflow: num(beyond.inflow), outflow: num(beyond.outflow) },
    { key: "undated", label: "No terms on record",
      why: "Owed, with no due date to place it. Defaulting one would invent terms nobody gave.",
      inflow: num(undated.inflow), outflow: num(undated.outflow) },
    { key: "orders", label: "Open orders",
      why: "Committed, and carrying no due date — only the invoice or bill that follows has one.",
      inflow: num(unscheduled.open_sales_value),
      outflow: num(unscheduled.open_purchase_value) },
    { key: "before", label: "Moved before this window opened",
      why: "Cash that really moved, older than the sheet. Counted here rather than piled onto its first week, which would draw a spike that never happened.",
      inflow: num(before?.inflow), outflow: num(before?.outflow) },
  ].filter((r) => r.inflow > 0 || r.outflow > 0);

  return (
    <div className="tier3-list">
      <h4>What is driving the journey</h4>
      {items.length > 0 && (
        <ul className="cash-aside">
          {items.map((r) => (
            <li key={r.key}>
              <span className="cash-aside-head">
                <strong>{r.label}</strong>
                <span className="cash-aside-figures">
                  {r.inflow > 0 && <em>{money(r.inflow)} in</em>}
                  {r.outflow > 0 && <em>{money(r.outflow)} out</em>}
                </span>
              </span>
              <span className="viz-muted">{r.why}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="viz-muted viz-footnote">
        The channel's width is measured from{" "}
        {num(basis.customers_measured)} customer
        {num(basis.customers_measured) === 1 ? "" : "s"} and{" "}
        {num(basis.vendors_measured)} supplier
        {num(basis.vendors_measured) === 1 ? "" : "s"} with enough settled
        history to have a rhythm at all. A party without one keeps their money on
        its due date, so a narrow channel can mean punctual parties or thin
        evidence — the shares in the headline say which.
        {num(basis.vendors_retimed) > 0 && (
          <> {num(basis.vendors_retimed)} supplier
          {num(basis.vendors_retimed) === 1 ? "" : "s"} had{" "}
          {money(num(basis.outflow_retimed))} re-dated because the term we
          agreed is not the one the ERP could express — a correction to the due
          date itself, never folded into how late anybody is.</>
        )}
        {(num(unattributed.inflow) > 0 || num(unattributed.outflow) > 0) && (
          <> {money(num(unattributed.inflow) + num(unattributed.outflow))} of
          the committed side belongs to a customer or supplier the contact pull
          did not return. It is real money and it is on the sheet; it simply has
          no name to file it under, which is why the Customers and Suppliers
          screens show less.</>
        )}
      </p>
    </div>
  );
}

// ── the accessible twin ─────────────────────────────────────────────────────

function describe(past: PastLeg[], future: FutureLeg[]): string {
  const travelled = past.length
    ? `Travelled: ${past.map((p) => `week of ${p.startsOn}, in ${p.inflow}, out ${p.outflow}`).join("; ")}. `
    : "";
  const ahead = future.map((f) =>
    `Week of ${f.startsOn}: in ${f.inflow}, out ${f.outflow}, on terms ${f.onTerms}`
    + (f.expected == null ? ""
      : `, expected ${f.expected}, best ${f.best}, worst ${f.worst}`)).join("; ");
  return `${travelled}Committed: ${ahead}`;
}

/** One grid, both halves, with the phase on the row.
 *
 *  Two tables would need a reader to notice that "running total" means
 *  something different in each — measured back from the datum on one side and
 *  forward from it on the other. One table with the phase named on every row
 *  makes that a column they can sort by. */
function JourneyTable({ past, future }: {
  past: PastLeg[]; future: FutureLeg[];
}) {
  const all: Row[] = [
    ...past.map((p) => ({
      phase: "Travelled", starts_on: p.startsOn, inflow: p.inflow,
      outflow: p.outflow, net: p.net, route: p.movement,
      expected: null, best: null, worst: null, envelope: null,
    })),
    ...future.map((f) => ({
      phase: "Committed", starts_on: f.startsOn, inflow: f.inflow,
      outflow: f.outflow, net: f.net, route: f.onTerms,
      expected: f.expected, best: f.best, worst: f.worst,
      envelope: f.envelope,
    })),
  ];
  const optional = (field: string, header: string, tip: string): ColDef<Row> =>
    numeric<Row>(field, header, (v) => signed(v),
                 { width: 150, flex: 0, headerTooltip: tip });

  return (
    <DataGrid<Row>
      ariaLabel="The working capital journey, week by week"
      pageSize={26}
      filters={false}
      rows={all}
      columns={[
        {
          field: "starts_on", headerName: "Week of", width: 140, flex: 0,
          valueFormatter: (p) => (p.value ? formatDate(String(p.value)) : "—"),
        },
        { field: "phase", headerName: "Phase", width: 130, flex: 0,
          headerTooltip: "Travelled is cash that moved; committed is money "
            + "already promised and not yet moved." },
        numeric<Row>("inflow", "Money in", (v) => money(v),
                     { width: 150, flex: 0 }),
        numeric<Row>("outflow", "Money out", (v) => money(v),
                     { width: 150, flex: 0 }),
        numeric<Row>("net", "Net", (v) => signed(v), { width: 140, flex: 0 }),
        numeric<Row>("route", "Movement", (v) => signed(v), {
          width: 160, flex: 0,
          headerTooltip: "Measured from the datum in both directions — back "
            + "from it on a travelled week, forward from it on a committed "
            + "one. Movement, never a balance.",
        }),
        optional("expected", "Expected",
                 "Every party at their own median days-late."),
        optional("best", "Best",
                 "Customers at their fastest, us paying at our slowest."),
        optional("worst", "Worst",
                 "Customers at their slowest, us paying at our fastest."),
        numeric<Row>("envelope", "Envelope", (v) => money(v), {
          width: 150, flex: 0,
          headerTooltip: "The distance between the best and worst corner in "
            + "that week. Blank where the band is not measured.",
        }),
      ]}
    />
  );
}
