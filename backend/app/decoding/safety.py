"""Whether a pattern is safe to run over every row of every rebuild.

**Static, and static because determinism requires it.** The obvious defence
against a pathological regex is a timeout, and a timeout is exactly what this
design cannot have: a wall-clock limit makes the same file decode into
different records on a loaded machine than on an idle one, which is the
guarantee :mod:`app.decoding.schema` exists to hold. So a pattern is judged
once, at freeze time, by its shape — the same pattern always gets the same
verdict — and an unsafe one is never stored.

The hazard is real rather than theoretical. ``docs/per-company-catalogues.md``
§1.2 refused *uploaded* rule sets partly because a catastrophically
backtracking expression would be an outage a tenant could upload for
themselves. A pattern proposed by an inference step is not uploaded, but it is
machine-written and runs over every row, which is the same hazard with nobody
to ask about it.

What is refused, and why each one:

* **nested unbounded quantifiers** — ``(a+)+``, ``(a*)*``. This is the shape
  that turns a 40-character line into exponential backtracking. It is detected
  on the parse tree rather than by reading the pattern text, because
  ``(?:(?:a+))+`` is the same hazard written to defeat a substring search.
* **alternation inside an unbounded repeat** — ``(a|aa)+``, which backtracks
  exponentially for the same reason whenever two branches can match the same
  text. Deciding *whether* two branches overlap is the expensive question, so
  this refuses the shape outright rather than answering it. That costs
  ``(?:[A-Z]|[0-9])+``, which is refused despite being harmless — and which is
  ``[A-Z0-9]+`` written the long way, so the refusal points at the better
  spelling.
* **backreferences** — ``\\1``, ``(?P=x)``. They force the engine into
  backtracking that no static bound covers, and nothing a decoder needs to
  express requires one.
* **more groups, or more length, than a decoder needs** — a bound rather than a
  judgement. A segment that needs 40 groups is not a segment.

Refusal is conservative by design: it rejects some patterns that would in fact
have run quickly. That is the correct direction. The cost of a wrong refusal is
an author rewriting a pattern; the cost of a wrong acceptance is a rebuild that
never finishes.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

#: The longest pattern a segment may carry. Generous against the hand-written
#: ones in this repository (the longest is ~300 characters) and short enough
#: that a pattern nobody could read is refused rather than stored.
MAX_PATTERN_LENGTH = 2000

#: The most capture groups one segment may declare. A segment binds groups to
#: attributes and there are 50 attributes in the vocabulary, so a pattern
#: wanting more than this is not describing one shape of description.
MAX_GROUPS = 40

#: The longest row this pattern will ever be run against. Enforced by the
#: executor rather than here, and stated here because it is half of the safety
#: argument: bounded input plus bounded ambiguity is what makes the worst case
#: finite. A description longer than this is truncated for matching, and the
#: executor records that it was.
MAX_INPUT_LENGTH = 512


class UnsafePattern(ValueError):
    """A pattern that will not be run, with the sentence saying why."""


def _parser() -> Any:
    """Python's own regex parser, or nothing.

    Imported through a guarded private name on purpose. Walking the parse tree
    is the only honest way to see nesting — every textual approximation can be
    written around — and there is no public API for it. If a future Python
    moves it, :func:`check_pattern` **refuses everything** rather than passing
    everything: a safety check that silently stops checking is worse than one
    that stops the build, and the test suite says so out loud.
    """
    try:
        import re._parser as parser  # type: ignore[import-not-found]  # noqa: PLC0415
        return parser
    except ImportError:  # pragma: no cover — Python 3.11 ships it
        try:
            import sre_parse as parser  # type: ignore[no-redef]  # noqa: PLC0415
            return parser
        except ImportError:
            return None


def check_pattern(pattern: str) -> None:
    """Raise :class:`UnsafePattern` unless this pattern is safe to store.

    Compiles it too, so a pattern that passes here is one the executor can
    certainly run.
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise UnsafePattern(
            f"the pattern is {len(pattern)} characters, over the limit of "
            f"{MAX_PATTERN_LENGTH}. A segment describes one shape of "
            f"description; this is describing several.")
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise UnsafePattern(f"the pattern does not compile: {e}") from e
    if compiled.groups > MAX_GROUPS:
        raise UnsafePattern(
            f"the pattern declares {compiled.groups} groups, over the limit of "
            f"{MAX_GROUPS}.")

    parser = _parser()
    if parser is None:  # pragma: no cover — see _parser
        raise UnsafePattern(
            "this Python does not expose the regex parser, so the pattern "
            "cannot be checked for catastrophic backtracking. Patterns are "
            "refused rather than run unchecked.")
    try:
        parsed = parser.parse(pattern)
    except re.error as e:  # pragma: no cover — compiled above
        raise UnsafePattern(f"the pattern does not parse: {e}") from e

    _walk(parsed, quantified=False)


#: Opcode names, compared as strings rather than imported as constants: the
#: constants live beside the parser behind the same private name, and a
#: comparison by name works across both spellings of it.
_REPEATS = {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}
_UNBOUNDED = 4294967295  # re's MAXREPEAT


def _walk(node: Iterable[Any], *, quantified: bool) -> None:
    """Refuse an unbounded repeat nested inside another one.

    ``quantified`` says whether everything below this point is already inside
    an unbounded repeat. One more unbounded repeat under that is the
    exponential shape, wherever the grouping puts it.
    """
    for item in node:
        try:
            opcode, argument = item
        except (TypeError, ValueError):  # pragma: no cover — literals
            continue
        name = getattr(opcode, "name", str(opcode))

        if name in _REPEATS:
            _minimum, maximum, body = argument
            unbounded = maximum >= _UNBOUNDED
            if unbounded and quantified:
                raise UnsafePattern(
                    "one unbounded repeat is nested inside another (the "
                    "'(a+)+' shape), which backtracks exponentially on a line "
                    "that nearly matches. Rewrite the inner part so only one "
                    "of the two is unbounded.")
            if unbounded and _contains_branch(body):
                raise UnsafePattern(
                    "an alternation sits inside an unbounded repeat (the "
                    "'(a|aa)+' shape). Where two branches can match the same "
                    "text that backtracks exponentially, and deciding whether "
                    "they can is the expensive question — so the shape is "
                    "refused rather than answered. A repeated alternation of "
                    "single characters is a character class: write "
                    "'[A-Z0-9]+' rather than '(?:[A-Z]|[0-9])+'.")
            _walk(body, quantified=quantified or unbounded)
        elif name == "SUBPATTERN":
            # (group_number, add_flags, del_flags, body)
            _walk(argument[3], quantified=quantified)
        elif name in {"ATOMIC_GROUP", "GROUPREF_EXISTS"}:
            body = argument[-1] if isinstance(argument, tuple) else argument
            if isinstance(body, Iterable) and not isinstance(body, (str, bytes)):
                _walk(body, quantified=quantified)
        elif name == "BRANCH":
            for branch in argument[1]:
                _walk(branch, quantified=quantified)
        elif name in {"ASSERT", "ASSERT_NOT"}:
            _walk(argument[1], quantified=quantified)
        elif name in {"GROUPREF", "GROUPREF_IGNORE"}:
            raise UnsafePattern(
                "the pattern uses a backreference. Backtracking through one "
                "has no static bound, and nothing a decoder needs to express "
                "requires it.")


def _contains_branch(node: Iterable[Any]) -> bool:
    """Whether an alternation appears anywhere under this node.

    Recursive rather than a check of the immediate children: ``(?:x(?:a|aa))+``
    hides the branch one level down and is the same hazard.
    """
    for item in node:
        try:
            opcode, argument = item
        except (TypeError, ValueError):
            continue
        name = getattr(opcode, "name", str(opcode))
        if name == "BRANCH":
            return True
        if name in _REPEATS:
            if _contains_branch(argument[2]):
                return True
        elif name == "SUBPATTERN":
            if _contains_branch(argument[3]):
                return True
        elif name in {"ASSERT", "ASSERT_NOT"}:
            if _contains_branch(argument[1]):
                return True
    return False
