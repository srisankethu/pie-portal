import Button from "@mui/material/Button";
import type { Line, LineIntelligence } from "../types";
import { REL_STYLE, statusColor } from "../rel";
import { Labelled, Tip } from "../Tip";
import { money } from "../money";

/** The worst exception on a line, as a chip. Ordered by severity, so the chip
 *  always shows the thing that most needs a decision rather than the first
 *  rule that happened to fire. */
function CommercialChip({ intel }: { intel: LineIntelligence | undefined }) {
  if (!intel) return <span className="text-muted">—</span>;
  const worst = intel.exceptions[0];
  if (!worst) return <span className="qi-chip ok">clear</span>;
  if (worst.severity === "INFO" && intel.exceptions.length === 1) {
    return (
      <span className="qi-chip info">
        <Labelled tip={worst.detail}>{worst.title}</Labelled>
      </span>
    );
  }
  const others = intel.exceptions.length - 1;
  return (
    <span className={`qi-chip ${worst.severity.toLowerCase()}`}>
      <Labelled
        tip={
          <>
            {intel.requires_approval && (
              <div style={{ marginBottom: 4 }}>
                <b>This line cannot be sent without an approval.</b>
              </div>
            )}
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {intel.exceptions.map((e) => (
                <li key={e.code}>{e.title}</li>
              ))}
            </ul>
            <div style={{ marginTop: 4 }}>Open the line for the detail behind each one.</div>
          </>
        }
      >
        {intel.requires_approval ? "approval" : worst.severity === "WARNING" ? "check price" : worst.title}
        {others > 0 && <span className="qi-chip-more">+{others}</span>}
      </Labelled>
    </span>
  );
}

export function LineGrid({
  lines,
  mgmt,
  intel,
  selected,
  focusId,
  onToggle,
  onOpen,
  onSetPrice,
  onDeleteLine,
  onCreateItem }: {
  lines: Line[];
  mgmt: boolean;
  intel: Record<string, LineIntelligence>;
  selected: Record<string, boolean>;
  focusId: string | null;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
  onDeleteLine: (id: string) => void;
  onCreateItem: (id: string) => void;
}) {
  if (lines.length === 0) {
    return <div className="empty">No lines match this view. Clear the filter, or paste an RFQ.</div>;
  }
  return (
    <table className="grid">
      <thead>
        <tr>
          <th style={{ width: 22 }}></th>
          <th style={{ width: 24 }}>#</th>
          <th>Requested item</th>
          <th className="num">Qty</th>
          <th>Supply product</th>
          <th>
            <Labelled tip="How the supply product relates to what the customer asked for — identical, an equivalent from another maker, or a substitute that differs in some dimension. It is not a judgement about whether to offer it.">
              Relationship
            </Labelled>
          </th>
          <th className="num">Avail.</th>
          <th className="num">Short.</th>
          <th className="num">Quoted ₹</th>
          <th className="num">Line total</th>
          {mgmt && (
            <th className="num">
              <Labelled tip="Gross profit ÷ line revenue at the quoted rate. Shown to managers and owners only — a salesperson's response from the server contains no cost and no margin at all.">
                Margin
              </Labelled>
            </th>
          )}
          <th>
            <Labelled tip="The most serious thing the deterministic checks found on this line. “clear” means every check passed, not that the price is optimal.">
              Commercial
            </Labelled>
          </th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l, i) => {
          const severityClass = l.flags.unresolved
            ? "severity-high"
            : l.substituted || l.flags.procurement
              ? "severity-medium"
              : l.flags.attention || l.flags.manualReview || l.flags.missingBooks
                ? "severity-low"
                : "";
          const rowClass = [
            focusId === l.id ? "focused" : "",
            l.status.kind === "technical" ? "tech" : l.flags.attention ? "attention" : "",
            severityClass,
          ]
            .filter(Boolean)
            .join(" ");
          const hintParts = [
            l.flags.unresolved ? "unresolved" : "",
            l.substituted ? "substitute active" : "",
            l.flags.procurement ? "procurement watch" : "",
            l.flags.missingBooks ? "missing Zoho item" : "",
            l.flags.manualReview ? "manual review" : "",
            l.shortage && l.shortage > 0 ? `shortage ${l.shortage}` : "",
            l.availUnknown ? "availability pending" : "",
          ].filter(Boolean);
          const li = intel[l.id];
          // The authoritative margin is the platform's: it uses the recorded
          // purchase cost as of today, net of bill-line discounts. The legacy
          // per-line figure is only a fallback for when the platform is not
          // connected.
          const authMargin = li?.economics?.margin ?? null;
          const econ = l.economics;
          return (
            <tr key={l.id} className={rowClass} onClick={() => onOpen(l.id)}>
              <td onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={!!selected[l.id]}
                  aria-label={`Select ${l.reqCode}`}
                  title={`Select ${l.reqCode}`}
                  onChange={() => onToggle(l.id)}
                  style={{ accentColor: "var(--color-accent)" }}
                />
              </td>
              <td className="text-muted">{i + 1}</td>
              <td>
                <div className="mono">{l.reqCode}</div>
                <div className="req-desc">{l.reqDesc}</div>
                {hintParts.length > 0 && (
                  <div className="row-hints" aria-label="Line alerts">
                    {hintParts.map((hint) => (
                      <span key={hint} className="row-hint-pill">
                        {hint}
                      </span>
                    ))}
                  </div>
                )}
                {l.substituted && <div className="row-meta-pill">substituted</div>}
              </td>
              <td className="num">{l.reqQty}</td>
              <td>
                {l.supplyCode ? (
                  <>
                    <div className={"mono" + (l.substituted ? " subst" : "")}>{l.supplyCode}</div>
                    <div className="req-desc">{l.supplyDesc || ""}</div>
                    {l.substituted && <div className="row-meta-pill accent">selected alternate</div>}
                    {(l.sel === "USER" || l.sel === "MANUAL") && (
                      <div style={{ fontSize: 10.5 }} className={l.sel === "MANUAL" ? "warn" : ""}>
                        {l.sel === "MANUAL" ? "manually selected" : "user selected"}
                      </div>
                    )}
                    {l.inBooks === false && (
                      <Button
                        variant="text" size="small"
                        title={`Create ${l.reqCode} in Zoho Books`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onCreateItem(l.id);
                        }}
                      >
                        + Create in Zoho
                      </Button>
                    )}
                  </>
                ) : (
                  <span className="text-muted">
                    {l.rel === "PIE_DOWN" ? "awaiting PIE" : l.rel === "AMBIGUOUS" ? "select product" : "not resolved"}
                  </span>
                )}
              </td>
              <td>
                <span className="chip" style={REL_STYLE[l.rel] || REL_STYLE.NONE}>
                  {l.relLabel}
                </span>
              </td>
              <td className="num">
                {!l.supplyCode ? (
                  "—"
                ) : l.availUnknown ? (
                  <span className="text-muted">?</span>
                ) : (
                  <span className={l.avail === 0 ? "warn" : ""}>{l.avail}</span>
                )}
              </td>
              <td className="num">
                {l.shortage !== null && l.shortage > 0 ? <span className="warn">{l.shortage}</span> : "—"}
              </td>
              <td className="num" onClick={(e) => e.stopPropagation()}>
                <input
                  className="input rate-input"
                  defaultValue={l.quoted ?? ""}
                  key={`${l.id}-${l.quoted}`}
                  placeholder="—"
                  title={`Set quoted rate for ${l.reqCode}`}
                  onBlur={(e) => {
                    const v = e.target.value.replace(/[^0-9.]/g, "");
                    onSetPrice(l.id, v === "" ? null : parseFloat(v));
                  }}
                />
              </td>
              <td className="num">{money(l.lineTotal)}</td>
              {mgmt && (
                <td className="num">
                  {authMargin !== null ? (
                    <span className={li?.blocking ? "warn" : ""}>
                      {(authMargin * 100).toFixed(1)}%
                      <Tip text="From this item's recorded purchase cost — bill lines actually synced from the books, not a catalogue figure." />
                    </span>
                  ) : econ && econ.margin !== null ? (
                    <span className={econ.below_floor ? "warn" : ""}>
                      {(econ.margin * 100).toFixed(1)}%
                      <Tip text="Derived from the catalogue cost, because the commercial platform is not connected. Indicative only — the real purchase cost comes from bills and may differ." />
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
              )}
              <td>
                <CommercialChip intel={li} />
              </td>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                  <span
                    className="chip"
                    style={{ color: statusColor(l.status.kind), border: "1px solid currentColor" }}
                  >
                    {l.status.label}
                  </span>
                  {selected[l.id] && (
                    <Button
                      variant="text" size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteLine(l.id);
                      }}
                      aria-label={`Delete ${l.reqCode}`}
                      title={`Remove ${l.reqCode} from this quote`}
                    >
                      <span aria-hidden="true">🗑</span>
                    </Button>
                  )}
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
