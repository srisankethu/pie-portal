# 13 — Confidence and input completeness: what `row_confidence 0.00` actually says

`01-application-engineering.md` §3 records that running the engine over all
15,028 Zoho item names scores `row_confidence` **0.00** on every row, carrying
`GRADE_MISSING` and `MANUFACTURER_UNKNOWN`.

That has been read two ways:

1. **"The engine cannot decide on real data."** The catalogue works on its own
   corpus and collapses on the business's actual records. The decoding thesis is
   not real.
2. **"The engine correctly abstains on an input that lacks the evidence."** The
   score is doing its job. The input is missing a field, and saying so is the
   right answer.

They have opposite implications for whether this product works, and until now
nobody had separated them. This document separates them, and the answer is
**(2), demonstrated rather than argued** — with a cost that reading (2) tends to
leave out, recorded in §5.

Measured 2026-08-23 on the pinned pie-parser submodule
(`kennametal_widia@0.10.0`, ruleset checksum `6be04ced55b4a239`) over
`corpora/kmt_zcnc_2026-07_nomenclature.csv`, 6,717 data rows — and replicated
2026-08-25 against a **different pack build** (`zcnc@0.10.0`, checksum
`f67131512eb97513`), which reproduced every figure below to the digit. §6 is
that replication, and the repository defect it had to clear first.

---

## 1. Why this does not use the item master

The obvious experiment is to run the master and stare at it. It cannot answer
the question, because a master run confounds two differences at once:

- The master **lacks fields** the corpus has. It has no grade column at all, and
  a prior measurement dated 2026-08-09 records 6,146 of its rows carrying no
  manufacturer either.
- The master **contains a different population**. It holds item names the
  catalogue was never built to decode — an EMUGE screwdriver, an `M3X11` screw —
  which is what §3's contamination finding is about.

A 0.00 could be either. One run over one dataset cannot tell you which, which is
precisely why the number has supported two readings for a fortnight.

So suppress the variable instead of swapping the dataset. What makes the master a
poorer input *is* the field it lacks, so model the master side by re-running
**the same 6,717 corpus rows through the same pipeline with the grade column
suppressed** — `RawRecord.grade` set to `None`, `payload` emptied with it so the
column cannot re-enter through the unmapped-columns bag — and compare against the
full-input run of those same rows, pairwise by `record_id`.

Population, ordering, pack, engine version, every threshold and every validator
are held identical **by construction**, not by assertion. Any difference between
the two runs is caused by the absent column and by nothing else.

`scripts/measure_input_completeness.py` is the run. It is read-only and
deterministic — two consecutive runs produce byte-identical JSON.

```bash
./scripts/setup_pie_parser.sh          # or: export PIE_PARSER_ROOT=<checkout>
cd backend && python3 ../scripts/measure_input_completeness.py
```

**Nothing was tuned to produce this result, and that is the point rather than an
omission.** A scorer adjusted to emit non-zero confidence over `GRADE_MISSING`
would collapse the measured gap to nothing while changing nothing about the item
master — `CLAUDE.md` §1's "absence of evidence read as a pass", wearing a
provenance stamp. `backend/tests/test_confidence_config_unchanged.py` pins the
weights, the penalties, the `critical_slots` tuple, the validator inventory, and
the behaviour itself; the one-token edit that would fake this result turns two of
its assertions red.

---

## 2. The paired confidence distribution

| `row_confidence` | Full input | Grade suppressed |
|---|---|---|
| 0.9700 | 5,538 (82.45%) | 0 |
| 0.8730 | 48 (0.71%) | 0 |
| 0.8245 | 1,123 (16.72%) | 0 |
| 0.7420 | 5 (0.07%) | 0 |
| **0.0000** | **3 (0.04%)** | **6,717 (100.00%)** |
| **Mean** | **0.9444** | **0.0000** |

Suppressing one column reproduces §3's finding exactly: 0.00 on every row.
Because the suppressed side is uniformly zero, the paired per-row delta is just
the full-input distribution — every row loses its entire score, and no row loses
part of one.

The three rows already at 0.00 in the full run are a **natural control**:
`1293468`, `1977630`, `2031737` are the corpus's only rows with a blank Grade
cell. They are master-shaped without anything being done to them, and they score
0.00 in the unmodified run. The suppression is not introducing a new behaviour;
it is widening a behaviour the corpus already exhibits on the rows that share the
master's defect.

---

## 3. The flag census

| Flag | Full input | Grade suppressed |
|---|---|---|
| `GRADE_MISSING` | 3 | **6,717** |
| `MANUFACTURER_UNKNOWN` | 330 | **6,717** |
| `TEXT_AMBIGUOUS` | 2,975 | 3,796 |
| `TRUNCATED_EXPLICIT` | 1,057 | 1,057 |
| `TRUNCATED_HARD` | 71 | 71 |
| `REPAIRED` | 53 | 24 |
| `GRADE_CONTAMINATED` | 29 | 0 |
| `UNRESOLVED_TOKENS_PRESENT` | 26 | 0 |

Both of §3's flags reproduce at full census from the single suppression, and
each has a mechanism that can be read off the engine:

- **`GRADE_MISSING`** is raised by `GradeClean` (`engine/normalize.py`) whenever
  the column is empty. It is a statement about the input cell, not about the row.
- **`MANUFACTURER_UNKNOWN`** is raised when no claim signature matches. All three
  claims this pack declares — `C-KMT`, `C-WIDIA`, `C-STELLRAM` — are
  **grade-pattern claims with no series tokens**. There is no channel by which
  this pack could name a manufacturer from a description alone. On a grade-less
  input the flag is therefore a *structural certainty*, not a judgement: it
  carries no information about the item, only about the input schema. Anything
  that reads `MANUFACTURER_UNKNOWN` on the master as evidence about the product
  is reading the absence of a column.
- **`TRUNCATED_*` are identical on both sides** (1,057 / 71). These are the flags
  that depend only on the description, and they do not move — a useful internal
  check that the two runs really are the same rows.
- `GRADE_CONTAMINATED` → 0 and `REPAIRED` 53 → 24 are the same 29 rows: a repair
  that strips a contamination suffix from the grade cell cannot fire when there
  is no cell. Consequence, not judgement.
- **`TEXT_AMBIGUOUS` rises by 821**, and this one is a genuine property of the
  master-shaped input rather than an artefact. The duplicate census keys on
  `(description, grade)`; with no grade, rows distinguished only by grade become
  indistinguishable. The master has the same problem for the same reason.

---

## 4. The attribution: which part of the gap is the missing field

The gap to attribute is the full mean, 0.9444 → 0.0000.

### 4a. In the full run, the grade column never sets the score

`row_confidence` is `min(family_confidence, critical-slot confidences)` and
`critical_slots` is `("grade",)`. So the grade column can do exactly one of two
things to a row: bind that minimum, or not. Which it did is readable straight off
the emitted `field_meta`, with nothing re-derived:

| | Full input | Grade suppressed |
|---|---|---|
| Score set by the **family route** | 6,714 | 0 |
| Score set by the **grade slot** | 3 | **6,717** |
| Grade provenance `EXPLICIT_COLUMN` | 6,714 | 0 |
| Grade provenance `ABSENT` | 3 | 6,717 |

A present grade scores 0.99 (`EXPLICIT_COLUMN`); a clean family route scores 0.97
(`GRAMMAR_EXACT`). 0.99 never binds against 0.97, so **the grade column
contributes exactly zero to the score of every row that has one.** Its only
effect on `row_confidence` is a veto when it is absent — and the three rows where
it does bind in the full run are the three natural controls, at 0.0.

This is worth stating plainly because it changes what the number means:
`row_confidence` in the full run is a **family-route confidence carrying an
evidence-present veto**, not a measure of how completely a row decoded. It is
described here, not criticised, and no change to it is proposed.

### 4b. The engine's reading of the description did not change

If any part of the gap were the engine's judgement moving, it would show as a
different reading of the same text. It does not — over all 6,717 paired rows:

| Compared | Rows differing |
|---|---|
| `description_norm` | 0 |
| `product_family` | 0 |
| `product_subfamily` | 0 |
| `grammar_id` | 0 |
| `family_rule_id` | 0 |
| `attributes_ext` | 0 |
| Any description-derived slot, **by value** | **0** |

Every one of the 35 description-derived slots — `iso_shape`, `edge_length_mm`,
`corner_radius_mm`, `chipbreaker`, `cutting_dia_mm`, `flute_count` and the rest —
holds an identical value on both sides of every row. The only differences
anywhere in the two outputs are:

- **311 slot-instances differing in per-slot *confidence* only**, all confined to
  the 29 `GRADE_CONTAMINATED` rows, where the full run applies a 0.90 repair
  penalty the suppressed run has no repair to apply. This residual points the
  **wrong way for reading (1)**: mean per-slot confidence on description slots is
  0.918675 with the grade column and 0.919555 without it. The suppressed run
  reads the description marginally *more* confidently.
- **26 `unresolved_tokens` differences, every one prefixed `grade_segment_suffix:`** —
  grade-derived by name.

### 4c. Therefore

**100% of the confidence gap is attributable to the suppressed field. 0% is
attributable to the engine's judgement**, and that zero is measured — an
exhaustive field-level comparison over 6,717 paired rows — not inferred from the
absence of a signal. The residual that does exist is 0.0009 of per-slot
confidence in the *opposite* direction.

The gap is one absent input crossing a `min()`. Reading (2) is correct.

---

## 5. What reading (2) leaves out, and it is the expensive part

"The engine correctly abstains" is true and is not the same as "the master is
fine". A score can be a defensible abstention while the run quietly loses half
its output, and confidence alone would not show it:

| | Full input | Grade suppressed |
|---|---|---|
| Non-null attribute values emitted | 68,000 | 33,753 |
| — from the description | 33,630 | **33,630** |
| — from the grade column | 34,370 | **123** |
| Rows with a named manufacturer | 6,387 | **0** |
| Rows with a named family | 6,717 | 6,717 |
| Rows with full ISO slot fill | 336 | 336 |

**50.4% of the emitted attribute values are lost, and they are not a random
half.** What survives is geometry — shape, diameter, edge length, corner radius,
flute count. What disappears is `material_class`, `grade_system`,
`toughness_index`, `applications`, and most of `coating` — the entire
material-and-application axis, which is exactly the axis the "advise on an
application rather than discount a part number" thesis in `01` §7 depends on.

The two survivals in that table matter as much as the losses. The **family route
is unchanged at 6,717**, and **full ISO slot fill is unchanged at 336** — so the
gate `01` §3 already identified ("ISO slot fill validates itself; a family route
does not") is intact on grade-less input. Everything §3 said was trustworthy
survives the suppression, byte for byte. Everything it said was not trustworthy
survives too, unchanged and equally untrustworthy.

So the correct statement of the finding is: **the 0.00 is not a calibration
defect and there is nothing to fix in the engine. It is a measurement of the item
master, and the only thing that would move it is a grade column on the input
side.** `01` §6 already refuses to infer grade or manufacturer from the item
name; §4a above is the mechanism behind that refusal — the engine has no channel
that could, and manufacturing one would be inventing the evidence whose absence
the score is reporting.

---

## 6. Replication, and the stale pin that made it unrunnable

Re-run 2026-08-25 by the documented route — `./scripts/setup_pie_parser.sh`,
which is to say the submodule at the commit this repository pins, not a
standalone checkout that happens to be lying around. Two things came out of it,
and only the first was expected.

### 6a. The finding replicates on a pack that is not the same artefact

Every number in §2 through §5 reproduces exactly: the paired distribution
(5,538 / 48 / 1,123 / 5 / 3, mean 0.9444 → 0.0000), the whole flag census, the
binding term at 6,714 / 3, the 311 confidence-only slot differences, the
0.918675 → 0.919555 residual pointing the wrong way for reading (1), and the
68,000 → 33,753 attribute count. Two consecutive runs are byte-identical, so
§1's determinism claim holds on this build too.

What makes that worth recording rather than assuming is that the pack changed
underneath it. The original run loaded `kennametal_widia@0.10.0`, checksum
`6be04ced55b4a239`; the replication loaded `zcnc@0.10.0`, checksum
`f67131512eb97513`. Between the two, pie-parser split its pack into `org/` and
`nomenclature/` layers, so the pack the portal loads has a new id, a new path
and a new checksum — and the rules inside it did not move. A result that
survives its own ruleset being re-packaged is a result about the input, which is
what this document claims it is. That is one replication across one refactor,
not a general invariance claim.

`backend/tests/test_confidence_config_unchanged.py` passed unchanged across the
split, which is the narrowness §1 promised working as designed: it pins the
arithmetic and the validator inventory rather than a checksum over the pack, so
a legitimate re-layering does not turn it red while a retuned scorer still
would.

### 6b. The pin and the config had disagreed for seventeen merges

The replication could not run at first. `backend/app/config.py` resolves
`PIE_PACK` to `pie-parser/packs/org/zcnc`, and the pinned submodule (`41ee3d0`)
predates the layer split and has no such directory. `94102e4`, "Follow
pie-parser's layered packs" (2026-08-23 14:55), moved the config without moving
the pin in the same commit — five hours after this document's own measurement
was taken, against the flat pack that was then still correct.

The consequence was not a narrowed run. It was 61 errors:

```
engine.model.ConfigError: missing config artifact: …/packs/org/zcnc/manifest.yaml
```

— every `requires_pie` test in the backend suite, `test_confidence_config_unchanged.py`
among them. The file that guards this document's hard constraint had not
executed since the pin went skew, and neither had the measurement script §1
tells a reader to run. 17 merges landed on `main` in that window.

Why it was not loud: the `verify` job in `.github/workflows/gate.yml`
deliberately does not fetch the submodule, so it skips those tests and stays
green without needing a credential. The job that would have caught it is
`pie-contract`, whose own comment names it "the check that would have caught the
pin skew" — and it runs only when the `PIE_PARSER_TOKEN` secret is present.
**Whether it ran during this window is UNKNOWN from inside the repository**, and
the workflow records that no secret had been added at least once before. So this
is not evidence that CI missed it. It is evidence that a developer taking the
documented local route got 61 errors, and that this document's method was
unreproducible while that lasted.

The pin is now `16e449e`, whose own gate is green (406 tests, corpus 6,717 rows
/ 11 families / 0 quarantined). All 61 tests pass, and the backend suite is
3,459 passed / 34 skipped — the 34 being the Postgres-only tests `verify.sh`
runs separately against its own sandbox. **No pie test skipped**, which is the
distinction §1's honesty depends on.

Two branches reached that pin independently and within the hour: PR #176 hit the
same wall from the enquiry-capture side and moved it to the same commit, which is
why the diff carrying this section no longer contains the move. Worth recording
rather than tidying away — a defect that two unrelated pieces of work trip over
on the same morning is a defect in the gate, not a coincidence, and it is the
second time this repository has found a check it could not tell from a check that
had passed.

### 6c. One drift the skew had been hiding

With the engine tests running again, `test_frontend_contract.py` failed at once:
`/api/v1/quotes/{id}/intake` sends `intake.captured`, and
`frontend/src/types.ts` declared only `read_by` and `detail`. The field is
deliberate on the server — the screen has to tell "not captured because nobody
stated the channel" apart from a capture that was refused — so the fix is the
declaration. PR #176 landed it first, from the same collision described above,
and also wired the field into the Quote Builder so leaving the channel unset has
a visible cost; this branch carries none of that work.

Nothing about the confidence finding depends on it. It is recorded because it is
the measurable cost of the window in 6b: a contract test that cannot run is a
contract nobody is checking, and this is the one it stopped checking. It went
undeclared from `7298ef4` until the engine tests could run again — the drift did
not begin with the pin, but the pin is why nothing said so.

---

## 7. What this does **not** establish — recorded UNKNOWN

The experiment isolates field completeness cleanly, which means it is silent on
everything else. Each of these is UNKNOWN on this branch, and none should be read
as favourable.

- **Whether the engine's reading of the description is *correct*.** This measures
  invariance, not accuracy. The two runs agreeing on `product_family` for an
  `M3X11` screw would be an agreement to be wrong. §3's contamination finding is
  exactly that case, and this experiment cannot see it.
- **§3's contamination rate (11.6% of routed rows carrying a named non-Kennametal
  manufacturer).** That is a *population* effect. Suppressing a column cannot
  introduce off-catalogue item names, so nothing here confirms, refutes or
  refines it. Worse, §4a shows the diagnostic §3 used is **unavailable** on
  master-shaped input: with all three claims keyed on the grade column, a
  grade-less run names no manufacturer at all, so "does this routed row carry a
  foreign manufacturer" has no answer to give. Any future contamination
  measurement over the master needs a different instrument.
- **§3's family-route rate on the master (30.3%).** This corpus routes 100% on
  both sides, because it is entirely Kennametal nomenclature. The experiment
  holds route rate constant by construction and therefore says nothing about
  whether a 30.3% route on a mixed population is trustworthy.
- **The prior figures 15,028 items / 1,420 identity-linked / 6,146 without a
  manufacturer.** These are prose in `01`, dated 2026-08-09, and are **not
  reproducible from this repository** — the item master is not in it. They are
  cited here as prior measurements and were not re-derived. What *is* confirmed
  reproducible for contrast: `PIE_PARSER_ROOT=… python3 scripts/build_catalog.py`
  yields 6,717 products with zero quarantined.
- **Whether a second field would behave the same way.** Only the grade column was
  suppressed. `critical_slots` contains one slot, so the veto mechanism
  demonstrated in §4a is specific to it; a different missing field would have to
  be measured, not assumed.
- **Whether adding a grade column to the master is feasible or worth it.** This
  says what it would buy in engine terms and nothing about what it would cost to
  obtain. `Product` has no grade field, and no `sku` column either — SKU is
  persisted only on `ItemConnectorRecord.sku` — so the question is a data-sourcing
  question, not a schema one.

---

## 8. Verdict

The 15,028-row 0.00 is **the engine abstaining correctly on an input that lacks
the evidence**, and this is now demonstrated rather than asserted: the same rows,
the same pipeline, one column removed, reproduces the finding at full census
while the engine's reading of the description stays identical in value on every
row.

It does not rescue the strategic claim. `01`'s verdict stands unchanged and this
strengthens its reasoning rather than softening it: the material-and-application
axis, which is the half of the output the value-selling thesis needs, is exactly
the half that a grade-less input destroys. The reachable piece is still geometry,
still gated on ISO slot fill, still roughly a sixth of the master.

What this changes is where to stop looking. The 0.00 is not a bug in the scorer
and not a reason to distrust the parser. Time spent trying to make the engine
decide on grade-less input is time spent manufacturing the evidence whose absence
the score is faithfully reporting.
