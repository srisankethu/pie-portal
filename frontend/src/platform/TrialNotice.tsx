/** Tell a tenant its free month is running out, before it runs out.
 *
 * `GET /api/v1/entitlements` has existed since plans landed, computes the trial
 * correctly, and had no caller. So the sequence was: sign up, connect Zoho, get
 * thirty days of Commercial Intelligence, and then one morning the decision
 * layer is simply gone — no warning, no date, and nothing on screen saying why.
 * That is the worst version of an expiry, because it reads as a fault.
 *
 * Three decisions worth stating, because each could reasonably have gone the
 * other way:
 *
 * **It says nothing until the last stretch.** A countdown from day one is a
 * banner people stop seeing by day three, and then it is not there when it
 * matters. Silent until `NOTICE_FROM_DAYS`, then plainly, then more urgently
 * inside `URGENT_FROM_DAYS`.
 *
 * **There is no "Upgrade" button, deliberately.** This deployment has no
 * billing — `set_plan` is an operator command and there is no API for it, on
 * purpose. A button that opened a checkout nobody built would be worse than no
 * button, so the notice says what will happen and who can stop it, and stops
 * there. When billing exists, this is the place the button goes.
 *
 * **Managers and owners only.** A salesperson cannot act on it, and the screens
 * they lose are already the ones their role does not open. Telling them a
 * licence is expiring is a worry with no lever attached.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import { useQuery } from "@tanstack/react-query";

import { papi } from "./api";
import type { PlatformSession } from "./types";

/** Stay quiet until this many days remain. */
const NOTICE_FROM_DAYS = 10;
/** Inside this, it stops being information and becomes something to act on. */
const URGENT_FROM_DAYS = 3;

/** What a feature key costs the reader, in their words rather than the plan's.
 *
 *  The *list* comes from the server (`loses_on_expiry`, derived from its own
 *  plan map) so the client never decides which tier holds what. This only
 *  supplies the phrasing — an unknown key falls back to the key itself rather
 *  than being dropped, because a feature silently missing from this sentence is
 *  a feature somebody loses without being told. */
const LOSS_LABEL: Record<string, string> = {
  intelligence: "the decision queue and the insight screens",
  multi_company: "all but one connected company",
};

function daysPhrase(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

export function TrialNotice({ session }: { session: PlatformSession }) {
  const { data } = useQuery({
    queryKey: ["entitlements", session.organization_id],
    queryFn: () => papi.entitlements(session.token),
  });

  // Silent on failure. This is a courtesy notice, and "the trial notice did not
  // load" at the top of every screen is worse than the notice being absent —
  // the same reasoning `SetupChecklist` gives.
  if (!data?.trial) return null;
  if (session.role === "SALESPERSON") return null;

  const { days_remaining: days, ends_on: endsOn } = data.trial;
  if (days > NOTICE_FROM_DAYS) return null;

  const urgent = days <= URGENT_FROM_DAYS;
  const losing = data.loses_on_expiry
    .map((k) => LOSS_LABEL[k] ?? k)
    .join(" and ");

  return (
    <Alert severity={urgent ? "warning" : "info"} sx={{ mb: 3 }}>
      <AlertTitle>
        Your Commercial Intelligence trial ends {daysPhrase(days)}
        {endsOn ? ` — ${endsOn}` : ""}
      </AlertTitle>
      {/* What actually happens, in the order it will happen. No hedging: the
          Quote Desk genuinely does keep working, and saying so is the
          difference between a deadline and a threat. */}
      After that you lose {losing || "the trial features"}. Quoting, margin
      floors and approvals carry on as they are, on the free plan, and nothing
      you have synced is deleted.
      {session.role === "OWNER"
        ? " To keep the decision layer, speak to whoever runs this deployment — plans are set by the operator, not from these screens."
        : " Your owner can arrange to keep it."}
    </Alert>
  );
}
