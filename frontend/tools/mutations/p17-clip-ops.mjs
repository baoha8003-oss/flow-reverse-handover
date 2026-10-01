/**
 * Mutation run for the frontend half of upscale and extend.
 *
 * The backend decides what Flow is asked for; this layer decides which ids get
 * handed to it and what lands on the node afterwards. Two of these mutations are
 * the reason the file exists:
 *
 * - the upscale landing must not write `mediaId`. The original clip is the file
 *   the user paid for, and there is no undo;
 * - `extensionOperationIds` must stay the same length as `extensionMediaIds`.
 *   Link n+1 reads the last entry to know what to reference, and a list that has
 *   drifted by one points at the wrong clip — which Flow accepts, bills, and
 *   then fails with NOT_FOUND.
 *
 * Run from `frontend/`: node tools/mutations/p17-clip-ops.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const OPS = "src/lib/clipOps.ts";
const HYDRATE = "src/store/board.ts";
const SUITE = "src/lib/clipOps.test.ts";
const HYDRATE_SUITE = "src/store/board.hydration.test.ts";

/** Preserve the file's own line endings — the repo carries both, and rewriting
 *  a whole file's endings turns a one-line mutation into a whole-file diff. */
function withFileNewlines(source, text) {
  return source.includes("\r\n") ? text.replace(/\n/g, "\r\n") : text;
}

const MUTATIONS = [
  // ── the original clip ────────────────────────────────────────────────
  {
    label: "the upscale lands on mediaId and destroys the original",
    file: OPS,
    suite: SUITE,
    from: '  return { patch: { upscaledMediaId: media }, error: null };',
    to: '  return { patch: { mediaId: media }, error: null };',
  },
  {
    label: "an empty upscale result writes a blank id instead of reporting it",
    file: OPS,
    suite: SUITE,
    from: '    return { patch: {}, error: "Upscale xong mà không trả về clip nào." };',
    to: '    return { patch: { upscaledMediaId: media }, error: null };',
  },
  // ── the extend chain ─────────────────────────────────────────────────
  {
    label: "the operation list stops being padded, so the two lists drift",
    file: OPS,
    suite: SUITE,
    from: "  op_ids.push(firstOperation(result));",
    to: "  const op = firstOperation(result);\n  if (op) op_ids.push(op);",
  },
  {
    label: "the chain reads the FIRST operation id instead of the last",
    file: OPS,
    suite: SUITE,
    from: "  const previous = ops.length > 0 ? ops[ops.length - 1] : undefined;",
    to: "  const previous = ops.length > 0 ? ops[0] : undefined;",
  },
  {
    label: "position stops counting the chain, so every link claims to be the first",
    file: OPS,
    suite: SUITE,
    from: "    position: done.length + 1,",
    to: "    position: 1,",
  },
  {
    label: "re-landing the same extension adds it twice",
    file: OPS,
    suite: SUITE,
    from: "  if (media_ids.includes(media)) {\n    return { patch: scene, error: null };\n  }",
    to: "  if (false) {\n    return { patch: scene, error: null };\n  }",
  },
  {
    label: "a failed extension forgets the scene Flow already made",
    file: OPS,
    suite: SUITE,
    from: '    return { patch: scene, error: "Nối xong mà không trả về clip nào." };',
    to: '    return { patch: {}, error: "Nối xong mà không trả về clip nào." };',
  },
  {
    label: "an extension overwrites the original clip too",
    file: OPS,
    suite: SUITE,
    from: "  return {\n    patch: {\n      ...scene,\n      extensionMediaIds: media_ids,",
    to: "  return {\n    patch: {\n      ...scene,\n      mediaId: media,\n      extensionMediaIds: media_ids,",
  },
  // ── what the buttons are allowed to do ───────────────────────────────
  {
    label: "an Omni clip is offered an extension Flow greys out",
    file: OPS,
    suite: SUITE,
    from: '  return typeof modelKey === "string" && modelKey.trim().toLowerCase().startsWith("abra_");',
    to: '  return typeof modelKey === "string" && modelKey.startsWith("abra_");',
  },
  {
    label: "the refusal stops naming the clip, so the card cannot explain",
    file: OPS,
    suite: SUITE,
    from: '        `Flow chỉ nối được clip Veo; clip này là “${data.sourceModelKey}” (Omni) `',
    to: '        `Flow chỉ nối được clip Veo; clip này là Omni `',
  },
  {
    label: "the upscale accepts a clip with no recorded operation id",
    file: OPS,
    suite: SUITE,
    from: '  if (typeof op !== "string" || !op) {',
    to: "  if (false) {",
  },
  {
    label: "the operation is read from slot 0 rather than the slot that rendered",
    file: OPS,
    suite: SUITE,
    from: "  const op = Array.isArray(ops) && slot < ops.length ? ops[slot] : null;",
    to: "  const op = Array.isArray(ops) ? ops[0] : null;",
  },
  {
    label: "the primary slot is slot 0 even when slot 0 failed",
    file: OPS,
    suite: SUITE,
    from: '    const at = ids.findIndex((m) => typeof m === "string" && m.length > 0);\n    return at;',
    to: "    return 0;",
  },
  {
    label: "an operation runs on a clip that is still rendering",
    file: OPS,
    suite: SUITE,
    from: '  if (data.status === "queued" || data.status === "running") {\n    return { ok: false, reason: "Clip đang chạy — đợi xong rồi mới nâng cấp được." };',
    to: '  if (false) {\n    return { ok: false, reason: "Clip đang chạy — đợi xong rồi mới nâng cấp được." };',
  },
  // ── surviving a reload ───────────────────────────────────────────────
  {
    label: "the extend chain is not hydrated, so a reload restarts it and pays again",
    file: HYDRATE,
    suite: HYDRATE_SUITE,
    from: '          extensionOperationIds: n.data["extensionOperationIds"] as\n            | string[]\n            | undefined,',
    to: "          extensionOperationIds: undefined,",
  },
  {
    label: "the operation ids are not hydrated, so upscale disappears on reload",
    file: HYDRATE,
    suite: HYDRATE_SUITE,
    from: '          operationNames: n.data["operationNames"] as (string | null)[] | undefined,',
    to: "          operationNames: undefined,",
  },
  {
    label: "the source model key is not hydrated, so Omni stops being refused",
    file: HYDRATE,
    suite: HYDRATE_SUITE,
    from: '          sourceModelKey: n.data["sourceModelKey"] as string | undefined,',
    to: "          sourceModelKey: undefined,",
  },
];

const survived = [];
for (const [index, m] of MUTATIONS.entries()) {
  const source = readFileSync(m.file, "utf8");
  const from = withFileNewlines(source, m.from);
  const count = source.split(from).length - 1;
  // The three hydration maps are three copies of the same list, so a mutation
  // there legitimately matches more than once — breaking all three is the point.
  const expected = m.file === HYDRATE ? 3 : 1;
  if (count !== expected) {
    console.log(`${index + 1}. ANCHOR MISSED (${count}x, want ${expected}): ${m.label}`);
    survived.push(`${index + 1}. ${m.label} (anchor)`);
    continue;
  }
  writeFileSync(
    m.file,
    source.split(from).join(withFileNewlines(source, m.to)),
  );
  let passed = false;
  try {
    execFileSync(
      process.platform === "win32" ? "npx.cmd" : "npx",
      ["vitest", "run", m.suite],
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
