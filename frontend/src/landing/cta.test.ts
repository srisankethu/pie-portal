/** The demo CTA must never point a visitor at a placeholder.
 *
 * This is the one button on the site whose failure mode is silent to everyone
 * who maintains the page and loud to everyone who does not: the maintainer
 * sees `{{DEMO_BOOKING_URL}}` in the source and in the build warning, while a
 * buyer sees a primary call to action that goes nowhere. Both states are
 * pinned here — the configured one and the unconfigured one — because the
 * transition between them happens exactly once, unattended, on the day
 * somebody pastes a scheduling link in.
 */
import { describe, expect, it } from "vitest";

import { DEMO_BOOKING_READY, DEMO_BOOKING_URL, demoCta, demoCtaFor } from "./cta";

const FALLBACK = { href: "#signin", label: "Start free" };

describe("while no scheduling link is configured", () => {
  it("sends the button to the caller's fallback, not to the token", () => {
    const cta = demoCtaFor("{{DEMO_BOOKING_URL}}", FALLBACK);
    expect(cta.ready).toBe(false);
    expect(cta.props.href).toBe("#signin");
    expect(cta.props.href).not.toContain("{{");
  });

  it("relabels the button to what it actually does", () => {
    // Not "Book a demo" pointing at a sign-in form. A button that describes an
    // action the visitor cannot take is a smaller lie than a dead link and
    // still a lie, on a page whose whole argument is that its claims check out.
    const cta = demoCtaFor("{{DEMO_BOOKING_URL}}", FALLBACK);
    expect(cta.label).toBe("Start free");
    expect(cta.label).not.toMatch(/demo/i);
  });

  it("opens nothing in a new tab", () => {
    // `target="_blank"` on a same-site fallback would strand the visitor on a
    // second tab of the page they were already reading.
    const cta = demoCtaFor("{{DEMO_BOOKING_URL}}", FALLBACK);
    expect(cta.props.target).toBeUndefined();
    expect(cta.props.rel).toBeUndefined();
  });

  it("carries the caller's handler, so the in-app door still opens", () => {
    const onClick = () => {};
    expect(demoCtaFor("{{X}}", { ...FALLBACK, onClick }).props.onClick).toBe(onClick);
  });
});

describe("once a scheduling link is configured", () => {
  const cta = demoCtaFor("https://cal.example/pie/demo", FALLBACK);

  it("uses it, says 'Book a demo', and opens it in a new tab", () => {
    expect(cta.ready).toBe(true);
    expect(cta.label).toBe("Book a demo");
    expect(cta.props.href).toBe("https://cal.example/pie/demo");
    expect(cta.props.target).toBe("_blank");
    expect(cta.props.rel).toBe("noreferrer");
  });

  it("announces the new tab in its accessible name", () => {
    expect(cta.props["aria-label"]).toBe("Book a demo (opens in a new tab)");
  });

  it("drops the fallback entirely", () => {
    expect(cta.props.onClick).toBeUndefined();
    expect(cta.props.href).not.toBe(FALLBACK.href);
  });
});

describe("the shipped constant", () => {
  it("agrees with itself about whether it is configured", () => {
    expect(DEMO_BOOKING_READY).toBe(!DEMO_BOOKING_URL.startsWith("{{"));
    expect(demoCta(FALLBACK).ready).toBe(DEMO_BOOKING_READY);
  });
});
