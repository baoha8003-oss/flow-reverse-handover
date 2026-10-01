import { describe, expect, it } from "vitest";
import { composePrompt } from "./ImageToImageTab";

/** Flow's payload carries only the media id as a reference's name, so the
 * label the user typed reaches the model ONLY through the prompt text. Drop
 * this composition and the names are decorative: the model cannot tell the
 * references apart and blends them. */
describe("composePrompt", () => {
  it("names every reference and keeps the user's prompt last", () => {
    const out = composePrompt("hai người ngồi quán cà phê", [
      { key: 1, mediaId: "media-aaa", name: "Lan" },
      { key: 2, mediaId: "media-bbb", name: "Minh" },
    ]);

    expect(out).toContain("Lan");
    expect(out).toContain("media-aaa");
    expect(out).toContain("Minh");
    expect(out).toContain("media-bbb");
    expect(out.endsWith("hai người ngồi quán cà phê")).toBe(true);
    // Each reference is introduced before the prompt it applies to.
    expect(out.indexOf("media-aaa")).toBeLessThan(out.indexOf("hai người"));
  });

  it("leaves a prompt untouched when there are no references", () => {
    expect(composePrompt("chỉ prompt", [])).toBe("chỉ prompt");
  });

  it("trims the typed name so stray spaces don't reach the model", () => {
    const out = composePrompt("x", [{ key: 1, mediaId: "m", name: "  Lan  " }]);
    expect(out).toContain("Lan là");
    expect(out).not.toContain("  Lan");
  });
});
