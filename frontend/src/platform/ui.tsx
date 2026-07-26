// Small shared presentation pieces for the Decision Platform.
import type { ReactNode } from "react";
import type { DecisionDetail, Fact } from "./types";
import { CONF_LABEL, TYPE_LABEL, aiState, factLabel, factValue } from "./format";

export function Bp({ children, className = "", style }: { children: ReactNode; className?: string; style?: React.CSSProperties }) {
  return (
    <div className={`bp ${className}`} style={style}>
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

export function Conf({ level }: { level?: string }) {
  const label = (level && CONF_LABEL[level]) || "—";
  return <span className={`conf ${label}`}>{label} confidence</span>;
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
  return (
    <div className="interp">
      <div className="interp-mark">AI interpretation{state === "degraded" ? " · degraded" : ""}</div>
      {d.interpretation.explanation && <p style={{ marginBottom: 8 }}>{d.interpretation.explanation}</p>}
      {d.interpretation.recommendation && <p className="rec">{d.interpretation.recommendation}</p>}
      {d.interpretation.caveat && (
        <p style={{ marginTop: 8, fontSize: 12.5, color: "var(--color-accent-800)" }}>{d.interpretation.caveat}</p>
      )}
      {state === "degraded" && (
        <p style={{ marginTop: 8, fontSize: 12, color: "var(--color-neutral-700)" }}>
          Shown from the deterministic signal — the model response failed validation and was not used.
        </p>
      )}
    </div>
  );
}

export function typeLabel(t: string): string {
  return TYPE_LABEL[t] || t;
}
