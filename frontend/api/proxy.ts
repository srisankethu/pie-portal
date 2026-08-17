// Proxies /api/* to the backend, whose host comes from the BACKEND_URL
// environment variable rather than a literal in vercel.json — that file's
// `rewrites` are static config and cannot interpolate a variable, so the
// indirection lives here instead. Changing backends is then a dashboard edit
// and a redeploy, not a commit.
//
// Routed by an explicit rewrite in vercel.json, NOT by a catch-all filename.
// This file used to be `api/[...path].ts`, and Vercel matched it for a single
// segment only: /api/health reached it while /api/v1/auth/login returned 404,
// so the app loaded and every sign-in failed. A fixed filename plus a rewrite
// that captures the rest of the path into `__path` does not depend on how a
// platform reads brackets in a filename.
export const config = { runtime: "edge" };

// The query key the rewrite uses to carry the original path. Underscored to
// keep it clear of anything the API itself accepts, and stripped below so the
// backend never sees it.
const PATH_PARAM = "__path";

// Every failure this proxy produces itself is labelled and given a status the
// backend never returns for these routes, because the alternative cost a real
// debugging session: a misconfigured proxy answered 500, the backend answers
// 500 for its own faults, and the two were indistinguishable from the network
// tab -- so "the API is broken" and "the deployment is missing a variable"
// looked identical. 503 means this proxy is not configured, 502 means it could
// not reach the backend, and any other status came from the backend itself,
// body and all.
function proxyError(status: number, error: string, detail?: string): Response {
  return new Response(
    JSON.stringify({ source: "vercel-proxy", error, ...(detail ? { detail } : {}) }),
    { status, headers: { "content-type": "application/json" } },
  );
}

export default async function handler(request: Request): Promise<Response> {
  const backend = process.env.BACKEND_URL;
  if (!backend) {
    return proxyError(
      503,
      "BACKEND_URL is not configured on this Vercel project",
      "Set it in Vercel → Settings → Environment Variables to the backend host, e.g. example.up.railway.app (host only, no scheme), then redeploy.",
    );
  }

  const incoming = new URL(request.url);
  const path = incoming.searchParams.get(PATH_PARAM) ?? "";

  // The caller's own query string survives: Vercel merges it into the
  // destination, so everything except our own marker is forwarded as sent.
  const forwarded = new URLSearchParams(incoming.searchParams);
  forwarded.delete(PATH_PARAM);
  const query = forwarded.toString();

  const target = `https://${backend}/api/${path}${query ? `?${query}` : ""}`;

  // Drop the inbound Host: it names the Vercel domain, and forwarding it to a
  // different origin invites that edge to route on a name it does not serve.
  const headers = new Headers(request.headers);
  headers.delete("host");

  // Buffered rather than streamed: a fetch that streams a body requires
  // duplex: "half" in the runtimes implementing the newer spec, and throws
  // before sending otherwise — which would fail exactly the POSTs this proxy
  // exists to carry. These are small JSON payloads.
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  // An unreachable backend must not surface as an unhandled throw. Letting it
  // escape gives Vercel's own bodiless 500, which says nothing about which of
  // the two services failed -- and a sleeping or restarting backend is the
  // most ordinary thing that can go wrong here.
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
    });
  } catch (cause) {
    return proxyError(
      502,
      `Could not reach the backend at ${backend}`,
      cause instanceof Error ? cause.message : String(cause),
    );
  }

  // Everything from here is the backend's own answer, including its errors:
  // status and body pass through untouched so a real 500 still reads as one.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
