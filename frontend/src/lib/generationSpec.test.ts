import { describe, expect, it } from "vitest";
import {
  firstMediaId,
  mediaIdsOf,
  requestTypeFor,
  toParams,
  type GenerationSpec,
} from "./generationSpec";

/** The params each lane's handler actually reads.
 *
 * These are pinned against the backend handlers, not against each other. A
 * key the handler ignores is not harmless: it lands in the request row and
 * in the activity log, where the next reader takes it for a setting that
 * did something.
 */

describe("toParams", () => {
  it("sends a text-to-video length, because that lane's keys carry one", () => {
    expect(
      toParams({ kind: "t2v", aspect: "P", quality: "lite", durationS: 8 }),
    ).toEqual({ aspect_ratio: "P", duration_s: 8, video_quality: "lite" });
  });

  it("does NOT send a length on image-to-video", () => {
    // `_handle_gen_video` never reads `duration_s`. The Affiliate tab used
    // to send `duration_s: 8` here — a number that looked like a setting.
    const params = toParams({
      kind: "i2v",
      aspect: "P",
      quality: "lite",
      start: "m1",
    });
    expect(params).not.toHaveProperty("duration_s");
    expect(params).toEqual({
      aspect_ratio: "P",
      video_quality: "lite",
      start_media_id: "m1",
    });
  });

  it("does NOT send a quality on OMNI, which has one model", () => {
    const params = toParams({
      kind: "omni",
      aspect: "P",
      durationS: 6,
      refs: ["a", "b"],
    });
    expect(params).not.toHaveProperty("video_quality");
    expect(params).toEqual({
      aspect_ratio: "P",
      duration_s: 6,
      ref_media_ids: ["a", "b"],
    });
  });

  it("sends both frames on a start→end dispatch", () => {
    expect(
      toParams({
        kind: "startEnd",
        aspect: "P",
        quality: "fast",
        start: "a",
        end: "b",
      }),
    ).toEqual({
      aspect_ratio: "P",
      video_quality: "fast",
      start_media_id: "a",
      end_media_id: "b",
    });
  });

  it("omits variant_count when only one output is wanted", () => {
    // The handler treats a missing count as one. Sending `1` explicitly is
    // noise in every request row.
    const params = toParams({ kind: "image", aspect: "S", model: "NB2", variants: 1 });
    expect(params).not.toHaveProperty("variant_count");
  });

  it("sends variant_count when more than one is wanted", () => {
    const params = toParams({ kind: "image", aspect: "S", model: "NB2", variants: 4 });
    expect(params.variant_count).toBe(4);
  });

  it("omits refs when there are none, rather than sending an empty list", () => {
    const params = toParams({ kind: "image", aspect: "S", model: "NB2", refs: [] });
    expect(params).not.toHaveProperty("ref_media_ids");
  });
});

describe("requestTypeFor", () => {
  const cases: [GenerationSpec, string][] = [
    [{ kind: "image", aspect: "S", model: "NB2" }, "gen_image"],
    [{ kind: "t2v", aspect: "P", quality: "lite", durationS: 8 }, "gen_video_text"],
    [{ kind: "i2v", aspect: "P", quality: "lite", start: "m" }, "gen_video"],
    [
      { kind: "startEnd", aspect: "P", quality: "fast", start: "a", end: "b" },
      "gen_video",
    ],
    [{ kind: "omni", aspect: "P", durationS: 4, refs: ["a"] }, "gen_video_omni"],
  ];

  it.each(cases)("routes %o to its handler", (spec, expected) => {
    expect(requestTypeFor(spec)).toBe(expected);
  });

  it("keeps start→end on gen_video, not a lane of its own", () => {
    // The FL keys are selected inside `resolve_video_model` from the
    // presence of an end frame, not from a separate request type.
    expect(
      requestTypeFor({
        kind: "startEnd",
        aspect: "P",
        quality: "fast",
        start: "a",
        end: "b",
      }),
    ).toBe(requestTypeFor({ kind: "i2v", aspect: "P", quality: "lite", start: "a" }));
  });
});

describe("mediaIdsOf", () => {
  it("keeps a failed variant's slot as null", () => {
    // A Veo content filter can block one clip of four. Collapsing the list
    // would re-map which output came from which input.
    expect(mediaIdsOf({ media_ids: ["a", null, "c"] })).toEqual(["a", null, "c"]);
  });

  it("treats an empty string as a failed slot, not an id", () => {
    expect(mediaIdsOf({ media_ids: ["a", ""] })).toEqual(["a", null]);
  });

  it("survives a result that is missing, empty or the wrong shape", () => {
    expect(mediaIdsOf(null)).toEqual([]);
    expect(mediaIdsOf({})).toEqual([]);
    expect(mediaIdsOf({ media_ids: "nope" })).toEqual([]);
    expect(mediaIdsOf("nope")).toEqual([]);
  });
});

describe("firstMediaId", () => {
  it("skips over a leading failed variant", () => {
    expect(firstMediaId({ media_ids: [null, "b"] })).toBe("b");
  });

  it("is null when every variant failed", () => {
    expect(firstMediaId({ media_ids: [null, null] })).toBeNull();
    expect(firstMediaId({ media_ids: [] })).toBeNull();
  });
});

describe("length and resolution ride only where they are read", () => {
  it("omits both on a Veo image-to-video dispatch", () => {
    // A Veo key carries its own eight seconds and no resolution at all. Sending
    // either puts a number in the request row and the activity log that did
    // nothing — which is how a `duration_s: 8` on an i2v dispatch came to look
    // like a setting.
    const params = toParams({
      kind: "i2v",
      aspect: "P",
      quality: "lite",
      start: "m-1",
      durationS: 4,
      resolution: "360p",
    });
    expect(params).not.toHaveProperty("duration_s");
    expect(params).not.toHaveProperty("resolution");
  });

  it("sends both on the OMNI image-to-video lane", () => {
    const params = toParams({
      kind: "i2v",
      aspect: "P",
      quality: "omni",
      start: "m-1",
      durationS: 4,
      resolution: "360p",
    });
    expect(params.duration_s).toBe(4);
    expect(params.resolution).toBe("360p");
  });

  it("sends both on first-to-last frame, which only OMNI serves", () => {
    const params = toParams({
      kind: "startEnd",
      aspect: "P",
      quality: "omni",
      start: "a",
      end: "b",
      durationS: 6,
      resolution: "720p",
    });
    expect(params.duration_s).toBe(6);
    expect(params.end_media_id).toBe("b");
  });

  it("omits them on a Veo first-to-last request, which will be refused anyway", () => {
    // The refusal is the backend's job. What matters here is not sending a
    // length that a refused dispatch would log as though it had been honoured.
    const params = toParams({
      kind: "startEnd",
      aspect: "P",
      quality: "fast",
      start: "a",
      end: "b",
      durationS: 6,
    });
    expect(params).not.toHaveProperty("duration_s");
  });

  it("leaves the resolution out entirely when nothing chose one", () => {
    // Absent, not "720p": the SDK owns that default, and a second copy here
    // would keep sending the old value after the SDK's default changed.
    const params = toParams({
      kind: "omni",
      aspect: "P",
      durationS: 8,
      refs: ["a"],
    });
    expect(params).not.toHaveProperty("resolution");
  });
});

describe("resolutionFromConfig", () => {
  it("accepts only what Flow's OMNI builder accepts", async () => {
    const { resolutionFromConfig } = await import("../store/models");
    expect(resolutionFromConfig("720p")).toBe("720p");
    expect(resolutionFromConfig("360P")).toBe("360p");
    // The packaged tool's config.json ships 480p, which the builder rejects.
    // Dropped here as well as in the backend, because it can also arrive from
    // a stale browser cache.
    expect(resolutionFromConfig("480p")).toBeUndefined();
    expect(resolutionFromConfig(undefined)).toBeUndefined();
    expect(resolutionFromConfig(1080)).toBeUndefined();
  });
});
