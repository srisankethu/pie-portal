// The bond strip: how close each customer and each supplier actually is, and
// how that got to be true.
//
// **This was a radial map first, and the scale test killed it.** Company at the
// centre, radius as bond strength — the metaphor made literal, and genuinely
// lovely at twenty counterparties. At two hundred it is a grey clot. The reason
// is not tuning and cannot be tuned away: bond scores *cluster*. On a synthetic
// book of 200 customers, 78 of the 139 scored sat between 50 and 69, so the
// middle half of the book occupied 36% of the radius — and a circle has only
// about 130px of radius to spend. The suppliers were worse: their middle half
// fitted inside 12%.
//
// A strip fixes exactly that, by construction and not by luck:
//
// **The score gets the full width.** Roughly 1,100px of horizontal resolution
// instead of 130px of radius, so the 50–69 pile-up separates into something a
// person can actually read a rank off.
//
// **A cluster becomes a mound instead of an overlap.** Points that share a
// score stack perpendicular to the axis, so the shape of the book — where the
// mass of your relationships sits — is information you can see. On the radial
// the same cluster was noise, because it was drawn on top of itself.
//
// **The play becomes one axis of motion.** A dot sliding left is an account
// decaying. Two-dimensional drift on a radius was much harder to follow, and
// forced the layout to be recomputed per frame.
//
// **The vertical position is packed once and then held.** It comes from the
// *current* scores and never from the frame being displayed. Repacking per
// frame would make every dot hop rows as its neighbours moved, which reads as
// noise and hides the one thing the play exists to show. So y is identity and
// x is the measure — the trade that makes the animation legible.
//
// **Colour is the only signed thing here**, so it gets the signed palette:
// movement over the window, blue strengthening and red weakening, the pairing
// `tokens.ts` validated for colour-vision deficiency. Position already carries
// strength; colouring by strength too would spend the one signed channel on a
// dimension that is already encoded.
//
// **What is NOT on the strip.** A bond the server declined to score has no
// position, because the left end means "weakest" and an unscored relationship
// is not weak — it is unmeasured. `Patterns.tsx` made the same call for points
// with no margin, and for the same reason: on the axis they read as a measured
// zero. They are counted, named and listed instead.

import Button from "@mui/material/Button";
import { useEffect, useMemo, useRef, useState } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { isAre } from "../format";
import { EntityName } from "../EntityName";
import { ChartTip, InlineLink, StatusChip, Unavailable } from "../kit";
import Autocomplete from "@mui/material/Autocomplete";
import Chip from "@mui/material/Chip";
import TextField from "@mui/material/TextField";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { vizPath } from "../route";
import { Figure, Panel, stateOf } from "./Panel";
import { Seg } from "./Seg";
import { pct, useInsight } from "./useInsight";
import { useMeasure } from "./useMeasure";
// The geometry, and the reading of the frame series. Pure, and tested — the
// marks below depend on two frames rather than one, which is where a playback
// starts telling a story the data does not support.
import {
  MOVEMENT_LOOKBACK, PAD_L, PAD_R, TRAIL_MIN_PTS,
  anchored, counted, firstScored, frameLine, frameStory, laneTone, layout,
  num, packLanes, prepare, rows, signed, sinceFrom, toneOf, xOf,
  type Lane, type Node, type PreparedSide, type Row,
} from "./bonds-layout";

/** The five facets, in the order the score weights them. Named here so the
 *  breakdown and the ledger cannot disagree about what a facet is called. */
const FACETS: [string, string, string][] = [
  ["recency", "On their rhythm",
   "Where they sit in their own buying cycle — not a fixed number of days. A quarterly buyer six weeks in is not late."],
  ["consistency", "Regular",
   "The share of the months they could have traded in that they did, counted from their first document rather than from the start of the window."],
  ["breadth", "Lines taken",
   "How many lines of the business they buy, out of how many there are. Counted by line and not by SKU: twenty cutting-tool items is still one line, and a customer taking four is one a competitor has to beat four times."],
  ["weight", "Material",
   "Their share of this company's book, saturating at a tenth. Concentration is part of a bond, and part of what makes losing one hurt."],
  ["reliability", "Dependable",
   "Customers: the share of datable invoices settled by the due date. Suppliers: the share of their orders not left hanging."],
];

const BAND_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  ANCHORED: "good",
  STEADY: "good",
  LOOSENING: "warn",
  THIN: "neutral",
};


/** Pick the counterparties worth watching, out of a book of two hundred.
 *
 *  **This hides dots; it never restates a score.** The bond score contains the
 *  Material facet — a share of the whole book's revenue — so a score is only
 *  true of the book it was computed against. The caption says so, because a
 *  reader who has narrowed to six names will reasonably wonder whether the
 *  numbers moved with them, and the honest answer is that they must not.
 *
 *  Options are ordered by money, largest first, so the ones most likely wanted
 *  are at the top before anybody types. Grouped by side, because a supplier and
 *  a same-named customer are different rows and picking the wrong one is a
 *  silent mistake.
 */
function WatchPicker({
  options, picked, onChange, topN,
}: {
  options: { id: string; label: string; side: string; money: number }[];
  picked: string[];
  onChange: (ids: string[]) => void;
  topN: (n: number) => string[];
}) {
  if (options.length < 2) return null;
  const chosen = options.filter((o) => picked.includes(o.id));
  return (
    <div className="bond-watch">
      <Autocomplete
        multiple size="small" disableCloseOnSelect
        options={options}
        value={chosen}
        groupBy={(o) => (o.side === "vendor" ? "Suppliers" : "Customers")}
        getOptionLabel={(o) => o.label}
        isOptionEqualToValue={(a, b) => a.id === b.id}
        onChange={(_, v) => onChange(v.map((o) => o.id))}
        renderInput={(params) => (
          <TextField {...params} label="Watch only"
                     placeholder={picked.length ? "" : "Everyone"} />
        )}
        renderOption={(props, o) => (
          <li {...props} key={o.id}>
            {o.label} <span className="viz-muted">· {money(o.money)}</span>
          </li>
        )}
        sx={{ minWidth: 0, flex: "1 1 340px" }}
      />
      {/* "Not all of them are important" usually means "show me the ones that
          are", and typing ten names is a worse answer than a button. */}
      <div className="bond-watch-quick">
        {[10, 25].map((n) => (
          <Chip key={n} size="small" variant="outlined"
                label={`Top ${n} by revenue`}
                onClick={() => onChange(topN(n))} />
        ))}
        {picked.length > 0 && (
          <Chip size="small" label={`Clear (${picked.length})`}
                onClick={() => onChange([])} />
        )}
      </div>
    </div>
  );
}

export function BondsScreen({ session }: { session: PlatformSession }) {
  const [months, setMonths] = useState("24");
  const { data, loading, error, reload } = useInsight(
    "bonds",
    () => papi.bonds(session.token, Number(months)),
    [session.token, months]);

  const customers = (data?.customers as Row | undefined) ?? {};
  const vendors = (data?.vendors as Row | null | undefined) ?? null;
  const supplierSide = Boolean(data?.supplier_side_visible) && vendors !== null;

  // Which halves are on the canvas. A salesperson never has a supplier half —
  // the server omits it — so the control that would switch to it is not
  // rendered rather than rendered disabled.
  const [view, setView] = useState("both");
  // One lane per side, or one per line of the business. Splitting is what makes
  // "my coolant customers are all thin bonds" visible — a sentence with a
  // decision attached — and it costs vertical space, so it is asked for rather
  // than assumed.
  const [group, setGroup] = useState("all");
  const showCustomers = !supplierSide || view !== "suppliers";
  const showSuppliers = supplierSide && view !== "customers";

  const customerBonds = rows(customers.bonds);
  const vendorBonds = rows(vendors?.bonds);

  // One filter over both sides. A connected company is a company whichever
  // direction the money runs, and two controls that mean the same thing is a
  // question about which one is in charge.
  const company = useCompanyFilter([...customerBonds, ...vendorBonds] as Sourced[]);
  const sourcesDiffer = Boolean(data?.sources_differ);

  const frames = rows(customers.frames);
  const vendorFrames = rows(vendors?.frames);
  const frameCount = Math.max(frames.length, vendorFrames.length);

  // The playhead. Starts at the end: the current state is the answer somebody
  // came for, and the history is what they scrub back into.
  const [frame, setFrame] = useState(-1);
  const at = frame < 0 ? frameCount - 1 : Math.min(frame, frameCount - 1);
  useEffect(() => { setFrame(-1); }, [months]);

  const [playing, setPlaying] = useState(false);
  const reduced = usePrefersReducedMotion();
  usePlayhead(playing, frameCount, at, setFrame, () => setPlaying(false));

  const [selected, setSelected] = useState<string | null>(null);
  // Which counterparties to draw. Empty means all of them.
  //
  // **A filter, not a scope**, and here that is forced rather than chosen. The
  // Material facet is a counterparty's share of the *whole book's* trailing
  // revenue, so it carries 15% of every score. Narrow the input and recompute,
  // and six accounts would each look like a sixth of the business — every score
  // inflated, every band meaningless. Doing this in the browser over scores the
  // server already computed is what makes that impossible: nothing here *could*
  // recompute them.
  const [picked, setPicked] = useState<string[]>([]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  // One seam for both narrowings, so the swarm, the play and the ledger cannot
  // disagree about which dots exist.
  const chosenIds = useMemo(() => new Set(picked), [picked]);
  const narrow = useMemo(() => (
    <R extends Sourced>(list: R[]): R[] => {
      const byCompany = company.apply(list);
      if (!chosenIds.size) return byCompany;
      return byCompany.filter(
        (r) => chosenIds.has(String((r as unknown as Row).counterparty_id)));
    }
  ), [company.company, chosenIds]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Indexed once per payload, and deliberately not per frame. Every mark that
  // needs last month — the trail, the arrival count, the band crossing — would
  // otherwise re-scan the whole book for one entry, several times per dot, two
  // and a half times a second.
  const sides = useMemo(() => {
    const out: { side: string; bonds: Row[]; frames: Row[] }[] = [];
    if (showSuppliers) out.push({ side: "vendor", bonds: vendorBonds, frames: vendorFrames });
    if (showCustomers) out.push({ side: "customer", bonds: customerBonds, frames });
    return prepare(out);
  }, [showCustomers, showSuppliers, customerBonds, vendorBonds, frames, vendorFrames]);

  const plotWidth = Math.max(320, room.width || 720);
  // Seats are packed from the *current* scores and deliberately do not depend
  // on `at`. Repacking per frame would make every dot hop rows as its
  // neighbours moved — see the note at the top of the file. The name set is
  // chosen in the same pass and for the same reason.
  const { lanes, seat, labels } = useMemo(
    () => packLanes(sides, narrow, plotWidth,
                    group === "line"),
    [sides, narrow, plotWidth, group]);   // eslint-disable-line react-hooks/exhaustive-deps
  const nodes = useMemo(
    () => layout(sides, at, narrow, seat, plotWidth),
    [sides, at, narrow, seat, plotWidth]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Built from the whole book rather than from what is on screen — a picker
  // that only offered the names already showing could never widen a selection.
  //
  // **Only what the strip can actually draw.** A counterparty below the evidence
  // floor has no score, and one outside the frame cover has no series to
  // animate; either way it can be picked and nothing appears. Offering it would
  // be inviting somebody into a dead end, which is the rule `CompanyFilter`
  // already states about companies with no rows. The first cut of this offered
  // all 227 and drew 201.
  const watchOptions = useMemo(() => {
    const drawable = (bonds: Row[], series: Row[], side: string) => {
      const covered = new Set(
        series.flatMap((f) => rows(f.bonds).map((e) => String(e.counterparty_id))));
      return bonds
        .filter((b) => b.score != null && covered.has(String(b.counterparty_id)))
        .map((b) => ({
          id: String(b.counterparty_id), label: String(b.label), side,
          money: num(b.money),
        }));
    };
    return [
      ...(showSuppliers ? drawable(vendorBonds, vendorFrames, "vendor") : []),
      ...(showCustomers ? drawable(customerBonds, frames, "customer") : []),
    ].sort((a, z) => z.money - a.money);
  }, [customerBonds, vendorBonds, frames, vendorFrames,
      showCustomers, showSuppliers]);

  const topByMoney = (n: number) => watchOptions.slice(0, n).map((o) => o.id);

  const shownCustomers = narrow(customerBonds as Sourced[]) as Row[];
  const shownVendors = narrow(vendorBonds as Sourced[]) as Row[];
  const ledger = [...(showCustomers ? shownCustomers : []),
                  ...(showSuppliers ? shownVendors : [])];
  const unscored = ledger.filter((b) => b.score == null);
  // Named because the sentence below needs it twice — once as the number and
  // once to agree with it. Counting inline in both places is how the two got
  // out of step in the first place.
  const overdueCount = ledger.filter((b) => b.overdue).length;
  const frameLabel = String(
    (frames[at] ?? vendorFrames[at])?.label ?? data?.as_of ?? "");

  const chosen = ledger.find((b) => String(b.counterparty_id) === selected) ?? null;

  // What changed between last frame and this one, as a sentence. Counts and
  // names only — the same construct as `anchored()` and `counted()` above, and
  // deliberately not a median or a percentile, which are commercial statistics
  // and belong in `commercial/` stamped with a thresholds version.
  //
  // This is the channel that carries the play to a reader who cannot use any of
  // the others: it survives a screenshot, it is the whole story for someone
  // scrubbing under reduced motion with no Play button, and you cannot watch
  // two hundred dots for the three that crossed a band — a counter you can
  // read, a flash you cannot.
  const bands = rows(customers.bands);
  const story = useMemo(
    () => frameStory(nodes, sides, at, bands),
    [nodes, sides, at, bands]);
  const sentence = nodes.length ? frameLine(story, frameLabel, bands) : "";

  return (
    <Panel
      title="Relationship bonds"
      question="Who is actually close to this book — and who is drifting out"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          {supplierSide && (
            <Seg label="Show" value={view} onChange={setView}
                 options={[["both", "Both"], ["customers", "Customers"],
                           ["suppliers", "Suppliers"]]} />
          )}
          <Seg label="Group" value={group} onChange={setGroup}
               options={[["all", "Together"], ["line", "By line"]]} />
          <Seg label="Window" value={months} onChange={setMonths}
               options={[["12", "1y"], ["24", "2y"], ["36", "3y"]]} />
        </div>
      }
    >
      <p className="viz-headline">
        {counted(shownCustomers, "customer")}
        {showSuppliers && <>, {counted(shownVendors, "supplier")}</>}
        {" "}scored on five measured facets.{" "}
        {anchored(ledger) > 0 && (
          <><strong>{anchored(ledger)}</strong>{" "}
            {isAre(anchored(ledger))} anchored; </>
        )}
        <strong>{overdueCount}</strong> {isAre(overdueCount)} past their
        own buying rhythm.
      </p>

      <CompanyFilter options={company.options} value={company.company}
                     onChange={company.setCompany} show={company.show} />

      <WatchPicker options={watchOptions} picked={picked} onChange={setPicked}
                   topN={topByMoney} />
      {picked.length > 0 && (
        <p className="bond-unscored">
          Showing <strong>{picked.length}</strong> of {watchOptions.length}.
          Every score is unchanged — a bond is scored against the whole book,
          and one of its five facets is this counterparty's share of it, so
          narrowing hides dots rather than re-scoring the ones that are left.
        </p>
      )}

      <div ref={ref} className="bond-stage">
        <Figure
          caption={
            // Every mark on the canvas is named here, including the two
            // thresholds. A deadband nobody is told about is a claim: a grey
            // dot has not necessarily held still, and a dot with no trail has
            // not necessarily stayed put.
            `Left to right is bond strength, 0–100. Dot size is total `
            + `${showSuppliers && !showCustomers ? "spend" : "revenue"} and `
            + `does not change as the play runs. Colour is movement over the `
            + `last ${MOVEMENT_LOOKBACK} months: blue strengthening, red `
            + `weakening, grey no material move. A dashed amber ring means `
            + `there is no score ${MOVEMENT_LOOKBACK} months back to compare `
            + `against — so that grey is "not measurable" rather than "not `
            + `moving". A short line behind a dot is where it stood last `
            + `month, drawn where the month's move is at least `
            + `${TRAIL_MIN_PTS} points. Seats are packed once from today's `
            + `scores and then held, so the mound is the shape of the book as `
            + `it stands now — not as it stood in the frame you are watching.`
            + (group === "line"
              ? ` Lines are each counterparty's dominant line today, held `
                + `across the play.`
              : "")}
          // The sentence, not a clause per node. The old summary rebuilt a
          // multi-thousand-character label two and a half times a second, which
          // is not something anybody could listen to. Per-node detail is still
          // reachable — the table below is a sibling of the figure, not inside
          // the element that carries `role="img"`.
          summary={sentence || "No scored relationship in this window."}
          table={<BondTable nodes={nodes} />}
        >
          {/* Only populated sides get a lane — an empty supplier band would
              read as "this business has no suppliers", which is never what it
              means. The emptiness is stated in words underneath instead. */}
          <BondStrip nodes={nodes} lanes={lanes} bands={bands}
                     width={plotWidth} reduced={reduced} selected={selected}
                     labels={labels} sides={sides} at={at} cramped={room.cramped}
                     scrubbed={at < frameCount - 1}
                     onSelect={(id) => setSelected(id === selected ? null : id)} />
        </Figure>

        {/* Between the strip and the scrubber: it reads as a caption to the
            frame the reader is looking at, and it is the last thing their eye
            passes on the way to the control that changes it. */}
        {sentence && <p className="bond-frame-line">{sentence}</p>}

        <Timeline
          count={frameCount} at={at} label={frameLabel}
          playing={playing} reduced={reduced}
          onScrub={setFrame} onToggle={() => setPlaying((p) => !p)}
        />

        {/* An empty frame in the middle of a populated book is not a blank
            chart, it is an answer: nobody had enough history to be scored yet.
            Scrubbing to 2024 on a book that started trading in 2026 otherwise
            shows an empty circle and no reason for it — the exact failure
            `Panel`'s empty state exists to prevent, arriving through the
            playhead instead of through the request. */}
        {nodes.length === 0 && ledger.some((b) => b.score != null) && (
          <p className="bond-unscored">
            Nothing was scored in <strong>{frameLabel}</strong> — no
            relationship had reached {num(customers.min_documents)} documents by
            then.{" "}
            {firstScored(frames, vendorFrames) != null && (
              <InlineLink onClick={() => setFrame(firstScored(frames, vendorFrames) as number)}>
                Jump to the first month with a bond
              </InlineLink>
            )}
          </p>
        )}
      </div>

      {/* An empty supplier half says why it is empty. A blank half-circle beside
          a populated one reads as "this business has no suppliers", which is
          never what it means. */}
      {showSuppliers && !shownVendors.length && vendors?.empty_reason != null && (
        <p className="bond-unscored">
          <strong>No supplier bonds.</strong>{" "}
          <span className="viz-muted">{String(vendors.empty_reason)}</span>
        </p>
      )}

      {unscored.length > 0 && (
        <p className="bond-unscored">
          <strong>{unscored.length}</strong>{" "}
          {unscored.length === 1 ? "relationship is" : "relationships are"} not on
          the map: fewer than {num(customers.min_documents)} documents on record,
          which is not enough to describe a rhythm. They are listed below with
          the reason — an unmeasured tie is not a weak one, so it gets no
          position rather than a place on the rim.
        </p>
      )}

      {chosen && <FacetBreakdown bond={chosen} sourcesDiffer={sourcesDiffer}
                                 onClose={() => setSelected(null)} />}

      <Unavailable items={rows(data?.unavailable)} verb="not in the score" />

      <div className="tier3-list">
        <h4>The ledger</h4>
        <DataGrid<Row>
          ariaLabel="Relationship bonds"
          rows={ledger}
          pageSize={15}
          twoLineRows
          getRowId={(r) => `${r.side}:${String(r.counterparty_id)}`}
          onRowClick={(r) => setSelected(String(r.counterparty_id))}
          columns={[
            {
              field: "label", headerName: "Counterparty", flex: 1, minWidth: 220,
              filter: "agTextColumnFilter",
              cellRenderer: (p: { data: Row }) => (
                <EntityName name={String(p.data.label)}
                            origin={p.data.origin as EntityOrigin | undefined}
                            show={sourcesDiffer} strong={false} />
              ),
            },
            {
              field: "band", headerName: "Bond", width: 130, flex: 0,
              cellRenderer: (p: { data: Row }) =>
                p.data.band
                  ? <StatusChip label={String(p.data.band).toLowerCase()}
                                tone={BAND_TONE[String(p.data.band)] ?? "neutral"}
                                dense />
                  : <StatusChip label="not scored" tone="neutral" dense
                                tip={String(p.data.unscored_reason ?? "")} />,
            },
            numeric<Row>("score", "Score",
                         (v) => (v == null ? "—" : v.toFixed(0)),
                         { width: 100, flex: 0 }),
            numeric<Row>("money", "Traded", (v) => money(v),
                         { width: 140, flex: 0 }),
            numeric<Row>("distinct_items", "Items", (v) => String(v),
                         { width: 100, flex: 0 }),
            {
              field: "typical_interval_days", headerName: "Rhythm", width: 130, flex: 0,
              valueFormatter: (p: { value: unknown }) =>
                p.value == null ? "—" : `~${Number(p.value).toFixed(0)}d`,
            },
            {
              field: "last_traded", headerName: "Last traded", width: 150, flex: 0,
              cellRenderer: (p: { data: Row }) => (
                <span className={p.data.overdue ? "bond-late" : undefined}>
                  {p.data.last_traded ? formatDate(String(p.data.last_traded)) : "—"}
                </span>
              ),
            },
          ]}
          empty={<p className="viz-muted">Nothing traded in this window.</p>}
        />
      </div>
    </Panel>
  );
}

// ── the strip ───────────────────────────────────────────────────────────────
function BondStrip({
  nodes, lanes, bands, width, reduced, selected, labels, sides, at, cramped,
  scrubbed, onSelect,
}: {
  nodes: Node[];
  lanes: Lane[];
  bands: Row[];
  width: number;
  reduced: boolean;
  selected: string | null;
  /** `side:id` keys allowed to carry a name. Fixed for the whole play. */
  labels: Set<string>;
  sides: PreparedSide[];
  at: number;
  cramped: boolean;
  /** The playhead is behind the present, so per-frame money is a running total
   *  rather than the current one. */
  scrubbed: boolean;
  onSelect: (id: string) => void;
}) {
  const HEAD = 20;
  // Each lane is exactly as tall as its own tallest stack. A fixed height
  // would either clip the crowded lane or leave the sparse one mostly empty.
  const tops: number[] = [];
  let y = AXIS_H;
  for (const lane of lanes) {
    tops.push(y + HEAD + lane.half);
    y += HEAD + lane.half * 2 + 14;
  }
  const height = Math.max(y, AXIS_H + 80);

  // The band edges, straight from the server's own legend — so the regions on
  // the axis and the chips in the ledger can never disagree about where
  // "steady" starts. Ascending, for drawing left to right.
  const edges = [...bands]
    .map((b) => ({ at: num(b.min_score), label: String(b.band).toLowerCase() }))
    .sort((a, b) => a.at - b.at);

  const chosen = nodes.find((n) => n.id === selected) ?? null;
  const sinceX = chosen
    ? sinceFrom(sides.find((s) => s.side === chosen.side)?.index.get(chosen.id),
                at, width)
    : null;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height}
         className={`bond-strip${reduced ? " bond-still" : ""}`
                    + (selected ? " bond-focus" : "")}>
      {/* Band regions, labelled. This replaces the radial's rings and is
          strictly better: a reader sees which band a dot is in without
          measuring anything, and the boundaries are policy rather than
          round numbers somebody liked. */}
      <g aria-hidden="true">
        {edges.map((e, i) => {
          const x0 = xOf(e.at, width);
          const x1 = xOf(edges[i + 1]?.at ?? 100, width);
          return (
            <g key={e.label}>
              {i > 0 && (
                <line x1={x0} x2={x0} y1={AXIS_H - 6} y2={height}
                      className="bond-edge" />
              )}
              <text x={(x0 + x1) / 2} y={14} textAnchor="middle"
                    className="bond-band-label">{e.label}</text>
            </g>
          );
        })}
        {[0, 25, 50, 75, 100].map((s) => (
          <text key={s} x={xOf(s, width)} y={AXIS_H - 8} textAnchor="middle"
                className="viz-axis">{s}</text>
        ))}
      </g>

      {/* The lane heading carries this frame's weather. It is the only channel
          that answers a question about a *population* — which line of the
          business is decaying — which no per-dot mark can, because nobody can
          count a mound of red by looking at it. `unmeasured` is called out
          separately from flat because it is a subset of it: that overlap is the
          hole the dashed ring fills, said again in words. */}
      {lanes.map((lane, i) => {
        const tone = laneTone(nodes, i);
        const weather = [
          tone.down ? `${tone.down} weakening` : "",
          tone.up ? `${tone.up} strengthening` : "",
          tone.unmeasured ? `${tone.unmeasured} not yet measurable` : "",
        ].filter(Boolean).join(" · ");
        return (
          <g key={`${lane.side}${lane.label}`}>
            <text x={2} y={tops[i] - lane.half - 6} className="bond-lane-label">
              {lane.label} ({lane.count})
              {weather && <tspan className="bond-lane-weather"> · {weather}</tspan>}
            </text>
            <line x1={PAD_L} x2={width - PAD_R} y1={tops[i]} y2={tops[i]}
                  className="bond-spine" aria-hidden="true" />
          </g>
        );
      })}

      {/* Beneath the dots, and outside the tooltips: a trail is scenery for the
          mark that owns it, not a thing to hover. One segment from last month's
          position to this one, so both ends are measured — a taper or a fading
          comet would assert a path between monthly samples that nobody
          sampled. */}
      <g aria-hidden="true">
        {nodes.map((n) => (n.trail == null ? null : (
          <line key={`t${n.side}${n.id}`}
                x1={n.trail} x2={n.x}
                y1={tops[n.lane] + n.y} y2={tops[n.lane] + n.y}
                strokeWidth={Math.max(1, n.r * 0.35)}
                className="bond-trail" />
        )))}
      </g>

      {/* Where the selected dot stood six months ago — the number the tooltip
          already speaks, drawn. Hidden in exactly the case the dashed ring
          marks, so it is never a line back to a month nothing was measured
          in. */}
      {chosen && sinceX != null && (
        <g aria-hidden="true">
          <line x1={sinceX} x2={chosen.x}
                y1={tops[chosen.lane] + chosen.y} y2={tops[chosen.lane] + chosen.y}
                className="bond-since" />
          <line x1={sinceX} x2={sinceX}
                y1={tops[chosen.lane] + chosen.y - 5}
                y2={tops[chosen.lane] + chosen.y + 5}
                className="bond-since" />
        </g>
      )}

      {nodes.map((n) => {
        const on = selected === n.id;
        return (
          <ChartTip
            key={`${n.side}${n.id}`}
            title={`${n.label} — ${n.score.toFixed(0)}/100, ${n.band.toLowerCase()}. `
              // "by then" while scrubbed, because this figure is revenue
              // accumulated to the displayed month, not the lifetime total the
              // dot is sized by. Two definitions of money on one mark, so the
              // one being spoken has to say which it is.
              + `${money(n.money)} traded${scrubbed ? " by then" : ""}.`
              + (n.movement == null
                ? ` No score ${MOVEMENT_LOOKBACK} months back to compare with.`
                : ` ${signed(n.movement)} over ${MOVEMENT_LOOKBACK} months.`)
              + (n.overdue ? " Past their own buying rhythm." : "")}
          >
            <circle
              cx={n.x} cy={tops[n.lane] + n.y} r={n.r + (on ? 3 : 0)}
              className={`bond-node ${toneOf(n.movement)}`
                + (n.movement == null ? " bond-fresh" : "")
                + (on ? " bond-on" : "")}
              tabIndex={0}
              role="button"
              aria-label={`${n.label}, bond ${n.score.toFixed(0)} of 100`}
              onClick={() => onSelect(n.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect(n.id); }}
            />
          </ChartTip>
        );
      })}

      {/* Names, last so they sit above everything, and on the largest marks
          only. The strip had no identity on it at all until now: every name
          needed a hover onto a target moving two and a half times a second, and
          the per-circle labels are inside the element carrying `role="img"`, so
          a screen reader never reached them either. Naming the dozen dots that
          carry the money turns "a big one is sliding" into "*Ace Designers* is
          sliding", which is the difference between a picture and a decision.

          Dropped entirely when there is no room for them, following the same
          rule the quadrant labels use. */}
      {!cramped && (
        <g aria-hidden="true">
          {nodes.map((n) => {
            if (!labels.has(`${n.side}:${n.id}`)) return null;
            const right = n.x > width * 0.75;
            return (
              <text key={`n${n.side}${n.id}`}
                    x={right ? n.x - n.r - 4 : n.x + n.r + 4}
                    y={tops[n.lane] + n.y + 3.5}
                    textAnchor={right ? "end" : "start"}
                    className="bond-name viz-mark-halo">
                {n.label.length > 22 ? `${n.label.slice(0, 21)}…` : n.label}
              </text>
            );
          })}
        </g>
      )}
    </svg>
  );
}

const AXIS_H = 34;

// ── the play ────────────────────────────────────────────────────────────────
function Timeline({
  count, at, label, playing, reduced, onScrub, onToggle,
}: {
  count: number; at: number; label: string; playing: boolean; reduced: boolean;
  onScrub: (i: number) => void; onToggle: () => void;
}) {
  if (count <= 1) return null;
  return (
    <div className="bond-timeline">
      {/* Autoplay is the thing reduced-motion is actually about here: the
          scrubber still works, because moving the playhead is the reader's own
          action and not motion imposed on them. */}
      {!reduced && (
        <Button type="button" size="small" variant="outlined" onClick={onToggle}
                aria-label={playing ? "Pause" : "Play the last two years"}>
          {playing ? "Pause" : "Play"}
        </Button>
      )}
      <input
        type="range" min={0} max={count - 1} value={at}
        aria-label="Month"
        onChange={(e) => onScrub(Number(e.target.value))}
      />
      <span className="bond-month">{label}</span>
      {at < count - 1 && (
        <InlineLink onClick={() => onScrub(count - 1)}>Back to now</InlineLink>
      )}
    </div>
  );
}

/** Advance the playhead while playing, and stop at the end rather than looping.
 *
 *  A loop is the wrong default for a history: it restarts without saying so, and
 *  a reader who looks away comes back unable to tell which pass they are in. */
function usePlayhead(
  playing: boolean, count: number, at: number,
  set: (i: number) => void, stop: () => void,
) {
  const ref = useRef({ at, count, set, stop });
  ref.current = { at, count, set, stop };
  useEffect(() => {
    if (!playing || count <= 1) return;
    // Restart from the beginning when play is pressed at the end, so the
    // button always does something.
    if (ref.current.at >= count - 1) ref.current.set(0);
    const id = window.setInterval(() => {
      const { at: now, count: n, set: go, stop: end } = ref.current;
      if (now >= n - 1) { end(); return; }
      go(now + 1);
    }, 420);
    return () => window.clearInterval(id);
  }, [playing, count]);
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return Boolean(reduced);
}

// ── the breakdown ───────────────────────────────────────────────────────────
function FacetBreakdown({
  bond, sourcesDiffer, onClose,
}: {
  bond: Row; sourcesDiffer: boolean;
  onClose: () => void;
}) {
  const facets = (bond.facets as Record<string, number | null>) ?? {};
  const missing = (bond.missing_facets as string[] | undefined) ?? [];
  const detail = (bond.detail as Record<string, unknown>) ?? {};
  const isCustomer = bond.side === "customer";

  return (
    <div className="bond-detail">
      <div className="bond-detail-head">
        <h4>
          <EntityName name={String(bond.label)}
                      origin={bond.origin as EntityOrigin | undefined}
                      show={sourcesDiffer} strong />
        </h4>
        <div>
          {bond.band != null && (
            <StatusChip label={String(bond.band).toLowerCase()}
                        tone={BAND_TONE[String(bond.band)] ?? "neutral"} />
          )}
          {isCustomer && (
            <InlineLink to={vizPath(`customer/${String(bond.counterparty_id)}`)}>
              Open the account
            </InlineLink>
          )}
          <InlineLink onClick={onClose}>Close</InlineLink>
        </div>
      </div>

      {bond.score == null ? (
        <p className="viz-muted">{String(bond.unscored_reason ?? "")}</p>
      ) : (
        <>
          <p className="viz-muted">
            {money(num(bond.money))} across {num(bond.documents)} document
            {num(bond.documents) === 1 ? "" : "s"} and {num(bond.distinct_items)} item
            {num(bond.distinct_items) === 1 ? "" : "s"}, since{" "}
            {bond.first_traded ? formatDate(String(bond.first_traded)) : "—"}.
            {bond.overdue ? " Past their own buying rhythm." : ""}
          </p>
          {/* The decomposition is the point. A score shown without it is the
              black box this whole module was built to avoid. */}
          <ul className="bond-facets">
            {FACETS.map(([key, label, meaning]) => {
              const v = facets[key];
              return (
                <li key={key}>
                  <span className="bond-facet-label" title={meaning}>{label}</span>
                  <span className="bond-facet-track">
                    {v != null && (
                      <span className="bond-facet-fill" style={{ width: `${v * 100}%` }} />
                    )}
                  </span>
                  <span className="bond-facet-value">
                    {v == null ? "not measured" : pct(v, 0)}
                  </span>
                </li>
              );
            })}
          </ul>
          {missing.length > 0 && (
            <p className="viz-muted">
              Scored on the {5 - missing.length} facet
              {5 - missing.length === 1 ? "" : "s"} that could be measured.{" "}
              {missing.join(", ")} {isAre(missing.length)} unknown
              for this relationship — which is not the same as being poor, so
              the score does not count it as one.
            </p>
          )}
          {bond.overdue_payable != null && num(bond.overdue_payable) > 0 && (
            <p className="viz-muted">
              We owe them {money(num(bond.overdue_payable))} past its due date.
              Shown here rather than inside the score: a bill carries a balance
              and no payment date, so it cannot be reconstructed for a past
              month, and a facet that meant something different in each frame
              would make the play misleading.
            </p>
          )}
          {typeof detail.median_days_to_pay === "number" && (
            <p className="viz-muted">
              Typically pays in {Math.round(detail.median_days_to_pay)} days.
            </p>
          )}
        </>
      )}
    </div>
  );
}


/** The chart's table fallback. A `<table>` is right here for exactly the reason
 *  `ui-standards` §13 gives — it is the accessible twin of one figure, not the
 *  screen's list of the business. The ledger below is the grid.
 *
 *  It carries `.viz-table` rather than `.grid`, which stopped existing with the
 *  Quote Builder migration and survives only as a tombstone comment in
 *  `styles.css` — so this rendered with browser defaults and no tabular
 *  numerals, in the one place a reader who cannot use the chart has to read
 *  numbers. `BookFlow.tsx` and `Mix.tsx` still name the dead class; they are
 *  not on this change's path, and are written down instead. */
function BondTable({ nodes }: { nodes: Node[] }) {
  return (
    <table className="viz-table">
      <thead>
        <tr>
          <th>Counterparty</th><th>Score</th><th>Bond</th>
          <th>Traded</th><th>Movement</th><th>This month</th>
        </tr>
      </thead>
      <tbody>
        {nodes.map((n) => (
          <tr key={`${n.side}${n.id}`}>
            <td>{n.label}</td>
            <td>{n.score.toFixed(0)}</td>
            <td>{n.band.toLowerCase()}</td>
            <td>{money(n.money)}</td>
            {/* Not a bare dash. "No comparison exists" and "did not move" are
                the same grey on the canvas until the ring says otherwise, and
                they were the same "—" here. */}
            <td>{n.movement == null ? "not yet measurable" : signed(n.movement)}</td>
            <td>{n.trail == null ? "no material move" : "moved"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

