// Measure the container, then draw in real pixels.
//
// The alternative — a fixed `viewBox` with `preserveAspectRatio="none"` — is
// what the first version of the waterfall did, and it is wrong in a way that is
// easy to miss until you look closely: a 100-unit-wide viewBox stretched across
// a 900px panel scales *everything* horizontally by nine, including glyphs. Text
// comes out smeared, stroke widths differ between the horizontal and vertical,
// and rounded corners turn into ellipses.
//
// Measuring also makes charts genuinely size-adaptive rather than merely
// scalable. A stretched chart at 380px is the same chart, unreadable; a measured
// one can *decide* — drop alternate axis labels, shorten a currency, switch a
// horizontal layout to a vertical one — because it knows how much room it has.

import { useCallback, useEffect, useRef, useState } from "react";

export interface Size {
  width: number;
  height: number;
}

/** Breakpoints for chart decisions. Named for what they mean to a chart, not
 *  for devices — a narrow panel on a wide screen has the same problem a phone
 *  does, and a device-width media query would miss it. */
export interface Room {
  /** Under ~420px: room for a headline and one mark, not a labelled axis. */
  cramped: boolean;
  /** Under ~680px: labels need thinning or rotating. */
  tight: boolean;
  width: number;
  height: number;
}

const CRAMPED = 420;
const TIGHT = 680;

export function useMeasure<T extends HTMLElement = HTMLDivElement>(): [
  (node: T | null) => void,
  Room,
] {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const observer = useRef<ResizeObserver | null>(null);

  // A callback ref rather than useRef + useEffect: the node can be swapped when
  // a panel changes state (loading → ready remounts the child), and a callback
  // ref is the only form that reliably sees both the detach and the attach.
  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    if (!node) return;

    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      // `contentRect` excludes padding, which is what a chart actually gets.
      const { width, height } = entry.contentRect;
      setSize((prev) =>
        // Guard against a resize loop: rendering can nudge the height by a
        // fraction of a pixel, and feeding that back would spin forever.
        Math.abs(prev.width - width) < 1 && Math.abs(prev.height - height) < 1
          ? prev
          : { width, height },
      );
    });
    ro.observe(node);
    observer.current = ro;
  }, []);

  useEffect(() => () => observer.current?.disconnect(), []);

  return [
    ref,
    {
      width: size.width,
      height: size.height,
      cramped: size.width > 0 && size.width < CRAMPED,
      tight: size.width > 0 && size.width < TIGHT,
    },
  ];
}

/** Thin a list of labels so they do not collide, keeping the first and last.
 *
 *  Every chart that puts a label per data point needs this, and every one that
 *  does it ad hoc gets the ends wrong — dropping the final month is how a time
 *  axis stops saying where it ends. */
export function thinLabels<T>(items: T[], room: Room, perLabel = 58): (T | null)[] {
  if (items.length === 0) return [];
  const fits = Math.max(2, Math.floor(room.width / perLabel));
  if (items.length <= fits) return items;
  const step = Math.ceil(items.length / fits);
  return items.map((item, i) =>
    i === 0 || i === items.length - 1 || i % step === 0 ? item : null,
  );
}

/** Compact money for an axis, where the full figure will not fit.
 *
 *  **Indian compaction is done by hand, on purpose.** `Intl` with `en-IN` and
 *  `notation: "compact"` is not stable across ICU versions: Node renders 85,040
 *  as `₹85.0K` and Chromium renders the same call as `₹85T`. "T" reads as
 *  *trillion* to an English reader, so the same chart said two different things
 *  depending on where it ran — and the browser was the one that got it wrong.
 *  Lakh and crore are unambiguous in that market and are applied here directly.
 *
 *  Below a lakh nothing is compacted at all: `₹85,040` is short enough to fit
 *  and needs no decoding, and an abbreviation that saves two characters at the
 *  cost of a moment's thought is a bad trade on a label someone is scanning. */
export function compactMoney(value: number, currency: string): string {
  const sign = value < 0 ? "−" : "";
  const n = Math.abs(value);

  if (currency === "INR") {
    const symbol = "₹";
    if (n >= 1e7) return `${sign}${symbol}${trim(n / 1e7)}Cr`;
    if (n >= 1e5) return `${sign}${symbol}${trim(n / 1e5)}L`;
    return `${sign}${symbol}${Math.round(n).toLocaleString("en-IN")}`;
  }

  try {
    return (
      sign +
      new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(n)
    );
  } catch {
    return `${sign}${Math.round(n).toLocaleString("en-US")}`;
  }
}

/** One decimal, but not a trailing `.0` — `3.6L` and `2L`, never `2.0L`. */
function trim(v: number): string {
  return v.toFixed(1).replace(/\.0$/, "");
}
