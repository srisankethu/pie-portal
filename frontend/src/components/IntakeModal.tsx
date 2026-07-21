import { useState } from "react";

const SAMPLE = `2001174, 20
CNMG 120408 KCP25  50
2045826 x30
XZ-CUSTOM-778-NOTREAL, 5`;

export function IntakeModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel modal-center" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h3 style={{ margin: 0 }}>Paste RFQ</h3>
        </div>
        <div className="panel-body">
          <p className="text-muted" style={{ fontSize: 13 }}>
            One requested item per line — a manufacturer code, a loose description, optionally a
            quantity (<code>code, qty</code> / <code>code xNN</code>). Each line is resolved through
            the PIE engine.
          </p>
          <textarea
            className="input"
            autoFocus
            placeholder={SAMPLE}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="btn btn-ghost btn-sm" onClick={() => setText(SAMPLE)}>
            Use sample RFQ
          </button>
        </div>
        <div className="panel-foot">
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!text.trim()} onClick={() => onSubmit(text)}>
            Resolve &amp; add
          </button>
        </div>
      </div>
    </div>
  );
}
