// Small shared presentation pieces for the Decision Platform.
import type { ReactNode } from "react";
import type { DecisionDetail, Fact } from "./types";
import { CONF_LABEL, TYPE_LABEL, aiState, factLabel, factValue } from "./format";

// Shared with the Quote Builder — see src/Tip.tsx.
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
