"""``python -m app.attributes`` — decorate one organization, and say what happened.

    python -m app.attributes ORG_ID
    python -m app.attributes ORG_ID --batch-size 200 -v
    python -m app.attributes --list-organizations

The operator's door to Phase 1. ``decorate_products`` landed with the store and
nothing called it, so in production the table was empty and every later phase
read nothing; this is one of the two callers that fixes that (the other is the
sync, ``ingestion.jobs.execute_analysis``). An organization can be decorated on
demand — after a pie-parser upgrade, after a catalogue rebuild, or the first
time on a book that was synced before this existed.

It prints what the run did and then the coverage, because published coverage per
category is what decision 002 makes Phase 1 succeed or fail on. What it does
**not** print is a verdict: there is no target here, no band and no colour, for
``coverage``'s stated reason — a number lower than somebody hoped is the answer,
and the fix is more decoded product rather than a friendlier denominator.

Exit codes, because this gets scripted:

    0   the run happened — whatever the coverage came out at
    1   neither source could be *asked*, so nothing was measured and nothing
        was written. Not a low number: an UNKNOWN, and usually a deployment
        that shipped without pie-parser
    2   a usage error, or an organization id that is not in this database
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence

from sqlalchemy import func, select

from ..config import settings
from ..domain import models
from .coverage import CoverageReport, attribute_coverage
from .decorate import DEFAULT_BATCH_SIZE, DecorationReport, decorate_organization


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m app.attributes",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("organization", nargs="?",
                    help="the organization to decorate (an organization_id)")
    ap.add_argument("--list-organizations", action="store_true",
                    help="print the organizations in this database, then exit")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                    metavar="N",
                    help=f"products per batch, committed at each boundary "
                         f"(default: {DEFAULT_BATCH_SIZE})")
    ap.add_argument("--rule-set", dest="rule_set", default=None,
                    metavar="ID_OR_PATH",
                    help="the rule set the product names are decoded through "
                         "— a shipped rule set id or a path. Defaults to the "
                         "organization layer in PIE_PACK, which is the one "
                         "written against a Zoho material master. Resolved by "
                         "master_health's own --rule-set, so the two commands "
                         "accept the same values.")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="report each batch as it commits, and log what the "
                         "decoder is doing")
    return ap


def _pct(rate: Optional[float]) -> str:
    """A rate, or the word for not having one.

    ``None`` is never rendered as 0%. An organization with no products has no
    fill rate — the ratio has no denominator — and 0% would read as a measured
    failure of a catalogue that does not exist.
    """
    return "unknown" if rate is None else f"{rate * 100:.1f}%"


def _render(report: DecorationReport, coverage: CoverageReport,
            org_name: str) -> str:
    out: list[str] = []
    add = out.append

    add(f"Product attributes — {report.organization_id}"
        + (f"  ({org_name})" if org_name else ""))
    add("")
    add("What this run read")
    add(f"  products considered          {report.products_considered:>9,}")
    add(f"  names decoded                {report.names_decoded:>9,}")
    add(f"  catalogue records read       {report.catalogue_records_read:>9,}")
    add(f"  catalogue links unresolved   {report.catalogue_links_unresolved:>9,}")
    if report.rows_without_outcome:
        # Zero in every observed run. Printed only when it is not, because a
        # row the decode batch returned no outcome for is not a row that
        # decoded to nothing, and the two must not be read as one.
        add(f"  rows the decoder skipped     {report.rows_without_outcome:>9,}")

    add("")
    add("What it wrote")
    add(f"  created                      {report.written.created:>9,}")
    add(f"  superseded                   {report.written.superseded:>9,}")
    add(f"  unchanged                    {report.written.unchanged:>9,}")
    add(f"  retracted                    {report.written.retracted:>9,}")

    # The two UNKNOWNs, printed as sentences and before the coverage they would
    # otherwise silently depress. A source that could not be asked wrote nothing
    # and retracted nothing, so its zero on the census below is not a finding.
    unknowns = [("DECODED_NAME", report.decoded_name_unavailable),
                ("CATALOGUE_LINK", report.catalogue_unavailable)]
    if any(reason for _kind, reason in unknowns):
        add("")
        add("Sources that could not be asked — their coverage is UNKNOWN, not zero")
        for kind, reason in unknowns:
            if reason:
                add(f"  {kind}: {reason}")

    if report.refusals:
        add("")
        add("Field extractions refused (a value the store declined to hold)")
        for (key, reason), count in sorted(report.refusals.items(),
                                           key=lambda kv: (-kv[1], kv[0])):
            add(f"  {count:>7,}  {key:<28}{reason}")

    add("")
    add("Coverage — the Phase 1 exit criterion")
    add(f"  products with any attribute  "
        f"{coverage.products_with_any_attribute:,} of {coverage.products_total:,}"
        f"  ({_pct(coverage.coverage_rate)})")
    per = coverage.attributes_per_decorated_product
    add(f"  live values                  {coverage.live_values:>9,}"
        + ("" if per is None else f"   ({per:.1f} per decorated product)"))
    for kind, count in coverage.by_source_kind:
        add(f"  {kind:<28} {count:>9,}")

    if coverage.by_key:
        add("")
        # Read this before the headline: a field the pack emits for almost
        # every row it can route makes "products with at least one attribute"
        # a measure of decode reach rather than of richness.
        add(f"  {'attribute_key':<28}{'products':>10}{'values':>9}{'fill':>9}")
        for key in coverage.by_key:
            add(f"  {key.attribute_key:<28}{key.products:>10,}{key.values:>9,}"
                f"{_pct(key.fill_rate):>9}")
    return "\n".join(out)


def _list_organizations(session) -> None:
    rows = session.execute(
        select(models.Organization.organization_id, models.Organization.name,
               func.count(models.Product.product_id))
        .outerjoin(models.Product,
                   models.Product.organization_id
                   == models.Organization.organization_id)
        .group_by(models.Organization.organization_id, models.Organization.name)
        .order_by(models.Organization.organization_id)).all()
    if not rows:
        print("This database holds no organizations.")
        return
    print(f"{'organization_id':<32}{'products':>9}   name")
    for org_id, name, products in rows:
        print(f"{org_id:<32}{products:>9,}   {name or ''}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    # Inside `main`, not at module scope, for the reason `master_health.cli`
    # imports the engine bridge late: opening the database is a side effect,
    # and `--help` must not have one. It is also what lets a test point the
    # CLI at its own in-memory database.
    from ..db import SessionLocal

    session = SessionLocal()
    try:
        if args.list_organizations:
            _list_organizations(session)
            return 0
        if not args.organization:
            _build_parser().print_usage(sys.stderr)
            print("error: an organization_id is required (or --list-organizations)",
                  file=sys.stderr)
            return 2

        org = session.get(models.Organization, args.organization)
        if org is None:
            print(f"error: no organization {args.organization!r} in this database. "
                  f"Run --list-organizations to see what is here.", file=sys.stderr)
            return 2

        def committed(done: int, total: int) -> None:
            """The batch boundary: end the write, and say so if asked.

            ``decorate_organization`` deliberately does not commit — it writes
            rows and leaves transaction boundaries to whoever owns them. Here
            that is this function, and committing per batch is what keeps a
            15,000-item master off one long write lock (CLAUDE.md §4).
            """
            session.commit()
            if args.verbose:
                print(f"  decorated {done:,} of {total:,}", file=sys.stderr)

        # Resolved through master_health's own resolver rather than a second
        # one: "a shipped id or a path" is one behaviour and it already has an
        # owner (CLAUDE.md §2). An unset --rule-set means the organization
        # layer, which is what a material master is phrased in; naming a wrong
        # one is a refusal from that resolver rather than a silent miss.
        from ..master_health.cli import _rule_set_path  # noqa: PLC0415
        rule_set = (_rule_set_path(args.rule_set) if args.rule_set
                    else settings.PIE_PACK)

        report = decorate_organization(session, args.organization,
                                       batch_size=args.batch_size,
                                       on_batch=committed,
                                       rule_set=rule_set)
        session.commit()
        print(_render(report, attribute_coverage(session, args.organization),
                      org.name or ""))
        # Not "the coverage was low": nothing was measured at all, which is a
        # deployment fault rather than a finding. One source missing is still a
        # run — it is printed above and the other source's numbers stand — so
        # only losing *both* is worth a non-zero exit. The caller scripting this
        # needs those apart, which is the same distinction the package refuses
        # to collapse when it declines to retract.
        if report.decoded_name_unavailable and report.catalogue_unavailable:
            return 1
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
