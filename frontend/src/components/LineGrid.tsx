import type { Line } from "../types";
import { REL_STYLE, statusColor, inr } from "../rel";

export function LineGrid({
  lines,
  mgmt,
  selected,
  focusId,
  onToggle,
  onOpen,
  onSetPrice,
  onCreateItem,
}: {
  lines: Line[];
  mgmt: boolean;
  selected: Record<string, boolean>;
  focusId: string | null;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
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
          <th>Relationship</th>
          <th className="num">Avail.</th>
          <th className="num">Short.</th>
          <th className="num">Quoted ₹</th>
          <th className="num">Line total</th>
          {mgmt && <th className="num">Margin</th>}
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l, i) => {
          const rowClass = [
            focusId === l.id ? "focused" : "",
            l.status.kind === "technical" ? "tech" : l.flags.attention ? "attention" : "",
          ]
            .filter(Boolean)
            .join(" ");
          const econ = l.economics;
          return (
            <tr key={l.id} className={rowClass} onClick={() => onOpen(l.id)}>
              <td onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={!!selected[l.id]}
                  onChange={() => onToggle(l.id)}
                  style={{ accentColor: "var(--color-accent)" }}
                />
              </td>
              <td className="text-muted">{i + 1}</td>
              <td>
                <div className="mono">{l.reqCode}</div>
                <div className="req-desc">{l.reqDesc}</div>
              </td>
              <td className="num">{l.reqQty}</td>
              <td>
                {l.supplyCode ? (
                  <>
                    <div className={"mono" + (l.substituted ? " subst" : "")}>{l.supplyCode}</div>
                    <div className="req-desc">{l.supplyDesc || ""}</div>
                    {(l.sel === "USER" || l.sel === "MANUAL") && (
                      <div style={{ fontSize: 10.5 }} className={l.sel === "MANUAL" ? "warn" : ""}>
                        {l.sel === "MANUAL" ? "manually selected" : "user selected"}
                      </div>
                    )}
                    {l.inBooks === false && (
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          onCreateItem(l.id);
                        }}
                      >
                        + Create in Zoho
                      </button>
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
                  onBlur={(e) => {
                    const v = e.target.value.replace(/[^0-9.]/g, "");
                    onSetPrice(l.id, v === "" ? null : parseFloat(v));
                  }}
                />
              </td>
              <td className="num">{inr(l.lineTotal)}</td>
              {mgmt && (
                <td className="num">
                  {econ && econ.margin !== null ? (
                    <span className={econ.below_floor ? "warn" : ""}>
                      {(econ.margin * 100).toFixed(1)}%
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
              )}
              <td>
                <span
                  className="chip"
                  style={{ color: statusColor(l.status.kind), border: "1px solid currentColor" }}
                >
                  {l.status.label}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
