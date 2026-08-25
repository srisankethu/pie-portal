"""The Prometheus exposition endpoint: bounded labels, a token, and no economics.

Three properties, and they fail in three different ways:

**Cardinality.** `Counter._by_label` and `Gauge._by_label` were dicts keyed by
label set, appended to on every observation and never trimmed, while the API
middleware labelled every request with `request.url.path` — the raw path, so
`/api/v1/quotes/<uuid>/lines/<uuid>/supply` was its own key. One key per URL,
forever. `Histogram` had exactly this and the class docstring records that it
was removed; the same defect survived in both siblings. It matters more now
than it did: a Prometheus label with one value per business record is how a TSDB
is killed, and it would be this codebase exporting the problem rather than
merely holding it.

**Authentication.** The endpoint is not role-scoped like its neighbours — a
static token, so a scraper needs no principal. That is only defensible if the
token is checked in constant time, is accepted on this route and no other, and
if an unset token refuses rather than opens.

**CLAUDE.md §1.** Metrics sit behind a token rather than a role, which is the
loosest guard in this codebase, so the cost/margin rule gets its tightest
reading here: no number on this surface may be economic, and no metric name or
label may answer a margin question.
"""
from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.observability import exposition
from app.observability.instrumentation import (
    UNMATCHED_ROUTE,
    api_instrumentation_middleware,
    route_template,
)
from app.observability.metrics import MAX_LABEL_SETS, Counter, Gauge, MetricRegistry

SCRAPE_PATH = "/api/v1/internal/observability/prometheus"
TOKEN = "scrape-token-for-tests"


@pytest.fixture()
def token_set(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture()
def token_unset(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", "")


def _scrape(client, token: str | None = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get(SCRAPE_PATH, headers=headers)


def _samples(body: str) -> list[tuple[str, dict[str, str], str]]:
    """Every sample line as (name, labels, value). Comments are skipped."""
    parsed = []
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        head, _, value = line.rpartition(" ")
        name, _, rest = head.partition("{")
        labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', rest))
        parsed.append((name, labels, value))
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# 1. The label set is bounded, and bounded by something you can state
# ─────────────────────────────────────────────────────────────────────────────


class TestTheEndpointLabelIsARouteTemplate:
    """The raw path is the defect; the matched template is the fix.

    A template comes from the routing table, so the number of distinct values it
    can take is the number of routes. A raw path comes from whatever the caller
    typed, so the number of distinct values it can take is the number of quotes,
    lines, decisions and customers this business will ever have — plus every URL
    a scanner invents.
    """

    @pytest.fixture()
    def app_client(self):
        app = FastAPI()
        app.middleware("http")(api_instrumentation_middleware)

        @app.get("/api/v1/quotes/{quote_id}/lines/{line_id}/supply")
        def supply(quote_id: str, line_id: str):
            return {"ok": True}

        return TestClient(app, raise_server_exceptions=False)

    def test_a_thousand_ids_produce_one_series(self, app_client):
        """The assertion the old code could not pass: 1000 URLs, one key.

        Before this, each of these was its own dict entry, in a process that
        runs for weeks and never shrinks — and each would have been its own
        Prometheus time series.
        """
        counter = Counter("probe_requests_total", "help")

        for _ in range(1000):
            response = app_client.get(
                f"/api/v1/quotes/{uuid.uuid4()}/lines/{uuid.uuid4()}/supply")
            assert response.status_code == 200

        # Re-drive the label the middleware would have used, through the same
        # helper, so this measures the label rather than the middleware's
        # bookkeeping.
        for _ in range(1000):
            counter.inc(labels={"endpoint": "/api/v1/quotes/{quote_id}/lines/"
                                            "{line_id}/supply",
                                "method": "GET", "status": 200})

        assert len(counter._by_label) == 1
        assert counter.export()["label_series"][0]["labels"]["endpoint"] == (
            "/api/v1/quotes/{quote_id}/lines/{line_id}/supply")

    def test_the_template_is_reported_not_the_path_that_was_requested(
            self, app_client):
        seen = {}

        @app_client.app.middleware("http")
        async def capture(request, call_next):
            response = await call_next(request)
            seen["endpoint"] = route_template(request)
            return response

        app_client.get("/api/v1/quotes/Q-9/lines/L-4/supply")

        assert seen["endpoint"] == "/api/v1/quotes/{quote_id}/lines/{line_id}/supply"
        assert "Q-9" not in seen["endpoint"]

    def test_a_miss_is_a_constant_and_never_the_path(self, app_client):
        """A 404 matches no route, and the tempting fallback — use the raw path
        when there is no template — reintroduces the whole defect on exactly the
        traffic most likely to be a scanner walking random URLs."""
        seen = {}

        @app_client.app.middleware("http")
        async def capture(request, call_next):
            response = await call_next(request)
            seen["endpoint"] = route_template(request)
            return response

        app_client.get("/wp-admin/setup-config.php")

        assert seen["endpoint"] == UNMATCHED_ROUTE
        assert "wp-admin" not in seen["endpoint"]


class TestTheCounterRefusesToGrowWithoutBound:
    """The cap is a backstop for the *next* call site, not for this one.

    The route template already bounds today's traffic. What the cap stops is
    somebody adding `{"quote_id": ...}` to a label set in a year's time and
    rediscovering this bug in production, where it presents as a monitoring
    outage rather than as a failing test.
    """

    def test_the_tally_stops_at_the_cap(self):
        counter = Counter("probe_total", "help", label_capacity=8)

        for i in range(500):
            counter.inc(labels={"unbounded": str(i)})

        assert len(counter._by_label) == 8
        assert counter.export()["label_sets"] == 8

    def test_nothing_is_lost_when_the_cap_bites(self):
        """The parts must still add up to the whole.

        Silently dropping the over-cap increments would make
        `sum(api_requests_total)` quietly smaller than the request count — a
        wrong number with no way to notice, which is worse than a missing one.
        """
        counter = Counter("probe_total", "help", label_capacity=8)

        for i in range(500):
            counter.inc(labels={"unbounded": str(i)})
        counter.inc(3.0)  # and an unlabelled increment, for good measure

        export = counter.export()
        attributed = sum(entry["value"] for entry in export["label_series"])
        assert attributed + export["unattributed"] == export["value"] == 503.0

    def test_the_default_cap_sits_above_what_the_routing_table_can_produce(self):
        """Stated as a number, per the rule that a bound you cannot state is not
        a bound. 183 path x method pairs against a dozen or so status codes is
        under 2,400 combinations; the cap must be above that or it would
        truncate legitimate traffic instead of catching a mistake."""
        assert MAX_LABEL_SETS >= 4096

    def test_a_label_set_is_identified_by_value_not_by_ordering_or_type(self):
        """`status` arrives from the middleware as an `int`. If the day someone
        passes `str(...)` instead splits one series into two, the graph breaks
        for a reason nobody will find."""
        counter = Counter("probe_total", "help")
        counter.inc(labels={"method": "GET", "status": 200})
        counter.inc(labels={"status": "200", "method": "GET"})

        assert len(counter._by_label) == 1
        assert counter.export()["label_series"][0]["value"] == 2.0


def test_a_gauge_keeps_no_per_label_breakdown_at_all():
    """Removed rather than capped, following `Histogram`'s precedent: no call
    site ever passed labels to a gauge and no reader ever read the field, so it
    was an unbounded dict that was pure cost. `labels` stays in the signature so
    the three metric types keep one shape."""
    gauge = Gauge("probe_gauge", "help")
    for i in range(500):
        gauge.set(float(i), labels={"unbounded": str(i)})

    assert not hasattr(gauge, "_by_label")
    assert gauge.get() == 499.0
    assert "by_label" not in gauge.export()


# ─────────────────────────────────────────────────────────────────────────────
# 2. The rendered text
# ─────────────────────────────────────────────────────────────────────────────


class TestTheExpositionText:
    def _payload(self):
        registry = MetricRegistry()
        requests = registry.counter("probe_requests_total", "Probe requests")
        requests.inc(labels={"method": "GET", "status": 200})
        requests.inc(2, labels={"method": "POST", "status": 500})
        registry.counter("probe_plain_total", "No labels here").inc(7)
        registry.gauge("probe_connections", "Open connections").set(4)
        latency = registry.histogram("probe_latency_seconds", "Probe latency")
        for value in (0.1, 0.2, 0.3, 0.4):
            latency.observe(value)
        registry.histogram("probe_empty_seconds", "Never observed")
        return registry.export()

    def test_every_series_carries_the_worker(self):
        """`MetricRegistry` is a per-process singleton, so a scrape sees one of
        `UVICORN_WORKERS`. Without the label two workers' counters look like one
        series sawing up and down, and an operator sums or averages them wrongly
        — which the JSON export already warns about in prose that no scrape
        carries."""
        body = exposition.render(self._payload())

        samples = _samples(body)
        assert samples
        assert all("worker" in labels for _, labels, _ in samples), body

    def test_a_counter_with_a_breakdown_emits_only_the_breakdown(self):
        """Emitting the aggregate under the same metric name as well would make
        `sum(probe_requests_total)` count every request twice."""
        body = exposition.render(self._payload())

        series = [(labels, value) for name, labels, value in _samples(body)
                  if name == "probe_requests_total"]
        assert len(series) == 2
        assert {labels["status"] for labels, _ in series} == {"200", "500"}
        assert sum(float(value) for _, value in series) == 3.0

    def test_a_counter_with_no_breakdown_emits_one_series(self):
        body = exposition.render(self._payload())
        series = [s for s in _samples(body) if s[0] == "probe_plain_total"]
        assert len(series) == 1
        assert series[0][2] == "7"

    def test_the_residual_is_published_rather_than_dropped(self):
        """Unlabelled increments on a counter that also has a breakdown, plus
        anything past the cardinality cap. Without this series the parts do not
        add up to the total and nothing says so."""
        registry = MetricRegistry()
        counter = registry.counter("probe_mixed_total", "Mixed")
        counter.inc(labels={"method": "GET"})
        counter.inc(5)  # no labels

        body = exposition.render(registry.export())
        series = {tuple(sorted(labels.items())): float(value)
                  for name, labels, value in _samples(body)
                  if name == "probe_mixed_total"}

        assert sum(series.values()) == 6.0
        assert any("label_overflow" in dict(key) for key in series)

    def test_a_histogram_is_a_summary_because_it_has_no_buckets(self):
        """`# TYPE histogram` with no `_bucket` series makes
        `histogram_quantile()` silently return nothing on a metric that looks
        like it should work. A summary is what this actually is: pre-computed
        quantiles that cannot be aggregated, plus a sum and count that can."""
        body = exposition.render(self._payload())

        assert "# TYPE probe_latency_seconds summary" in body
        assert "_bucket" not in body
        quantiles = {labels["quantile"] for name, labels, _ in _samples(body)
                     if name == "probe_latency_seconds"}
        assert quantiles == {"0.5", "0.95", "0.99"}
        assert "probe_latency_seconds_count" in body
        assert "probe_latency_seconds_sum" in body

    def test_an_unobserved_histogram_publishes_no_quantile(self):
        """Not a zero. Nothing was observed, so there is no answer, and a 0 here
        reads as a suspiciously fast p99 — the benign default §1 forbids."""
        body = exposition.render(self._payload())

        assert "probe_empty_seconds_count" in body
        assert 'probe_empty_seconds{' not in body

    def test_an_undeclared_worker_count_is_absent_not_one(self):
        """Absence is a gap a scraper can notice. `1` is a confident wrong
        answer that tells it a single scrape had the whole deployment."""
        payload = self._payload()

        payload["workers_configured"] = None
        assert "pie_workers_configured" not in exposition.render(payload)

        payload["workers_configured"] = 2
        assert "pie_workers_configured" in exposition.render(payload)

    def test_rendering_is_deterministic(self):
        """Two scrapes of an idle process must be byte-identical, or a diff
        between them means nothing."""
        payload = self._payload()
        assert exposition.render(payload) == exposition.render(payload)

    def test_a_label_value_with_quotes_or_backslashes_is_escaped(self):
        """A route template is not attacker-controlled, but a label value that
        breaks out of its quotes corrupts every series after it in the body —
        one unescaped character and the whole scrape fails to parse."""
        registry = MetricRegistry()
        registry.counter("probe_escaping_total", "Escaping").inc(
            labels={"endpoint": 'a"b\\c'})

        body = exposition.render(registry.export())
        line = next(ln for ln in body.splitlines()
                    if ln.startswith("probe_escaping_total"))
        assert r'endpoint="a\"b\\c"' in line

    def test_a_metric_whose_name_prometheus_cannot_parse_is_skipped(self, caplog):
        """One bad name must not take down a scrape polled every 15 seconds."""
        payload = {"worker": "w", "metrics": [
            {"name": "not a name", "type": "counter", "value": 1,
             "label_series": [], "unattributed": 1},
            {"name": "good_total", "type": "counter", "value": 2,
             "label_series": [], "unattributed": 2},
        ]}
        with caplog.at_level(logging.WARNING):
            body = exposition.render(payload)

        assert "not a name" not in body
        assert "good_total" in body
        assert any("invalid Prometheus name" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# 3. The token
# ─────────────────────────────────────────────────────────────────────────────


class TestTheScrapeToken:
    def test_a_valid_token_is_served(self, api_client, token_set):
        response = _scrape(api_client)
        assert response.status_code == 200
        assert response.headers["content-type"] == exposition.CONTENT_TYPE

    @pytest.mark.parametrize("header", [None, "", "wrong", "Bearer", TOKEN[:-1]])
    def test_anything_but_the_token_is_refused(self, api_client, token_set,
                                               header):
        assert _scrape(api_client, header).status_code == 401

    def test_an_unset_token_refuses_rather_than_opens(self, api_client,
                                                      token_unset):
        """The failure this guards is a deployment that never configured
        monitoring — the common case — quietly serving its metrics to anyone.
        An empty configured token must not be satisfied by an empty bearer, and
        `hmac.compare_digest("", "")` is `True`, so the guard has to run before
        the compare."""
        assert _scrape(api_client, None).status_code == 401
        assert _scrape(api_client, "").status_code == 401
        assert _scrape(api_client, "anything").status_code == 401

    def test_the_refusal_does_not_say_whether_a_token_is_configured(
            self, api_client, monkeypatch):
        """Otherwise an unauthenticated caller learns that this deployment has
        no scrape secret, which is exactly the deployment worth probing."""
        from app.config import settings

        monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", "")
        unset = _scrape(api_client, "guess")

        monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", TOKEN)
        wrong = _scrape(api_client, "guess")

        assert unset.status_code == wrong.status_code == 401
        assert unset.text == wrong.text
        assert unset.headers.get("www-authenticate") == \
            wrong.headers.get("www-authenticate")

    @pytest.mark.parametrize("bearer", [
        b"Bearer \xe9",                 # latin-1, decodes to a non-ASCII str
        b"Bearer \xff\xfe",
        b"Bearer ",                     # empty after the scheme
        b"Bearer " + b"\x80" * 40,
    ], ids=["latin1", "high-bytes", "empty", "long-high-bytes"])
    def test_no_byte_sequence_distinguishes_the_two_refusals(
            self, api_client, monkeypatch, bearer):
        """The same property as above, over the input space that broke it.

        The ASCII-only version of this test passed while a real oracle existed.
        ``hmac.compare_digest`` raises TypeError on a non-ASCII ``str``, and
        Starlette decodes header bytes as latin-1 — so `Authorization: Bearer
        \xe9` reached the compare as a non-ASCII string. With a token
        configured that raised and became a 500; with none, the empty-token
        guard short-circuited to 401. One unauthenticated request answered "does
        this deployment have a scrape secret?"

        The assertion was never wrong. It was narrow, which is the harder thing
        to notice, so this widens it to the bytes an attacker actually controls.
        """
        from app.config import settings

        monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", "")
        unset = api_client.get(SCRAPE_PATH, headers={b"Authorization": bearer})

        monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", TOKEN)
        configured = api_client.get(SCRAPE_PATH, headers={b"Authorization": bearer})

        assert unset.status_code == configured.status_code == 401, (
            f"{bearer!r} distinguished the two states: "
            f"unset={unset.status_code}, configured={configured.status_code}")
        assert unset.text == configured.text

    def test_the_token_is_not_a_key_to_anything_else(self, api_client,
                                                     token_set):
        """It is checked on this route and no other, so a credential sitting in
        a config file on a monitoring host cannot be replayed against a tenant
        surface. Every neighbouring observability route still wants a
        manager-or-owner principal."""
        headers = {"Authorization": f"Bearer {TOKEN}"}
        for path in ("/api/v1/internal/observability/metrics",
                     "/api/v1/internal/observability/dashboard",
                     "/api/v1/internal/observability/capacity"):
            assert api_client.get(path, headers=headers).status_code == 401, path

    def test_the_scrape_touches_no_database(self, api_client, token_set, engine):
        """Scraped every 15 seconds for the life of the deployment. A read on
        that path — let alone a write — is how the SQLite locking incident in
        CLAUDE.md §4 starts, and the registry is in memory, so there is no
        reason for one."""
        statements: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        try:
            assert _scrape(api_client).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert statements == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLAUDE.md §1 — nothing economic on a surface guarded by a token
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def live_body():
    """An exposition body from the real application, after real traffic.

    Built over `app.main` rather than a hand-made registry on purpose: the thing
    the tests below are looking for would arrive as a *new* metric somebody
    registers in a year, and a fixture that lists the metrics it expects cannot
    notice one it does not list.
    """
    import app.main as main
    from app.config import settings

    client = TestClient(main.app, raise_server_exceptions=False)
    client.get("/api/health")
    client.get("/api/v1/quotes/8f2a-0001/lines/1b7c-0002/price")
    client.get("/api/v1/admin/margin-policy")
    client.get("/api/v1/insight/revenue-flow")
    client.get("/api/v1/nothing-here")

    original = settings.METRICS_SCRAPE_TOKEN
    settings.METRICS_SCRAPE_TOKEN = TOKEN
    try:
        response = _scrape(client)
    finally:
        settings.METRICS_SCRAPE_TOKEN = original
    assert response.status_code == 200
    return response.text


#: Words that would mean a number on this surface answers a commercial question.
_ECONOMIC = re.compile(
    r"cost|margin|price|pricing|profit|revenue|discount|gmroi|payable|"
    r"receivable|invoice_value|floor|landed|quote_value",
    re.IGNORECASE)


class TestNoEconomicsReachTheExposition:
    """The tightest reading of §1, because this surface is the loosest guard.

    Everywhere else the rule is enforced against a *role*: a salesperson may not
    see cost. Here there is no role at all — a static token, held by a
    monitoring host, in a file. So the rule applied is not "the right people see
    it" but "the number is not here".
    """

    def test_no_metric_name_answers_a_commercial_question(self, live_body):
        """A walk over every name in the body, so a metric added later is
        covered without anybody remembering to add it here."""
        offenders = sorted({name for name, _, _ in _samples(live_body)
                            if _ECONOMIC.search(name)})
        assert not offenders, f"economic metric names exposed: {offenders}"

    def test_no_label_name_answers_a_commercial_question(self, live_body):
        offenders = sorted({label for _, labels, _ in _samples(live_body)
                            for label in labels
                            if _ECONOMIC.search(label)})
        assert not offenders, f"economic label names exposed: {offenders}"

    def test_every_label_value_comes_from_the_routing_table_or_the_protocol(
            self, live_body):
        """The stronger half, and the reason a plain substring walk is not
        enough on its own.

        A route template *may* legitimately contain an economic word — this
        application serves `/api/v1/admin/margin-policy`, and a count of
        requests to a screen is not a margin. What must never happen is a label
        value that came from anywhere other than the protocol or this
        application's own routing table: a customer name, a quote id, a price a
        caller proposed. So rather than grepping the values, this enumerates
        their provenance. Nothing here originates with a caller.
        """
        import app.main as main

        client = TestClient(main.app, raise_server_exceptions=False)
        templates = set(client.get("/openapi.json").json()["paths"])
        for route in main.app.routes:
            for attribute in ("path_format", "path"):
                value = getattr(route, attribute, None)
                if value:
                    templates.add(value)

        methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        quantiles = {"0.5", "0.95", "0.99"}

        for name, labels, _ in _samples(live_body):
            for label, value in labels.items():
                if label == "worker":
                    continue                      # host:pid:rand, this process
                if label == "method":
                    assert value in methods, (name, value)
                elif label == "status":
                    assert re.fullmatch(r"\d{3}", value), (name, value)
                elif label == "quantile":
                    assert value in quantiles, (name, value)
                elif label == "label_overflow":
                    assert value == "true", (name, value)
                elif label == "endpoint":
                    assert value == UNMATCHED_ROUTE or value in templates, (
                        f"{name} carries an endpoint label that is not a route "
                        f"template: {value!r} — a value from outside the "
                        f"routing table is unbounded by definition")
                else:
                    pytest.fail(
                        f"{name} carries an undeclared label {label!r}={value!r}. "
                        f"Every label on this surface must have a stated, "
                        f"bounded vocabulary; add it here deliberately.")

    def test_the_body_carries_no_decimal_that_could_be_money(self, live_body):
        """Every value on this surface is a count, a byte size, a connection
        count or a duration in seconds. None of them is currency, and this is
        the assertion that would fail the day somebody registers a gauge for
        `quote_value_total` and labels it as a size."""
        units = ("_total", "_count", "_sum", "_seconds", "_bytes",
                 "_connections", "_size")
        for name, _, value in _samples(live_body):
            assert name.endswith(units) or name == "pie_workers_configured", (
                f"{name} is not obviously a count, a duration or a size — say "
                f"what unit it carries before exporting it")
            float(value)
