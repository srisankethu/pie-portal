// The access list has to follow the connector tabs.
//
// It did not. "Scopes this platform needs" was its own panel below the add
// form, fed by the connections view rather than by the tab strip, so it listed
// Zoho's ten `ZohoBooks.*.READ` strings whichever system was selected —
// telling somebody connecting NetSuite to grant scopes that do not exist in
// NetSuite, and telling them nothing about the role permissions that do. The
// two panels were about one decision and only one of them was listening.
//
// So this pins the behaviour rather than the arrangement: pick a system, and
// what the screen says must be granted is *that system's* list.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionsPanel } from "./ConnectionsPanel";
import { papi } from "./api";
import type { ConnectorCatalogEntry, PlatformSession, ZohoConnection } from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

function entry(over: Partial<ConnectorCatalogEntry>): ConnectorCatalogEntry {
  return {
    key: "x", label: "X", company_term: "company", icon: "◇",
    setup_note: "", credential_fields: [], connection_fields: [],
    external_id_field: "company_id", can_discover: false,
    permissions: [], permission_note: "", permission_string: "",
    permission_string_minimum: "", can_authorize: false,
    writes: [], can_write_quotes: false,
    ...over,
  };
}

const CATALOG: ConnectorCatalogEntry[] = [
  entry({
    key: "zoho", label: "Zoho Books", company_term: "organization",
    setup_note: "Reads Zoho Books over its v3 API.",
    writes: ["sales_quotes"], can_write_quotes: true,
    permissions: [
      { name: "ZohoBooks.bills.READ", why: "Bills — what it cost.",
        required: true, reads: ["bills"] },
    ],
    permission_note: "Paste this into the scope field in the Zoho API console.",
    permission_string: "ZohoBooks.bills.READ,ZohoBooks.users.READ",
    permission_string_minimum: "ZohoBooks.bills.READ",
  }),
  entry({
    key: "netsuite", label: "Oracle NetSuite", company_term: "account",
    setup_note: "Reads NetSuite through SuiteQL.",
    credential_fields: [
      { name: "consumer_key", label: "Consumer key", secret: false,
        required: true, placeholder: "", help: "" },
    ],
    connection_fields: [
      { name: "company_id", label: "Account ID", secret: false,
        required: true, placeholder: "", help: "" },
    ],
    permissions: [
      { name: "Setup → REST Web Services", why: "SuiteQL is served over REST.",
        required: true, reads: [] },
      { name: "Transactions → Bill (View)", why: "Vendor bills — what it cost.",
        required: false, reads: ["bills"] },
    ],
    permission_note: "Granted on the role the access token is issued for.",
    permission_string: "",
  }),
  // Copied from the real spec rather than invented, because the rotate tests
  // below turn on the shape it actually has: two non-secret credential fields,
  // one secret, one OPTIONAL non-secret (`environment`) — and a per-company
  // field that lives on the connection, not on the sign-in.
  entry({
    key: "dynamics365", label: "Dynamics 365 Business Central",
    company_term: "company",
    credential_fields: [
      { name: "tenant_id", label: "Directory (tenant) ID", secret: false,
        required: true, placeholder: "", help: "" },
      { name: "client_id", label: "Application (client) ID", secret: false,
        required: true, placeholder: "", help: "" },
      { name: "client_secret", label: "Client secret", secret: true,
        required: true, placeholder: "", help: "" },
      { name: "environment", label: "Environment", secret: false,
        required: false, placeholder: "", help: "" },
    ],
    connection_fields: [
      { name: "company_id", label: "Company ID", secret: false,
        required: true, placeholder: "", help: "" },
    ],
  }),
];

function mountPanel() {
  vi.spyOn(papi, "listConnections").mockResolvedValue({
    connections: [], credentials: [], can_manage: true,
    source_mode: "api", pooling_note: "Everything pools.",
  });
  vi.spyOn(papi, "connectorCatalog").mockResolvedValue({ connectors: CATALOG });
  return render(
    <ConnectionsPanel
      session={SESSION}
      canSync
      onSync={vi.fn()}
      busyConnections={[]}
      starting={false}
    />);
}

/** Open the add-a-company dialog, which is where all of this now lives.
 *
 *  It used to be an always-open panel under the list — several screens of
 *  setup standing permanently between the companies and everything below
 *  them, read once and scrolled past for ever after. */
async function openAdd() {
  fireEvent.click(await screen.findByRole("button", { name: /Add company/ }));
}

describe("ConnectionsPanel — the add dialog", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("keeps the whole form behind one button until it is asked for", async () => {
    mountPanel();
    // The button is the only part of adding a company on the screen.
    expect(await screen.findByRole("button", { name: /Add company/ }))
      .toBeInTheDocument();
    expect(screen.queryByText(/What Zoho Books must let it read/))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Oracle NetSuite" }))
      .not.toBeInTheDocument();

    await openAdd();
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Oracle NetSuite" }))
      .toBeInTheDocument();
  });

  it("closes itself once the company is added", async () => {
    // Closing is the receipt. Left open over a reloaded list it reads as a
    // submission that did nothing, and the next click adds a duplicate.
    vi.spyOn(papi, "addConnection").mockResolvedValue({} as never);
    mountPanel();
    await openAdd();
    fireEvent.change(await screen.findByLabelText(/^Client ID/),
                     { target: { value: "1000.APP" } });
    fireEvent.change(screen.getByLabelText(/^Client secret/),
                     { target: { value: "s3cr3t" } });
    fireEvent.change(screen.getByLabelText(/^Grant code/),
                     { target: { value: "1000.code.fresh" } });
    fireEvent.change(screen.getByLabelText(/organization id/),
                     { target: { value: "60036630626" } });
    fireEvent.click(screen.getByRole("button", { name: /^Add / }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes on Cancel without adding anything", async () => {
    const add = vi.spyOn(papi, "addConnection").mockResolvedValue({} as never);
    mountPanel();
    await openAdd();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(add).not.toHaveBeenCalled();
  });
});

describe("ConnectionsPanel — the access list", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("opens on Zoho and lists Zoho's scopes, with the string to paste", async () => {
    mountPanel();
    await openAdd();
    expect(await screen.findByText(/What Zoho Books must let it read/))
      .toBeInTheDocument();
    // Three times, deliberately: once in the list with what it buys, and once
    // in each of the two strings an owner can paste into the Zoho console —
    // the full grant and the minimum. A required scope appears in both.
    expect(screen.getAllByText(/^ZohoBooks\.bills\.READ/)).toHaveLength(3);
    expect(screen.getAllByRole("button", { name: "Copy" })).toHaveLength(2);
  });

  it("offers the minimum grant beside the full one, each copyable on its own",
     async () => {
    // `Permission.required` already separates two different days — without a
    // required grant no sync runs at all, without an optional one a screen
    // stays empty. Until both strings existed, an owner whose policy is to
    // grant the least that works had to assemble it by hand from the table.
    mountPanel();
    await openAdd();
    expect(await screen.findByText(/Everything this platform reads/))
      .toBeInTheDocument();
    expect(screen.getByText(/The least that still runs a sync/))
      .toBeInTheDocument();

    // The full set leads, and it is the one that carries the optional scope.
    expect(screen.getByText("ZohoBooks.bills.READ,ZohoBooks.users.READ"))
      .toBeInTheDocument();

    // Two buttons, two clipboards: one shared "Copied" flag would light up
    // under both and claim something that was never copied.
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    const [full, minimum] = screen.getAllByRole("button", { name: "Copy" });

    fireEvent.click(minimum);
    expect(writeText).toHaveBeenCalledWith("ZohoBooks.bills.READ");
    await waitFor(() => expect(minimum).toHaveTextContent("Copied"));
    expect(full).toHaveTextContent("Copy");
  });

  it("swaps the whole list when another system is picked", async () => {
    mountPanel();
    await openAdd();
    fireEvent.click(await screen.findByRole("button", { name: "Oracle NetSuite" }));

    expect(await screen.findByText("Setup → REST Web Services")).toBeInTheDocument();
    expect(screen.getByText(/What Oracle NetSuite must let it read/)).toBeInTheDocument();
    // The defect, stated as an assertion: no Zoho scope survives the switch.
    await waitFor(() =>
      expect(screen.queryAllByText("ZohoBooks.bills.READ")).toHaveLength(0));
    // NetSuite's grants are clicked in a console, not pasted, so there is
    // nothing to copy — an empty box would be worse than none.
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
  });

  it("says required and optional apart, per system", async () => {
    mountPanel();
    await openAdd();
    fireEvent.click(await screen.findByRole("button", { name: "Oracle NetSuite" }));
    const row = (await screen.findByText("Transactions → Bill (View)")).closest("tr");
    expect(row).toHaveTextContent("optional");
    expect((await screen.findByText("Setup → REST Web Services")).closest("tr"))
      .toHaveTextContent("required");
  });
});

// ── which stage of the grant the box holds ──────────────────────────────────
//
// A Zoho grant code and the refresh token it produces are indistinguishable by
// sight — both `1000.xxxxxxxx.yyyyyyyy`. A single box labelled "Refresh token"
// accepts either, and the one it should not accept is the one the API console
// actually gives you: the connection is created, its first check fails with
// `invalid_code`, and the message speaks of a *revoked* token on a credential
// a minute old.
//
// So the field asks, and these pin that the answer reaches the server as the
// field name rather than as a value it has to sniff.
describe("ConnectionsPanel — grant code or refresh token", () => {
  beforeEach(() => vi.restoreAllMocks());

  async function fillZohoForm() {
    const add = vi.spyOn(papi, "addConnection")
      .mockResolvedValue({} as never);
    mountPanel();
    await openAdd();
    fireEvent.change(await screen.findByLabelText(/^Client ID/), {
      target: { value: "1000.APP" },
    });
    fireEvent.change(screen.getByLabelText(/^Client secret/),
                     { target: { value: "s3cr3t" } });
    fireEvent.change(screen.getByLabelText(/organization id/),
                     { target: { value: "60036630626" } });
    return add;
  }

  it("defaults to the grant code, because that is what the console hands you",
     async () => {
    const add = await fillZohoForm();
    // The default is the assertion: a refresh token exists at all only once
    // somebody has run the exchange by hand, which is the step this removes.
    fireEvent.change(screen.getByLabelText(/^Grant code/),
                     { target: { value: "1000.code.fresh" } });
    fireEvent.click(screen.getByRole("button", { name: /^Add / }));

    await waitFor(() => expect(add).toHaveBeenCalled());
    expect(add.mock.calls[0][1]).toMatchObject({ grant_code: "1000.code.fresh" });
    expect(add.mock.calls[0][1]).not.toHaveProperty("refresh_token");
  });

  it("sends a refresh token as one when that is what was pasted", async () => {
    const add = await fillZohoForm();
    fireEvent.click(screen.getByRole("button", { name: "Refresh token" }));
    fireEvent.change(screen.getByLabelText(/^Refresh token/),
                     { target: { value: "1000.rt.byhand" } });
    fireEvent.click(screen.getByRole("button", { name: /^Add / }));

    await waitFor(() => expect(add).toHaveBeenCalled());
    expect(add.mock.calls[0][1]).toMatchObject({ refresh_token: "1000.rt.byhand" });
    expect(add.mock.calls[0][1]).not.toHaveProperty("grant_code");
  });
});

// ── what the rotate form opens on ───────────────────────────────────────────
//
// A rotation replaces the secrets whole and keeps the non-secret settings it
// does not name (`rotate_erp_credential`), so the form's job is to show which
// values are about to be carried forward. It read the wrong dictionary to do
// it: a connection carries `config`, the per-company half, and
// `credential_config`, the sign-in's own half, and the two are disjoint on
// every connector — so every box opened blank under a sentence promising that
// a filled box is the value the connection is on. Nothing failed, because an
// empty prefill and no prefill render identically. That is what these pin.

const BC_CONNECTION: ZohoConnection = {
  connection_id: "cx1",
  connector: "dynamics365",
  connector_label: "Dynamics 365 Business Central",
  label: "US Books",
  zoho_organization_id: "bc-guid",
  enabled: true,
  credential_id: "cr1",
  client_id: null,
  credential_label: "Business Central sign-in",
  credential_rotated_at: null,
  accounts_base: "",
  api_base: "",
  config: { company_id: "bc-guid" },
  credential_config: { tenant_id: "t1", client_id: "c1", environment: "sandbox" },
  last_checked_at: null,
  last_check_ok: null,
  last_check_detail: null,
  created_at: null,
  last_sync: null,
  suggested_since: "2026-01-01",
  covered_from: null,
};

function mountWithConnection() {
  vi.spyOn(papi, "listConnections").mockResolvedValue({
    connections: [BC_CONNECTION], credentials: [], can_manage: true,
    source_mode: "api", pooling_note: "Everything pools.",
  });
  vi.spyOn(papi, "connectorCatalog").mockResolvedValue({ connectors: CATALOG });
  return render(
    <ConnectionsPanel
      session={SESSION}
      canSync
      onSync={vi.fn()}
      busyConnections={[]}
      starting={false}
    />);
}

async function openRotate() {
  fireEvent.click(await screen.findByRole("button", { name: /Replace the sign-in/ }));
}

describe("ConnectionsPanel — replacing a sign-in", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("opens on the stored settings, so an omission is visible before it is made",
     async () => {
    mountWithConnection();
    await openRotate();
    // The optional one is the whole incident: a Business Central rotation that
    // did not re-type `environment` used to read as "put it back to the
    // default", and the connection moved from sandbox onto production against
    // a tenant where the same company GUID exists.
    expect(await screen.findByLabelText(/^Environment/)).toHaveValue("sandbox");
    expect(screen.getByLabelText(/^Directory \(tenant\) ID/)).toHaveValue("t1");
    expect(screen.getByLabelText(/^Application \(client\) ID/)).toHaveValue("c1");
  });

  it("leaves every secret box empty", async () => {
    // Write-only by construction: no response has ever carried a secret back,
    // so a filled secret box could only be a guess wearing the stored value's
    // clothes — and an operator who trusted it would rotate onto it.
    mountWithConnection();
    await openRotate();
    expect(await screen.findByLabelText(/^Client secret/)).toHaveValue("");
  });

  it("posts the sign-in's own fields and no per-company one", async () => {
    // `company_id` is on `config`, not on the credential, and the rotate
    // endpoint refuses by name any key the credential half does not declare.
    // Seeding the wrong dictionary would turn a rotation into a 400.
    //
    // Asserted on the POSTED object, which this first tried to do by rendering:
    // `queryByLabelText(/^Company/)`. That could not fail. The rotate form maps
    // over `credential_fields` alone — `connection_fields` renders only in the
    // add-a-company form — so no company box exists under ANY prefill, and a
    // `rotatePrefill` that seeded `company_id` straight into `values` passed it
    // green. A seeded-but-unrendered key is invisible until submit, so submit
    // is the only place the claim is testable.
    const rotate = vi.spyOn(papi, "rotateErpConnection")
      .mockResolvedValue({ note: "Rotated." } as never);
    mountWithConnection();
    await openRotate();
    fireEvent.change(await screen.findByLabelText(/^Client secret/),
                     { target: { value: "fresh-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Rotate" }));

    await waitFor(() => expect(rotate).toHaveBeenCalled());
    // Equality, not `not.toHaveProperty("company_id")`: the next wrong key to
    // be seeded will not be the one named in this test.
    expect(rotate.mock.calls[0][2]).toEqual({
      tenant_id: "t1", client_id: "c1", environment: "sandbox",
      client_secret: "fresh-secret",
    });
  });
});
