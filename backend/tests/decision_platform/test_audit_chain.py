"""The audit chain: linked, ordered, verifiable, and impossible to fork.

The properties here are the ones that make the log worth having. A row somebody
can edit is not evidence; a chain nobody can check is decoration; and an entry
that commits while the change it describes rolls back is a false record, which
is worse than a missing one.

The concurrency half — that two appenders cannot both take a position — needs
real threads against a real file and lives in
``test_audit_chain_concurrency.py``. None of it reproduces in-memory.
"""
from __future__ import annotations

from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.domain import models
from app.trust import audit, signing
from tests.dbsupport import fresh_engine

ORG = "org_pie"
OTHER = "org_other"


@pytest.fixture()
def session():
    engine = fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = maker()
    yield s
    s.close()
    if not getattr(engine.dialect, "name", "") == "postgresql":
        engine.dispose()


def _append(s, org=ORG, action=audit.POLICY_CHANGED, **kw):
    row = audit.append(s, organization_id=org, action=action, **kw)
    s.commit()
    return row


# ── the chain ────────────────────────────────────────────────────────────────
def test_the_first_entry_starts_from_genesis(session):
    row = _append(session)
    assert row.seq == 1
    assert row.prev_hash == audit.GENESIS
    assert len(row.entry_hash) == 64


def test_each_entry_names_the_hash_of_the_one_before_it(session):
    rows = [_append(session, detail={"n": n}) for n in range(5)]
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    for earlier, later in zip(rows, rows[1:]):
        assert later.prev_hash == earlier.entry_hash


def test_a_chain_verifies_when_nothing_has_been_touched(session):
    for n in range(4):
        _append(session, detail={"n": n})
    report = audit.verify(session, ORG)
    assert report["ok"] is True
    assert report["entries"] == 4
    assert report["first_break"] is None


def test_two_organizations_keep_separate_chains(session):
    """Scope is the tenant, so one org's writes never take another's position."""
    a1 = _append(session, org=ORG)
    b1 = _append(session, org=OTHER)
    a2 = _append(session, org=ORG)

    assert (a1.seq, a2.seq) == (1, 2)
    assert b1.seq == 1 and b1.prev_hash == audit.GENESIS
    assert audit.verify(session, ORG)["ok"]
    assert audit.verify(session, OTHER)["ok"]


def test_an_empty_chain_verifies_but_says_it_is_empty(session):
    """§1: absence of evidence is not a pass, so the count is reported beside
    the verdict. "Nothing is wrong" and "nothing happened" must be tellable
    apart by the caller, because this function cannot tell them apart itself."""
    report = audit.verify(session, ORG)
    assert report["ok"] is True
    assert report["entries"] == 0
    assert report["head_hash"] == audit.GENESIS


# ── tampering ────────────────────────────────────────────────────────────────
def test_editing_a_stored_entry_is_detected(session):
    """The whole point. Rewrite what an entry says and the chain refuses it."""
    for n in range(5):
        _append(session, detail={"n": n})

    victim = session.scalars(
        select(models.AuditEntry)
        .where(models.AuditEntry.organization_id == ORG,
               models.AuditEntry.seq == 3)).first()
    victim.detail = {"n": 3, "and": "something nobody wrote"}
    session.commit()

    report = audit.verify(session, ORG)
    assert report["ok"] is False
    assert report["first_break"]["kind"] == "SIGNATURE"
    assert report["first_break"]["seq"] == 3


def test_changing_who_acted_is_detected(session):
    """The field an attacker actually wants to change."""
    _append(session, actor_user_id="u_real", actor_label="real@example.com")
    row = audit.chain(session, ORG)[0]
    row.actor_label = "someone.else@example.com"
    session.commit()

    assert audit.verify(session, ORG)["first_break"]["kind"] == "SIGNATURE"


def test_removing_an_entry_from_the_middle_is_detected(session):
    """Deletion is the other half of tampering, and it breaks a different way:
    the signatures still check, so only the *links* can object."""
    for n in range(5):
        _append(session, detail={"n": n})
    session.delete(audit.chain(session, ORG)[2])
    session.commit()

    report = audit.verify(session, ORG)
    assert report["ok"] is False
    # The row that was seq 4 is now in position 3, so the sequence objects
    # first — which is the earliest honest place to point at the damage.
    assert report["first_break"]["kind"] == "SEQUENCE"


def test_a_re_signed_forgery_still_needs_the_key(session):
    """Tamper-evidence is only as good as the key not being in the database.

    Re-hashing an edited row with the *right* key would pass — that is what
    holding the secret means. This asserts the other half: the shape of the
    hash is not enough, so an attacker who can write rows but cannot sign them
    is caught.
    """
    _append(session, detail={"n": 1})
    row = audit.chain(session, ORG)[0]
    row.detail = {"n": 999}
    row.entry_hash = "f" * 64          # right shape, wrong signature
    session.commit()

    assert audit.verify(session, ORG)["first_break"]["kind"] == "SIGNATURE"


def test_re_parenting_an_entry_onto_a_used_parent_is_refused_by_the_database(session):
    """A fork is two entries naming one predecessor, and the constraint says so.

    ``uq_audit_entry_org_prev`` states the property directly rather than as a
    consequence of the sequence numbering, and this is what that buys: the
    write does not merely become *detectable*, it does not land. The chain
    cannot be forked by an UPDATE any more than by an INSERT.
    """
    _append(session, detail={"n": 1})
    _append(session, detail={"n": 2})
    second = audit.chain(session, ORG)[1]
    second.prev_hash = audit.GENESIS          # already the first entry's parent

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert audit.verify(session, ORG)["ok"] is True


def test_the_signature_covers_the_link_not_just_the_content(session):
    """Re-pointing an entry at a parent nothing else claims is still detected.

    The constraint above cannot object here — no two rows share a predecessor —
    so this is the case only the signature catches. Without ``prev_hash`` inside
    the signed body, entries could be re-parented while every individual
    signature still checked, and a chain whose links are unsigned is a list.
    """
    _append(session, detail={"n": 1})
    _append(session, detail={"n": 2})
    second = audit.chain(session, ORG)[1]
    second.prev_hash = "9" * 64               # a parent that does not exist
    session.commit()

    report = audit.verify(session, ORG)
    assert report["ok"] is False
    assert report["first_break"]["kind"] in ("LINK", "SIGNATURE")
    assert report["first_break"]["seq"] == 2


# ── the covered body ─────────────────────────────────────────────────────────
def test_the_body_is_read_from_the_row_not_rebuilt(session):
    """``erasure.receipt_body``'s rule: what a signature covers is a fact about
    the moment it was made."""
    row = _append(session, detail={"a": 1}, thresholds_version="ci_abc123")
    body = audit.covered_body(row)
    assert signing.matches(body, row.entry_hash)
    assert "entry_id" not in body, "the row's identity is not part of what it says"
    assert body["thresholds_version"] == "ci_abc123"


def test_canonicalisation_does_not_depend_on_key_insertion_order(session):
    """The reason there is one signing module and not two."""
    assert signing.sign({"a": 1, "b": 2}) == signing.sign({"b": 2, "a": 1})


# ── export ───────────────────────────────────────────────────────────────────
def test_the_json_export_is_re_verifiable_by_the_recipient(session):
    for n in range(3):
        _append(session, detail={"n": n})
    dump = audit.export_json(session, ORG)

    assert dump["verification"]["ok"] is True
    assert len(dump["entries"]) == 3
    assert "HMAC-SHA256" in dump["method"]
    # A third party with the key recomputes every hash from the file alone.
    for entry in dump["entries"]:
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        assert signing.matches(body, entry["entry_hash"])
    # …and re-walks the links without asking us.
    prev = audit.GENESIS
    for entry in dump["entries"]:
        assert entry["prev_hash"] == prev
        prev = entry["entry_hash"]


def test_the_csv_export_carries_every_signed_field(session):
    import csv
    import io

    _append(session, detail={"n": 1}, actor_user_id="u1",
            actor_label="a@example.com", actor_role="OWNER")
    rows = list(csv.DictReader(io.StringIO(audit.export_csv(session, ORG))))
    assert len(rows) == 1
    assert rows[0]["actor_label"] == "a@example.com"
    assert rows[0]["entry_hash"] and rows[0]["prev_hash"] == audit.GENESIS
    # The detail cell is the exact canonical substring the signature covers,
    # so a spreadsheet copy can still be checked.
    assert rows[0]["detail"] == '{"n":1}'


# ── atomicity with the change being recorded ────────────────────────────────
def test_an_entry_rolled_back_with_its_change_leaves_nothing_behind(session):
    """``append`` does not commit, which is what makes the pairing true.

    An audit entry that survived a rolled-back transaction would claim
    something happened that did not — the mirror image of the missing-entry
    failure and just as false.
    """
    audit.append(session, organization_id=ORG, action=audit.POLICY_CHANGED)
    session.rollback()
    assert audit.verify(session, ORG)["entries"] == 0


def test_the_chain_survives_a_gap_free_after_a_rollback(session):
    """A lost transaction must not burn a position and leave a hole."""
    _append(session)
    audit.append(session, organization_id=ORG, action=audit.LOGIN_FAILED)
    session.rollback()
    _append(session)
    assert [r.seq for r in audit.chain(session, ORG)] == [1, 2]
    assert audit.verify(session, ORG)["ok"]


# ── the actor ────────────────────────────────────────────────────────────────
def test_the_actor_label_is_never_blank(session):
    """``IdentityEvent.actor``'s rule: "who did this" is the first question."""
    row = _append(session)
    assert row.actor_label == audit.SYSTEM


def test_a_user_id_that_no_longer_resolves_does_not_become_the_system(session):
    """A deleted account is still not the system. Saying SYSTEM would be a
    false record rather than a missing one."""
    row = _append(session, actor_user_id="u_gone")
    assert row.actor_label == "u_gone"
    assert row.actor_label != audit.SYSTEM


# ── truncation: the half a hash chain does not cover on its own ──────────────
def test_deleting_the_newest_entries_is_detected(session):
    """The finding that made this table necessary.

    A chain covers modification and insertion by construction: edit a row and
    its signature breaks, insert one and the sequence does. **Deleting from the
    tail breaks nothing** — what remains is a shorter chain that is internally
    perfect. Two reviewers proved it independently on a real database: build
    five entries, DELETE the newest two, and `verify` answered `ok: True`.

    That is the half an attacker uses, because the incriminating rows are at
    the end. `audit_chain_heads` records where the chain is supposed to stop.
    """
    rows = [_append(session, detail={"n": n}) for n in range(5)]
    assert audit.verify(session, ORG)["ok"] is True

    session.query(models.AuditEntry).filter(
        models.AuditEntry.entry_id.in_([rows[3].entry_id, rows[4].entry_id])
    ).delete(synchronize_session=False)
    session.commit()

    verdict = audit.verify(session, ORG)
    assert verdict["ok"] is False, "a shortened chain must not read as clean"
    assert verdict["first_break"]["kind"] == "TRUNCATED"
    assert verdict["entries"] == 3
    assert verdict["first_break"]["seq"] == 5, "the anchor still names the real end"


def test_deleting_the_whole_chain_is_detected(session):
    """The extreme of the same case, and it used to be the quietest: with every
    row gone `verify` walked nothing, found nothing wrong, and said so."""
    for n in range(3):
        _append(session, detail={"n": n})
    session.query(models.AuditEntry).delete(synchronize_session=False)
    session.commit()

    verdict = audit.verify(session, ORG)
    assert verdict["ok"] is False
    assert verdict["first_break"]["kind"] == "TRUNCATED"
    assert verdict["entries"] == 0


def test_removing_the_anchor_is_itself_detected(session):
    """Truncation now needs a consistent edit of two tables. Doing half of it —
    dropping the anchor to silence the mismatch — is its own finding."""
    for n in range(3):
        _append(session, detail={"n": n})
    session.query(models.AuditChainHead).delete(synchronize_session=False)
    session.commit()

    verdict = audit.verify(session, ORG)
    assert verdict["ok"] is False
    assert verdict["first_break"]["kind"] == "TRUNCATED"


def test_the_anchor_tracks_the_head_as_the_chain_grows(session):
    rows = [_append(session, detail={"n": n}) for n in range(4)]
    anchor = session.get(models.AuditChainHead, ORG)
    assert anchor.seq == 4
    assert anchor.entry_hash == rows[-1].entry_hash


def test_two_tenants_keep_separate_anchors(session):
    _append(session, org=ORG)
    _append(session, org=ORG)
    _append(session, org=OTHER)
    assert session.get(models.AuditChainHead, ORG).seq == 2
    assert session.get(models.AuditChainHead, OTHER).seq == 1
    assert audit.verify(session, ORG)["ok"] is True
    assert audit.verify(session, OTHER)["ok"] is True


# ── the signature must not depend on how a driver renders a timestamp ────────
def test_the_signature_survives_a_non_utc_rendering_of_its_timestamp(session):
    """`covered_body` normalises `at` to UTC before signing.

    Signing happens over `clock.now()`, always `+00:00`. Verification happens
    over a value read back from the database — and psycopg renders a
    `timestamptz` in the *session* TimeZone, which follows the server's. On a
    Postgres whose TimeZone is Asia/Kolkata every entry would read back as
    `...+05:30`, hash differently, and report SIGNATURE on a chain nobody had
    touched: the entire log failing closed because of a server setting.

    The instant is what is signed, never its rendering.
    """
    from datetime import timezone as _tz

    row = _append(session)
    covered = audit.covered_body(row)

    shifted = type(row)(
        organization_id=row.organization_id, seq=row.seq,
        prev_hash=row.prev_hash, entry_hash=row.entry_hash,
        at=row.at.astimezone(_tz(timedelta(hours=5, minutes=30))),
        action=row.action, actor_user_id=row.actor_user_id,
        actor_label=row.actor_label, actor_role=row.actor_role,
        subject_type=row.subject_type, subject_id=row.subject_id,
        detail=row.detail, thresholds_version=row.thresholds_version,
        source=row.source)

    assert audit.covered_body(shifted) == covered, (
        "the same instant rendered in another zone must sign identically")
    assert audit.signing.matches(audit.covered_body(shifted), row.entry_hash)
