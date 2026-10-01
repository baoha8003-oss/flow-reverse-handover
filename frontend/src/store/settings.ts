import { create } from "zustand";

/**
 * Per-user model preferences. Survives page reload via localStorage —
 * single-user, single-host app, so no need for server persistence.
 *
 * Image model: Flow ships two checkpoints — "NANO_BANANA_PRO" (premium,
 * higher quality, slower) and "NANO_BANANA_2" (faster, lighter). Users
 * pick once in the dashboard Settings panel; every gen_image / edit_image
 * dispatch reads the cached preference and forwards it to the worker.
 *
 * The video lane is NOT a display. `dispatchGeneration` reads `videoQuality`
 * from here for every canvas dispatch, so whatever this store holds is the lane
 * that gets billed. The old comment here described a server-resolved
 * `[tier][quality][aspect]` lookup that no longer exists: the batch transport
 * sends no tier, and aspect became its own payload slot.
 */
export type ImageModelKey =
  | "NANO_BANANA_PRO"
  | "NANO_BANANA_2"
  | "NANO_BANANA_2_LITE";
// The lanes this transport actually has a key for, plus OMNI.
//
//   - lite         Veo 3.1 Lite
//   - fast         Veo 3.1 Fast — the priciest key on this path
//   - lite_relaxed Lite on the low-priority queue (0 credit where the plan has it)
//   - omni         OMNI Flash: the ONLY family serving text-to-video,
//                  first→last and references on this transport
//
// `quality` is deliberately absent: it has no entry in `BATCH_VIDEO_LANES`, so
// offering it could only produce a refusal while reading like a working lane.
// The removed comment also promised that Pro users picking `lite_relaxed` "fall
// back to Fast on the backend" — that substitution is exactly what this build
// exists to avoid, and `routes/models.py` records that the promise must not be
// shown again.
export type VideoQuality =
  | "fast"
  | "lite"
  | "lite_relaxed"
  | "omni";

// Video model family. "veo" = the existing Veo 3.1 i2v family controlled
// by videoQuality (lite/fast/quality/...). "omni_flash" = the new
// reference-image r2v model with per-duration credit cost and no
// per-tier quality variants — duration is picked per dispatch in the
// GenerationDialog. The video dispatch path branches on this.
export type VideoModelFamily = "veo" | "omni_flash";

// The lengths Omni Flash accepts. Their credit prices used to be mirrored
// here as a literal; they now come from /api/models, because a stale copy
// would quote a cost the account never gets charged. The type stays: it is
// what the persisted preference is validated against.
export type OmniFlashDuration = 4 | 6 | 8 | 10;

interface SettingsState {
  imageModel: ImageModelKey;
  videoQuality: VideoQuality;
  videoModel: VideoModelFamily;
  omniFlashDuration: OmniFlashDuration;
  setImageModel(model: ImageModelKey): void;
  setVideoQuality(q: VideoQuality): void;
  setVideoModel(m: VideoModelFamily): void;
  setOmniFlashDuration(d: OmniFlashDuration): void;
}

const STORAGE_KEY = "flowboard.settings.v1";

interface PersistShape {
  imageModel?: ImageModelKey;
  videoQuality?: VideoQuality;
  videoModel?: VideoModelFamily;
  omniFlashDuration?: OmniFlashDuration;
}

function loadPersisted(): PersistShape {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function persist(state: PersistShape): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage disabled / quota — non-fatal, just lose persistence.
  }
}

const persisted = loadPersisted();

export const VALID_VIDEO_QUALITIES: VideoQuality[] = [
  "lite",
  "fast",
  "lite_relaxed",
  "omni",
];

export const VALID_IMAGE_MODELS: ImageModelKey[] = [
  "NANO_BANANA_PRO",
  "NANO_BANANA_2",
  "NANO_BANANA_2_LITE",
];

export const useSettingsStore = create<SettingsState>((set, get) => ({
  imageModel:
    persisted.imageModel && VALID_IMAGE_MODELS.includes(persisted.imageModel)
      ? persisted.imageModel
      : "NANO_BANANA_2",
  // `lite`, matching `flow_sdk.DEFAULT_VIDEO_QUALITY` and its reason: "a board
  // that never chose should not be charged the most for it". This defaulted to
  // `fast` — the priciest key on this path — and because the store always sends
  // a value the backend's own default was never reached, so every canvas
  // image-to-video dispatch from an unconfigured install went to it.
  videoQuality:
    persisted.videoQuality && VALID_VIDEO_QUALITIES.includes(persisted.videoQuality)
      ? persisted.videoQuality
      : "lite",
  videoModel: persisted.videoModel ?? "veo",
  omniFlashDuration: persisted.omniFlashDuration ?? 4,
  setImageModel(model) {
    set({ imageModel: model });
    persist({
      imageModel: model,
      videoQuality: get().videoQuality,
      videoModel: get().videoModel,
      omniFlashDuration: get().omniFlashDuration,
    });
  },
  setVideoQuality(q) {
    set({ videoQuality: q });
    persist({
      imageModel: get().imageModel,
      videoQuality: q,
      videoModel: get().videoModel,
      omniFlashDuration: get().omniFlashDuration,
    });
  },
  setVideoModel(m) {
    set({ videoModel: m });
    persist({
      imageModel: get().imageModel,
      videoQuality: get().videoQuality,
      videoModel: m,
      omniFlashDuration: get().omniFlashDuration,
    });
  },
  setOmniFlashDuration(d) {
    set({ omniFlashDuration: d });
    persist({
      imageModel: get().imageModel,
      videoQuality: get().videoQuality,
      videoModel: get().videoModel,
      omniFlashDuration: d,
    });
  },
}));
