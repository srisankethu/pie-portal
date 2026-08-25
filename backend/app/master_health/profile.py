"""Which column means what — as data, never as a branch.

This module is the whole reason the report can be pointed at an ERP nobody
here has seen. It knows the *roles* a Master Health Report needs filled — sku,
name, manufacturer, rate, stock, hsn, uom — and knows nothing at all about
which headers a particular ERP uses to fill them. That fact lives in
``profiles/<source>.yaml``, one small file per source, and adding an ERP is
adding a file.

The pattern is pie-parser's, not a new one. Its ``engine.pipeline.ColumnMapping``
is described in its own docstring as a "run-profile mapping of source columns to
the RawRecord contract", and its packs declare a ``columns:`` block so a second
distributor's corpus is a pack edit rather than a code edit. The same
discipline, applied to the same problem, one repository over. We *use* that
class rather than re-implementing it, for the two roles it already covers.

If you ever find yourself writing ``if source == "zoho"`` in this package, the
branch belongs in a profile.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

PROFILE_DIR = Path(__file__).parent / "profiles"

#: The column roles a report can fill, and whether the report can run without
#: one. Only ``sku`` and ``name`` are required — everything else degrades to a
#: *stated* gap rather than to a zero, which is the whole of ``CLAUDE.md`` §1's
#: "absence of evidence is not a pass" as it applies to a missing column. A
#: source with no HSN column has an UNKNOWN HSN census, never a clean one.
ROLES: tuple[str, ...] = (
    "sku", "name", "manufacturer", "rate", "stock", "hsn", "uom",
)
REQUIRED_ROLES: frozenset[str] = frozenset({"sku", "name"})

#: What each role is for, printed by ``--list-profiles`` and by the error a bad
#: profile raises. A person mapping an unfamiliar export reads this, so it says
#: what the report *does* with the column rather than restating the name.
ROLE_MEANING: Dict[str, str] = {
    "sku": "the item's own identifier — what identity linking is attempted against",
    "name": "the item's description — what the geometry decode is run over",
    "manufacturer": "who makes it, as the master words it — the manufacturer census",
    "rate": "SELLING price per unit. Never a purchase rate: see the module docstring",
    "stock": "quantity on hand — with rate, gives stock value at selling price",
    "hsn": "HSN/SAC classification code — the missing-HSN census",
    "uom": "unit of measure",
}


class ProfileError(ValueError):
    """A column profile that cannot be trusted to read an export correctly."""


@dataclass(frozen=True)
class ColumnProfile:
    """One source's headers, named by the role each fills.

    ``columns`` maps a role from :data:`ROLES` to the header string in the
    export. A role that is absent from the mapping is a column this source does
    not have, which is a fact the report states rather than a value it assumes.
    """

    profile_id: str
    label: str
    columns: Dict[str, str]
    notes: str = ""

    def header(self, role: str) -> Optional[str]:
        return self.columns.get(role)

    def has(self, role: str) -> bool:
        return bool(self.columns.get(role))

    @property
    def missing_roles(self) -> tuple[str, ...]:
        """Roles this source has no column for — reported, never defaulted."""
        return tuple(r for r in ROLES if not self.has(r))

    @property
    def version(self) -> str:
        """A content hash of the mapping that read the file.

        The same stamp discipline as ``CommercialThresholds.version``: a number
        has to be able to say what rule produced it. Two exports of the same
        master read under different profiles are different measurements, and a
        report that could not name its profile could not explain the
        difference. Prefixed ``cp_`` so it is never mistaken for a ``ci_``
        commercial stamp or a ``th_`` signal one.
        """
        body = "|".join(f"{r}={self.columns.get(r, '')}" for r in ROLES)
        return "cp_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]

    def with_overrides(self, overrides: Dict[str, Optional[str]]) -> "ColumnProfile":
        """A copy with individual roles re-pointed, for the ``--col-*`` flags.

        An override with a ``None`` value is ignored (the flag was not passed);
        an override with an empty string *removes* the role, which is how a
        caller says "this export has no such column" about a source whose
        profile normally does.
        """
        cols = dict(self.columns)
        touched = False
        for role, value in overrides.items():
            if value is None:
                continue
            touched = True
            if value == "":
                cols.pop(role, None)
            else:
                cols[role] = value
        if not touched:
            return self
        return replace(self, columns=cols,
                       profile_id=f"{self.profile_id}+overrides",
                       label=f"{self.label} (with column overrides)")

    def validate_against(self, headers: Iterable[str]) -> None:
        """Refuse a profile whose columns are not in the file it was pointed at.

        Loudly, and naming both sides. The failure this prevents is the quiet
        one: a mapped header that does not exist reads every row's value as
        blank, and a blank-field census over a mis-pointed profile reports 100%
        of the master missing a manufacturer — a catastrophic-looking finding
        with no defect behind it.
        """
        present = {str(h).strip() for h in headers}
        wrong = {role: col for role, col in self.columns.items() if col not in present}
        if wrong:
            named = ", ".join(f"{r} -> '{c}'" for r, c in sorted(wrong.items()))
            raise ProfileError(
                f"profile '{self.profile_id}' maps columns that this file does not "
                f"have: {named}. The file's headers are: {sorted(present)}. Fix the "
                f"profile, or re-point one role with --col-<role>."
            )


def _coerce(profile_id: str, doc: Any) -> ColumnProfile:
    if not isinstance(doc, dict):
        raise ProfileError(f"profile '{profile_id}': expected a YAML mapping")
    raw_cols = doc.get("columns")
    if not isinstance(raw_cols, dict) or not raw_cols:
        raise ProfileError(f"profile '{profile_id}': needs a non-empty 'columns:' mapping")

    unknown = sorted(set(raw_cols) - set(ROLES))
    if unknown:
        raise ProfileError(
            f"profile '{profile_id}': unknown column role(s) {unknown}. "
            f"The roles a report fills are {list(ROLES)}. A profile cannot invent a "
            f"role — that would be a report feature, and it belongs in code."
        )
    columns = {r: str(v).strip() for r, v in raw_cols.items() if v is not None and str(v).strip()}
    missing_required = sorted(REQUIRED_ROLES - set(columns))
    if missing_required:
        raise ProfileError(
            f"profile '{profile_id}': missing required role(s) {missing_required}. "
            f"{'; '.join(f'{r}: {ROLE_MEANING[r]}' for r in missing_required)}"
        )
    return ColumnProfile(
        profile_id=profile_id,
        label=str(doc.get("label") or profile_id),
        columns=columns,
        notes=str(doc.get("notes") or "").strip(),
    )


def available_profiles() -> tuple[str, ...]:
    """Every shipped profile id, sorted — the listing is the directory."""
    return tuple(sorted(p.stem for p in PROFILE_DIR.glob("*.yaml")))


def load_profile(name_or_path: str) -> ColumnProfile:
    """A shipped profile by name, or any YAML file by path.

    The path form is what makes a tenant's own odd export a five-line file
    rather than a support ticket; the name form is what makes the common cases
    a single flag.
    """
    candidate = Path(name_or_path)
    if candidate.suffix in (".yaml", ".yml") or candidate.exists():
        if not candidate.is_file():
            raise ProfileError(f"no such profile file: {candidate}")
        path, profile_id = candidate, candidate.stem
    else:
        path = PROFILE_DIR / f"{name_or_path}.yaml"
        if not path.is_file():
            raise ProfileError(
                f"unknown profile '{name_or_path}'. Shipped profiles: "
                f"{list(available_profiles())}. A new ERP is a new YAML file in "
                f"{PROFILE_DIR}, not a code change — or pass a path to one."
            )
        profile_id = name_or_path
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"could not read profile {path}: {exc}") from exc
    return _coerce(profile_id, doc)


def profile_from_columns(columns: Dict[str, Optional[str]],
                         profile_id: str = "cli") -> ColumnProfile:
    """A profile built entirely from ``--col-*`` flags, with no file behind it.

    For the one-off: someone holding an export from a system with no profile
    yet, who wants an answer now. If they run it twice, they should write the
    five-line YAML — and the report says so in that case.
    """
    doc = {"label": "ad-hoc profile from --col-* flags",
           "columns": {k: v for k, v in columns.items() if v},
           "notes": "Built from command-line flags. Write it to a YAML file in "
                    "profiles/ if this export will be read again."}
    return _coerce(profile_id, doc)
