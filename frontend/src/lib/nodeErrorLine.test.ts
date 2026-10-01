import { describe, expect, it } from "vitest";
import { nodeErrorView } from "./nodeErrorLine";

/** Every node type that can fail must be able to say why.
 *
 * The reason was rendered in exactly one place — inside `VideoBody` — so a failed
 * image, character, storyboard, upload or post-production node showed the red
 * status strip and nothing else. Nothing caught it because the card has no test:
 * the suite runs in node with no DOM, so the decision now lives in a function
 * that does not need one.
 */

const FAILABLE = [
  "image",
  "video",
  "character",
  "visual_asset",
  "Storyboard",
  "edit_video",
  "merge_video",
  "add_bgm",
  "remove_watermark",
  "create_voice",
  "prompt",
];

describe("which nodes report a failure", () => {
  it.each(FAILABLE)("%s shows its reason", (type) => {
    const view = nodeErrorView({ type, status: "error", error: "stopped_by_user" });
    expect(view, `${type} swallowed its error`).not.toBeNull();
    expect(view?.code).toBe("stopped_by_user");
    expect(view?.isError).toBe(true);
  });

  it("a note has no dispatch of its own to fail", () => {
    expect(nodeErrorView({ type: "note", status: "error", error: "x" })).toBeNull();
  });

  it("says nothing when there is nothing to say", () => {
    expect(nodeErrorView({ type: "image", status: "done" })).toBeNull();
    expect(nodeErrorView({ type: "image", status: "error", error: "" })).toBeNull();
    expect(nodeErrorView(null)).toBeNull();
    expect(nodeErrorView(undefined)).toBeNull();
  });

  it("distinguishes a hard failure from a partial result worth keeping", () => {
    // A wave where some variants landed: the node stays `done` and the note is
    // advisory, not an alert. Reading it as a failure would hide paid-for images
    // behind an error state.
    const partial = nodeErrorView({
      type: "image",
      status: "done",
      error: "1/4 variants failed: PUBLIC_ERROR_MODEL_ACCESS_DENIED (ogiZ0b)",
    });
    expect(partial?.isError).toBe(false);
    expect(partial?.code).toContain("PUBLIC_ERROR_MODEL_ACCESS_DENIED");
  });
});
