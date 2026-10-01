/**
 * Mutation run for P13.7 — the frontend surfaces after the batch migration.
 *
 * Each entry breaks one decision and names the suite that must go red. A
 * survivor is a test with no teeth: the decision looks load-bearing and is not
 * actually checked, which for the money-facing ones means a wrong number could
 * reach the screen unchallenged.
 *
 * Run from `frontend/`: node tools/mutations/p13-frontend.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const SPEC = "src/lib/generationSpec.ts";
const MODELS = "src/store/models.ts";
const BAR = "src/shell/StatusBar.tsx";
const PANEL = "src/components/AccountPanel.tsx";

const MUTATIONS = [
  {
    label: "length and resolution ride along on a Veo lane too",
    file: SPEC,
    from: '  if (spec.quality !== "omni") return {};',
    to: "  if (false) return {};",
  },
  {
    label: "a resolution Flow rejects is passed through from config",
    file: MODELS,
    from: '  return raw === "360p" || raw === "720p" ? raw : undefined;',
    to: "  return raw || undefined;",
  },
  {
    label: "an unasked Flow tab is coloured as broken",
    file: BAR,
    from: '  if (stats?.flow_tab_present == null) return "text-ink-dim";',
    to: '  if (false) return "text-ink-dim";',
  },
  {
    label: "a tab that cannot sign reads as ready",
    file: BAR,
    from: '  if (stats?.at_token_present) return "text-ok";',
    to: '  if (stats?.flow_tab_present) return "text-ok";',
  },
  {
    label: "a missing Flow tab is reported as an extension problem",
    file: PANEL,
    from: `  if (!scan.flow_tab_present) {
    return {
      short: "Chưa có tab Flow",`,
    to: `  if (false) {
    return {
      short: "Chưa có tab Flow",`,
  },
  {
    label: "a tab that cannot sign is reported as ready",
    file: PANEL,
    from: "  if (!scan.flow_tab_signed) {",
    to: "  if (false) {",
  },
];

/** Match the file's own line endings.
 *
 * Without this a multi-line anchor silently stops matching any CRLF file, and
 * the run reports ANCHOR MISSED — a check that has quietly stopped checking.
 * That is what happened to the AccountPanel entries below. */
function withFileNewlines(text, source) {
  return source.includes("\r\n") ? text.replace(/\n/g, "\r\n") : text;
}

let survived = [];
for (const [index, m] of MUTATIONS.entries()) {
  const source = readFileSync(m.file, "utf8");
  const from = withFileNewlines(m.from, source);
  const count = source.split(from).length - 1;
  if (count !== 1) {
    console.log(`${index + 1}. ANCHOR MISSED (${count}x): ${m.label}`);
    survived.push(`${index + 1}. ${m.label} (anchor)`);
    continue;
  }
  writeFileSync(m.file, source.replace(from, withFileNewlines(m.to, source)));
  let passed = false;
  try {
    execFileSync("npx", ["vitest", "run", "--reporter=dot"], {
      stdio: "pipe",
      shell: true,
    });
    passed = true;
  } catch {
    passed = false;
  } finally {
    writeFileSync(m.file, source);
  }
  if (passed) {
    console.log(`${index + 1}. SURVIVED: ${m.label}`);
    survived.push(`${index + 1}. ${m.label}`);
  } else {
    console.log(`${index + 1}. caught:   ${m.label}`);
  }
}

console.log(`\n${MUTATIONS.length - survived.length}/${MUTATIONS.length} caught`);
for (const s of survived) console.log("  survived:", s);
process.exit(survived.length ? 1 : 0);
