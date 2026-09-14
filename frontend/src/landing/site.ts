/** Where this site lives, said once.
 *
 * Every absolute URL the build emits — the canonical tag, `og:url`, the
 * `Sitemap:` line in robots.txt, every `<loc>` in the sitemap, every `@id` in
 * the JSON-LD graph and every link in llms.txt — is this string plus a path.
 * There is no second place to change it and no page that can disagree with
 * another about what its own address is.
 *
 * It was `https://pie-portal-seven.vercel.app` until now, as the *default* of
 * an env var nothing set, which is the worst of both shapes: it looked
 * configurable and behaved hardcoded. Vercel does not set `SITE_ORIGIN`, so
 * every deployed page named the platform host as its canonical — telling
 * search engines that the address to rank, and to attribute every inbound
 * link to, was the preview domain rather than the site. A canonical tag is
 * not a hint; a wrong one hands the whole signal away.
 *
 * `www`, not the apex, and that choice is enforced rather than assumed: the
 * apex redirects here (see `vercel.json`), so the site has exactly one
 * address and a crawler is never offered two documents with identical bytes.
 * Either host would have done as a choice; having both is what costs.
 *
 * No trailing slash. Callers append the path, and `${SITE_URL}/` is the
 * landing page — so a slash here would produce `//` everywhere else.
 */
export const SITE_URL = "https://www.syncpie.com";

/** The absolute URL of one page, from the slug its registry entry carries.
 *
 *  `""` is the landing at `/`; `"erp/netsuite"` is `/erp/netsuite`. The
 *  landing therefore gets the trailing slash and nothing else does, which is
 *  the same shape the sitemap, the canonical tag and the redirect rules all
 *  use — one address per page, in one form, in every file that names it. */
export function pageUrl(slug: string): string {
  return `${SITE_URL}/${slug}`;
}
