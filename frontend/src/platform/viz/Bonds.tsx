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
import Autocomplete from "@mui/material/Autocomplete";
import Chip from "@mui/material/Chip";
import TextField from "@mui/material/TextField";
import { useEffect, useMemo, useRef, useState } from "react";
import { scaleSqrt } from "d3-scale";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { ChartTip, InlineLink, StatusChip } from "../kit";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { Figure, Panel, stateOf } from "./Panel";
import { Seg } from "./Seg";
import { pct, useInsight } from "./useInsight";
import { useMeasure } from "./useMeasure";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

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

/** How far back movement is read, in frames. Six months is long enough that a
 *  seasonal dip does not read as a decaying relationship, and short enough that
 *  a real slide shows up before it is a recovery job. */
const MOVEMENT_LOOKBACK = 6;

/** A plotted node. Everything the strip needs, resolved once per frame.
 *
 *  `y` and `lane` are the counterparty's fixed seat and do not depend on the
 *  frame; `x`, `r`, `score` and `movement` do. That split is what makes the
 *  playback readable. */
interface Node {
  id: string;
  label: string;
  sector: string;
  score: number;
  money: number;
  movement: number | null;
  band: string;
  side: string;
  origin?: EntityOrigin;
  overdue: boolean;
  lane: number;
  x: number;
  y: number;
  r: number;
}


/** Pick the counterparties worth watching, out of a book of two hundred.
 *
 *  **This hides dots; it never restates a score.** The bond score contains the
 *  Material facet — a share of the whole book's revenue — so a score is only
 *  true of the book it was computed against. The caption says so, because a
 *  reader who has narrowed to six names will reasonably wonder whether the
 *  numbers moved with them, and the honest answer is that they must not.
 *
 *  Options are ordered by money, largest first, so the ones most likely wanted
 *  are at the top before anybody types. Grouped by side, because "Kennametal"
 *  as a supplier and a same-named customer are different rows and picking the
 *  wrong one is a silent mistake.
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
        sx={{ minWidth: 340, flex: "1 1 340px" }}
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

export function BondsScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
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
  // **A filter, not a scope**, and on this screen that is forced rather than
  // chosen. The Material facet is a counterparty's share of the *whole book's*
  // trailing revenue, so it carries 15% of every score. Narrow the input and
  // recompute, and six accounts would each look like a sixth of the business —
  // every score inflated, the bands meaningless. Doing this in the browser over
  // scores the server already computed is what makes that impossible: there is
  // nothing here that *could* recompute them.
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

  const sides = useMemo(() => {
    const out: { side: string; bonds: Row[]; frames: Row[] }[] = [];
    if (showSuppliers) out.push({ side: "vendor", bonds: vendorBonds, frames: vendorFrames });
    if (showCustomers) out.push({ side: "customer", bonds: customerBonds, frames });
    return out;
  }, [showCustomers, showSuppliers, customerBonds, vendorBonds, frames, vendorFrames]);

  const plotWidth = Math.max(320, room.width || 720);
  // Seats are packed from the *current* scores and deliberately do not depend
  // on `at`. Repacking per frame would make every dot hop rows as its
  // neighbours moved — see the note at the top of the file.
  const { lanes, seat } = useMemo(
    () => packLanes(sides, narrow, plotWidth, group === "line"),
    [sides, narrow, plotWidth, group]);   // eslint-disable-line react-hooks/exhaustive-deps
  const nodes = useMemo(
    () => layout(sides, at, narrow, seat, plotWidth),
    [sides, at, narrow, seat, plotWidth]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Built from the whole book rather than from what is on screen — a picker
  // that only offered the names already showing could never widen a selection.
  //
  // **Only what the strip can actually draw.** A counterparty below the
  // evidence floor has no score, and one outside the frame cover has no series
  // to animate; either way it can be picked and nothing appears. Offering it
  // would be inviting somebody into a dead end, which is the rule
  // `CompanyFilter` already states about companies with no rows. The first cut
  // of this offered all 227 and drew 201.
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
  const frameLabel = String(
    (frames[at] ?? vendorFrames[at])?.label ?? data?.as_of ?? "");

  const overdueCount = ledger.filter((b) => b.overdue).length;
  const chosen = ledger.find((b) => String(b.counterparty_id) === selected) ?? null;

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
        {/* The verb agrees with its own count. Invisible while the strip always
            showed two hundred; "1 are anchored" the moment somebody watches a
            single account, which is exactly when they are reading closely. */}
        {anchored(ledger) > 0 && (
          <><strong>{anchored(ledger)}</strong>{" "}
            {anchored(ledger) === 1 ? "is" : "are"} anchored; </>
        )}
        <strong>{overdueCount}</strong> {overdueCount === 1 ? "is" : "are"} past
        {" "}their own buying rhythm.
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
            `Left to right is bond strength, 0–100. Dots stack where scores `
            + `cluster, so the mound is where most of the book sits. Dot size `
            + `is ${showSuppliers && !showCustomers ? "spend" : "revenue"}. `
            + `Colour is movement over the last ${MOVEMENT_LOOKBACK} months: `
            + `blue strengthening, red weakening.`}
          summary={nodes.map((n) =>
            `${n.label}: ${n.score.toFixed(0)} of 100, ${n.band.toLowerCase()}, ${money(n.money)}`)
            .join(". ") || "No scored relationship in this window."}
          table={<BondTable nodes={nodes} />}
        >
          {/* Only populated sides get a lane — an empty supplier band would
              read as "this business has no suppliers", which is never what it
              means. The emptiness is stated in words underneath instead. */}
          <BondStrip nodes={nodes} lanes={lanes} bands={rows(customers.bands)}
                     width={plotWidth} reduced={reduced} selected={selected}
                     onSelect={(id) => setSelected(id === selected ? null : id)} />
        </Figure>

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
                                 onOpen={onNavigate} onClose={() => setSelected(null)} />}

      <Unavailable items={rows(data?.unavailable)} />

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
  nodes, lanes, bands, width, reduced, selected, onSelect,
}: {
  nodes: Node[];
  lanes: Lane[];
  bands: Row[];
  width: number;
  reduced: boolean;
  selected: string | null;
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

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height}
         className={`bond-strip${reduced ? " bond-still" : ""}`}>
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

      {lanes.map((lane, i) => (
        <g key={lane.side}>
          <text x={2} y={tops[i] - lane.half - 6} className="bond-lane-label">
            {lane.label} ({lane.count})
          </text>
          <line x1={PAD_L} x2={width - PAD_R} y1={tops[i]} y2={tops[i]}
                className="bond-spine" aria-hidden="true" />
        </g>
      ))}

      {nodes.map((n) => {
        const on = selected === n.id;
        return (
          <ChartTip
            key={`${n.side}${n.id}`}
            title={`${n.label} — ${n.score.toFixed(0)}/100, ${n.band.toLowerCase()}. `
              + `${money(n.money)} traded.`
              + (n.movement == null ? "" : ` ${signed(n.movement)} over ${MOVEMENT_LOOKBACK} months.`)
              + (n.overdue ? " Past their own buying rhythm." : "")}
          >
            <circle
              cx={n.x} cy={tops[n.lane] + n.y} r={n.r + (on ? 3 : 0)}
              className={`bond-node ${toneOf(n.movement)}${on ? " bond-on" : ""}`}
              tabIndex={0}
              role="button"
              aria-label={`${n.label}, bond ${n.score.toFixed(0)} of 100`}
              onClick={() => onSelect(n.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect(n.id); }}
            />
          </ChartTip>
        );
      })}
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
  bond, sourcesDiffer, onOpen, onClose,
}: {
  bond: Row; sourcesDiffer: boolean;
  onOpen: (r: string) => void; onClose: () => void;
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
            <InlineLink onClick={() => onOpen(`customer/${String(bond.counterparty_id)}`)}>
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
              {missing.join(", ")} {missing.length === 1 ? "is" : "are"} unknown
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

/** What the server says it cannot answer. Rendered, never dropped. */
function Unavailable({ items }: { items: Row[] }) {
  if (!items.length) return null;
  return (
    <ul className="tl-unavailable said-plain">
      {items.map((u, i) => (
        <li key={i}>
          <strong>{String(u.what)}</strong> — not in the score.{" "}
          <span className="viz-muted">{String(u.why)}</span>
        </li>
      ))}
    </ul>
  );
}

/** The chart's table fallback. A `<table>` is right here for exactly the reason
 *  `ui-standards` §13 gives — it is the accessible twin of one figure, not the
 *  screen's list of the business. The ledger below is the grid. */
function BondTable({ nodes }: { nodes: Node[] }) {
  return (
    <table className="grid">
      <thead>
        <tr>
          <th>Counterparty</th><th>Score</th><th>Bond</th>
          <th>Traded</th><th>Movement</th>
        </tr>
      </thead>
      <tbody>
        {nodes.map((n) => (
          <tr key={`${n.side}${n.id}`}>
            <td>{n.label}</td>
            <td>{n.score.toFixed(0)}</td>
            <td>{n.band.toLowerCase()}</td>
            <td>{money(n.money)}</td>
            <td>{n.movement == null ? "—" : signed(n.movement)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── geometry and small helpers ──────────────────────────────────────────────

/** The lanes, and every counterparty's fixed seat inside one.
 *
 *  Computed from the *current* scores and never from the displayed frame — see
 *  the note at the top of the file. This is the whole reason the play reads as
 *  movement rather than as churn: a dot's row is its identity, and only its
 *  horizontal position is the measure.
 *
 *  Only a populated side gets a lane. An empty supplier half reserving a band
 *  of canvas says "this business has no suppliers", which is never what it
 *  means — the emptiness is stated in words below the chart instead.
 */
function packLanes(
  sides: { side: string; bonds: Row[]; frames: Row[] }[],
  apply: <R extends Sourced>(rows: R[]) => R[],
  width: number,
  branch: boolean,
): { lanes: Lane[]; seat: Map<string, Seat> } {
  // The radius domain, pinned to every bond on the canvas *before* narrowing.
  //
  // ``radiusScale`` used to re-domain on whatever rows it was handed, which is
  // one lane's worth. Harmless while the whole book was always shown and wrong
  // the moment it is not: picking your six biggest accounts would re-domain on
  // those six, draw them all at 13px, and destroy the one reading the size
  // channel carries — a big dot on the left is material money in a weakening
  // relationship. Pinned, a small customer stays small when you single it out,
  // and the same money is the same size in every lane.
  const biggest = Math.max(
    1, ...sides.flatMap(({ bonds }) => bonds.map((b) => num(b.money))));
  const seat = new Map<string, Seat>();
  const lanes: Lane[] = [];

  sides.forEach(({ side, bonds, frames }) => {
    // Only a counterparty the server sent frames for can be drawn — the frame
    // series is where a per-month score comes from. The lane label counts
    // exactly these, so it can never claim more dots than are on the canvas.
    const covered = new Set(
      frames.flatMap((f) => rows(f.bonds).map((e) => String(e.counterparty_id))));
    const eligible = (apply(bonds as Sourced[]) as Row[])
      .filter((b) => b.score != null
                     && covered.has(String(b.counterparty_id)));
    if (!eligible.length) return;

    // Split into one lane per line of the business, when asked. This is the
    // branching the mix goal wants: "my coolant customers are all thin bonds"
    // is a sentence with a decision attached, and it is invisible when every
    // line is packed into one row.
    for (const group of splitBy(eligible, side, branch)) {
      packOne(group, side, seat, lanes, width, biggest);
    }
  });

  return { lanes, seat };
}

/** One lane's worth: the swarm packing, and the lane it produces. */
function packOne(group: { label: string; rows: Row[] }, side: string,
                 seat: Map<string, Seat>, lanes: Lane[], width: number,
                 biggest: number): void {
  {
    // Ascending, so the packer places the crowded left end first and the
    // sparse right end settles around it rather than the other way round.
    const scored = group.rows
      .slice()
      .sort((a, b) => num(a.score) - num(b.score));
    const r = radiusScale(biggest);
    const lane = lanes.length;
    const placed: { x: number; y: number; r: number }[] = [];
    let extent = 0;

    for (const b of scored) {
      const x = xOf(num(b.score), width);
      const rr = r(num(b.money));
      // Nearest free seat to the lane's spine, tried outward in both
      // directions. A dot that cannot find one at all stays on the spine and
      // overlaps rather than being pushed off the canvas — an overlap is
      // survivable, a mark drawn outside its own lane is not.
      let y = 0;
      for (let k = 0; k <= MAX_STACK; k += 1) {
        const options = k === 0 ? [0] : [k * STACK_STEP, -k * STACK_STEP];
        const free = options.find((cy) => placed.every((p) => {
          const dx = p.x - x, dy = p.y - cy;
          const reach = p.r + rr + 1;
          return dx * dx + dy * dy >= reach * reach;
        }));
        if (free !== undefined) { y = free; break; }
      }
      placed.push({ x, y, r: rr });
      extent = Math.max(extent, Math.abs(y) + rr);
      seat.set(`${side}:${String(b.counterparty_id)}`, { lane, y });
    }

    lanes.push({
      side,
      label: group.label,
      count: scored.length,
      half: Math.max(extent + 6, 26),
    });
  }
}

/** One group per lane: the whole side, or one per line of the business.
 *
 *  Splitting is opt-in because it costs vertical space and only earns it when
 *  the question is about mix. Lines are ordered by population so the crowded
 *  ones lead, and a counterparty whose trade is entirely uncategorised falls in
 *  a named lane rather than being dropped off the chart.
 */
function splitBy(rows: Row[], side: string, branch: boolean,
                 ): { label: string; rows: Row[] }[] {
  const whole = side === "vendor" ? "Suppliers" : "Customers";
  if (!branch) return [{ label: whole, rows }];

  const groups = new Map<string, Row[]>();
  for (const r of rows) {
    const key = String(r.sector ?? "UNCATEGORISED");
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  return [...groups.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([key, group]) => ({
      label: `${whole} · ${LINE_LABEL[key] ?? key}`,
      rows: group,
    }));
}

/** Category code → the words the server uses for it. Kept beside the chart
 *  rather than fetched, because the strip renders before the label list would
 *  arrive and a lane briefly titled "CUTTING_TOOLS" is a lane that looks
 *  broken. Overridden by the server's own list where it is present. */
const LINE_LABEL: Record<string, string> = {
  CUTTING_TOOLS: "Cutting tools",
  COOLANTS: "Coolants & lubricants",
  CONSUMABLES: "Consumables",
  METROLOGY: "Metrology",
  MACHINES: "Machines",
  UNCATEGORISED: "Not categorised",
};

interface Lane { side: string; label: string; count: number; half: number }
interface Seat { lane: number; y: number }

/** Marks sized by area, not by diameter — the same rule `Patterns.tsx` uses.
 *  Scaling the radius makes a counterparty with twice the revenue look four
 *  times as important. */
/** Money → radius. Square-rooted because the eye reads a circle's *area*: a
 *  customer twice the size drawn at twice the radius looks four times as big.
 *
 *  Takes the domain's top rather than deriving it, so the caller can pin it to
 *  the whole book — see ``packLanes``. */
export function radiusScale(biggest: number) {
  return scaleSqrt().domain([0, Math.max(biggest, 1)]).range([3, 13]);
}

/** Score → x. The axis is the full panel width, which is the entire point of
 *  the strip: a radial had ~130px to spend on the same 0–100. */
function xOf(score: number, width: number): number {
  const s = Math.min(100, Math.max(0, score));
  return PAD_L + (s / 100) * Math.max(1, width - PAD_L - PAD_R);
}

const PAD_L = 34;
const PAD_R = 18;
const STACK_STEP = 2;
const MAX_STACK = 70;

function layout(
  sides: { side: string; bonds: Row[]; frames: Row[] }[],
  at: number,
  apply: <R extends Sourced>(rows: R[]) => R[],
  seat: Map<string, Seat>,
  width: number,
): Node[] {
  const out: Node[] = [];
  // The same pinned domain the packer used, and it has to be the same one or a
  // dot would be drawn at one size and seated at another.
  const biggest = Math.max(
    1, ...sides.flatMap(({ bonds }) => bonds.map((b) => num(b.money))));
  sides.forEach(({ side, bonds, frames }) => {
    const visible = apply(bonds as Sourced[]) as Row[];
    const r = radiusScale(biggest);
    for (const b of visible) {
      const id = String(b.counterparty_id);
      const here = seat.get(`${side}:${id}`);
      if (!here) continue;
      const point = frameFor(frames, at, id);
      // Unscored *in this frame* means absent from it, not parked at zero.
      if (point?.score == null) continue;
      out.push({
        id, label: String(b.label), sector: String(b.sector ?? "Other"), side,
        score: Number(point.score),
        money: num(point.money ?? b.money),
        movement: movementOf(frames, at, id),
        band: String(point.band ?? b.band ?? "THIN"),
        origin: b.origin as EntityOrigin | undefined,
        overdue: Boolean(b.overdue),
        lane: here.lane,
        y: here.y,
        x: xOf(Number(point.score), width),
        r: r(num(point.money ?? b.money)),
      });
    }
  });
  return out;
}

function frameFor(frames: Row[], at: number, id: string): Row | null {
  const f = frames[at];
  if (!f) return null;
  return rows(f.bonds).find((e) => String(e.counterparty_id) === id) ?? null;
}

/** Score now minus score `MOVEMENT_LOOKBACK` frames back, in points.
 *
 *  `null` when there is nothing to compare against — a relationship that did
 *  not exist six months ago has not weakened, and drawing it as a full-strength
 *  gain would be just as wrong. */
function movementOf(frames: Row[], at: number, id: string): number | null {
  const now = frameFor(frames, at, id)?.score;
  const then = frameFor(frames, at - MOVEMENT_LOOKBACK, id)?.score;
  if (now == null || then == null) return null;
  return Number(now) - Number(then);
}

/** Movement → colour class. Neutral inside a band that is not worth a claim:
 *  a two-point drift over six months is noise, and colouring it would put a
 *  red dot next to a relationship nothing has happened to. */
function toneOf(movement: number | null): string {
  if (movement == null) return "bond-flat";
  if (movement >= 5) return "bond-up";
  if (movement <= -5) return "bond-down";
  return "bond-flat";
}

function signed(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(0)} pts`;
}

function counted(list: Row[], noun: string): string {
  const n = list.filter((b) => b.score != null).length;
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function anchored(list: Row[]): number {
  return list.filter((b) => b.band === "ANCHORED").length;
}

/** The earliest frame in which anything at all was scored, across both sides.
 *
 *  A book that started trading last year has a long dead run at the front of
 *  its window, and scrubbing through it by hand to find where the picture
 *  begins is a worse first experience than being taken there. */
function firstScored(...series: Row[][]): number | null {
  const length = Math.max(...series.map((s) => s.length), 0);
  for (let i = 0; i < length; i += 1) {
    const any = series.some((frames) =>
      rows(frames[i]?.bonds).some((e) => e.score != null));
    if (any) return i;
  }
  return null;
}
