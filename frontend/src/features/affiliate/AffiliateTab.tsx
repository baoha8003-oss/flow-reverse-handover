import { useEffect, useState } from "react";
import { useTabState } from "@/lib/useTabState";
import {
  Card,
  GhostButton,
  LabeledSelect,
  PrimaryButton,
  StopButton,
} from "@/ui/primitives";
import { MediaPicker } from "@/ui/MediaPicker";
import { JobList } from "@/features/jobs/JobList";
import { toParams } from "@/lib/generationSpec";
import { ModelsBanner } from "@/components/ModelsBanner";
import { useJobsStore } from "@/store/jobs";
import {
  defaultValue,
  toSelectOptions,
  useImageOptions,
  useLane,
  useModelsReady,
  useModelsStore,
} from "@/store/models";
import {
  fashionPrompt,
  fashionPrompts,
  mediaUrl,
  readProduct,
  uploadImageFromUrl,
  type LibraryPromptEntry,
  type ProductInfo,
} from "@/api/client";

/** Affiliate thời trang — thử đồ ảo rồi dựng video.
 *
 * This mirrors the packaged tool's `AffiliateVTONWorkflow`, which is a fashion
 * flow, not a generic product-ad one: it combines a KOL/model photo with a
 * garment photo ("Kết hợp KOL + trang phục") and the OUTPUT IS A VIDEO, made
 * by feeding the try-on still into start-image video generation.
 *
 * Shopee links are read through the user's own signed-in session — the same
 * `/api/v4/item/get` call the packaged tool makes. Scraping the public page
 * returns nothing because Shopee renders the listing in the browser, which is
 * why a plain link reader comes back empty there and works elsewhere.
 */

const IMAGE_TYPE = "gen_image";
const VIDEO_TYPE = "gen_video";
/** Own ui keys: both request types are shared with other tabs, and a shared
 * key would make each tab list — and cancel — the other's jobs. */
const TRYON_TOOL = "affiliate-tryon";
const VIDEO_TOOL = "affiliate-video";

const SHOT_OPTIONS = [
  {
    value: "full",
    label: "Toàn thân",
    hint: "full-body shot, the whole outfit visible, standing naturally",
  },
  {
    value: "half",
    label: "Nửa người",
    hint: "waist-up shot, the garment's upper detail clearly visible",
  },
  {
    value: "walk",
    label: "Sải bước",
    hint: "mid-stride walking shot, fabric moving naturally",
  },
];

const MOTION_OPTIONS = [
  {
    value: "turn",
    label: "Xoay người khoe đồ",
    hint: "The model turns slowly to show the outfit from another angle, fabric settling naturally.",
  },
  {
    value: "walk",
    label: "Bước tới camera",
    hint: "The model walks a few steps toward the camera, garment moving with each step.",
  },
  {
    value: "pose",
    label: "Đổi dáng",
    hint: "The model shifts into a second pose, hands settling, gaze returning to camera.",
  },
];

export function AffiliateTab() {
  const enqueueRow = useJobsStore((s) => s.enqueueRow);
  const cancelAll = useJobsStore((s) => s.cancelAll);
  const ensureProject = useJobsStore((s) => s.ensureProject);
  const jobs = useJobsStore((s) => s.jobs);
  const tryonBusy = useJobsStore((s) => s.busy[TRYON_TOOL] ?? false);
  const videoBusy = useJobsStore((s) => s.busy[VIDEO_TOOL] ?? false);
  const tryonError = useJobsStore((s) => s.error[TRYON_TOOL] ?? null);
  const videoError = useJobsStore((s) => s.error[VIDEO_TOOL] ?? null);

  // The typed-in half of this tab, kept the way the packaged tool keeps
  // `affiliate_state.json`. Media ids are included because they are ids of
  // things already uploaded — losing them means uploading the same product
  // photo again — while `product` (a fetched description) is not: it is
  // re-derivable from the URL and would go stale.
  const [saved, saveTab] = useTabState("affiliate", {
    url: "",
    note: "",
    shot: "full",
    aspect: "",
    variants: 2,
    motion: "turn",
    garmentId: null as string | null,
    kolId: null as string | null,
  });
  const url = saved.url;
  const setUrl = (next: string) => saveTab({ url: next });
  const [product, setProduct] = useState<ProductInfo | null>(null);
  const garmentId = saved.garmentId;
  const setGarmentId = (next: string | null) => saveTab({ garmentId: next });
  const kolId = saved.kolId;
  const setKolId = (next: string | null) => saveTab({ kolId: next });
  const note = saved.note;
  const setNote = (next: string) => saveTab({ note: next });
  const shot = saved.shot;
  const setShot = (next: string) => saveTab({ shot: next });
  const loadModels = useModelsStore((s) => s.load);
  const modelsReady = useModelsReady();
  const { models: imageModels, aspects: imageAspects } = useImageOptions();
  const videoLane = useLane("i2v");
  // The try-on still becomes the first frame of a 9:16 clip, so a landscape
  // frame would be cropped away. Portrait and square only — the rest of the
  // list is the registry's.
  const aspects = imageAspects.filter((a) => !a.value.endsWith("LANDSCAPE"));

  const aspect = saved.aspect;
  const setAspect = (next: string) => saveTab({ aspect: next });
  const variants = saved.variants;
  const setVariants = (next: number) => saveTab({ variants: next });
  const motion = saved.motion;
  const setMotion = (next: string) => saveTab({ motion: next });
  const [startId, setStartId] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The 47 bundled fashion-posing prompts. Headings only — a body is ~5KB
  // and only the picked one is fetched.
  const [library, setLibrary] = useState<LibraryPromptEntry[]>([]);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  useEffect(() => {
    // A missing library is not an error worth showing: it greys the picker
    // and the three short motions still work.
    fashionPrompts()
      .then(setLibrary)
      .catch(() => setLibrary([]));
  }, []);

  // Seed once the registry lands; before that the select has nothing to show.
  useEffect(() => {
    if (aspect || aspects.length === 0) return;
    setAspect(defaultValue(aspects));
  }, [aspect, aspects]);

  const running = (tool: string) =>
    jobs.some(
      (j) => j.tool === tool && (j.status === "queued" || j.status === "running"),
    );

  /** Finished try-on stills, offered as the video's first frame. */
  const tryonResults = jobs
    .filter((j) => j.tool === TRYON_TOOL && j.status === "done")
    .flatMap((j) => j.mediaIds);

  async function read() {
    setReading(true);
    setError(null);
    setProduct(null);
    try {
      const info = await readProduct({ url });
      setProduct(info);
      if (info.title && !note) setNote(info.title);
      if (info.imageUrl) {
        const projectId = await ensureProject();
        if (projectId) {
          const up = await uploadImageFromUrl(info.imageUrl, projectId);
          setGarmentId(up.media_id);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đọc được link này.");
    } finally {
      setReading(false);
    }
  }

  function makeTryOn() {
    if (!kolId || !garmentId) {
      setError("Cần cả ảnh người mẫu và ảnh trang phục.");
      return;
    }
    setError(null);
    const shotHint = SHOT_OPTIONS.find((s) => s.value === shot)!.hint;
    const garment = note.trim() || "the garment in the second reference image";
    // Name each reference inside the prompt: Flow carries only the media id,
    // so without this the model cannot tell the person from the clothing.
    const prompt =
      `The first reference image is the person. The second reference image is `
      + `the garment: ${garment}. Photorealistic fashion photo of that exact `
      + `person wearing that exact garment, ${shotHint}. Keep the person's face `
      + `and the garment's real colour, pattern and cut unchanged. Clean studio `
      + `lighting, neutral background.`;
    void enqueueRow(
      IMAGE_TYPE,
      Array.from({ length: variants }, () => ({
        prompt,
        params: toParams({
          kind: "image",
          aspect,
          model: defaultValue(imageModels),
          refs: [kolId, garmentId],
        }),
      })),
      TRYON_TOOL,
    );
  }

  async function makeVideo() {
    if (!startId) return;
    setError(null);
    let prompt: string;
    if (motion.startsWith("fashion_male_")) {
      // A library prompt is a complete instruction — a locked camera, a
      // silent-output clause and a second-by-second timeline. Appending the
      // short hint's sentence would contradict clauses it already states.
      try {
        prompt = (await fashionPrompt(motion)).body;
      } catch {
        setError("Không đọc được prompt mẫu. Chọn chuyển động khác.");
        return;
      }
    } else {
      const hint = MOTION_OPTIONS.find((m) => m.value === motion)!.hint;
      prompt = `${hint} Keep the outfit and the person exactly as in the first frame.`;
    }
    void enqueueRow(
      VIDEO_TYPE,
      [
        {
          prompt,
          // Portrait is the product decision here, not a default: the
          // try-on still was framed for a 9:16 clip.
          params: toParams({
            kind: "i2v",
            aspect: "VIDEO_ASPECT_RATIO_PORTRAIT",
            quality: defaultValue(videoLane.qualities, "lite"),
            start: startId,
          }),
        },
      ],
      VIDEO_TOOL,
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <ModelsBanner />
      <Card>
        <h2 className="text-[13px] font-extrabold text-ink">
          🛍 Bước 1 — Lấy ảnh trang phục
        </h2>
        <p className="mt-1 text-[12px] text-ink-dim">
          Dán link Shopee: tool đọc qua phiên đăng nhập của bạn trong Chrome,
          đúng cách bản đóng gói làm. Sàn khác thì đọc thẻ chia sẻ của trang.
          Hoặc bỏ qua và tự chọn ảnh bên dưới.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://shopee.vn/...-i.<shop>.<item>"
            className="h-9 min-w-[300px] flex-1 rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink placeholder:text-ink-dim"
          />
          <PrimaryButton
            onClick={() => void read()}
            disabled={url.trim().length === 0}
            loading={reading}
          >
            📥 ĐỌC SẢN PHẨM
          </PrimaryButton>
        </div>
        {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
        {product && (
          <p className="mt-2 text-[12px] text-ink-mute">
            {product.siteName ? `${product.siteName} · ` : ""}
            {product.title || "(không tiêu đề)"}
            {product.price ? ` · ${product.price}` : ""}
          </p>
        )}
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="mb-2 text-[13px] font-semibold text-ink">
            👗 Ảnh trang phục
          </div>
          {garmentId ? (
            <div className="flex items-start gap-3">
              <img
                src={mediaUrl(garmentId)}
                alt=""
                className="h-28 w-28 rounded-(--radius-ctl) border border-line bg-black object-cover"
              />
              <GhostButton onClick={() => setGarmentId(null)}>Đổi ảnh</GhostButton>
            </div>
          ) : (
            <MediaPicker
              label="👗 Chọn ảnh trang phục"
              value={null}
              onChange={(id) => id && setGarmentId(id)}
            />
          )}
        </Card>

        <Card>
          <div className="mb-2 text-[13px] font-semibold text-ink">
            🧍 Ảnh người mẫu (KOL)
          </div>
          {kolId ? (
            <div className="flex items-start gap-3">
              <img
                src={mediaUrl(kolId)}
                alt=""
                className="h-28 w-28 rounded-(--radius-ctl) border border-line bg-black object-cover"
              />
              <GhostButton onClick={() => setKolId(null)}>Đổi ảnh</GhostButton>
            </div>
          ) : (
            <MediaPicker
              label="🧍 Chọn ảnh người mẫu"
              value={null}
              onChange={(id) => id && setKolId(id)}
            />
          )}
        </Card>
      </div>

      <Card>
        <h2 className="text-[13px] font-extrabold text-ink">
          ✨ Bước 2 — Ghép người mẫu mặc trang phục
        </h2>
        <div className="mt-2">
          <span className="text-[12px] text-ink-mute">Mô tả trang phục</span>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Ví dụ: áo thun cotton form rộng màu kem"
            className="mt-1 h-9 w-full rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink placeholder:text-ink-dim"
          />
          <p className="mt-1 text-[11px] text-ink-dim">
            Flow chỉ mang mã ảnh chứ không mang tên, nên câu này là cách duy nhất
            model biết đâu là người và đâu là đồ.
          </p>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <LabeledSelect
            label="📷 Kiểu chụp"
            value={shot}
            onChange={setShot}
            options={SHOT_OPTIONS.map((s) => ({ value: s.value, label: s.label }))}
            className="min-w-[170px]"
          />
          <LabeledSelect
            label="📐 Tỉ lệ"
            value={aspect}
            onChange={setAspect}
            options={toSelectOptions(aspects)}
          />
          <label className="flex flex-col gap-1">
            <span className="text-[12px] text-ink-mute">Số ảnh</span>
            <input
              type="number"
              min={1}
              max={4}
              value={variants}
              onChange={(e) =>
                setVariants(Math.max(1, Math.min(4, Number(e.target.value) || 1)))
              }
              className="h-9 w-[90px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
            />
          </label>
          <div className="ml-auto flex items-end gap-2">
            <PrimaryButton
              onClick={makeTryOn}
              disabled={!kolId || !garmentId || !modelsReady}
              loading={tryonBusy}
            >
              THỬ ĐỒ ẢO
            </PrimaryButton>
            <StopButton
              onClick={() => void cancelAll(TRYON_TOOL)}
              disabled={!running(TRYON_TOOL)}
            >
              DỪNG
            </StopButton>
          </div>
        </div>
        {tryonError && (
          <p className="mt-2 text-[12px] text-danger">{tryonError}</p>
        )}
      </Card>

      <JobList tool={TRYON_TOOL} kind="image" />

      <Card>
        <h2 className="text-[13px] font-extrabold text-ink">
          🎬 Bước 3 — Dựng video từ ảnh thử đồ
        </h2>
        {tryonResults.length === 0 ? (
          <p className="mt-1 text-[12px] text-ink-dim">
            Làm xong bước 2 trước — ảnh thử đồ sẽ hiện ở đây để chọn làm khung
            hình đầu.
          </p>
        ) : (
          <>
            <p className="mt-1 text-[12px] text-ink-dim">
              Chọn một ảnh làm khung đầu. Video 8 giây, CÓ tính credit.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {tryonResults.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setStartId(id === startId ? null : id)}
                  className={`overflow-hidden rounded-(--radius-ctl) border ${
                    startId === id ? "border-primary" : "border-line"
                  }`}
                >
                  <img
                    src={mediaUrl(id)}
                    alt=""
                    className="h-24 w-24 bg-black object-cover"
                  />
                </button>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <LabeledSelect
                label="🎞 Chuyển động"
                value={motion}
                onChange={setMotion}
                options={[
                  ...MOTION_OPTIONS.map((m) => ({
                    value: m.value,
                    label: m.label,
                  })),
                  // The packaged tool's own library, picked by the same ids
                  // its workflows store (`fashion_male_07`).
                  ...library.map((e) => ({
                    value: e.id,
                    label: `📖 ${e.number}. ${e.summary}`,
                  })),
                ]}
                className="min-w-[210px]"
              />
              <div className="ml-auto flex items-end gap-2">
                <PrimaryButton
                  onClick={() => void makeVideo()}
                  disabled={!startId || !modelsReady}
                  loading={videoBusy}
                >
                  TẠO VIDEO
                </PrimaryButton>
                <StopButton
                  onClick={() => void cancelAll(VIDEO_TOOL)}
                  disabled={!running(VIDEO_TOOL)}
                >
                  DỪNG
                </StopButton>
              </div>
            </div>
            {videoError && (
              <p className="mt-2 text-[12px] text-danger">{videoError}</p>
            )}
          </>
        )}
      </Card>

      <JobList tool={VIDEO_TOOL} />
    </div>
  );
}
