import { useEffect, useRef, useState } from "react";
import { Card, LabeledSelect, PrimaryButton, StopButton, GhostButton } from "@/ui/primitives";
import { JobList } from "@/features/jobs/JobList";
import { toParams } from "@/lib/generationSpec";
import { ModelsBanner } from "@/components/ModelsBanner";
import { useJobsStore } from "@/store/jobs";
import { useAppConfigStore } from "@/store/appConfig";
import {
  aspectFromConfig,
  qualityFromLabel,
  toSelectOptions,
  useImageOptions,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Text to Image — one prompt per line, each dispatched as a gen_image
 * request. Reuses the jobs store + JobList (image mode). Labels are
 * byte-exact from the packaged app (see docs/ui-spec.md).
 *
 * Two deliberate deviations from the original's control strip:
 *  - `🖼 Quality` (CREATE_IMAGE_QUALITY) is NOT rendered: the spec records it
 *    as a local post-processing step, not an API parameter, and gen_image
 *    takes no quality argument. A control that changes nothing is worse than
 *    no control.
 *  - The variant count is hidden and read from OUTPUT_COUNT, matching the
 *    original (where it is config-only). Exposing it invited 10 prompts × 4
 *    variants = 40 billed units from one click with no warning.
 * Only the two backend-verified models are offered — resolve_image_model
 * accepts NANO_BANANA_PRO and NANO_BANANA_2, so listing LITE would promise
 * a model the dispatch silently swaps for Pro (and bills as Pro). */

const TOOL = "gen_image";
const MAX_PROMPT_FILE_BYTES = 1_000_000;
const MAX_VARIANT_COUNT = 4;

// Aspect and model lists come from /api/models. Typed out here they were
// missing the square aspect the backend has supported all along.



function variantCountFromConfig(v: unknown): number {
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) return 1;
  return Math.min(n, MAX_VARIANT_COUNT);
}

export function TextToImageTab() {
  const loadConfig = useAppConfigStore((s) => s.load);
  const configLoaded = useAppConfigStore((s) => s.loaded);
  const configValues = useAppConfigStore((s) => s.values);
  const enqueue = useJobsStore((s) => s.enqueue);
  const cancelAll = useJobsStore((s) => s.cancelAll);
  const rehydrate = useJobsStore((s) => s.rehydrate);
  const projectName = useJobsStore((s) => s.projectName);
  const busy = useJobsStore((s) => s.busy[TOOL] ?? false);
  const error = useJobsStore((s) => s.error[TOOL] ?? null);
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
    setVariantCount(variantCountFromConfig(configValues["OUTPUT_COUNT"]));
  }, [configLoaded, configValues, imageAspects, imageModels]);

  const promptLines = prompts.split("\n").filter((l) => l.trim().length > 0);
  const totalImages = promptLines.length * variantCount;

  function onGenerate() {
    void enqueue(
      TOOL,
      promptLines,
      toParams({ kind: "image", aspect, model, variants: variantCount }),
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

      <Card>
        <div className="mb-1 text-[13px] font-semibold text-ink">
          ✏️  Nhập prompt (mỗi dòng là 1 prompt)
        </div>
        <div className="mb-2 flex items-center gap-2 text-[12px] text-ink-mute">
          <span>💡 Mỗi dòng = 1 ảnh. Dòng trống sẽ bị bỏ qua.</span>
          <GhostButton onClick={() => fileRef.current?.click()}>📄 Import prompt.txt</GhostButton>
          <input ref={fileRef} type="file" accept=".txt" hidden onChange={onImport} />
          <span className="ml-auto text-ink-dim">
            {promptLines.length} prompt
            {variantCount > 1 ? ` · ${totalImages} ảnh (${variantCount}/prompt)` : ""}
          </span>
        </div>
        <textarea
          value={prompts}
          onChange={(e) => setPrompts(e.target.value)}
          placeholder="Nhập một prompt cho mỗi dòng..."
          className="h-56 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[13px] text-ink placeholder:text-ink-dim"
        />
        {importError && <div className="mt-2 text-[12px] text-danger">{importError}</div>}
        {error && <div className="mt-2 text-[12px] text-danger">{error}</div>}
      </Card>

      <JobList tool={TOOL} kind="image" />
    </div>
  );
}
