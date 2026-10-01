/** One shape for "make me something", instead of nine hand-rolled param bags.
 *
 * Every tab used to assemble its own `params` object literal. They disagreed
 * in ways that are invisible until you compare them side by side:
 *
 *   * the Affiliate tab sent `duration_s: 8` on an image-to-video dispatch,
 *     which `_handle_gen_video` never reads — a number that looked like a
 *     setting and was not one;
 *   * text-to-video sent `duration_s`, image-to-video did not, and nothing
 *     said which lanes take a length and which encode it in the model key;
 *   * `video_quality` went out on OMNI dispatches, where the concept does
 *     not exist.
 *
 * The union below makes those states unrepresentable: a lane's spec carries
 * exactly the fields that lane's handler reads. The compiler is what enforces
 * it, so a future tab cannot quietly invent a tenth variation.
 *
 * This deliberately does NOT introduce a new storage table. `Request` already
 * records this lifecycle (type / params / result / status / attempt); a
 * parallel table would mean writing twice and keeping two truths in step.
 * What is standardised here is the SHAPE, not a second place to keep it.
 */

export type GenerationSpec =
  | ImageSpec
  | TextToVideoSpec
  | ImageToVideoSpec
  | StartEndSpec
  | OmniSpec;

export interface ImageSpec {
  kind: "image";
  aspect: string;
  model: string;
  /** How many outputs per prompt. One dispatch, N results. */
  variants?: number;
  /** Reference images, for image-to-image. */
  refs?: string[];
}

export interface TextToVideoSpec {
  kind: "t2v";
  aspect: string;
  /** Any lane the registry offered. It was briefly `"omni"`-or-refused, on the
   *  belief that the September 2026 transport had no Veo text-to-video payload.
   *  Measured live 19/09/2026: it does — `veo_3_1_t2v_lite` produced a real
   *  8.00s clip — so the Veo lanes are back and the lane a board picks is the
   *  lane that is sent. A lane with no key behind it is refused by the backend,
   *  for free, rather than swapped for a pricier one. */
  quality: string;
  /** Part of the Omni model key, so a wrong value bills a different length. */
  durationS: number;
  resolution?: string;
}

export interface ImageToVideoSpec {
  kind: "i2v";
  aspect: string;
  quality: string;
  start: string;
  /** Read on the `omni` lane only, where the length is part of the model key. A
   *  Veo key carries its own 8 seconds, and sending a length beside one would
   *  be a number that looks like a setting and is not. */
  durationS?: number;
  resolution?: string;
}

export interface StartEndSpec {
  kind: "startEnd";
  aspect: string;
  /** Has to be `"omni"`. This is where the capability changed hands: Omni gained
   *  first-to-last frame (`nprQif`) while Veo's FL keys stopped being accepted
   *  and its end-image payload was never captured. A Veo lane here is refused
   *  rather than run as a plain first-frame clip — that would ignore the end
   *  frame the user picked and bill for it, which is the exact failure this
   *  lane's model key was introduced to prevent. */
  quality: string;
  start: string;
  end: string;
  durationS?: number;
  resolution?: string;
}

export interface OmniSpec {
  kind: "omni";
  aspect: string;
  /** OMNI bills by length, so this one is a real per-request choice. */
  durationS: number;
  refs: string[];
  resolution?: string;
  // No quality: OMNI has one model family here.
}

/** The worker request type this spec dispatches as. */
export function requestTypeFor(spec: GenerationSpec): string {
  switch (spec.kind) {
    case "image":
      return "gen_image";
    case "t2v":
      return "gen_video_text";
    case "omni":
      return "gen_video_omni";
    case "i2v":
    case "startEnd":
      return "gen_video";
  }
}

/** Length and resolution, but only where the dispatch will read them.
 *
 * Both belong to Omni's model key. A Veo key carries its own eight seconds and
 * no resolution at all, so sending either alongside one puts a number in the
 * request row and the activity log that did nothing — which is how a
 * `duration_s: 8` on an image-to-video dispatch came to look like a setting.
 */
function omniExtras(spec: {
  quality: string;
  durationS?: number;
  resolution?: string;
}): Record<string, unknown> {
  if (spec.quality !== "omni") return {};
  const out: Record<string, unknown> = {};
  if (spec.durationS) out.duration_s = spec.durationS;
  if (spec.resolution) out.resolution = spec.resolution;
  return out;
}

/** The params the handler for this spec actually reads.
 *
 * Nothing else is added. A key the handler ignores is not free: it shows up
 * in the request row and in the activity log, where the next person reads it
 * as a setting that did something.
 */
export function toParams(spec: GenerationSpec): Record<string, unknown> {
  switch (spec.kind) {
    case "image": {
      const params: Record<string, unknown> = {
        aspect_ratio: spec.aspect,
        image_model: spec.model,
      };
      if (spec.variants && spec.variants > 1) params.variant_count = spec.variants;
      if (spec.refs && spec.refs.length > 0) params.ref_media_ids = spec.refs;
      return params;
    }
    case "t2v":
      return {
        aspect_ratio: spec.aspect,
        duration_s: spec.durationS,
        video_quality: spec.quality,
        ...omniExtras(spec),
      };
    case "i2v":
      return {
        aspect_ratio: spec.aspect,
        video_quality: spec.quality,
        start_media_id: spec.start,
        // Length and resolution only on the Omni lane — see `omniExtras`.
        ...omniExtras(spec),
      };
    case "startEnd":
      return {
        aspect_ratio: spec.aspect,
        video_quality: spec.quality,
        start_media_id: spec.start,
        end_media_id: spec.end,
        ...omniExtras(spec),
      };
    case "omni":
      return {
        aspect_ratio: spec.aspect,
        duration_s: spec.durationS,
        ...(spec.resolution ? { resolution: spec.resolution } : {}),
        ref_media_ids: spec.refs,
      };
  }
}

/** What a settled request produced, read the same way for every lane.
 *
 * `mediaIds` carries a positional `null` for a variant that failed — a Veo
 * content filter can block one clip of four — and the slot is kept so the
 * index still lines up with the upstream's variants. Collapsing the list
 * would silently re-map which output came from which input.
 */
export function mediaIdsOf(result: unknown): (string | null)[] {
  if (!result || typeof result !== "object") return [];
  const ids = (result as { media_ids?: unknown }).media_ids;
  if (!Array.isArray(ids)) return [];
  return ids.map((id) => (typeof id === "string" && id ? id : null));
}

/** The first usable output, or null when every variant failed. */
export function firstMediaId(result: unknown): string | null {
  return mediaIdsOf(result).find((id): id is string => id !== null) ?? null;
}
