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
 * **There is an ask, and it is not a checkout.** This deployment still has no
 * billing, and `set_plan` is still an operator command with no API — an owner
 * who could set their own plan would not have one. What was missing was the
 * other half: an owner had no way to *say* they wanted the plan, so a platform
 * selling three tiers offered no way to buy the upper two. The button records a
 * `PlanChangeRequest` and grants nothing; a person decides it. Its label says
 * so, because "Upgrade" over a control that opens a queue rather than a
 * checkout is the dead button this comment used to be about, wearing a
 * different coat.
 *
 * Once asked, the control is replaced by what was asked and when. An owner who
 * pressed it and still sees a button concludes it did not work, and presses it
 * again — which is the case `request_plan_change` refuses server-side, and
 * refusing something the screen invited is a worse experience than not
 * inviting it.
 *
 * **Managers and owners only.** A salesperson cannot act on it, and the screens
 * they lose are already the ones their role does not open. Telling them a
 * licence is expiring is a worry with no lever attached.
 */
import { useState } from "react";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { formatDate } from "../when";

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
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: ["entitlements", session.organization_id],
    queryFn: () => papi.entitlements(session.token),
  });

  /** Record the ask, then re-read from the server rather than patching what is
   *  on screen. The POST returns the whole entitlement view for that reason —
   *  a control that decided locally that it had worked is a control that lies
   *  when the request was refused. */
  const ask = async () => {
    setAsking(true);
    setError(null);
    try {
      await papi.requestPlan(session.token, "intelligence");
      await queryClient.invalidateQueries({
        queryKey: ["entitlements", session.organization_id] });
    } catch (e) {
      setError(e instanceof Error ? e.message
        : "That could not be sent. Try again, or speak to whoever runs this deployment.");
    } finally {
      setAsking(false);
    }
  };

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
      {session.role === "OWNER" ? null : " Your owner can arrange to keep it."}
      {session.role === "OWNER" && (
        <Box sx={{ mt: 1.5 }}>
          {data.pending_request ? (
            <Typography variant="body2">
              You asked to move to{" "}
              <b>{data.pending_request.requested_plan_label}</b> on{" "}
              {formatDate(data.pending_request.requested_at)}. Whoever runs this
              deployment will be in touch — nothing is charged from these
              screens.
            </Typography>
          ) : (
            <>
              <Button size="small" variant="outlined" disabled={asking}
                      onClick={ask}>
                {asking ? "Sending…" : "Ask to keep Commercial Intelligence"}
              </Button>
              {error && (
                <Typography variant="body2" color="error" sx={{ mt: 1 }}>
                  {error}
                </Typography>
              )}
            </>
          )}
        </Box>
      )}
    </Alert>
  );
}
