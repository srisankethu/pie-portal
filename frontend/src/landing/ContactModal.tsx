/** The demo-request form as a dialog, opened by any "Book a demo" button.
 *
 * The form used to sit at the foot of the plans section, always on screen. A
 * visitor who pressed a panel's button was scrolled to it, which meant the
 * page's one conversion surface competed with the section that was meant to
 * lead into it — and everybody who was not asking anything scrolled past a
 * nine-field form to reach the closing block. It opens on the press now, over
 * whatever was being read.
 *
 * The form itself is `ContactForm`: this file owns *how it opens*, and nothing
 * about what it asks or where it posts. A second copy of those fields is the
 * failure this split exists to prevent.
 *
 * What a dialog owes a person, done by hand rather than with `<dialog>` or a
 * component library:
 *
 * - `<dialog>`.showModal() would give focus containment and Escape for free,
 *   but jsdom 25 does not implement it, so every test of this file would be
 *   testing a stub. MUI's `Dialog` is the app's answer and deliberately not
 *   this page's — `landing/` imports no component library, and pulling one in
 *   here would land it in the prerender's import graph for one overlay.
 * - Escape closes, the backdrop closes, and focus moves into the dialog on
 *   open and back to whatever opened it on close. Tab is contained: a modal
 *   whose Tab key walks into the page behind it is a modal only for people
 *   using a mouse.
 * - The page behind does not scroll while it is open.
 *
 * Nothing here runs during the prerender: the dialog is not rendered until a
 * visitor opens it, so `renderLandingMarkup` still touches no `window`.
 */
import { useEffect, useRef } from "react";

import { ContactForm } from "./ContactForm";

/** Everything in the dialog a keyboard can reach. Read at the moment Tab is
 *  pressed rather than cached on open, because the form's own fields go
 *  `disabled` while it is sending and the thank-you replaces all of them. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),'
  + ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ContactModal({ onClose }: {
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);

  // Focus in on open, and back out to the opener on close.
  //
  // Onto the dialog itself rather than the first field: a screen reader then
  // reads the dialog's name — the form's own heading, via `aria-labelledby` —
  // before the person is standing in a text box with no idea what it is part
  // of. Tab from there reaches the close button, then the fields, in order.
  //
  // Restoring focus matters as much as taking it: a dialog that closes and
  // leaves focus nowhere drops a keyboard visitor back at the top of a long
  // page, several sections above the button they pressed.
  //
  // `preventScroll`, because taking focus asks the browser to scroll the
  // element into view and this element is taller than a phone: at 393x727 the
  // scroll landed the sheet at top: -14px, with the close button off the top
  // edge of the screen and nothing on screen suggesting it had moved. The
  // backdrop is the scroller and it starts at the top, which is where the
  // heading is.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    dialog.current?.focus({ preventScroll: true });
    return () => opener?.focus?.();
  }, []);

  // The page behind must not scroll under the overlay. The previous value is
  // restored rather than cleared: this is a shared property and blanking it
  // would silently undo anything else that had set it.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab" || !dialog.current) return;
      const stops = Array.from(
        dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (stops.length === 0) return;
      const first = stops[0];
      const last = stops[stops.length - 1];
      const at = stops.indexOf(document.activeElement as HTMLElement);
      // Not on a stop: focus is on the dialog itself, which is where it starts,
      // or it has got out somehow. Tab enters at the top, Shift+Tab at the
      // bottom — the same two ends the wrap below uses.
      if (at === -1) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && at === 0) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && at === stops.length - 1) {
        e.preventDefault();
        first.focus();
      }
      // Everywhere in between is left alone: the browser's own order is better
      // than anything this could impose.
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="lp-modal-backdrop"
         // The backdrop closes, the dialog inside it does not: the test is
         // where the press landed, not where it bubbled to, so a drag that
         // starts on a field and ends outside does not close the form the
         // visitor is filling in.
         onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="lp-modal" role="dialog" aria-modal="true"
           aria-labelledby="lp-form-head" ref={dialog} tabIndex={-1}>
        <button className="lp-modal-close" type="button" onClick={onClose}
                aria-label="Close">
          <span aria-hidden="true">×</span>
        </button>
        <ContactForm />
      </div>
    </div>
  );
}
