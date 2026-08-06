/** Runtime shapes for the data this app refuses to guess about.
 *
 * TypeScript checks what the code believes; it cannot check what actually
 * arrives. `JSON.parse(raw) as PlatformSession` is a *claim*, and the two
 * places it is least defensible are the two validated here.
 *
 * **Not every endpoint.** Schemas for forty payloads would be a second copy of
 * `types.ts` to keep in step, and the failure they would catch — a field the
 * server renamed — is one the type-checker already catches at the seam where
 * both sides are ours. What is validated is where the type-checker genuinely
 * cannot help: data from `localStorage`, which any browser extension or a
 * previous version of this app may have written, and the session the whole
 * shell's authorization hangs off.
 */
import { z } from "zod";

/** The signed-in session, as stored and as returned by login.
 *
 * `role` is an enum rather than a string on purpose. It decides what the nav
 * shows and which screens are reachable, so a session carrying a role this
 * build does not know about must fail here — loudly, at the door — rather than
 * fall through every `role === "OWNER"` check and quietly render the
 * salesperson's view to an owner.
 */
export const platformSessionSchema = z.object({
  token: z.string().min(1),
  role: z.enum(["SALESPERSON", "SALES_MANAGER", "OWNER"]),
  name: z.string(),
  user_id: z.string().min(1),
  organization_id: z.string().min(1),
  // Optional because a session stored before these were added is still a valid
  // session — the app falls back to its defaults for both.
  currency: z.string().optional(),
  timezone: z.string().optional(),
});

export type CheckedSession = z.infer<typeof platformSessionSchema>;

/** Parse a stored session, or `null` if it is not one.
 *
 * Never throws. A corrupt or outdated entry means "not signed in", which the
 * app already handles well — it shows the door. Throwing here would happen
 * during module load and take the whole page down with a blank screen, which
 * is the one outcome worse than asking somebody to sign in again.
 */
export function parseStoredSession(raw: string): CheckedSession | null {
  try {
    const result = platformSessionSchema.safeParse(JSON.parse(raw));
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}
