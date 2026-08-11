"""The quote total says how it was arrived at, not just what it is.

Until this existed the summary computed `subtotal * SALES_TAX_RATE` — one
blended rate over every line — while `zoho_books_service` sent no tax field on
the estimate at all, so Zoho priced each line from its own item settings. Two
tax authorities, disagreeing on any item that is not on the default rate, and
the one the customer receives is the one the screen did not compute.

`store.sales_tax_rate`'s docstring claimed "line-level tax from the ERP
supersedes this wherever it is available" throughout. Nothing of the sort
existed: a grep of the backend for tax_amount, tax_percentage or item_tax
returned nothing. The claim is what stopped anybody looking, which is why the
count below is the control and the docstring is not.
"""
from __future__ import annotations

from app.store import Line, Quote


def _line(idx: str, *, quoted: float, qty: int = 1,
          tax: float | None = None) -> Line:
    return Line(id=idx, raw=f"item-{idx}", reqCode=f"C{idx}", reqDesc="d",
                reqQty=qty, rel="EXACT", supplyCode=f"C{idx}", candidates=[],
                outcome="AUTO_MATCH", semantics="IDENTITY",
                quoted=quoted, taxPercent=tax)


def _quote(*lines: Line) -> Quote:
    return Quote(id="q1", customer="Acme", number="Q-1", lines=list(lines))


def test_a_line_is_taxed_at_the_rate_its_own_books_hold():
    q = _quote(_line("1", quoted=1000, tax=12.0))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 120.0, "12% of 1000, not the configured default"
    assert s["grand"] == 1120.0
    assert s["taxBasis"] == {"known": 1, "assumed": 0, "defaultRate": 0.18}


def test_a_line_with_no_rate_on_record_takes_the_default_and_is_counted():
    """The fallback stays — a pasted RFQ line has no item to ask — but it is
    reported. A total assembled from guesses reads differently from the same
    total assembled from facts, and only one of them is worth sending."""
    q = _quote(_line("1", quoted=1000))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 180.0
    assert s["taxBasis"]["assumed"] == 1
    assert s["taxBasis"]["known"] == 0
    assert s["taxRate"] == 0.18, "one assumed rate over the whole quote is printable"


def test_a_mixed_rate_quote_refuses_to_name_a_single_rate():
    """The misstatement this exists to stop.

    A quote holding an 18% line and a 12% line has no single rate, and a screen
    printing "GST 18%" over it is asserting something false about a document a
    customer will receive. The amount is still exact — it is the sum of two
    correct line computations — but there is deliberately no rate to print.
    """
    q = _quote(_line("1", quoted=1000, tax=18.0),
               _line("2", quoted=1000, tax=12.0))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 300.0, "180 + 120, each line at its own rate"
    assert s["taxRate"] is None
    assert s["taxBasis"] == {"known": 2, "assumed": 0, "defaultRate": 0.18}


def test_a_quote_mixing_a_known_rate_with_an_assumed_one_names_no_rate_either():
    """Known 12% plus an assumed 18% is still two rates. That the second one
    came from configuration rather than from the books does not make it the
    quote's rate."""
    q = _quote(_line("1", quoted=1000, tax=12.0), _line("2", quoted=1000))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 300.0
    assert s["taxRate"] is None
    assert s["taxBasis"] == {"known": 1, "assumed": 1, "defaultRate": 0.18}


def test_every_line_sharing_one_book_rate_prints_that_rate():
    q = _quote(_line("1", quoted=1000, tax=5.0), _line("2", quoted=500, tax=5.0))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 75.0
    assert s["taxRate"] == 0.05, "the books' own rate, not the configured one"
    assert s["taxBasis"]["assumed"] == 0


def test_an_unpriced_line_is_not_taxed_and_not_counted():
    """It contributes nothing to the subtotal, so it must contribute nothing to
    the basis either — counting it as assumed would report a guess that was
    never made."""
    q = _quote(_line("1", quoted=1000, tax=18.0),
               Line(id="2", raw="x", reqCode="C2", reqDesc="d", reqQty=1,
                    rel="UNRESOLVED", supplyCode=None, candidates=[],
                    outcome="NONE", semantics="REQUIREMENT"))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 180.0
    assert s["taxBasis"] == {"known": 1, "assumed": 0, "defaultRate": 0.18}
    assert s["unpriced"] == 1


def test_quantity_is_part_of_the_taxed_amount():
    q = _quote(_line("1", quoted=100, qty=10, tax=18.0))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["subtotal"] == 1000.0
    assert s["tax"] == 180.0


def test_an_empty_quote_reports_the_rate_that_would_be_used():
    """No lines is not a rate disagreement.

    The first version of this refused to name a rate on an empty quote, because
    it keyed the decision on the rates the *books* stated and an empty quote
    states none. `test_quote_summary_reports_the_rate_it_used` in
    test_currency_neutrality.py caught it — a test written for jurisdiction
    neutrality, guarding a case this change had not thought about.
    """
    s = _quote().to_dict(mgmt=True)["summary"]

    assert s["taxRate"] == 0.18
    assert s["tax"] == 0.0
    assert s["taxBasis"] == {"known": 0, "assumed": 0, "defaultRate": 0.18}


def test_a_known_rate_equal_to_the_default_still_prints_one_rate():
    """The decision is on the rates actually applied, not on where they came
    from. A line the books put at 18% and a line assumed at 18% were both taxed
    at 18%, so that is the quote's rate — and `taxBasis` is what discloses that
    one of them was an assumption."""
    q = _quote(_line("1", quoted=1000, tax=18.0), _line("2", quoted=1000))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 360.0
    assert s["taxRate"] == 0.18
    assert s["taxBasis"] == {"known": 1, "assumed": 1, "defaultRate": 0.18}


def test_a_zero_rated_item_is_not_the_same_as_an_unknown_one():
    """0% is a fact the books stated; None is the absence of one. Folding them
    together would apply 18% to a genuinely exempt line."""
    q = _quote(_line("1", quoted=1000, tax=0.0))
    s = q.to_dict(mgmt=True)["summary"]

    assert s["tax"] == 0.0
    assert s["grand"] == 1000.0
    assert s["taxBasis"] == {"known": 1, "assumed": 0, "defaultRate": 0.18}
