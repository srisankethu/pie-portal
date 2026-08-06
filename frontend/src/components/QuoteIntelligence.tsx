import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import { useState } from "react";
import type { Line, LineIntelligence, QuoteException } from "../types";
import { Labelled, Tip } from "../Tip";
import { money } from "../money";

/**
 * Deterministic commercial intelligence for one quote line.
 *
 * Everything on this panel is computed by the platform's commercial engine —
 * the same engine behind the Customer × Product analysis — from invoice and
 * bill lines. No part of it is generated, estimated or interpreted by a model,
 * which is why a price reference here can be quoted back to a customer.
 *
 * The panel does not change the price. It states what the price is being
 * compared against, which rules that comparison trips, and captures a reason
 * when a salesperson goes ahead anyway. The reason field is the most valuable
 * thing here: it is how a threshold that is wrong for the business gets found.
 */

const OVERRIDE_REASONS: [string, string][] = [
  ["VOLUME_COMMITMENT", "Volume commitment"],
  ["STRATEGIC_ACCOUNT", "Strategic account"],
  ["COMPETITIVE_PRESSURE", "Competitive pressure"],
  ["CLEARING_STOCK", "Clearing slow stock"],
  ["CONTRACTED_PRICE", "Contracted / agreed price"],
  ["OTHER", "Other"],
];

function severityClass(s: string | null): string {
  return s ? `qi-${s.toLowerCase()}` : "";
}

function ExceptionCard({ e }: { e: QuoteException }) {
  return (
    <div className={`qi-exc ${severityClass(e.severity)}`}>
      <div className="qi-exc-head">
        <span className="qi-exc-title">{e.title}</span>
        {e.impact_amount !== null && e.impact_amount > 0 && (
          <span className="qi-exc-impact">{money(e.impact_amount)}</span>
        )}
      </div>
      <p className="qi-exc-detail">{e.detail}</p>
      {e.manager_detail && <p className="qi-exc-mgmt">{e.manager_detail}</p>}
    </div>
  );
}

export function QuoteIntelligence({
  line,
  intel,
  loading,
  error,
  connected,
  onOverride,
  onRequestApproval,
  approvalStatus,
  onDrilldown }: {
  line: Line;
  intel: LineIntelligence | null;
  loading: boolean;
  error: string | null;
  connected: boolean;
  onOverride: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  onRequestApproval: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  /** The live approval on this line, if one has been raised. */
  approvalStatus: { status: string; required_authority: string; decision_note: string | null } | null;
  onDrilldown: (customerId: string, productId: string) => void;
}) {
  const [reasonCode, setReasonCode] = useState(OVERRIDE_REASONS[0][0]);
  const [reason, setReason] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  if (!connected) {
    return (
      <div className="qi qi-connect">
        <div className="qi-head">Commercial intelligence</div>
        <p>
          Sign in to the Decisions platform to price this line against what this customer has
          actually paid, at this quantity, and against the rest of the book.
        </p>
      </div>
    );
  }

  if (loading && !intel) {
    return (
      <div className="qi">
        <div className="qi-head">Commercial intelligence</div>
        <div className="qi-skel">Reading this customer's history…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="qi">
        <div className="qi-head">Commercial intelligence</div>
        <div className="qi-error">Could not load commercial context: {error}</div>
      </div>
    );
  }

  if (!intel) return null;

  const dq = intel.data_quality;
  const confidence =
    dq.data_sufficiency === "SUFFICIENT" ? "High" : dq.data_sufficiency === "PARTIAL" ? "Medium" : "Low";

  const needsApproval = intel?.requires_approval ?? false;

  async function save() {
    setSaveError(null);
    try {
      // Below the floor, recording a reason is not enough — the reason goes
      // with a request that somebody has to answer before this can be sent.
      if (needsApproval) {
        await onRequestApproval(intel!.line_id, reasonCode, reason.trim());
      } else {
        await onOverride(intel!.line_id, reasonCode, reason.trim());
      }
      setSaved(true);
      setCapturing(false);
      setReason("");
    } catch (e) {
      setSaveError((e as Error).message);
    }
  }

  return (
    <div className="qi">
      <div className="qi-head">
        <Labelled tip="Computed by the same engine behind the account analysis, from this customer's own invoice and bill lines. Nothing on this panel is generated or estimated by a model, which is why a figure here can be quoted back to a customer.">
          Commercial intelligence
        </Labelled>
        <span className={`qi-conf ${confidence}`}>
          {confidence} confidence
          <Tip text="How much trading history stands behind these figures. Low confidence does not mean the numbers are wrong — it means there are few of them, so the comparisons are thin." />
        </span>
      </div>

      <div className="qi-sub">
        {intel.resolved ? (
          <>
            <Labelled tip="Prices are compared within a bracket. The same item at 5 pieces and 500 is a different commercial question, and comparing a bulk line against an all-quantities average makes every bulk line look under-priced.">
              Quantity band
            </Labelled>{" "}
            <strong>{intel.quantity_band.label}</strong> · {dq.transaction_count} past{" "}
            {dq.transaction_count === 1 ? "order" : "orders"} · as of {intel.as_of}
          </>
        ) : (
          <>No sales history matches “{intel.product_ref || line.reqCode}”.</>
        )}
      </div>

      {approvalStatus && (
        <div className={`qi-approval qi-ap-${approvalStatus.status.toLowerCase()}`}>
          {approvalStatus.status === "PENDING" && (
            <>Waiting on {approvalStatus.required_authority === "OWNER" ? "an owner" : "a manager"} to approve this price.</>
          )}
          {approvalStatus.status === "APPROVED" && <>Approved at this price.</>}
          {approvalStatus.status === "REJECTED" && <>Rejected. This price cannot be sent.</>}
          {approvalStatus.status === "CHANGES_REQUESTED" && <>A different price was asked for.</>}
          {approvalStatus.decision_note && (
            <div className="qi-approval-note">“{approvalStatus.decision_note}”</div>
          )}
        </div>
      )}

      {/* Exceptions first: this is the part that changes what someone does. */}
      {intel.exceptions.length > 0 && (
        <div className="qi-section">
          <div className="qi-section-h">
            What to check
            {intel.requires_approval && (
              <span className="qi-approval">
                Approval needed
                <Tip text="This line crosses a policy boundary, so the quote cannot be sent until someone with the authority answers. An approval covers the price it was granted at — re-pricing lower means asking again." />
              </span>
            )}
          </div>
          {intel.exceptions.map((e) => (
            <ExceptionCard key={e.code} e={e} />
          ))}
        </div>
      )}

      {/* References: prices, with the evidence each one rests on. */}
      {intel.references.length > 0 && (
        <div className="qi-section">
          <div className="qi-section-h">
            <Labelled tip="Each row is a price this line is measured against, with the evidence it rests on. The right column is how far the quoted price sits from it — negative means the quote is below that reference.">
              Compared against
            </Labelled>
          </div>
          <table className="qi-refs">
            <tbody>
              {intel.references.map((r) => {
                const delta = line.quoted !== null ? line.quoted - r.value : null;
                return (
                  <tr key={r.code}>
                    <th scope="row">
                      {r.label}
                      <span className="qi-ref-basis">{r.basis}</span>
                    </th>
                    <td className="num">{money(r.value)}</td>
                    <td className={"num qi-delta " + (delta === null ? "" : delta < 0 ? "under" : "over")}>
                      {delta === null ? "—" : `${delta < 0 ? "−" : "+"}${money(Math.abs(delta))}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Rendered outside the table: when a salesperson has no purchase history
          for this pair, every remaining reference is cost-derived and withheld,
          so a note nested inside the references section would disappear exactly
          when it is most worth saying. */}
      {intel.references_withheld.length > 0 && (
        <div className="qi-withheld">
          {intel.references_withheld.length} cost-based reference
          {intel.references_withheld.length === 1 ? "" : "s"} not shown for your role.
          <Tip text="Not hidden in the browser — the server never sends them. Any reference derived from purchase cost would let cost be worked out from it, so they are absent for a salesperson rather than masked." />
        </div>
      )}

      {/* Economics — management only; the server omits it entirely otherwise. */}
      {intel.economics && (
        <div className="qi-section">
          <div className="qi-section-h">This line at the quoted price</div>
          <dl className="qi-econ">
            <div>
              <dt>
                <Labelled tip="Purchase cost per unit from the bills, after landed costs and supplier discounts. “Not recorded” means no bill covers this item — the margin is then absent rather than assumed to be full.">
                  Effective cost
                </Labelled>
              </dt>
              <dd>{intel.economics.unit_cost === null ? "not recorded" : money(intel.economics.unit_cost)}</dd>
            </div>
            <div>
              <dt>Line revenue</dt>
              <dd>{money(intel.economics.line_revenue)}</dd>
            </div>
            <div>
              <dt>Gross profit</dt>
              <dd>{intel.economics.gross_profit === null ? "—" : money(intel.economics.gross_profit)}</dd>
            </div>
            <div>
              <dt>
                <Labelled tip="Gross profit ÷ line revenue. Below the review floor it is flagged; below the approval floor the line cannot be sent without a signature. Both floors are in Settings → Margin policy.">
                  Margin
                </Labelled>
              </dt>
              <dd className={intel.blocking ? "warn" : ""}>
                {intel.economics.margin === null ? "—" : `${(intel.economics.margin * 100).toFixed(1)}%`}
              </dd>
            </div>
          </dl>
        </div>
      )}

      {dq.reasons.length > 0 && (
        <ul className="qi-reasons">
          {dq.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}

      {/* Override capture — the price is already the salesperson's to set; this
          records why, against the exact rules that fired. */}
      {intel.exceptions.some((e) => e.severity !== "INFO") &&
        (saved ? (
          <div className="qi-captured">
            {needsApproval
              ? "Sent for approval. The quote cannot go out until someone answers."
              : "Recorded. The price is yours to set — this notes why."}
          </div>
        ) : capturing ? (
          <div className="qi-capture">
            <TextField
              id={`qi-reason-${intel.line_id}`}
              select
              fullWidth
              size="small"
              label={needsApproval ? "Why should this be approved?" : "Why is this price right?"}
              value={reasonCode}
              onChange={(e) => setReasonCode(e.target.value)}
              sx={{ mb: 1 }}
            >
              {OVERRIDE_REASONS.map(([code, label]) => (
                <MenuItem key={code} value={code}>{label}</MenuItem>
              ))}
            </TextField>
            <textarea
              className="input"
              rows={2}
              placeholder="Anything a reviewer would need to know (optional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-label="Reason detail"
            />
            {saveError && <div className="qi-error">{saveError}</div>}
            <div className="qi-actions">
              <Button variant="contained" size="small" onClick={save}>
                {needsApproval ? "Send for approval" : "Record this decision"}
              </Button>
              <Button variant="text" size="small" onClick={() => setCapturing(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="qi-actions">
            <Button variant="outlined" size="small" onClick={() => setCapturing(true)}>
              {needsApproval ? "Request approval" : "Record why this price is right"}
            </Button>
            {intel.drilldown && (
              <Button
                variant="text" size="small"
                onClick={() => onDrilldown(intel.drilldown!.customer_id, intel.drilldown!.product_id)}
              >
                Full analysis →
              </Button>
            )}
          </div>
        ))}

      {intel.exceptions.every((e) => e.severity === "INFO") && intel.drilldown && (
        <div className="qi-actions">
          <Button
            variant="text" size="small"
            onClick={() => onDrilldown(intel.drilldown!.customer_id, intel.drilldown!.product_id)}
          >
            Full analysis →
          </Button>
        </div>
      )}

      <div className="qi-prov">
        Computed from your invoices and bills · rules {intel.thresholds_version} · engine{" "}
        {intel.engine_version}
      </div>
    </div>
  );
}
