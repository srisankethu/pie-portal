// The negotiation desk — the one screen here a salesperson uses to decide.
//
// Everything else in this product is read. This is the screen somebody sits at
// with a customer on the phone, so it is built around the three questions that
// actually get asked in that call, in the order they get asked:
//
//   "What can I give them?"      → the break-even discount, free to give
//   "What does that cost me?"    → the incentive, updated as they move it
//   "What do I have to hold?"    → the price that keeps the incentive intact
//
// **No cost and no margin appear here for a salesperson, by construction.**
// The server computes their incentive on the gap against what this customer
// already paid, not on margin — because an incentive of *k* × margin, with *k*
// published, is the cost by division. What the company keeps is absent from
// their payload entirely, and the screen says why rather than leaving a hole.

import { useState } from "react";
import { money } from "../../money";
import { papi } from "../api";
import type { PlatformSession } from "../types";
import { Panel } from "./Panel";
import { pct } from "./useInsight";

type Envelope = Record<string, unknown>;

const num = (v: unknown): number => Number(v ?? 0);

export function NegotiateScreen({
  session, customerId, productId,
}: {
  session: PlatformSession;
  customerId?: string;
  productId?: string;
}) {
  const [customer, setCustomer] = useState(customerId ?? "");
  const [product, setProduct] = useState(productId ?? "");
  const [qty, setQty] = useState("100");
  const [price, setPrice] = useState("");
  const [discount, setDiscount] = useState("0");
  const [vendorAsk, setVendorAsk] = useState("0");
  const [holdAt, setHoldAt] = useState("");

  const [data, setData] = useState<Envelope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canPrice = customer.trim() && product.trim()
    && Number(qty) > 0 && Number(price) > 0;

  async function run() {
    if (!canPrice) return;
    setBusy(true); setError(null);
    try {
      setData(await papi.negotiate(session.token, {
        customer_id: customer.trim(),
        product_id: product.trim(),
        qty: Number(qty),
        agreed_price: Number(price),
        customer_discount: Number(discount) || 0,
        vendor_concession: Number(vendorAsk) || 0,
        target_incentive: holdAt.trim() ? Number(holdAt) : null,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const negotiable = data?.negotiable === true;
  const configured = data?.scheme_configured === true;
  const reasons = (data?.approval_reasons as string[] | undefined) ?? [];
  const unavailable = (data?.unavailable as Record<string, string>[] | undefined) ?? [];
  const breakEven = data?.break_even_discount_per_unit as number | null | undefined;
  const holdPrice = data?.price_to_hold_incentive as number | null | undefined;
  // Present only for a manager or owner. Its absence is the invariant working.
  const seesCompany = data != null && "company_retained" in data;

  return (
    <Panel
      title="Negotiation desk"
      question="What can I give away, and what does it cost me"
      state={error ? "error" : "ready"}
      error={error}
      onRetry={run}
      wide
    >
      <div className="neg-form">
        <Field label="Customer id" value={customer} onChange={setCustomer}
               hint="From the account page URL." />
        <Field label="Item id" value={product} onChange={setProduct}
               hint="From the item drill-down." />
        <Field label="Quantity" value={qty} onChange={setQty} numeric />
        <Field label="Price you are agreeing" value={price} onChange={setPrice}
               numeric hint="Per unit, before any discount below." />
        <Field label="Discount to the customer" value={discount}
               onChange={setDiscount} numeric
               hint="Per unit. Funded from your incentive first." />
        <Field label="Concession asked of the vendor" value={vendorAsk}
               onChange={setVendorAsk} numeric
               hint="Per unit off what we pay today. A request, not a decision." />
        <Field label="Incentive you want to hold" value={holdAt} onChange={setHoldAt}
               numeric hint="Optional. Returns the price that leaves you exactly this." />
        <div className="neg-actions">
          <button type="button" className="btn btn-primary" disabled={!canPrice || busy}
                  onClick={run}>
            {busy ? "Working…" : "Work it out"}
          </button>
          {!canPrice && (
            <span className="viz-muted">
              Needs a customer, an item, a quantity and a price.
            </span>
          )}
        </div>
      </div>

      {data && !negotiable && (
        <p className="viz-headline">{String(data.empty_reason)}</p>
      )}

      {data && negotiable && (
        <>
          <p className="viz-muted">
            <strong>{String(data.customer_label)}</strong> ·{" "}
            {String(data.product_label)} · last paid{" "}
            <strong>{money(num(data.reference_price))}</strong> a unit.{" "}
            Everything below is measured against that.
          </p>

          {!configured && (
            <p className="viz-headline">
              No incentive scheme is configured, so this pays nothing. An owner
              sets the rates in Settings. The price realisation below is still
              real.
            </p>
          )}

          <div className="neg-figures">
            <Figure3 label="You won" value={money(num(data.realisation))}
                     note={`${pct(num(data.salesperson_share), 0)} of this is yours`}
                     tone={num(data.realisation) < 0 ? "bad" : "good"} />
            <Figure3 label="Your incentive" value={money(num(data.salesperson_incentive))}
                     note={num(data.self_funded) > 0
                       ? `after funding ${money(num(data.self_funded))} of the discount`
                       : "nothing given away yet"} />
            <Figure3 label="Free to give"
                     value={breakEven == null ? "—" : `${money(breakEven)} a unit`}
                     note={breakEven == null
                       ? "nothing earned to give away"
                       : `up to ${pct(num(data.self_funding_cap), 0)} of your incentive`} />
            {holdPrice != null && (
              <Figure3 label="Hold the price at" value={money(holdPrice)}
                       note="leaves you exactly what you asked for" />
            )}
            {/* Manager and owner only. A salesperson's payload has no such
                field, so this simply does not render for them. */}
            {seesCompany && (
              <Figure3 label="Company keeps"
                       value={money(num(data.company_retained))}
                       note={`incl. ${money(num(data.vendor_gain))} from the vendor ask`} />
            )}
          </div>

          {reasons.length > 0 && (
            <div className="neg-approval">
              <h4>Needs someone to agree</h4>
              <ul>{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}

          {unavailable.length > 0 && (
            <ul className="tl-unavailable">
              {unavailable.map((u, i) => (
                <li key={i}>
                  <strong>{String(u.series).replace(/_/g, " ")}</strong> — not
                  shown. <span className="viz-muted">{String(u.reason)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

function Field({
  label, value, onChange, hint, numeric,
}: {
  label: string; value: string; onChange: (v: string) => void;
  hint?: string; numeric?: boolean;
}) {
  const id = `neg-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="field neg-field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" value={value}
             inputMode={numeric ? "decimal" : undefined}
             onChange={(e) => onChange(e.target.value)} />
      {hint && <span className="viz-muted neg-hint">{hint}</span>}
    </div>
  );
}

function Figure3({
  label, value, note, tone,
}: { label: string; value: string; note?: string; tone?: "good" | "bad" }) {
  return (
    <div className="neg-figure">
      <span className="neg-figure-label">{label}</span>
      <span className={`neg-figure-value${tone ? ` ${tone}` : ""}`}>{value}</span>
      {note && <span className="viz-muted">{note}</span>}
    </div>
  );
}
