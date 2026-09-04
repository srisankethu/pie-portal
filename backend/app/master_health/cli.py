"""``python -m app.master_health`` — an item-master export in, a report out.

    python -m app.master_health ITEMS.csv --profile zoho
    python -m app.master_health ITEMS.xlsx --profile netsuite --json report.json
    python -m app.master_health odd.csv --col-sku Code --col-name Description \
        --col-rate Price --col-stock Qty

No HTTP, no upload, no ERP connection, no database. A path and a profile.

The ``--col-*`` flags exist so an export from a system with no profile yet can
be read today; if the same export will be read again, write the five-line YAML
into ``profiles/`` and the flags go away. Adding an ERP is adding that file —
if this module ever grows a branch on the source's name, the branch belongs in
a profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .analysis import build_report
from .geometry import decode_names
from .policy import PolicyError, load_policy
from .profile import (
    ROLE_MEANING,
    ROLES,
    ColumnProfile,
    ProfileError,
    available_profiles,
    load_profile,
    profile_from_columns,
)
from .render import render_text
from .source import SourceError, read_export

log = logging.getLogger("pie_portal.master_health")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m app.master_health",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("export", nargs="?", type=Path,
                    help="path to the item-master export (.csv or .xlsx)")
    ap.add_argument("--profile", default=None,
                    help=f"named profile ({', '.join(available_profiles())}) or a "
                         f"path to a profile YAML")
    ap.add_argument("--list-profiles", action="store_true",
                    help="print the shipped profiles and the roles they fill, then exit")
    ap.add_argument("--sheet", default=None,
                    help="worksheet name for an .xlsx export (default: the first)")
    ap.add_argument("--policy", type=Path, default=None,
                    help="remediation policy YAML (default: the shipped one)")
    ap.add_argument("--company", default=None, metavar="CONNECTION_ID",
                    help="the connected company whose decoded catalogue this "
                         "export is measured against (its connection id). "
                         "Without it nothing is looked up and the identity "
                         "section reports UNKNOWN rather than zero")
    ap.add_argument("--rule-set", dest="rule_set", default=None,
                    metavar="ID_OR_PATH",
                    help="the rule set the names are decoded through — a "
                         "shipped rule set id or a path. Required for the "
                         "geometry half: which rule set reads an export is a "
                         "fact about that file, there is no default one, and "
                         "without it geometry coverage reports UNKNOWN rather "
                         "than decoding through somebody else's grammars. "
                         "Named separately from --company because this tool "
                         "reads no database, and a file's decoding config is "
                         "stored beside the file")
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the full report as JSON to this path")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the text report here instead of stdout")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="log what the engine is doing")
    for role in ROLES:
        ap.add_argument(f"--col-{role}", dest=f"col_{role}", default=None,
                        metavar="HEADER",
                        help=f"{ROLE_MEANING[role]} (overrides the profile; pass "
                             f"'' to say this export has no such column)")
    return ap


def _resolve_profile(args: argparse.Namespace) -> ColumnProfile:
    overrides = {role: getattr(args, f"col_{role}") for role in ROLES}
    if args.profile:
        return load_profile(args.profile).with_overrides(overrides)
    given = {r: v for r, v in overrides.items() if v}
    if not given:
        raise ProfileError(
            "no column profile. Pass --profile <name|path> — shipped profiles are "
            f"{list(available_profiles())} — or map the columns directly with "
            "--col-sku/--col-name and the rest. The profile is not optional: this "
            "report does not know what any particular ERP's export looks like, "
            "which is the property that lets it read one it has never seen.")
    return profile_from_columns(given)


def _rule_set_path(given: Optional[str]) -> Optional[Path]:
    """Resolve ``--rule-set`` to a directory: a shipped id, or a path.

    An id rather than only a path because that is what a file's saved decoding
    config names, so the two read the same way. Returns None for "unspecified",
    which decodes nothing and reports geometry coverage as UNKNOWN — an id this
    engine does not ship is an error rather than a silent fallback, since the
    coverage numbers would then belong to a rule set nobody asked for.
    """
    if not given:
        return None
    candidate = Path(given)
    if candidate.is_dir():
        return candidate

    from .. import catalog  # noqa: PLC0415

    for rule_set in catalog.available_rule_sets():
        if rule_set["id"] == given:
            return Path(rule_set["path"])
    raise ProfileError(
        f"{given!r} is neither a directory nor a rule set this engine ships. "
        f"Shipped rule sets: "
        f"{', '.join(r['id'] for r in catalog.available_rule_sets()) or 'none'}.")


def _print_profiles() -> None:
    print("Column roles a Master Health Report fills:\n")
    for role in ROLES:
        print(f"  {role:<14}{ROLE_MEANING[role]}")
    print("\nShipped profiles (each is one YAML file — adding an ERP is adding one):\n")
    for name in available_profiles():
        profile = load_profile(name)
        print(f"  {name}  —  {profile.label}  [{profile.version}]")
        for role in ROLES:
            header = profile.header(role)
            print(f"      {role:<14}{header if header else '(no column in this export)'}")
        if profile.notes:
            print(f"      note: {profile.notes}")
        print()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.list_profiles:
        _print_profiles()
        return 0
    if args.export is None:
        _build_parser().print_usage(sys.stderr)
        print("error: an export path is required (or --list-profiles)", file=sys.stderr)
        return 2

    try:
        profile = _resolve_profile(args)
        rule_set = _rule_set_path(args.rule_set)
        policy = load_policy(args.policy)
        rows, _headers = read_export(args.export, profile, sheet=args.sheet)
    except (ProfileError, PolicyError, SourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not rows:
        print(f"error: {args.export} has a header but no data rows.", file=sys.stderr)
        return 2

    # Imported here rather than at module scope: loading the engine bridge costs
    # a catalogue build, and `--list-profiles` and every argument error above
    # must not pay for it.
    from ..pie_service import pie_service  # noqa: PLC0415

    # One company's catalogue, and the rule set these names are decoded
    # through.
    #
    # Neither is borrowed and neither has a default: another company's item
    # master reports every SKU as unknown, and another file's grammars report
    # every name as unparsed. Where no company is named nothing is looked up
    # and the identity section says UNKNOWN rather than zero, which is what
    # `catalogue_available=False` already means downstream; where no rule set
    # is named nothing is decoded and geometry coverage says the same.
    decode = decode_names(rows, rule_set)
    report = build_report(
        rows=rows, profile=profile, decode=decode, policy=policy,
        lookup=lambda identifier: pie_service.lookup_record(identifier, args.company),
        catalogue_available=pie_service.catalog_available(args.company),
        source_file=str(args.export),
        source_digest=hashlib.sha256(Path(args.export).read_bytes()).hexdigest(),
    )

    text = render_text(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":   # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
