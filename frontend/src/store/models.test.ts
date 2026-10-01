import { describe, expect, it } from "vitest";
import {
  aspectFromConfig,
  canSeed,
  creditsFor,
  defaultValue,
  noteFor,
  qualityFromLabel,
  toSelectOptions,
} from "./models";
import type { DurationOption, ModelOption } from "@/api/client";

/** The helpers every tab now seeds its controls through.
 *
 * These replaced twenty hand-typed option lists across ten files. The lists
 * had drifted, and the drift cost money, so the replacements are pinned here
 * rather than trusted.
 */

const opt = (value: string, label = value, note: string | null = null): ModelOption => ({
  value,
  label,
  note,
});

const LANE: ModelOption[] = [
  opt("lite", "Veo 3.1 - Lite"),
  opt("fast", "Veo 3.1 - Fast"),
  opt("lite_relaxed", "Veo 3.1 - Lite [Lower Priority]", "sẽ CÓ tính credit"),
  opt("quality", "Veo 3.1 - Quality", "sẽ chạy Fast"),
];

const ASPECTS: ModelOption[] = [
  opt("VIDEO_ASPECT_RATIO_PORTRAIT", "Dọc 9:16"),
  opt("VIDEO_ASPECT_RATIO_LANDSCAPE", "Ngang 16:9"),
];

describe("defaultValue", () => {
  it("prefers an option that carries no billing warning", () => {
    // The one that must never happen: seeding onto a lane that reads free
    // and is billed. Putting the warned lane first proves the preference is
    // real and not just "the first entry".
    const reordered = [LANE[2], LANE[0], LANE[1]];
    expect(defaultValue(reordered)).toBe("lite");
  });

  it("falls back to the first option when every lane is warned", () => {
    expect(defaultValue([LANE[2], LANE[3]])).toBe("lite_relaxed");
  });

  it("returns the caller's fallback for an empty lane", () => {
    // An empty lane means the registry has not loaded. The caller decides
    // what that means; this must not invent a model key.
    expect(defaultValue([], "")).toBe("");
    expect(defaultValue([])).toBe("");
  });
});

describe("qualityFromLabel", () => {
  it("matches the exact label, never a substring", () => {
    // "Veo 3.1 - Lite [Lower Priority]" contains "Lite". Substring matching
    // used to land on the wrong lane in both directions.
    expect(qualityFromLabel("Veo 3.1 - Lite [Lower Priority]", LANE)).toBe(
      "lite_relaxed",
    );
    expect(qualityFromLabel("Veo 3.1 - Lite", LANE)).toBe("lite");
  });

  it("sends an unknown label to an unwarned lane, not the priciest one", () => {
    expect(qualityFromLabel("Veo 9 - Imaginary", LANE)).toBe("lite");
  });

  it("handles a missing setting without throwing", () => {
    expect(qualityFromLabel(undefined, LANE)).toBe("lite");
    expect(qualityFromLabel(null, LANE)).toBe("lite");
  });

  it("trims what the settings file stored", () => {
    expect(qualityFromLabel("  Veo 3.1 - Fast  ", LANE)).toBe("fast");
  });
});

describe("aspectFromConfig", () => {
  it("maps the settings spelling onto this lane's enum", () => {
    expect(aspectFromConfig("16:9", ASPECTS)).toBe(
      "VIDEO_ASPECT_RATIO_LANDSCAPE",
    );
    expect(aspectFromConfig("9:16", ASPECTS)).toBe("VIDEO_ASPECT_RATIO_PORTRAIT");
  });

  it("cannot seed an orientation the lane does not offer", () => {
    // The reason it matches against the lane instead of a hardcoded pair.
    const portraitOnly = [ASPECTS[0]];
    expect(aspectFromConfig("16:9", portraitOnly)).toBe(
      "VIDEO_ASPECT_RATIO_PORTRAIT",
    );
  });

  it("returns empty for an empty lane rather than guessing an enum", () => {
    expect(aspectFromConfig("16:9", [])).toBe("");
  });
});

describe("noteFor", () => {
  it("returns the warning for a substituted lane", () => {
    expect(noteFor(LANE, "lite_relaxed")).toContain("credit");
  });

  it("returns null for a lane that runs as labelled", () => {
    // Otherwise the warning becomes noise and stops being read.
    expect(noteFor(LANE, "lite")).toBeNull();
  });

  it("returns null for a value that is not in the lane", () => {
    expect(noteFor(LANE, "nonexistent")).toBeNull();
  });
});

describe("creditsFor", () => {
  const DURATIONS: DurationOption[] = [
    { value: 4, label: "4s", credits: 15, note: null },
    { value: 8, label: "8s", credits: 25, note: null },
    { value: 10, label: "10s", credits: null, note: null },
  ];

  it("prices a length the lane publishes", () => {
    expect(creditsFor(DURATIONS, 8)).toBe(25);
  });

  it("accepts the string a select element hands back", () => {
    expect(creditsFor(DURATIONS, "4")).toBe(15);
  });

  it("returns null — not zero — when the price is unknown", () => {
    // Zero would render as "free" and understate a real bill.
    expect(creditsFor(DURATIONS, 10)).toBeNull();
    expect(creditsFor(DURATIONS, 6)).toBeNull();
  });
});

describe("toSelectOptions", () => {
  it("drops the note, which is rendered separately", () => {
    expect(toSelectOptions(LANE)).toEqual([
      { value: "lite", label: "Veo 3.1 - Lite" },
      { value: "fast", label: "Veo 3.1 - Fast" },
      { value: "lite_relaxed", label: "Veo 3.1 - Lite [Lower Priority]" },
      { value: "quality", label: "Veo 3.1 - Quality" },
    ]);
  });

  it("survives an empty lane", () => {
    expect(toSelectOptions([])).toEqual([]);
  });
});

describe("canSeed", () => {
  it("waits for the registry even after the settings have arrived", () => {
    // The three-tab bug. Seeding here leaves every control on "", and an
    // empty quality resolves to `fast` at the backend — a pricier lane than
    // the `lite` these tabs used to default to.
    expect(canSeed(true, [])).toBe(false);
  });

  it("waits for the settings even after the registry has arrived", () => {
    expect(canSeed(false, LANE)).toBe(false);
  });

  it("allows seeding once both have landed", () => {
    expect(canSeed(true, LANE)).toBe(true);
  });

  it("is false before either has landed", () => {
    expect(canSeed(false, [])).toBe(false);
  });
});
