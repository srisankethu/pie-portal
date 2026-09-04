/** The console's transport. One credential, one header, one place it lives.
 *
 * Deliberately not `platform/api.ts` and not `authFetch.ts`. Both of those
 * carry a *tenant* session — a cookie, a 401 handler that routes to the
 * sign-in card, the currency side effects of a login — and none of it applies
 * here. Sharing them would mean one module that sometimes speaks for a tenant
 * and sometimes for the vendor, which is the same conflation
 * `docs/operator-console.md` refuses on the server side.
 *
 * **`sessionStorage`, not `localStorage`.** The key is a credential with no
 * expiry and no tenant scope; closing the tab should end the session. That is
 * also why nothing here sets a cookie: a header-only credential has no
 * ambient-authority problem to defend against, so the console needs no CSRF
 * token and the API needs no exemption for it.
 */
const KEY = "pie.operator.key";

export function storedKey(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    // A browser with site data blocked. The console still works for as long as
    // the tab lives; it just asks for the key again on reload.
    return "";
  }
}

export function rememberKey(key: string): void {
  try {
    sessionStorage.setItem(KEY, key);
  } catch {
    /* see above — not remembering is a degradation, not a failure */
  }
}

export function forgetKey(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* nothing to forget */
  }
}

export class OperatorError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** One call. Throws `OperatorError` with the server's own sentence.
 *
 * The server's refusals are written for a person — "sanketh has no active
 * break-glass grant for org_x. Open one with a justification first" — and are
 * more useful than anything this layer could say instead, which is the same
 * rule `landing/ContactForm` follows.
 */
export async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = storedKey();
  const res = await fetch(`/api/v1/operator${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const detail = await res.json()
      .then((j) => (typeof j?.detail === "string" ? j.detail : ""))
      .catch(() => "");
    throw new OperatorError(res.status, detail || `${res.status} from ${path}`);
  }
  return res.json() as Promise<T>;
}

export const get = <T,>(path: string) => call<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
export const del = <T,>(path: string) => call<T>(path, { method: "DELETE" });

// ── what the endpoints answer with ──────────────────────────────────────────
export interface Whoami { operator_id: string; key_id: string; name: string }

export interface Enquiry {
  id: string;
  company: string; name: string; email: string; phone: string;
  plan: string; erp: string; message: string;
  created_at: string | null;
  /** When somebody was told this arrived — not when it was answered. Null on a
   *  waiting row means the alert has not run or could not deliver, which is the
   *  one place a broken webhook is visible. */
  notified_at: string | null;
  status: string;
  handled_at: string | null;
  handled_by: string | null;
}

export interface OrgRow {
  organization_id: string;
  name: string;
  plan: string;
  wants: string | null;
  currency: string | null;
  created_at: string | null;
}

export interface AskedAtSignup {
  organization_id: string; name: string; on: string; wants: string;
}

export interface InProductRequest {
  request_id: string; organization_id: string;
  from: string; to: string;
  requested_at: string | null; requested_by: string; note: string | null;
}

export interface Requests {
  at_signup: AskedAtSignup[];
  in_product: InProductRequest[];
  enquiries: Enquiry[];
}

export interface AccessEvent {
  at: string | null; action: string; staff: string; detail: string;
}

export interface AccessView {
  active_grant: { grant_id: string; expires_at: string | null;
                  justification: string } | null;
  events: AccessEvent[];
}

export interface SupportView {
  organization_id: string;
  people: { total: number; active: number };
  last_sync: { started_at: string | null; finished_at: string | null;
               status: string } | null;
}
