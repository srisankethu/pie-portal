import * as React from "react";
import Button from "@mui/material/Button";
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import type { Line, LineIntelligence } from "../types";
import { isFromOwnBook, relTone, sourceLabel } from "../rel";
import { StatusChip } from "../platform/kit";
import { DecisionSupport } from "./DecisionSupport";
import { QuoteIntelligence } from "./QuoteIntelligence";
import { money } from "../money";
import { formatTime } from "../when";
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
  onSetCustomCost,
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
  /** Record the cost price this line is quoted against, or clear it with
   *  `null`. Every role, which is the point — see the panel below. */
  onSetCustomCost: (lineId: string, cost: number | null, note: string) => void;
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
                  {/* Which cost this is. The card used to print the figure
                      alone, so a stand-in adapter's hashed number and a
                      ledger's landed cost were the same three glyphs in the
                      same weight — and on a deployment left in mock mode the
                      whole card was arithmetic on `sha256(code)`. */}
                  <div className="drawer-pricing-footnote">{costBasisLabel(line)}</div>
                </div>
              </div>
              {pricingDelta !== null && (
                <div className="drawer-pricing-footnote">
                  Current quoted rate is {pricingDelta > 0 ? "above" : "below"} recommendation by {money(Math.abs(pricingDelta))}.
                </div>
              )}
            </div>
          )}
          {/* The desk's pricing context.
            *
            * `recommended` is served to a salesperson deliberately — `store.py`
            * marks it "decision support, safe for both roles", and CLAUDE.md §1
            * accepts it as a residual: cost is recoverable from it by algebra,
            * and coarsening it would blunt the one screen this role uses to
            * decide. The frontend has been dropping it on the floor, so the
            * desk has been pricing by guess-and-check against an endpoint that
            * caps a product at four distinct prices per request.
            *
            * Shown beside the current rate rather than instead of it: the
            * comparison is the point. No cost, no margin, no floor — those are
            * absent from this role's response and stay absent. */}
          {!mgmt && (line.quoted !== null || line.recommended !== null) && (
            <div className="drawer-pricing-card">
              <div className="drawer-pricing-header">Pricing</div>
              <div className="drawer-pricing-grid">
                <div>
                  <div className="drawer-pricing-label">Current line rate</div>
                  <div className="drawer-pricing-value">{money(line.quoted)}</div>
                </div>
                {line.recommended !== null && (
                  <div>
                    <div className="drawer-pricing-label">Recommended</div>
                    <div className="drawer-pricing-value">{money(line.recommended)}</div>
                  </div>
                )}
              </div>
              {line.recommended !== null && line.quoted !== null && (
                <div className="drawer-pricing-footnote">
                  {line.quoted === line.recommended
                    ? "This line is at the recommended rate."
                    : `Your rate is ${money(Math.abs(line.quoted - line.recommended))} `
                      + `${line.quoted > line.recommended ? "above" : "below"} the recommendation.`}
                </div>
              )}
              <div className="drawer-pricing-footnote">
                Set the rate on the line; the recommendation is guidance, not a limit.
              </div>
            </div>
          )}
          <CostBasisPanel
            line={line}
            mgmt={mgmt}
            readOnly={readOnly}
            onSetCustomCost={onSetCustomCost}
          />
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
                  {/* Only for a candidate out of this organization's own book.
                      Not a chip per brand: every catalogue candidate carries a
                      manufacturer label, and a chip on all of them would be a
                      row of decoration that stops meaning anything. This one
                      marks the distinction a person acts on — "we already sell
                      this" against "the maker's catalogue lists it". */}
                  {isFromOwnBook(c.brand) && (
                    <StatusChip
                      label="in our book"
                      tone="info"
                      dense
                      tip="From this organization's own item master rather than the manufacturer catalogue — something the business already sells."
                    />
                  )}
                  {/* A retrieved record sits beside ranked ones and must not
                      read as one: "nearest description" is a statement about
                      text, and the chip says so where the score would be. */}
                  {c.retrieved && c.alias && c.alias_kind === "phrase" && (
                    <StatusChip
                      label={`quoted before for “${c.alias}”`} tone="info" dense
                      tip="A person put this product on a quote for this customer when they asked for those words. A past choice, not a match — they may mean the same thing this time, or not."
                    />
                  )}
                  {c.retrieved && c.alias && c.alias_kind !== "phrase" && (
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
                  {c.unverified && (
                    <StatusChip
                      label="unverified fit"
                      tone="warn"
                      dense
                      tip="The engine could not compare every dimension the request named, so the match score is not a measure of fit. Capped at POSSIBLE and never auto-selected."
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
                {/* Grade, source and catalogue are separate facts and are
                    guarded separately. They were one line with the source
                    inside the grade's guard — so a candidate whose grade did
                    not decode showed nothing about where it came from. That
                    became load-bearing the moment this organization's own book
                    joined the candidate pool: a book item and a catalogue item
                    for the same product can both appear in one list, and
                    unmarked they read as two unrelated products. `sourceLabel`
                    is what turns the server's internal "book" into words a
                    salesperson can read. */}
                {(c.grade || c.brand || c.catalogue) && (
                  <div className="text-muted" style={{ fontSize: 11.5 }}>
                    {c.grade ? `grade ${c.grade}` : ""}
                    {c.grade && c.brand ? " · " : ""}
                    {c.brand ? sourceLabel(c.brand) : ""}
                    {/* Which of the company's catalogues — which manufacturer's
                        — this record is from. A company resolves against every
                        catalogue it has built at once, so a part number's
                        provenance is not complete without it. */}
                    {c.catalogue ? ` · catalogue ${c.catalogue}` : ""}
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

/** What the line's cost is, in one phrase — a source, never a value.
 *
 *  `DEMO` is the one that had to exist. The offline stand-in adapter derives a
 *  list price and a landed cost from `sha256(code)`, and a deployment that has
 *  not set `ZOHO_QUOTE_SERVICE=live` renders those in exactly the weight a real
 *  landed cost gets — which is how a pricing card came to read "Cost ₹2,830"
 *  for an item nobody had ever bought.
 */
export function costBasisLabel(line: Line): string {
  switch (line.costBasis) {
    case "CUSTOM":
      return "cost price entered for this line";
    case "BOOKS":
      return "landed cost from the books";
    case "DEMO":
      return "demo stand-in — not a real purchase price";
    default:
      return "no cost on record";
  }
}

/** The second cost basis: what a person sourced for this line.
 *
 *  Open to every role that may edit the quote, and that is the point of it
 *  rather than a hole in §1. The books answer "what have we paid for this
 *  item"; for a first-time part they answer nothing, and the person holding the
 *  supplier's offer is the one at the desk. Refusing them the field does not
 *  keep a cost off the quote — it keeps the *right* one off, and leaves the
 *  margin, the floors and the approval gate resting on nothing.
 *
 *  What is written is the caller's own number. Reading one back is where the
 *  gate is: the server withholds an entry management made, so `customCost`
 *  arrives absent while `customCostSet` still says one exists.
 */
function CostBasisPanel({
  line, mgmt, readOnly, onSetCustomCost,
}: {
  line: Line;
  mgmt: boolean;
  readOnly: boolean;
  onSetCustomCost: (lineId: string, cost: number | null, note: string) => void;
}) {
  const visible = line.customCost ?? null;
  const withheld = line.customCostSet && visible === null;
  const [cost, setCost] = React.useState(visible === null ? "" : String(visible));
  const [note, setNote] = React.useState(line.customCostNote ?? "");
  // Remounts on the server's answer, so the field shows what was stored rather
  // than what was typed at it — including the case where the store refused.
  const key = `${line.id}-${visible ?? ""}`;
  React.useEffect(() => {
    setCost(visible === null ? "" : String(visible));
    setNote(line.customCostNote ?? "");
  }, [key]);                              // eslint-disable-line react-hooks/exhaustive-deps

  const typed = cost.trim();
  const parsed = typed === "" ? null : Number(typed);
  const invalid = parsed !== null && (!Number.isFinite(parsed) || parsed <= 0);
  const unchanged = parsed === visible && (note ?? "") === (line.customCostNote ?? "");

  return (
    <Paper variant="outlined" sx={{ p: 1.5, mb: 1.5 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 1 }}>
        <Typography variant="overline" color="text.secondary" component="div"
                    sx={{ lineHeight: 1.6 }}>
          Cost price
        </Typography>
        <StatusChip
          label={costBasisLabel(line)}
          tone={line.costBasis === "CUSTOM" ? "info"
            : line.costBasis === "BOOKS" ? "good"
              : line.costBasis === "DEMO" ? "warn" : "neutral"}
          dense
        />
      </Stack>
      {withheld ? (
        <Typography variant="body2" color="text.secondary">
          A cost price is set on this line by management. It is what the margin,
          the floors and the approval gate are computed against.
        </Typography>
      ) : (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {line.costBasis === null
              ? "Nothing has been bought against this item, so there is no cost "
                + "on record. Enter what this line costs you and the margin and "
                + "the floors follow from it."
              : "A cost you enter here is used for this line instead of the one "
                + "on record. It stays on this quote — the item master is not "
                + "changed."}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
            <TextField
              label="Custom cost price"
              size="small"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
              error={invalid}
              helperText={invalid ? "Must be greater than zero" : " "}
              placeholder="—"
              disabled={readOnly}
              slotProps={{ htmlInput: {
                inputMode: "decimal",
                "aria-label": `Custom cost price for ${line.reqCode}`,
              } }}
              sx={{ width: 150 }}
            />
            <TextField
              label="Where it came from"
              size="small"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="supplier, offer, valid until"
              helperText=" "
              disabled={readOnly}
              sx={{ flex: 1 }}
            />
          </Stack>
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined" size="small"
              disabled={readOnly || invalid || unchanged}
              onClick={() => onSetCustomCost(line.id, parsed, note)}
            >
              Save cost price
            </Button>
            {visible !== null && (
              <Button
                variant="text" size="small"
                disabled={readOnly}
                onClick={() => onSetCustomCost(line.id, null, "")}
              >
                Clear
              </Button>
            )}
          </Stack>
          {mgmt && line.customCostAt && visible !== null && (
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: "block", mt: 1 }}>
              Recorded {formatTime(line.customCostAt)}
              {line.customCostBy ? " · by a user on this quote" : ""}
            </Typography>
          )}
        </>
      )}
    </Paper>
  );
}
