import { beforeEach, describe, expect, it } from "vitest";

/** What the canvas spends when nobody opened Settings.
 *
 * `useSettingsStore` is localStorage-only — it never syncs to `/api/settings` —
 * and `dispatchGeneration` reads it directly for every canvas dispatch. So its
 * defaults are not a UI preference: they are the lane and the model a user who
 * never touched Settings is billed on.
 *
 * The backend records the opposite rule for exactly this case, at
 * `flow_sdk.DEFAULT_VIDEO_QUALITY`: "`lite`, not `fast`: `fast` maps to the
 * priciest key this path has, and a board that never chose should not be charged
 * the most for it." Because the store always sends a value, that backend default
 * was never reached and the canvas inverted it.
 */

/** The suite runs in node, where `localStorage` does not exist, and this store
 * reads it at module load. An empty stub is exactly the case under test: a fresh
 * install that has never opened Settings. */
function stubEmptyStorage(): void {
  const store = new Map<string, string>();
  (globalThis as { localStorage?: Storage }).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: () => null,
    get length() {
      return store.size;
    },
  } as Storage;
}

describe("the canvas's own defaults", () => {
  beforeEach(() => {
    stubEmptyStorage();
  });

  it("does not put an unconfigured board on the priciest lane", async () => {
    const { useSettingsStore } = await import("./settings");
    // `fast` resolves to `veo_3_1_i2v_s_fast_ultra`, the most expensive key on
    // this transport. Matching the backend's recorded default is the point; the
    // exact cheaper value is not what this pins.
    expect(useSettingsStore.getState().videoQuality).not.toBe("fast");
    expect(useSettingsStore.getState().videoQuality).not.toBe("quality");
  });

  it("can represent every lane and image model the registry serves", async () => {
    const { VALID_VIDEO_QUALITIES, VALID_IMAGE_MODELS } = await import("./settings");
    // `omni` was missing entirely — the lane the batch migration made mandatory
    // for text-to-video, first→last and references — so the canvas could not
    // select the only family that serves them.
    expect(VALID_VIDEO_QUALITIES).toContain("omni");
    // And `NANO_BANANA_2_LITE` was unreachable from this panel while
    // `/api/models` listed it.
    expect(VALID_IMAGE_MODELS).toContain("NANO_BANANA_2_LITE");
  });

  it("does not offer a lane that has no key behind it", async () => {
    const { VALID_VIDEO_QUALITIES } = await import("./settings");
    // `quality` has no entry in `BATCH_VIDEO_LANES`, so choosing it can only
    // produce a refusal. Offering it reads as a lane that works.
    expect(VALID_VIDEO_QUALITIES).not.toContain("quality");
  });
});
