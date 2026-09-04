"""Stage D: a price list decoded by a decoder built for it, and one namespace.

Stages A–C made a decoder that can be inferred from a file, frozen and run.
This is where it becomes a decode path a build actually takes — and where the
question the packs used to answer by accident has to be answered on purpose:
**what numbering authority are a company's identifiers unique within, when
there is no pack id to borrow?**

Three things are pinned.

**Two paths, and a config names exactly one.** A file whose config holds a
confirmed decoder is decoded by ``app.decoding``; a file whose config names a
shipped rule set is decoded by pie-parser, as before. Neither is a fallback for
the other, both together is refused, and neither is a file that is not decoded —
which a build reports by name.

**One namespace per company, whichever path decoded the file.** ``org_id`` is
stamped at the merge with the connection's own id, because that is what
``record_namespace`` means — the distributor's system issued ``record_id``, and
it is unique inside that system and nowhere else. Two namespaces inside one
company would turn a material number the merge should resolve into a key
present in two spaces, which the index answers as an abstention rather than as
the newest record.

**A catalogue number two manufacturers share is named, not resolved.** The
index namespaces the manufacturer's number by the same single field as the
distributor's, so inside one company they land in one space. That cannot be
fixed from this side: the record carries one namespace and ``record_id`` is the
identifier that must have the company's. So both records are kept and the
repeat is counted and named — losing a product over its secondary identifier
would be the worse answer.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import catalog
from app.db import get_session
from app.decoding import DecoderError, apply_bindings, bind, evidence, freeze, infer
from app.domain import models
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

requires_pie = pytest.mark.requires_pie

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: A price list phrased in a way no shipped pack has ever seen: the point of
#: the decoder path is that a file nothing was written for can still be read.
ROWS = [(f"AC-{700 + n}", f"ACME BORING BAR {n},5mm 4xD", "AK15", 1200 + n)
        for n in range(1, 30)]


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id="cx_sls", organization_id="org_pie",
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.add(models.CompanyCatalogue(organization_id="org_pie", connection_id="cx_sls",
                                  catalogue_key="default", name=""))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(data_status.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email="s.menon@pie.example"):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _xlsx(rows, headers=("Part No", "Item Description", "Grade", "Price")) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _upload(c, hdr, rows, filename="acme.xlsx", source_key=None, **kw):
    url = (f"/api/v1/data/catalog/companies/cx_sls/catalogues/default/corpus"
           f"?filename={filename}")
    if source_key is not None:
        url += f"&source_key={source_key}"
    return c.post(url, content=_xlsx(rows, **kw),
                  headers={**hdr, "Content-Type": XLSX})


def _cat(body):
    return next(x for x in body["catalogues"] if x["catalogue_key"] == "default")


def _sources(body):
    return {s["source_key"]: s for s in _cat(body)["sources"]}


def _newest(body):
    return _cat(body)["sources"][-1]


def _url(source_key, tail):
    return (f"/api/v1/data/catalog/companies/cx_sls/catalogues/default"
            f"/sources/{source_key}/{tail}")


def _save(c, hdr, source, **extra):
    columns = source["decoding"]["columns"]
    return c.put(_url(source["source_key"], "decoding"),
                 json={"record_id": columns["record_id"],
                       "description": columns["description"],
                       "grade": columns.get("grade"), **extra},
                 headers=hdr)


def _decoder_for(descriptions):
    """A decoder for these descriptions, with the bindings the text settles —
    Stages B and C, as the propose endpoint runs them."""
    proposal = infer.propose(descriptions)
    assert proposal.decoder is not None, proposal.reason
    suggestions = bind.suggest(evidence.gather(proposal.decoder, descriptions))
    return apply_bindings(proposal.decoder, [
        {"segment": s.segment, "group": s.group, "slot": s.slot, "type": s.type}
        for s in suggestions if s.slot])


# ── which path a config names ──────────────────────────────────────────────

def test_a_config_names_one_decode_path_or_neither(client):
    """The two are alternatives, not a preference and a fallback. A config
    naming both would make which of them read a row a question about
    evaluation order, and nothing on the row would answer it."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    decoder = _decoder_for([r[1] for r in ROWS])

    both = _save(client, hdr, source, rule_set="zcnc",
                 decoder=decoder.to_dict())
    assert both.status_code == 400
    assert "one or the other" in both.json()["detail"]


def test_a_config_naming_neither_is_saved_and_the_build_says_which_file(client):
    """The honest state for a file nothing reads yet. The columns are on
    record, the file is not decoded, and the refusal names it."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    body = _save(client, hdr, source).json()
    saved = _sources(body)[source["source_key"]]["decoding"]
    assert saved["confirmed_at"] is not None
    assert saved["path"] is None
    assert saved["ready"] is False

    built = client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/"
                        "default/build", headers=hdr)
    assert built.status_code == 409
    assert "acme.xlsx" in built.json()["detail"]
    assert "no saved decoding config" in built.json()["detail"]


def test_a_confirmed_decoder_makes_the_file_ready_by_the_decoder_path(client):
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    decoder = _decoder_for([r[1] for r in ROWS])
    body = _save(client, hdr, source, decoder=decoder.to_dict()).json()
    saved = _sources(body)[source["source_key"]]["decoding"]
    assert saved["path"] == catalog.DECODE_DECODER
    assert saved["ready"] is True
    assert saved["decoder_id"] == decoder.decoder_id
    assert saved["rule_set"] is None


def test_a_review_is_saved_as_bindings_over_a_proposed_artifact(client):
    """The patterns come from a proposal this deployment produced and can
    verify; only the answers come from the caller. A binding names an
    attribute and the review exists to catch a wrong one, but a pattern is a
    regular expression that runs over every row of every rebuild."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    proposed = client.post(_url(source["source_key"], "propose-decoder"),
                           headers=hdr).json()["proposal"]
    segment = proposed["decoder"]["segments"][0]["id"]

    body = _save(client, hdr, source, decoder=proposed["decoder"],
                 bindings=[{"segment": segment, "group": "num1",
                            "slot": "cutting_dia_mm", "type": "number"}],
                 decimal="comma").json()
    saved = _sources(body)[source["source_key"]]["decoding"]
    assert saved["ready"] is True
    # A new artifact: the same patterns under a different convention decode
    # the same rows to different numbers.
    assert saved["decoder_id"] != proposed["decoder_id"]
    assert saved["decoder"]["decimal"] == "comma"
    assert saved["decoder"]["decoder_id"] == saved["decoder_id"]
    assert saved["decoder"]["segments"][0]["fields"] == [
        {"group": "num1", "slot": "cutting_dia_mm", "type": "number"}]
    # And the pattern is the proposal's, unchanged.
    assert (saved["decoder"]["segments"][0]["pattern"]
            == proposed["decoder"]["segments"][0]["pattern"])


def test_an_artifact_whose_id_stopped_matching_is_not_a_proposal(client):
    """So a decoder cannot be hand-written into the config: the only artifacts
    that pass are ones a proposal produced, and a proposal's own id is the
    evidence of that."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    payload = _decoder_for([r[1] for r in ROWS]).to_dict()
    # A segment's label is free text and affects no matching, so this passes
    # every structural check in `freeze` and is caught only by the id.
    payload["segments"][0]["label"] = "boring bars"
    r = _save(client, hdr, source, decoder=payload)
    assert r.status_code == 400
    assert "has been changed since it was frozen" in r.json()["detail"]


def test_a_binding_the_file_cannot_support_is_refused(client):
    """``apply_bindings`` re-freezes, so every check runs: a group the segment
    does not declare, a slot the vocabulary does not know, two bindings on one
    slot. A config that cannot decode is never stored."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    proposed = client.post(_url(source["source_key"], "propose-decoder"),
                           headers=hdr).json()["proposal"]
    segment = proposed["decoder"]["segments"][0]["id"]
    for bad in ({"segment": segment, "group": "num99", "slot": "loc_mm",
                 "type": "number"},
                {"segment": segment, "group": "num1", "slot": "cutting_diameter",
                 "type": "number"},
                {"segment": "nope", "group": "num1", "slot": "loc_mm",
                 "type": "number"}):
        r = _save(client, hdr, source, decoder=proposed["decoder"],
                  bindings=[bad])
        assert r.status_code == 400, bad
        assert "cannot be stored" in r.json()["detail"]


def test_a_stored_artifact_that_stopped_matching_its_id_decodes_nothing(client,
                                                                        tmp_path):
    """The check that *is* meaningful: an artifact edited in the database. It
    carries its own id, so this needs nothing from the row — and the row's
    denormalised id is cross-checked too, which catches a whole artifact
    swapped for another valid one."""
    hdr = _hdr(client)
    source_key = _newest(_upload(client, hdr, ROWS).json())["source_key"]
    payload = _decoder_for([r[1] for r in ROWS]).to_dict()
    edited = {**payload, "decimal": "comma"}          # id now describes nothing
    with client.Maker() as s:
        row = next(r for r in s.query(models.CompanyCorpus).all()
                   if r.source_key == source_key)
        with pytest.raises(DecoderError) as caught:
            catalog.run_decoder(row, edited, tmp_path / "out.jsonl")
        assert "has been changed since it was frozen" in str(caught.value)

        # And a different, perfectly valid artifact under the stored id.
        other = _decoder_for([f"OTHER TOOL {n}mm" for n in range(3, 30)])
        with pytest.raises(catalog.CatalogueError) as refused:
            catalog.run_decoder(row, other.to_dict(), tmp_path / "out.jsonl",
                                expect_id=payload["decoder_id"])
        assert "the two disagree" in str(refused.value).lower()


def test_a_decoder_that_never_froze_is_refused(client):
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    r = _save(client, hdr, source,
              decoder={"schema_version": 1, "decimal": "dot", "segments": []})
    assert r.status_code == 400
    assert "cannot be stored" in r.json()["detail"]


# ── the propose endpoint ───────────────────────────────────────────────────

def test_propose_reads_the_file_and_saves_nothing(client):
    """Proposes and returns. A proposal can be asked for twice and compared
    without changing what the file currently decodes through — the same "show,
    validate, save, then decode" flow the rule-set half has."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    r = client.post(_url(source["source_key"], "propose-decoder"), headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["proposal"]["decoder"] is not None
    assert body["review"]["suggestions"]
    # Nothing was written.
    with client.Maker() as s:
        row = s.get(models.CompanyCorpus, source["corpus_id"])
        assert row.decoder is None and row.decoder_id is None
        assert row.decoding_confirmed_at is None
    # And it is reproducible: the same file proposes the same decoder.
    again = client.post(_url(source["source_key"], "propose-decoder"), headers=hdr)
    assert (again.json()["proposal"]["decoder_id"]
            == body["proposal"]["decoder_id"])


def test_a_proposal_can_be_saved_as_it_came_back(client):
    """The round trip the screen makes: propose, then save the artifact the
    review returned. It must freeze on the way in, so a proposal that could
    not be stored would be a proposal nobody could act on."""
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    proposed = client.post(_url(source["source_key"], "propose-decoder"),
                           headers=hdr).json()
    body = _save(client, hdr, source,
                 decoder=proposed["proposal"]["decoder"]).json()
    saved = _sources(body)[source["source_key"]]["decoding"]
    assert saved["ready"] is True
    assert saved["decoder_id"] == proposed["proposal"]["decoder_id"]


def test_propose_refuses_a_file_whose_description_column_is_empty(client):
    hdr = _hdr(client)
    rows = [(f"AC-{n}", "", "AK15", 10) for n in range(1, 12)]
    source = _newest(_upload(client, hdr, rows, filename="blank.xlsx").json())
    r = client.post(_url(source["source_key"], "propose-decoder"), headers=hdr)
    assert r.status_code == 422
    assert "no descriptions" in r.json()["detail"]


# ── the build, through the decoder path ────────────────────────────────────

def test_a_build_decodes_through_the_files_own_decoder(client, tmp_path,
                                                       monkeypatch):
    """End to end with no shipped grammar involved: upload a file phrased in a
    way no pack has seen, infer a decoder for it, save it, build. The records
    carry the decoder's id and the slots its bindings named."""
    monkeypatch.setattr(catalog.settings, "PIE_CATALOG",
                        tmp_path / "products.jsonl")
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    decoder = _decoder_for([r[1] for r in ROWS])
    _save(client, hdr, source, decoder=decoder.to_dict())

    built = client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/"
                        "default/build", headers=hdr)
    assert built.status_code == 200, built.text
    cat = _cat(built.json())
    assert cat["records"] == len(ROWS)
    assert cat["built_from"][0]["decoded_by"] == catalog.DECODE_DECODER
    assert cat["built_from"][0]["decoder_id"] == decoder.decoder_id

    records = [json.loads(line) for line in
               catalog.company_catalog_path("cx_sls", "default")
               .read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == len(ROWS)
    assert {r["decoder_id"] for r in records} == {decoder.decoder_id}
    assert all(r["depth_ratio_xd"] == 4 for r in records)
    # The grade column is carried through, unparsed: decoding a grade *code*
    # into its system and class is a second grammar this path does not have.
    assert {r["grade"] for r in records} == {"AK15"}
    # And no price reached it, from a path that never saw the pack's profile.
    # Asserted on the *values*, not on the file's text: a four-digit price
    # turns up inside a sha256 by chance often enough that a substring check
    # here failed on the first run and would have failed silently the other
    # way just as easily.
    prices = {str(r[3]) for r in ROWS} | {float(r[3]) for r in ROWS}
    for record in records:
        assert not prices & set(map(str, record.values()))
        assert not prices & set(record.values())


def test_a_rebuild_through_the_same_decoder_is_byte_identical(client, tmp_path,
                                                             monkeypatch):
    """What the freeze is for. The build is not a pure function — it stamps a
    time on the row — but the decoded file is."""
    monkeypatch.setattr(catalog.settings, "PIE_CATALOG",
                        tmp_path / "products.jsonl")
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    _save(client, hdr, source, decoder=_decoder_for([r[1] for r in ROWS]).to_dict())
    url = "/api/v1/data/catalog/companies/cx_sls/catalogues/default/build"

    client.post(url, headers=hdr)
    first = catalog.company_catalog_path("cx_sls", "default").read_bytes()
    client.post(url, headers=hdr)
    assert catalog.company_catalog_path("cx_sls", "default").read_bytes() == first


@requires_pie
def test_the_two_paths_build_one_catalogue_together(client, tmp_path,
                                                    monkeypatch):
    """A company mid-migration: one file still read by a shipped rule set, one
    read by its own decoder. Both land in one catalogue, each row saying which
    path produced it, and the merge sees one namespace."""
    monkeypatch.setattr(catalog.settings, "PIE_CATALOG",
                        tmp_path / "products.jsonl")
    hdr = _hdr(client)

    packed = _upload(client, hdr, [("MM1", "SC DRILL 5,1mm/.2008/ 3xD", "KC7315", 90)],
                     filename="master.csv", source_key="master").json()
    _save(client, hdr, _sources(packed)["master"], rule_set="zcnc")
    acme = _upload(client, hdr, ROWS, filename="acme.xlsx",
                   source_key="acme").json()
    _save(client, hdr, _sources(acme)["acme"],
          decoder=_decoder_for([r[1] for r in ROWS]).to_dict())

    built = client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/"
                        "default/build", headers=hdr)
    assert built.status_code == 200, built.text
    by_key = {s["source_key"]: s for s in _cat(built.json())["built_from"]}
    assert by_key["master"]["decoded_by"] == catalog.DECODE_RULE_SET
    assert by_key["acme"]["decoded_by"] == catalog.DECODE_DECODER

    records = [json.loads(line) for line in
               catalog.company_catalog_path("cx_sls", "default")
               .read_text(encoding="utf-8").splitlines() if line.strip()]
    # The whole point: ONE namespace, though the pack stamped its own on half
    # of these. Two would stop a colliding material number being resolved by
    # the merge and make it an abstention from the index instead.
    assert {r["org_id"] for r in records} == {"cx_sls"}
    assert {r.get("source_key") for r in records} == {"master", "acme"}


# ── the namespace, and what one field for two authorities costs ────────────

@requires_pie
def test_the_namespace_is_the_company_not_the_pack(client, tmp_path, monkeypatch):
    """``record_namespace`` reads ``org_id`` first and its docstring says what
    the field means: the organisation, not the manufacturer. Under packs the
    value was the org pack's id — a proxy that happened to be constant across
    every company in a deployment. The portal states the real answer."""
    monkeypatch.setattr(catalog.settings, "PIE_CATALOG",
                        tmp_path / "products.jsonl")
    hdr = _hdr(client)
    source = _newest(_upload(client, hdr, ROWS).json())
    _save(client, hdr, source, decoder=_decoder_for([r[1] for r in ROWS]).to_dict())
    client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build",
                headers=hdr)

    import sys

    sys.path.insert(0, str(catalog.settings.PIE_PARSER_ROOT))
    from identity.store import record_namespace

    records = [json.loads(line) for line in
               catalog.company_catalog_path("cx_sls", "default")
               .read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {record_namespace(r) for r in records} == {"cx_sls"}


def test_a_material_number_two_files_claim_resolves_to_the_newest_and_is_counted(
        client, tmp_path, monkeypatch):
    """One namespace means the merge sees the collision and resolves it —
    newest file wins — rather than leaving two records the index answers as an
    abstention. ``record_id`` is the distributor's own number, so the same one
    twice is their master contradicting itself."""
    monkeypatch.setattr(catalog.settings, "PIE_CATALOG",
                        tmp_path / "products.jsonl")
    hdr = _hdr(client)
    # Six rows a side, because a shape seen fewer than five times is not
    # proposed as a segment (`infer.MIN_CLUSTER_ROWS`) — a pattern induced
    # from two examples is a pattern about those two examples. AC-701 is in
    # both files; the rest are not.
    old = [("AC-701", "ACME BORING BAR 1,5mm 4xD", "AK15", 10)] + [
        (f"AC-{80 + n}", f"ACME BORING BAR {n},5mm 4xD", "AK15", 10 + n)
        for n in range(1, 6)]
    new = [("AC-701", "ACME BORING BAR 9,5mm 4xD", "AK20", 20)] + [
        (f"AC-{90 + n}", f"ACME BORING BAR {n},5mm 4xD", "AK20", 20 + n)
        for n in range(1, 6)]
    for key, rows in (("first", old), ("second", new)):
        body = _upload(client, hdr, rows, filename=f"{key}.xlsx",
                       source_key=key).json()
        _save(client, hdr, _sources(body)[key],
              decoder=_decoder_for([r[1] for r in rows]).to_dict())

    built = client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/"
                        "default/build", headers=hdr).json()
    cat = _cat(built)
    assert cat["records"] == 11              # 12 rows, one part number shared
    assert cat["ingest"]["collisions"] == 1
    assert cat["ingest"]["collision_examples"] == ["AC-701"]
    records = {r["record_id"]: r for r in (
        json.loads(line) for line in
        catalog.company_catalog_path("cx_sls", "default")
        .read_text(encoding="utf-8").splitlines() if line.strip())}
    assert records["AC-701"]["source_key"] == "second"
    assert records["AC-701"]["grade"] == "AK20"


def test_a_catalogue_number_two_files_share_is_named_and_both_are_kept(tmp_path):
    """The conflation the index cannot keep apart, reported rather than
    resolved. ``catalog_number_full`` is the *manufacturer's* number and two
    manufacturers reusing one is normal — but it is namespaced by the same
    single field as the distributor's material number, so inside one company
    they land in one space. Dropping a record over its secondary identifier
    would lose a product; naming the repeat is the useful thing left."""
    members = []
    for index, (record_id, catalogue) in enumerate(
            [("AC-701", "5730123"), ("YG-902", "5730123")]):
        path = tmp_path / f"{index}.jsonl"
        path.write_text(json.dumps(
            {"record_id": record_id, "catalog_number_full": catalogue,
             "description_raw": "x"}, sort_keys=True) + "\n", encoding="utf-8")
        members.append({"key": f"cat{index}", "path": str(path)})

    merged = catalog._merge_decoded(members, tmp_path / "out.jsonl",
                                    tag="catalogue_key", namespace="cx_sls")
    assert merged["records"] == 2                     # both kept
    assert merged["catalog_collisions"] == 1
    assert merged["catalog_collision_examples"] == ["5730123"]
    assert merged["duplicates"] == 0                  # different material ids


def test_the_merge_stamps_the_namespace_over_whatever_the_decode_left(tmp_path):
    """Stamped before the de-duplication key is taken, so the key is the one
    the index will use rather than the one the decode path happened to leave
    behind. A pack writes ``zcnc`` here; the merge overwrites it."""
    path = tmp_path / "0.jsonl"
    path.write_text(json.dumps({"record_id": "MM1", "org_id": "zcnc"},
                               sort_keys=True) + "\n", encoding="utf-8")
    merged = catalog._merge_decoded([{"key": "a", "path": str(path)}],
                                    tmp_path / "out.jsonl", tag="source_key",
                                    namespace="cx_sls")
    assert merged["records"] == 1
    written = json.loads((tmp_path / "out.jsonl").read_text().strip())
    assert written["org_id"] == "cx_sls"


def test_two_files_agreeing_on_a_material_number_collide_across_the_paths(
        tmp_path):
    """The regression this namespace change exists to prevent: with the pack's
    ``zcnc`` on one record and the company's id on the other, the same material
    number sat in two namespaces and stopped being a collision the merge could
    resolve."""
    paths = []
    for index, org in enumerate(("zcnc", "cx_sls")):
        path = tmp_path / f"{index}.jsonl"
        path.write_text(json.dumps({"record_id": "MM1", "org_id": org},
                                   sort_keys=True) + "\n", encoding="utf-8")
        paths.append({"key": f"s{index}", "path": str(path)})
    merged = catalog._merge_decoded(paths, tmp_path / "out.jsonl",
                                    tag="source_key", namespace="cx_sls")
    assert merged["records"] == 1
    assert merged["duplicates"] == 1


# ── run_decoder on its own ─────────────────────────────────────────────────

def test_run_decoder_keeps_the_rows_it_could_not_read(client, tmp_path):
    """"Unknown means unknown" is the executor's contract and pie-parser's,
    and losing the rows at this boundary would break both."""
    hdr = _hdr(client)
    rows = list(ROWS) + [("AC-999", "SOMETHING ELSE ENTIRELY", "AK15", 5)]
    source_key = _newest(_upload(client, hdr, rows).json())["source_key"]
    with client.Maker() as s:
        row = next(r for r in s.query(models.CompanyCorpus).all()
                   if r.source_key == source_key)
        decoder = _decoder_for([r[1] for r in ROWS])
        result = catalog.run_decoder(row, decoder.to_dict(), tmp_path / "out.jsonl")

    assert result["records"] == len(ROWS)
    assert result["quarantined"] == 1
    assert result["decoder_id"] == decoder.decoder_id
    held = [json.loads(line) for line in
            (tmp_path / "out.quarantine.jsonl").read_text().splitlines() if line]
    assert held[0]["description_raw"] == "SOMETHING ELSE ENTIRELY"
    assert held[0]["reason"] == "NO_SEGMENT"


def test_run_decoders_run_id_names_the_file_and_the_decoder(client, tmp_path):
    """The same question pie-parser's ``run_id`` answers — which input and
    which rules produced this — so ``_union_version`` keeps working unchanged.
    Two decoders over one file give two run ids; one decoder over one file
    always gives the same."""
    hdr = _hdr(client)
    source_key = _newest(_upload(client, hdr, ROWS).json())["source_key"]
    first = _decoder_for([r[1] for r in ROWS])
    # The same patterns under a different decimal convention: a different
    # decoder, because it reads the same rows as different numbers.
    second = freeze(first.segments, decimal="comma")
    assert second.decoder_id != first.decoder_id
    with client.Maker() as s:
        row = next(r for r in s.query(models.CompanyCorpus).all()
                   if r.source_key == source_key)
        a = catalog.run_decoder(row, first.to_dict(), tmp_path / "a.jsonl")
        b = catalog.run_decoder(row, first.to_dict(), tmp_path / "b.jsonl")
        c = catalog.run_decoder(row, second.to_dict(), tmp_path / "c.jsonl")
    assert a["stamp"]["run_id"] == b["stamp"]["run_id"]
    assert c["stamp"]["run_id"] != a["stamp"]["run_id"]


def test_a_decoded_record_carries_no_column_the_file_had_beyond_the_three(
        client, tmp_path):
    """The nomenclature-only rule, on the path that never sees the pack's run
    profile. ``emit_rows`` is what drops the commercial columns, and it is the
    same function the rule-set path normalises with — so the guarantee has one
    implementation rather than one per path."""
    hdr = _hdr(client)
    source_key = _newest(_upload(client, hdr, ROWS).json())["source_key"]
    with client.Maker() as s:
        row = next(r for r in s.query(models.CompanyCorpus).all()
                   if r.source_key == source_key)
        catalog.run_decoder(row, _decoder_for([r[1] for r in ROWS]).to_dict(),
                            tmp_path / "out.jsonl")
    records = [json.loads(line) for line in
               (tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    prices = {str(r[3]) for r in ROWS} | {float(r[3]) for r in ROWS}
    for record in records:
        assert "Price" not in record
        assert not prices & set(record.values())
        assert not prices & set(map(str, record.values()))
