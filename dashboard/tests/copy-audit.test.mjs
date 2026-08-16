/* §106 — the phrases the rewrite removed must stay removed.
 *
 * This is a string audit over the normal product surface, not over the whole
 * repository: the same vocabulary is correct and expected in `docs/`, in
 * `skills/`, in the MCP server and in the API itself, because those have a
 * different audience. What must not happen is the language drifting back into
 * the pages a person uses.
 *
 * Field names and identifiers are not copy, so the audit looks at rendered
 * strings: JSX text and string/template literals, with comments stripped.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";

const SRC = new URL("../src/", import.meta.url);

/* Files whose visible copy is normal product UI. `demo-tour-model.js` is the
 * guided walkthrough's script — also normal copy. */
const UI_FILES = readdirSync(SRC)
  .filter((name) => /\.(jsx|js)$/.test(name) && name !== "api.js");

/** Strip comments so a note *about* a removed phrase does not fail the audit. */
function visibleCopy(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, " ");
}

const SOURCES = Object.fromEntries(
  UI_FILES.map((name) => [name, visibleCopy(readFileSync(new URL(name, SRC), "utf8"))]),
);

/* Phrases from the audit's "potentially redundant/technical copy" lists that
 * were explicitly removed. Each is matched case-insensitively. */
const BANNED_PHRASES = [
  "logical provenance",
  "Health reflects event ingestion",
  "Agent analysis contract",
  "Worker control is cooperative",
  "Generated views",
  "ManySight never executes generated UI code",
  "geometry-first",
  "world-to-pixel",
  "decision ROIs",
  "Rows are raw worker submissions",
  "Ask a ManySight-connected coding agent",
  "Add widgets through MCP",
  "Observation updates live",
  "Signals support human review",
  "items in this view",
  "New review threshold",
  "Thresholds & notifications",
  "Fused view",
  "Source debug view",
  "How to read this table",
  "Raw observation explorer",
  "epoch-namespaced",
  "anonymous fused",
  "ManySight-derived fused state",
  "calibrated source-local tracks",
  "atomic DetectionSample",
];

for (const phrase of BANNED_PHRASES) {
  test(`removed copy stays removed: "${phrase}"`, () => {
    const offenders = Object.entries(SOURCES)
      .filter(([, text]) => text.toLowerCase().includes(phrase.toLowerCase()))
      .map(([name]) => name);
    assert.deepEqual(offenders, [], `still present in ${offenders.join(", ")}`);
  });
}

/* Terms that may legitimately appear as an API field name or inside a
 * Technical details block, but never as a sentence a person reads. The audit
 * therefore looks only at prose: a run of words with spaces around the term. */
const BANNED_IN_PROSE = ["homography", "materialization", "identity scope", "producer_kind"];

/** Every complete string literal in a file, quotes stripped. */
function stringLiterals(text) {
  return [...text.matchAll(/"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'/g)]
    .map((match) => match[1] ?? match[2]);
}

for (const term of BANNED_IN_PROSE) {
  test(`internal vocabulary stays out of prose: "${term}"`, () => {
    const offenders = Object.entries(SOURCES)
      .filter(([, text]) => stringLiterals(text).some((literal) =>
        // A bare identifier or API value is not prose; a sentence is.
        literal.toLowerCase().includes(term.toLowerCase())
        && literal.trim().split(/\s+/).length > 2))
      .map(([name]) => name);
    assert.deepEqual(offenders, [], `prose use in ${offenders.join(", ")}`);
  });
}

test("the prose matcher reads whole literals, not spans between them", () => {
  // The pattern that produced a false positive: two adjacent literals with an
  // identifier between them must not read as one string.
  const sample = 'const replay = source.metadata?.producer_kind === "replay";';
  assert.deepEqual(stringLiterals(sample), ["replay"]);
  assert.deepEqual(stringLiterals('a "one two three homography" b'), ["one two three homography"]);
});

/* §70 — a normal row must not print raw JSON. The rule list used to render
 * `{"query_id": 1}` verbatim. */
test("no page stringifies a params object straight into a row", () => {
  const offenders = Object.entries(SOURCES)
    .filter(([name]) => ["review.jsx", "sources.jsx", "setup.jsx", "dashboard-page.jsx"].includes(name))
    .filter(([, text]) => /JSON\.stringify\((?!.*technical)/i.test(text)
      && !/<TechnicalDetails|<pre/.test(text))
    .map(([name]) => name);
  assert.deepEqual(offenders, []);
});

/* ManySight is the product's name and belongs in copy. Its *identifiers* do not:
 * the demo-session header, localStorage keys, DOM event names, the API's
 * connection-management enum and the environment variables are machinery, and a
 * sentence that names one is leaking implementation into the interface. The
 * distinction is the same one used above — a bare identifier is not prose, a run
 * of words is. */
const INTERNAL_IDENTIFIER = /(MANYSIGHT_[A-Z_]+|X-ManySight-[A-Za-z-]+|manysight[_.][a-z0-9_.-]+|manysight-[a-z0-9-]+)/;

test("internal ManySight identifiers stay out of user-visible prose", () => {
  const offenders = [];
  for (const [name, text] of Object.entries(SOURCES)) {
    for (const literal of stringLiterals(text)) {
      if (!INTERNAL_IDENTIFIER.test(literal)) continue;
      // One token is a key or a header being used; a sentence is copy.
      if (literal.trim().split(/\s+/).length <= 2) continue;
      offenders.push(`${name}: "${literal}"`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("the identifier matcher separates a storage key from a sentence", () => {
  assert.ok(INTERNAL_IDENTIFIER.test("manysight_api_key"));
  assert.ok(INTERNAL_IDENTIFIER.test("X-ManySight-Demo-Session"));
  assert.ok(INTERNAL_IDENTIFIER.test("MANYSIGHT_CREDENTIAL_KEY"));
  // The product name on its own is copy, not an identifier.
  assert.ok(!INTERNAL_IDENTIFIER.test("Explore ManySight"));
  assert.ok(!INTERNAL_IDENTIFIER.test("Watch ManySight track the space."));
});

test("the audit actually looked at the product files", () => {
  assert.ok(UI_FILES.includes("main.jsx"));
  assert.ok(UI_FILES.includes("live.jsx"));
  assert.ok(UI_FILES.includes("demo.jsx"));
  assert.ok(UI_FILES.includes("demo-tour-model.js"));
  assert.ok(UI_FILES.length >= 15, `only ${UI_FILES.length} files scanned`);
});
