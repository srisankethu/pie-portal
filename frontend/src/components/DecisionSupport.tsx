import Button from "@mui/material/Button";
import { useEffect, useState } from "react";
import type { Line } from "../types";
import { productRef } from "../rel";

/**
 * Commercial decision support for a quote line, sourced from the Decision
 * Platform's on-demand QUOTE_CONTEXT service. It shows the deterministic FACTS
 * (this customer's history, last price, trend, cadence, and — for managers —
 * cost/margin) kept strictly separate from the AI RECOMMENDATION, and captures
 * the salesperson's accept / modify / reject back into the Decision Store.
 *
 * It never changes the price or the product. The salesperson decides.
 */

interface QFact {
  label: string;
  value: string | number | boolean;
  unit: string | null;
  data_class: "OPERATIONAL" | "RESTRICTED";
  source: string;
}
interface QuoteSupport {
  resolution: {
    customer_resolved: boolean;
    customer_label: string | null;
    products_resolved: string[];
    products_unresolved: string[];
  };
  customer_facts: QFact[];
  items: { product_id: string; label: string; facts: QFact[] }[];
  unknowns: { field: string; reason: string }[];
  interpretation: {
    status: string;
    title: string | null;
    recommendation: string | null;
    explanation: string | null;
    caveat: string | null;
    should_surface: boolean;
  };
  confidence: { evidence_sufficiency?: string; reasons?: string[] };
  restricted_absent: boolean;
  decision_id: string | null;
}

function fmt(value: QFact["value"], unit: string | null): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") {
    if (value === "up") return "↑ up";
    if (value === "down") return "↓ down";
    if (value === "flat") return "→ flat";
    return value;
  }
  if (unit === "currency") return "₹" + Math.round(value).toLocaleString("en-IN");
  if (unit === "ratio") return (value * 100).toFixed(1) + "%";
  if (unit === "days" || unit === "count") return String(Math.round(value));
  return String(value);
}

const AI_STATE: Record<string, { mark: string; tone: "ok" | "degraded" | "failed" | "withheld" }> = {
  OK: { mark: "AI recommendation", tone: "ok" },
  DEGRADED: { mark: "AI recommendation · degraded", tone: "degraded" },
  FAILED: { mark: "Interpretation unavailable", tone: "failed" },
  SUPPRESSED: { mark: "Recommendation withheld", tone: "withheld" },
  PENDING: { mark: "Interpretation pending", tone: "withheld" },
};

/** Why there is nothing to accept, per AI status.
 *
 *  Accepting is offered only on a reading that passed the grounding gate. That
 *  is the right rule — a degraded card carrying an "Accept recommendation"
 *  button would make the status taxonomy decorative, saying the model output
 *  was not used while inviting somebody to act on it — but it used to be
 *  enforced by simply not rendering the button, and an absent control reads as
 *  a missing feature rather than a decision.
 *
 *  It matters for measurement too. A card where accepting was impossible is not
 *  a card somebody declined to accept; counting the two together would report a
 *  property of this component as a property of the person. */
const NO_ACCEPT: Record<string, string> = {
  OK: "This line has facts but no recommendation to accept.",
  DEGRADED: "There is nothing to accept here: the model's response did not pass the grounding check, so the reading above is the deterministic one. The facts stand; the call is yours.",
  FAILED: "There is nothing to accept here: the model could not be reached. The facts above stand on their own.",
  SUPPRESSED: "There is nothing to accept here: the evidence was too thin to interpret, so no recommendation was made.",
  PENDING: "No interpretation has run for this line yet.",
};

/** @param token the signed-in session's token, passed down from the screen.
 *
 *  This used to read `pie_platform_session` out of `localStorage` through a
 *  private copy of a `platformToken()` helper — the third place in the bundle
 *  doing that, and the reason this panel could be looking at one identity while
 *  the grid behind it used another. */
export function DecisionSupport({ customer, line, token }: {
  customer: string; line: Line; token: string;
}) {
  const [data, setData] = useState<QuoteSupport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null); // captured action label
  const [modifying, setModifying] = useState<null | "modify" | "reject">(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDecision(null);
    fetch("/api/v1/quote-support", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        customer,
        products: [productRef(line)],
        proposed_price: line.quoted,
      }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        return r.json();
      })
      .then((d: QuoteSupport) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // re-fetch when the line's product or price changes
  }, [token, customer, line.id, line.supplyCode, line.quoted]);

  // No "sign in to the Decisions platform" state. It existed because the Quote
  // Builder had a login of its own, so somebody could be on this screen holding
  // no platform session at all; reaching it means the support call can be made.

  // OVERRIDE is "I am doing something else", not "I am overruling authority" —
  // that is ESCALATE, and this drawer does not offer it. `modify` used to post
  // ACT, the same action as accepting, which made agreement and disagreement
  // the same row afterwards on the one screen a salesperson actually works.
  async function act(action: "ACT" | "OVERRIDE" | "DISMISS", label: string, reason?: string) {
    if (!data?.decision_id) return;
    try {
      const r = await fetch(`/api/v1/decisions/${data.decision_id}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ action, note: reason, reason }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      setDecision(label);
      setModifying(null);
      setNote("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const ai = data ? AI_STATE[data.interpretation.status] || AI_STATE.PENDING : AI_STATE.PENDING;
  const canAccept = !!data && data.interpretation.status === "OK"
    && !!data.interpretation.recommendation;
  const conf = data?.confidence?.evidence_sufficiency;
  const confLabel = conf === "SUFFICIENT" ? "High" : conf === "PARTIAL" ? "Medium" : conf === "INSUFFICIENT" ? "Low" : null;

  return (
    <div className="qs">
      <div className="qs-head">
        Commercial decision support
        {confLabel && <span className={`qs-conf ${confLabel}`}>{confLabel} confidence</span>}
      </div>

      {loading && <div className="qs-skel">Reading this customer's history…</div>}
      {error && <div className="qs-error">Could not load context: {error}</div>}

      {data && !loading && (
        <>
          {/* resolution / missing-data notice */}
          {(!data.resolution.customer_resolved || data.resolution.products_unresolved.length > 0) && (
            <div className="qs-note">
              {!data.resolution.customer_resolved && (
                <div>New or unmatched customer — no prior commercial history to weigh.</div>
              )}
              {data.resolution.products_unresolved.map((p) => (
                <div key={p}>No sales history found for “{p}”.</div>
              ))}
            </div>
          )}

          {/* FACTS — read straight from source systems */}
          {(data.customer_facts.length > 0 || data.items.length > 0) && (
            <div className="qs-facts">
              <div className="qs-facts-mark">Facts · from your systems</div>
              {data.customer_facts.length > 0 && (
                <dl className="qs-fact-grid">
                  {data.customer_facts.map((f) => (
                    <div className="qs-fact" key={f.label}>
                      <dt>{f.label}</dt>
                      <dd>{fmt(f.value, f.unit)}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {data.items.map((it) => (
                <div key={it.product_id} className="qs-item">
                  {data.items.length > 1 && <div className="qs-item-label">{it.label}</div>}
                  <dl className="qs-fact-grid">
                    {it.facts.map((f) => (
                      <div className="qs-fact" key={f.label}>
                        <dt>
                          {f.label}
                          {f.data_class === "RESTRICTED" && <span className="qs-restricted">restricted</span>}
                        </dt>
                        <dd>{fmt(f.value, f.unit)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
              {data.restricted_absent && (
                <div className="qs-redacted">Cost and margin are not shown for your role.</div>
              )}
            </div>
          )}

          {data.unknowns.length > 0 && (
            <ul className="qs-unknowns">
              {data.unknowns.map((u, i) => (
                <li key={i}>{u.reason}</li>
              ))}
            </ul>
          )}

          {/* AI RECOMMENDATION — clearly separated from the facts */}
          <div className={`qs-ai ${ai.tone}`}>
            <div className="qs-ai-mark">{ai.mark}</div>
            {ai.tone === "failed" ? (
              <p>
                The reasoning service did not respond. The facts above are read straight from your
                systems and are all present — only the reading of them is missing. You can still
                decide and record it.
              </p>
            ) : ai.tone === "withheld" ? (
              <p>
                {data.interpretation.explanation ||
                  "The evidence does not support a confident recommendation. The facts are shown; a judgement is withheld rather than manufactured."}
              </p>
            ) : (
              <>
                {data.interpretation.explanation && <p>{data.interpretation.explanation}</p>}
                {data.interpretation.recommendation && (
                  <p className="qs-ai-rec">{data.interpretation.recommendation}</p>
                )}
                {data.interpretation.caveat && <p className="qs-ai-caveat">{data.interpretation.caveat}</p>}
                {ai.tone === "degraded" && (
                  <p className="qs-ai-caveat">
                    Shown from the deterministic facts — the model response was not used.
                  </p>
                )}
              </>
            )}
          </div>

          {/* Decision capture — the salesperson decides; price/product unchanged */}
          {data.decision_id &&
            (decision ? (
              <div className="qs-captured">Recorded: {decision}. You still set the final price.</div>
            ) : modifying ? (
              <div className="qs-capture-form">
                <textarea
                  className="input"
                  rows={2}
                  placeholder={
                    modifying === "reject"
                      ? "Why are you setting this aside? (optional)"
                      : "What are you doing differently? (optional)"
                  }
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  aria-label="Reason"
                />
                <div className="qs-actions">
                  <Button
                    variant="contained" size="small"
                    onClick={() =>
                      act(modifying === "reject" ? "DISMISS" : "OVERRIDE",
                          modifying === "reject" ? "set aside" : "acting differently", note)
                    }
                  >
                    Save decision
                  </Button>
                  <Button variant="text" size="small" onClick={() => setModifying(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                {!canAccept && (
                  <p className="qs-ai-caveat">
                    {NO_ACCEPT[data.interpretation.status] || NO_ACCEPT.PENDING}
                  </p>
                )}
                <div className="qs-actions">
                  {canAccept && (
                    <Button variant="contained" size="small" onClick={() => act("ACT", "accepted")}>
                      Accept recommendation
                    </Button>
                  )}
                  <Button variant="outlined" size="small" onClick={() => setModifying("modify")}>
                    Modify
                  </Button>
                  <Button variant="text" size="small" onClick={() => setModifying("reject")}>
                    Set aside
                  </Button>
                </div>
              </>
            ))}
        </>
      )}
    </div>
  );
}
