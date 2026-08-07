// The bond map: how close each customer and each supplier actually is, and how
// that got to be true.
//
// **Radius is the measure, and that is the whole reason this is radial.** The
// company sits at the centre and a counterparty's distance from it *is* their
// bond strength — the metaphor everyone already uses for a relationship, made
// literal. A bar chart of the same numbers would rank them, which is a
// different and less interesting question, and the ledger below answers it
// anyway.
//
// **Angle carries the sector, not decoration.** The nodes in a wedge are the
// counterparties whose trade is mostly one thing, so "everything on the drill
// side is drifting out" is a sentence somebody can read off the picture and
// then act on. An angle that meant nothing would be half the ink carrying no
// information — and worse, it would make the play unreadable, because nodes
// would have to be re-laid-out every frame and would jump.
//
// **Colour is the only signed thing here**, so it gets the signed palette:
// movement over the window, blue for strengthening and red for weakening,
// which is the pairing `tokens.ts` validated for colour-vision deficiency.
// Strength is already the radius; colouring by strength too would spend the
// one signed channel on a dimension that is redundant.
//
// **What is NOT on the map.** A bond the server declined to score has no
// position, because the outer edge means "weakest" and an unscored
// relationship is not weak — it is unmeasured. `Patterns.tsx` made the same
// call for points with no margin, and for the same reason: on the axis they
// read as a measured zero. They are counted, named and listed instead.

import Button from "@mui/material/Button";
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
  ["breadth", "Spread",
   "How much of the catalogue the relationship covers. One item is a transaction; a dozen is being embedded in how they work."],
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

/** A plotted node. Everything the map needs, resolved once per frame. */
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
  angle: number;
  overdue: boolean;
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
  const [ref, room] = useMeasure<HTMLDivElement>();

  const sides = useMemo(() => {
    const out: { side: string; bonds: Row[]; frames: Row[] }[] = [];
    if (showSuppliers) out.push({ side: "vendor", bonds: vendorBonds, frames: vendorFrames });
    if (showCustomers) out.push({ side: "customer", bonds: customerBonds, frames });
    return out;
  }, [showCustomers, showSuppliers, customerBonds, vendorBonds, frames, vendorFrames]);

  const nodes = useMemo(
    () => layout(sides, at, company.apply.bind(company)),
    [sides, at, company.company]);   // eslint-disable-line react-hooks/exhaustive-deps

  const shownCustomers = company.apply(customerBonds as Sourced[]) as Row[];
  const shownVendors = company.apply(vendorBonds as Sourced[]) as Row[];
  const ledger = [...(showCustomers ? shownCustomers : []),
                  ...(showSuppliers ? shownVendors : [])];
  const unscored = ledger.filter((b) => b.score == null);
  const frameLabel = String(
    (frames[at] ?? vendorFrames[at])?.label ?? data?.as_of ?? "");

  const size = room.cramped ? 320 : Math.min(room.width || 560, 620);
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
          <><strong>{anchored(ledger)}</strong> are anchored; </>
        )}
        <strong>{ledger.filter((b) => b.overdue).length}</strong> are past their
        own buying rhythm.
      </p>

      <CompanyFilter options={company.options} value={company.company}
                     onChange={company.setCompany} show={company.show} />

      <div ref={ref} className="bond-stage">
        <Figure
          caption={
            `Distance from the centre is bond strength — closer is stronger. `
            + `Wedge is what they mostly trade. Dot size is ${showSuppliers && !showCustomers ? "spend" : "revenue"}. `
            + `Colour is movement over the last ${MOVEMENT_LOOKBACK} months: blue strengthening, red weakening.`}
          summary={nodes.map((n) =>
            `${n.label}: ${n.score.toFixed(0)} of 100, ${n.band.toLowerCase()}, ${money(n.money)}`)
            .join(". ") || "No scored relationship in this window."}
          table={<BondTable nodes={nodes} />}
        >
          {/* The hub names the sides actually drawn, taken from the nodes
              rather than from the toggle — a hub reading "suppliers ·
              customers" over a circle of only customers labels an absence. */}
          <BondMap nodes={nodes} size={size}
                   reduced={reduced} selected={selected}
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

// ── the map ─────────────────────────────────────────────────────────────────
function BondMap({
  nodes, size, reduced, selected, onSelect,
}: {
  nodes: Node[];
  size: number;
  reduced: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const drawn = [...new Set(nodes.map((n) => n.side))];
  const cx = size / 2, cy = size / 2;
  const rOuter = size / 2 - 26;
  // The hub is not decoration: without it every anchored bond would pile onto
  // one point and the strongest relationships — the ones the screen exists to
  // show — would be the least readable part of it.
  const rInner = Math.max(34, size * 0.14);
  const pr = scaleSqrt()
    .domain([0, Math.max(...nodes.map((n) => n.money), 1)])
    .range([3.5, size > 420 ? 15 : 10]);

  return (
    <svg viewBox={`0 0 ${size} ${size}`} width="100%" height={size}
         className={`bond-map${reduced ? " bond-still" : ""}`}>
      {/* Strength rings, so a radius can be read rather than only compared. */}
      <g aria-hidden="true">
        {[25, 50, 75, 100].map((s) => (
          <circle key={s} cx={cx} cy={cy} r={rInner + (1 - s / 100) * (rOuter - rInner)}
                  className="bond-ring" />
        ))}
        {[25, 50, 75].map((s) => (
          <text key={s} x={cx} className="viz-axis" textAnchor="middle"
                y={cy - (rInner + (1 - s / 100) * (rOuter - rInner)) - 3}>{s}</text>
        ))}
      </g>

      {/* The tie itself — drawn from the hub, so "close" is something you see
          rather than something you measure. */}
      <g aria-hidden="true">
        {nodes.map((n) => {
          const p = point(cx, cy, n.angle, radiusFor(n.score, rInner, rOuter));
          const h = point(cx, cy, n.angle, rInner);
          return (
            <line key={`l${n.side}${n.id}`} x1={h.x} y1={h.y} x2={p.x} y2={p.y}
                  className="bond-tie"
                  style={{ strokeWidth: Math.max(0.6, pr(n.money) / 5) }} />
          );
        })}
      </g>

      <circle cx={cx} cy={cy} r={rInner} className="bond-hub" />
      <text x={cx} y={cy - 4} textAnchor="middle" className="bond-hub-label">
        This book
      </text>
      <text x={cx} y={cy + 12} textAnchor="middle" className="bond-hub-sub">
        {drawn.length > 1
          ? "suppliers · customers"
          : drawn[0] === "vendor" ? "suppliers" : "customers"}
      </text>

      {nodes.map((n) => {
        const p = point(cx, cy, n.angle, radiusFor(n.score, rInner, rOuter));
        const on = selected === n.id;
        return (
          <ChartTip
            key={`${n.side}${n.id}`}
            title={`${n.label} — ${n.score.toFixed(0)}/100, ${n.band.toLowerCase()}. `
              + `${money(n.money)} traded. ${n.sector}.`
              + (n.movement == null ? "" : ` ${signed(n.movement)} over ${MOVEMENT_LOOKBACK} months.`)
              + (n.overdue ? " Past their own buying rhythm." : "")}
          >
            <circle
              cx={p.x} cy={p.y} r={pr(n.money) + (on ? 3 : 0)}
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
    <ul className="tl-unavailable">
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
//
// The layout is computed from the *bond list*, never from the frame, so a
// node's angle is the same in every frame. Laying out per frame would let a
// dot swap wedges as its neighbours came and went, and the play would read as
// noise rather than as movement.

function layout(
  sides: { side: string; bonds: Row[]; frames: Row[] }[],
  at: number,
  apply: <R extends Sourced>(rows: R[]) => R[],
): Node[] {
  const out: Node[] = [];
  // Only a side with something to draw claims an arc. Splitting the circle by
  // how many sides are *selected* rather than by how many are populated is what
  // put four customers into a quarter of the canvas while an empty supplier
  // half held the other 180° — the emptiness of a half is said in words below
  // the chart, not by reserving space for it.
  const populated = sides
    .map((s) => ({ ...s, scored: (apply(s.bonds as Sourced[]) as Row[])
                                   .filter((b) => b.score != null) }))
    .filter((s) => s.scored.length > 0);
  const span = (Math.PI * 2) / Math.max(1, populated.length);

  populated.forEach(({ side, frames, scored }, sideIndex) => {
    const bySector = new Map<string, Row[]>();
    for (const b of scored) {
      const key = String(b.sector ?? "Other");
      bySector.set(key, [...(bySector.get(key) ?? []), b]);
    }
    // Stable ordering, so the wedges do not reshuffle between renders.
    const sectors = [...bySector.keys()].sort();
    const total = scored.length || 1;
    const base = sideIndex * span - Math.PI / 2;

    let placed = 0;
    for (const sector of sectors) {
      const group = (bySector.get(sector) ?? [])
        .slice()
        .sort((a, b) => num(b.money) - num(a.money));
      for (const b of group) {
        const id = String(b.counterparty_id);
        const point = frameFor(frames, at, id);
        const score = point?.score;
        if (score == null) { placed += 1; continue; }
        out.push({
          id, label: String(b.label), sector, side,
          score: Number(score),
          money: num(point?.money ?? b.money),
          movement: movementOf(frames, at, id),
          band: String(point?.band ?? b.band ?? "THIN"),
          origin: b.origin as EntityOrigin | undefined,
          overdue: Boolean(b.overdue),
          // Half a slot in, so the first node is not welded to the seam
          // between two wedges.
          angle: base + span * ((placed + 0.5) / total),
        });
        placed += 1;
      }
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

/** Strong bonds sit near the hub. The inversion is the whole metaphor. */
function radiusFor(score: number, rInner: number, rOuter: number): number {
  const s = Math.min(100, Math.max(0, score));
  return rInner + (1 - s / 100) * (rOuter - rInner);
}

function point(cx: number, cy: number, angle: number, r: number) {
  return { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r };
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
