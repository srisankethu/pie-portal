// Which bands belong to the path you are pointing at.
//
// `BookFlow`'s header says the interaction the shape exists for is "where does
// Kennametal actually end up" — follow one ribbon rather than read twenty. It
// did not do that. The test was `r.source === lit || r.target === lit`, one hop
// on a three-stage picture, so hovering a principal lit the principal→line
// ribbons and dimmed every line→customer ribbon behind them. The half of the
// diagram that answers the question went dark at the moment you asked it.
//
// Extracted rather than left inline for the reason `bonds-layout.ts` and
// `cycle-layout.ts` were: a fixture can show a walk that stops one stage short,
// or one that leaks sideways into a principal that shares a line, and an eye
// looking at ribbons cannot.
//
// **Direction is never mixed inside one walk.** Descendants are found going
// forward only, ancestors going backward only, and the two sets are unioned at
// the end. A single both-directions flood from the same seed would step
// forward from a principal to a line and then backward from that line into
// *other* principals, lighting competitors as if they were on your path.
// That is the bug this module is most likely to grow, so it is the one the
// tests pin hardest.

/** A link between two node ids. `money` and the rest are the caller's. */
export interface Edge {
  source: string;
  target: string;
}

/** Follow `links` in one direction from `from`, collecting everything reached.
 *
 *  Iterative rather than recursive, and guarded by `seen`, so a cycle in the
 *  data cannot hang the render. The server builds a layered DAG today; a chart
 *  that stops responding is a worse way to find out that changed than a
 *  slightly odd-looking highlight.
 */
function reach(
  from: string, links: readonly Edge[], forward: boolean,
): Set<string> {
  const seen = new Set<string>();
  const queue = [from];
  while (queue.length) {
    const at = queue.pop() as string;
    for (const l of links) {
      const [near, far] = forward ? [l.source, l.target] : [l.target, l.source];
      if (near !== at || seen.has(far)) continue;
      seen.add(far);
      queue.push(far);
    }
  }
  return seen;
}

/**
 * Every node on the path running through `id` — its ancestors and its
 * descendants, and `id` itself.
 *
 * A caller lights a link when **both** of its endpoints are in this set. That
 * is exact rather than approximate because the graph is layered: with a
 * stage-0 seed the set holds only `id` and what lies downstream of it, so no
 * link between two lit nodes can be off the path. The same holds seeding at
 * stage 1 or 2.
 *
 * What this can and cannot answer is set by the data, not by the walk. The
 * server emits principal→line and line→customer links and nothing that joins a
 * principal to a customer directly, so seeding a principal lights the customers
 * of every line it feeds — including customers who bought that line from
 * somebody else. At this granularity that *is* the honest answer; a narrower
 * one would need per-flow links the endpoint does not send.
 */
export function pathThroughNode(
  id: string, links: readonly Edge[],
): Set<string> {
  const on = reach(id, links, true);
  for (const up of reach(id, links, false)) on.add(up);
  on.add(id);
  return on;
}

/**
 * Every node on the path running through one link.
 *
 * Hovering a single band should light that band's own path, not its source
 * node's entire fan — pointing at Kennametal→Milling and getting Kennametal→
 * Turning lit as well answers a question you did not ask. So the walk goes
 * backward from the source and forward from the target, and the two endpoints
 * are always included.
 */
export function pathThroughLink(
  link: Edge, links: readonly Edge[],
): Set<string> {
  const on = reach(link.target, links, true);
  for (const up of reach(link.source, links, false)) on.add(up);
  on.add(link.source);
  on.add(link.target);
  return on;
}
