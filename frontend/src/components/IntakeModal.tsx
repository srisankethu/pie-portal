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
          <div>
            <h3 style={{ margin: 0 }}>Paste RFQ</h3>
            <p className="text-muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
              One request per line. The engine resolves each line into a quote-ready product.
            </p>
          </div>
        </div>
        <div className="panel-body">
          <div className="field">
            <label>RFQ text</label>
            <textarea
              className="input"
              autoFocus
              placeholder={SAMPLE}
              value={text}
              title="Paste the RFQ text to resolve it into quote lines"
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="helper-stack">
            <div className="helper-card">
              <strong>Accepted formats</strong>
              <ul>
                <li>Manufacturer code</li>
                <li>Loose description</li>
                <li>Quantity in the form <code>code, qty</code> or <code>code xNN</code></li>
              </ul>
            </div>
            <div className="helper-card">
              <strong>What happens next</strong>
              <p>The quote grid will show matching supply products, availability, and the next best action.</p>
            </div>
          </div>
          <div className="empty-state-actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              title="Load the sample RFQ format"
              onClick={() => setText(SAMPLE)}
            >
              Use sample RFQ
            </button>
          </div>
        </div>
        <div className="panel-foot">
          <button type="button" className="btn btn-secondary" title="Close the RFQ intake dialog" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!text.trim()}
            title="Resolve the pasted RFQ into quote lines"
            onClick={() => onSubmit(text)}
          >
            Resolve &amp; add
          </button>
        </div>
      </div>
    </div>
  );
}
