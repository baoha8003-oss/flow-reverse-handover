import { beforeEach, describe, expect, it, vi } from "vitest";

/** The two canvas view switches.
 *
 * The one worth a test is "hide previews". It is a VIEW switch, and the
 * failure that would matter is it being mistaken for a clear — so what these
 * assert is that flipping it changes nothing but itself, and that it survives
 * a reload the way a preference should.
 */

const LS_KEY = "veo3.canvasUi.v1";

/** The suite runs in node, where `localStorage` does not exist. The store
 *  already survives that (its reads and writes are wrapped, so a private
 *  window degrades to session-only) — but "remembers across a reload" cannot
 *  be tested without somewhere to remember into. */
function installStorage(): void {
  const data = new Map<string, string>();
  (globalThis as { localStorage?: Storage }).localStorage = {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: (i: number) => Array.from(data.keys())[i] ?? null,
    get length() {
      return data.size;
    },
  } as Storage;
}

installStorage();

async function freshStore() {
  vi.resetModules();
  const mod = await import("./canvasUi");
  return mod.useCanvasUiStore;
}

describe("canvas view switches", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts with everything shown", async () => {
    const store = await freshStore();
    expect(store.getState().previewsHidden).toBe(false);
    expect(store.getState().paletteCollapsed).toBe(false);
  });

  it("remembers hidden previews across a reload", async () => {
    const store = await freshStore();
    store.getState().togglePreviews();
    expect(store.getState().previewsHidden).toBe(true);

    const reloaded = await freshStore();
    expect(reloaded.getState().previewsHidden).toBe(true);
  });

  it("keeps the two switches independent", async () => {
    // Collapsing the node library while previews are hidden used to be the
    // easy way to write one over the other: both persist to the same key.
    const store = await freshStore();
    store.getState().togglePreviews();
    store.getState().togglePalette();
    expect(store.getState().previewsHidden).toBe(true);
    expect(store.getState().paletteCollapsed).toBe(true);

    const reloaded = await freshStore();
    expect(reloaded.getState().previewsHidden).toBe(true);
    expect(reloaded.getState().paletteCollapsed).toBe(true);
  });

  it("toggles back off", async () => {
    const store = await freshStore();
    store.getState().togglePreviews();
    store.getState().togglePreviews();
    expect(store.getState().previewsHidden).toBe(false);
    const reloaded = await freshStore();
    expect(reloaded.getState().previewsHidden).toBe(false);
  });

  it("survives storage that refuses to be read", async () => {
    // Private windows throw on read. A view preference must not take the
    // canvas down with it.
    localStorage.setItem(LS_KEY, "{not json");
    const store = await freshStore();
    expect(store.getState().previewsHidden).toBe(false);
  });
});
