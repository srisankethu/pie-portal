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
// one ribbon, not by reading twenty. The walk that decides what "whole path"
// means lives in `flow-highlight.ts`, where a fixture can catch it stopping a
// stage short — which is what it used to do.
//
// **A path can be pinned, and reached without a mouse.** Hover alone resets the
// moment the pointer moves, so the values along a lit path could not be read
// off one at a time; and the marks carried mouse handlers only, which left the
// whole interaction unavailable to a keyboard. `FlowTable` was standing in for
// both, and a table of every band is not an answer to "trace this one".

import { useMemo, useState } from "react";
import { money } from "../../money";
import { ChartTip } from "../kit";
import { type Edge, pathThroughLink, pathThroughNode } from "./flow-highlight";
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

/** What the reader is pointing at, or has pinned: a node, or one band. */
type Focus =
  | { kind: "node"; id: string }
  | { kind: "link"; source: string; target: string };

const sameFocus = (a: Focus | null, b: Focus | null): boolean =>
  a === b || (a?.kind === "node" && b?.kind === "node" && a.id === b.id);

export function BookFlow({ flow }: { flow: Row }) {
  const [ref, room] = useMeasure<HTMLDivElement>();
  const [hovered, setHovered] = useState<Focus | null>(null);
  const [pinned, setPinned] = useState<Focus | null>(null);

  const nodes = rows(flow.nodes);
  const links = rows(flow.links);
  const total = num(flow.total);

  const width = Math.max(420, room.width || 900);
  const height = Math.min(660, Math.max(360, nodes.length * 16));

  const boxes = useMemo(
    () => layout(nodes, width, height), [nodes, width, height]);

  // A pin outranks the pointer, so moving the mouse away to read a tooltip or
  // reach for the scroll wheel does not throw the path away.
  const focus = pinned ?? hovered;
  const edges = useMemo<Edge[]>(
    () => links.map((l) => ({
      source: String(l.source), target: String(l.target),
    })), [links]);
  const lit = useMemo(
    () => (focus === null ? null
      : focus.kind === "node" ? pathThroughNode(focus.id, edges)
        : pathThroughLink(focus, edges)),
    [focus, edges]);

  if (!nodes.length) return null;
  const byId = new Map(boxes.map((b) => [b.id, b]));

  // Ribbons are stacked in the same order on both ends, so a node's outgoing
  // bands leave in the order they arrive — without that the picture crosses
  // itself far more than the data does.
  const ribbons = ribbonGeometry(links, byId);

  return (
    <div ref={ref}>
      <Figure
        caption={`Every band is revenue. Left to right: the principal whose product it is, the line of the business, and the customer who bought it. ${money(total)} in total. Hover a band to trace it end to end; click to hold it, Escape to release.`}
        summary={boxes
          .filter((b) => b.stage === 0)
          .map((b) => `${b.label}: ${money(b.money)}`)
          .join(". ")}
        table={<FlowTable boxes={boxes} total={total} />}
      >
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height}
             className="flow-map"
             onKeyDown={(e) => { if (e.key === "Escape") setPinned(null); }}>
          {/* Clicking off the bands releases a pin. A backdrop rather than a
              handler on the svg, so a click that landed on a node is consumed
              by the node and never reaches this — no stopPropagation, and no
              ordering between the two to get wrong later. */}
          <rect x={0} y={0} width={width} height={height} fill="transparent"
                aria-hidden="true" onClick={() => setPinned(null)} />

          <g aria-hidden="true">
            {["Principal", "Line", "Customer"].map((t, i) => (
              <text key={t} className="bond-band-label" y={12}
                    x={stageX(i, width) + (i === 2 ? NODE_W : 0)}
                    textAnchor={i === 2 ? "end" : "start"}>{t}</text>
            ))}
          </g>

          <g>
            {ribbons.map((r, i) => {
              // Both ends lit means the band is on the path. Exact rather than
              // approximate because the stages are ordered — the argument, and
              // the fixture that holds it, are in `flow-highlight.ts`.
              const on = lit === null
                || (lit.has(r.source) && lit.has(r.target));
              return (
                <path
                  key={i}
                  d={r.d}
                  className={`flow-ribbon${on ? "" : " dim"}${r.residual ? " residual" : ""}`}
                  style={{ strokeWidth: r.w }}
                  onMouseEnter={() => setHovered(
                    { kind: "link", source: r.source, target: r.target })}
                  onMouseLeave={() => setHovered(null)}
                />
              );
            })}
          </g>

          {boxes.map((b) => {
            const self: Focus = { kind: "node", id: b.id };
            const on = lit === null || lit.has(b.id);
            const held = sameFocus(pinned, self);
            // Toggling on the pinned node is the way back out, so a reader who
            // pinned by accident is never stuck with a lit path they cannot
            // clear without hunting for blank canvas.
            const pin = () => setPinned(held ? null : self);
            return (
              <ChartTip key={b.id}
                        title={`${b.label} — ${money(b.money)}${total ? `, ${pct(b.money / total, 0)} of revenue` : ""}`}>
                <g className={`flow-node${on ? "" : " dim"}${held ? " pinned" : ""}`}
                   tabIndex={0}
                   role="button"
                   aria-pressed={held}
                   aria-label={`${b.label}, ${money(b.money)}${total ? `, ${pct(b.money / total, 0)} of revenue` : ""}`}
                   onMouseEnter={() => setHovered(self)}
                   onMouseLeave={() => setHovered(null)}
                   onFocus={() => setHovered(self)}
                   onBlur={() => setHovered(null)}
                   onClick={pin}
                   onKeyDown={(e) => {
                     if (e.key === "Enter" || e.key === " ") {
                       e.preventDefault();   // Space would scroll the page.
                       pin();
                     }
                   }}>
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
            );
          })}
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
