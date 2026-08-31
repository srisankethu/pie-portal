/** Section F's content: who else runs PIE, what it was worth to one of them,
 *  and what a security review will find.
 *
 * Lifted out of the markup so that "is there anything to show?" is a question
 * with an answer rather than a judgement somebody has to remember to make.
 * Each of the three blocks appears only when its own content exists, and the
 * section itself disappears when none of them do — see `Landing.tsx`.
 *
 * Everything here is a `{{TOKEN}}` today. Replacing one makes its block
 * appear; nothing else has to change. What must replace each is written out in
 * `docs/marketing-placeholders.md`, and the rules are absolute:
 *
 *   - **Customers** are named with written permission or not named. Fewer than
 *     four is fine — the strip renders whatever is real.
 *   - **The case study** is one customer's own figures, read off their value
 *     ledger inside the product, quoted with their permission. Partial is not
 *     allowed: `caseStudy()` returns nothing unless every field is real,
 *     because a case study missing its number is a claim with the evidence
 *     removed.
 *   - **Compliance** states what is true on the day it ships. "In progress" is
 *     a good answer and reads far better than silence; a certification that
 *     does not exist is the claim a diligence process takes apart.
 */
import { allFilled, filled } from "./content";

/** Customers who run PIE and have agreed in writing to be named. */
const CUSTOMER_NAMES = [
  "{{CUSTOMER_LOGO_1}}",
  "{{CUSTOMER_LOGO_2}}",
  "{{CUSTOMER_LOGO_3}}",
  "{{CUSTOMER_LOGO_4}}",
];

/** One customer's own value-ledger figures. All or nothing — see above. */
const CASE_STUDY = {
  erp: "{{CASE_STUDY_ERP}}",
  profile: "{{CASE_STUDY_DISTRIBUTOR_PROFILE}}",
  narrative: "{{CASE_STUDY_NARRATIVE}}",
  marginRecovered: "{{CASE_STUDY_MARGIN_RECOVERED}}",
  linesChecked: "{{CASE_STUDY_LINES_CHECKED}}",
  linesHeld: "{{CASE_STUDY_LINES_HELD}}",
  policyVersion: "{{CASE_STUDY_POLICY_VERSION}}",
  window: "{{CASE_STUDY_WINDOW}}",
};

/** Each row's own status, and the durable half of what it means. The `note` is
 *  prose that stays true whatever the status turns out to be — instructions to
 *  whoever fills it in belong in the checklist, not on the page. */
const COMPLIANCE = [
  {
    name: "SOC 2 Type II",
    status: "{{SOC2_TYPE_II_STATUS}}",
    note: "",
  },
  {
    name: "Data residency",
    status: "{{DATA_RESIDENCY}}",
    note: " — where this deployment keeps your rows, named as a region rather than as a promise.",
  },
  {
    name: "GDPR DPA",
    status: "{{GDPR_DPA_STATUS}}",
    note: ". Erasure is a mechanism here rather than a clause.",
  },
];

export interface ComplianceRow {
  name: string;
  status: string;
  note: string;
}

/** The customers there are, in order. Empty when none has been cleared. */
export function namedCustomers(): string[] {
  return CUSTOMER_NAMES.map(filled).filter((n): n is string => n !== null);
}

/** The case study, or nothing at all. */
export function caseStudy(): { [K in keyof typeof CASE_STUDY]: string } | null {
  return allFilled(CASE_STUDY);
}

/** The compliance rows whose status is known. */
export function complianceRows(): ComplianceRow[] {
  return COMPLIANCE.flatMap((row) => {
    const status = filled(row.status);
    return status === null ? [] : [{ ...row, status }];
  });
}

/** Whether Section F has anything at all to say today. */
export function hasProof(): boolean {
  return namedCustomers().length > 0
    || caseStudy() !== null
    || complianceRows().length > 0;
}
