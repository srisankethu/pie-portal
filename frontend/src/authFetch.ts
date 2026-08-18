/** How this app talks to its API, in one place.
 *
 *  The session token used to live in `localStorage` and ride on an
 *  `Authorization` header that every call site assembled for itself. It now
 *  lives in an httpOnly cookie the page cannot read, which the browser attaches
 *  on its own — so the call sites stopped needing to carry a secret, and gained
 *  one obligation instead: the `X-PIE-App` header.
 *
 *  That header is the CSRF control. The server requires it on any
 *  cookie-authenticated request that can change something, because a cross-site
 *  form POST cannot set a header at all and a cross-site `fetch` that sets one
 *  triggers a preflight this API refuses. Sent on every request rather than only
 *  the unsafe ones: a rule with an exception is a rule somebody gets wrong, and
 *  it costs nothing on a GET.
 *
 *  This module sits outside `platform/` for the reason `Tip.tsx` does — both
 *  apps in this repo call the same API, and two copies of the transport would
 *  be two places to remember the header.
 */

/** Named here rather than spelled at each call site; the server's `APP_HEADER`
 *  in `authz.py` is the other half of this pair. */
export const APP_HEADER = "X-PIE-App";

/** `RequestInit` for an authenticated call.
 *
 *  `token` is optional and, in the browser, normally absent: the cookie is the
 *  credential. It is still honoured because programmatic callers — tests, and
 *  anything not a browser — have nowhere for a cookie to live and pass a bearer
 *  token instead. When it is empty, no `Authorization` header is sent and the
 *  cookie is what authenticates.
 */
export function authInit(opts: RequestInit = {}, token?: string): RequestInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    [APP_HEADER]: "1",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return {
    ...opts,
    // The default for a same-origin request already, stated because this is the
    // line that carries the session now — an future change to `mode` or a move
    // to a second origin would otherwise silently sign every request out.
    credentials: "same-origin",
    headers: { ...headers, ...(opts.headers || {}) },
  };
}
