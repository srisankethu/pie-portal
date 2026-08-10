"""Why a figure is missing — as a typed reason rather than a paragraph.

Every view in ``insight/`` already refuses in prose: ``stock`` will not print
weeks of cover, ``supply`` will not measure lateness against a promise nobody
made, ``dependency`` will not claim to know what a customer buys elsewhere. That
discipline is the reason the numbers beside them are worth reading, and none of
it changes here.

What changes is that a reader could not tell those refusals apart. They render
identically, so a screen full of them reads as "this platform cannot do much"
when four of the entries in this codebase are **jobs somebody could finish this
week** and the rest are answers. A refusal that is a task and a refusal that is
a limit want different responses, and only the writer of the sentence knew
which was which.

So each entry carries a ``kind``. Five, because five is what the existing
refusals actually turned out to be — this list was derived by reading every
refusal in the package, not designed in advance:

``PERMANENT``    No data would fix it. Either an epistemic limit (we see what a
                 customer buys here and nothing of what they buy elsewhere) or a
                 grain mismatch (stock is a level *now*; a bill's supplier
                 belongs to the bill, so an item bought from two suppliers has
                 no single one). Perfect data does not make these answerable.
``COLLECTABLE``  Somebody has to record something. A reorder level left blank, a
                 promised delivery date nobody types, a sync not yet run. This
                 is the worklist, and it is the reason the field exists.
``BUILDABLE``    The data exists, or can be bought. Reading purchase-order lines
                 costs one API call per order; warehouse-grain stock is on a
                 Zoho plan this pull does not read. Engineering, not clerical.
``TRANSIENT``    Resolves itself. A quarter too young to project from, a rebate
                 period with too few bills in it yet. Nobody should act on
                 these; putting "wait a fortnight" on a worklist is noise.
``WITHHELD``     Computed, and deliberately not shown to *this* reader. Cost and
                 margin for a salesperson. The number exists and is correct; the
                 permission model is doing its job. Filing this under
                 ``PERMANENT`` would invite somebody to "fix" it.

The distinction that pays for the whole module is ``PERMANENT`` versus
``COLLECTABLE``. Everything else is there because lumping it into one of those
two would have been a lie.

**Two shapes, deliberately left alone.** Half the refusals in this package are
``{"series", "reason"}`` and half are ``{"what", "why"}``. That is one concept
with two vocabularies and it should be one — but every renderer on the client
reads the keys it was given, so unifying them is a change to the views rather
than to the reasons they carry. ``kind`` is added to both and the duplication is
recorded here rather than quietly fixed halfway.
"""
from __future__ import annotations

#: No data would fix it — an epistemic limit or a grain mismatch.
PERMANENT = "PERMANENT"
#: Somebody has to record something. This is the worklist.
COLLECTABLE = "COLLECTABLE"
#: The data exists or can be bought; wiring it is engineering work.
BUILDABLE = "BUILDABLE"
#: Resolves on its own as the period runs or transactions accumulate.
TRANSIENT = "TRANSIENT"
#: Computed and correct, withheld from this reader by the permission model.
WITHHELD = "WITHHELD"

#: The closed set. Anything else is a typo, and the test in
#: ``tests/decision_platform/test_absence_kinds.py`` says so rather than letting
#: a mistyped kind render as an unclassified refusal.
KINDS = frozenset({PERMANENT, COLLECTABLE, BUILDABLE, TRANSIENT, WITHHELD})

# The words a screen prints for each kind live in `platform/kit.tsx`, beside the
# one component that renders them — deliberately not here. This module owns the
# taxonomy; the client owns the wording, which is what "the client formats; it
# does not calculate" means. A label map on both sides of the wire would be two
# sources for one string, and the one nobody updates is always the far one.
