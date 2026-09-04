"""Decoding a file through a decoder built for that file, and nothing else.

There is no shipped rule set here, no default and no fallback. A price list is
read by a **decoder artifact** frozen for it — its own extraction rules, its
own decimal convention — and a file without one is not decoded. That is the
architecture ``docs/per-company-catalogues.md`` §13 describes for the decoding
config, carried down to the grammars themselves.

**How something inferred becomes something deterministic.** Working out how a
file should be decoded is a judgement — it reads the text, proposes structure,
and a person confirms it — and no judgement is reproducible. Decoding is not a
judgement, and it is reproducible, because the two are separated by a freeze:

    read the file  →  propose a decoder  →  a person confirms  →  FROZEN
                                                                    ↓
                             file bytes + frozen decoder  →  records, forever

Inference is a *build* step, in the sense that authoring a migration is: the
non-reproducible act happens once, produces a reviewed artifact, and the
artifact is what runs. The rule that keeps this true is one line long —
**inference never runs at decode time** — and everything else in this package
is machinery for holding it:

* :mod:`app.decoding.schema` freezes and content-addresses an artifact, and
  refuses one that does not compile, does not match its own examples, matches
  its own counterexamples, or names a slot the shared vocabulary does not know.
* :mod:`app.decoding.safety` decides whether a pattern is safe to run over
  every row of every rebuild — *statically*, because the obvious alternative
  is a timeout and a timeout would make the same file decode differently on a
  busy machine.
* :mod:`app.decoding.executor` runs a frozen decoder as a pure function and
  refuses one frozen against a different executor version rather than doing
  its best with it.

The one thing that is **not** per file is the attribute vocabulary
(:data:`~app.decoding.schema.CORE_SLOTS`, 50 names). Every file gets its own
rules, but they all extract into the same names — because a quote asks whether
this drill is equivalent to that one, and the answer is a comparison of
``cutting_dia_mm`` against ``cutting_dia_mm``. If each file named its own
fields there would be nothing to compare.

Deterministic by contract: listed in ``test_layer_boundaries.DETERMINISTIC``,
and it never imports ``ai/``. The step that *proposes* a decoder may use a
model; the step that runs one may not, and the layer test is what keeps the
two on opposite sides of the freeze.
"""
from .executor import (
    BAD_VALUE,
    NO_DESCRIPTION,
    NO_SEGMENT,
    DecodeResult,
    DecoderVersionError,
    Quarantined,
    decode,
    to_jsonl,
)
from .safety import MAX_INPUT_LENGTH, UnsafePattern, check_pattern
from .schema import (
    CORE_SLOTS,
    DECIMAL_CONVENTIONS,
    EXT_PREFIX,
    ID_WIDTH,
    SCHEMA_VERSION,
    SLOT_TYPES,
    Decoder,
    DecoderError,
    FieldBinding,
    Segment,
    canonical_bytes,
    content_id,
    freeze,
    from_dict,
    is_known_slot,
)

__all__ = [
    "BAD_VALUE", "CORE_SLOTS", "DECIMAL_CONVENTIONS", "EXT_PREFIX",
    "ID_WIDTH", "MAX_INPUT_LENGTH", "NO_DESCRIPTION", "NO_SEGMENT",
    "SCHEMA_VERSION",
    "SLOT_TYPES", "DecodeResult", "Decoder", "DecoderError",
    "DecoderVersionError", "FieldBinding", "Quarantined", "Segment",
    "UnsafePattern", "canonical_bytes", "check_pattern", "content_id",
    "decode", "freeze", "from_dict", "is_known_slot", "to_jsonl",
]
