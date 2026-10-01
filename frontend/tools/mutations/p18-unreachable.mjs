/**
 * Mutation run for P18's frontend half: a clip whose provenance was never recorded.
 *
 * The hole, concretely. `_settle_generation_node` only started keeping
 * `sourceModelKey` at P16, so every earlier clip had none — and a blank string
 * does not start with `abra_`. So `isOmniClip` answered false, the extend button
 * was offered on Omni clips, and the backend's refusal could not fire either
 * because it reads the same field. Flow accepts that submit and then fails it,
 * after billing.
 *
 * The start-up backfill makes the data true for clips that have a request row;
 * `hasUnknownSource` covers the rest by refusing. These mutations pin the
 * refusal, and pin that it did NOT spread to upscale — Omni clips upscale fine,
 * so demanding provenance there would take a capability away from a clip the
 * user already paid for.
 *
 * Run from `frontend/`: node tools/mutations/p18-unreachable.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const OPS = "src/lib/clipOps.ts";
const SUITE = "src/lib/clipOps.test.ts";

/** Preserve the file's own line endings — the repo carries both, and rewriting
 *  a whole file's endings turns a one-line mutation into a whole-file diff. */
function withFileNewlines(source, text) {
  return source.includes("\r\n") ? text.replace(/\n/g, "\r\n") : text;
}

const MUTATIONS = [
  {
    label: "a clip with no recorded source is offered an extension anyway",
    file: OPS,
    from: "  if (hasUnknownSource(data.sourceModelKey)) {",
    to: "  if (false) {",
  },
  {
    label: "a blank model key reads as recorded, so silence is trusted",
    file: OPS,
    from: '  return typeof modelKey !== "string" || modelKey.trim() === "";',
    to: '  return typeof modelKey !== "string";',
  },
  {
    label: "whitespace counts as a recorded provenance",
    file: OPS,
    from: '  return typeof modelKey !== "string" || modelKey.trim() === "";',
    to: '  return typeof modelKey !== "string" || modelKey === "";',
  },
  {
    label: "the unknown-source refusal reads the same as the Omni one",
    file: OPS,
    from:
      '        "Không có bản ghi clip này từ model nào (render trước khi app lưu thông "\n'
      + '        + "tin đó). Chạy lại clip để có bản ghi, hoặc nối trong Flow.",',
    to: '        `Flow chỉ nối được clip Veo; clip này là “${data.sourceModelKey}” (Omni) nên nút nối bị mờ.`,',
  },
  {
    label: "upscale starts demanding provenance it does not need",
    file: OPS,
    from: "  const slot = primarySlot(data);\n  if (slot < 0) {\n    return { ok: false, reason: \"Chưa có clip nào để nâng cấp.\" };\n  }",
    to: "  if (hasUnknownSource(data.sourceModelKey)) {\n    return { ok: false, reason: \"Chưa có clip nào để nâng cấp.\" };\n  }\n  const slot = primarySlot(data);\n  if (slot < 0) {\n    return { ok: false, reason: \"Chưa có clip nào để nâng cấp.\" };\n  }",
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
      ["vitest", "run", SUITE],
      { stdio: "pipe" },
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
