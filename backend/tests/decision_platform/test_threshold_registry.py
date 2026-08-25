"""A threshold stamp must dereference — or refuse by name.

The property these tests exist for is the one the whole feature is: **an edited
policy leaves the old stamp still resolving to the OLD values.** Everything else
here defends the ways that could quietly stop being true.

The two negative tests carry the most weight. ``resolve`` must refuse an
unrecorded stamp rather than hand back today's policy, and ``rebuild`` must
refuse when today's dataclass has grown a field the recorded policy has no value
for — both are the same §1 defect ("absence of evidence is not a pass") in
different clothing, and both would produce a number that is stamped, formatted
and confidently wrong rather than an error anybody would notice.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app import threshold_registry as registry
from app.commercial import policy
from app.commercial.config import CommercialThresholds
from app.domain import models
from app.signals.config import SignalThresholds

ORG = "org_test"
OTHER = "org_other"


@pytest.fixture(autouse=True)
def _clean_process_memory():
    """Counters and the pre-image memo are process-global; tests are not.

    The memo is *deliberately* global — it is a fact about what this process
    minted, not about any database — which makes it exactly the thing a test
    must not inherit from its neighbour. The engine-scoped persisted set needs
    no reset: it dies with the engine the ``session`` fixture builds.
    """
    registry._PRE_IMAGES.clear()
    registry._COLLISIONS.clear()
    registry._MISSING_PRE_IMAGE.clear()
    registry._POST_EPOCH_GAPS.clear()
    registry._RECORDING_FAILURES["count"] = 0
    yield
    registry._PRE_IMAGES.clear()
    registry._COLLISIONS.clear()
    registry._MISSING_PRE_IMAGE.clear()
    registry._POST_EPOCH_GAPS.clear()
    registry._RECORDING_FAILURES["count"] = 0


def _org(session, org_id: str = ORG) -> models.Organization:
    row = models.Organization(organization_id=org_id, name=org_id)
    session.add(row)
    session.flush()
    return row


def _fails_at_the_database(connection, _rows) -> None:
    """An ``_insert_ignore`` that fails the way the real one does — *in the database*.

    The distinction is the whole reason the containment tests below exist. A
    ``raise RuntimeError`` never reaches the connection, so it cannot reproduce
    what PostgreSQL actually does to a caller: abort the surrounding transaction
    and take the business write down with it two statements later. This sends a
    statement the server rejects, on the caller's own connection, which is the
    failure the recorder has to survive.
    """
    from sqlalchemy import text

    connection.execute(text(
        "INSERT INTO a_table_the_registry_does_not_have (x) VALUES (1)"))


def _metric(session, th: CommercialThresholds, *, org: str = ORG,
            customer: str = "c1", product: str = "p1") -> models.CustomerItemMetric:
    """A stamped, computed row — the thing whose version must dereference."""
    row = models.CustomerItemMetric(
        organization_id=org, customer_id=customer, product_id=product,
        thresholds_version=th.version, computed_at=datetime.now(timezone.utc))
    session.add(row)
    return row


# ── 6. the round trip, both kinds ────────────────────────────────────────────
def test_serialized_bytes_rehash_to_the_stamp_for_both_kinds():
    """``serialized`` and ``version`` must never be two answers to one question.

    If they ever drift, every recorded row fails its re-hash check in
    ``resolve`` and reads as corrupt — so this is the check that keeps the
    registry's own verification meaningful.
    """
    import hashlib

    for th, prefix, kind in ((CommercialThresholds(), "ci_", "commercial"),
                             (SignalThresholds(), "th_", "signal")):
        blob = registry.serialized(th)
        digest = hashlib.sha256(blob.encode()).hexdigest()
        assert th.version == prefix + digest[:10]
        assert registry.kind_of(th.version) == kind
        # Minting remembered the pre-image without anybody asking it to.
        assert registry.pre_image(kind, th.version) == blob
        # And the bytes are the documented serialisation, not something near it.
        assert json.loads(blob)


# ── 1. a stamp resolves to the values that produced it ───────────────────────
def test_a_stamp_resolves_to_the_values_that_produced_it(session):
    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.11, margin_floor=0.16)
    registry.record_current(session, ORG, kind="commercial", version=th.version,
                            serialized=registry.serialized(th))
    session.commit()

    resolved = registry.resolve(session, ORG, th.version)

    assert resolved.version == th.version
    assert resolved.kind == "commercial"
    assert resolved.values["min_margin"] == 0.11
    assert resolved.values["margin_floor"] == 0.16
    # And the strong form: today's dataclass reconstructs it exactly.
    assert registry.rebuild(resolved) == th


# ── the whole point: editing does not erase the old answer ───────────────────
def test_an_edited_policy_leaves_the_old_stamp_resolving_to_the_old_values(session):
    """Change the floor; old rows still say *which* floor they were judged against.

    ``commercial_policies`` is one mutable row per organization, so after this
    edit the only record of the previous floor is the registry. This is the
    sentence ``policy.py``'s module docstring used to claim and could not
    support.
    """
    _org(session)
    before = policy.save_for_org(session, ORG, {"min_margin": 0.10})
    session.commit()
    old_version = before.version

    # A row judged under that policy, written the way production writes one.
    _metric(session, before)
    session.commit()

    after = policy.save_for_org(session, ORG, {"min_margin": 0.13})
    session.commit()

    assert after.version != old_version, "an edit must move the stamp"
    # The mutable policy row now holds only the new floor…
    assert session.get(models.CommercialPolicy, ORG).overrides["min_margin"] == 0.13
    # …and the old stamp still answers with the old one.
    resolved = registry.resolve(session, ORG, old_version)
    assert resolved.values["min_margin"] == 0.10
    assert registry.resolve(session, ORG, after.version).values["min_margin"] == 0.13


# ── 4. an unrecorded stamp does not resolve to today's policy ────────────────
def test_an_unrecorded_stamp_does_not_resolve_to_todays_policy(session):
    """The refusal is the feature.

    A fallback would replay an old approval under today's floor and return it
    stamped, formatted and confidently wrong. So: the current policy must be
    perfectly loadable in the same session, and the unknown stamp must still
    raise — the failure is specific to the *stamp*, not to the tenant.
    """
    _org(session)
    current = policy.save_for_org(session, ORG, {"min_margin": 0.14})
    session.commit()

    unknown = "ci_" + "0" * 10
    with pytest.raises(registry.UnresolvedStamp) as caught:
        registry.resolve(session, ORG, unknown,
                         stamped_at=datetime.now(timezone.utc) - timedelta(days=900))

    assert caught.value.version == unknown
    assert caught.value.reason == "PRE_EPOCH"
    assert caught.value.epoch is not None
    # The message names what is missing rather than offering a value.
    assert "0.14" not in str(caught.value)
    assert "cannot be reconstructed" in str(caught.value)
    # Today's policy is loadable in the very same session — the refusal is not
    # the registry being broken or the tenant being unknown.
    assert policy.load_for_org(session, ORG).version == current.version
    assert registry.resolve(session, ORG, current.version).values["min_margin"] == 0.14


def test_a_row_stamped_after_the_epoch_and_never_recorded_is_a_named_defect(session):
    """PRE_EPOCH and POST_EPOCH_GAP are not the same news and must not read alike.

    Missing history is expected and finite. A row written *after* this
    organization began recording, carrying a stamp nothing recorded, is a bug in
    the recording path, and reporting it as history is the benign default §1
    forbids.

    The evidence is the stamped row's own date against this organization's own
    epoch — both persisted, both per-tenant. It used to be "did this process
    mint that version", which is neither: see ``_unresolved``.
    """
    _org(session)
    policy.save_for_org(session, ORG, {"min_margin": 0.10})
    session.commit()

    orphan = replace(CommercialThresholds(), min_margin=0.4321).version
    stamped = datetime.now(timezone.utc) + timedelta(minutes=1)

    with pytest.raises(registry.UnresolvedStamp) as caught:
        registry.resolve(session, ORG, orphan, stamped_at=stamped)

    assert caught.value.reason == "POST_EPOCH_GAP"
    assert "bug in the recording path" in str(caught.value)
    assert registry.coverage(session, ORG).post_epoch_gaps == 1
    assert not registry.coverage(session, ORG).healthy


def test_minting_a_variant_cannot_turn_another_orgs_history_into_a_defect(session):
    """The regression a review found, and the reason ``minted_here`` is gone.

    A stamp is a *content hash*, so two organizations running the same numbers
    share it, and ``CommercialThresholds.version`` mints on every property
    access — ``backtest.run`` computing a variant from a caller-supplied
    ``min_margin`` therefore puts a policy this book may never have run into the
    process-wide pre-image memo. Reading membership in that memo as proof the
    registry was recording turned expected pre-epoch history into a logged
    recording-path bug and flipped ``/api/health`` UNHEALTHY for the life of the
    process, for every tenant at once.
    """
    _org(session)
    policy.save_for_org(session, ORG, {"min_margin": 0.10})
    session.commit()

    # Exactly what a backtest does: mint a variant nobody necessarily ran.
    minted = replace(CommercialThresholds(), min_margin=0.1234).version

    # An undated pre-epoch row carrying that same stamp is missing history.
    with pytest.raises(registry.UnresolvedStamp) as caught:
        registry.resolve(session, ORG, minted)

    assert caught.value.reason == "PRE_EPOCH"
    assert registry.coverage(session, ORG).post_epoch_gaps == 0
    assert registry.coverage(session, ORG).healthy


def test_a_tenant_with_no_recorded_versions_says_so_rather_than_guessing(session):
    _org(session)
    _org(session, OTHER)
    policy.save_for_org(session, OTHER, {"min_margin": 0.10})
    session.commit()

    with pytest.raises(registry.UnresolvedStamp) as caught:
        registry.resolve(session, ORG, "ci_" + "a" * 10)
    assert caught.value.reason == "UNRECORDED_ORG"
    assert caught.value.epoch is None


def test_one_tenants_epoch_never_speaks_for_another(session):
    """The reason the primary key is ``(organization_id, version)``.

    Under a version-only key an organization onboarded later would inherit the
    older tenant's epoch and report its own genuine recording gaps as expected
    history.
    """
    _org(session)
    _org(session, OTHER)
    th = replace(CommercialThresholds(), min_margin=0.13)
    registry.record_current(session, OTHER, kind="commercial",
                            version=th.version,
                            serialized=registry.serialized(th))
    session.commit()

    assert registry.resolve(session, OTHER, th.version).values["min_margin"] == 0.13
    with pytest.raises(registry.UnresolvedStamp) as caught:
        registry.resolve(session, ORG, th.version)
    assert caught.value.reason == "UNRECORDED_ORG"


# ── 3. the biconditional: recorded iff committed ─────────────────────────────
def test_a_rolled_back_stamped_row_leaves_no_registry_row(session):
    """Registry row and stamped row commit together or not at all.

    The INSERT rides the flush's own connection precisely so this holds. A
    recorder on a second session would leave the registry claiming a policy for
    rows that were never written, and — the worse direction — would mark the
    triple known, so the retry that *does* commit records nothing.
    """
    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.17)
    _metric(session, th)
    session.flush()
    assert session.get(models.ThresholdVersion, (ORG, th.version)) is not None

    session.rollback()

    assert session.get(models.ThresholdVersion, (ORG, th.version)) is None
    # And the rollback did not poison the skip list: the retry records.
    _org(session)
    _metric(session, th)
    session.commit()
    assert registry.resolve(session, ORG, th.version).values["min_margin"] == 0.17


def test_a_committed_stamped_row_leaves_exactly_one_registry_row(session):
    from sqlalchemy import func, select

    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.17)
    for n in range(5):
        _metric(session, th, product=f"p{n}")
        session.commit()

    count = session.scalar(
        select(func.count()).select_from(models.ThresholdVersion).where(
            models.ThresholdVersion.organization_id == ORG))
    assert count == 1
    row = session.get(models.ThresholdVersion, (ORG, th.version))
    assert row.first_seen_via == "stamp"
    assert row.kind == "commercial"
    assert len(row.content_digest) == 64


def test_a_signal_stamp_is_recorded_as_a_signal_not_as_a_commercial_one(session):
    """``Signal.threshold_config_version`` holds both prefixes, so the kind is
    read off the stamp rather than off the column marker."""
    _org(session)
    th = replace(SignalThresholds(), margin_drop_points=0.077)
    session.add(models.Signal(
        organization_id=ORG, signal_type="CUSTOMER_DECLINE",
        subject_entity_type="CUSTOMER", subject_entity_id="c1",
        detected_at=datetime.now(timezone.utc), detector_version="v1",
        severity_base=50, threshold_config_version=th.version))
    session.commit()

    resolved = registry.resolve(session, ORG, th.version)
    assert resolved.kind == "signal"
    assert resolved.values["margin_drop_points"] == 0.077
    assert isinstance(registry.rebuild(resolved), SignalThresholds)


# ── 5. rebuild refuses when the dataclass has moved ──────────────────────────
def test_rebuild_refuses_when_the_stored_key_set_does_not_match_todays_fields(session):
    """``CommercialThresholds(**old_values)`` after a field is added is the
    forbidden substitution wearing a type annotation: the new field silently
    takes today's default and the object claims to be the historical policy."""
    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.12)
    values = json.loads(registry.serialized(th))

    # A policy recorded before today's dataclass grew a field.
    values.pop("min_margin")
    dropped = registry.ResolvedThresholds(
        organization_id=ORG, version=th.version, kind="commercial",
        values=values, first_seen_at=datetime.now(timezone.utc),
        first_seen_via="stamp")
    with pytest.raises(registry.UnrebuildableThresholds) as caught:
        registry.rebuild(dropped)
    assert "min_margin" in caught.value.missing
    assert "today's defaults" in str(caught.value)

    # And the mirror case: a field this code no longer defines.
    values["min_margin"] = 0.12
    values["a_threshold_since_removed"] = 3
    extra = replace(dropped, values=values)
    with pytest.raises(registry.UnrebuildableThresholds) as caught:
        registry.rebuild(extra)
    assert "a_threshold_since_removed" in caught.value.unexpected


def test_resolve_still_answers_a_single_value_when_rebuild_cannot(session):
    """The split that makes ``rebuild``'s strictness affordable.

    "What was the approval floor?" is a fact about recorded numbers and stays
    answerable however the dataclass evolves. Only the stronger claim — *this
    object is the policy* — is refused.
    """
    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.12)
    registry.record_current(session, ORG, kind="commercial", version=th.version,
                            serialized=registry.serialized(th))
    session.commit()

    resolved = registry.resolve(session, ORG, th.version)
    assert resolved.values["min_margin"] == 0.12


# ── verification: a row that lies is not a gap ───────────────────────────────
def test_a_row_whose_values_do_not_hash_to_its_stamp_is_refused(session):
    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.12)
    registry.record_current(session, ORG, kind="commercial", version=th.version,
                            serialized=registry.serialized(th))
    session.commit()

    tampered = json.loads(registry.serialized(th))
    tampered["min_margin"] = 0.50
    row = session.get(models.ThresholdVersion, (ORG, th.version))
    row.values_json = json.dumps(tampered, sort_keys=True)
    session.commit()

    with pytest.raises(registry.CorruptThresholdRecord) as caught:
        registry.resolve(session, ORG, th.version)
    assert "content_digest" in str(caught.value)


# ── 7. collision detection ───────────────────────────────────────────────────
def test_remember_reports_a_collision_and_keeps_the_first_pre_image(caplog):
    """Ten hex characters is 40 bits, so a collision is possible rather than
    theoretical. Detected where it happens, not inferred later from a puzzling
    number — and the first pre-image wins, because overwriting would quietly
    change what every already-stamped row means."""
    import logging

    version = "ci_deadbeef01"
    registry.remember("commercial", version, '{"min_margin": 0.1}')
    with caplog.at_level(logging.ERROR):
        registry.remember("commercial", version, '{"min_margin": 0.9}')

    assert registry.pre_image("commercial", version) == '{"min_margin": 0.1}'
    assert "collision" in caplog.text
    assert sum(registry._COLLISIONS.values()) == 1

    # Re-remembering the same bytes is not a collision.
    registry.remember("commercial", version, '{"min_margin": 0.1}')
    assert sum(registry._COLLISIONS.values()) == 1


# ── the boot recorder ────────────────────────────────────────────────────────
def test_the_boot_backfill_makes_todays_version_resolvable(session):
    """The recorder that closes the environment-only gap.

    Move ``CI_RECENT_DAYS`` and every stamp in the platform changes while
    nothing may be *stamped* for days. This runs at the deploy that made it.
    """
    _org(session)
    registry.record_current_policies(session, ORG)
    session.commit()

    commercial = policy.load_for_org(session, ORG)
    from app.signals.engine import thresholds_for_org

    signal = thresholds_for_org(session, ORG)

    assert registry.resolve(session, ORG, commercial.version).kind == "commercial"
    assert registry.resolve(session, ORG, signal.version).kind == "signal"
    row = session.get(models.ThresholdVersion, (ORG, commercial.version))
    assert row.first_seen_via == "boot"

    report = registry.coverage(session, ORG)
    assert report.recorded["commercial"] == 1
    assert report.epochs["signal"] is not None
    assert report.healthy


def test_the_boot_backfill_is_idempotent_and_keeps_the_first_sighting(session):
    _org(session)
    registry.record_current_policies(session, ORG)
    session.commit()
    version = policy.load_for_org(session, ORG).version
    first_seen = session.get(models.ThresholdVersion, (ORG, version)).first_seen_at

    registry.record_current_policies(session, ORG)
    session.commit()

    assert session.get(models.ThresholdVersion, (ORG, version)).first_seen_at == first_seen


# ── the recorder is on by default, everywhere ────────────────────────────────
def test_the_flush_recorder_is_installed_at_import():
    """Registered on the ``Session`` class, so tests, bootstrap and scripts are
    covered without opting in. If this ever fails, every row stamped from then
    on is permanently unexplainable and nothing else would say so."""
    assert registry.installed()
    registry.install()          # idempotent
    assert registry.installed()


def test_a_stamp_with_no_pre_image_records_nothing_and_says_so(session, caplog):
    """Never filled in from today's policy. A row is stamped and not yet
    dereferenceable, which is honest; a plausible current policy served as the
    historical one is the single worst outcome available here."""
    import logging

    _org(session)
    carried_in = "ci_" + "b" * 10       # e.g. restored from another deployment
    with caplog.at_level(logging.WARNING):
        session.add(models.CustomerItemMetric(
            organization_id=ORG, customer_id="c1", product_id="p1",
            thresholds_version=carried_in,
            computed_at=datetime.now(timezone.utc)))
        session.commit()

    assert session.get(models.ThresholdVersion, (ORG, carried_in)) is None
    assert "never minted" in caplog.text
    assert registry._MISSING_PRE_IMAGE.get(ORG) == 1


def test_recording_never_fails_the_business_write(session, monkeypatch):
    """Bookkeeping that can fail a quote save has inverted the priority.

    The failure is sent to the database on purpose. This test used to
    monkeypatch ``_insert_ignore`` with a pure-Python ``raise``, which never
    touches the connection — so it passed green on both dialects while the path
    it claimed to cover was broken on the one production runs: on PostgreSQL a
    failed statement aborts the whole transaction, and the swallowed error was
    followed by the metric INSERT itself dying with ``InFailedSqlTransaction``.
    Every stamped write in a deploy-before-migrate window failed outright. A
    ``try/except`` around a statement that has already spoken to the database is
    not containment; a SAVEPOINT is, and only a real failure can tell them
    apart.
    """
    _org(session)
    monkeypatch.setattr(registry, "_insert_ignore", _fails_at_the_database)
    th = replace(CommercialThresholds(), min_margin=0.17)
    _metric(session, th)
    session.commit()

    from sqlalchemy import func, select

    assert session.scalar(
        select(func.count()).select_from(models.CustomerItemMetric).where(
            models.CustomerItemMetric.organization_id == ORG)) == 1
    # And the row is stamped with a version nothing recorded, which is the
    # honest half of this outcome — so it must be *counted*, not merely logged.
    assert session.get(models.ThresholdVersion, (ORG, th.version)) is None
    assert registry._RECORDING_FAILURES["count"] >= 1


def test_a_failed_recording_write_is_counted_rather_than_only_logged(session,
                                                                     monkeypatch):
    """A swallowed failure that increments nothing is a pass dressed as health.

    ``check_threshold_registry`` reads counters and nothing else, so before this
    counter existed it answered HEALTHY with "Threshold versions recorded as
    stamped" while every INSERT the recorder attempted had failed and the rows
    that committed meanwhile were permanently unexplainable. §1: absence of
    evidence is not a pass — least of all when the evidence is absent because
    the code chose not to look.
    """
    _org(session)
    monkeypatch.setattr(registry, "_insert_ignore", _fails_at_the_database)
    _metric(session, replace(CommercialThresholds(), min_margin=0.17))
    session.commit()

    report = registry.coverage(session, ORG)
    assert report.process_recording_failures >= 1
    assert not report.healthy
    assert report.to_dict()["process_recording_failures"] >= 1


def test_a_policy_edit_survives_a_registry_that_cannot_be_written(session,
                                                                 monkeypatch):
    """An owner must still be able to move the margin floor.

    ``save_for_org`` records the new version's pre-image before anything is
    stamped with it, and that call had no containment at all: with
    ``threshold_versions`` unwritable — the window between deploying this code
    and running ``d1thrv``, a permission error — PATCH /api/v1/admin/policy
    returned 500 and the floor could not be edited. The bookkeeping that exists
    to explain a policy must never be the reason the policy cannot change.
    """
    _org(session)
    monkeypatch.setattr(registry, "_insert_ignore", _fails_at_the_database)

    saved = policy.save_for_org(session, ORG, {"min_margin": 0.13})
    session.commit()

    assert saved.min_margin == 0.13
    assert policy.load_for_org(session, ORG).min_margin == 0.13
    assert registry._RECORDING_FAILURES["count"] >= 1


def test_an_ordinary_update_to_an_already_recorded_row_is_not_a_gap(session,
                                                                   caplog):
    """"This process never minted it" is not evidence that nothing recorded it.

    Approving an ``ApprovalRequest`` raised before the last policy edit, an
    ``OutcomeSnapshot`` copying a signal's stamp verbatim, a detector reusing
    the stamp on the row it flagged — all ordinary, all carrying a version this
    process did not mint, and all previously logged as unexplainable and
    counted into the health check, which then reported DEGRADED for the life of
    the process. §6: a check that is always red is a check nobody reads, and
    this one is the only instrument that reports a genuine POST_EPOCH_GAP.
    """
    import logging

    _org(session)
    th = replace(CommercialThresholds(), min_margin=0.17)
    row = _metric(session, th)
    session.commit()
    assert session.get(models.ThresholdVersion, (ORG, th.version)) is not None

    # A second process: it minted nothing and knows nothing was persisted.
    registry._PRE_IMAGES.clear()
    registry._persisted(session).clear()

    with caplog.at_level(logging.WARNING):
        row.transaction_count = (row.transaction_count or 0) + 1
        session.commit()

    assert registry._MISSING_PRE_IMAGE.get(ORG, 0) == 0
    assert "never minted" not in caplog.text
    assert registry.coverage(session, ORG).healthy


def test_one_tenants_gap_is_not_reported_as_another_tenants(session):
    """``CoverageReport`` carried an organization_id and four process-wide
    counters, so a gap raised while resolving org B's stamps made
    ``coverage(session, org_a).healthy`` False and told org A it had a defect in
    its recording path. Two of the four know whose stamp they were about and are
    keyed by it now; the two that cannot — a content-hash collision, a failed
    INSERT — are named for the process in the payload instead of implying a
    tenant.
    """
    _org(session)
    _org(session, OTHER)
    policy.save_for_org(session, ORG, {"min_margin": 0.10})
    policy.save_for_org(session, OTHER, {"min_margin": 0.10})
    session.commit()

    orphan = replace(CommercialThresholds(), min_margin=0.4321).version
    with pytest.raises(registry.UnresolvedStamp):
        registry.resolve(session, OTHER, orphan,
                         stamped_at=datetime.now(timezone.utc) + timedelta(minutes=1))

    assert registry.coverage(session, OTHER).post_epoch_gaps == 1
    assert not registry.coverage(session, OTHER).healthy
    # The book that did nothing wrong.
    assert registry.coverage(session, ORG).post_epoch_gaps == 0
    assert registry.coverage(session, ORG).healthy


def test_the_report_says_which_counts_are_the_processs_and_which_are_the_books(
        session):
    """Scope in the name, because the flat shape was what invited the mistake."""
    _org(session)
    policy.save_for_org(session, ORG, {"min_margin": 0.10})
    session.commit()

    keys = set(registry.coverage(session, ORG).to_dict())

    assert {"post_epoch_gaps", "stamps_without_a_pre_image"} <= keys
    assert {"process_collisions", "process_recording_failures"} <= keys
    # The names that could be read either way are gone.
    assert "collisions" not in keys and "recording_failures" not in keys
