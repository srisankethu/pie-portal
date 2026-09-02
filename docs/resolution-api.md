# The resolution API

`POST /api/v1/resolve` turns one line of a customer's own words into a
structured product resolution — or into an explicit, well-formed abstention that
says which kind of "no" it is.

It exists to make one claim testable. PIE's argument is that deterministic
product identity is *infrastructure* — something an ERP, a CPQ or a quoting desk
calls, rather than an application somebody logs into. Until there was an
endpoint, that was an assertion. This is the endpoint.

- **Machine-first.** An API key, no session, no cookie, no browser.
- **Deterministic.** No model is called on this path, and no number in the
  response is produced by one. The same text, against the same catalogue
  ruleset, returns the same answer.
- **Provenanced.** Every decoded fact carries where it came from, how sure the
  engine was, and — where one exists — the characters it was read out of.
- **Willing to abstain.** A correct "I do not know" outranks a confident wrong
  answer, and the five ways of not knowing are five different answers.

The machine-readable contract is served, unauthenticated, at
**`GET /api/v1/resolve/openapi.json`**. It is generated from the routes
themselves, so it cannot drift from what the server does; this page is the prose
that goes with it.

---

## Getting a key

Keys are minted by an **owner**, signed in to the platform. A key cannot mint
another key — a machine credential that could issue machine credentials turns
one leak into a permanent foothold.

```bash
curl -X POST https://<host>/api/v1/api-keys \
  -H "Authorization: Bearer <owner session token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme CPQ (sandbox)", "role": "SALESPERSON"}'
```

```json
{
  "key_id": "9f2c7a41d0e35b8146ac91b7",
  "name": "Acme CPQ (sandbox)",
  "role": "SALESPERSON",
  "secret_hint": "kQ7x",
  "rate_limit_per_minute": 60,
  "active": true,
  "secret": "pie_9f2c7a41d0e35b8146ac91b7_Xb3…kQ7x",
  "detail": "Copy this key now — it is stored as a hash and cannot be shown again."
}
```

The secret is returned **once**. Only a PBKDF2 hash is stored, so there is no
"show key" endpoint and adding one would defeat storing a hash at all. Lost a
key? Revoke it (`DELETE /api/v1/api-keys/{key_id}`) and mint another.

`GET /api/v1/api-keys` lists what exists, with a four-character hint and
`last_used_at`, and never a secret.

### The `role` field is the important one

A key resolves to an ordinary **principal** carrying the role it was minted at,
and every projection downstream acts on it exactly as it does on a signed-in
person's. This is not a convenience — it is the mechanism by which the
platform's disclosure rules reach the API without being written a second time.

| role | what a resolution's `commercial` block contains |
|---|---|
| `SALESPERSON` (default) | Prices this customer has actually paid, the quantity band, whether approval is needed. **No cost, no margin, and no rule whose boundary is either.** |
| `SALES_MANAGER`, `OWNER` | The above plus `economics` and `position` — unit cost, margin, the relationship's standing. |

The default is the narrowest role deliberately. An integration that resolves
nomenclature needs no economics at all, and a key that inherited whoever minted
it would hand a partner's server the whole book's cost basis because nobody
thought about the field.

**What a `SALESPERSON` key gets instead of a withheld rule.** A rule that fired
is a predicate, and a caller who can walk `proposed_price` to the value where
the answer changes has recovered the number that rule tests against. So rules
whose boundary is cost are withheld outright and one fixed `APPROVAL_REQUIRED`
is substituted — the control survives, the boundary does not. The reasoning is
in `CLAUDE.md` §1 and the code is `commercial/quote_service._project_exceptions`,
which is the same function the Quote Builder's own screen goes through.

---

## Resolving a line

```bash
curl -X POST https://<host>/api/v1/resolve \
  -H "Authorization: Bearer pie_9f2c…kQ7x" \
  -H "Content-Type: application/json" \
  -d '{"text": "2576285 x 10 nos", "customer_ref": "Pitti Engineering"}'
```

`X-API-Key: pie_…` is accepted as well, because several ERP middlewares cannot
set an `Authorization` header on an outbound webhook. Same credential, one door,
two handles.

### The request

| field | required | meaning |
|---|---|---|
| `text` | yes | One enquiry line, up to 512 characters. Send one line per request — a splitter that guessed line boundaries would be making a decision this endpoint could not explain afterwards. |
| `company_id` | when the organization has several | Which connected company's catalogue answers. Each company decodes its own item master, so this decides what the text is resolved *against*. With one company it is optional and ignored; with several, omitting it is a **422** listing the ids (below). |
| `customer_ref` | no | The customer, as your system names them. Used to read this organization's confirmed code mappings, and — when a price is supplied — to price against that relationship's history. An unknown name resolves the text alone. |
| `quantity` | no | For the quantity band. Ignored without `proposed_price`. |
| `proposed_price` | no | Unit price you intend to quote. Supplying it adds the `commercial` block; omitting it keeps this a pure nomenclature call. |

### The answer

```json
{
  "api_version": "v1",
  "input": { "text": "2576285 x 10 nos", "length": 16 },
  "status": "RESOLVED",
  "abstention": null,
  "resolution": {
    "record_id": "2576285",
    "description": "CNMG 120408 - TN2000",
    "brand": "WIDIA",
    "grade": "TN2000",
    "relationship": "EXACT",
    "equivalence_score": null,
    "record_confidence": 0.97,
    "input_span": { "start": 0, "end": 7, "text_ref": "input.text" },
    "attributes": [
      { "name": "product_family", "value": "turning_insert",
        "provenance": null, "confidence": null,
        "read_from": null, "span": null },
      { "name": "iso_shape", "value": "C", "provenance": "GRAMMAR_EXACT",
        "confidence": 0.97, "read_from": "C",
        "span": { "start": 0, "end": 1, "text_ref": "product.description" } },
      { "name": "corner_radius_mm", "value": 0.8, "provenance": "GRAMMAR_EXACT",
        "confidence": 0.97, "read_from": "08",
        "span": { "start": 9, "end": 11, "text_ref": "product.description" } },
      { "name": "insert_polarity", "value": "negative", "provenance": "DERIVED",
        "confidence": 0.9, "read_from": null, "span": null },
      { "name": "thickness_mm", "value": 4.76, "provenance": "LOOKUP_CONFIRMED",
        "confidence": 0.95, "read_from": "04", "span": null }
    ]
  },
  "alternatives": [ … ],
  "identity_proposal": null,
  "commercial": null,
  "engine": {
    "catalogue_available": true,
    "ruleset_checksum": "f67131512eb97513",
    "company": "cn_7f21a9",
    "input_semantics": "IDENTITY",
    "outcome": "AUTO_MATCH",
    "retrieval": null
  },
  "notes": []
}
```

That is the *identity* shape: the caller named a manufacturer number, so
`relationship` is `EXACT`, `equivalence_score` is null, and `input_span` points
at the characters the code was found in.

**`status: "RESOLVED"` means the engine selected a record, not that it was
certain.** `relationship` is how strong that selection is — `EXACT` (identity),
`TECH` and `COMPAT` (the two equivalence bands this organization sets), and
`POSSIBLE` (below the compatibility band — *or* above it on a comparison the
engine could not complete). Read `relationship` and `equivalence_score`
together and decide; a `POSSIBLE` is a suggestion to check, not a part to quote
unseen.

**`comparison_complete: false` is why a high score can still be `POSSIBLE`.**
The engine skips a dimension neither side carries rather than penalising it, so
a request it could not decode is compared against nothing and every candidate
scores near the ceiling — a ball bearing scored 1.0 against a carbide insert.
A candidate whose comparison did not cover everything the request specified is
capped at `POSSIBLE` however high it scored, and never auto-selected. Treat
`comparison_complete: false` as "the score is a ceiling, not a fit": the
relationship is what the engine is willing to claim, and this field says how
much was actually checked to support it. The line is
drawn where the Quote Builder draws it, deliberately — an API that abstained
where the screen answered would make "did PIE resolve this?" depend on who
asked.

**`found_by: "retrieval"` is an option, never a match.** On a requirement the
engine could not rank — a series named in words, a request with no ISO code in
it — the catalogue is also searched by *description*: the records whose text
reads most like the line are compared by the engine and, where its gates do
not reject them, listed among `alternatives` with `found_by: "retrieval"`,
`relationship: "POSSIBLE"` and a null `equivalence_score`. Nothing ranked such
a record and nothing selects it: `resolution` is never one of them, and a line
whose only candidates were retrieved abstains with `AMBIGUOUS`. Every
alternative the engine ranked says `found_by: "ranking"`. A third value,
`found_by: "confirmed_code"`, is a near miss of a code this customer confirmed
means this product — `confirmed_code` carries the code — offered under the
same terms: the exact code resolves as an identity; a line that is almost it
gets the record as an option, for that customer only. A fourth,
`found_by: "prior_choice"`, is a record a person put on a quote for this
customer when they asked for words close to these — `prior_phrase` carries
the words. A past choice, never an identity: the engine does not read it, and
the same words may honestly mean a different product this time. `engine.retrieval`
says whether that search ran — which model id, over how many records, offering
how many — and is null where it did not, which reads as "not searched" and
never as "nothing near". The model is a deterministic hashed n-gram embedding
built beside the catalogue, so the same catalogue and model id retrieve the
same records; see `docs/per-company-catalogues.md` §12.

Free text takes the other shape. `"cnmg 1204 08 tn2000 - 10 nos"` is a
requirement rather than an identifier, so `input_semantics` is `REQUIREMENT`,
`relationship` is `TECH` or `COMPAT` according to this organization's
equivalence bands, `equivalence_score` carries the score, `explanation` says
what matched — and `input_span` is `null`, because no catalogue code appears
literally in that text.

**Every abstention has the same keys.** `resolution` is null exactly when
`status` is `ABSTAINED`, and that is the only structural difference — so you
parse one document type rather than branching before you can read anything.

#### Reading provenance, confidence and spans

Three things that look similar and are not:

- **`equivalence_score`** — how well this record answered *your request*. Null
  for an exact identity, where the question does not arise. It is a score under
  *this organization's* equivalence policy, not a property of the two products:
  two organizations may legitimately band the same pair differently.
- **`record_confidence`** — how well the engine decoded *this record's own*
  description. A property of the catalogue, not of your call.
- **`attributes[].confidence`** — the same, per decoded slot.

They are kept apart on purpose. A single fused "confidence" would mean none of
the three, and it is exactly the kind of figure a downstream system rounds and
then quotes back at somebody.

**`span` says which string it indexes into, in every entry.** A
`product.description` span points into `resolution.description`; an
`input_span` points into the text you sent. They are different strings, and a
caller that conflated them would highlight arbitrary characters of the
customer's own words. An `input_span` is present only when the matched code is
*literally* in your text — a nearly-right span looks authoritative while
pointing at the wrong word, so the honest answer is `null`.

A slot with `"span": null` was **derived** — from a lookup table, an explicit
column, an implication of another slot — rather than read out of a substring.
That is a real and different provenance, not a missing one; `provenance` still
names which kind (`DERIVED`, `LOOKUP_CONFIRMED`, `LOOKUP_STRONG`, …).

A slot with `"provenance": null` is narrower again: the pack recorded no
provenance for it at all. `product_family` is the live case — it is decided by
the family router rather than by a grammar slot, so nothing in the decode wrote
a span or a confidence for it. Null rather than a stand-in label, because a
made-up provenance would be an audit trail the engine never produced.

A slot the engine decoded to nothing is **absent from the list entirely**,
because a null value would read as "decoded and found nothing", which is a
different claim from "never decoded".

---

## The five abstentions, and why they are five

This is the part worth reading twice. `status: "ABSTAINED"` is an answer, and
`abstention.reason` says which of five:

| reason | HTTP | `is_evidence_about_the_input` | means | your next move |
|---|---|---|---|---|
| `NO_MATCH` | 200 | `true` | The catalogue was searched and holds nothing like this. | Record the gap. |
| `AMBIGUOUS` | 200 | `true` | Several records answer it and the text does not choose. They are in `alternatives`, ranked. | Choose one. |
| `NEEDS_CONFIRMATION` | 200 | `true` | The catalogue holds this exact code, but nobody has confirmed that *this customer's* code means it. | Confirm it — see below. |
| `CATALOGUE_UNAVAILABLE` | 503 | `false` | This company has no catalogue built. **Nothing was asked.** | Retry; do not record anything. Have an owner build it on Setup → Decoded catalogue. |
| `ENGINE_ERROR` | 503 | `false` | The engine was asked and the ask failed. | Retry; do not record anything. |

Each of the three `200` reasons has a *different* next move, which is the test
for whether a reason earns its own name rather than being folded into a
neighbour.

**Never record one of the last two as "unknown part".** They say nothing
whatever about the product; recording them writes a deployment problem into your
master data, permanently, and it will look exactly like a catalogue gap
afterwards. The distinction is drawn three ways — a distinct reason, a boolean
your code can branch on, and a 5xx — because it is the one an integrator is most
likely to flatten.

The platform draws it internally too: `pie_service.catalog_available` exists
precisely to separate "the pack does not cover this item" from "nobody asked the
pack", and this endpoint carries that separation to the wire rather than
collapsing both into a null.

### The sixth answer: naming the company

Not an abstention, because nothing was asked and the caller can fix it: an
organization reading several companies' books gets a **422** when the request
names none.

```json
{
  "detail": {
    "message": "This organization reads more than one company's books, and each has its own product catalogue. Name the company this line is for (company_id): cn_7f21a9 (SLS Engineers), cn_b40c12 (4U Precision)",
    "companies": [
      { "connection_id": "cn_7f21a9", "label": "SLS Engineers" },
      { "connection_id": "cn_b40c12", "label": "4U Precision" }
    ]
  }
}
```

Send `company_id` and retry. The refusal exists because each company decodes its
own item master: answering from an unspecified catalogue would be a provenanced
answer about possibly the wrong company's product, which is worse than no answer
and looks exactly like a right one. An id that is not this key's organization's
gets the same 422, listing only the companies that are — a company you may not
see is a company this endpoint will not confirm exists.

An organization with **one** company answers with no `company_id` at all, so a
single-entity integration needs no change.

---

## Confirming what a customer's code means

This is the `NEEDS_CONFIRMATION` abstention above. The engine found your
customer's own item code in the catalogue but declined to assert it: the code
exists, but nobody has confirmed that *this customer's* `7781` means that
product. The document then carries an `identity_proposal`:

```json
"identity_proposal": {
  "record_id": "2001174",
  "confirmable": true,
  "detail": "The catalogue holds this exact code, but nobody has confirmed…"
}
```

Answering that question is a durable fact worth keeping:

```bash
curl -X POST https://<host>/api/v1/resolve/confirm \
  -H "Authorization: Bearer pie_9f2c…kQ7x" \
  -H "Content-Type: application/json" \
  -d '{"text": "7781", "customer_ref": "Pitti Engineering", "record_id": "2001174"}'
```

**The gate is narrow, and it is a correctness boundary rather than bookkeeping.**
Only the record the engine *itself* proposed may be confirmed. Sending a
different `record_id` — including one you legitimately picked off
`alternatives` — records nothing and answers `{"recorded": false, "reason": …}`.

The reason is that a confirmed mapping is *asserted* identity. Afterwards the
engine resolves that code authoritatively and will derive a requirement from the
record and rank equivalents off it. A scored equivalence suggestion is a
substitution on **one quote** — the engine put it a tolerance band away and said
so. Filing it here would make an approximate match exact by storage, and the
next "same as their 7781 but 12 mm" would compose two tolerance bands into a
wrong part with a defensible-looking explanation attached. `A ≈ B` within band
and `B ≈ C` within band is not `A ≈ C`.

The line is re-resolved server-side rather than trusting a proposal echoed back
in the request; a caller that could name its own proposal could name any record,
and the gate would be a field in a request body.

Refusals are `200` with `recorded: false`, never a 4xx that distinguishes "not
the proposal" from "no proposal at all" — that would tell a caller which half of
a guess was right.

---

## Rate limits

Each key carries an allowance, per minute, per key — so one partner's runaway
loop cannot spend another integration's. Every response carries:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 58
X-RateLimit-Window-Seconds: 60
```

Over the limit is `429` with `Retry-After`. Pace off the headers rather than
discovering the limit in production.

**It is a speed bump, and is documented as one.** The counters are in-process,
so with several replicas behind a load balancer the effective limit is the
configured one times the number of replicas. What it buys is that a
price-bisection sweep is slow enough to show up in a log, not that a quota is
metered.

Failed authentications are bounded separately — ten a minute against any one
key id, after which that id is refused for the rest of the window even with the
right secret. A deployment still holding a rotated key will trip this; mint a
new one rather than retrying.

---

## Versioning

The path carries the major version (`/api/v1`) and every document echoes
`api_version`. That field moves when a field changes meaning or disappears —
never when one is added, because an integrator that breaks on a new key was
already broken. **Tolerate unknown fields.**

`engine.ruleset_checksum` is a different kind of version and worth storing
alongside anything you persist: it is the checksum of the catalogue ruleset that
produced the answer, and it is the one fact that explains, months later, why the
same text resolved to a different product than it does today.

---

## How accurate is it?

**Deliberately not stated here.** A number restated in two places drifts, and
the second copy is always the one somebody quotes.

The measurement lives in pie-parser: `tools/eval_rfq.py` over
`eval/rfq_benchmark/`, which reports **precision**, **coverage**, **abstention
rate** and **wrong-confident rate** per arm — the deterministic engine, a
normalise-and-fuzzy baseline as the floor, and an LLM arm — with bootstrap
confidence intervals, sliced by channel and ambiguity class. Read
`eval/rfq_benchmark/README.md` for what each rate is and why precision and
coverage must be read together.

**The figure is pending.** At the time of writing that harness runs on a
hand-written seed set built to exercise the harness itself, and its own README
says in as many words that no rate computed over those cases is a result or may
be quoted as one — the bootstrap interval on precision is about ±30 points. The
real corpus of inbound RFQ text is being collected separately; when it lands,
the report produces the number and this section will cite it rather than restate
it.

What *is* measured today is the parser against the manufacturer's own
nomenclature corpus — the clean case — which says nothing about a typo, an OCR
artefact or "same as last time". Quoting the clean-case figure as the API's
accuracy would be precisely the overstatement both repositories are written
against.

---

## What this endpoint does not do

- **It does not price.** Supplying `proposed_price` returns the platform's
  assessment *of the price you named*; it never proposes one. Prices, margins
  and thresholds are computed by `commercial/`, deterministically, from
  persisted rows.
- **It does not split documents.** One enquiry line per request.
- **It does not learn from what you send.** The only durable write this API can
  make is a confirmed code mapping, through the gate above, on your explicit
  request.
- **It does not call a model.** Not on this path, not for a fallback, not for a
  tie-break.

## Related

- `docs/architecture.md` — how resolution fits the rest of the platform.
- `docs/connectors.md` — the ERP adapters on the ingestion side.
- `CLAUDE.md` §1 — the disclosure invariants this endpoint inherits.
- pie-parser's `README.md` — the engine, the packs, and the family-by-family
  delivery status.
