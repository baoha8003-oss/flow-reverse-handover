/**
 * Mutation run for the 20/09 parallel-review fixes, frontend half.
 *
 * Each entry breaks one decision and names nothing — the whole suite runs,
 * because these defects crossed module boundaries. A survivor is a test with no
 * teeth, which is exactly how the originals shipped: the hydration test asserted
 * ONE field on two of its three loaders while its docstring claimed it walked all
 * three, and the engine test matched a substring loose enough to pass under
 * either wording.
 *
 * Run from `frontend/`: node tools/mutations/p14-review-fixes.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const BOARD = "src/store/board.ts";
const SETTINGS = "src/store/settings.ts";
const LABELS = "src/lib/errorLabels.ts";
const CARD = "src/canvas/NodeCard.tsx";
const GEN = "src/store/generation.ts";

const MUTATIONS = [
  {
    label: "aspectRatio drops out of refreshBoardState again (portrait bills landscape)",
    file: BOARD,
    // Anchored on the comment, because the same field assignment appears in all
    // three loaders and only the third one is under test here.
    from:
      "          // upstream and bill a landscape clip from a portrait source.\n"
      + '          aspectRatio: n.data["aspectRatio"] as string | undefined,\n',
    to: "          // upstream and bill a landscape clip from a portrait source.\n",
  },
  {
    label: "the canvas defaults to the priciest lane again",
    file: SETTINGS,
    from: '      : "lite",',
    to: '      : "fast",',
  },
  {
    label: "a lane with no key behind it is offered again",
    file: SETTINGS,
    from: '  "lite_relaxed",\n  "omni",\n];',
    to: '  "lite_relaxed",\n  "omni",\n  "quality" as VideoQuality,\n];',
  },
  {
    label: "the image model the registry serves goes missing again",
    file: SETTINGS,
    from: '  "NANO_BANANA_2",\n  "NANO_BANANA_2_LITE",\n];',
    to: '  "NANO_BANANA_2",\n  "NANO_BANANA_PRO",\n];',
  },
  {
    label: "error codes stop being unwrapped, so 25 of 29 render raw",
    file: LABELS,
    from: "  const inner = unwrapOnce(trimmed);",
    to: "  const inner: string | null = null;",
  },
  {
    label: "a [5] claims it knows which of two things went wrong",
    file: LABELS,
    from: '    return "Flow không tìm thấy: tên model lạ, hoặc media nguồn không còn — kiểm tra cả hai";',
    to: '    return "Flow không tìm thấy tên model này";',
  },
  {
    label: "the failure reason goes back to video nodes only",
    file: "src/lib/nodeErrorLine.ts",
    from: '  if (data?.type === "note") return null;',
    to: '  if (data?.type !== "video") return null;',
  },
  {
    label: "the node path writes prose where the convention is a code",
    file: GEN,
    from: '              error: "openai_image_refs_unsupported",',
    to: '              error: "Engine OpenAI chỉ vẽ từ chữ, không nhận ảnh tham chiếu.",',
  },
];

const survived = [];

/** Match the file's own line endings.
 *
 * These sources are CRLF on this machine, so a multi-line anchor written with
 * `\n` finds nothing — and "nothing" reports as ANCHOR MISSED, which reads like a
 * survivor and hides whether the decision is pinned at all. Detected per file
 * rather than assumed, so the runner works either way.
 */
function withFileNewlines(text, source) {
  return source.includes("\r\n") ? text.replace(/\n/g, "\r\n") : text;
}

for (const [index, raw] of MUTATIONS.entries()) {
  const source = readFileSync(raw.file, "utf8");
  const m = {
    ...raw,
    from: withFileNewlines(raw.from, source),
    to: withFileNewlines(raw.to, source),
  };
  const count = source.split(m.from).length - 1;
  if (count !== 1) {
    console.log(`${index + 1}. ANCHOR MISSED (${count}x): ${m.label}`);
    survived.push(`${index + 1}. ${m.label} (anchor)`);
    continue;
  }
  writeFileSync(m.file, source.replace(m.from, m.to));
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
