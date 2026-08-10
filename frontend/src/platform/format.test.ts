// Turning already-computed values into the words on screen.
//
// Nothing here decides anything — the numbers arrive computed, and `format.ts`
// says so in its first line. What it can still get wrong is the *unit*, and
// that is what these tests are about: a ratio printed as though it were rupees,
// a rupee figure printed as the raw decimal the fold stored it as, or a count
// rendered with a decimal tail. Each of those has happened in this file's
// history, and none of them throws.
//
// The rules are substring rules over a label, so the cases worth writing are
// the ones where two rules could both claim a label and the order decides.
import { beforeEach, describe, expect, it } from "vitest";

import {
  CONF_LABEL,
  MONEY_STATE_FIELDS,
  ROLE_LABEL,
  aiState,
  factLabel,
  factValue,
  isAre,
  isPrimaryFact,
  stateFieldLabel,
  stateFieldValue,
} from "./format";
import { money, setMoneyCurrency } from "../money";

// `money()` reads the organization's currency from module scope, and these
// assertions compare against it — so state the currency rather than inheriting
// whatever ran first.
beforeEach(() => setMoneyCurrency("INR"));

describe("isAre", () => {
  it("agrees with the count, including the one that reads wrong first", () => {
    // "1 are past their own buying rhythm" is what this replaced, and one is
    // the common case on a small book.
    expect(isAre(1)).toBe("is");
    expect(isAre(0)).toBe("are");
    expect(isAre(2)).toBe("are");
  });
});

describe("stateFieldValue", () => {
  it("renders a money field as money, not as the stored decimal", () => {
    // The bug it was written for: the evidence table rendered every value with
    // String(v), so ₹6,12,300 arrived as "612300".
    const rendered = stateFieldValue("revenue", 612300);
    expect(rendered).toBe(money(612300));
    expect(rendered).not.toBe("612300");
  });

  it("formats a money value the same whether it arrives as a number or a string", () => {
    // Several endpoints serialize money as a string to avoid the float
    // round-trip, so both shapes genuinely arrive here.
    expect(stateFieldValue("outstanding", "45000")).toBe(stateFieldValue("outstanding", 45000));
  });

  it("leaves a field that is not money alone", () => {
    // `open_purchase_count` contains neither "balance" nor "value", and
    // `outstanding` contains neither word either — which is why the set is
    // explicit rather than a substring test, in both directions.
    expect(stateFieldValue("open_purchase_count", 12)).toBe("12");
    expect(MONEY_STATE_FIELDS.has("outstanding")).toBe(true);
    expect(MONEY_STATE_FIELDS.has("open_purchase_count")).toBe(false);
  });

  it("says nothing rather than zero when there is no value", () => {
    // A dash is "we do not have this". A zero is a claim.
    expect(stateFieldValue("revenue", null)).toBe("—");
    expect(stateFieldValue("revenue", undefined)).toBe("—");
  });

  it("renders a boolean as a word", () => {
    expect(stateFieldValue("is_active", true)).toBe("yes");
    expect(stateFieldValue("is_active", false)).toBe("no");
  });

  it("leaves a money field that is not a number as it found it", () => {
    // Withheld rather than coerced: Number("n/a") is NaN, and money(NaN) would
    // put an em dash where the server had something to say.
    expect(stateFieldValue("revenue", "n/a")).toBe("n/a");
  });
});

describe("stateFieldLabel", () => {
  it("prefers the written label and falls back to the field name, readably", () => {
    expect(stateFieldLabel("first_purchased_on")).toBe("First purchased");
    // A field nobody has named yet still reads as words, not as an identifier.
    expect(stateFieldLabel("some_new_field")).toBe("some new field");
  });
});

describe("factLabel", () => {
  it("drops the driver prefix and the list index, and says % in words", () => {
    expect(factLabel("drivers.price_change_pct")).toBe("Price change %");
    expect(factLabel("top_products[0]")).toBe("Top products");
    expect(factLabel("margin_recent")).toBe("Margin recent");
  });
});

describe("factValue", () => {
  it("renders a ratio as a percentage to one place", () => {
    expect(factValue("margin_recent", 0.191)).toBe("19.1%");
    expect(factValue("price_change_pct", -0.2)).toBe("-20.0%");
    expect(factValue("overdue_ratio", 1.5)).toBe("150.0%");
  });

  it("renders money as money", () => {
    expect(factValue("revenue_recent", 480000)).toBe(money(480000));
    expect(factValue("unit_cost", 124)).toBe(money(124));
  });

  it("renders a count as a whole number", () => {
    // A transaction count with a decimal tail reads as a bug in the analysis
    // even when the analysis is right.
    expect(factValue("transaction_count", 7.0)).toBe("7");
    expect(factValue("days_since_last", 176.4)).toBe("176");
  });

  it("reads a percentage label as a percentage even when it also says price", () => {
    // Both rules can claim `price_change_pct`, and the percentage rule is
    // checked first on purpose — the file says so. Printed as money it would be
    // "₹-0.20" for a twenty percent fall.
    expect(factValue("price_change_pct", -0.2)).toBe("-20.0%");
    expect(factValue("drivers.cost_change_pct", 0.24)).toBe("24.0%");
  });

  it("reads a plain money change as money, not as a percentage", () => {
    // The other side of the same ordering: "change" with no pct/margin/ratio in
    // it is an amount.
    expect(factValue("revenue_change", 25000)).toBe(money(25000));
  });

  it("renders a boolean as a word and leaves a string alone", () => {
    expect(factValue("is_costed", true)).toBe("yes");
    expect(factValue("subject", "Customer C-KQNVXB")).toBe("Customer C-KQNVXB");
  });
});

describe("isPrimaryFact", () => {
  it("keeps the fact and drops its list expansion", () => {
    expect(isPrimaryFact("top_products")).toBe(true);
    expect(isPrimaryFact("top_products[0]")).toBe(false);
  });
});

describe("aiState", () => {
  it("maps each status the server sends onto the state the design has", () => {
    expect(aiState("OK")).toBe("ok");
    expect(aiState("DEGRADED")).toBe("degraded");
    expect(aiState("FAILED")).toBe("failed");
    // SUPPRESSED is "withheld" on screen: the platform chose not to say
    // something, which is not the same as having failed to.
    expect(aiState("SUPPRESSED")).toBe("withheld");
  });

  it("treats a status this build does not know as pending, not as ok", () => {
    // The safe direction. A status the server adds tomorrow must not render as
    // a confident green tick here.
    expect(aiState("SOMETHING_NEW")).toBe("pending");
    expect(aiState("")).toBe("pending");
  });
});

describe("the label tables", () => {
  it("names every role and every confidence level the app can receive", () => {
    expect(ROLE_LABEL.SALESPERSON).toBe("Salesperson");
    expect(ROLE_LABEL.SALES_MANAGER).toBe("Sales manager");
    expect(ROLE_LABEL.OWNER).toBe("Owner");
    expect(Object.keys(CONF_LABEL).sort())
      .toEqual(["INSUFFICIENT", "PARTIAL", "SUFFICIENT"]);
  });
});
