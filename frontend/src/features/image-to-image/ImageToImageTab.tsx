import { useEffect, useRef, useState } from "react";
import { Card, LabeledSelect, PrimaryButton, StopButton, GhostButton } from "@/ui/primitives";
import { MediaPicker } from "@/ui/MediaPicker";
import { JobList } from "@/features/jobs/JobList";
import { toParams } from "@/lib/generationSpec";
import { ModelsBanner } from "@/components/ModelsBanner";
import { useJobsStore } from "@/store/jobs";
import { useAppConfigStore } from "@/store/appConfig";
import { mediaUrl } from "@/api/client";
import { cn } from "@/lib/utils";
import {
  aspectFromConfig,
  qualityFromLabel,
  toSelectOptions,
  useImageOptions,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Image to Image — every reference image feeds every prompt.
 *
 * Three things docs/ui-spec.md is emphatic about, all of them easy to get
 * wrong:
 *
 * 1. This is NOT `edit_image`. The original calls the image generator with
 *    `imageInputs`, so the request type here is `gen_image` with
 *    `ref_media_ids`. Routing it to `edit_image` would demand a
 *    `source_media_id` the tab never collects and fail every dispatch.
 * 2. Flow's payload uses the media id as the reference's name, so the label
 *    the user types would vanish. The original works around that by naming
 *    each reference INSIDE the prompt text; skip that and the model has no
 *    way to tell the references apart. Hence `composePrompt` below.
 * 3. A name per reference is mandatory, and validation runs in the original's
 *    order — no prompt, then no image, then unnamed images — because the
 *    third message lists exactly which ones are missing a name.
 */

/** The backend handler is the same `gen_image` the Text-to-Image tab uses, so
 * the jobs need their OWN ui key — otherwise both tabs list each other's work
 * and one tab's DỪNG cancels the other's, which is the exact cross-tab bug
 * that cost a review round. */
const REQUEST_TYPE = "gen_image";
const TOOL = "image-to-image";
const MAX_REFS = 15;
const SOFT_REF_WARNING = 5;
const MAX_PROMPT_FILE_BYTES = 1_000_000;
const MAX_VARIANT_COUNT = 4;

// Aspect and model lists come from /api/models. Typed out here they were
// missing the square aspect the backend has supported all along.



interface RefImage {
  key: number;
  mediaId: string;
  name: string;
}

let refKey = 0;

/** Name the references inside the prompt, the way the original does.
 *
 * Flow only carries the media id as a reference's name, so without this
 * sentence the labels the user typed never reach the model and it blends the
 * references together. */
export function composePrompt(prompt: string, refs: RefImage[]): string {
  if (refs.length === 0) return prompt;
  const intro = refs
    .map((r) => `${r.name.trim()} là nhân vật/ảnh trong ảnh tham chiếu có mediaId là ${r.mediaId}.`)
    .join(" ");
  return `${intro} ${prompt}`;
}

export function ImageToImageTab() {
  const loadConfig = useAppConfigStore((s) => s.load);
  const configLoaded = useAppConfigStore((s) => s.loaded);
  const configValues = useAppConfigStore((s) => s.values);
  const enqueueRow = useJobsStore((s) => s.enqueueRow);
  const cancelAll = useJobsStore((s) => s.cancelAll);
  const rehydrate = useJobsStore((s) => s.rehydrate);
  const projectName = useJobsStore((s) => s.projectName);
  const busy = useJobsStore((s) => s.busy[TOOL] ?? false);
  const storeError = useJobsStore((s) => s.error[TOOL] ?? null);
  const anyRunning = useJobsStore((s) =>
    s.jobs.some(
      (j) => j.tool === TOOL && (j.status === "queued" || j.status === "running"),
    ),
  );

  const loadModels = useModelsStore((s) => s.load);
  const modelsReady = useModelsReady();
  const { models: imageModels, aspects: imageAspects } = useImageOptions();

  const [aspect, setAspect] = useState("");
  const [model, setModel] = useState("");
  const [variantCount, setVariantCount] = useState(1);
  const [prompts, setPrompts] = useState("");
  const [refs, setRefs] = useState<RefImage[]>([]);
  const [adding, setAdding] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const seeded = useRef(false);

  useEffect(() => {
    void loadConfig();
    void loadModels();
    void rehydrate();
  }, [loadConfig, loadModels, rehydrate]);

  useEffect(() => {
    if (seeded.current || !canSeed(configLoaded, imageModels)) return;
    seeded.current = true;
    // VIDEO_ASPECT_RATIO is the one orientation default in settings; it is
    // stored as "9:16" / "16:9" and applies to both media kinds.
    setAspect(aspectFromConfig(configValues["VIDEO_ASPECT_RATIO"], imageAspects));
    setModel(qualityFromLabel(configValues["CREATE_IMAGE_MODEL"], imageModels));
    const n = Number(configValues["OUTPUT_COUNT"]);
    if (Number.isInteger(n) && n >= 1) {
      setVariantCount(Math.min(n, MAX_VARIANT_COUNT));
    }
  }, [configLoaded, configValues, imageAspects, imageModels]);

  const promptLines = prompts.split("\n").filter((l) => l.trim().length > 0);
  const unnamed = refs.filter((r) => !r.name.trim());

  function onGenerate() {
    // Validation order mirrors the original's, because the messages differ.
    if (promptLines.length === 0) {
      setValidation("Hãy nhập ít nhất một prompt.");
      return;
    }
    if (refs.length === 0) {
      setValidation(
        "Hãy chọn ảnh tham chiếu và điền tên nhân vật cho từng ảnh.",
      );
      return;
    }
    if (unnamed.length > 0) {
      setValidation(
        `Các ảnh tham chiếu sau chưa được đặt tên: ${unnamed
          .map((_, i) => `#${refs.indexOf(unnamed[i]) + 1}`)
          .join(", ")}`,
      );
      return;
    }
    setValidation(null);

    const refIds = refs.map((r) => r.mediaId);
    void enqueueRow(
      REQUEST_TYPE,
      promptLines.map((line) => ({
        prompt: composePrompt(line.trim(), refs),
        params: toParams({
          kind: "image",
          aspect,
          model,
          variants: variantCount,
          refs: refIds,
        }),
      })),
      TOOL,
    );
  }

  function onImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImportError(null);
    if (file.size > MAX_PROMPT_FILE_BYTES) {
      setImportError("File quá lớn (tối đa 1 MB).");
      return;
    }
    const reader = new FileReader();
    reader.onload = () =>
      setPrompts((prev) => (prev ? prev + "\n" : "") + String(reader.result || ""));
    reader.onerror = () => setImportError("Không đọc được file prompt.");
    reader.readAsText(file);
  }

  return (
    <div className="flex flex-col gap-3">
      <ModelsBanner />
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <LabeledSelect
            label="Chọn dự án"
            value={projectName}
            onChange={() => undefined}
            options={[{ value: projectName, label: `📁 ${projectName}` }]}
            disabled
            className="min-w-[180px]"
          />
          <div className="ml-auto flex items-end gap-2">
            <PrimaryButton onClick={onGenerate} disabled={promptLines.length === 0 || !modelsReady} loading={busy}>
              {anyRunning && promptLines.length > 0
                ? `THÊM ${promptLines.length} VÀO HÀNG CHỜ`
                : "TẠO ẢNH"}
            </PrimaryButton>
            <StopButton onClick={() => void cancelAll(TOOL)} disabled={!anyRunning}>
              {anyRunning ? "DỪNG ĐANG CHẠY" : "DỪNG"}
            </StopButton>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          <LabeledSelect label="📐 Tỉ lệ" value={aspect} onChange={setAspect} options={toSelectOptions(imageAspects)} />
          <LabeledSelect label="🎨 Model" value={model} onChange={setModel} options={toSelectOptions(imageModels)} className="min-w-[200px]" />
        </div>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="mb-1 text-[13px] font-semibold text-ink">
            ✏️  Prompt tạo ảnh từ ảnh tham chiếu
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-mute">
            <span>
              💡 Mỗi dòng là một prompt. Toàn bộ ảnh bên phải sẽ được dùng làm
              tham chiếu cho mỗi prompt.
            </span>
            <GhostButton onClick={() => fileRef.current?.click()}>
              📄 Import prompt.txt
            </GhostButton>
            <input ref={fileRef} type="file" accept=".txt" hidden onChange={onImport} />
            <span className="ml-auto text-ink-dim">{promptLines.length} prompt</span>
          </div>
          <textarea
            value={prompts}
            onChange={(e) => setPrompts(e.target.value)}
            placeholder="Nhập một prompt cho mỗi dòng..."
            className="h-56 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[13px] text-ink placeholder:text-ink-dim"
          />
        </Card>

        <Card>
          <div className="mb-1 flex items-center gap-2 text-[13px] font-semibold text-ink">
            📸 Ảnh tham chiếu
            <span className="ml-auto text-[11px] font-normal text-ink-dim">
              {refs.length}/{MAX_REFS} · tên là bắt buộc
            </span>
          </div>

          <div className="flex flex-col gap-2">
            {refs.map((r, i) => (
              <div
                key={r.key}
                className={cn(
                  "flex items-center gap-2 rounded-(--radius-card) border p-2",
                  r.name.trim() ? "border-line" : "border-danger",
                )}
              >
                <img
                  src={mediaUrl(r.mediaId)}
                  alt=""
                  className="h-14 w-14 shrink-0 rounded-md border border-line bg-black object-cover"
                />
                <input
                  value={r.name}
                  onChange={(e) =>
                    setRefs((prev) =>
                      prev.map((x) =>
                        x.key === r.key ? { ...x, name: e.target.value } : x,
                      ),
                    )
                  }
                  placeholder={`Tên nhân vật/ảnh #${i + 1}`}
                  className="h-9 min-w-0 flex-1 rounded-(--radius-ctl) border border-line bg-surface px-2 text-[13px] text-ink placeholder:text-ink-dim"
                />
                <button
                  type="button"
                  onClick={() => setRefs((prev) => prev.filter((x) => x.key !== r.key))}
                  className="px-1 text-[13px] text-danger"
                  aria-label={`Xóa ảnh tham chiếu ${i + 1}`}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>

          {refs.length >= MAX_REFS ? (
            <div className="mt-2 text-[12px] text-warn">
              Đã đủ số lượng tối đa ({MAX_REFS} ảnh).
            </div>
          ) : adding ? (
            <MediaPicker
              className="mt-2"
              label="🖼 Thêm ảnh tham chiếu"
              value={null}
              onChange={(mediaId) => {
                if (mediaId) {
                  setRefs((prev) => [...prev, { key: ++refKey, mediaId, name: "" }]);
                  setAdding(false);
                }
              }}
            />
          ) : (
            <GhostButton onClick={() => setAdding(true)}>
              📂 Chọn ảnh tham chiếu
            </GhostButton>
          )}

          {refs.length > SOFT_REF_WARNING && (
            <div className="mt-2 text-[12px] text-warn">
              ⚠ {refs.length} ảnh tham chiếu cho mỗi prompt thường làm loãng kết
              quả — cân nhắc giảm bớt.
            </div>
          )}
        </Card>
      </div>

      {validation && <div className="text-[12px] text-danger">{validation}</div>}
      {importError && <div className="text-[12px] text-danger">{importError}</div>}
      {storeError && <div className="text-[12px] text-danger">{storeError}</div>}

      <JobList tool={TOOL} kind="image" />
    </div>
  );
}
