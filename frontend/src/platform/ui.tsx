// Small shared presentation pieces for the Decision Platform.
import type { ReactNode } from "react";
import type { DecisionDetail, Fact } from "./types";
import { CONF_LABEL, TYPE_LABEL, aiState, factLabel, factValue, stateFieldLabel } from "./format";
import { money } from "../money";
import type { DecisionAction, DecisionImpact, DecisionRanking } from "./types";

// Shared with the Quote Builder — see src/Tip.tsx. Imported as well as
// re-exported: the panels below use it, and a module cannot read its own
// re-export.
import { Labelled } from "../Tip";
export { Tip, Labelled } from "../Tip";

type BpProps = { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>;

export function Bp({ children, className = "", ...rest }: BpProps) {
  return (
    <div className={`bp ${className}`} {...rest}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      {children}
    </div>
  );
}

export function Pri({ band }: { band: string }) {
  return <span className={`pri ${band}`}>{band}</span>;
}

/** Confidence in the *recommendation*.
 *
 * When the AI degraded or failed there is no recommendation to be confident
 * about — only the deterministic reading. Showing "High confidence" beside
 * "AI output failed validation" (which the card used to do) reads as a
 * contradiction and quietly erodes trust in every other badge. */
export function Conf({ level, aiStatus }: { level?: string; aiStatus?: string }) {
  const state = aiStatus ? aiState(aiStatus) : "ok";
  if (state === "degraded" || state === "failed") {
    return <span className="conf deterministic">Facts only · no AI reading</span>;
  }
  const label = (level && CONF_LABEL[level]) || "—";
  return <span className={`conf ${label}`}>{label} evidence</span>;
}

export function FactChip({ f }: { f: Fact }) {
  return (
    <span className="factchip">
      <span className="k">{factLabel(f.label)}</span>
      <span className="v">{factValue(f.label, f.value)}</span>
    </span>
  );
}

/** The AI interpretation region — renders the special states the design mandates
 * (AI unavailable / withheld) as distinct panels, never a hedged recommendation. */
export function Interpretation({ d }: { d: DecisionDetail }) {
  const state = aiState(d.interpretation.status);
  const suff = d.confidence?.evidence_sufficiency;

  if (state === "failed") {
    return (
      <div className="state-panel">
        <div className="state-mark">Interpretation unavailable</div>
        <p style={{ margin: 0, fontSize: 14 }}>
          The reasoning service did not respond. The facts, history and evidence on the left are
          read straight from your systems and are all present — only the reading of them is missing.
          You can still act and record a decision.
        </p>
      </div>
    );
  }
  if (state === "withheld" || suff === "INSUFFICIENT") {
    return (
      <div className="state-panel">
        <div className="state-mark">Recommendation withheld</div>
        <p style={{ margin: 0, fontSize: 14 }}>
          {d.interpretation.explanation ||
            "The evidence does not support a confident recommendation. The movement is shown; a judgement is withheld rather than manufactured."}
        </p>
        {d.confidence?.reasons?.length ? (
          <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 13 }}>
            {d.confidence.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  }
  // Degraded = the model answered but failed validation, so what follows is the
  // deterministic reading, not a recommendation. Label it for what it is.
  if (state === "degraded") {
    return (
      <div className="state-panel">
        <div className="state-mark">Deterministic reading · no AI recommendation</div>
        <p style={{ margin: 0, fontSize: 14 }}>{d.interpretation.explanation}</p>
        <p style={{ marginTop: 8, fontSize: 12, color: "var(--color-neutral-700)" }}>
          The model responded but its answer failed validation, so it was discarded. The sentence
          above is generated from the signal's own figures.
        </p>
      </div>
    );
  }
  return (
    <div className="interp">
      <div className="interp-mark">AI recommendation</div>
      {d.interpretation.explanation && <p style={{ marginBottom: 8 }}>{d.interpretation.explanation}</p>}
      {d.interpretation.recommendation && <p className="rec">{d.interpretation.recommendation}</p>}
      {d.interpretation.caveat && (
        <p style={{ marginTop: 8, fontSize: 12.5, color: "var(--color-accent-800)" }}>{d.interpretation.caveat}</p>
      )}
    </div>
  );
}

export function typeLabel(t: string): string {
  return TYPE_LABEL[t] || t;
}

/* ── the state-derived decision card ──────────────────────────────────────────
 *
 * A different card from the interpreted one, because it makes a different kind
 * of claim. An AI card says "here is a reading of the evidence, and here is how
 * confident we are in it". This one says "here is what this is worth, here is
 * the arithmetic, and here is where every number came from" — so the reader
 * checks it rather than trusting it.
 *
 * Nothing here is a recommendation. The actions are what the situation
 * *permits*; choosing between them is the reason a person is paid.
 */

/** What the situation is worth, and what that number is. */
export function ImpactPanel({ impact }: { impact: DecisionImpact }) {
  if (!impact?.financial) return null;
  const operational = Object.entries(impact.operational || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <div className="impact">
      <div className="impact-mark">Business impact</div>
      <div className="impact-figure">{money(impact.financial)}</div>
      {/* The sentence matters as much as the figure: capital locked and annual
          holding cost can be the same number and are not the same claim. */}
      {impact.basis && <div className="impact-basis">{impact.basis}</div>}
      {impact.monthly && (
        <div className="impact-monthly">
          {money(impact.monthly)} <span>a month while it sits</span>
        </div>
      )}
      {operational.length > 0 && (
        <div className="impact-ops">
          {/* Lower-cased because these read as a phrase after the number —
              "1,000 on hand", not "1,000 On hand". The same labels are
              sentence-cased in the evidence table, where they are row headings
              rather than the tail of a clause. */}
          {operational.map(([k, v]) => (
            <span key={k}>
              <b>{String(v)}</b>{" "}
              {(() => { const l = stateFieldLabel(k); return l.charAt(0).toLowerCase() + l.slice(1); })()}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Why this exists, and the state fields that produced it. */
export function WhyPanel({ rationale, evidence }:
  { rationale: string | null; evidence: Record<string, unknown> }) {
  const rows = Object.entries(evidence || {})
    .filter(([k, v]) => v !== null && v !== undefined && v !== ""
                        && k !== "state" && k !== "key");
  return (
    <>
      <div className="section-h">
        <Labelled tip="Assembled from the numbers that triggered this, not written by a model. Every figure in it appears in the state below, so the sentence can be checked rather than believed.">
          Why this exists
        </Labelled>
      </div>
      {rationale && <p className="why-text">{rationale}</p>}
      {rows.length > 0 && (
        <Bp style={{ padding: "8px 14px" }}>
          <table className="facttable">
            <tbody>
              {rows.map(([k, v]) => (
                <tr key={k}>
                  <td>{stateFieldLabel(k)}</td>
                  <td className="fv">{String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Bp>
      )}
    </>
  );
}

/** The ranking, shown working. A queue position nobody can check is a queue
 *  position nobody argues with, and one nobody argues with is one nobody
 *  reads. */
export function RankingPanel({ ranking }: { ranking: DecisionRanking }) {
  if (!ranking?.score && ranking?.score !== 0) return null;
  return (
    <div className="ranking">
      <div className="ranking-mark">
        <Labelled tip="Money first, lateness second, both capped. The rupee scale is a versioned setting, so re-tuning the queue does not make last quarter's ordering unexplainable.">
          Why it sits here
        </Labelled>
      </div>
      <div className="ranking-sum">
        <span><b>{ranking.money_points}</b> from {money(ranking.financial)}</span>
        <span className="op">+</span>
        <span>
          <b>{ranking.urgency_points}</b>{" "}
          {ranking.days_past_due
            ? `from ${ranking.days_past_due} days past due`
            : "— nothing was promised"}
        </span>
        <span className="op">=</span>
        <span className="total"><b>{ranking.score}</b>/100</span>
      </div>
      <div className="ranking-scale">
        one point per {money(ranking.rupees_per_point)} · money caps at{" "}
        {ranking.money_cap}, lateness at {ranking.urgency_cap}
      </div>
    </div>
  );
}

/** What can be done. Presented, never chosen — so they are rendered as a list
 *  of equals rather than one primary button and some alternatives. */
export function ActionsPanel({ actions }: { actions: DecisionAction[] }) {
  if (!actions?.length) return null;
  return (
    <>
      <div className="section-h">
        <Labelled tip="What this situation permits. The platform lists them and does not pick one — which of these is right depends on the customer, the supplier and the month, and none of that is in the data.">
          Available actions
        </Labelled>
      </div>
      <ul className="actions-list">
        {actions.map((a) => <li key={a.key}>{a.label}</li>)}
      </ul>
    </>
  );
}
