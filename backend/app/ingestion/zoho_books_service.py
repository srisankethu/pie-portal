"""Live Zoho Books adapter for the Quote Builder — the write side.

``ZohoApiSource`` next door reads the ledger for analysis and is read-only by
construction. This is the other half: the four calls the quoting screen makes,
against the same API, through the same ``ZohoTransport`` — one OAuth path, one
rate limiter, one error taxonomy. A second auth stack here is how a rotated
credential ends up applied on one side and stale on the other.

Three decisions are worth reading before changing anything in this file.

**A write is never replayed on a guess.** ``ZohoTransport`` refuses to retry a
POST after a 5xx, because a 5xx cannot be told apart from a write that landed
and lost its response, and replaying that puts two estimates in front of one
customer. Instead this adapter makes the write *re-checkable*: every estimate
carries the caller's ``reference``, so both the pre-flight ("has this quote
already been sent?") and the recovery ("did the one that timed out land?") are
the same cheap GET. Where even that GET fails, the caller gets
``ZohoWriteUnknown`` with the reference to look up — never a fabricated success.

**Nothing is invented to make the write succeed.** No contact is created for an
unrecognised customer, no item is silently added mid-estimate, and a line with
no price is refused rather than sent without a rate for Zoho to fill in from the
item master. Each of those would produce a document that looks right and says
something the salesperson never agreed to.

**Cost is read but never travels.** ``purchase_rate`` lands in ``ZohoItem.cost``
because management pricing needs it; it reaches a response only through
``Line.to_dict(mgmt=True)``, which is where that gate lives and stays.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, List, Optional

from ..config import settings
from ..identity.matchers import normalize_sku
from ..zoho import (
    ZohoEstimate,
    ZohoItem,
    ZohoUnavailable,
    ZohoWriteRefused,
    ZohoWriteUnknown,
)
from .normalize import NormalizationError, _parse_decimal
from .write_settle import settle_by_read
from .zoho_client import (
    ZohoCredentials,
    ZohoError,
    ZohoTransport,
    ZohoWriteUncertain,
)

log = logging.getLogger("pie_portal.zoho.quote")

# Zoho's own names for on-hand quantity, most specific first. An item that is
# not inventory-tracked carries none of them, which is "availability unknown"
# rather than "none in stock" — a distinction the line status already draws.
_STOCK_FIELDS = ("available_stock", "actual_available_stock", "stock_on_hand")


def _money(raw: Any, ctx: str, field: str) -> Optional[float]:
    """A Zoho money string as a number, parsed the way ingestion parses money.

    Zoho sends money as strings, and ``float("1,234.50")`` raises while
    ``Decimal`` at least fails loudly on the same input — so this goes through
    the same ``_parse_decimal`` the read pipeline uses rather than growing a
    second, laxer idea of what a number is.

    The ``float`` at the end is a boundary conversion, not a preference:
    ``ZohoItem`` and ``pricing.compute_economics`` are float-typed today, and
    handing them a ``Decimal`` would only push the same conversion one call
    deeper while making this the one adapter that disagrees with the dataclass
    it fills in.
    """
    if raw in (None, ""):
        return None
    try:
        return float(_parse_decimal(raw, ctx, field))
    except NormalizationError as e:
        raise ZohoUnavailable(
            f"Zoho returned an unusable {field} for {ctx}: {e.detail}") from e


def _stock(raw: dict[str, Any], ctx: str) -> Optional[int]:
    """On-hand quantity, or None when Zoho does not track it for this item."""
    for field in _STOCK_FIELDS:
        if raw.get(field) not in (None, ""):
            try:
                return int(_parse_decimal(raw[field], ctx, field))
            except NormalizationError as e:
                raise ZohoUnavailable(
                    f"Zoho returned an unusable {field} for {ctx}: {e.detail}") from e
    return None


class ZohoBooksService(ZohoTransport):
    """The ``ZohoService`` protocol, against one live Zoho Books company.

    One instance is bound to one connection's credentials, which is what makes
    "the right company writes the estimate" a property of construction rather
    than a check somebody has to remember.
    """

    def __init__(self, credentials: Optional[ZohoCredentials] = None,
                 http: Any = None) -> None:
        super().__init__(http=http, credentials=credentials)
        # ``available`` is read once per quote line, so the reachability probe is
        # cached: without this, a fifty-line RFQ would spend fifty calls of the
        # rate-limit budget asking the same question.
        self._reachable: Optional[bool] = None
        self._reachable_until: float = 0.0

    # ── reads ────────────────────────────────────────────────────────────────
    def _find_item(self, code: str) -> Optional[dict[str, Any]]:
        """The raw Zoho item whose SKU or name *is* this code.

        ``search_text`` is Zoho's own fuzzy search across name, SKU and
        description, so the result is a candidate list and the match is decided
        here: exact equality on the normalised SKU first, then on the normalised
        name. Accepting Zoho's ranking instead would let "CNMG 120408" quote the
        price of "CNMG 120412".
        """
        wanted = normalize_sku(code)
        if not wanted:
            return None
        body = self._get("items", search_text=code, per_page=settings.ZOHO_PAGE_SIZE)
        rows = body.get("items") or []
        if not isinstance(rows, list):
            # A shape nobody expected. Falling through would let a string be
            # iterated character by character and quietly match nothing, which
            # reads on screen as "not in books" — a wrong answer rather than a
            # visible failure.
            raise ZohoUnavailable(
                f"Zoho returned an unexpected shape for items (got {type(rows).__name__}).")
        for row in rows:
            if normalize_sku(row.get("sku")) == wanted:
                return row
        for row in rows:
            if normalize_sku(row.get("name")) == wanted:
                return row
        return None

    def _item_from(self, raw: dict[str, Any], code: str) -> ZohoItem:
        ctx = f"item {raw.get('item_id') or code}"
        # An inactive item exists in the ledger and cannot be put on a document,
        # so for quoting purposes it is not in the books. Reporting it as
        # available would move the failure to the moment the estimate is sent.
        active = str(raw.get("status") or "active").lower() == "active"
        return ZohoItem(
            code=str(raw.get("sku") or code),
            name=str(raw.get("name") or code),
            in_books=active,
            list_price=_money(raw.get("rate"), ctx, "rate"),
            stock=_stock(raw, ctx),
            cost=_money(raw.get("purchase_rate"), ctx, "purchase_rate"),
            item_id=(str(raw["item_id"]) if raw.get("item_id") else None),
            # Read through `_money` for the reason every other number here is:
            # Zoho sends numbers as strings often enough that a bare float()
            # would raise on a live book, and a tax rate that cannot be parsed
            # must read as "not stated" rather than take the screen down.
            tax_percentage=_money(raw.get("tax_percentage"), ctx, "tax_percentage"),
        )

    def get_item(self, code: str) -> Optional[ZohoItem]:
        if not code:
            return None
        raw = self._read(lambda: self._find_item(code))
        if raw is None:
            # Known code, absent from this company's books: the NOT IN BOOKS
            # state, with no price attached. Deliberately not None — None reads
            # as "nothing to say about this code at all".
            return ZohoItem(code=code, name=code, in_books=False,
                            list_price=None, stock=None, cost=None)
        return self._item_from(raw, code)

    def _read(self, call):
        """Run a read, collapsing transport failure into ``ZohoUnavailable``.

        The quote flow has exactly one thing it can do about an unreadable
        ledger — say BOOKS OFFLINE on the line — so translating here keeps
        Zoho's error taxonomy from leaking into ``store.py``.
        """
        try:
            return call()
        except ZohoError as e:
            log.warning("zoho quote read failed: %s", e)
            raise ZohoUnavailable(str(e)) from e

    @property
    def available(self) -> bool:
        """Whether this company's books answered recently. Never raises.

        Genuine reachability: the same ``ping`` the connections screen uses,
        which proves the credentials work *and* that this organization id is one
        the token can see — a token that authenticates against the wrong data
        centre's org fails here rather than at the moment of the write.
        """
        now = time.monotonic()
        if self._reachable is not None and now < self._reachable_until:
            return self._reachable
        try:
            info = self.ping()
            ok = bool(info.get("organization_found"))
            if not ok:
                log.warning("zoho quote: organization %s is not visible to this token",
                            self._org)
        except Exception as e:                       # noqa: BLE001
            # Deliberately broad: this is a health probe, and any failure — a
            # rejected token, a throttle, a dropped socket — means the same
            # thing to the screen. A probe that raises would turn an outage
            # into a 500 on every line of the quote.
            log.warning("zoho quote: books unreachable (%s)", e)
            ok = False
        self._reachable = ok
        self._reachable_until = now + max(0.0, settings.ZOHO_HEALTH_TTL_SECONDS)
        return ok

    # ── writes ───────────────────────────────────────────────────────────────
    def create_item(self, code: str, name: str,
                    list_price: Optional[float] = None) -> ZohoItem:
        """Add the item to this company's books.

        Idempotent by re-read: Zoho refuses a duplicate name or SKU, and the
        right answer to "it is already there" is the item that is already there,
        not an error the salesperson cannot act on.
        """
        payload: dict[str, Any] = {"name": name or code, "sku": code,
                                   "product_type": "goods"}
        if list_price is not None:
            payload["rate"] = str(Decimal(str(list_price)))
        try:
            body = self._request("POST", "items", json=payload)
        except ZohoWriteUncertain as e:
            # The POST may or may not have landed. A read settles it, and a read
            # is safe — unlike the retry the transport correctly refused.
            return self._settle_item(code, str(e))
        except ZohoError as e:
            existing = self._recover_item(code)
            if existing is not None:
                return existing
            raise ZohoWriteRefused(
                f"Zoho would not create item {code}: {e}", codes=[code]) from e
        except Exception as e:                       # noqa: BLE001
            # A transport-level fault (timeout, dropped connection) never
            # reached the retry logic, so the request's fate is unknown for the
            # same reason a 5xx is. Same treatment: settle it by reading, never
            # by sending again. Without this the fault left this method
            # unwrapped and the caller got a 500 — no outcome it could act on,
            # for an item that may well be sitting in the books.
            return self._settle_item(code, str(e))
        raw = body.get("item") or {}
        return self._item_from(raw, code)

    def _settle_item(self, code: str, detail: str) -> ZohoItem:
        """Decide what actually happened to an item write whose response was lost.

        One search answers it: the item carries the SKU that was sent, so either
        it is there (the write landed — report it, do not send again) or it is
        not (nothing landed — refuse, and the caller may retry safely). Only
        when the lookup itself fails is the outcome genuinely unknown, and that
        is the one case that says so.

        These are the same three verdicts on the same evidence that
        ``_settle_estimate`` reaches, deliberately. A read that succeeded and
        found nothing is evidence that nothing landed; reporting it as UNKNOWN
        would discard it and send a salesperson hunting Zoho by hand for a
        record the read just proved is not there.
        """
        log.warning("zoho quote: item write outcome unclear for %s (%s)", code, detail)
        return settle_by_read(
            lambda: self._find_item(code),
            lambda raw: self._quotable_item(code, raw),
            unknown_message=lambda e: (
                f"Item {code} could not be completed ({detail}), and Zoho could not be "
                f"re-read to find out ({e}) — whether it reached Zoho cannot be "
                f"established. Look for {code} in Zoho before adding it again."),
            refused_message=(
                f"Item {code} was not created ({detail}). Zoho holds nothing under that "
                f"code, so adding it again is safe."),
            codes=[code],
            unknown_error=ZohoWriteUnknown, refused_error=ZohoWriteRefused)

    def _quotable_item(self, code: str, raw: dict[str, Any]) -> ZohoItem:
        """The item Zoho holds, refusing the one state that cannot be quoted."""
        item = self._item_from(raw, code)
        # An item that exists but is archived cannot be quoted, and "created"
        # would be the wrong word for finding it.
        if not item.in_books:
            raise ZohoWriteRefused(
                f"Item {code} already exists in Zoho but is inactive — reactivate "
                f"it there before quoting it.", codes=[code])
        return item

    def _recover_item(self, code: str) -> Optional[ZohoItem]:
        """The item as Zoho holds it now, or None if the lookup itself failed.

        Used only after a write whose outcome is in doubt, so a lookup failure
        must not be reported as "not there" — that is the difference between
        "nothing happened" and "we cannot tell", and only the second one is
        true here.
        """
        try:
            raw = self._find_item(code)
        except Exception:                            # noqa: BLE001 — see _settle_item
            return None
        if raw is None:
            return None
        return self._quotable_item(code, raw)

    def _estimate_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        body = self._get("estimates", reference_number=reference)
        for row in body.get("estimates") or []:
            if str(row.get("reference_number") or "") == reference:
                return row
        return None

    @staticmethod
    def _estimate_from(raw: dict[str, Any], customer: str,
                       line_count: int, already_existed: bool) -> ZohoEstimate:
        raw_lines = raw.get("line_items") or []
        return ZohoEstimate(
            document_id=str(raw.get("estimate_id") or ""),
            number=str(raw.get("estimate_number") or ""),
            customer=str(raw.get("customer_name") or customer),
            line_count=len(raw_lines) if raw_lines else line_count,
            already_existed=already_existed,
        )

    @staticmethod
    def _estimate_lines(lines: List[dict]) -> list[dict[str, Any]]:
        """Quote lines as Zoho line items, or a refusal naming what is wrong.

        Reads the ``itemId`` the line already resolved against these books
        rather than matching the code again: a second match is a call per line
        against the rate limit *and* a second chance to land on a different
        item than the one whose price is on screen.

        Both checks refuse rather than degrade. A line with no item id would
        become a free-text row that no stock or margin analysis can ever see
        again; a line with no rate would be priced by Zoho from the item master,
        which is a number the salesperson never chose.
        """
        missing: list[str] = []
        unpriced: list[str] = []
        out: list[dict[str, Any]] = []
        for line in lines:
            code = str(line.get("code") or "")
            item_id = line.get("itemId")
            rate = line.get("rate")
            if not item_id:
                missing.append(code)
                continue
            if rate is None:
                unpriced.append(code)
                continue
            out.append({
                "item_id": str(item_id),
                "quantity": str(Decimal(str(line.get("qty") or 0))),
                "rate": str(Decimal(str(rate))),
            })
        if missing or unpriced:
            parts = []
            if missing:
                parts.append(f"not in these books: {', '.join(sorted(missing))}")
            if unpriced:
                parts.append(f"no price set: {', '.join(sorted(unpriced))}")
            raise ZohoWriteRefused(
                "The estimate was not created — " + "; ".join(parts) + ".",
                codes=sorted(set(missing) | set(unpriced)))
        if not out:
            raise ZohoWriteRefused("The estimate was not created — it has no lines.")
        return out

    def create_sales_quotes(self, customer: str, lines: List[dict], *,
                        customer_ref: Optional[str] = None,
                        reference: Optional[str] = None) -> ZohoEstimate:
        if not customer_ref:
            raise ZohoWriteRefused(
                "This quote's customer could not be tied to a contact in these "
                "books, so there is no account to write the estimate to.")
        if not reference:
            # Without a reference the write cannot be made re-checkable, and an
            # un-re-checkable write into a real ledger is the thing this adapter
            # is not willing to do.
            raise ZohoWriteRefused(
                "This quote has no reference, so an estimate created for it could "
                "not be found again — refusing to write one.")

        # Pre-flight: the same quote sent twice must not become two estimates.
        existing = self._read(lambda: self._estimate_by_reference(reference))
        if existing is not None:
            return self._estimate_from(existing, customer, len(lines), already_existed=True)

        payload = {
            "customer_id": str(customer_ref),
            "reference_number": reference,
            "line_items": self._estimate_lines(lines),
        }
        try:
            body = self._request("POST", "estimates", json=payload)
        except ZohoWriteUncertain as e:
            return self._settle_estimate(reference, customer, len(lines), str(e))
        except ZohoError as e:
            # Zoho answered, and said no. Nothing was written.
            raise ZohoWriteRefused(f"Zoho refused the estimate: {e}") from e
        except Exception as e:                       # noqa: BLE001
            # A transport-level fault (timeout, dropped connection) never
            # reached the retry logic, so the request's fate is unknown for the
            # same reason a 5xx is. Same treatment: settle it by reading, never
            # by sending again.
            return self._settle_estimate(reference, customer, len(lines), str(e))
        return self._estimate_from(body.get("estimate") or {}, customer,
                                   len(lines), already_existed=False)

    def _settle_estimate(self, reference: str, customer: str, line_count: int,
                         detail: str) -> ZohoEstimate:
        """Decide what actually happened to a write whose response was lost.

        One GET answers it: the estimate carries the reference, so either it is
        there (the write landed — report it, do not send again) or it is not
        (nothing landed — refuse, and the caller may retry safely). Only when
        the lookup itself fails is the outcome genuinely unknown, and that is
        the one case that says so.
        """
        log.warning("zoho quote: estimate write outcome unclear for %s (%s)",
                    reference, detail)
        return settle_by_read(
            lambda: self._estimate_by_reference(reference),
            lambda landed: self._estimate_from(landed, customer, line_count,
                                               already_existed=True),
            unknown_message=lambda e: (
                f"The estimate could not be completed ({detail}), and Zoho could not be "
                f"re-read to find out ({e}) — whether it reached Zoho cannot be "
                f"established. Look for reference {reference} in Zoho before sending "
                f"this quote again."),
            refused_message=(
                f"The estimate was not created ({detail}). Zoho holds nothing under "
                f"reference {reference}, so sending again is safe."),
            reference=reference,
            unknown_error=ZohoWriteUnknown, refused_error=ZohoWriteRefused)
