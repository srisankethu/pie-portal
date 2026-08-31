/** What the public pages know, and what they are still waiting for.
 *
 * Every slot on this site that needs content nobody has supplied yet is
 * declared here as a `{{TOKEN}}`, and **a token never reaches a visitor**. The
 * block that would have shown it is not rendered at all: no empty panel, no
 * "coming soon", no placeholder in a heading. A page missing a section reads
 * as a page about a product; a page showing `{{CASE_STUDY_MARGIN_RECOVERED}}`
 * reads as a building site, and reads that way to the one visitor whose
 * opinion is worth the most.
 *
 * The tokens stay because they are the checklist. `docs/marketing-placeholders.md`
 * says what has to replace each one, `scripts/prerender.mjs` reports at the end
 * of every build which slots are still empty and therefore which sections are
 * hidden, and filling one in is all it takes for its block to appear — there is
 * no second switch to remember.
 *
 * The rule the whole site rests on is unchanged and is the reason for the
 * shape of this file: **nothing here may be invented to fill a gap.** Hiding a
 * section costs a section. Filling it with something plausible costs the
 * argument every other claim on the page depends on.
 */

/** Is this value still a placeholder rather than content?
 *
 *  Whitespace counts as absent. A value of `"   "` is not a token and is not
 *  content either — it renders as nothing while passing every truthiness check
 *  between here and the page, which is precisely how a blank panel ships. It
 *  is the answer a half-finished edit leaves behind, so it is the one this
 *  has to get right. */
export function isPlaceholder(value: string | null | undefined): boolean {
  const text = value?.trim();
  return !text || /^\{\{[A-Z0-9_]+\}\}$/.test(text);
}

/** The value, or `null` where nobody has supplied one yet.
 *
 *  Returning `null` rather than `""` on purpose: an empty string renders as
 *  nothing *and* passes a truthiness check in half the places it is used,
 *  which is how a blank panel ends up on a page. `null` fails loudly at the
 *  type level if a caller forgets to handle it. */
export function filled(value: string | null | undefined): string | null {
  return isPlaceholder(value) ? null : (value as string).trim();
}

/** Every one of a set, or `null` if any is missing.
 *
 *  For content that only means something whole. A case study without its
 *  figure is not a shorter case study; it is a claim with the evidence taken
 *  out, which is worse than no case study at all. */
export function allFilled<T extends Record<string, string>>(
  values: T,
): { [K in keyof T]: string } | null {
  const out = {} as { [K in keyof T]: string };
  for (const key of Object.keys(values) as (keyof T)[]) {
    const value = filled(values[key]);
    if (value === null) return null;
    out[key] = value;
  }
  return out;
}
