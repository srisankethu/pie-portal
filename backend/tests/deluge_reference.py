"""Reference implementation of the Deluge port, in Python.

Written the way Deluge has to work — no named capture groups, no lookaheads,
positional character tests only — so that verifying this against pie-parser
verifies the Deluge function too. Every function here maps 1:1 onto a block in
the .dg file.
"""
from __future__ import annotations

ISO_SHAPES = {
    "A": ("parallelogram 85°", 85), "B": ("parallelogram 82°", 82),
    "C": ("rhombic 80°", 80), "D": ("rhombic 55°", 55),
    "E": ("rhombic 75°", 75), "F": ("rhombic 50°", 50),
    "H": ("hexagon", 120), "K": ("parallelogram 55°", 55),
    "L": ("rectangle", 90), "M": ("rhombic 86°", 86),
    "O": ("octagon", 135), "P": ("pentagon", 108),
    "R": ("round", None), "S": ("square", 90),
    "T": ("triangle", 60), "V": ("rhombic 35°", 35),
    "W": ("trigon 80°", 80), "X": ("special", None),
}

ISO_CLEARANCES = {
    "A": 3, "B": 5, "C": 7, "D": 15, "E": 20,
    "F": 25, "G": 30, "N": 0, "P": 11, "O": None,
}

ISO_THICKNESS = {
    "01": 1.59, "T1": 1.98, "02": 2.38, "T2": 2.78, "03": 3.18,
    "T3": 3.97, "04": 4.76, "05": 5.56, "06": 6.35, "07": 7.94,
    "09": 9.52, "12": 12.70,
}

PREFIXES = ["CARBIDE INSERT ", "INSERTS ", "INSERT. ", "INSERT ", "INS. ", "INS "]

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"


def is_letter(ch: str) -> bool:
    return ch in LETTERS


def is_digit(ch: str) -> bool:
    return ch in DIGITS


def normalize(text: str) -> str:
    """Upper-case, collapse whitespace. Deluge: toUpperCase + replaceAll."""
    out = (text or "").upper().strip()
    while "  " in out:
        out = out.replace("  ", " ")
    return out


def strip_prefix(text: str) -> str:
    for p in PREFIXES:
        if text.startswith(p):
            return text[len(p):].strip()
    return text


def parse_iso(raw: str) -> dict:
    """Decode an ISO 1832 insert designation from anywhere sensible in the text.

    Tries the whole string first, then each comma/slash-separated segment, so
    "WSP,CCMT060204,THM" is read the same way "CCMT060204" is. Mirrors patterns
    P-KMT-ISO-CODE and P-KMT-ISO-CODE-R, including their digit-count guards.
    """
    candidates = [raw]
    for sep in (",", "/"):
        if sep in (raw or ""):
            candidates = candidates + [c for c in raw.split(sep)]
    for candidate in candidates:
        found = _parse_one(candidate)
        if found:
            return found
    return {}


def _parse_one(raw: str) -> dict:
    text = strip_prefix(normalize(raw))
    if len(text) < 6:
        return {}

    shape, clear, tol, fix = text[0], text[1], text[2], text[3]
    for ch in (shape, clear, tol, fix):
        if not is_letter(ch):
            return {}
    # P-KMT-ISO-CODE excludes I and R from the shape position; R has its own
    # pattern because its size block is a diameter, not an edge length.
    if shape == "I":
        return {}
    if shape not in ISO_SHAPES:
        return {}

    rest = text[4:]
    if rest.startswith(" "):
        rest = rest[1:]

    # ── the size block ──────────────────────────────────────────────────────
    # Two digits, then a thickness code (T1..T3 or two digits), then for the
    # six-character form two more digits of corner radius.
    if len(rest) < 4:
        return {}
    if not (is_digit(rest[0]) and is_digit(rest[1])):
        return {}

    size_a = rest[0:2]

    if rest[2] == "T" and rest[3] in "123":
        thick_code = rest[2:4]
        after_thick = rest[4:]
    elif is_digit(rest[2]) and is_digit(rest[3]):
        thick_code = rest[2:4]
        after_thick = rest[4:]
    else:
        return {}

    radius_code = ""
    if len(after_thick) >= 2 and is_digit(after_thick[0]) and is_digit(after_thick[1]):
        # Six-digit form. The engine's (?!\d(?:\d|$)) guard rejects a run that
        # continues with more digits — that is a part number, not a size.
        tail = after_thick[2:]
        if len(tail) >= 1 and is_digit(tail[0]):
            if len(tail) == 1 or is_digit(tail[1]):
                return {}
        radius_code = after_thick[0:2]
        after_size = after_thick[2:]
    else:
        # Four-digit form. (?!\d) — a fifth digit means this is not a size.
        if len(after_thick) >= 1 and is_digit(after_thick[0]):
            return {}
        after_size = after_thick

    shape_name, included = ISO_SHAPES[shape]

    out = {
        "iso_shape": shape,
        "shape_name": shape_name,
        "included_angle_deg": included,
        "iso_clearance_letter": clear,
        "iso_tolerance": tol,
        "iso_fixing": fix,
        "thickness_code": thick_code,
        "tail": after_size.strip(" -"),
    }

    if clear in ISO_CLEARANCES:
        out["iso_clearance_deg"] = ISO_CLEARANCES[clear]

    if shape == "R":
        out["family"] = "round"
        out["cutting_dia_mm"] = int(size_a)
    else:
        out["edge_length_mm"] = int(size_a)

    if thick_code in ISO_THICKNESS:
        out["thickness_mm"] = ISO_THICKNESS[thick_code]
    # A round insert has no corner radius — the pack's round grammar captures
    # those digits and deliberately binds no slot for them. Emitting "R0.0"
    # would be a measurement the standard does not define.
    if radius_code != "" and shape != "R":
        out["corner_radius_mm"] = int(radius_code) / 10.0
    return out


def num(value) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def render(d: dict) -> str:
    if not d:
        return ""
    parts = ["Turning insert · iso full", d["shape_name"]]

    dims = []
    if "cutting_dia_mm" in d:
        dims.append("Ø" + num(d["cutting_dia_mm"]) + " mm")
    if "edge_length_mm" in d:
        dims.append("edge " + num(d["edge_length_mm"]) + " mm")
    if "thickness_mm" in d:
        dims.append("thk " + num(d["thickness_mm"]) + " mm")
    if "corner_radius_mm" in d:
        dims.append("R" + num(d["corner_radius_mm"]) + " mm")
    if dims:
        parts.append(" × ".join(dims))
    return " · ".join(parts)


def describe(raw: str) -> str:
    return render(parse_iso(raw))
