/**
 * Every form with an onSubmit must contain a control that can submit it.
 *
 * This exists because of a regression that reached a user. Migrating the
 * hand-rolled `.btn` elements to MUI's `Button` silently broke three forms: a
 * bare `<button>` inside a `<form>` defaults to `type="submit"`, and MUI's
 * `Button` defaults to `type="button"`. The markup still rendered, the button
 * still enabled and depressed, and nothing happened — "Add company" added no
 * company. Types cannot catch it, the build cannot catch it, and it looks
 * correct in review.
 *
 * Deliberately crude: a regex over the source, not a parse. It only has to
 * answer one question, and a false positive is a missing `type="submit"` that
 * was worth stating anyway.
 */
import fs from "fs";
import path from "path";

const SRC = path.join(process.cwd(), "src");

function* tsx(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) yield* tsx(full);
    else if (e.name.endsWith(".tsx")) yield full;
  }
}

const problems = [];
for (const file of tsx(SRC)) {
  const s = fs.readFileSync(file, "utf8");
  for (const m of s.matchAll(/<form\b/g)) {
    const end = s.indexOf("</form>", m.index);
    const seg = s.slice(m.index, end === -1 ? s.length : end + 7);
    if (!seg.includes("onSubmit")) continue;

    const hasMuiSubmit = /<Button\b[^>]*type="submit"/s.test(seg);
    // A native <button> with no explicit type submits by default.
    const hasNativeImplicit = /<button\b(?![^>]*\btype=)[^>]*>/s.test(seg);
    const hasNativeSubmit = /<button\b[^>]*type="submit"/s.test(seg);
    if (hasMuiSubmit || hasNativeImplicit || hasNativeSubmit) continue;

    const line = s.slice(0, m.index).split("\n").length;
    problems.push(`${path.relative(process.cwd(), file)}:${line}`);
  }
}

if (problems.length) {
  console.error(
    "\nA form has an onSubmit but nothing that can submit it:\n" +
    problems.map((p) => `  ${p}`).join("\n") +
    "\n\nMUI's Button defaults to type=\"button\". Add type=\"submit\" to the\n" +
    "control that submits, or type=\"button\" to one that deliberately does not.\n");
  process.exit(1);
}
console.log(`forms: ${problems.length === 0 ? "every onSubmit has a submit control" : ""}`);
