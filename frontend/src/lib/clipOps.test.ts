import { describe, expect, it } from "vitest";
import {
  extendLanding,
  extendReadiness,
  hasUnknownSource,
  isOmniClip,
  primarySlot,
  upscaleLanding,
  upscaleReadiness,
  type ClipOpNodeState,
} from "./clipOps";

/** Upscale and extend act on a clip that was already paid for.
 *
 * That changes what a bug costs. A broken generation costs the generation; these
 * can cost the original — by overwriting it, or by chaining from the wrong id and
 * paying for a render Flow then fails. Each test below is one of those.
 */

const VEO: ClipOpNodeState = {
  mediaId: "m-1",
  mediaIds: ["m-1"],
  operationNames: ["op-1"],
  sourceModelKey: "veo_3_1_i2v_lite",
  status: "done",
};

describe("which variant these operations act on", () => {
  it("picks the first slot that actually rendered", () => {
    expect(primarySlot({ mediaIds: [null, "m-b", "m-c"] })).toBe(1);
  });

  it("answers -1 when nothing rendered", () => {
    expect(primarySlot({ mediaIds: [null, null] })).toBe(-1);
    expect(primarySlot({})).toBe(-1);
  });

  it("treats a single mediaId as slot 0", () => {
    expect(primarySlot({ mediaId: "m" })).toBe(0);
  });
});

describe("upscale readiness", () => {
  it("hands back both ids, from the same slot", () => {
    const r = upscaleReadiness({
      mediaIds: [null, "m-b"],
      operationNames: [null, "op-b"],
      status: "done",
    });
    expect(r).toEqual({ ok: true, operationId: "op-b", mediaId: "m-b" });
  });

  it("refuses a clip with no recorded operation, and says what to do", () => {
    // Clips rendered before the app started keeping operation ids. Guessing the
    // operation from the media id is the exact swap the capture warns about:
    // accepted, then NOT_FOUND, and the submit is spent.
    const r = upscaleReadiness({ ...VEO, operationNames: undefined });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toMatch(/Chạy lại clip/);
  });

  it("refuses when the slot that rendered has no operation beside it", () => {
    const r = upscaleReadiness({
      mediaIds: [null, "m-b"],
      operationNames: ["op-a", null],
      status: "done",
    });
    // `op-a` belongs to the slot that FAILED. Using it would upscale nothing.
    expect(r.ok).toBe(false);
  });

  it("refuses a clip that has not rendered", () => {
    expect(upscaleReadiness({ status: "done" }).ok).toBe(false);
  });

  it("refuses while the node is still running", () => {
    expect(upscaleReadiness({ ...VEO, status: "running" }).ok).toBe(false);
    expect(upscaleReadiness({ ...VEO, status: "queued" }).ok).toBe(false);
  });

  it("allows an Omni clip — only EXTEND is Veo-only", () => {
    const r = upscaleReadiness({ ...VEO, sourceModelKey: "abra_i2v_8s" });
    expect(r.ok).toBe(true);
  });
});

describe("extend readiness", () => {
  it("starts at position 1 with no previous operation", () => {
    const r = extendReadiness(VEO);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.position).toBe(1);
      expect(r.previousOperationId).toBeUndefined();
    }
  });

  it("references the previous extension's operation id from link 2 on", () => {
    // Not a media id. Converting the operation id to one produces a request Flow
    // accepts and then fails NOT_FOUND — billed, and no clip.
    const r = extendReadiness({
      ...VEO,
      sceneId: "s-1",
      sceneCloneMediaId: "clone-1",
      extensionMediaIds: ["e-1"],
      extensionOperationIds: ["op-ext-1"],
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.position).toBe(2);
      expect(r.previousOperationId).toBe("op-ext-1");
    }
  });

  it("counts position from the chain, not from a guess", () => {
    const r = extendReadiness({
      ...VEO,
      extensionMediaIds: ["e-1", "e-2", "e-3"],
      extensionOperationIds: ["o-1", "o-2", "o-3"],
    });
    if (r.ok) expect(r.position).toBe(4);
  });

  it("refuses an Omni clip and names it", () => {
    const r = extendReadiness({ ...VEO, sourceModelKey: "abra_i2v_8s" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toContain("abra_i2v_8s");
  });

  it("does not need an operation id, because link 1 goes through the scene", () => {
    const r = extendReadiness({ ...VEO, operationNames: undefined });
    expect(r.ok).toBe(true);
  });

  it("refuses a clip that has not rendered", () => {
    expect(extendReadiness({ sourceModelKey: "veo_3_1_i2v_lite" }).ok).toBe(false);
  });

  it("carries the scene through so the handler does not make a second one", () => {
    const r = extendReadiness({ ...VEO, sceneId: "s-1", sceneCloneMediaId: "c-1" });
    if (r.ok) {
      expect(r.sceneId).toBe("s-1");
      expect(r.sceneCloneMediaId).toBe("c-1");
    }
  });
});

describe("recognising an Omni clip", () => {
  it.each(["abra_i2v_8s", "ABRA_R2V_4S", " abra_t2v_6s "])("%s is Omni", (k) => {
    expect(isOmniClip(k)).toBe(true);
  });

  it.each(["veo_3_1_i2v_lite", "", undefined])("%s is not Omni", (k) => {
    expect(isOmniClip(k)).toBe(false);
  });
});

describe("what an upscale lands on the node", () => {
  it("writes its own key and never touches the original", () => {
    // The one property this whole feature turns on. The original is the file
    // that was paid for; a "make this better" button that replaces it is a loss
    // with no undo.
    const { patch, error } = upscaleLanding({ media_ids: ["up-1"] });
    expect(patch).toEqual({ upscaledMediaId: "up-1" });
    expect(patch).not.toHaveProperty("mediaId");
    expect(patch).not.toHaveProperty("mediaIds");
    expect(error).toBeNull();
  });

  it("reports an empty result instead of writing a blank id", () => {
    const { patch, error } = upscaleLanding({ media_ids: [null] });
    expect(patch).toEqual({});
    expect(error).toMatch(/không trả về clip/);
  });
});

describe("what an extension lands on the node", () => {
  it("appends the clip and its operation id in lockstep", () => {
    const { patch } = extendLanding(VEO, {
      media_ids: ["e-1"],
      operation_names: ["op-ext-1"],
      scene_id: "s-1",
      scene_clone_media_id: "c-1",
    });
    expect(patch.extensionMediaIds).toEqual(["e-1"]);
    expect(patch.extensionOperationIds).toEqual(["op-ext-1"]);
    expect(patch.sceneId).toBe("s-1");
    expect(patch.sceneCloneMediaId).toBe("c-1");
  });

  it("never touches the original clip either", () => {
    const { patch } = extendLanding(VEO, {
      media_ids: ["e-1"],
      operation_names: ["op-1"],
    });
    expect(patch).not.toHaveProperty("mediaId");
    expect(patch).not.toHaveProperty("mediaIds");
  });

  it("keeps the two lists the same length so link n+1 reads the right id", () => {
    const first = extendLanding(
      { ...VEO, extensionMediaIds: ["e-1"], extensionOperationIds: ["o-1"] },
      { media_ids: ["e-2"], operation_names: ["o-2"] },
    );
    expect(first.patch.extensionMediaIds).toEqual(["e-1", "e-2"]);
    expect(first.patch.extensionOperationIds).toEqual(["o-1", "o-2"]);
  });

  it("pads the operation list even when the id is unreadable", () => {
    // Otherwise the lists drift by one and the NEXT link references the wrong
    // clip — which Flow accepts and then fails, after billing.
    const { patch } = extendLanding(
      { ...VEO, extensionMediaIds: ["e-1"], extensionOperationIds: ["o-1"] },
      { media_ids: ["e-2"] },
    );
    expect(patch.extensionOperationIds).toEqual(["o-1", ""]);
    expect((patch.extensionMediaIds as string[]).length).toBe(2);
  });

  it("landing the same extension twice adds it once", () => {
    const state = {
      ...VEO,
      extensionMediaIds: ["e-1"],
      extensionOperationIds: ["o-1"],
    };
    const { patch, error } = extendLanding(state, {
      media_ids: ["e-1"],
      operation_names: ["o-1"],
    });
    expect(patch).not.toHaveProperty("extensionMediaIds");
    expect(error).toBeNull();
  });

  it("remembers the scene even when the render came back empty", () => {
    // The scene exists on Flow now. Forgetting it means the next attempt makes a
    // second one and restarts the chain at position 1.
    const { patch, error } = extendLanding(VEO, {
      media_ids: [],
      scene_id: "s-1",
      scene_clone_media_id: "c-1",
    });
    expect(patch).toEqual({ sceneId: "s-1", sceneCloneMediaId: "c-1" });
    expect(error).toMatch(/không trả về clip/);
  });
});


describe("a clip whose provenance is not recorded", () => {
  /** The hole this closes, concretely.
   *
   * Before the start-up backfill, every clip rendered before P16 had no
   * `sourceModelKey`. A blank string does not start with `abra_`, so an Omni clip
   * read as "not Omni", the extend button was offered, and the backend's own
   * refusal could not fire either — it reads the same field. Flow then accepts
   * the submit and fails it, after billing the attempt.
   *
   * So "no record" is refused, not allowed. Same rule as an unknown model key
   * never being called free: when the fact is missing, take the expensive
   * reading.
   */
  it.each([undefined, "", "   "])("is refused for extend (%p)", (key) => {
    const r = extendReadiness({ ...VEO, sourceModelKey: key });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toMatch(/Chạy lại clip|nối trong Flow/);
  });

  it("is a different refusal from the Omni one, so the card can explain which", () => {
    const unknown = extendReadiness({ ...VEO, sourceModelKey: undefined });
    const omni = extendReadiness({ ...VEO, sourceModelKey: "abra_i2v_8s" });
    expect(unknown.ok).toBe(false);
    expect(omni.ok).toBe(false);
    if (!unknown.ok && !omni.ok) expect(unknown.reason).not.toBe(omni.reason);
  });

  it("does NOT block an upscale — Omni clips upscale fine, so provenance is moot", () => {
    // Only extend is Veo-only. Refusing an upscale here would take away a
    // capability from a clip the user already paid for, for no safety gain.
    const r = upscaleReadiness({ ...VEO, sourceModelKey: undefined });
    expect(r.ok).toBe(true);
  });

  it.each([undefined, "", "  "])("hasUnknownSource(%p) is true", (k) => {
    expect(hasUnknownSource(k)).toBe(true);
  });

  it.each(["veo_3_1_i2v_lite", "abra_i2v_8s"])("hasUnknownSource(%p) is false", (k) => {
    expect(hasUnknownSource(k)).toBe(false);
  });
});
