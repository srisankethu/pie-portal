"""The Quote Diagnosis Engine: is this quoted price commercially sensible?

Six questions per quote line — what was observed, what supports it, what looks
unusual, whether there is a margin opportunity, what else could explain it, and
how confident the platform is — answered deterministically from evidence that
was **knowable when the quote was written**.

The package is layered in the order the pipeline runs, and each layer is pure:

``evidence``     what was knowable at a moment, normalised and classified
``comparables``  which of it is comparable, on the customer axis and the peer axis
``baselines``    what the price and the cost should have been, outliers trimmed
``rules``        which diagnosis codes fire, and how strong the evidence is

Nothing here calls a model, touches the network, or reads the wall clock: a
diagnosis is a function of its inputs, and the same inputs must produce the same
bytes. The database seam is the caller's; these are functions over rows.
"""
