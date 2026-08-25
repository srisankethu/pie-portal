"""Which tenant this database connection is currently acting for.

PostgreSQL row-level security decides what a query can see from a *connection
setting*, not from anything in the SQL. This module owns that setting: one
name, one place it is written, one place it is read.

**Why a GUC and not a WHERE clause.** Every tenant-scoped query in this
codebase already filters on ``organization_id``, and 72 of the 74 models carry
the column. That is the control today and it works exactly as long as nobody
forgets — and the survey that preceded this module found several places where
the filter is in Python rather than in SQL (``ingestion/connections.py`` reads
every credential row and decides in a comprehension). A policy attached to the
table is the version that holds when the query is the one nobody reviewed.

**Fail-closed by construction, not by care.** ``current_setting(name, true)``
returns NULL when the setting has never been assigned, and a policy of the form
``organization_id = current_setting('app.current_org', true)`` is NULL — not
true — for every row. So a connection that never announced a tenant sees
**nothing**, rather than everything. That is the opposite of the default this
codebase keeps finding (§1: absence of evidence is not a pass), and it is worth
knowing that it comes from SQL's three-valued logic rather than from a check
somebody remembered to write.

**Why ``set_config`` rather than ``SET LOCAL``.** ``SET LOCAL app.current_org =
'…'`` cannot take a bind parameter — the value has to be interpolated into the
statement text, and the value here is a string that arrived over the network.
``set_config(name, value, is_local => true)`` is the same operation as a
function call, so the tenant id travels as a parameter and there is no way to
spell an organization id that ends the statement and starts another one.

**Why ``LOCAL``.** The setting is scoped to the transaction and reverts at
commit or rollback. ``db.get_session`` opens a session per request and commits
or rolls back at the end of it, so the tenant cannot outlive the request that
established it and leak into whatever the pooled connection serves next. A
session-scoped ``SET`` would do exactly that, and the bug would appear only
under load.

**SQLite is a no-op, and that is a hole rather than a design.** SQLite has no
row-level security and no connection settings, so on the dialect that dev and
most of the test suite run, none of this enforces anything — the Python
``organization_id`` filters remain the only control there. Anything relying on
this for isolation must be tested on PostgreSQL, which is what
``tests/decision_platform/test_row_level_security.py`` exists for.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

#: The connection setting every policy reads. A custom GUC needs a prefix with
#: a dot or PostgreSQL rejects it as an unrecognised parameter.
GUC = "app.current_org"

_SET = text("SELECT set_config(:name, :value, true)")
_GET = text("SELECT current_setting(:name, true)")


def _is_postgres(session: Session) -> bool:
    bind = session.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def set_tenant(session: Session, organization_id: str) -> None:
    """Announce the tenant this transaction is acting for.

    Call it *before* the first query that reads tenant data, which for a request
    means before ``load_principal`` looks the user up — the organization is
    known at that point from the signed token, without a database read.

    An empty id is refused rather than written, because ``set_config(…, '')``
    sets the GUC to the empty string, and an empty string is not NULL: a policy
    comparing against it stops being NULL-and-therefore-false and starts being
    an ordinary mismatch. Both deny, but only one of them still denies if a
    column somewhere is ever empty too.
    """
    if not organization_id:
        raise ValueError("refusing to set an empty tenant; clear_tenant() is the "
                         "way to say 'no tenant'")
    if _is_postgres(session):
        session.execute(_SET, {"name": GUC, "value": organization_id})


def clear_tenant(session: Session) -> None:
    """Say that this transaction acts for no tenant — so it may see no tenant rows.

    Restores NULL rather than setting an empty string, so the policies stay in
    their fail-closed state rather than in a state that merely matches nothing.
    """
    if _is_postgres(session):
        session.execute(text("RESET " + GUC))


#: The one query allowed to cross the tenant boundary, and it is a *function*
#: rather than a policy exemption on purpose — see `adopt_tenant_for_login`.
_LOGIN_LOOKUP = text("SELECT organization_id FROM app_login_lookup(:email)")


def adopt_tenant_for_login(session: Session, email: str) -> Optional[str]:
    """Find which tenant an email belongs to, and announce it. The one hole.

    Sign-in is the single request that cannot know its tenant in advance: there
    is no token yet, and the organization is a property of the row being looked
    for. Under a fail-closed policy the ordinary
    ``select(User).where(email == …)`` returns nothing, and every sign-in fails
    — so something has to be able to answer this one question across tenants.

    **Why a SECURITY DEFINER function and not a policy that permits it.** A
    policy clause wide enough to let an unauthenticated caller find a user by
    email is a policy clause wide enough to enumerate the table; a WHERE that
    matches "the row you asked for" is the same WHERE that matches every row,
    one query at a time. A function is narrow in a way a predicate cannot be:
    it takes one email, returns two columns, and there is no argument that
    makes it return a third or a second row. Its body is the audited surface,
    and it is four lines long.

    ``search_path`` is pinned inside the function (the migration does it, not
    this call) because a SECURITY DEFINER function that resolves ``users``
    through the caller's ``search_path`` can be pointed at a table the caller
    made. That is the classic way this construct becomes a privilege
    escalation, and it is a one-line mitigation.

    Returns the organization, or ``None`` when the email matches nothing — in
    which case no tenant is announced and the caller's own query comes back
    empty, which is the same answer sign-in already gives for an unknown
    address. On SQLite it returns ``None`` and announces nothing, because there
    is no policy to satisfy and the caller's plain query works unchanged.
    """
    if not _is_postgres(session):
        return None
    org = session.execute(_LOGIN_LOOKUP, {"email": email}).scalar()
    if org:
        set_tenant(session, org)
        return str(org)
    return None


_EMAIL_REGISTERED = text("SELECT app_email_registered(:email)")
_ORG_ID_TAKEN = text("SELECT app_org_id_taken(:organization_id)")
_OAUTH_STATE_ORG = text("SELECT app_oauth_state_org(:state_hash)")


def email_registered(session: Session, email: str) -> Optional[bool]:
    """Whether any account already holds this address. ``None`` = ask normally.

    Sign-up's refusal rests on this, and under a policy the ordinary
    ``select(User).where(email == …)`` returns nothing for a caller with no
    tenant — so the refusal never fires and two organizations end up sharing an
    owner address. That is not a broken feature but a corrupted one, which is
    why this is a lookup rather than something sign-up can be trusted to notice.

    Returns a boolean and nothing else — not a user id, not an organization.
    Sign-up needs no more, and anything more would make the form an
    address-to-tenant oracle for anyone who can reach it. It deliberately does
    not filter on ``active``, unlike ``adopt_tenant_for_login``: a deactivated
    user still holds their address.

    ``None`` on SQLite, where there is no function and no policy, so the caller
    falls back to its own query. Two spellings of one predicate would be the
    §2 failure; one authority per dialect, with the ordinary query clearly the
    fallback, is the honest shape the dialect split forces.
    """
    if not _is_postgres(session):
        return None
    return bool(session.execute(_EMAIL_REGISTERED, {"email": email}).scalar())


def org_id_taken(session: Session, organization_id: str) -> Optional[bool]:
    """Whether an organization id already exists. ``None`` = ask normally.

    Provisioning derives ``org_<slug>`` from a company name and walks past
    collisions. Under a policy the ordinary ``session.get`` sees no other
    tenant's row, so the walk stops at the first candidate and the insert fails
    on the primary key — loud rather than silent, but a sign-up broken by
    another company having a similar name is still broken.

    ``None`` on SQLite, where the caller's own lookup is the authority. Same
    shape as ``email_registered``, and for the same reason.
    """
    if not _is_postgres(session):
        return None
    return bool(session.execute(
        _ORG_ID_TAKEN, {"organization_id": organization_id}).scalar())


#: One identity's own workspaces. The second hole, and narrower than the first.
_USER_MEMBERSHIPS = text(
    "SELECT organization_id, organization_name, role "
    "FROM app_user_memberships(:user_id)")


def user_memberships(session: Session,
                     user_id: str) -> Optional[list[tuple[str, str, str]]]:
    """Every organization this user may open, as ``(id, name, role)``.
    ``None`` = ask normally.

    The one question a multi-workspace product cannot answer from inside a
    tenant. A request arrives acting for organization A; "which other
    workspaces does this person have" is by construction about rows A's policy
    hides, so the ordinary query returns the one membership the caller already
    knew about and the switcher shows a list of one.

    Narrow in the way ``adopt_tenant_for_login`` argues for. It takes a user id
    and returns only that user's own active memberships — there is no argument
    that makes it answer for somebody else, and the caller has already been
    authenticated *as* that user by ``load_principal`` before it is reached. So
    what crosses the boundary is a person's own list of doors, which they could
    equally recite from memory.

    It deliberately does **not** grant anything: switching organization still
    goes back through ``memberships.active_membership_for`` inside the target
    tenant, which is where the grant is checked. This only says where to look.

    ``None`` on SQLite, where there is no function and no policy, so the caller
    falls back to ``memberships.organizations_for``.
    """
    if not _is_postgres(session):
        return None
    rows = session.execute(_USER_MEMBERSHIPS, {"user_id": user_id}).all()
    return [(str(r[0]), str(r[1] or ""), str(r[2])) for r in rows]


def adopt_tenant_for_oauth_state(session: Session,
                                 state_hash: str) -> Optional[str]:
    """Announce the tenant that issued an OAuth state token. ``None`` if none did.

    The Zoho callback arrives holding a state token and nothing else — no
    session, no principal — and the row it needs is the one that says which
    organization started the authorization. The argument is already a hash of a
    single-use secret, so the lookup leaks nothing to a caller who does not hold
    one.

    ``None`` leaves no tenant announced, and the caller's own ``session.get``
    then finds nothing — which is the same refusal an unknown token already
    gets.
    """
    if not _is_postgres(session):
        return None
    org = session.execute(_OAUTH_STATE_ORG, {"state_hash": state_hash}).scalar()
    if org:
        set_tenant(session, org)
        return str(org)
    return None


def current_tenant(session: Session) -> Optional[str]:
    """The tenant this transaction announced, or ``None``.

    A read, for tests and for a health or diagnostic surface. It is deliberately
    not used to *decide* anything in application code: the authority on which
    tenant a request belongs to is the principal, and a second source for that
    is how the two come to disagree.
    """
    if not _is_postgres(session):
        return None
    value = session.execute(_GET, {"name": GUC}).scalar()
    return value or None
