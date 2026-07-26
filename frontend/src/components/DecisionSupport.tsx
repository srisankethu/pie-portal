import { useEffect, useState } from "react";
import type { Line } from "../types";

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

function platformToken(): string | null {
  try {
    const raw = localStorage.getItem("pie_platform_session");
    return raw ? JSON.parse(raw).token || null : null;
  } catch {
    return null;
  }
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

function productRef(line: Line): string {
  return line.supplyDesc || line.reqDesc || line.supplyCode || line.reqCode;
}

const AI_STATE: Record<string, { mark: string; tone: "ok" | "degraded" | "failed" | "withheld" }> = {
  OK: { mark: "AI recommendation", tone: "ok" },
  DEGRADED: { mark: "AI recommendation · degraded", tone: "degraded" },
  FAILED: { mark: "Interpretation unavailable", tone: "failed" },
  SUPPRESSED: { mark: "Recommendation withheld", tone: "withheld" },
  PENDING: { mark: "Interpretation pending", tone: "withheld" },
};

export function DecisionSupport({ customer, line }: { customer: string; line: Line }) {
  const token = platformToken();
  const [data, setData] = useState<QuoteSupport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null); // captured action label
  const [modifying, setModifying] = useState<null | "modify" | "reject">(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!token) return;
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

  if (!token) {
    return (
      <div className="qs-connect">
        <div className="qs-head">Commercial decision support</div>
        <p>
          Sign in to the Decisions platform to see this customer's history, last price, and an AI
          recommendation alongside this line.
        </p>
      </div>
    );
  }

  async function act(action: "ACT" | "DISMISS", label: string, reason?: string) {
    if (!data?.decision_id || !token) return;
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
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() =>
                      act(modifying === "reject" ? "DISMISS" : "ACT",
                          modifying === "reject" ? "set aside" : "acting differently", note)
                    }
                  >
                    Save decision
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setModifying(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="qs-actions">
                {data.interpretation.status === "OK" && data.interpretation.recommendation && (
                  <button className="btn btn-primary btn-sm" onClick={() => act("ACT", "accepted")}>
                    Accept recommendation
                  </button>
                )}
                <button className="btn btn-secondary btn-sm" onClick={() => setModifying("modify")}>
                  Modify
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setModifying("reject")}>
                  Set aside
                </button>
              </div>
            ))}
        </>
      )}
    </div>
  );
}
