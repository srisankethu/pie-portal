"""The three verdicts a lost write can settle into, pinned on the helper itself.

The Zoho adapter's own tests cover these through a fake HTTP layer, which is
the right test for that adapter and the wrong place to pin the *shape*: the
next connector to write a record will use this function and share nothing with
those fixtures. So the contract is asserted here, on a read that is a plain
callable, where the case that matters most — a read that fails rather than
finds nothing — takes one line to express.
"""
from __future__ import annotations

import pytest

from app.ingestion.errors import SourceWriteRefused, SourceWriteUnknown
from app.ingestion.write_settle import settle_by_read


def _settle(read, **kw):
    return settle_by_read(
        read, lambda found: f"presented:{found}",
        unknown_message=lambda e: f"unknown because {e}",
        refused_message="nothing landed, retrying is safe", **kw)


def test_a_record_the_read_finds_is_reported_not_written_again():
    """The write landed and its answer was lost. The record is the answer."""
    assert _settle(lambda: "REC-1") == "presented:REC-1"


def test_a_read_that_finds_nothing_is_a_refusal_the_caller_may_retry():
    """The distinction this helper exists for. A successful read that finds
    nothing has *established* that nothing landed — reporting UNKNOWN would
    discard that and send someone searching for a record it just disproved."""
    with pytest.raises(SourceWriteRefused) as e:
        _settle(lambda: None, codes=["ITEM-9"])
    assert "retrying is safe" in str(e.value)
    assert e.value.codes == ["ITEM-9"], "the lines responsible must survive"


def test_a_read_that_itself_fails_is_unknown_and_names_what_to_look_up():
    """The other half. Nothing was established, so neither verdict is available
    and the only honest answer names the reference a person must go and check."""
    def boom():
        raise TimeoutError("connection timed out")

    with pytest.raises(SourceWriteUnknown) as e:
        _settle(boom, reference="Q-1042")
    assert "connection timed out" in str(e.value)
    assert e.value.reference == "Q-1042"


def test_any_fault_settles_as_unknown_not_only_the_sources_own_error_type():
    """A read failing with something the source layer never defined is still a
    read that failed. Catching narrowly here is precisely the bug this replaced:
    a raw transport fault escaped the adapter and reached the caller as a 500 —
    no outcome at all, for a record that may well be sitting in the books."""
    class Unforeseen(Exception):
        pass

    def boom():
        raise Unforeseen("something nobody modelled")

    with pytest.raises(SourceWriteUnknown):
        _settle(boom)


def test_an_adapter_may_keep_raising_its_own_named_outcomes():
    """Connector-specific subclasses stay catchable by name. Without this the
    helper would silently stop every existing ``except <Connector>WriteRefused``
    the moment an adapter moved onto it."""
    class BooksWriteRefused(SourceWriteRefused):
        pass

    with pytest.raises(BooksWriteRefused):
        _settle(lambda: None, refused_error=BooksWriteRefused)
