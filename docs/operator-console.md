# The operator console

PIE operates this platform through five command-line tools. They work, and every
one of them needs a shell on the box:

| Command | What it answers |
|---|---|
| `python -m app.contact list` | Who filled in the site's form and has not been replied to |
| `python -m app.entitlements requests` | Who is asking to buy something — three queues, one answer |
| `python -m app.entitlements set-plan` | Put a tenant on a plan |
| `python -m app.provision_org` | Create a tenant |
| `python -m app.demo` / `app.sync_all` | Seed the demo workspace; run the syncs |

This document is about giving three of those a screen, and about the thing that
decision turns on, which is not the screen at all.

---

## 1. The identity, which is the whole design

A console needs a caller, and the cheap version of that caller is a boolean on
`users` — `is_staff`, checked at the top of a few endpoints. That version is
wrong here, and specifically wrong rather than inelegant.

Every control in this codebase is downstream of `Principal.organization_id`
being set. The cost and margin withholding in `commercial.quote_service.project`
reads it. The scope checks in `authz` read it. Row-level security reads it, from
a connection setting `tenancy` owns. An operator is the one caller in the system
with **no** organization, so a flag on `users` means one verification path that
sometimes returns a tenant-bound principal and sometimes returns a
tenant-bypassing one, told apart by a nullable column.

That is the shape of every defect §1 of `CLAUDE.md` records. `filterCounts.MFLOOR`
was a count computed two lines below the guard that correctly withheld
`marginFloor`. The rule-code walk on `quote-intelligence/assess` was the same
leak returning as a predicate. In both, the check existed and the other branch
did not have it. A staff flag on the customer login table is that pattern with
the whole tenant boundary as the payload.

So the credential is separate at every level:

- its own table, `operator_keys` (`app/domain/models.py`), added by migration `o1op`;
- its own prefix, `pieop_`, so a credential presented at the wrong door fails on
  its shape rather than on a lookup;
- its own verifier, `operator.authenticate`, with no code path from a customer
  credential to it;
- its own dataclass, `operator.Operator`, which is deliberately **not** a
  `Principal` — it has no `organization_id` and no `Role`, so it cannot be
  passed to something that projects a quote for one.

`tests/decision_platform/test_operator_console.py` pins the part that matters:
a tenant API key, minted against the same database and live, is refused.

### Why not a fourth `Role`

`Role`'s own docstring says it is "what a person may do **inside one
organization**", stored on a membership and resolved per request from the
organization the session is acting for. A cross-tenant role is a contradiction
in that sentence, and every `require_owner` in the codebase would have to grow a
case for the role that is not a member of anything.

---

## 2. What an operator may read

Two scopes, and the boundary between them is enforced by the database rather
than by this router remembering to check.

**Vendor scope** needs a live operator key and nothing more:

- `organizations` — a tenant's name, plan, currency and start date. This is the
  vendor's own billing metadata.
- `contact_requests` — an enquiry from the public site, written before there
  was a tenant to scope it to.

Neither table carries a row-level security policy (`d2rls`, `d3rls`), and that
is the reason those two panels work at all.

**Tenant scope** — anything inside a customer's book — needs a break-glass
grant. The other 58 tables *are* policied and are fail-closed: a connection that
has not announced a tenant sees nothing, from SQL's three-valued logic rather
than from a check somebody wrote. So this is not a rule the console applies. It
is a rule the console cannot get around: without `tenancy.set_tenant` the
queries return empty.

`operator.reach_into` is the only thing in the console that calls it, and the
order of its two statements is the control:

```python
access.record_use(session, organization_id=..., staff_user_id=..., resource=...)
tenancy.set_tenant(session, organization_id)
```

The grant is asserted and the reach is logged **before** the tenant is
announced, so a refusal leaves the connection exactly as fail-closed as it was.
Reversed, there would be a window in which a read is possible and unlogged.

### The grant

`trust/access` was written for exactly this and, until now, had no production
caller — `trust/audit`'s docstring says so. Its three properties are the ones an
internal console needs and the ones internal consoles are usually missing:

- **A stated reason**, at least ten characters, recorded verbatim.
- **An expiry.** Standing access is what turns one compromised staff account
  into every tenant's data.
- **An event per use**, not per grant. Opening the door once and opening it
  fifty times are different facts.

And the property that makes the rest honest: those events are exposed on
`GET /api/v1/trust/access`, which is the **customer's** endpoint, with no
mechanism to suppress one. A log the customer cannot see is an internal control.
A log they can see is a constraint on us, which is the party they are actually
worried about.

---

## 3. What the console holds

Three panels, mapping onto three of the five commands.

| Panel | Reads | Writes |
|---|---|---|
| **Queues** | The three ask-queues, exactly as `entitlements requests` prints them | Grant or decline an in-product plan request |
| **Enquiries** | The demo-request form's queue, waiting or answered | Mark one answered, stamped with the operator's own id |
| **Tenants** | Every tenant with its licensed plan | Put one on a plan; open and hand back a break-glass grant; read one support view |

The support view is deliberately the smallest useful answer to "is their
platform working": seat count, and when the book last synced. No quotes, no
customers, no money. The questions this console exists to answer are the
vendor's, and a screen that showed a customer's margins because it technically
could would be the leak §1 keeps describing.

### What stays on the command line

Provisioning a tenant, seeding the demo, and running syncs. Each writes a great
deal on one call, none is reversible from a screen, and none is done often
enough to want a button for. A console whose most destructive actions are its
least used is a console built around the wrong verbs.

Minting an operator key stays there too, and for a stronger reason: a console
that can mint its own credentials turns one leaked key into a permanent
foothold. `routers/api_keys` already makes that argument about *tenant* keys —
"a key cannot mint another key" — and it is stronger for a credential that is
not scoped to a tenant at all.

---

## 4. Where it lives

A separate document, `operator.html`, with its own bundle and its own entry
(`src/operator/`). Not a route inside the app.

The practical reason is the same as the identity one: every screen in
`PlatformApp` reads `session.organization_id`, and this page's caller has none.
A console mounted beside them would be one application whose components
sometimes have a tenant and sometimes do not. A second entry means nothing in
the app's bundle can import a console panel, or the reverse.

It shares the theme, `platform/kit.tsx` and `platform/DataGrid.tsx`, because
those are the design system and a second one would be a second design system. It
does **not** share `platform/api.ts` or `authFetch.ts`: both carry a tenant
session — a cookie, a 401 handler that routes to the sign-in card, the currency
side effects of a login — and none of it applies.

The key is held in `sessionStorage` and sent as `Authorization: Bearer`. Header
only, so there is no ambient authority and therefore no CSRF surface to defend;
closing the tab ends the session without anything having to expire. A key
already in storage is re-checked against `/whoami` on load rather than trusted,
because it may have been revoked since the tab was opened.

---

## 5. Running it

```bash
# Mint a key. The secret prints once and is stored only as a PBKDF2 hash.
python -m app.operator mint sanketh --name laptop

# Every key ever minted, revoked ones included.
python -m app.operator list

# End one.
python -m app.operator revoke <key_id>
```

`operator_id` is the person and is stable across key rotations: it is what lands
in `contact_requests.handled_by` and in `access_grants.staff_user_id`, which is
the string a customer reads in their own access log. Two keys for one person — a
laptop and a phone — are two rows sharing one `operator_id`. Make it something
somebody could be asked about.

Then open `/operator.html` and paste the key.

### Restrict it at the edge as well

The console refuses every request without a live key, and that is the control.
It is not the only one worth having. The industry practice for an internal
console — after Uber's "God View" made the case for everyone — is that it should
not be reachable from the open internet at all. In this deployment that means
`deploy/Caddyfile`: put `/operator.html` behind an IP allowlist, a VPN, or a
separate hostname that is not the customer origin.

It is excluded from `robots.txt` and carries `noindex`, but neither of those is
a security control — they keep it out of a search result, which is a different
job.

---

## 6. What this is not, yet

- **No impersonation.** "View as this customer" is the panel every support
  console eventually grows, and it is the one that needs the most thought: it
  would mean minting a tenant session from an operator credential, which is
  precisely the bridge §1 exists to prevent. If it is ever built it goes through
  `trust/access` per view, like everything else here, and it needs its own
  document.
- ~~No notification.~~ **Done, and it is a job rather than a screen.**
  `python -m app.contact alert` sends what has arrived since it last said
  anything and stamps those rows, so an enquiry is announced once and a quiet
  run is silent. `docs/hosting.md` has the cron entry and the destinations. The
  console makes the queue *visible*; that job is what makes it *noticed*, and
  the two are different problems — which is why the Enquiries tab now carries an
  **Alerted** column: a webhook broken for a week is a column of "not sent" and
  is otherwise invisible.
- **No second operator.** Everything here works for a team, but nothing has been
  used by one. Roles among operators — who may grant a plan versus who may only
  read a queue — is a real question and is deliberately not answered yet: there
  is one operator, and inventing a permission model for a team that does not
  exist is the over-application §7 warns about.
