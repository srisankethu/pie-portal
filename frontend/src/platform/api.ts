import type { DecisionDetail, DecisionSummary, PlatformSession } from "./types";

const KEY = "pie_platform_session";

export function loadPlatformSession(): PlatformSession | null {
  const raw = localStorage.getItem(KEY);
  return raw ? (JSON.parse(raw) as PlatformSession) : null;
}
export function savePlatformSession(s: PlatformSession) {
  localStorage.setItem(KEY, JSON.stringify(s));
}
export function clearPlatformSession() {
  localStorage.removeItem(KEY);
}

async function req<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json()) as T;
}

interface LoginResp {
  token: string;
  role: PlatformSession["role"];
  name: string;
  user_id: string;
  organization_id: string;
}

export const papi = {
  login: (email: string, password: string) =>
    req<LoginResp>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  listDecisions: (t: string, q: { type?: string; status_filter?: string } = {}) => {
    const p = new URLSearchParams();
    if (q.type) p.set("type", q.type);
    if (q.status_filter) p.set("status_filter", q.status_filter);
    const qs = p.toString();
    return req<DecisionSummary[]>(`/api/v1/decisions${qs ? "?" + qs : ""}`, {}, t);
  },

  getDetail: (t: string, id: string) => req<DecisionDetail>(`/api/v1/decisions/${id}/detail`, {}, t),

  act: (t: string, id: string, body: { action: string; note?: string; reason?: string }) =>
    req<DecisionSummary>(`/api/v1/decisions/${id}/action`, { method: "POST", body: JSON.stringify(body) }, t),

  demoSeed: (t: string) => req<Record<string, unknown>>("/api/v1/internal/demo-seed", { method: "POST" }, t),
};

// Demo accounts for the three roles (any password).
export const DEMO_ACCOUNTS: { role: PlatformSession["role"]; email: string; label: string }[] = [
  { role: "SALESPERSON", email: "r.nair@sanketh.in", label: "Salesperson" },
  { role: "SALES_MANAGER", email: "m.rao@sanketh.in", label: "Sales manager" },
  { role: "OWNER", email: "s.menon@sanketh.in", label: "Owner" },
];
