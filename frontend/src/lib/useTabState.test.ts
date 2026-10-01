import { describe, expect, it } from "vitest";
import { applyLoadedTabState } from "./useTabState";

describe("applyLoadedTabState", () => {
  it("fills in what was saved", () => {
    const next = applyLoadedTabState({ url: "", height: "1080" }, { url: "https://x/y" }, false);
    expect(next).toEqual({ url: "https://x/y", height: "1080" });
  });

  it("keeps the default for a field added since the state was written", () => {
    // Merging OVER the defaults, not replacing them — a field the stored state
    // has never heard of must not come back undefined and blank the box.
    const next = applyLoadedTabState({ url: "", note: "", shot: "full" }, { url: "a" }, false);
    expect(next.shot).toBe("full");
  });

  it("does not overwrite what the user already typed", () => {
    // The load is in flight while someone types. Applying it then replaces
    // their keystroke with yesterday's value — the field appears to edit
    // itself, and nothing on screen says why.
    const next = applyLoadedTabState({ url: "đang gõ" }, { url: "hôm qua" }, true);
    expect(next.url).toBe("đang gõ");
  });

  it("leaves the state alone when there is nothing saved", () => {
    const current = { url: "", height: "1080" };
    expect(applyLoadedTabState(current, null, false)).toBe(current);
    expect(applyLoadedTabState(current, undefined, false)).toBe(current);
  });

  it("carries a stored null through", () => {
    // `garmentId` is a media id or null; null is a real saved value ("nothing
    // uploaded"), not an absent key to fall back on.
    const next = applyLoadedTabState(
      { garmentId: "m-1" as string | null },
      { garmentId: null },
      false,
    );
    expect(next.garmentId).toBeNull();
  });
});
