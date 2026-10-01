import { describe, expect, it } from "vitest";
import {
  namesInPrompt,
  resolveLine,
  type Character,
} from "./CharacterSyncTab";

const cast: Character[] = [
  { key: 1, mediaId: "media-lan", name: "Lan" },
  { key: 2, mediaId: "media-minh", name: "Minh" },
  { key: 3, mediaId: "media-an", name: "An" },
  { key: 4, mediaId: "media-bo", name: "Bo" },
];

describe("namesInPrompt", () => {
  it("pulls every {Tên} out in order", () => {
    expect(namesInPrompt("{Lan} chào {Minh} rồi đi")).toEqual(["Lan", "Minh"]);
  });

  it("counts a repeated name once", () => {
    // Otherwise "{Lan} nhìn {Lan}" would send the same reference twice and
    // eat one of the three per-prompt slots for nothing.
    expect(namesInPrompt("{Lan} nhìn {lan} trong gương")).toEqual(["Lan"]);
  });

  it("ignores empty braces and finds nothing when there is no call", () => {
    expect(namesInPrompt("{} {  }")).toEqual([]);
    expect(namesInPrompt("một cảnh phố đông")).toEqual([]);
  });
});

describe("resolveLine", () => {
  it("sends only the characters the line actually calls", () => {
    // The whole point: a 10-person cast must not push 10 references into a
    // clip that mentions two of them.
    const r = resolveLine("{Lan} gặp {Minh}", cast);
    expect(r.refIds).toEqual(["media-lan", "media-minh"]);
    expect(r.unknown).toEqual([]);
    expect(r.tooMany).toBe(false);
  });

  it("strips the braces from the text sent to Flow", () => {
    // The braces are this tool's syntax; leaving them in asks the model to
    // render punctuation it was never meant to see.
    expect(resolveLine("{Lan} vẫy tay", cast).prompt).toBe("Lan vẫy tay");
  });

  it("matches names regardless of capitalisation", () => {
    expect(resolveLine("{lan} và {MINH}", cast).refIds).toEqual([
      "media-lan",
      "media-minh",
    ]);
  });

  it("reports a name that is not in the cast instead of dropping it", () => {
    const r = resolveLine("{Lan} gặp {Hoa}", cast);
    expect(r.unknown).toEqual(["Hoa"]);
    expect(r.refIds).toEqual(["media-lan"]);
  });

  it("flags a line calling more than three characters", () => {
    const r = resolveLine("{Lan} {Minh} {An} {Bo}", cast);
    expect(r.refIds).toHaveLength(4);
    expect(r.tooMany).toBe(true);
  });

  it("does not flag exactly three", () => {
    expect(resolveLine("{Lan} {Minh} {An}", cast).tooMany).toBe(false);
  });

  it("returns no refs for a line that names nobody", () => {
    const r = resolveLine("cảnh phố đông người", cast);
    expect(r.refIds).toEqual([]);
    expect(r.tooMany).toBe(false);
  });
});
