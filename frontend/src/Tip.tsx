// The tooltip primitive, shared by both apps in this repo — the Decision
// Platform and the Quote Builder. It lives outside ``platform/`` because a
// tooltip is not a platform concept, and the Quote Builder needs the same one:
// two implementations would drift, and a term explained one way on the quote
// screen and another way on the account screen is worse than no explanation.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

/* ── tooltips ───────────────────────────────────────────────────────────────
 *
 * One rule decides where these go, because a tooltip on everything is a
 * tooltip on nothing: attach one where the label is *accurate but
 * incomplete* — a term with a specific meaning in this platform ("review
 * floor" is not the same as "approval floor"), a number whose units are not
 * obvious from the field (a margin is a ratio, a movement is percentage
 * points), or a control whose consequence is invisible from the control
 * itself (deleting a connection keeps the data it pulled). Never to restate
 * the label, and never to hide something a person needs in order to act —
 * that belongs on the page.
 *
 * The trigger is a real button, so the text is reachable by keyboard and by
 * tap, not only by hover. A hover-only tooltip does not exist on a phone.
 */

const WIDTH = 300; // must match .tip-bubble max-width
const GAP = 8;
const MARGIN = 10; // keep this far off the viewport edge

/** Where to put the bubble, in viewport coordinates.
 *
 * Positioned `fixed` rather than absolute on purpose. Several of the places a
 * tooltip is most needed — the Customer × Item table, the peer table — scroll
 * horizontally, and `overflow-x: auto` clips vertically too, so an absolutely
 * positioned bubble on a column header is cut off exactly where the header
 * needs explaining. Fixed coordinates escape every ancestor's overflow.
 */
function useTipPlacement(open: boolean) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; below: boolean } | null>(null);

  useEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const place = () => {
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const left = Math.min(
        Math.max(MARGIN, r.left + r.width / 2 - WIDTH / 2),
        Math.max(MARGIN, window.innerWidth - WIDTH - MARGIN),
      );
      // Flip under the trigger when there is not enough room above it — which
      // is the case for every tooltip in a table's header row.
      const below = r.top < 140;
      setPos({ left, top: below ? r.bottom + GAP : r.top - GAP, below });
    };
    place();
    // Scrolling or resizing while a tooltip is open would otherwise leave it
    // stranded where the trigger used to be.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  return { ref, pos };
}

/** Wraps any trigger. `children` is what the user points at; `text` is the why. */
export function Tip({
  text,
  children,
  label = "Explain",
}: {
  text: ReactNode;
  children?: ReactNode;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const { ref, pos } = useTipPlacement(open);

  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  return (
    <span
      className="tip-wrap"
      ref={ref}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={close}
    >
      <button
        type="button"
        className="tip-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={close}
        onClick={(e) => {
          e.preventDefault();
          // Tooltips live inside clickable table rows. Asking what a column
          // means must not also navigate away from the table.
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {children ?? <span className="tip-mark" aria-hidden="true">?</span>}
      </button>
      {open && pos && (
        <span
          role="tooltip"
          id={id}
          className="tip-bubble"
          style={{
            left: pos.left,
            top: pos.top,
            transform: pos.below ? undefined : "translateY(-100%)",
          }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** A label with its explanation attached — the common case, so it is one call. */
export function Labelled({
  children,
  tip,
  className = "",
}: {
  children: ReactNode;
  tip: ReactNode;
  className?: string;
}) {
  return (
    <span className={`labelled ${className}`}>
      {children}
      <Tip text={tip} />
    </span>
  );
}
