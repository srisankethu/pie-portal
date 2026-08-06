// The homepage. A briefing, not a dashboard.
//
// The distinction is structural, not cosmetic. A dashboard is a grid of metrics
// and leaves the reader to work out which one matters today; this is an ordered
// list of *beats*, each of which is required to carry all three answers before
// it may appear:
//
//   what changed  the movement, with both periods named so it can be checked
//   why           the decomposition, from persisted rows — never a guess
//   what next     a place to go, with a count of what is waiting there
//
// A beat with no cause is not padded with a plausible one. The server emits
// `UNEXPLAINED` and this screen prints it, because "we do not know why" is a
// true and useful thing to tell someone at 9am, and inventing a reason is how a
// tool stops being trusted.
//
// Order is by what to deal with first, not by size: money already lost outranks
// money at risk, which outranks money available. Good news comes last and is
// visually quieter, because it is not an action.

import Button from "@mui/material/Button";
import { MonthPicker } from "./Seg";
import { useCallback, useEffect, useState } from "react";
import { money } from "../../money";
import { Tip } from "../../Tip";
import { VarianceIndicator } from "../kit";
import { papi } from "../api";
import type { PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { Waterfall } from "./Waterfall";

interface Beat {
  kind: string;
  headline: string;
  severity: string;
  change: Record<string, unknown>;
  cause: Record<string, unknown> | null;
  action: { label: string; route: string; count?: number; simulate?: string };
  evidence: Record<string, unknown>[];
}

interface StoryboardData {
  currency: string;
  empty_reason: string | null;
  as_of?: string;
  period?: { current: { label: string }; previous: { label: string } };
  net_change?: number;
  net_change_pct?: number | null;
  beats?: Beat[];
  restricted_withheld?: boolean;
}

const SEVERITY_ORDER: Record<string, number> = {
  HIGH: 0, MEDIUM: 1, INFO: 2, GOOD: 3,
};

const CAUSE_LABEL: Record<string, string> = {
  STOPPED_BUYING: "Customers stopped ordering",
  COST_INCREASE_NOT_PASSED: "Supplier cost rose and was not passed on",
  MARGIN_EROSION: "Margin fell without a cost cause",
  COST_NOT_PASSED: "Cost rose faster than price",
  UNEXPLAINED: "No cause the data can name",
  REVENUE_CONCENTRATION: "One customer is a large share of revenue",
  NO_RECENT_ORDERS: "No orders for six months",
  PRICED_BELOW_REFERENCE: "Priced below what the history supports",
  CUSTOMER_GROWTH: "Customers spending more, plus new ones",
};

export function Storyboard({
  session,
  onNavigate,
}: {
  session: PlatformSession;
  onNavigate: (route: string) => void;
}) {
  const [data, setData] = useState<StoryboardData | null>(null);
  const [flow, setFlow] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [months, setMonths] = useState(3);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, f] = await Promise.all([
        papi.storyboard(session.token, months),
        papi.revenueFlow(session.token, months),
      ]);
      setData(s as unknown as StoryboardData);
      setFlow(f);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session.token, months]);

  useEffect(() => { void load(); }, [load]);

  const state = stateOf(loading, error, data?.empty_reason);
  const beats = (data?.beats ?? []).slice().sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );

  return (
    <div className="storyboard">
      <header className="story-head">
        <div>
          <h2>What changed</h2>
          {data?.period && (
            <p className="viz-muted">
              {data.period.current.label} against {data.period.previous.label}
              {data.as_of && (
                <>
                  {" · "}
                  <Tip text="Periods hang from the last day the business actually traded, not from today. Anchoring on today would make the first days of a month look like a collapse, and would make a stale sync look like one too.">
                    <span>data to {data.as_of}</span>
                  </Tip>
                </>
              )}
            </p>
          )}
        </div>
        <div className="story-controls">
          <MonthPicker id="story-months" value={months} onChange={setMonths}
                       options={[1, 3, 6]} label="Compare" long />
        </div>
      </header>

      {/* The single number, stated once, with its sign carried by an arrow and
          by the word beside it as well as a colour. `.story-hero-value` still
          owns the display type scale; the component owns the direction and
          reads its two colours from the theme rather than from a hex literal
          in viz.css. */}
      {state === "ready" && data?.net_change !== undefined && (
        <div className="story-hero">
          <span className="story-hero-value">
            <VarianceIndicator value={data.net_change} sx={{ fontSize: "inherit" }} />
          </span>
          <span className="story-hero-label">
            {data.net_change < 0 ? "less" : "more"} than the period before
            {data.net_change_pct != null && (
              <> ({(Math.abs(data.net_change_pct) * 100).toFixed(1)}%)</>
            )}
          </span>
        </div>
      )}

      <Panel
        title="The briefing"
        question="What changed, why, and what to do about it"
        state={state}
        error={error}
        emptyReason={data?.empty_reason}
        onRetry={load}
        wide
      >
        <ol className="beats">
          {beats.map((beat) => (
            <li key={beat.kind} className={`beat beat-${beat.severity.toLowerCase()}`}>
              <div className="beat-mark" aria-hidden="true" />
              <div className="beat-body">
                <h4>{beat.headline}</h4>

                <dl className="beat-answers">
                  <div>
                    <dt>Why</dt>
                    <dd>
                      {CAUSE_LABEL[String(beat.cause?.primary ?? "")] ??
                        String(beat.cause?.primary ?? "Not established")}
                      {beat.cause?.note ? (
                        <span className="viz-muted"> — {String(beat.cause.note)}</span>
                      ) : null}
                    </dd>
                  </div>
                  {beat.evidence.length > 0 && (
                    <div>
                      <dt>Evidence</dt>
                      <dd className="beat-evidence">
                        {beat.evidence.map((e, i) => (
                          <span key={i} className="beat-chip">
                            {String(e.label ?? e.customer_label ?? "")}
                            {e.lost != null && ` · ${money(Number(e.lost))}`}
                            {e.impact != null && ` · ${money(Number(e.impact))}`}
                          </span>
                        ))}
                      </dd>
                    </div>
                  )}
                  <div>
                    <dt>Next</dt>
                    <dd>
                      <Button
                        type="button"
                        variant="outlined" size="small"
                        onClick={() => onNavigate(beat.action.route)}
                      >
                        {beat.action.label}
                        {beat.action.count ? ` (${beat.action.count})` : ""}
                      </Button>
                      {beat.action.simulate && (
                        <Button
                          type="button"
                          variant="outlined" size="small"
                          onClick={() => onNavigate(`simulate?scenario=${beat.action.simulate}`)}
                        >
                          Model it
                        </Button>
                      )}
                    </dd>
                  </div>
                </dl>
              </div>
            </li>
          ))}
        </ol>

        {data?.restricted_withheld && (
          <p className="viz-muted viz-footnote">
            Margin-based items are not shown for your role. They are absent from
            the response, not hidden here.
          </p>
        )}
      </Panel>

      <Panel
        title="Where the money moved"
        question="Which customers account for the change"
        state={stateOf(loading, error, (flow as { empty_reason?: string })?.empty_reason)}
        error={error}
        emptyReason={(flow as { empty_reason?: string })?.empty_reason}
        onRetry={load}
        wide
      >
        {flow && <Waterfall data={flow as never} onDrill={(id) => onNavigate(`customer/${id}`)} />}
      </Panel>
    </div>
  );
}
