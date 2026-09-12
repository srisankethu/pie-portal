/**
 * No hook may be called after its component has already returned.
 *
 * This exists because of a regression that reached a user. The Quote Builder
 * reads its draft asynchronously and returns a skeleton until it lands, and a
 * `useCallback` was added *below* that return. So the first render of every
 * visit ran one hook fewer than the second, React threw "Rendered more hooks
 * than during the previous render" the instant the quote arrived, and — with no
 * error boundary in this app — the whole tree unmounted. Opening a quote was a
 * white page on every device. `QuoteDetails` had the same fault a commit
 * earlier, keyed on which of two requests answered first.
 *
 * Types cannot catch it: the code is perfectly well typed. The build cannot
 * catch it, the tests did not catch it, and it looks correct in review — the
 * hook sits with the other callbacks, above the JSX that uses it. It only
 * appears at runtime, and only once state has moved.
 *
 * **Only a hook that begins after the return statement ends.** `return
 * useMemo(...)` is the ordinary way to write a custom hook, and the call runs
 * unconditionally as part of producing the value. Three of those are in this
 * codebase and all three are correct, so a check that could not tell them apart
 * would be a check people learn to scroll past.
 *
 * A parse rather than a regex, using the TypeScript compiler already installed:
 * "is this call inside the same function body as that return" is a question
 * about the tree, and nesting — a callback defined inside a component, a
 * component defined inside a file of them — is exactly where a text scan gets
 * it wrong.
 */
import fs from "fs";
import path from "path";
import ts from "typescript";

const SRC = path.join(process.cwd(), "src");

function* sources(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) yield* sources(full);
    else if (/\.tsx?$/.test(e.name) && !e.name.includes(".test.")) yield full;
  }
}

/** React's own rule for what a hook is: a call to something named `useFoo`.
 *  Deliberately name-based — the same convention the runtime relies on. */
const isHook = (name) => /^use[A-Z]/.test(name);

function calledName(node) {
  const e = node.expression;
  if (ts.isIdentifier(e)) return e.text;
  // `React.useMemo(...)`, and the `Proxy.useCallback` a bundler can produce.
  if (ts.isPropertyAccessExpression(e) && ts.isIdentifier(e.name)) return e.name.text;
  return null;
}

const problems = [];
for (const file of sources(SRC)) {
  const src = ts.createSourceFile(
    file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const rel = path.relative(process.cwd(), file);
  const lineOf = (n) => src.getLineAndCharacterOfPosition(n.getStart()).line + 1;

  /** Walk one function body, stopping at any nested function — a hook inside a
   *  callback belongs to that callback's scope, not to this one. */
  function scan(fn) {
    // Where this body first returns, and where that statement ends. A hook
    // starting before `end` is part of the returned expression.
    let firstReturn = null;
    (function walk(node) {
      if (node !== fn && ts.isFunctionLike(node)) return;
      if (ts.isReturnStatement(node) && firstReturn === null) {
        firstReturn = { line: lineOf(node), end: node.getEnd() };
      }
      if (ts.isCallExpression(node)) {
        const name = calledName(node);
        if (name && isHook(name) && firstReturn !== null
            && node.getStart() > firstReturn.end) {
          problems.push(
            `${rel}:${lineOf(node)}  ${name}() — the body already returns on line `
            + `${firstReturn.line}`);
        }
      }
      ts.forEachChild(node, walk);
    })(fn);
  }

  (function walkAll(node) {
    if (ts.isFunctionLike(node) && node.body) scan(node);
    ts.forEachChild(node, walkAll);
  })(src);
}

if (problems.length) {
  console.error(
    "\nA hook is called after its function has already returned:\n"
    + problems.map((p) => `  ${p}`).join("\n")
    + "\n\nThe render that takes the early return runs fewer hooks than the one\n"
    + "that does not, and React throws on the difference. Nothing in this app\n"
    + "catches that throw, so the screen goes blank. Move the hook above every\n"
    + "return in that body — a hook whose result an early return never uses is\n"
    + "still cheaper than the screen not rendering.\n");
  process.exit(1);
}
console.log("hooks: every hook runs before its component returns");
