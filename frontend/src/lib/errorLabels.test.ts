import { describe, expect, it } from "vitest";

import { errorLabel } from "./errorLabels";

/** The node card used to print the backend's code verbatim.
 *
 * Pressing "Dừng toàn bộ" then left a row of cards reading `stopped_by_user`,
 * and a refusal that had just saved the user money looked like a crash.
 */

describe("a code becomes a sentence", () => {
  it("translates the one Stop leaves behind", () => {
    expect(errorLabel("stopped_by_user")).toContain("dừng");
  });

  it("says which rule blocked a prompt, and why it cost nothing", () => {
    const label = errorLabel("prompt_rule:tag_in_dialogue");
    expect(label).toContain("đọc thành tiếng");
    expect(label).toContain("trước khi tốn tiền");
  });

  it("handles two rules at once", () => {
    const label = errorLabel("prompt_rule:banned_name,tag_in_dialogue");
    expect(label).toContain("bộ lọc");
    expect(label).toContain("lời thoại");
  });

  it("names the empty socket", () => {
    expect(errorLabel("missing_upload:image_1,image_2")).toContain("image_1");
  });

  it("keeps the step number a post-production chain reports", () => {
    const label = errorLabel("timeout_step_3");
    expect(label).toContain("bước 3");
    expect(label).toContain("Chờ quá lâu");
  });

  it("passes an unknown code through rather than hiding it", () => {
    // A code nobody has translated is still the one clue the user can act on.
    expect(errorLabel("some_new_backend_code")).toBe("some_new_backend_code");
  });

  it("is safe on an empty string", () => {
    expect(errorLabel("")).toBe("");
  });
});

describe("Flow's own error codes, measured live 19/09/2026", () => {
  it("turns the plan refusal into the sentence a user can act on", () => {
    // What the wire actually carried before this: `RpcError: eb1hJf failed:
    // [7, None, [['type.googleapis.com/google.rpc.ErrorInfo',
    // ['PUBLIC_ERROR_MODEL_ACCESS_DENIED']]]]`. It is the error a Pro user is
    // most likely to see, because the UI marks the lane they picked as free.
    expect(errorLabel("PUBLIC_ERROR_MODEL_ACCESS_DENIED (eb1hJf)")).toContain(
      "không có model này",
    );
  });

  it("says a replayed captcha recovers on its own", () => {
    // The worker re-mints. Telling the user to do something would send them
    // chasing a problem that fixes itself.
    expect(errorLabel("PUBLIC_ERROR_UNUSUAL_ACTIVITY (ogiZ0b)")).toContain(
      "tự xin token mới",
    );
  });

  it("keeps an untranslated Flow code readable instead of hiding it", () => {
    const label = errorLabel("PUBLIC_ERROR_SOMETHING_NEW (as29s)");
    expect(label).toContain("PUBLIC_ERROR_SOMETHING_NEW");
    // And drops the rpcid from the sentence: it is for a bug report, not for
    // the person deciding what to click next.
    expect(label).not.toContain("as29s");
  });

  it("says a refused capability cost nothing", () => {
    // A refusal that reads like a crash gets runs cancelled that did not need
    // cancelling — which is the expensive direction for a message to be wrong.
    const label = errorLabel(
      "unsupported_on_batch_veo_start_end: Veo chưa có payload khung cuối",
    );
    expect(label).toContain("không tốn credit");
    expect(label).toContain("khung cuối");
  });

  it("names the browser-side fixes separately", () => {
    // Three different fixes; one label for all of them is what made the bridge
    // look healthy for a week.
    const hints = new Set(
      ["NO_AT_TOKEN", "NO_FLOW_TAB", "FLOW_TAB_DISCARDED"].map(errorLabel),
    );
    expect(hints.size).toBe(3);
  });
});

describe("codes the backend actually emits", () => {
  /* The four extension codes had labels that could never be reached.
   *
   * `_payload` wraps a bridge code as `FlowBatchError: <rpcid>: <code>` and
   * `_error_text` prepends the exception type, so the code always arrives
   * embedded — never bare. The table only matched exactly or by prefix, and the
   * test that "proved" these labels typed the bare form by hand. 25 of 29 real
   * strings rendered as raw tokens on the card. */
  it.each([
    "FlowBatchError: ogiZ0b: NO_AT_TOKEN",
    "FlowBatchError: eb1hJf: NO_FLOW_TAB",
    "FlowBatchError: eb1hJf: FLOW_TAB_DISCARDED",
    "FlowBatchError: eb1hJf: NO_INJECTION_RESULT",
  ])("unwraps %s", (raw) => {
    const out = errorLabel(raw);
    expect(out).not.toBe(raw);
    expect(out).not.toContain("FlowBatchError");
  });

  it("translates a Flow code the image path now leads with", () => {
    // `flow_sdk` used to build image errors from `str(exc)`, so this arrived as
    // a repr of a nested protobuf and the 80-char cut sliced the code in half.
    const out = errorLabel("image_failed: PUBLIC_ERROR_MODEL_ACCESS_DENIED (ogiZ0b)");
    expect(out).not.toContain("PUBLIC_ERROR_MODEL_ACCESS_DENIED (ogiZ0b)");
  });

  it("does not claim to know which of two things a [5] means", () => {
    /* Measured 20/09: Flow checks the model name, then the plan, then the media.
     * `[5]` NOT_FOUND is the answer for an unknown model name AND for a media it
     * cannot find — a probe built on bogus media proved they are
     * indistinguishable. So the sentence must not assert one of them. */
    const out = errorLabel("RpcError: eb1hJf failed: [5]");
    expect(out).not.toBe("RpcError: eb1hJf failed: [5]");
    expect(out.toLowerCase()).toContain("model");
    expect(out.toLowerCase()).toContain("media");
  });

  it.each([
    "API_403",
    "timeout_waiting_video",
    "no_operations_in_response",
    "flow_transient_retry: ogiZ0b failed: [8]",
  ])("has something to say about %s", (raw) => {
    expect(errorLabel(raw)).not.toBe(raw);
  });
});
