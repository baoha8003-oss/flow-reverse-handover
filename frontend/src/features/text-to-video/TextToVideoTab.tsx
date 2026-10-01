import { useEffect, useRef, useState } from "react";
import { Card, LabeledSelect, PrimaryButton, StopButton, GhostButton } from "@/ui/primitives";
import { JobList } from "@/features/jobs/JobList";
import { toParams } from "@/lib/generationSpec";
import { ModelsBanner } from "@/components/ModelsBanner";
import { useJobsStore } from "@/store/jobs";
import { useAppConfigStore } from "@/store/appConfig";
import { parseBatchSheet } from "@/api/client";
import {
  aspectFromConfig,
  noteFor,
  qualityFromLabel,
  resolutionFromConfig,
  toSelectOptions,
  useLane,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Text to Video — the screenshot's default tab.
 *
 * One prompt per line → one video job each, dispatched through the concurrent
 * worker. Params (aspect / duration / model) seed from /api/settings. Labels
 * are byte-exact from the packaged app (see docs/ui-spec.md). */

const TOOL = "gen_video_text";
const MAX_PROMPT_FILE_BYTES = 1_000_000;

// Options come from /api/models, filtered by this account's plan. They used
// to be four hardcoded lists here — and in eight other files, which had
// drifted apart from each other and from the backend.

export function TextToVideoTab() {
  // Select individual store slices, NOT the whole store: a whole-store hook
  // returns a new object on every update, which would make the load effect
  // below (keyed on it) re-run and re-fetch settings in a loop. Actions and
  // primitive fields are stable references.
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
  const lane = useLane("t2v");

  const [aspect, setAspect] = useState("");
  const [duration, setDuration] = useState("");
  const [quality, setQuality] = useState("");
  const [prompts, setPrompts] = useState("");
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const xlsxRef = useRef<HTMLInputElement>(null);
  const seeded = useRef(false);

  useEffect(() => {
    void loadConfig();
    void loadModels();
    void rehydrate();
  }, [loadConfig, loadModels, rehydrate]);

  // Seed once, after BOTH the settings and the model registry have landed —
  // seeding from config alone could select a lane this plan does not offer.
  useEffect(() => {
    if (seeded.current || !canSeed(configLoaded, lane.qualities)) return;
    seeded.current = true;
    setAspect(aspectFromConfig(configValues["VIDEO_ASPECT_RATIO"], lane.aspects));
    setQuality(qualityFromLabel(configValues["VEO_MODEL"], lane.qualities));
    // Honour the configured duration when this plan actually has it. It used
    // to be pinned to 8s here while Settings offered 4–10, so the number the
    // user chose was silently discarded.
    const wanted = Number(configValues["VIDEO_DURATION_SECONDS"]);
    const usable = lane.durations.some((d) => d.value === wanted);
    setDuration(
      String(usable ? wanted : (lane.durations[0]?.value ?? 8)),
    );
  }, [configLoaded, configValues, lane]);

  const promptLines = prompts.split("\n").filter((l) => l.trim().length > 0);
  const laneNote = noteFor(lane.qualities, quality);

  function onGenerate() {
    void enqueue(
      TOOL,
      promptLines,
      toParams({
        kind: "t2v",
        aspect,
        quality,
        durationS: Number(duration),
        resolution: resolutionFromConfig(configValues["VIDEO_RESOLUTION"]),
      }),
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
    reader.onload = () => {
      setPrompts((prev) => (prev ? prev + "\n" : "") + String(reader.result || ""));
    };
    reader.onerror = () => setImportError("Không đọc được file prompt.");
    reader.readAsText(file);
  }

  /** Import an .xlsx of prompts.
   *
   * Only the prompt column is pulled into the box; per-row aspect/model would
   * have to override the controls above on a row-by-row basis, and silently
   * ignoring the controls the user can see is worse than not honouring the
   * columns. Rows land as text so they stay editable before dispatch. */
  async function onImportSheet(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImportError(null);
    try {
      const { rows, skipped } = await parseBatchSheet(file);
      const text = rows.map((r) => r.prompt.replace(/\s*\n\s*/g, " ")).join("\n");
      setPrompts((prev) => (prev ? prev + "\n" : "") + text);
      if (skipped > 0) {
        setImportError(`Đã nhập ${rows.length} dòng, bỏ qua ${skipped} dòng trống.`);
      }
    } catch (err) {
      setImportError(
        err instanceof Error ? err.message : "Không đọc được file Excel.",
      );
    }
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
                : "TẠO VIDEO"}
            </PrimaryButton>
            <StopButton onClick={() => void cancelAll(TOOL)} disabled={!anyRunning}>
              {anyRunning ? "DỪNG ĐANG CHẠY" : "DỪNG"}
            </StopButton>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          <LabeledSelect
            label="📏 Tỷ lệ"
            value={aspect}
            onChange={setAspect}
            options={toSelectOptions(lane.aspects)}
          />
          <LabeledSelect
            label="⏱ Thời lượng"
            value={duration}
            onChange={setDuration}
            options={lane.durations.map((d) => ({
              value: String(d.value),
              label: d.label,
            }))}
            // Only one length on this plan — a select with a single entry is
            // a label, not a choice.
            disabled={lane.durations.length <= 1}
          />
          <LabeledSelect
            label="🎬 Model"
            value={quality}
            onChange={setQuality}
            options={toSelectOptions(lane.qualities)}
            className="min-w-[240px]"
          />
        </div>
        {laneNote && (
          <div className="mt-2 text-[12px] text-warn">⚠ {laneNote}</div>
        )}
      </Card>

      <Card>
        <div className="mb-1 text-[13px] font-semibold text-ink">
          ✏️  Nhập prompt (mỗi dòng là 1 prompt)
        </div>
        <div className="mb-2 flex items-center gap-2 text-[12px] text-ink-mute">
          <span>💡 Mỗi dòng = 1 video. Dòng trống sẽ bị bỏ qua.</span>
          <GhostButton onClick={() => fileRef.current?.click()}>📄 Import prompt.txt</GhostButton>
          <input ref={fileRef} type="file" accept=".txt" hidden onChange={onImport} />
          <GhostButton onClick={() => xlsxRef.current?.click()}>📊 Import Excel</GhostButton>
          <input
            ref={xlsxRef}
            type="file"
            accept=".xlsx"
            hidden
            onChange={onImportSheet}
          />
          <span className="ml-auto text-ink-dim">{promptLines.length} prompt</span>
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

      <JobList tool={TOOL} kind="video" />
    </div>
  );
}
