/**
 * Mutation run for the canvas agent's frontend surface.
 *
 * Each entry breaks one decision and names the suite that must go red. Two of
 * these are the reason the file exists: the Apply gate (`canApply`) and the
 * wording of a run offer. If either loses its teeth, the panel can show a board
 * the run will refuse, or make an offer read as a charge that already happened.
 *
 * Run from `frontend/`: node tools/mutations/p12-canvas-agent.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const VIEW = "src/lib/agentPlanView.ts";

/** Preserve the file's own line endings — the repo carries both, and rewriting
 *  a whole file's endings turns a one-line mutation into a whole-file diff. */
function withFileNewlines(source, text) {
  return source.includes("\r\n") ? text.replace(/\n/g, "\r\n") : text;
}

const MUTATIONS = [
  {
    label: "apply is allowed while a blocking finding stands",
    file: VIEW,
    from: "    canApply: errors.length === 0,",
    to: "    canApply: true,",
  },
  {
    label: "warnings are counted as blockers, so an ordinary plan cannot apply",
    file: VIEW,
    from: '  const errors = findings.filter((f) => f.severity === "error");',
    to: "  const errors = findings.slice();",
  },
  {
    label: "a run offer reads as a run that already happened",
    file: VIEW,
    from: "        ? `Đề xuất chạy ${ids.length} node — bấm Chạy để xem giá trước`",
    to: "        ? `Đang chạy ${ids.length} node`",
  },
  {
    label: "a mixed-provider batch names one of them anyway",
    file: VIEW,
    from: "  return named.size === 1 ? [...named][0] : null;",
    to: "  return [...named][0] ?? null;",
  },
  {
    label: "mermaid ids stop being sanitised, so the diagram fails to render",
    file: VIEW,
    from: '  return `n${String(value).replace(/[^A-Za-z0-9_]/g, "_")}`;',
    to: "  return `n${String(value)}`;",
  },
  {
    label: "a title's quotes and brackets reach the diagram",
    file: VIEW,
    from: '  return (title || type).replace(/["\\[\\]{}|]/g, " ").trim() || type;',
    to: "  return (title || type).trim() || type;",
  },
  {
    label: "a diagram is drawn for a plan short enough to read",
    file: VIEW,
    from: "  if (created.length + wires.length < DIAGRAM_THRESHOLD) return null;",
    to: "  if (false) return null;",
  },
  {
    label: "a wireless plan is drawn as a diagram of loose boxes",
    file: VIEW,
    from: "  return wires.length > 0 ? lines.join(\"\\n\") : null;",
    to: '  return lines.join("\\n");',
  },
  {
    label: "a wire with a missing end emits a broken diagram line",
    file: VIEW,
    from: "    if (from === undefined || to === undefined) continue;",
    to: "    if (false) continue;",
  },
];

const survived = [];
for (const [index, m] of MUTATIONS.entries()) {
  const source = readFileSync(m.file, "utf8");
  const from = withFileNewlines(source, m.from);
  const count = source.split(from).length - 1;
  if (count !== 1) {
    console.log(`${index + 1}. ANCHOR MISSED (${count}x): ${m.label}`);
    survived.push(`${index + 1}. ${m.label} (anchor)`);
    continue;
  }
  writeFileSync(m.file, source.replace(from, withFileNewlines(source, m.to)));
  let passed = false;
  try {
    execFileSync(
      process.platform === "win32" ? "npx.cmd" : "npx",
      ["vitest", "run", "src/lib/agentPlanView.test.ts"],
      { stdio: "pipe" }
    );
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
