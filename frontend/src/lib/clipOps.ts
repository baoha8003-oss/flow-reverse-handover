/**
 * Operations on a clip that already rendered: upscale, and extend.
 *
 * These are not generations, and treating them like one is how they go wrong.
 * A generation writes the node's media; these must not. The original clip is
 * what the user paid for, so an upscale lands on `upscaledMediaId` and an
 * extension appends to `extensionMediaIds` — `mediaId` is never touched by
 * either. There is no undo for overwriting it.
 *
 * Everything here is pure: what the button may do, why it may not, and what
 * lands on the node afterwards. The network lives in the store; the decisions
 * live here because these are the money rules and they need tests.
 */

/** The parts of a node's data these operations read. */
export interface ClipOpNodeState {
  mediaId?: string;
  mediaIds?: (string | null)[];
  /** Positional twin of `mediaIds`: clip *i* came from operation *i*. */
  operationNames?: (string | null)[];
  /** The Flow model key that produced the clip. Omni cannot be extended. */
  sourceModelKey?: string;
  upscaledMediaId?: string;
  sceneId?: string;
  sceneCloneMediaId?: string;
  extensionMediaIds?: string[];
  /** Aligned with `extensionMediaIds`: link *n+1* must reference link *n*. */
  extensionOperationIds?: string[];
  status?: string;
  /** Flow's `VIDEO_ASPECT_RATIO_*`. Upscale reads it: one capture derives the
   *  aspect slot from the source clip, and sending the wrong one asks for the
   *  wrong frame and pays for it. */
  aspectRatio?: string;
}

export interface Refused {
  ok: false;
  /** Shown on the card. Written for the user, not for a log. */
  reason: string;
}

export interface UpscaleReady {
  ok: true;
  operationId: string;
  mediaId: string;
}

export interface ExtendReady {
  ok: true;
  mediaId: string;
  /** 1 for the first link, then one more each time. */
  position: number;
  /** Set from link 2 onwards: the operation id of the previous extension. */
  previousOperationId?: string;
  sceneId?: string;
  sceneCloneMediaId?: string;
  sourceModelKey?: string;
}

/** Which variant these operations act on: the first slot that actually rendered.
 *
 * Slot alignment is the whole reason `operationNames` is kept as a list rather
 * than a single id. A 3-variant batch whose first two were content-filtered has
 * its clip at slot 2, and slot 2's operation is the only one that addresses it.
 */
export function primarySlot(data: ClipOpNodeState): number {
  const ids = data.mediaIds;
  if (Array.isArray(ids) && ids.length > 0) {
    const at = ids.findIndex((m) => typeof m === "string" && m.length > 0);
    return at;
  }
  return typeof data.mediaId === "string" && data.mediaId.length > 0 ? 0 : -1;
}

function mediaAt(data: ClipOpNodeState, slot: number): string {
  const ids = data.mediaIds;
  if (Array.isArray(ids) && slot >= 0 && slot < ids.length) {
    const v = ids[slot];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return typeof data.mediaId === "string" ? data.mediaId : "";
}

/** True when the clip came from Omni. Flow greys the extend button out for it. */
export function isOmniClip(modelKey: string | undefined): boolean {
  return typeof modelKey === "string" && modelKey.trim().toLowerCase().startsWith("abra_");
}

/** True when no record says what produced this clip.
 *
 * Distinguished from "produced by Omni" because the two are different facts with
 * the same consequence. A clip whose provenance is unknown must be refused an
 * extension for the same reason an unknown model key is never called free: the
 * safe reading of "no record" is the expensive one. Before the start-up backfill
 * existed, every clip rendered before P16 landed here — and because a blank key
 * does not start with `abra_`, it read as "not Omni" and the button offered an
 * extension Flow would refuse after billing the attempt. */
export function hasUnknownSource(modelKey: string | undefined): boolean {
  return typeof modelKey !== "string" || modelKey.trim() === "";
}

/**
 * May this clip be upscaled, and with which ids?
 *
 * Needs BOTH ids. They are different things — the operation that produced the
 * clip and the media it produced — and the capture warns that swapping them
 * gets the request accepted and then failed with NOT_FOUND. So a clip with no
 * recorded operation is refused by name rather than upscaled with a guess: a
 * wrong id costs a submit to learn it was wrong.
 */
export function upscaleReadiness(data: ClipOpNodeState): UpscaleReady | Refused {
  if (data.status === "queued" || data.status === "running") {
    return { ok: false, reason: "Clip đang chạy — đợi xong rồi mới nâng cấp được." };
  }
  const slot = primarySlot(data);
  if (slot < 0) {
    return { ok: false, reason: "Chưa có clip nào để nâng cấp." };
  }
  const ops = data.operationNames;
  const op = Array.isArray(ops) && slot < ops.length ? ops[slot] : null;
  if (typeof op !== "string" || !op) {
    return {
      ok: false,
      reason:
        "Clip này không lưu operation id (render trước khi app bắt đầu lưu). "
        + "Chạy lại clip để có id, hoặc upscale trong Flow.",
    };
  }
  const mediaId = mediaAt(data, slot);
  if (!mediaId) return { ok: false, reason: "Chưa có clip nào để nâng cấp." };
  return { ok: true, operationId: op, mediaId };
}

/**
 * May this clip be extended, and from what?
 *
 * The chain rule is the part that matters. Link 1 references the CLONE the scene
 * makes; every later link references the PREVIOUS extension's OPERATION id.
 * Using a media id for link 2+ produces a request Flow accepts and then fails
 * NOT_FOUND — billed, and no clip. So the previous operation id is carried here
 * explicitly rather than derived from the media list.
 */
export function extendReadiness(data: ClipOpNodeState): ExtendReady | Refused {
  if (data.status === "queued" || data.status === "running") {
    return { ok: false, reason: "Clip đang chạy — đợi xong rồi mới nối được." };
  }
  if (isOmniClip(data.sourceModelKey)) {
    return {
      ok: false,
      reason:
        `Flow chỉ nối được clip Veo; clip này là “${data.sourceModelKey}” (Omni) `
        + "nên nút nối bị mờ.",
    };
  }
  if (hasUnknownSource(data.sourceModelKey)) {
    // Refused, not allowed through. Flow only extends Veo, and a clip with no
    // recorded model could be either — dispatching to find out is a submit Flow
    // accepts and then fails, after billing it.
    return {
      ok: false,
      reason:
        "Không có bản ghi clip này từ model nào (render trước khi app lưu thông "
        + "tin đó). Chạy lại clip để có bản ghi, hoặc nối trong Flow.",
    };
  }
  const slot = primarySlot(data);
  const mediaId = slot < 0 ? "" : mediaAt(data, slot);
  if (!mediaId) return { ok: false, reason: "Chưa có clip nào để nối." };
  const done = Array.isArray(data.extensionMediaIds) ? data.extensionMediaIds : [];
  const ops = Array.isArray(data.extensionOperationIds)
    ? data.extensionOperationIds
    : [];
  const previous = ops.length > 0 ? ops[ops.length - 1] : undefined;
  return {
    ok: true,
    mediaId,
    position: done.length + 1,
    previousOperationId: previous || undefined,
    sceneId: data.sceneId || undefined,
    sceneCloneMediaId: data.sceneCloneMediaId || undefined,
    sourceModelKey: data.sourceModelKey || undefined,
  };
}

export interface ClipOpLanding {
  /** Node-data deltas to merge. Empty when nothing usable came back. */
  patch: Record<string, unknown>;
  /** Set when the result carried no clip; shown instead of a silent no-op. */
  error: string | null;
}

function firstMedia(result: Record<string, unknown>): string {
  const ids = result["media_ids"];
  if (Array.isArray(ids)) {
    for (const v of ids) if (typeof v === "string" && v) return v;
  }
  return "";
}

function firstOperation(result: Record<string, unknown>): string {
  const ops = result["operation_names"];
  if (Array.isArray(ops)) {
    for (const v of ops) if (typeof v === "string" && v) return v;
  }
  return "";
}

/**
 * What an upscale leaves on the node.
 *
 * `mediaId` and `mediaIds` are deliberately absent from the patch. The upscaled
 * file is a SECOND artefact, not a replacement: keeping both is what makes the
 * button safe to press.
 */
export function upscaleLanding(result: Record<string, unknown>): ClipOpLanding {
  const media = firstMedia(result);
  if (!media) {
    return { patch: {}, error: "Upscale xong mà không trả về clip nào." };
  }
  return { patch: { upscaledMediaId: media }, error: null };
}

/**
 * What an extension leaves on the node.
 *
 * The two lists stay aligned, because link *n+1* reads `extensionOperationIds`
 * to know what to reference. A media id recorded without its operation id would
 * make the next link fall back to the clone and restart the chain from the
 * start of the clip.
 *
 * Re-landing the same extension is a no-op rather than a duplicate: a retry or a
 * second poll of the same request must not make the card show one clip twice.
 */
export function extendLanding(
  existing: ClipOpNodeState,
  result: Record<string, unknown>,
): ClipOpLanding {
  const scene: Record<string, unknown> = {};
  const sceneId = result["scene_id"];
  if (typeof sceneId === "string" && sceneId) scene.sceneId = sceneId;
  const clone = result["scene_clone_media_id"];
  if (typeof clone === "string" && clone) scene.sceneCloneMediaId = clone;

  const media = firstMedia(result);
  if (!media) {
    // The scene still goes on the node when there is one: it exists on Flow, and
    // forgetting it means the next attempt makes a second one and restarts the
    // chain at position 1.
    return { patch: scene, error: "Nối xong mà không trả về clip nào." };
  }
  const media_ids = Array.isArray(existing.extensionMediaIds)
    ? [...existing.extensionMediaIds]
    : [];
  const op_ids = Array.isArray(existing.extensionOperationIds)
    ? [...existing.extensionOperationIds]
    : [];
  if (media_ids.includes(media)) {
    return { patch: scene, error: null };
  }
  media_ids.push(media);
  // Push in lockstep even when the operation id is unreadable, so index *i* of
  // one list always describes index *i* of the other.
  op_ids.push(firstOperation(result));
  return {
    patch: {
      ...scene,
      extensionMediaIds: media_ids,
      extensionOperationIds: op_ids,
    },
    error: null,
  };
}
