/** Machine error codes, in the language the user reads.
 *
 * The backend stamps a node with a code — `stopped_by_user`, `upstream_failed`,
 * `prompt_rule:tag_in_dialogue` — because a code is what a log, a test and a
 * re-run can all match on. The card used to print it verbatim, so pressing
 * "Dừng toàn bộ" left a row of nodes reading `stopped_by_user` and a refusal
 * that saved money looked like a crash.
 *
 * Unknown codes fall through unchanged: a code nobody has translated yet is
 * still information, and hiding it behind "Đã xảy ra lỗi" would cost the one
 * clue the user can act on.
 */

const LABELS: Record<string, string> = {
  stopped_by_user: "Đã dừng theo yêu cầu của bạn",
  upstream_failed: "Node phía trước lỗi nên bước này không chạy",
  timeout: "Chờ quá lâu, đã bỏ chờ (request được huỷ, không tính tiền)",
  // What the worker actually writes when a poll runs out, as opposed to the bare
  // `timeout` above, which is the NODE's status. Only the bare one had a label, so
  // the string users saw most often was the untranslated one.
  timeout_waiting_video:
    "Hết giờ chờ clip. Clip có thể vẫn đang render — bấm RUN Lỗi để chờ lại, "
    + "KHÔNG tính tiền lần nữa",
  no_operations_in_response:
    "Flow nhận request nhưng không trả mã theo dõi nào — chưa có gì được tạo",
  flow_transient_retry: "Flow đang quá tải (lỗi tạm) — sẽ tự thử lại",
  auth_retry_exhausted:
    "Đã thử lại nhiều lần mà tab Flow vẫn không ký được request",
  create_project_failed:
    "Không tạo được project trên Flow — dán project id vào Cài đặt để dùng tạm",
  canceled: "Request đã bị huỷ",
  request_failed: "Flow trả về lỗi cho request này",
  no_project: "Board chưa gắn project Flow",
  missing_upstream_image: "Thiếu ảnh khung hình đầu từ node phía trước",
  character_upstream_empty:
    "Có dây nhân vật nhưng node đó chưa có ảnh — chạy nó trước",
  character_wire_without_port:
    "Dây nhân vật chưa có tên cổng — nối lại từ canvas để đánh số character_N",
  openai_image_disabled:
    "Node đặt engine OpenAI nhưng đường đó đang tắt trong Settings",
  openai_image_refs_unsupported:
    "Engine OpenAI vẽ từ chữ, không nhận ảnh tham chiếu — bỏ ảnh hoặc đổi engine",
  motion_no_images: "Node Motion Control thiếu ảnh — bản này chưa hỗ trợ node đó",

  // Flow's own names, measured live on a Pro account 19/09/2026. These arrive
  // with the rpcid in brackets after them, so they are matched as prefixes
  // below — the exact table is kept for the ones that arrive bare.
  PUBLIC_ERROR_MODEL_ACCESS_DENIED:
    "Gói Flow của bạn không có model này — đổi làn trong node hoặc nâng gói",
  PUBLIC_ERROR_UNUSUAL_ACTIVITY:
    "Google chặn tạm vì token captcha bị dùng lại — hệ thống sẽ tự xin token mới",
  PUBLIC_ERROR_UNSAFE_GENERATION:
    "Bộ lọc nội dung của Flow từ chối prompt này",
  PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED:
    "Bộ lọc người nổi tiếng của Flow từ chối — bỏ tên người thật khỏi mô tả hình",
  NO_AT_TOKEN: "Tab Flow chưa ký được — đăng nhập lại rồi đợi app load xong",
  NO_FLOW_TAB: "Chưa có tab Flow nào đang mở — mở flow.google.com và để đó",
  FLOW_TAB_DISCARDED: "Chrome đã ngủ tab Flow — bấm vào tab đó một lần",
  NO_INJECTION_RESULT: "Không chạy được lệnh trong tab Flow — thử lại, hoặc reload tab",
};

/** Codes that carry a payload after a colon or an underscore. */
const PREFIXES: [string, (rest: string) => string][] = [
  [
    // The marker IS the meaning here, so it is read before unwrapping: the
    // payload after it is a bare `[8]` that no label could improve on, and
    // peeling down to it loses the one word that matters — transient.
    "flow_transient_retry",
    () => LABELS.flow_transient_retry,
  ],
  [
    "prompt_rule:",
    (rest) => {
      const rules = rest.split(",").map((r) => RULE_LABELS[r] ?? r);
      return `Prompt bị chặn trước khi tốn tiền: ${rules.join("; ")}`;
    },
  ],
  [
    "missing_upload:",
    (rest) => `Ổ upload còn trống: ${rest.split(",").join(", ")}`,
  ],
  [
    // `unsupported_on_batch_veo_start_end: <câu giải thích>`. The backend
    // already writes a full sentence after the colon, so the useful thing to
    // add is the fact that it cost nothing — a refusal that reads like a crash
    // gets cancelled runs it did not need to.
    "unsupported_on_batch_",
    (rest) => {
      const detail = rest.includes(":") ? rest.slice(rest.indexOf(":") + 1).trim() : rest;
      return `Đường mới của Flow chưa có năng lực này (không tốn credit): ${detail}`;
    },
  ],
  [
    // Flow's own codes arrive as `PUBLIC_ERROR_X (rpcid)`. Matching the bare
    // code first keeps the rpcid out of the user's sentence while leaving it in
    // the raw string a bug report can still carry.
    "PUBLIC_ERROR_",
    (rest) => {
      const code = `PUBLIC_ERROR_${rest.split(" ")[0].trim()}`;
      return LABELS[code] ?? `Flow từ chối: ${code}`;
    },
  ],
];

const RULE_LABELS: Record<string, string> = {
  tag_in_dialogue: "tag @@ nằm trong lời thoại (sẽ bị đọc thành tiếng)",
  banned_name: "tên người nổi tiếng trong phần mô tả hình (bộ lọc sẽ từ chối)",
  unknown_tag: "tag không khớp nhân vật nào đã nối",
  too_many_characters: "quá số nhân vật lane cho phép trong một cảnh",
  bad_duration: "thời lượng không hợp lệ cho lane này",
};

/** Layers the backend wraps a code in, peeled one at a time.
 *
 * `_payload` raises `FlowBatchError: <rpcid>: <code>` and `_error_text` prepends
 * the exception type, so a bridge code NEVER arrives bare — while this table only
 * matched exactly or by prefix. The four extension-code labels were therefore
 * unreachable, and the test that "proved" them typed the bare form by hand.
 * Measured before this: 4 of 29 real backend strings translated.
 */
const WRAPPERS = [
  /^(?:FlowBatchError|RpcError|ValueError|RuntimeError):\s*/,
  /^(?:image_failed|edit_failed|upload_failed|sync_failed):\s*/,
  /^\d+\/\d+ variants failed:\s*/,
  /^flow_transient_retry:\s*/,
  // `ogiZ0b: ` / `eb1hJf failed: ` — an rpcid is not a sentence for the user. It
  // stays in the raw string a bug report carries.
  /^[A-Za-z0-9]{5,8}(?: failed)?:\s*/,
];

function unwrapOnce(code: string): string | null {
  for (const re of WRAPPERS) {
    if (re.test(code)) {
      const rest = code.replace(re, "").trim();
      if (rest && rest !== code) return rest;
    }
  }
  return null;
}

export function errorLabel(code: string): string {
  const trimmed = code.trim();
  if (!trimmed) return trimmed;
  const exact = LABELS[trimmed];
  if (exact) return exact;
  for (const [prefix, render] of PREFIXES) {
    if (trimmed.startsWith(prefix)) return render(trimmed.slice(prefix.length));
  }
  // An HTTP status from the page fetch. It only reaches here at all because the
  // transport now folds it into the error; it used to be kept for the health
  // panel and dropped before any classifier or label could see it.
  const api = /^API_(\d{3})$/.exec(trimmed);
  if (api) {
    const status = api[1];
    if (status === "401" || status === "403") {
      return "Google từ chối phiên đăng nhập — mở lại tab Flow và đăng nhập, rồi thử lại";
    }
    if (status === "429") return "Google đang giới hạn tốc độ — sẽ tự thử lại";
    return `Google trả lỗi HTTP ${status} — sẽ tự thử lại`;
  }
  // `[5]` deliberately says BOTH things it can mean. Measured 20/09: Flow checks
  // the model name, then the plan, then the media — and answers `[5]` NOT_FOUND
  // for an unknown name AND for a media it cannot find. A probe built on bogus
  // media proved the two are indistinguishable, so naming one would be a guess.
  if (/failed: \[5\]$/.test(trimmed)) {
    return "Flow không tìm thấy: tên model lạ, hoặc media nguồn không còn — kiểm tra cả hai";
  }
  // `<something>_step_3` — a post-production chain names which pass failed.
  const step = /^(.*)_step_(\d+)$/.exec(trimmed);
  if (step) {
    const inner = LABELS[step[1]] ?? step[1];
    return `${inner} (bước ${step[2]})`;
  }
  const inner = unwrapOnce(trimmed);
  if (inner) {
    const label = errorLabel(inner);
    // Only when unwrapping reached something translatable. Otherwise keep the
    // whole original: the wrapper names which stage failed, which beats a bare
    // payload nobody has a label for.
    if (label !== inner) return label;
  }
  return trimmed;
}
