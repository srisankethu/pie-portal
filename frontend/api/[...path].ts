// Proxies /api/* to the Railway backend, read from an environment variable at
// request time instead of a hardcoded host in vercel.json. Vercel's static
// `rewrites` config cannot interpolate env vars — this file is the runtime
// equivalent: any file under api/ is deployed as a serverless function, and
// Vercel routes /api/* here before falling through to the SPA rewrite, so no
// entry in vercel.json is needed for this path.
//
// Set BACKEND_URL in the Vercel project's Environment Variables (just the
// host, e.g. pie-portal-production.up.railway.app — no scheme) and every
// redeploy of the backend that changes its URL is a dashboard edit, not a
// commit.
export const config = { runtime: "edge" };

export default async function handler(request: Request): Promise<Response> {
  const backend = process.env.BACKEND_URL;
  if (!backend) {
    return new Response(
      JSON.stringify({ error: "BACKEND_URL is not configured on this Vercel project" }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }

  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, `https://${backend}`);

  // Drop the inbound Host: it names the Vercel domain, and forwarding it to a
  // different origin invites the upstream edge to route on a name it does not
  // serve. fetch sets the correct one for the target.
  const headers = new Headers(request.headers);
  headers.delete("host");

  // Buffered rather than streamed. Passing request.body through is a
  // ReadableStream, and a fetch that streams a body requires duplex: "half"
  // in the runtimes that implement the newer spec -- omitting it throws
  // before the request is ever sent, which would fail exactly the POSTs this
  // proxy exists to carry. These bodies are small JSON payloads, so buffering
  // costs nothing and works on every runtime.
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    redirect: "manual",
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
