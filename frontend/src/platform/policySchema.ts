/** What a margin policy is allowed to say, as rules rather than as an if.
 *
 * The settings form validated nothing until you pressed Save, and then threw on
 * the *first* bad field. Somebody who mistyped three boxes fixed one, saved,
 * and met the next error — three round trips to learn what one glance should
 * have told them. These rules run against the draft as it is typed, so every
 * problem is visible at once and next to the box that has it.
 *
 * Zod rather than React Hook Form because the fields are *server-driven* -
 * `policy.fields` arrives from `commercial/policy.EDITABLE` and changes when
 * the backend adds a threshold. RHF would add a second source of "what fields
 * exist" for no behaviour this form lacks.
 *
 * The ladder is the rule worth having: approval floor ≤ review floor ≤ target
 * margin. Out of order, a price is below the review floor and above the
 * approval floor beneath it, and two screens argue forever. Cross-field rules
 * are what a schema states better than an inline `&&`, and the message lands on
 * the offending field rather than in a banner.
 */
import { z } from "zod";

import type { PolicyField, PolicyKind } from "./types";

/** The three floors, in the order they have to hold. */
export const LADDER = ["min_margin", "margin_floor", "target_margin_default"] as const;

/** A number typed into a box, with a message that says what to type instead.
 *
 * `""` is "empty", not zero — a cleared box is somebody midway through editing,
 * not somebody asking for a zero threshold. */
const numberFrom = (what: string) =>
  z.string()
    .trim()
    .min(1, `${what} cannot be blank.`)
    .refine((v) => Number.isFinite(Number(v)), `${what} must be a number.`)
    .transform(Number);

function ruleFor(field: PolicyField): z.ZodTypeAny {
  const label = field.label;
  const kind: PolicyKind = field.kind;

  if (kind === "flag") {
    // A switch. Only ever "true"/"false" from the control that renders it.
    return z.enum(["true", "false"]);
  }
  if (kind === "days") {
    return numberFrom(label).refine(
      (n) => Number.isInteger(n) && n >= 1,
      `${label} is a number of days — at least one, and whole.`);
  }
  if (kind === "band_edges") {
    return z.string().refine(
      (v) => {
        const parts = v.split(/[,\s]+/).filter(Boolean);
        return parts.length > 0 && parts.every((p) => Number.isFinite(Number(p)));
      },
      `${label}: whole numbers separated by commas.`);
  }
  if (kind === "money") {
    return numberFrom(label).refine((n) => n >= 0,
                                    `${label} cannot be negative.`);
  }
  // A ratio, typed as a percentage. 0–100 rather than 0–1: the box says "%".
  return numberFrom(label).refine(
    (n) => n >= 0 && n <= 100,
    `${label} is a percentage — between 0 and 100.`);
}

/** Build the schema for whatever fields this server actually offers.
 *
 * Runtime rather than compile time on purpose. `EDITABLE` is the server's list
 * and it grows; a hand-written schema here would be a second copy of it, and
 * the copy that forgets a field is the one that lets a bad value through.
 */
export function policySchema(fields: PolicyField[]) {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const f of fields) {
    if (f.kind === "family_margins") continue;   // its own editor, its own rules
    shape[f.field] = ruleFor(f);
  }
  return z.object(shape).superRefine((values, ctx) => {
    const [approval, review, target] = LADDER.map((k) => Number(values[k]));
    if (![approval, review, target].every(Number.isFinite)) return;
    if (approval > review) {
      ctx.addIssue({
        code: "custom",
        path: ["min_margin"],
        message: "The approval floor must sit at or below the review floor — "
          + "otherwise a line is flagged for review and cleared for sending at "
          + "the same time.",
      });
    }
    if (review > target) {
      ctx.addIssue({
        code: "custom",
        path: ["margin_floor"],
        message: "The review floor must sit at or below the target margin — "
          + "otherwise every line that hits its target is flagged.",
      });
    }
  });
}

/** Field name → the first thing wrong with it. Empty when the draft is valid.
 *
 * First rather than all: two messages under one box is one more than anybody
 * reads, and the second is usually a consequence of the first.
 */
export function policyProblems(
  fields: PolicyField[], draft: Record<string, string>,
): Record<string, string> {
  const result = policySchema(fields).safeParse(draft);
  if (result.success) return {};
  const out: Record<string, string> = {};
  for (const issue of result.error.issues) {
    const key = String(issue.path[0] ?? "");
    if (key && !(key in out)) out[key] = issue.message;
  }
  return out;
}
