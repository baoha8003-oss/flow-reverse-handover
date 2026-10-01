import { useEffect, useRef, useState } from "react";
import { useGenerationStore } from "../store/generation";
import {
  useSettingsStore,
  VALID_IMAGE_MODELS,
  VALID_VIDEO_QUALITIES,
  type ImageModelKey,
  type VideoQuality,
} from "../store/settings";
import { getLatestRelease, isNewerVersion, type LatestRelease } from "../api/github";
import packageJson from "../../package.json";

const APP_VERSION: string = packageJson.version;
const COMMUNITY_URL = "https://www.facebook.com/groups/flowkit.flowboard.community";

/**
 * Dashboard Settings popover anchored to the AccountPanel gear button.
 *
 * Surfaces the model context that drives every generation:
 *   - Paygate tier — chosen in Settings, and a LABEL only. The batch transport
 *     sends no tier, so entitlement is Google's answer at dispatch time.
 *   - Video lane — the lanes this transport has a key for, plus OMNI. Written
 *     to the localStorage settings store, which `dispatchGeneration` reads for
 *     every canvas dispatch, so this is what gets billed.
 *   - Image model — persisted the same way; every gen_image / edit_image
 *     dispatch reads it.
 *
 * `quality` used to be offered here and has no key in `BATCH_VIDEO_LANES`, so
 * picking it could only ever be refused. `omni` was missing while being the only
 * family that serves text-to-video, first→last and references. Both lists were
 * typed out by hand beside a registry that already knew better, which is how they
 * drifted; the lane keys now come from the store's own closed list, so a lane
 * added there without a label here fails to compile.
 */

const IMAGE_MODEL_LABELS: Record<ImageModelKey, { label: string; hint: string }> = {
  NANO_BANANA_PRO: {
    label: "Nano Banana Pro",
    hint: "GEM_PIX_2 — premium, higher fidelity, slightly slower",
  },
  NANO_BANANA_2: {
    label: "Nano Banana 2",
    hint: "NARWHAL — faster, lighter checkpoint",
  },
  NANO_BANANA_2_LITE: {
    label: "Nano Banana 2 Lite",
    hint: "HARBOR_SEAL — lightest checkpoint",
  },
};

const IMAGE_MODELS: { key: ImageModelKey; label: string; hint: string }[] =
  VALID_IMAGE_MODELS.map((key) => ({ key, ...IMAGE_MODEL_LABELS[key] }));

const VIDEO_LANE_LABELS: Record<VideoQuality, { label: string; hint: string }> = {
  lite: {
    label: "Veo 3.1 Lite",
    hint: "Mặc định. Nhẹ và nhanh nhất trong họ Veo. Áp cho cả 16:9 và 9:16.",
  },
  fast: {
    label: "Veo 3.1 Fast",
    hint: "Key đắt nhất trên đường này. Tên nó mang 'Ultra' — gói Pro có gửi được hay không thì chưa đo.",
  },
  lite_relaxed: {
    label: "Veo 3.1 Lite (Low Priority)",
    hint: "Cùng checkpoint Lite, hàng đợi ưu tiên thấp. Gói Pro đo được là KHÔNG có làn này — Flow trả MODEL_ACCESS_DENIED, dispatch bị từ chối (không tốn credit).",
  },
  omni: {
    label: "OMNI Flash",
    hint: "Họ DUY NHẤT chạy được text-to-video, ảnh đầu→cuối và cổng nhân vật trên đường mới. CÓ tính credit theo thời lượng.",
  },
};

const VIDEO_QUALITIES: { key: VideoQuality; label: string; hint: string }[] =
  VALID_VIDEO_QUALITIES.map((key) => ({ key, ...VIDEO_LANE_LABELS[key] }));

interface SettingsPanelProps {
  open: boolean;
  onClose(): void;
  // Provided by AccountPanel. Called when the user clicks "Sign out"
  // — AccountPanel owns the post-logout state reset (clear cached
  // profile, kick the /me poll). Pass undefined when no identity is
  // loaded (the button auto-hides in that case).
  onLogout?: () => Promise<void> | void;
  // True while the parent's logout call is in flight — disables the
  // button so a double-click doesn't fire two POSTs.
  logoutPending?: boolean;
}

export function SettingsPanel({ open, onClose, onLogout, logoutPending }: SettingsPanelProps) {
  const tier = useGenerationStore((s) => s.paygateTier);
  const imageModel = useSettingsStore((s) => s.imageModel);
  const setImageModel = useSettingsStore((s) => s.setImageModel);
  const videoQuality = useSettingsStore((s) => s.videoQuality);
  const setVideoQuality = useSettingsStore((s) => s.setVideoQuality);
  const videoModel = useSettingsStore((s) => s.videoModel);
  const setVideoModel = useSettingsStore((s) => s.setVideoModel);

  const panelRef = useRef<HTMLDivElement>(null);

  // Esc closes (click-outside is handled by the backdrop's onClick).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Check GitHub for a newer release. Cached in sessionStorage by
  // the helper, so re-opening the dialog doesn't burn API quota.
  const [latestRelease, setLatestRelease] = useState<LatestRelease | null>(null);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    getLatestRelease().then((r) => {
      if (alive) setLatestRelease(r);
    });
    return () => {
      alive = false;
    };
  }, [open]);
  const updateAvailable =
    !!latestRelease?.tagName &&
    isNewerVersion(latestRelease.tagName, APP_VERSION);

  if (!open) return null;

  const tierLabel = tier === "PAYGATE_TIER_TWO"
    ? "Ultra"
    : tier === "PAYGATE_TIER_ONE"
      ? "Pro"
      : "Detecting…";

  return (
    <div
      className="settings-panel-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="settings-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
      >
        <div className="settings-panel__header">
        <span className="settings-panel__title">Settings</span>
        <button
          type="button"
          className="settings-panel__close"
          onClick={onClose}
          aria-label="Close settings"
        >
          ×
        </button>
      </div>

      <div className="settings-panel__section">
        <div className="settings-panel__label">Account tier</div>
        <div className="settings-panel__value settings-panel__value--readonly">
          {tierLabel}
        </div>
        <div className="settings-panel__hint">
          Auto-detected from Google Flow when the first project loads.
        </div>
      </div>

      {/* Single unified Video model picker — flat list of every option
          (Veo tiers + Omni Flash). Selecting a Veo row stamps both
          videoModel="veo" + the matching quality; selecting Omni Flash
          stamps videoModel="omni_flash" (duration is picked per dispatch
          in the GenerationDialog). */}
      <div className="settings-panel__section">
        <div className="settings-panel__label">Video model</div>
        <div className="settings-panel__radio-group">
          {/* No lane is locked here any more. The lock read
              `q.ultraOnly && tier !== "PAYGATE_TIER_TWO"`, with `tier` coming
              from the Settings dropdown — a self-declared label. `routes/models.py`
              records the opposite policy for the same reason: "a list narrowed by
              a tier this process only half knows would hide a lane the user
              actually has". A real Ultra user who never picked a plan — the
              documented-as-fine default — had those lanes disabled while
              `/api/models` listed them and the backend would dispatch them. What
              a lane costs, or that it is refused, is said in its hint. */}
          {VIDEO_QUALITIES.map((q) => {
            const checked = videoModel === "veo" && videoQuality === q.key;
            return (
              <label
                key={q.key}
                className={`settings-panel__radio${checked ? " settings-panel__radio--active" : ""}`}
              >
                <input
                  type="radio"
                  name="video-model"
                  value={`veo:${q.key}`}
                  checked={checked}
                  onChange={() => {
                    setVideoModel("veo");
                    setVideoQuality(q.key);
                  }}
                />
                <div>
                  <div className="settings-panel__radio-label">{q.label}</div>
                  <div className="settings-panel__radio-hint">{q.hint}</div>
                </div>
              </label>
            );
          })}
          <label
            className={`settings-panel__radio${videoModel === "omni_flash" ? " settings-panel__radio--active" : ""}`}
          >
            <input
              type="radio"
              name="video-model"
              value="omni_flash"
              checked={videoModel === "omni_flash"}
              onChange={() => setVideoModel("omni_flash")}
            />
            <div>
              <div className="settings-panel__radio-label">
                Omni Flash (r2v)
              </div>
              <div className="settings-panel__radio-hint">
                Reference-image to video. Variable duration (4 / 6 / 8 / 10s)
                picked per dispatch — 15 / 20 / 25 / 30 credits. Portrait +
                landscape supported.
              </div>
            </div>
          </label>
        </div>
      </div>

      <div className="settings-panel__section">
        <div className="settings-panel__label">Image model</div>
        <div className="settings-panel__radio-group">
          {IMAGE_MODELS.map((m) => (
            <label
              key={m.key}
              className={`settings-panel__radio${imageModel === m.key ? " settings-panel__radio--active" : ""}`}
            >
              <input
                type="radio"
                name="image-model"
                value={m.key}
                checked={imageModel === m.key}
                onChange={() => setImageModel(m.key)}
              />
              <div>
                <div className="settings-panel__radio-label">{m.label}</div>
                <div className="settings-panel__radio-hint">{m.hint}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="settings-panel__section">
        <div className="settings-panel__label">About</div>
        <div className="settings-panel__about-row">
          <span className="settings-panel__about-key">Version</span>
          <span className="settings-panel__about-value">
            <code>v{APP_VERSION}</code>
            {updateAvailable && latestRelease && (
              <a
                className="settings-panel__update-badge"
                href={latestRelease.htmlUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Latest: ${latestRelease.tagName}`}
              >
                New version {latestRelease.tagName} →
              </a>
            )}
          </span>
        </div>
        <div className="settings-panel__about-row">
          <span className="settings-panel__about-key">Community</span>
          <a
            className="settings-panel__about-link"
            href={COMMUNITY_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            FlowKit & Flowboard on Facebook →
          </a>
        </div>
      </div>

      {onLogout && (
        // Sign out lives here (not in the AccountPanel chip) so the
        // chip stays narrow enough for the email + status row to
        // render without ellipsizing on default sidebar widths.
        <div className="settings-panel__section settings-panel__section--logout">
          <button
            type="button"
            className="settings-panel__logout-btn"
            onClick={onLogout}
            disabled={logoutPending}
          >
            {logoutPending ? "Signing out…" : "Sign out from Flow account"}
          </button>
          <div className="settings-panel__hint">
            Clears the cached identity and tells the extension to drop
            its in-memory token. The WebSocket stays open so signing
            back in doesn't require a Chrome restart.
          </div>
        </div>
      )}
      </div>
    </div>
  );
}

