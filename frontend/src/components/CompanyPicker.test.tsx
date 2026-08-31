// The company question, and the two things it must not do.
//
// It must not offer a default — each company decodes its own item master, and
// a pre-selected one is the server's refusal undone in the browser. And it must
// name the companies rather than their ids alone, because "cx_4u" is not what
// anybody calls the business they are quoting from.
//
// The dialog only ever opens on the server's refusal (`CompanyRequired`), so
// there is no "one company" case to test here: with one company the server
// answers and this component is never mounted. That path is pinned on the
// server, in test_per_company_resolution.py.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CompanyPicker } from "./CompanyPicker";

const COMPANIES = [
  { connection_id: "cx_sls", label: "SLS Engineers" },
  { connection_id: "cx_4u", label: "4U Precision" },
];

describe("the company picker", () => {
  it("offers every company by name and selects none of them", () => {
    render(<CompanyPicker open companies={COMPANIES} customer="Pitti"
                          onPick={() => {}} onCancel={() => {}} />);

    expect(screen.getByText("SLS Engineers")).toBeTruthy();
    expect(screen.getByText("4U Precision")).toBeTruthy();
    // Nothing is chosen for the person: the server refused to choose, and a
    // pre-selected option would be that refusal undone one layer up.
    expect(document.querySelectorAll(".Mui-selected").length).toBe(0);
  });

  it("returns the connection id, not the label", () => {
    const onPick = vi.fn();
    render(<CompanyPicker open companies={COMPANIES} customer="Pitti"
                          onPick={onPick} onCancel={() => {}} />);

    fireEvent.click(screen.getByText("4U Precision"));
    expect(onPick).toHaveBeenCalledWith("cx_4u");
  });

  it("says which customer the question is part of, and can be cancelled", () => {
    const onCancel = vi.fn();
    render(<CompanyPicker open companies={COMPANIES} customer="Pitti Engineering"
                          onPick={() => {}} onCancel={onCancel} />);

    expect(screen.getByText(/Pitti Engineering/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("falls back to the id where a company has no label", () => {
    render(<CompanyPicker open customer="Pitti"
                          companies={[{ connection_id: "cx_nameless", label: "" }]}
                          onPick={() => {}} onCancel={() => {}} />);
    expect(screen.getByText("cx_nameless")).toBeTruthy();
  });
});
