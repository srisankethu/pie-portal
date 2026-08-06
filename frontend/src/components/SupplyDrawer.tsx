import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import type { Line, LineIntelligence } from "../types";
import { REL_STYLE } from "../rel";
import { DecisionSupport } from "./DecisionSupport";
import { QuoteIntelligence } from "./QuoteIntelligence";
import { money } from "../money";

export function SupplyDrawer({
  line,
  customer,
  mgmt,
  intel,
  intelLoading,
  intelError,
  intelConnected,
  onRecordOverride,
  onRequestApproval,
  approvalStatus,
  onOpenPlatform,
  onClose,
  onSelect,
  onRevert }: {
  line: Line;
  customer: string;
  mgmt: boolean;
  intel: LineIntelligence | null;
  intelLoading: boolean;
  intelError: string | null;
  intelConnected: boolean;
  onRecordOverride: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  onRequestApproval: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  approvalStatus: { status: string; required_authority: string; decision_note: string | null } | null;
  /** Jump to a platform screen — the Customer × Item analysis drill-down. */
  onOpenPlatform?: (hash: string) => void;
  onClose: () => void;
  onSelect: (code: string, manual: boolean) => void;
  onRevert: () => void;
}) {
  const exactSelected = line.supplyCode === line.reqCode;
  const pricingDelta =
    line.quoted !== null && line.economics?.recommended !== null && line.economics?.recommended !== undefined
      ? line.quoted - line.economics.recommended
      : null;
  return (
    /* A real Drawer: focus stays inside it, Escape closes it, and focus returns
       to the grid row that opened it. The hand-rolled overlay did none of those
       — a keyboard user could tab out into the quote grid it was covering and
       edit a price they could not see. */
    <Drawer
      anchor="right"
      open
      onClose={onClose}
      slotProps={{ paper: { sx: { width: "min(560px, 92vw)" } } }}
    >
      <Box sx={{ height: "100%", overflow: "auto" }}>
        <div className="drawer-head">
          <div style={{ flex: 1 }}>
            <h6 className="text-muted" style={{ margin: 0 }}>
              Requested item
            </h6>
            <div className="mono" style={{ fontWeight: 700 }}>
              {line.reqCode}
            </div>
            <div className="req-desc">{line.reqDesc}</div>
          </div>
          <div className="text-muted" style={{ fontSize: 12 }}>
            Qty {line.reqQty}
          </div>
        </div>
        <div className="drawer-body">
          {line.substituted && (
            <div className="drawer-alert">
              Requested product remains visible. This line currently quotes an alternate supply product.
            </div>
          )}
          {line.substituted && (
            <button
              className="btn btn-ghost btn-sm"
              title="Revert this line to the originally requested product"
              onClick={onRevert}
              style={{ marginBottom: 12 }}
            >
              ↩ Revert to exact / requested
            </button>
          )}
          {mgmt && line.economics && (
            <div className="drawer-pricing-card">
              <div className="drawer-pricing-header">Pricing context</div>
              <div className="drawer-pricing-grid">
                <div>
                  <div className="drawer-pricing-label">Current quote</div>
                  <div className="drawer-pricing-value">{money(line.quoted)}</div>
                </div>
                <div>
                  <div className="drawer-pricing-label">Recommended</div>
                  <div className="drawer-pricing-value">{money(line.economics.recommended)}</div>
                </div>
                <div>
                  <div className="drawer-pricing-label">Cost</div>
                  <div className="drawer-pricing-value">{money(line.economics.cost)}</div>
                </div>
              </div>
              {pricingDelta !== null && (
                <div className="drawer-pricing-footnote">
                  Current quoted rate is {pricingDelta > 0 ? "above" : "below"} recommendation by {money(Math.abs(pricingDelta))}.
                </div>
              )}
            </div>
          )}
          {!mgmt && line.quoted !== null && (
            <div className="drawer-pricing-card">
              <div className="drawer-pricing-header">Current line rate</div>
              <div className="drawer-pricing-value">{money(line.quoted)}</div>
              <div className="drawer-pricing-footnote">Adjust the rate inline in the grid when you need to update this line.</div>
            </div>
          )}
          <QuoteIntelligence
            line={line}
            intel={intel}
            loading={intelLoading}
            error={intelError}
            connected={intelConnected}
            onOverride={onRecordOverride}
            onRequestApproval={onRequestApproval}
            approvalStatus={approvalStatus}
            onDrilldown={(customerId, productId) =>
              onOpenPlatform?.(`#/account/${customerId}/item/${productId}`)
            }
          />
          <DecisionSupport customer={customer} line={line} />

          {line.candidates.length === 0 && (
            <div className="empty">
              No supply candidates. The PIE engine could not resolve this line to a product.
              {line.notes.length > 0 && (
                <ul style={{ textAlign: "left", marginTop: 12 }}>
                  {line.notes.map((n, i) => (
                    <li key={i} style={{ fontSize: 12 }}>
                      {n}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {line.candidates.map((c) => {
            const selected = c.code === line.supplyCode;
            return (
              <div key={c.code} className={"cand" + (selected ? " selected" : "")}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="code">{c.code}</span>
                  <span className="chip" style={REL_STYLE[c.rel] || REL_STYLE.NONE}>
                    {c.rel}
                  </span>
                  {c.score !== null && (
                    <span className="text-muted" style={{ fontSize: 11 }}>
                      match {(c.score * 100).toFixed(0)}%
                    </span>
                  )}
                  <span style={{ flex: 1 }} />
                  {!selected && (
                    <button
                      className="btn btn-primary btn-sm"
                      title={`Select ${c.code} as the supply product`}
                      onClick={() => onSelect(c.code, false)}
                    >
                      Select
                    </button>
                  )}
                  {selected && (
                    <span className="text-muted" style={{ fontSize: 12 }}>
                      selected
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12.5, marginTop: 4 }}>{c.desc}</div>
                {c.grade && (
                  <div className="text-muted" style={{ fontSize: 11.5 }}>
                    grade {c.grade}
                    {c.brand ? ` · ${c.brand}` : ""}
                  </div>
                )}
                {c.reason && <div className="reason">{c.reason}</div>}
              </div>
            );
          })}
          {mgmt && exactSelected && line.economics && (
            <div className="text-muted" style={{ fontSize: 11.5, marginTop: 12 }}>
              Economics — cost {money(line.economics.cost)} · recommended {money(line.economics.recommended)}
              {line.economics.margin !== null
                ? ` · margin ${(line.economics.margin * 100).toFixed(1)}%`
                : ""}
            </div>
          )}
        </div>
      </Box>
    </Drawer>
  );
}
