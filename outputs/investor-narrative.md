# PIE — Product Intelligence Engine

A distributor of industrial cutting tools makes its margin at the quote. A request
arrives as a WhatsApp line, a terse email, a scanned PDF or a photograph of a
handwritten note, and between that message and a priced quotation sits a translation
nobody has written down: what product is this, do we carry it, does something else
substitute, what does it cost us, what should we charge. In a business carrying
fifteen thousand items across a dozen manufacturers, it runs on one person's memory.
It is slow, it does not scale, and when they are away the quote is wrong or late.

The ERP does not solve this and is not built to. An ERP records that a transaction
happened: a SKU string, stock, a last cost, a last price, a customer, a tax code. A
quote needs a different category of fact: what the product physically is, whether
another substitutes for it, whether the price holds margin, whether this customer
justifies the concession. On the item master measured here, 1.4% of item names carry
a recognisable grade token and there is no grade field at all — yet grade is what the
technical decision runs on. PIM describes products but does not decide; CPQ issues a
quote but assumes somebody worked out what goes on it; pricing platforms optimise a
number once the line exists. Each leaves the same gap, and what fills it today is a
spreadsheet beside somebody's recollection.

The insight is that the bottleneck is not recording the order. It is turning an
unstructured request into a technically correct, commercially actionable quote.

PIE is that translation layer. It parses a manufacturer's product code
deterministically, decodes geometry and grade against versioned data packs, resolves
the line by exact identity where one exists and ranks equivalents where one does not,
joins it to the distributor's own stock and landed cost, and returns a recommended
price with its margin position. It replaces nothing in the stack. It supplies the
layer none of the incumbents own.

Being deterministic first is a commercial choice, not a technical limitation. Every
number comes from arithmetic over source records, and every emitted field carries its
provenance, a confidence and the character span it came from. The AI layer sits above
that, dashed rather than solid: it reads computed facts and phrases them, absorbs the
ambiguous edge of a request, and may never produce a figure of its own — any number it
states that cannot be traced to a supplied fact causes the response to be rejected in
favour of the deterministic reading. The AI ships switched off, and the system is
useful in that state. A quote a distributor cannot audit is one they will not send,
and an engine that invents a corner radius is worse than no engine.

The wedge is the quote desk, because that is where the pain is daily and the value
legible inside one session. From there the same substrate extends along a path the
architecture already supports: product intelligence, pricing, margin management,
watchers over the commercial book.

The asset compounds specifically rather than generically. Each manufacturer's naming
system is reverse-engineered once into a versioned pack — eleven families and 6,717
rows decoded at full coverage today — and the engine holds no manufacturer knowledge
at all, enforced rather than asserted. A new manufacturer is new data against unchanged
code; a new category costs one added decoder. That structure is published nowhere. Not
a data-scale claim but a specific artefact that took specification work to build.

ERP records the transaction. PIE understands the product and helps decide what
should happen.
