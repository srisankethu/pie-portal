// The remaining overview views.
//
// Each answers its three questions in the same order and with the same
// furniture, so moving between them costs nothing: a headline that states what
// changed, a decomposition that says why, and a control that does something
// about it. Where a view cannot answer one of the three, it says so rather than
// filling the space.
//
// **Understandable before impressive.** Every chart here is a form a reader
// already knows — ranked bars, a small-multiple sparkline, a matrix — rather
// than a novel encoding that needs a key. The originality is in *what is
// measured* (evidence as an axis, break-even as the headline, a decomposition
// that reconciles), not in making the reader learn a new shape. A chart nobody
// can read is a chart nobody acts on.
//
// **Size-adaptive by measurement, not by media query.** A narrow panel on a wide
// screen has the same problem a phone does, and only the container knows.

import Button from "@mui/material/Button";
import { MonthPicker as SharedMonthPicker } from "./Seg";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { money } from "../../money";
import { Tip } from "../../Tip";
import { ChartTip, InlineLink, Unavailable, VarianceIndicator } from "../kit";
import { DataGrid, numeric } from "../DataGrid";
import { papi } from "../api";
import type { PlatformSession } from "../types";
import { vizPath } from "../route";
import { Figure, Panel, stateOf } from "./Panel";
import {
  BAND_COLOR, BUCKET_LABEL, BUCKET_MEANING, BUCKET_SHADE, BUCKET_SIGN,
  CONFIDENCE_LABEL, CONFIDENCE_OPACITY,
} from "./tokens";
import { type Envelope, useInsight } from "./useInsight";
import { compactMoney, thinLabels, useMeasure } from "./useMeasure";


function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function signedPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : "−"}${(Math.abs(v) * 100).toFixed(digits)}%`;
}

// ── Commercial Weather ──────────────────────────────────────────────────────
// Fronts, not a score. Each dimension keeps its own band, its own sentence and
// its own way in; a single number out of 100 would hide which thing is wrong.
export function WeatherScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const [months, setMonths] = useState(3);
  const { data, loading, error, reload } = useInsight(
    "weather",
    () => papi.weather(session.token, months), [session.token, months]);

  const fronts = (data?.fronts as Record<string, unknown>[] | undefined) ?? [];
  return (
    <Panel
      title="Commercial weather"
      question="Which parts of the business need attention, and which do not"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error}
      emptyReason={data?.empty_reason as string}
      onRetry={reload}
      wide
      actions={<MonthPicker value={months} onChange={setMonths} id="weather-months" />}
    >
      <ul className="fronts">
        {fronts.map((f) => (
          <li key={String(f.key)} className={`front front-${String(f.band).toLowerCase()}`}>
            <span
              className="front-band"
              style={{ background: BAND_COLOR[String(f.band)] ?? "var(--viz-neutral)" }}
              aria-hidden="true"
            />
            <div className="front-body">
              <div className="front-top">
                <h4>{String(f.label)}</h4>
                {/* The band is a word as well as a colour. A status swatch on its
                    own fails every colour-vision and forced-colours case. */}
                <span className="front-tag">{String(f.band).toLowerCase()}</span>
              </div>
              <p className="front-headline">{String(f.headline)}</p>
              <p className="viz-muted">{String(f.detail)}</p>
              {f.drill_to ? (
                <Button type="button" variant="outlined" size="small"
                        onClick={() => onNavigate(String(f.drill_to))}>
                  Look into it
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      <p className="viz-muted viz-footnote">{String(data?.note ?? "")}</p>
    </Panel>
  );
}

// ── Opportunity Radar ───────────────────────────────────────────────────────
// Two axes: money at stake, and how well evidenced it is. Ranked bars rather
// than a scatter — a scatter of six points is a puzzle, and the reader's real
// question is "what do I work on first", which is a rank, not a position.
export function OpportunityScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const { data, loading, error, reload } = useInsight(
    "opportunities",
    () => papi.opportunities(session.token), [session.token]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const rows = (data?.opportunities as Record<string, unknown>[] | undefined) ?? [];
  const totals = (data?.totals as Record<string, number> | undefined) ?? {};
  const widest = Math.max(...rows.map((r) => Number(r.impact) || 0), 1);
  const currency = String(data?.currency ?? "INR");

  return (
    <Panel
      title="Opportunity radar"
      question="Where is money on the table, and how sure are we"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error}
      emptyReason={data?.empty_reason as string}
      onRetry={reload}
      wide
    >
      <div className="radar-totals">
        <div>
          <span className="radar-total-value">{money(totals.confident_impact ?? 0)}</span>
          <span className="viz-muted">
            {" "}
            <Tip text="Only relationships with sufficient evidence are counted here. The weaker ones are listed below but deliberately left out of the total — a headline number that includes guesses is a headline number nobody can quote.">
              <span>well-evidenced</span>
            </Tip>
          </span>
        </div>
        <div className="viz-muted">
          {totals.count ?? 0} relationship{totals.count === 1 ? "" : "s"} flagged ·
          {" "}{money(totals.total_impact ?? 0)} including weaker evidence
        </div>
      </div>

      <div ref={ref}>
        <Figure
          caption="Bar length is money at stake. Fill strength is how well evidenced it is — solid bars are the ones to work first."
          summary={rows.map((r) =>
            `${r.customer_label} / ${r.product_label}: ${money(Number(r.impact))}, ${CONFIDENCE_LABEL[String(r.confidence)]}`,
          ).join("; ")}
          table={
            <table className="viz-table">
              <thead>
                <tr>
                  <th scope="col">Customer</th><th scope="col">Item</th>
                  <th scope="col">At stake</th><th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{String(r.customer_label)}</td>
                    <td>{String(r.product_label)}</td>
                    <td>{money(Number(r.impact))}</td>
                    <td>{CONFIDENCE_LABEL[String(r.confidence)]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          <ol className="radar-rows">
            {rows.map((r, i) => (
              <li key={i} className="radar-row">
                <button
                  type="button"
                  className="radar-hit"
                  onClick={() => onNavigate(`customer/${String(r.customer_id)}`)}
                  aria-label={`${r.customer_label}, ${r.product_label}. ${money(Number(r.impact))} at stake, ${CONFIDENCE_LABEL[String(r.confidence)]}. Open the relationship.`}
                >
                  <span className="radar-names">
                    <strong>{String(r.customer_label)}</strong>
                    <span className="viz-muted">{String(r.product_label)}</span>
                  </span>
                  <span className="radar-track" aria-hidden="true">
                    <span
                      className="radar-fill"
                      style={{
                        width: `${(Number(r.impact) / widest) * 100}%`,
                        opacity: CONFIDENCE_OPACITY[String(r.confidence)] ?? 0.4,
                      }}
                    />
                  </span>
                  <span className="radar-figures">
                    <strong>
                      {room.tight
                        ? compactMoney(Number(r.impact), currency)
                        : money(Number(r.impact))}
                    </strong>
                    <span className="radar-conf">
                      {CONFIDENCE_LABEL[String(r.confidence)]}
                      {r.annualized ? " · per year" : ""}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </Figure>
      </div>
    </Panel>
  );
}

// ── Lost Revenue Explorer ───────────────────────────────────────────────────
export function LostRevenueScreen({ session }: { session: PlatformSession }) {
  const [months, setMonths] = useState(3);
  const { data, loading, error, reload } = useInsight(
    "lostRevenue",
    () => papi.lostRevenue(session.token, months), [session.token, months]);

  const causes = (data?.causes as Record<string, unknown>[] | undefined) ?? [];
  const total = Number(data?.total_lost ?? 0);

  const CAUSE_TEXT: Record<string, string> = {
    STOPPED_BUYING: "Stopped ordering entirely",
    COST_INCREASE_NOT_PASSED: "Cost rose and was not passed on",
    MARGIN_EROSION: "Margin fell without a cost cause",
    UNEXPLAINED: "No cause the data can name",
  };

  return (
    <Panel
      title="Where revenue went"
      question="What stopped, and what the data says caused it"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error}
      emptyReason={data?.empty_reason as string}
      onRetry={reload}
      wide
      actions={<MonthPicker value={months} onChange={setMonths} id="lost-months" />}
    >
      <div className="story-hero">
        {/* Negated: `total` is the magnitude of what was lost, and the hero has
            always shown it with a minus. The component needs the sign to pick
            its direction. */}
        <span className="story-hero-value">
          <VarianceIndicator value={-total} sx={{ fontSize: "inherit" }} />
        </span>
        <span className="story-hero-label">against the previous period</span>
      </div>

      <ul className="cause-list">
        {causes.map((c) => {
          const amount = Number(c.amount);
          const share = total ? amount / total : 0;
          const unexplained = c.cause === "UNEXPLAINED";
          return (
            <li key={String(c.cause)} className="cause">
              <div className="cause-head">
                <h4>{CAUSE_TEXT[String(c.cause)] ?? String(c.cause)}</h4>
                <span className="cause-amount">{money(amount)}</span>
              </div>
              <div className="cause-track" aria-hidden="true">
                <span
                  className={`cause-fill${unexplained ? " hatched" : ""}`}
                  style={{ width: `${share * 100}%` }}
                />
              </div>
              <p className="viz-muted">
                {pct(share, 0)} of the fall · {String(c.count)} customer
                {c.count === 1 ? "" : "s"}
                {unexplained && (
                  <>
                    {" — "}
                    <Tip text="These customers spent less and no persisted metric explains it. Shown as its own bucket rather than distributed across the others, because a complete-looking chart built on a guess is worse than an honest gap.">
                      <span>shown as a gap, not guessed at</span>
                    </Tip>
                  </>
                )}
              </p>
              <ul className="cause-members">
                {(c.customers as Record<string, unknown>[] ?? []).slice(0, 5).map((m, i) => (
                  <li key={i}>
                    <InlineLink to={vizPath(`customer/${String(m.customer_id)}`)}>
                      {String(m.label)}
                    </InlineLink>
                    <span className="viz-muted"> −{money(Number(m.lost))}</span>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

// ── Customer Journey ────────────────────────────────────────────────────────
// A stacked band per month of how many customers were in each state. The
// question it answers — "is the base churning?" — is invisible in revenue,
// because ten gained and ten lost is flat.
export function JourneyScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const [months, setMonths] = useState(12);
  const { data, loading, error, reload } = useInsight(
    "journey",
    () => papi.journey(session.token, months), [session.token, months]);
  const [ref, room] = useMeasure<HTMLDivElement>();
  /** Which band is open, as (month label, state). One at a time — two open
   *  drill-downs is two tables nobody asked to compare. */
  const [focus, setFocus] = useState<{ month: string; state: string } | null>(null);
  // A band from a 24-month window does not exist in a 6-month one.
  useEffect(() => { setFocus(null); }, [months]);

  const series = (data?.series as Record<string, unknown>[] | undefined) ?? [];
  const dormant = (data?.dormant as Record<string, unknown> | undefined) ?? {};
  const ORDER = ["NEW", "RECOVERED", "GROWN", "STABLE", "SHRUNK", "LOST"];
  const maxCount = Math.max(
    ...series.map((p) => ORDER.reduce(
      (a, k) => a + Number((p.counts as Record<string, number>)[k] ?? 0), 0)), 1);

  const labels = useMemo(() => thinLabels(series, room, 64), [series, room]);

  // The open band's members, and the honest total behind them. The server caps
  // the list; the count is uncapped, so the heading can say "the 20 largest of
  // 214" instead of implying 20 is all there was.
  const focusPoint = focus
    ? series.find((p) => String(p.label) === focus.month)
    : undefined;
  const focusRows = ((focusPoint?.members as Record<string, Record<string, unknown>[]>
    | undefined)?.[focus?.state ?? ""]) ?? [];
  const focusTotal = focus
    ? Number((focusPoint?.counts as Record<string, number> | undefined)?.[focus.state] ?? 0)
    : 0;

  return (
    <Panel
      title="Customer journey"
      question="Is the customer base growing, holding, or turning over"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error}
      emptyReason={data?.empty_reason as string}
      onRetry={reload}
      wide
      actions={
        <MonthPicker value={months} onChange={setMonths} id="journey-months"
                     options={[6, 12, 24]} />
      }
    >
      <div ref={ref}>
        <Figure
          caption="Each column is a month; each band is how many customers were in that state versus the month before."
          summary={series.map((p) =>
            `${p.label}: ` + ORDER
              .filter((k) => (p.counts as Record<string, number>)[k])
              .map((k) => `${(p.counts as Record<string, number>)[k]} ${BUCKET_LABEL[k]}`)
              .join(", "),
          ).join("; ")}
          table={
            <DataGrid<Record<string, unknown>>
              ariaLabel="Customer journey by month"
              pageSize={24}
              filters={false}
              rows={series.map((p) => {
                const c = p.counts as Record<string, number>;
                return {
                  month: String(p.label),
                  ...Object.fromEntries(ORDER.map((k) => [k, c[k] ?? 0])),
                  total: ORDER.reduce((a, k) => a + (c[k] ?? 0), 0),
                };
              })}
              columns={[
                { field: "month", headerName: "Month", width: 140, flex: 0 },
                ...ORDER.map((k) => numeric<Record<string, unknown>>(
                  k, BUCKET_LABEL[k], (v) => String(v),
                  { width: 130, flex: 0, headerTooltip: BUCKET_MEANING[k] })),
                numeric<Record<string, unknown>>("total", "Customers trading",
                                                 (v) => String(v), { width: 170, flex: 0 }),
              ]}
            />
          }
        >
          <div className="journey">
            {series.map((p, i) => {
              const counts = p.counts as Record<string, number>;
              return (
                <div className="journey-col" key={i}>
                  <div className="journey-stack" title={`${p.label}`}>
                    {ORDER.every((k) => !Number(counts[k] ?? 0)) && (
                      <span className="journey-none"
                            title={`${p.label} · no customer activity`} />
                    )}
                    {ORDER.map((k) => {
                      const n = Number(counts[k] ?? 0);
                      if (!n) return null;
                      const sign = BUCKET_SIGN[k] ?? 0;
                      const share = n / maxCount;
                      const monthTotal = ORDER.reduce(
                        (a, key) => a + Number(counts[key] ?? 0), 0);
                      const on = focus?.month === String(p.label) && focus?.state === k;
                      return (
                        <ChartTip
                          key={k}
                          title={
                            <>
                              <strong>{BUCKET_LABEL[k]}</strong> · {String(p.label)}
                              <br />
                              {n} of {monthTotal} customers trading
                              {" "}({Math.round((n / monthTotal) * 100)}%)
                              <br />
                              <span style={{ opacity: 0.8 }}>{BUCKET_MEANING[k]}</span>
                              <br />
                              <span style={{ opacity: 0.8 }}>Click to list them</span>
                            </>
                          }
                        >
                          {/* A button, not a span. The band is the control that
                              answers "who are those 21?", and a click target
                              that is not a button is one a keyboard cannot
                              reach. */}
                          <button
                            type="button"
                            aria-pressed={on}
                            aria-label={`${n} ${BUCKET_LABEL[k]} in ${String(p.label)}`}
                            className={`journey-seg ${sign < 0 ? "neg" : sign > 0 ? "pos" : "flat"}`
                              + `${on ? " on" : ""}`}
                            style={{
                              height: `${share * 100}%`,
                              // Lightness separates the states inside one
                              // direction; see BUCKET_SHADE.
                              opacity: BUCKET_SHADE[k] ?? 1,
                            }}
                            onClick={() => setFocus(
                              on ? null : { month: String(p.label), state: k })}
                          >
                            {/* The count, printed inside the band when there is
                                room for it. Without this the chart shows relative
                                heights and no quantity — a reader cannot tell one
                                customer from five, which is the entire question
                                this screen exists to answer. Segments too short to
                                hold a numeral keep it in the tooltip and the
                                table rather than overflowing. */}
                            {share > 0.13 && !room.cramped && (
                              <span className="journey-count">{n}</span>
                            )}
                          </button>
                        </ChartTip>
                      );
                    })}
                  </div>
                  <span className="journey-label">
                    {labels[i] ? String(p.label).replace(/ \d{4}$/, "") : ""}
                  </span>
                </div>
              );
            })}
          </div>
          {/* Every state named, not three families. The chart draws six bands
              and the legend used to name three, so a column showing two red
              blocks was unreadable by construction: nothing on the page said
              the upper one was "Lost" and the lower "Spent less", or why that
              distinction is the whole point of the view. */}
          <Legend
            items={ORDER.map((k) => ({
              label: BUCKET_LABEL[k],
              cls: (BUCKET_SIGN[k] ?? 0) < 0 ? "neg" : (BUCKET_SIGN[k] ?? 0) > 0 ? "pos" : "flat",
              opacity: BUCKET_SHADE[k] ?? 1,
              tip: BUCKET_MEANING[k],
            }))}
          />
        </Figure>
      </div>

      {focus && (
        <div className="journey-drill">
          <h4>
            {BUCKET_LABEL[focus.state]} · {focus.month}
            <span className="viz-muted">
              {" "}— {focusTotal} customer{focusTotal === 1 ? "" : "s"}
              {focusTotal > focusRows.length &&
                `, the ${focusRows.length} largest movements shown`}
            </span>
            {" "}
            <InlineLink onClick={() => setFocus(null)}>Close</InlineLink>
          </h4>
          {focusRows.length === 0 ? (
            <p className="viz-muted">
              This month's states were counted before customer names travelled
              with them. Re-run the page to load the list.
            </p>
          ) : (
            <DataGrid<Record<string, unknown>>
              ariaLabel={`${BUCKET_LABEL[focus.state]} customers in ${focus.month}`}
              pageSize={20}
              rows={focusRows}
              onRowClick={(r) => onNavigate(`customer/${String(r.customer_id)}`)}
              columns={[
                { field: "label", headerName: "Customer", flex: 1, minWidth: 240,
                  filter: "agTextColumnFilter" },
                numeric<Record<string, unknown>>("previous", "Month before",
                                                 (v) => money(v), { width: 150, flex: 0 }),
                numeric<Record<string, unknown>>("current", "This month",
                                                 (v) => money(v), { width: 150, flex: 0 }),
                numeric<Record<string, unknown>>("delta", "Movement", (v) =>
                  `${v >= 0 ? "+" : "−"}${money(Math.abs(v))}`,
                  { width: 160, flex: 0, sort: "asc" }),
              ]}
            />
          )}
        </div>
      )}

      {Number(dormant.count ?? 0) > 0 && (
        <div className="dormant">
          <h4>
            {String(dormant.count)} gone quiet
            <span className="viz-muted">
              {" "}— no order in {String(dormant.threshold_months)} months
            </span>
          </h4>
          <ul className="cause-members">
            {(dormant.customers as Record<string, unknown>[] ?? []).slice(0, 8).map((c, i) => (
              <li key={i}>
                <InlineLink to={vizPath(`customer/${String(c.customer_id)}`)}>
                  {String(c.label)}
                </InlineLink>
                <span className="viz-muted">
                  {" "}{money(Number(c.lifetime_revenue))} lifetime ·{" "}
                  {String(c.months_quiet)} months quiet
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

// ── Decision Impact Simulator ───────────────────────────────────────────────
// The headline is break-even, not a projection. Everything that needs a
// behavioural guess is labelled as the user's assumption, in the UI as well as
// in the payload.
export function SimulatorScreen({ session }: { session: PlatformSession }) {
  // The Storyboard's "Model it" links here as `simulate?scenario=MARGIN_FLOOR`
  // (Storyboard.tsx:206). Reading it was missing, so the beat about the margin
  // floor opened a price-change model: an answer to a question the reader did
  // not ask, with the URL claiming otherwise.
  //
  // `useSearchParams`, never `window.location.search` — the router may hold the
  // query inside the hash, where `location.search` is empty.
  const [params] = useSearchParams();
  const [scenario, setScenario] = useState(() => params.get("scenario") || "PRICE_CHANGE");
  const [pctChange, setPctChange] = useState(5);
  const [volume, setVolume] = useState(0);
  const [floor, setFloor] = useState(20);
  const [result, setResult] = useState<Envelope | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: scenarios } = useInsight(
    "simulationScenarios",
    () => papi.simulationScenarios(session.token), [session.token]);

  // The URL is a value a person can type, so it is checked against the list the
  // server actually offers rather than trusted. An unknown name falls back to
  // the default instead of being posted and rejected — the reader would
  // otherwise get an error where the link promised a model. Runs once the list
  // arrives, and only to reject: a name that is in the list is left alone.
  useEffect(() => {
    const available = scenarios?.available as { scenario?: string }[] | undefined;
    if (!available) return;
    const known = available.map((s) => s.scenario);
    setScenario((current) => (known.includes(current) ? current : "PRICE_CHANGE"));
  }, [scenarios]);

  const run = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { scenario };
      if (scenario === "PRICE_CHANGE") {
        body.price_change_pct = pctChange / 100;
        body.assumed_volume_change = volume / 100;
      }
      if (scenario === "MARGIN_FLOOR") body.floor = floor / 100;
      setResult(await papi.simulate(session.token, body));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }, [session.token, scenario, pctChange, volume, floor]);

  useEffect(() => { void run(); }, [run]);

  const breakEven = result?.break_even_volume_change as number | null | undefined;
  const baseline = result?.baseline as Record<string, number> | undefined;
  const projected = result?.projected as Record<string, number> | undefined;
  const delta = result?.delta as Record<string, number> | undefined;
  const unavailable = (scenarios?.unavailable as Record<string, string>[]) ?? [];

  return (
    <Panel
      title="Decision impact simulator"
      question="What would this change be worth, and what has to be true"
      state={stateOf(running && !result, error, result?.empty_reason as string)}
      error={error}
      emptyReason={result?.empty_reason as string}
      onRetry={run}
      wide
    >
      <div className="sim-controls">
        <fieldset>
          <legend>Scenario</legend>
          <div className="sim-choices">
            {[
              ["PRICE_CHANGE", "Change prices"],
              ["MARGIN_FLOOR", "Lift to a margin floor"],
            ].map(([id, label]) => (
              <label key={id} className={scenario === id ? "chip chip-on" : "chip"}>
                <input type="radio" name="scenario" value={id}
                       checked={scenario === id}
                       onChange={() => { setScenario(id); setResult(null); }} />
                {label}
              </label>
            ))}
          </div>
        </fieldset>

        {scenario === "PRICE_CHANGE" && (
          <>
            <Slider id="sim-price" label="Price change" value={pctChange}
                    min={-20} max={30} onChange={setPctChange} suffix="%" />
            <Slider id="sim-volume" label="Assumed volume response" value={volume}
                    min={-50} max={20} onChange={setVolume} suffix="%"
                    hint="Your assumption. The platform has no basis for predicting it." />
          </>
        )}
        {scenario === "MARGIN_FLOOR" && (
          <Slider id="sim-floor" label="Margin floor" value={floor}
                  min={5} max={45} onChange={setFloor} suffix="%" />
        )}
      </div>

      {result && scenario === "PRICE_CHANGE" && (
        <>
          {/* The honest headline: needs no behavioural assumption at all. */}
          <div className="sim-headline">
            <span className="sim-headline-label">
              You could lose up to
              <Tip text="Below this the price change stops paying for itself. It follows from cost, price and margin alone — no assumption about customer behaviour is involved, which is why it leads rather than the projection.">
                <span> break-even volume</span>
              </Tip>
            </span>
            <span className="sim-headline-value">
              {breakEven == null ? "not recoverable" : pct(Math.abs(breakEven), 1)}
            </span>
            <span className="viz-muted">
              {breakEven == null
                ? "at this price cut, no volume gain compensates the lost contribution"
                : "of volume before this is worse than doing nothing"}
            </span>
          </div>

          <div className="sim-grid">
            <SimCol title="Today" revenue={baseline?.revenue} profit={baseline?.gross_profit}
                    margin={baseline?.margin} />
            <SimCol title="At your assumption" revenue={projected?.revenue}
                    profit={projected?.gross_profit} margin={projected?.margin}
                    deltaRevenue={delta?.revenue} deltaProfit={delta?.gross_profit} />
          </div>
        </>
      )}

      {result && scenario === "MARGIN_FLOOR" && (
        <div className="sim-grid">
          <div className="sim-col">
            <h4>Lines below the floor</h4>
            <p className="sim-value">
              {String(result.lines_below_floor)}{" "}
              <span className="viz-muted">of {String(result.lines_considered)}</span>
            </p>
          </div>
          <div className="sim-col">
            <h4>Upper bound on the gain</h4>
            <p className="sim-value">{money(Number(result.profit_gain_if_all_lifted ?? 0))}</p>
            <p className="viz-muted">
              Revenue exposed: {money(Number(result.revenue_at_risk ?? 0))}
            </p>
          </div>
        </div>
      )}

      {result?.assumption_note ? (
        <p className="sim-note">{String(result.assumption_note)}</p>
      ) : null}

      {unavailable.length > 0 && (
        <details className="sim-blocked">
          {/* Counted rather than spelled. `simulate.UNAVAILABLE` has been
              narrowed once already as blocked scenarios were built, and a
              hardcoded "Two" is only correct until the next time. */}
          <summary>
            {unavailable.length} scenario{unavailable.length === 1 ? " is" : "s are"}{" "}
            blocked on data
          </summary>
          <Unavailable items={unavailable} verb="blocked" />
        </details>
      )}
    </Panel>
  );
}

// ── small shared pieces ─────────────────────────────────────────────────────
/** This screen's window control. The picker itself now lives in `Seg.tsx`
 *  alongside the segmented control, because four screens had grown their own. */
function MonthPicker({
  value, onChange, id, options = [1, 3, 6, 12],
}: {
  value: number; onChange: (n: number) => void; id: string; options?: number[];
}) {
  return (
    <SharedMonthPicker id={id} value={value} onChange={onChange}
                       options={options} label="Period" long />
  );
}

function Slider({
  id, label, value, min, max, onChange, suffix, hint,
}: {
  id: string; label: string; value: number; min: number; max: number;
  onChange: (n: number) => void; suffix?: string; hint?: string;
}) {
  return (
    <div className="sim-slider">
      <label htmlFor={id}>
        {label}
        <output htmlFor={id}>{value > 0 ? "+" : ""}{value}{suffix}</output>
      </label>
      <input id={id} type="range" min={min} max={max} value={value}
             onChange={(e) => onChange(Number(e.target.value))} />
      {hint && <p className="viz-muted">{hint}</p>}
    </div>
  );
}

function SimCol({
  title, revenue, profit, margin, deltaRevenue, deltaProfit,
}: {
  title: string; revenue?: number; profit?: number; margin?: number | null;
  deltaRevenue?: number; deltaProfit?: number;
}) {
  return (
    <div className="sim-col">
      <h4>{title}</h4>
      <dl>
        <div><dt>Revenue</dt><dd>{money(revenue ?? 0)}
          {deltaRevenue != null && (
            <> <VarianceIndicator value={deltaRevenue} /></>
          )}
        </dd></div>
        <div><dt>Gross profit</dt><dd>{money(profit ?? 0)}
          {deltaProfit != null && (
            <> <VarianceIndicator value={deltaProfit} /></>
          )}
        </dd></div>
        <div><dt>Margin</dt><dd>{pct(margin)}</dd></div>
      </dl>
    </div>
  );
}

function Legend({ items }: {
  items: {
    label: string; cls: string;
    /** Lightness within the hue, for a palette that separates states inside a
     *  direction. Must match the mark, or the legend is describing a colour
     *  that is not on the chart. */
    opacity?: number;
    /** What the state means. A legend that only names the bands explains the
     *  colours and not the categories — "Spent less" and "Lost" are both red
     *  and both bad, and the difference between them is the decision. */
    tip?: string;
  }[];
}) {
  return (
    <ul className="viz-legend">
      {items.map((i) => (
        <li key={i.label}>
          <span className={`viz-swatch ${i.cls}`} aria-hidden="true"
                style={i.opacity != null ? { opacity: i.opacity } : undefined} />
          {i.label}
          {i.tip && <Tip label={i.label} text={i.tip} />}
        </li>
      ))}
    </ul>
  );
}

export { signedPct };
