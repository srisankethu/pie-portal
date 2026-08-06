// Where revenue moved between two periods, as a waterfall that reconciles.
//
// The bars sum exactly to the movement — the server asserts it and logs loudly
// if it ever fails — because a waterfall whose segments do not add up is the
// fastest way to teach people that the charts on a page are decorative. The
// running balance is drawn as a connector between bars, so the reconciliation is
// visible rather than merely claimed.
//
// Direction is encoded three ways: bar position relative to the running balance,
// colour, and a signed number in the label. Colour alone fails for eight percent
// of men, in greyscale print, and under forced-colours mode — and the sign is
// the entire message of this chart.
//
// **Drawn in measured pixels.** The first version used a 100-unit viewBox with
// `preserveAspectRatio="none"`, which scaled glyphs horizontally by the panel
// width over 100 — legible at 100px wide and smeared at 900. Measuring the
// container also lets the chart *decide* rather than merely scale: below ~680px
// it drops the per-bar customer count, and below ~420px it turns on its side,
// because six vertical bars in 380px is six unreadable slivers.

import { scaleBand, scaleLinear } from "d3-scale";

import { money } from "../../money";
import { Figure, ValueAxis } from "./Panel";
import { BUCKET_LABEL, BUCKET_SIGN } from "./tokens";
import { compactMoney, useMeasure } from "./useMeasure";

interface Bucket {
  kind: string;
  amount: number;
  customers: number;
  top: {
    customer_id: string; label: string; delta: number;
    previous: number; current: number;
  }[];
}

interface FlowData {
  currency?: string;
  comparison: { current: { label: string }; previous: { label: string } };
  previous_total: number;
  current_total: number;
  delta: number;
  buckets: Bucket[];
  reconciles: boolean;
}

const H = 300;
// The left pad holds the value axis. It was 8 when there was no axis to hold,
// and the chart was the poorer for it: bar heights were comparable to each
// other and to nothing else, so you could see revenue fall without being able
// to say roughly how far.
const PAD = { top: 30, right: 8, bottom: 54, left: 56 };
const MIN_BAR = 3;
/** Widest a bar gets however much room there is. Six bars stretched across a
 *  1200px panel stop reading as a sequence and start reading as a table. */
const MAX_BAR = 76;

export function Waterfall({
  data,
  onDrill,
}: {
  data: FlowData;
  onDrill?: (customerId: string) => void;
}) {
  const [ref, room] = useMeasure<HTMLDivElement>();
  const currency = data.currency ?? "INR";
  const active = data.buckets.filter((b) => b.customers > 0);

  // Running balance: start at the previous total, apply each bucket in order,
  // end at the current total. Each bar spans from the balance before it to the
  // balance after — that span *is* the reconciliation.
  let running = data.previous_total;
  const steps = active.map((b) => {
    const from = running;
    running += b.amount;
    return { ...b, from, to: running };
  });

  const summary =
    `Revenue moved from ${money(data.previous_total)} in ${data.comparison.previous.label} ` +
    `to ${money(data.current_total)} in ${data.comparison.current.label}. ` +
    steps
      .map(
        (s) =>
          `${BUCKET_LABEL[s.kind] ?? s.kind}: ${s.amount >= 0 ? "up" : "down"} ` +
          `${money(Math.abs(s.amount))} across ${s.customers} customers`,
      )
      .join("; ") + ".";

  const table = (
    <table className="viz-table">
      <caption className="sr-only">Revenue movement by cause</caption>
      <thead>
        <tr>
          <th scope="col">Movement</th>
          <th scope="col">Amount</th>
          <th scope="col">Customers</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">{data.comparison.previous.label}</th>
          <td>{money(data.previous_total)}</td>
          <td>—</td>
        </tr>
        {steps.map((s) => (
          <tr key={s.kind}>
            <th scope="row">{BUCKET_LABEL[s.kind] ?? s.kind}</th>
            <td>
              {s.amount >= 0 ? "+" : "−"}
              {money(Math.abs(s.amount))}
            </td>
            <td>{s.customers}</td>
          </tr>
        ))}
        <tr>
          <th scope="row">{data.comparison.current.label}</th>
          <td>{money(data.current_total)}</td>
          <td>—</td>
        </tr>
      </tbody>
    </table>
  );

  return (
    <div ref={ref} className="viz-measure">
      <Figure
        caption={`${data.comparison.previous.label} → ${data.comparison.current.label}. Bars sum to the change.`}
        summary={summary}
        table={table}
      >
        {room.width === 0 ? (
          <div style={{ height: H }} aria-hidden="true" />
        ) : room.cramped ? (
          <HorizontalBars steps={steps} data={data} onDrill={onDrill} />
        ) : (
          <VerticalWaterfall
            steps={steps}
            data={data}
            room={room}
            currency={currency}
            onDrill={onDrill}
          />
        )}
      </Figure>
    </div>
  );
}

type Step = Bucket & { from: number; to: number };

function VerticalWaterfall({
  steps, data, room, currency, onDrill,
}: {
  steps: Step[];
  data: FlowData;
  room: { width: number; tight: boolean };
  currency: string;
  onDrill?: (id: string) => void;
}) {
  const w = room.width;
  const values = [
    data.previous_total,
    data.current_total,
    ...steps.flatMap((s) => [s.from, s.to]),
    0,
  ];
  const max = Math.max(...values);
  const min = Math.min(...values);

  const cols = steps.length + 2;
  // `.nice()` rounds the domain out to whole tick boundaries, which is what
  // makes the gridlines land on round money rather than on wherever the largest
  // bar happened to end.
  const y = scaleLinear()
    .domain([min, max])
    .range([H - PAD.bottom, PAD.top])
    .nice();
  // paddingInner 0.38 leaves each bar 62% of its column, which is what the
  // hand-computed layout did; the cap is applied when the bar is drawn, because
  // the column centres must stay evenly spaced whether or not the cap bites.
  const band = scaleBand<number>()
    .domain(Array.from({ length: cols }, (_, i) => i))
    .range([PAD.left, w - PAD.right])
    .paddingInner(0.38);
  const barW = Math.min(band.bandwidth(), MAX_BAR);
  const cx = (i: number) => (band(i) ?? PAD.left) + band.bandwidth() / 2;
  // The rule the anchor bars stand on. Zero is forced into `values`, so it is
  // always inside the domain and always on screen — a bar drawn down to a zero
  // the scale could not reach would run off the axis.
  const base = y(0);
  const plotRight = w - PAD.right;

  return (
    <svg
      width={w}
      height={H}
      viewBox={`0 0 ${w} ${H}`}
      className="viz-svg"
      role="presentation"
    >
      {/* Behind everything: this is how the running balance becomes checkable
          rather than merely asserted — each bar's top can be read off the
          scale, not just compared with its neighbour. */}
      <ValueAxis
        scale={y}
        x0={PAD.left}
        x1={plotRight}
        format={(v) => compactMoney(v, currency)}
      />
      <line
        x1={PAD.left} x2={plotRight} y1={base} y2={base}
        stroke="var(--viz-rule)" strokeWidth="1"
      />

      <Bar
        cx={cx(0)} w={barW} top={y(data.previous_total)} bottom={base}
        fill="var(--viz-neutral)" label={data.comparison.previous.label}
        value={compactMoney(data.previous_total, currency)}
        full={money(data.previous_total)}
      />

      {steps.map((s, i) => {
        const sign = BUCKET_SIGN[s.kind] ?? 0;
        const top = y(Math.max(s.from, s.to));
        const bottom = y(Math.min(s.from, s.to));
        const fill =
          sign < 0 ? "var(--viz-loss)" : sign > 0 ? "var(--viz-gain)" : "var(--viz-neutral)";
        const target = s.top[0];
        return (
          <g key={s.kind}>
            {/* The connector from the previous bar's end — this is what makes
                the chain visible rather than a row of unrelated bars. */}
            <line
              x1={cx(i) + barW / 2} x2={cx(i + 1) - barW / 2}
              y1={y(s.from)} y2={y(s.from)}
              stroke="var(--viz-rule)" strokeWidth="1" strokeDasharray="3 3"
            />
            <Bar
              cx={cx(i + 1)} w={barW} top={top} bottom={bottom} fill={fill}
              label={BUCKET_LABEL[s.kind] ?? s.kind}
              value={`${s.amount >= 0 ? "+" : "−"}${compactMoney(Math.abs(s.amount), currency)}`}
              full={`${s.amount >= 0 ? "+" : "−"}${money(Math.abs(s.amount))}`}
              sub={room.tight ? undefined : `${s.customers} customer${s.customers === 1 ? "" : "s"}`}
              tooltip={s.top
                .map((t) => `${t.label}: ${t.delta >= 0 ? "+" : "−"}${money(Math.abs(t.delta))}`)
                .join("\n")}
              onClick={target && onDrill ? () => onDrill(target.customer_id) : undefined}
            />
          </g>
        );
      })}

      <line
        x1={cx(steps.length) + barW / 2} x2={cx(steps.length + 1) - barW / 2}
        y1={y(data.current_total)} y2={y(data.current_total)}
        stroke="var(--viz-rule)" strokeWidth="1" strokeDasharray="3 3"
      />
      <Bar
        cx={cx(cols - 1)} w={barW} top={y(data.current_total)} bottom={base}
        fill="var(--viz-neutral)" label={data.comparison.current.label}
        value={compactMoney(data.current_total, currency)}
        full={money(data.current_total)}
      />
    </svg>
  );
}

function Bar({
  cx, w, top, bottom, fill, label, value, full, sub, tooltip, onClick,
}: {
  cx: number; w: number; top: number; bottom: number; fill: string;
  label: string; value: string; full: string; sub?: string;
  tooltip?: string; onClick?: () => void;
}) {
  const h = Math.max(Math.abs(bottom - top), MIN_BAR);
  const yTop = Math.min(top, bottom);
  const x = cx - w / 2;
  const interactive = Boolean(onClick);
  return (
    <g
      className={interactive ? "viz-bar viz-bar-clickable" : "viz-bar"}
      onClick={onClick}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick?.(); }
            }
          : undefined
      }
      aria-label={interactive ? `${label}, ${full}. Open the largest contributor.` : undefined}
    >
      <title>{tooltip ? `${label}\n${full}\n\n${tooltip}` : `${label}: ${full}`}</title>
      <rect x={x} y={yTop} width={w} height={h} fill={fill} rx="2" />
      <text x={cx} y={yTop - 8} className="viz-bar-value" textAnchor="middle">
        {value}
      </text>
      <text x={cx} y={H - 30} className="viz-bar-label" textAnchor="middle">
        {label}
      </text>
      {sub && (
        <text x={cx} y={H - 15} className="viz-bar-sub" textAnchor="middle">
          {sub}
        </text>
      )}
    </g>
  );
}

/** Below ~420px the vertical form becomes six unreadable slivers, so the chart
 *  turns on its side: labels get a full line, bars diverge from a centre rule,
 *  and the reading order stays the same. Not a fallback — the same data, in the
 *  form that fits. */
function HorizontalBars({
  steps, data, onDrill,
}: {
  steps: Step[];
  data: FlowData;
  onDrill?: (id: string) => void;
}) {
  const widest = Math.max(...steps.map((s) => Math.abs(s.amount)), 1);
  return (
    <div className="wf-rows">
      <div className="wf-anchor">
        <span>{data.comparison.previous.label}</span>
        <strong>{money(data.previous_total)}</strong>
      </div>
      {steps.map((s) => {
        const sign = BUCKET_SIGN[s.kind] ?? 0;
        const pct = (Math.abs(s.amount) / widest) * 50;
        const target = s.top[0];
        const clickable = Boolean(target && onDrill);
        return (
          <div
            key={s.kind}
            className={`wf-row${clickable ? " wf-row-clickable" : ""}`}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            onClick={clickable ? () => onDrill?.(target.customer_id) : undefined}
            onKeyDown={
              clickable
                ? (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onDrill?.(target.customer_id);
                    }
                  }
                : undefined
            }
          >
            <span className="wf-row-label">
              {BUCKET_LABEL[s.kind] ?? s.kind}
              <em>{s.customers}</em>
            </span>
            <span className="wf-track" aria-hidden="true">
              <span
                className={`wf-fill ${sign < 0 ? "neg" : sign > 0 ? "pos" : "flat"}`}
                style={
                  sign < 0
                    ? { right: "50%", width: `${pct}%` }
                    : { left: "50%", width: `${pct}%` }
                }
              />
            </span>
            <span className={`wf-row-value ${sign < 0 ? "neg" : "pos"}`}>
              {s.amount >= 0 ? "+" : "−"}
              {money(Math.abs(s.amount))}
            </span>
          </div>
        );
      })}
      <div className="wf-anchor">
        <span>{data.comparison.current.label}</span>
        <strong>{money(data.current_total)}</strong>
      </div>
    </div>
  );
}
