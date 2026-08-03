# pie-portal — working agreement

An AI-native Commercial Decision Platform for a B2B cutting-tool distributor
running on Zoho Books, across three legal entities (SLS Engineers, 4U Precision,
UPS). Python 3.11 / FastAPI / SQLAlchemy 2.0 / Alembic on the backend; React 18 +
Vite + TypeScript on the front.

Read `docs/architecture.md` for the design and `docs/development.md` for the
day-to-day loop. This file is the part that constrains how code gets *added*.

---

## 1. Invariants — these are not preferences

**AI never computes a number.** Prices, margins, priorities, thresholds and
commercial recommendations are computed by `app/commercial/` and `app/signals/`,
deterministically, from persisted rows. A model may *read* those numbers and
phrase them. It may never produce one. This is what makes an output auditable
and reproducible, and it is the reason the platform is trusted at all.

Mechanically: `commercial/`, `signals/` and `ingestion/` must not import `ai/`,
and `ai/` must not import `commercial/`. `decisions/` is the single seam where
deterministic facts meet interpretation.

```bash
# Must print nothing. If it prints, the invariant is broken — fix, don't explain.
rg -n '^\s*(from|import)\s+\.*\.?ai[\. ]' backend/app/commercial backend/app/signals backend/app/ingestion
rg -n '^\s*(from|import)\s+.*commercial' backend/app/ai
```

**Cost and margin never reach a salesperson.** Absent from the response, not
hidden in the browser. The server omits the fields; there is nothing to read out
of a network tab.

**Money is `Decimal`.** Margin is a ratio (`0.24`), never a percentage. Movement
is percentage points (`_pp`). Aggregated margin is `Σ gross_profit ÷ Σ revenue`,
never the mean of per-line margins.

**Thresholds carry a version.** `CommercialThresholds.version` is a content hash
stamped on every metric row, signal and quote snapshot. That is what makes the
margin policy editable without making past numbers unexplainable. Never remove
it, and never let a computed row be written without one.

**Do not weaken a rule to make output appear.** If a screen is empty because the
evidence is thin, that is the correct answer. Lowering a threshold, fabricating a
decision, or widening a band to produce a demo is a defect, not a fix.

---

## 2. Before writing new code — the capability search

Duplication is cheaper to prevent than to detect. Before adding any new
function, class, module or endpoint, run a capability search and answer three
questions. This is not optional, and the answers belong in the response.

**1. Does this already exist?** Search the domain verb *and* the entity, plus
synonyms — the second copy of something is almost always named differently from
the first.

```bash
# Adapt the alternation to the concept, not the name you were about to type.
rg -n --type py -e '^\s*(def|class)\s+\w*(resolve|normali[sz]|validate|compute)\w*' backend/app
rg -n --type ts  -e '(function|const|export function)\s+\w*(format|parse|when|inr|pct)\w*' frontend/src
```

**2. Does something *almost* do this?** If a close match exists, the default is
to **extend or parameterize it**, not to add a sibling. Adding a sibling needs a
stated reason.

**3. If nothing exists, where does it belong?** Check §3 before creating a file.
A concern given a second home is how responsibility duplication starts.

### What "already done right" looks like here

`commercial/quote_service.resolve_customer` does not reimplement customer
matching. It delegates:

```python
def resolve_customer(session: Session, org: str, ref: str) -> Optional[models.Customer]:
    """Reuses the Quote Builder's existing tolerant matcher — one behaviour."""
    from ..decisions.quote_support import _resolve_customer
    return _resolve_customer(session, org, ref)
```

Two tolerant matchers would drift, and a customer resolvable on the quote screen
but not in the analysis is a bug nobody can reproduce. Copy this pattern.

Likewise `frontend/src/Tip.tsx` sits outside `platform/` and is re-exported by
`platform/ui.tsx`, because both apps in this repo need the same tooltip — two
implementations would explain the same term two ways.

### Four kinds of redundancy, because they need different fixes

| Kind | What it is | How it shows up |
|---|---|---|
| **Exact / near-exact** | Same block, renamed variables | Similarity scan, or you recognise it while pasting |
| **Semantic** | Two solutions to one problem, written differently | The pre-write search above — this is the one search catches |
| **Responsibility** | Three modules each partly own one concern | Same domain term owned in several files; count the owners |
| **Abstraction** | A wrapper or interface that exists only to look SOLID | An interface with one implementer and no second one planned |

The last two are usually SOLID violations wearing a redundancy costume, which is
why §4 treats them together.

---

## 3. Module boundaries

```
backend/app/
  domain/        SQLAlchemy models + enums. No business decisions.
  commercial/    Deterministic computation: metrics, thresholds, policy,
                 quote assessment. The numbers live here. Never imports ai/.
  signals/       Detectors over persisted rows. Never imports ai/.
  decisions/     The seam: deterministic signal in, AI reading out. May import ai/.
  ai/            Providers, prompts, validation, telemetry. Receives facts;
                 never computes them. Never imports commercial/.
  ingestion/     Zoho adapters, normalisation, connections, credentials.
  routers/       HTTP mapping and role scoping. Thin — no money arithmetic.
  context/       Bundle assembly for interpretation.
```

SQLAlchemy is used throughout by design — this is a session-passing codebase, not
a ports-and-adapters one, and pretending otherwise would mean a repository
interface per table for no gain. The boundary that matters here is
**deterministic vs interpreted**, not domain vs infrastructure.

Rules with teeth:

- A router that computes a price, a margin or a threshold is in the wrong file.
  It calls `commercial/` and maps the result.
- A new Zoho concern goes in `ingestion/`, not in the router that first needed it.
- Anything a screen renders as a number was computed in `commercial/` or
  `signals/` and persisted with a `thresholds_version`.

---

## 4. SOLID, as checks rather than philosophy

Each principle restated as something you can actually look at. **All of them are
subject to the size floor in §6** — a 40-line helper does not need an interface,
and inventing one is itself the defect.

**S — Single responsibility.** A class with methods spanning unrelated verb
groups (parses / persists / emails) owns more than one reason to change. For
calibration: the largest class in this repo has 13 public methods and the median
is 1, so a new class above ~10 public methods, or one touching more than two of
{database, HTTP, filesystem}, deserves a second look.

```bash
python3 - <<'EOF'
import ast, pathlib
for p in pathlib.Path("backend/app").rglob("*.py"):
    try: t = ast.parse(p.read_text())
    except SyntaxError: continue
    for n in ast.walk(t):
        if isinstance(n, ast.ClassDef):
            pub = [x.name for x in n.body
                   if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and not x.name.startswith("_")]
            if len(pub) > 10:
                print(f"{p}:{n.lineno}  {n.name}  {len(pub)} public methods")
EOF
```

**O — Open/closed.** The signal here is an `if/elif` chain keyed on a type string
that grows every time a feature lands. One chain edited by three unrelated
changes wants a registry or a strategy; a chain written once and left alone is
fine. Check the churn before refactoring:

```bash
git log --oneline -15 -- backend/app/signals/   # same block, unrelated features?
```

**L — Liskov.** An override must not narrow accepted inputs, widen the exceptions
the base promised, or return `None` where the base guarantees a value. The
detectors in `signals/` share a base — new ones must satisfy the same contract,
not a convenient subset of it.

**I — Interface segregation.** If no implementer uses more than ~two-thirds of an
abstract class's methods, or a consumer calls two methods of a ten-method
protocol, it is two protocols. `ai/provider.py` is the model to follow: narrow
enough that `mock_provider` and `anthropic_provider` both implement all of it.

**D — Dependency inversion.** Here this means the deterministic layers do not
depend on the interpreted one (§1), and the AI provider is selected behind
`select_provider()` rather than imported concretely. It does **not** mean
wrapping SQLAlchemy.

---

## 5. After writing code — the gate

Deterministic tools first, judgement second. Never report "no duplication"
without a tool having actually looked.

```bash
# 1. Tests. Non-negotiable; the suite is the contract.
cd backend && python -m pytest tests -q

# 2. Types and build.
cd frontend && npx tsc -b && npm run build

# 3. Lint (ruff is installed).
ruff check backend/app

# 4. The invariant checks from §1 — must print nothing.

# 5. Schema drift, if models changed.
cd backend && DATABASE_URL="sqlite:////tmp/mig.db" python -m alembic upgrade head
```

Optional, if you want a real similarity scan and are willing to install it:
`npx jscpd --min-tokens 30 backend/app frontend/src`. Treat >30 duplicated
tokens in a contiguous block as a flag, not a failure — some repetition is
clearer than the abstraction that removes it.

Then fill in §7 against the output.

---

## 6. Size floor — do not over-apply this

Over-applying SOLID to small, honestly-simple code is a defect in its own right,
and it is the failure mode this document is most likely to cause.

Skip §4 entirely for:

- Files under ~50 lines, or classes with fewer than 3 public methods.
- One-off scripts, migrations, fixtures, and `tools/`-style CLI entry points.
- Presentational React components with no branching logic.

A single implementation with no second one in sight does not need an interface.
"We might need it later" is not a variation point; a second caller is.

---

## 7. Self-review, per change

Fill this in against real tool output. Every flag gets a fix or a visible
trade-off — never a silent pass.

```markdown
## Redundancy & SOLID self-review — {files}

**Capability search run:** yes/no — searched: [keywords]
**Near-matches found:** [none | file:line …]
**Reuse rejected because:** [reason | "extended it instead"]

**Duplicate scan:** [not run | clean | N blocks] — [action]
**Invariant checks (§1):** [clean | violation + fix]
**SOLID:**
  - SRP: [within bounds | flagged: reason + action]
  - OCP: [no growing type-chain | flagged: extracted to registry]
  - LSP: [override contract holds | flagged: fixed]
  - ISP: [usage ratio ok | flagged: split]
  - DIP: [deterministic layers clean | flagged: fixed]
**Below the size floor (§6):** [n/a | yes — checks skipped deliberately]

**Verdict:** APPROVED / APPROVED WITH NOTED TRADE-OFF / NEEDS REWORK
```

Where a near-match was found and not reused, that reason belongs in the commit
message too, so the decision stays auditable instead of silent.

---

## 8. What this is not

- Not a blocking CI gate. Tests, types and the §1 invariants block; the SOLID
  heuristics advise. Promote a check to blocking only after it has run clean on
  this codebase for a while — false-positive fatigue kills the whole practice.
- Not a substitute for reading the code around what you are changing. Match its
  comment density, naming and idiom.
- Not a licence to refactor unrelated code. Fix what the change touches.
