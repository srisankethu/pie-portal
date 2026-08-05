// The negotiation desk — the one screen here a salesperson uses to decide.
//
// Everything else in this product is read. This is the screen somebody sits at
// with a customer on the phone, so it is built around the questions that
// actually get asked in that call, in the order they get asked:
//
//   "What can I give them?"   → the discount that takes the line to the floor
//   "What does that cost me?" → the contribution, recomputed as they move it
//   "What do I have to hold?" → the price that leaves a target contribution
//
// **The floor is the whole design.** A salesperson sees F and their own agreed
// price, and everything else is arithmetic they can do themselves: price, less
// floor, times quantity. What the item cost is never in the payload — not
// hidden, absent — and the screen says so rather than leaving a hole where a
// number should be.
//
// The four levers below the price are the two-sided negotiation the desk
// exists for: give something to the customer, ask something of the vendor, and
// see immediately what each does to the same figure.

import { useEffect, useState } from "react";
import { money } from "../../money";
import { papi } from "../api";
import type { Account, AccountItem, PlatformSession, StatusFilter } from "../types";
import { Panel } from "./Panel";

type Envelope = Record<string, unknown>;

const num = (v: unknown): number => Number(v ?? 0);

/** The payment-timing choices, as days past due. Deliberately days rather than
 *  band names: the server owns the band table, and a second copy here would be
 *  the copy that disagrees after the next re-cut. */
const TIMING: { label: string; days: number }[] = [
  { label: "Advance / against delivery", days: -1 },
  { label: "On the due date", days: 0 },
  { label: "Up to a month late", days: 30 },
  { label: "One to two months late", days: 60 },
  { label: "Two to three months late", days: 90 },
];

/** The floor tables published in `parameters.yaml`. A fixed list because these
 *  are policy, not data: a family only exists here once somebody has set a floor
 *  markup for it, and a free-text box would let a typo silently fall back to the
 *  default floor without saying so. */
const FAMILIES: { value: string; label: string }[] = [
  { value: "", label: "Use the default floor" },
  { value: "inserts", label: "Inserts" },
  { value: "solid_carbide", label: "Solid carbide" },
  { value: "holders_toolsystems", label: "Holders and tool systems" },
  { value: "metrology", label: "Metrology" },
  { value: "chemicals", label: "Chemicals" },
  { value: "machines", label: "Machines" },
];

export function NegotiateScreen({
  session, customerId, productId,
}: {
  session: PlatformSession;
  customerId?: string;
  productId?: string;
}) {
  const [customer, setCustomer] = useState(customerId ?? "");
  const [product, setProduct] = useState(productId ?? "");
  const [family, setFamily] = useState("");
  const [qty, setQty] = useState("100");
  const [price, setPrice] = useState("");
  const [discount, setDiscount] = useState("0");
  const [vendorAsk, setVendorAsk] = useState("0");
  const [thirdParty, setThirdParty] = useState("0");
  const [toolkit, setToolkit] = useState("0");
  const [timing, setTiming] = useState(0);
  const [holdAt, setHoldAt] = useState("");

  const [data, setData] = useState<Envelope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The pickers. A salesperson knows their customer's name and the tool they
  // are quoting; nobody knows either id, and asking for one means leaving the
  // screen to go and find it. The account list is already role-scoped server
  // side, so this dropdown shows exactly the accounts they may negotiate on.
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [items, setItems] = useState<AccountItem[]>([]);
  const [itemsLoading, setItemsLoading] = useState(false);
  // Discontinued items are excluded by default. They are in the master now —
  // the pull reads inactive items so their history resolves — but an item Zoho
  // says is retired should not be the one picked by accident on a live quote.
  // "All" is here because a retired item is still sometimes sold off the shelf.
  const [itemStatus, setItemStatus] = useState<StatusFilter>("active");

  useEffect(() => {
    let live = true;
    papi.listAccounts(session.token)
      .then((rows) => { if (live) setAccounts(rows); })
      .catch(() => { /* the picker degrades to empty; the error surfaces on submit */ });
    return () => { live = false; };
  }, [session.token]);

  useEffect(() => {
    if (!customer) { setItems([]); return; }
    let live = true;
    setItemsLoading(true);
    papi.listAccountItems(session.token, customer, itemStatus)
      .then((rows) => { if (live) setItems(rows); })
      .catch(() => { if (live) setItems([]); })
      .finally(() => { if (live) setItemsLoading(false); });
    return () => { live = false; };
  }, [session.token, customer, itemStatus]);

  const canPrice = customer.trim() && product.trim()
    && Number(qty) > 0 && Number(price) > 0;

  async function run() {
    if (!canPrice) return;
    setBusy(true); setError(null);
    try {
      setData(await papi.negotiate(session.token, {
        customer_id: customer.trim(),
        product_id: product.trim(),
        family: family.trim() || null,
        qty: Number(qty),
        agreed_price: Number(price),
        customer_discount: Number(discount) || 0,
        vendor_concession: Number(vendorAsk) || 0,
        third_party_incentive: Number(thirdParty) || 0,
        toolkit_spend: Number(toolkit) || 0,
        expected_days_late: timing,
        target_caf: holdAt.trim() ? Number(holdAt) : null,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const negotiable = data?.negotiable === true;
  const warnings = (data?.warnings as string[] | undefined) ?? [];
  const unavailable = (data?.unavailable as Record<string, string>[] | undefined) ?? [];
  const freeToGive = data?.discount_to_floor_per_unit as number | null | undefined;
  const holdPrice = data?.price_to_hold_target as number | null | undefined;
  const lastPaid = data?.last_price_paid as number | null | undefined;
  const blocked = data != null && data.third_party_allowed === false;
  // Present only for a manager or owner. Its absence is the invariant working.
  const seesCost = data != null && "unit_cost" in data;

  return (
    <Panel
      title="Negotiation desk"
      question="What can I give away, and what does it leave"
      state={error ? "error" : "ready"}
      error={error}
      onRetry={run}
      wide
    >
      <div className="neg-form">
        <Choice label="Customer" value={customer}
                onChange={(v) => { setCustomer(v); setProduct(""); }}
                hint={accounts.length ? undefined : "No accounts are assigned to you yet."}
                options={[
                  { value: "", label: "Choose an account…" },
                  ...accounts.map((a) => ({ value: a.customer_id, label: a.name })),
                ]} />
        <Choice label="Item" value={product} onChange={setProduct}
                disabled={!customer || itemsLoading}
                hint={!customer
                  ? "Choose the account first — the list is what they buy."
                  : itemsLoading
                    ? "Loading…"
                    : items.length
                      ? "What this account has bought, most recent first."
                      : "This account has no purchase history to price against."}
                options={[
                  { value: "", label: "Choose an item…" },
                  ...items.map((i) => ({
                    value: i.product_id,
                    label: (i.sku ? `${i.name} · ${i.sku}` : i.name)
                      + (i.active ? "" : " (discontinued)"),
                  })),
                ]} />
        <Choice label="Items to offer" value={itemStatus}
                onChange={(v) => { setItemStatus(v as StatusFilter); setProduct(""); }}
                disabled={!customer}
                options={[
                  { value: "active", label: "In the current range" },
                  { value: "inactive", label: "Discontinued only" },
                  { value: "all", label: "Everything they have bought" },
                ]}
                hint="Discontinued items are hidden unless you ask for them." />
        <Choice label="Tool family" value={family} onChange={setFamily}
                options={FAMILIES}
                hint="Sets which floor applies." />
        <Field label="Quantity" value={qty} onChange={setQty} numeric />
        <Field label="Price you are agreeing" value={price} onChange={setPrice}
               numeric hint="Per unit, before any discount below." />
        <Field label="Discount to the customer" value={discount}
               onChange={setDiscount} numeric
               hint="Per unit. Comes straight off what the line contributes." />
        <Field label="Concession asked of the vendor" value={vendorAsk}
               onChange={setVendorAsk} numeric
               hint="Per unit. Credited in full — a rupee won here is worth a rupee held on price." />
        {/* Once the server has said this account is restricted the field is
            closed rather than left open to be refused. A legal block belongs
            next to the input somebody would have typed in, not in a banner
            under the results that appears on every deal and gets ignored. */}
        <Field label="Payment to someone at the customer" value={thirdParty}
               onChange={setThirdParty} numeric disabled={blocked}
               hint={blocked
                 ? "Not available on this account — see below."
                 : "A total, not per unit. Charged in full. Not permitted on a government, PSU or defence account."} />
        <Field label="Tooling, training or trials for them" value={toolkit}
               onChange={setToolkit} numeric
               hint="A total. Charged at half — the compliant lever is the cheaper one." />
        <Choice label="When the money arrives" value={String(timing)}
                onChange={(v) => setTiming(Number(v))}
                options={TIMING.map((t) => ({ value: String(t.days), label: t.label }))}
                hint="Contribution is banked on the invoice and earned on the receipt." />
        <Field label="Contribution you want to hold" value={holdAt} onChange={setHoldAt}
               numeric hint="Optional. Returns the price that leaves exactly this." />
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
            {String(data.product_label)} · floor{" "}
            <strong>{money(num(data.floor_price))}</strong> a unit
            {lastPaid != null && <> · last paid {money(lastPaid)}</>}.{" "}
            Everything below is measured against the floor.
          </p>

          <div className="neg-figures">
            <Figure3 label="Above the floor" value={money(num(data.contribution))}
                     note={`${qty} × (price − floor)`}
                     tone={num(data.contribution) < 0 ? "bad" : "good"} />
            <Figure3 label="After what you gave" value={money(num(data.caf))}
                     note={termNote(data)}
                     tone={num(data.caf) < 0 ? "bad" : undefined} />
            {/* Deliberately untinted. A line paid at sixty days is worth less,
                not lost, and the loss colour on a positive figure taught the
                first reader that a normal credit term was a bad deal. The
                factor in the note carries it. */}
            <Figure3 label={`Earned if paid ${String(data.collection_label)}`}
                     value={money(num(data.collected_caf))}
                     note={`×${num(data.collection_factor).toFixed(2)} at that timing`} />
            <Figure3 label="Free to give"
                     value={freeToGive == null ? "—" : `${money(freeToGive)} a unit`}
                     note={freeToGive == null
                       ? "this line is already at or under the floor"
                       : "takes the line exactly to the floor"} />
            {holdPrice != null && (
              <Figure3 label="Hold the price at" value={money(holdPrice)}
                       note="leaves exactly what you asked for" />
            )}
            {/* Manager and owner only. A salesperson's payload has no such
                field, so this simply does not render for them. */}
            {seesCost && (
              <Figure3 label="Gross profit"
                       value={money(num(data.gross_profit))}
                       note={`cost ${money(num(data.unit_cost))} a unit · the floor holds ${(num(data.margin_at_floor) * 100).toFixed(1)}%`} />
            )}
          </div>

          {blocked && (
            <div className="neg-approval">
              <h4>No third-party payment on this account</h4>
              <p className="viz-muted">
                This customer is classified as a government, PSU or defence
                buyer — or has not been classified at all. Until an owner
                records that it is a private account the desk will not price a
                payment to anyone there. Tooling, training and trials are the
                lever that is available, and they cost half.
              </p>
            </div>
          )}

          {warnings.length > 0 && (
            <div className="neg-approval">
              <h4>Worth knowing before you agree it</h4>
              <ul>{warnings.map((r, i) => <li key={i}>{r}</li>)}</ul>
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

/** What moved the figure between contribution and CAF, named rather than
 *  implied — three charges netting to a small number reads as an error. */
function termNote(data: Envelope): string {
  const parts: string[] = [];
  if (num(data.third_party_charged) > 0)
    parts.push(`less ${money(num(data.third_party_charged))} paid across`);
  if (num(data.toolkit_charged) > 0)
    parts.push(`less ${money(num(data.toolkit_charged))} of toolkit`);
  if (num(data.vendor_yield_credited) > 0)
    parts.push(`plus ${money(num(data.vendor_yield_credited))} from the vendor`);
  return parts.length ? parts.join(", ") : "nothing given away yet";
}

/** A labelled dropdown. Same shape as `Field` so the two sit on one grid.
 *
 *  Every choice on this screen is from a closed set — the accounts a person may
 *  negotiate on, the items that account buys, the published floor tables, the
 *  collection bands — so all of them are selects. A free-text id field on a
 *  screen used with a customer on the phone is a screen nobody uses twice. */
function Choice({
  label, value, onChange, options, hint, disabled,
}: {
  label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[];
  hint?: string; disabled?: boolean;
}) {
  const id = `neg-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="field neg-field">
      <label htmlFor={id}>{label}</label>
      {/* `title` on both: a select clips its own value, and tool names are
          long enough that "25mm shank turning ho…" is ambiguous between two
          real items. Hovering gives the whole thing back. */}
      <select id={id} className="input" value={value} disabled={disabled}
              title={options.find((o) => o.value === value)?.label}
              onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} title={o.label}>{o.label}</option>
        ))}
      </select>
      {hint && <span className="viz-muted neg-hint">{hint}</span>}
    </div>
  );
}

function Field({
  label, value, onChange, hint, numeric, disabled,
}: {
  label: string; value: string; onChange: (v: string) => void;
  hint?: string; numeric?: boolean; disabled?: boolean;
}) {
  const id = `neg-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="field neg-field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" value={value} disabled={disabled}
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
