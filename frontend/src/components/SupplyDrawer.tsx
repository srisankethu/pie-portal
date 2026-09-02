import Button from "@mui/material/Button";
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import type { Line, LineIntelligence } from "../types";
import { relTone } from "../rel";
import { StatusChip } from "../platform/kit";
import { DecisionSupport } from "./DecisionSupport";
import { QuoteIntelligence } from "./QuoteIntelligence";
import { money } from "../money";
import { pathFor } from "../platform/route";

export function SupplyDrawer({
  line,
  customer,
  token,
  mgmt,
  intel,
  intelLoading,
  intelError,
  onRecordOverride,
  onRequestApproval,
  approvalStatus,
  onOpenPlatform,
  onClose,
  onSelect,
  onRevert,
  readOnly = false }: {
  line: Line;
  customer: string;
  /** The signed-in session's token, for the panels that call the platform. */
  token: string;
  mgmt: boolean;
  intel: LineIntelligence | null;
  intelLoading: boolean;
  intelError: string | null;
  onRecordOverride: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  onRequestApproval: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  approvalStatus: { status: string; required_authority: string; decision_note: string | null } | null;
  /** Jump to a platform screen — the Customer × Item analysis drill-down. */
  onOpenPlatform?: (path: string) => void;
  onClose: () => void;
  onSelect: (code: string, manual: boolean) => void;
  onRevert: () => void;
  /** The reader may not change this quote: the options are shown, the
   *  select and revert controls are not. */
  readOnly?: boolean;
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
          {line.substituted && !readOnly && (
            <Button
              variant="text" size="small"
              title="Revert this line to the originally requested product"
              onClick={onRevert}
              style={{ marginBottom: 12 }}
            >
              ↩ Revert to exact / requested
            </Button>
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
            onOverride={onRecordOverride}
            onRequestApproval={onRequestApproval}
            approvalStatus={approvalStatus}
            // `pathFor` rather than a template literal: the platform owns where
            // its screens live, and a second spelling of that path here is the
            // one that would still say `/account/…` after the platform moved.
            onDrilldown={(customerId, productId) =>
              onOpenPlatform?.(pathFor("customerItem", customerId, productId))
            }
          />
          <DecisionSupport customer={customer} line={line} token={token} />

          {/* What the engine wants read before the list below it.
            *
            * These used to render only when there were *no* candidates, which
            * is the one case they are never about. The engine's own vacuity
            * note — "no dimension was comparable, so a perfect dimensional
            * score is vacuous" — describes candidates that are on screen, and
            * it was invisible in exactly that case. A caveat shown only when
            * there is nothing to caveat is not a caveat. */}
          {line.notes.length > 0 && (
            <Paper variant="outlined" sx={{ p: 1.5, mb: 1.5 }}>
              <Typography
                variant="overline"
                color="text.secondary"
                component="div"
                sx={{ lineHeight: 1.6 }}
              >
                Before you choose
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {line.notes.map((n, i) => (
                  <Typography key={i} component="li" variant="body2" color="text.secondary">
                    {n}
                  </Typography>
                ))}
              </Box>
            </Paper>
          )}
          {line.candidates.length === 0 && (
            <div className="empty">
              No supply candidates. The PIE engine could not resolve this line to a product.
            </div>
          )}
          {line.candidates.map((c) => {
            const selected = c.code === line.supplyCode;
            return (
              <div key={c.code} className={"cand" + (selected ? " selected" : "")}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="code">{c.code}</span>
                  {/* The same chip the grid behind this drawer draws, from the
                      same tone table. Two spellings of one term is how a line
                      reads AMBIGUOUS in amber on the grid and in grey here. */}
                  <StatusChip label={c.rel} tone={relTone(c.rel)} dense />
                  {/* A retrieved record sits beside ranked ones and must not
                      read as one: "nearest description" is a statement about
                      text, and the chip says so where the score would be. */}
                  {c.retrieved && c.alias && (
                    <StatusChip
                      label={`near confirmed code ${c.alias}`} tone="info" dense
                      tip="This customer confirmed that code means this product. This line is close to that code but is not it, so the record is offered, not selected."
                    />
                  )}
                  {c.retrieved && !c.alias && (
                    <StatusChip
                      label="nearest by description" tone="neutral" dense
                      tip="Found because its catalogue description reads like this line, then compared by the engine. Not ranked, not a match — an option to consider."
                    />
                  )}
                  {c.score !== null && (
                    <span className="text-muted" style={{ fontSize: 11 }}>
                      match {(c.score * 100).toFixed(0)}%
                    </span>
                  )}
                  <span style={{ flex: 1 }} />
                  {!selected && !readOnly && (
                    <Button
                      variant="contained" size="small"
                      title={`Select ${c.code} as the supply product`}
                      onClick={() => onSelect(c.code, false)}
                    >
                      Select
                    </Button>
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
