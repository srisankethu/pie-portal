"""Turn an item name into a normalized description, using pie-parser.

Separate from ``pie_service.resolve``: that one *matches* a requirement against
a catalogue and answers "which product is this?". This one *decodes* a single
description and answers "what does this name actually say?" — which is what a
Zoho Books item description needs.

Deluge cannot do this. pie-parser is Python with a rules pack, and Zoho's
scripting language cannot import it. Reimplementing the grammar in Deluge would
create a second parser that disagrees with the first within a month, which is
the failure this codebase has been removing everywhere else. So the parser stays
here and Zoho calls it.

**The engine abstains often, and that is the important behaviour.** With the
current ``kennametal_widia`` pack, ISO turning inserts decode richly and most
other names decode to nothing at all. A describe endpoint that returned a
confident-looking empty string would overwrite a human-written description with
a worse one, which is why ``confident`` is computed here and why the caller is
expected to skip when it is false.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("pie_portal.describe")

# Dimension slots the engine emits, in the order a person reads a tool. The
# prefix carries its own spacing: a symbol sits against its number (Ø12) and a
# word does not (edge 12).
_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("diameter_mm", "Ø"),
    ("cutting_dia_mm", "Ø"),
    ("edge_length_mm", "edge "),
    ("cutting_length_mm", "cut "),
    ("overall_length_mm", "OAL "),
    ("shank_diameter_mm", "shank "),
    ("thickness_mm", "thk "),
    ("corner_radius_mm", "R"),
    ("neck_diameter_mm", "neck "),
    ("point_angle_deg", "point "),
    ("helix_angle_deg", "helix "),
)

_FAMILY_LABEL = {
    "turning_insert": "Turning insert",
    "milling_insert": "Milling insert",
    "solid_carbide_endmill": "Solid carbide end mill",
    "solid_carbide_drill": "Solid carbide drill",
    "drill_tip": "Drill tip",
    "reamer": "Reamer",
    "tool_holder": "Tool holder",
    "boring_bar": "Boring bar",
    "other_tooling": None,        # deliberately unlabelled — says nothing useful
}


@dataclass
class Described:
    """What the parser made of one item name."""

    name: str
    description: str = ""
    product_family: Optional[str] = None
    product_subfamily: Optional[str] = None
    grade: Optional[str] = None
    manufacturer: Optional[str] = None
    dimensions: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    unresolved_tokens: list[str] = field(default_factory=list)
    row_confidence: float = 0.0
    engine_version: str = ""
    pack_id: str = ""
    ruleset_checksum: str = ""
    error: Optional[str] = None

    @property
    def confident(self) -> bool:
        """Whether this is worth writing onto a record.

        The test is whether the parser recognised anything *specific* — a
        dimension or a decoded attribute — not whether it produced a string. A
        name that only routed to "other tooling / general" produced no
        knowledge, and writing that over an existing description destroys
        information rather than adding it.
        """
        if self.error or not self.description:
            return False
        return bool(self.dimensions or self.attributes)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "confident": self.confident,
            "product_family": self.product_family,
            "product_subfamily": self.product_subfamily,
            "grade": self.grade,
            "manufacturer": self.manufacturer,
            "dimensions": self.dimensions,
            "attributes": self.attributes,
            "flags": self.flags,
            "unresolved_tokens": self.unresolved_tokens,
            "row_confidence": self.row_confidence,
            "engine_version": self.engine_version,
            "pack_id": self.pack_id,
            "ruleset_checksum": self.ruleset_checksum,
            "error": self.error,
        }


def _num(value: Any) -> str:
    """Render a measurement without inventing precision: 12.0 → 12, 0.80 → 0.8."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render(record: dict) -> str:
    """Compose the description a human would write from the decoded slots.

    Deterministic and ordered, so the same item always produces the same string
    and a diff against what is already stored is meaningful.
    """
    parts: list[str] = []

    family = record.get("product_family")
    label = _FAMILY_LABEL.get(family, (family or "").replace("_", " ").capitalize() or None)
    if label:
        sub = record.get("product_subfamily")
        # "general" is the engine's way of saying it did not narrow the family.
        if sub and sub not in ("general", "unknown"):
            label = f"{label} · {sub.replace('_', ' ')}"
        parts.append(label)

    shape = (record.get("attributes_ext") or {}).get("iso.shape_name")
    if shape:
        parts.append(str(shape))

    dims = [f"{prefix}{_num(record[key])}" + (" mm" if key.endswith("_mm") else "°")
            for key, prefix in _DIMENSIONS
            if record.get(key) is not None]
    if dims:
        parts.append(" × ".join(dims))

    if record.get("grade"):
        parts.append(f"grade {record['grade']}")
    if record.get("manufacturer"):
        parts.append(str(record["manufacturer"]))

    return " · ".join(parts)


def describe(name: str) -> Described:
    """Decode one item name. Never raises — an engine failure is a result."""
    text = (name or "").strip()
    if not text:
        return Described(name=text, error="No item name supplied")

    try:
        record = _decode(text)
    except Exception as e:  # noqa: BLE001 — an engine fault must not 500 a webhook
        log.exception("pie-parser describe failed for %r", text)
        return Described(name=text, error=f"{type(e).__name__}: {e}"[:300])

    dims = {k: record[k] for k, _ in _DIMENSIONS if record.get(k) is not None}
    return Described(
        name=text,
        description=render(record),
        product_family=record.get("product_family"),
        product_subfamily=record.get("product_subfamily"),
        grade=record.get("grade"),
        manufacturer=record.get("manufacturer"),
        dimensions=dims,
        attributes=dict(record.get("attributes_ext") or {}),
        flags=list(record.get("flags") or []),
        unresolved_tokens=list(record.get("unresolved_tokens") or []),
        row_confidence=float(record.get("row_confidence") or 0.0),
        engine_version=str(record.get("engine_version") or ""),
        pack_id=str(record.get("pack_id") or ""),
        ruleset_checksum=str(record.get("ruleset_checksum") or ""),
    )


# ── engine plumbing ─────────────────────────────────────────────────────────
# Loaded once. The pack self-test runs on load for the same reason the CLI runs
# it: a pack whose own patterns do not match its own examples will decode
# everything wrongly and silently.
_pack = None
_profile = None
_lock = __import__("threading").Lock()

PACK_NAME = "kennametal_widia"


def _load():
    global _pack, _profile
    if _pack is not None:
        return _pack, _profile
    with _lock:
        if _pack is not None:
            return _pack, _profile
        import sys

        from .config import settings

        root = str(settings.PIE_PARSER_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)

        from engine.pack import load_pack
        from engine.pipeline import RunProfile

        pack_dir = settings.PIE_PARSER_ROOT / "packs" / PACK_NAME
        pack = load_pack(pack_dir)
        failures = pack.patterns.self_test()
        if failures:
            raise RuntimeError(
                f"pie-parser pack {PACK_NAME} failed its own pattern self-test: "
                + "; ".join(str(f) for f in failures[:3]))
        _pack, _profile = pack, RunProfile()
        log.info("pie-parser describe: pack %s v%s loaded (checksum %s)",
                 pack.pack_id, pack.version, pack.checksum)
        return _pack, _profile


def _decode(text: str) -> dict:
    """Decode one description through the CLI's own path.

    Deliberately ``tools.single_query.run_single`` — the exact function behind
    ``python -m tools.run_parser --text`` — so what this endpoint returns and
    what someone sees on the command line cannot diverge.
    """
    pack, profile = _load()
    from tools.single_query import run_single

    record, _classified = run_single(pack, text, None, profile)
    return record
