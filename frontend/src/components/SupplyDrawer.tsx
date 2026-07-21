import type { Line } from "../types";
import { REL_STYLE, inr } from "../rel";

export function SupplyDrawer({
  line,
  mgmt,
  onClose,
  onSelect,
  onRevert,
}: {
  line: Line;
  mgmt: boolean;
  onClose: () => void;
  onSelect: (code: string, manual: boolean) => void;
  onRevert: () => void;
}) {
  const exactSelected = line.supplyCode === line.reqCode;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
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
            <button className="btn btn-ghost btn-sm" onClick={onRevert} style={{ marginBottom: 8 }}>
              ↩ Revert to exact / requested
            </button>
          )}
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
                    <button className="btn btn-primary btn-sm" onClick={() => onSelect(c.code, false)}>
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
              Economics — cost {inr(line.economics.cost)} · recommended {inr(line.economics.recommended)}
              {line.economics.margin !== null
                ? ` · margin ${(line.economics.margin * 100).toFixed(1)}%`
                : ""}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
