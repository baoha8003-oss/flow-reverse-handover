import { describe, expect, it } from "vitest";
import { modelScale } from "./UpscaleTab";

/** The scale sent to the backend is derived from the model name, never chosen
 * by the user. The bundled engines are fixed-ratio networks that accept a
 * mismatched `-s` and still exit 0, writing a correctly sized image of
 * corrupted pixels (measured 4.5 dB PSNR against 26 dB at the native ratio).
 * If this function ever returns something other than the ratio in the name,
 * the UI starts asking for silently broken output. */
describe("modelScale", () => {
  it("reads the ratio out of the bundled model names", () => {
    expect(modelScale("realesrgan-x4plus")).toBe(4);
    expect(modelScale("upscayl-lite-4x")).toBe(4);
  });

  it("handles both spellings of the ratio", () => {
    expect(modelScale("something-x2plus")).toBe(2);
    expect(modelScale("something-3x")).toBe(3);
  });

  it("falls back to 4 for a name with no ratio in it", () => {
    // Every model actually shipped is 4x, so guessing 4 keeps an unknown
    // name working; guessing 2 would corrupt output on all of them.
    expect(modelScale("mystery-model")).toBe(4);
    expect(modelScale("")).toBe(4);
  });
});
