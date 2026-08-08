// The whole book as one picture: principals → lines → customers.
//
// The dependency lists answer "how exposed are we to this name". This answers
// the question an owner asks first — *what does my business look like* — and it
// answers both ends and the mix in the middle at once, which no list can. Band
// width is money; you can trace a principal through a line to the customers it
// ends up with.
//
// **A hand-drawn Sankey rather than a library.** The layout is one pass of
// stacked offsets and the ribbons are two cubic curves; pulling in d3-sankey to
// do that would add a dependency for an algorithm that is thirty lines, and the
// existing charts here are all hand-drawn SVG over `d3-scale`.
//
// **Nothing is silently dropped.** Revenue with no traceable principal, and
// trade in items with no resolved line, get their own bands and are drawn
// muted-and-hatched rather than omitted. A flow picture that quietly left them
// out would show a smaller, tidier business than the real one — and would not
// reconcile with the totals on every other screen, which is how a page teaches
// people to distrust it.
//
// **Hovering a band lights its whole path.** That is the interaction the shape
// exists for: "where does Kennametal actually end up" is answered by following
// one ribbon, not by reading twenty.

import { useMemo, useState } from "react";
import { money } from "../../money";
import { ChartTip } from "../kit";
import { Figure } from "./Panel";
import { useMeasure } from "./useMeasure";
import { pct } from "./useInsight";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

/** Laid-out geometry for one node. */
interface Box {
  id: string;
  label: string;
  stage: number;
  money: number;
  residual: boolean;
  x: number;
  y: number;
  h: number;
}

const NODE_W = 13;
const GAP = 5;
const PAD_T = 26;

export function BookFlow({ flow }: { flow: Row }) {
  const [ref, room] = useMeasure<HTMLDivElement>();
  const [lit, setLit] = useState<string | null>(null);

  const nodes = rows(flow.nodes);
  const links = rows(flow.links);
  const total = num(flow.total);

  const width = Math.max(420, room.width || 900);
  const height = Math.min(660, Math.max(360, nodes.length * 16));

  const boxes = useMemo(
    () => layout(nodes, width, height), [nodes, width, height]);

  if (!nodes.length) return null;
  const byId = new Map(boxes.map((b) => [b.id, b]));

  // Ribbons are stacked in the same order on both ends, so a node's outgoing
  // bands leave in the order they arrive — without that the picture crosses
  // itself far more than the data does.
  const ribbons = ribbonGeometry(links, byId);

  return (
    <div ref={ref}>
      <Figure
        caption={`Every band is revenue. Left to right: the principal whose product it is, the line of the business, and the customer who bought it. ${money(total)} in total.`}
        summary={boxes
          .filter((b) => b.stage === 0)
          .map((b) => `${b.label}: ${money(b.money)}`)
          .join(". ")}
        table={<FlowTable boxes={boxes} total={total} />}
      >
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height}
             className="flow-map">
          <g aria-hidden="true">
            {["Principal", "Line", "Customer"].map((t, i) => (
              <text key={t} className="bond-band-label" y={12}
                    x={stageX(i, width) + (i === 2 ? NODE_W : 0)}
                    textAnchor={i === 2 ? "end" : "start"}>{t}</text>
            ))}
          </g>

          <g>
            {ribbons.map((r, i) => {
              const on = lit === null || r.source === lit || r.target === lit;
              return (
                <path
                  key={i}
                  d={r.d}
                  className={`flow-ribbon${on ? "" : " dim"}${r.residual ? " residual" : ""}`}
                  style={{ strokeWidth: r.w }}
                  onMouseEnter={() => setLit(r.source)}
                  onMouseLeave={() => setLit(null)}
                />
              );
            })}
          </g>

          {boxes.map((b) => (
            <ChartTip key={b.id}
                      title={`${b.label} — ${money(b.money)}${total ? `, ${pct(b.money / total, 0)} of revenue` : ""}`}>
              <g className="flow-node"
                 onMouseEnter={() => setLit(b.id)}
                 onMouseLeave={() => setLit(null)}>
                <rect x={b.x} y={b.y} width={NODE_W} height={b.h}
                      className={`flow-bar${b.residual ? " residual" : ""}`} />
                {/* Only labels with room. A band four pixels tall cannot carry
                    a name, and stacking one on top of its neighbour is worse
                    than the tooltip that is already there. */}
                {b.h >= 13 && (
                  <text
                    className="flow-label"
                    x={b.stage === 2 ? b.x - 6 : b.x + NODE_W + 6}
                    y={b.y + b.h / 2 + 4}
                    textAnchor={b.stage === 2 ? "end" : "start"}>
                    {b.label}
                  </text>
                )}
              </g>
            </ChartTip>
          ))}
        </svg>
      </Figure>
    </div>
  );
}

function stageX(stage: number, width: number): number {
  const inner = width - 260;               // room for labels at both edges
  return 130 + (stage / 2) * (inner - NODE_W);
}

/** Stack each stage's nodes top to bottom, scaled so the tallest stage fills. */
function layout(nodes: Row[], width: number, height: number): Box[] {
  const stages = [0, 1, 2].map((s) => nodes.filter((n) => num(n.stage) === s));
  const tallest = Math.max(
    ...stages.map((ns) => ns.reduce((a, n) => a + num(n.money), 0)), 1);
  const usable = (ns: Row[]) => height - PAD_T - 10 - (ns.length - 1) * GAP;

  const out: Box[] = [];
  stages.forEach((ns, stage) => {
    const scale = usable(ns) / tallest;
    let y = PAD_T;
    for (const n of ns) {
      // Every band stays clickable and hoverable even when it is tiny.
      const h = Math.max(3, num(n.money) * scale);
      out.push({
        id: String(n.id), label: String(n.label), stage,
        money: num(n.money), residual: Boolean(n.residual),
        x: stageX(stage, width), y, h,
      });
      y += h + GAP;
    }
  });
  return out;
}

/** Two cubic curves per link, stacked in node order at both ends. */
function ribbonGeometry(links: Row[], byId: Map<string, Box>) {
  const usedOut = new Map<string, number>();
  const usedIn = new Map<string, number>();
  const out: { d: string; w: number; source: string; target: string;
               residual: boolean }[] = [];

  for (const l of links) {
    const a = byId.get(String(l.source));
    const b = byId.get(String(l.target));
    if (!a || !b) continue;
    // Height per unit of money is the node's own scale, so the ribbons leaving
    // a node exactly fill it — a band that overflowed its node would be the
    // picture failing to reconcile.
    const wa = (num(l.money) / a.money) * a.h;
    const wb = (num(l.money) / b.money) * b.h;
    const oa = usedOut.get(a.id) ?? 0;
    const ob = usedIn.get(b.id) ?? 0;
    usedOut.set(a.id, oa + wa);
    usedIn.set(b.id, ob + wb);

    const y0 = a.y + oa + wa / 2;
    const y1 = b.y + ob + wb / 2;
    const x0 = a.x + NODE_W;
    const x1 = b.x;
    const mid = (x0 + x1) / 2;
    out.push({
      d: `M${x0},${y0} C${mid},${y0} ${mid},${y1} ${x1},${y1}`,
      w: Math.max(1, Math.min(wa, wb)),
      source: a.id, target: b.id,
      residual: a.residual || b.residual,
    });
  }
  return out;
}

/** The accessible twin. A fixed shape — three stages — so a `<table>` is the
 *  right tool here, exactly as `ui-standards` §13 describes. */
function FlowTable({ boxes, total }: { boxes: Box[]; total: number }) {
  const stage = ["Principal", "Line", "Customer"];
  return (
    <table className="grid">
      <thead>
        <tr><th>Stage</th><th>Band</th><th>Revenue</th><th>Share</th></tr>
      </thead>
      <tbody>
        {boxes.map((b) => (
          <tr key={b.id}>
            <td>{stage[b.stage]}</td>
            <td>{b.label}</td>
            <td>{money(b.money)}</td>
            <td>{total ? pct(b.money / total, 1) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
