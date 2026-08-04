// Where revenue moved between two periods, as a waterfall that reconciles.
//
// The bars sum exactly to the movement — the server asserts it and logs loudly
// if it ever fails — because a waterfall whose segments do not add up to the
// total is the fastest way to teach people that the charts on a page are
// decorative. The running balance is drawn as a connector between bars so the
// reconciliation is visible rather than merely claimed.
//
// Direction is encoded three ways: bar direction from the baseline, colour, and
// a signed number in the label. That redundancy is deliberate — colour alone
// fails for eight percent of men, in greyscale print, and under forced-colours
// mode, and the sign is the entire message of this chart.
//
// Every bar is a button. A number a person cannot open is a number they have to
// take on faith.

import { money } from "../../money";
import { BUCKET_LABEL, BUCKET_SIGN } from "./tokens";
import { Figure } from "./Panel";

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
  comparison: { current: { label: string }; previous: { label: string } };
  previous_total: number;
  current_total: number;
  delta: number;
  buckets: Bucket[];
  reconciles: boolean;
}

const HEIGHT = 260;
const PAD = { top: 24, right: 16, bottom: 52, left: 16 };

export function Waterfall({
  data,
  onDrill,
}: {
  data: FlowData;
  onDrill?: (customerId: string) => void;
}) {
  const active = data.buckets.filter((b) => b.customers > 0);
  if (active.length === 0) return null;

  // Running balance: start at the previous total, apply each bucket in order,
  // end at the current total. The bar for a bucket spans from the balance
  // before it to the balance after, which is what makes the chain visible.
  let running = data.previous_total;
  const steps = active.map((b) => {
    const from = running;
    running += b.amount;
    return { ...b, from, to: running };
  });

  const values = [data.previous_total, data.current_total, ...steps.flatMap((s) => [s.from, s.to])];
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const span = max - min || 1;

  const cols = steps.length + 2;            // opening + steps + closing
  const colW = 100 / cols;
  const barW = colW * 0.56;

  const y = (v: number) => PAD.top + (1 - (v - min) / span) * (HEIGHT - PAD.top - PAD.bottom);

  const summary =
    `Revenue moved from ${money(data.previous_total)} in ${data.comparison.previous.label} ` +
    `to ${money(data.current_total)} in ${data.comparison.current.label}. ` +
    steps.map((s) => `${BUCKET_LABEL[s.kind] ?? s.kind}: ${s.amount >= 0 ? "up" : "down"} ${money(Math.abs(s.amount))} across ${s.customers} customers`).join("; ") + ".";

  return (
    <Figure
      caption={`${data.comparison.previous.label} → ${data.comparison.current.label}. Bars sum to the change.`}
      summary={summary}
      table={
        <table className="viz-table">
          <caption className="sr-only">Revenue movement by cause</caption>
          <thead>
            <tr><th scope="col">Movement</th><th scope="col">Amount</th><th scope="col">Customers</th></tr>
          </thead>
          <tbody>
            <tr><th scope="row">{data.comparison.previous.label}</th><td>{money(data.previous_total)}</td><td>—</td></tr>
            {steps.map((s) => (
              <tr key={s.kind}>
                <th scope="row">{BUCKET_LABEL[s.kind] ?? s.kind}</th>
                <td>{s.amount >= 0 ? "+" : "−"}{money(Math.abs(s.amount))}</td>
                <td>{s.customers}</td>
              </tr>
            ))}
            <tr><th scope="row">{data.comparison.current.label}</th><td>{money(data.current_total)}</td><td>—</td></tr>
          </tbody>
        </table>
      }
    >
      <svg
        viewBox={`0 0 100 ${HEIGHT}`}
        preserveAspectRatio="none"
        className="viz-waterfall"
        style={{ width: "100%", height: HEIGHT }}
      >
        {/* Zero rule only when the range actually crosses it. */}
        {min < 0 && max > 0 && (
          <line x1="0" x2="100" y1={y(0)} y2={y(0)} stroke="var(--viz-rule)" strokeWidth="1"
                vectorEffect="non-scaling-stroke" />
        )}

        {/* Opening balance */}
        <Bar x={colW * 0.5 - barW / 2} w={barW} top={y(data.previous_total)} bottom={y(min < 0 ? 0 : min)}
             fill="var(--viz-neutral)" label={data.comparison.previous.label}
             value={money(data.previous_total)} />

        {steps.map((s, i) => {
          const sign = BUCKET_SIGN[s.kind] ?? 0;
          const x = colW * (i + 1.5) - barW / 2;
          const top = y(Math.max(s.from, s.to));
          const bottom = y(Math.min(s.from, s.to));
          const fill = sign < 0 ? "var(--viz-loss)" : sign > 0 ? "var(--viz-gain)" : "var(--viz-neutral)";
          return (
            <g key={s.kind}>
              {/* Connector to the previous bar — this is the reconciliation,
                  drawn rather than asserted. */}
              <line x1={colW * (i + 0.5) + barW / 2} x2={x} y1={y(s.from)} y2={y(s.from)}
                    stroke="var(--viz-rule)" strokeWidth="1" strokeDasharray="2 2"
                    vectorEffect="non-scaling-stroke" />
              <Bar x={x} w={barW} top={top} bottom={bottom} fill={fill}
                   label={BUCKET_LABEL[s.kind] ?? s.kind}
                   value={`${s.amount >= 0 ? "+" : "−"}${money(Math.abs(s.amount))}`}
                   sub={`${s.customers}`}
                   title={s.top.map((t) => `${t.label}: ${t.delta >= 0 ? "+" : "−"}${money(Math.abs(t.delta))}`).join("\n")}
                   onClick={s.top[0] && onDrill ? () => onDrill(s.top[0].customer_id) : undefined} />
            </g>
          );
        })}

        {/* Closing balance */}
        <Bar x={colW * (cols - 0.5) - barW / 2} w={barW} top={y(data.current_total)}
             bottom={y(min < 0 ? 0 : min)} fill="var(--viz-neutral)"
             label={data.comparison.current.label} value={money(data.current_total)} />
      </svg>
    </Figure>
  );
}

function Bar({
  x, w, top, bottom, fill, label, value, sub, title, onClick,
}: {
  x: number; w: number; top: number; bottom: number; fill: string;
  label: string; value: string; sub?: string; title?: string; onClick?: () => void;
}) {
  const h = Math.max(Math.abs(bottom - top), 2);
  return (
    <g
      className={onClick ? "viz-bar viz-bar-clickable" : "viz-bar"}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } } : undefined}
      aria-label={onClick ? `${label}, ${value}. Open the largest contributor.` : undefined}
    >
      {title && <title>{title}</title>}
      {/* rx in user units would stretch under a non-uniform viewBox, so the
          radius is applied via CSS where it stays circular. */}
      <rect x={x} y={Math.min(top, bottom)} width={w} height={h} fill={fill} />
      <text x={x + w / 2} y={Math.min(top, bottom) - 6} className="viz-bar-value"
            textAnchor="middle">{value}</text>
      <text x={x + w / 2} y={HEIGHT - 30} className="viz-bar-label" textAnchor="middle">{label}</text>
      {sub && (
        <text x={x + w / 2} y={HEIGHT - 16} className="viz-bar-sub" textAnchor="middle">
          {sub} customers
        </text>
      )}
    </g>
  );
}
