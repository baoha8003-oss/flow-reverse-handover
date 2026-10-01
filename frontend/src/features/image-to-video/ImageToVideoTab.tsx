import { useEffect, useMemo, useRef, useState } from "react";
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
  noteFor,
  qualityFromLabel,
  resolutionFromConfig,
  toSelectOptions,
  useLane,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Image to Video — one row per clip: a source image plus its own prompt.
 *
 * Rows are explicit objects rather than two parallel lists. The original
 * pairs a list of images with the lines of a textarea BY POSITION, and
 * docs/ui-spec.md calls that its number-one source of silent errors: five
 * images against four prompts shifts the whole batch by one, runs anyway,
 * and bills for it. Here a row owns both halves, so they cannot drift, and
 * an incomplete row blocks dispatch by name.
 *
 * Two controls from the original are deliberately absent: "🧩 Chạy từ các
 * thành phần" and "🎙️ Đồng Bộ Giọng Nói" have no backend path in this build
 * (gen_video takes no voice_id), and a control the dispatch ignores is worse
 * than no control. */

const TOOL = "gen_video";
const OMNI_TOOL = "gen_video_omni";
const MAX_PROMPT_FILE_BYTES = 1_000_000;

// Model, aspect and duration lists come from /api/models. This tab spans two
// lanes — Veo image-to-video and OMNI Flash — so it concatenates them rather
// than keeping a fifth entry of its own.

interface Row {
  key: number;
  mediaId: string | null;
  prompt: string;
}

let rowKey = 0;
const newRow = (): Row => ({ key: ++rowKey, mediaId: null, prompt: "" });

export function ImageToVideoTab() {
  const loadConfig = useAppConfigStore((s) => s.load);
  const configLoaded = useAppConfigStore((s) => s.loaded);
  const configValues = useAppConfigStore((s) => s.values);
  const enqueueRow = useJobsStore((s) => s.enqueueRow);
  const cancelAll = useJobsStore((s) => s.cancelAll);
  const rehydrate = useJobsStore((s) => s.rehydrate);
  const projectName = useJobsStore((s) => s.projectName);
  const busy = useJobsStore((s) => s.busy[TOOL] ?? false);
  const error = useJobsStore((s) => s.error[TOOL] ?? null);
  const anyRunning = useJobsStore((s) =>
    s.jobs.some(
      (j) =>
        (j.tool === TOOL || j.tool === OMNI_TOOL) &&
        (j.status === "queued" || j.status === "running"),
    ),
  );

  const loadModels = useModelsStore((s) => s.load);
  const modelsReady = useModelsReady();
  const veo = useLane("i2v");
  const omni = useLane("omni");

  const [aspect, setAspect] = useState("");
  const [model, setModel] = useState("");
  const [duration, setDuration] = useState("");
  const [rows, setRows] = useState<Row[]>([newRow()]);
  const modelOptions = useMemo(
    () => [...veo.qualities, ...omni.qualities],
    [veo.qualities, omni.qualities],
  );
  const [pickerFor, setPickerFor] = useState<number | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const seeded = useRef(false);

  useEffect(() => {
    void loadConfig();
    void loadModels();
    void rehydrate();
  }, [loadConfig, loadModels, rehydrate]);

  useEffect(() => {
    if (seeded.current || !canSeed(configLoaded, modelOptions)) return;
    seeded.current = true;
    setAspect(aspectFromConfig(configValues["VIDEO_ASPECT_RATIO"], veo.aspects));
    setModel(qualityFromLabel(configValues["VEO_MODEL"], modelOptions));
    const wanted = Number(configValues["VIDEO_DURATION_SECONDS"]);
    const usable = omni.durations.some((d) => d.value === wanted);
    setDuration(String(usable ? wanted : (omni.durations[0]?.value ?? 8)));
  }, [configLoaded, configValues, veo, omni, modelOptions]);

  const isOmni = model === "omni";
  const laneNote = noteFor(modelOptions, model);
  // OMNI is the only lane with a length dial: a Veo clip takes its duration
  // from the model key, and the i2v table has no 4s/6s key.
  const durations = isOmni ? omni.durations : veo.durations;

  // A row counts only when it has BOTH halves; naming the incomplete ones is
  // what stops a shifted batch from being dispatched and billed.
  const filled = rows.filter((r) => r.mediaId && r.prompt.trim().length > 0);
  const incomplete = rows
    .map((r, i) => ({ r, n: i + 1 }))
    .filter(({ r }) => (r.mediaId || r.prompt.trim()) && !(r.mediaId && r.prompt.trim()));

  function patchRow(key: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function onGenerate() {
    const items = filled.map((r) => ({
      prompt: r.prompt.trim(),
      params: toParams(
        isOmni
          ? {
              kind: "omni",
              aspect,
              durationS: Number(duration),
              // Part of the Omni model key. Taken from Settings rather than
              // defaulted here: a second copy of the default is how the price
              // quoted and the render produced come to disagree.
              resolution: resolutionFromConfig(configValues["VIDEO_RESOLUTION"]),
              refs: [r.mediaId as string],
            }
          : {
              kind: "i2v",
              aspect,
              quality: model,
              start: r.mediaId as string,
            },
      ),
    }));
    void enqueueRow(isOmni ? OMNI_TOOL : TOOL, items, TOOL);
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
      const lines = String(reader.result || "")
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      // Fill empty prompts in order, then append rows for the remainder —
      // never overwrite a prompt the user already typed.
      setRows((prev) => {
        const next = prev.map((r) => ({ ...r }));
        let li = 0;
        for (const r of next) {
          if (li >= lines.length) break;
          if (!r.prompt.trim()) r.prompt = lines[li++];
        }
        while (li < lines.length) next.push({ ...newRow(), prompt: lines[li++] });
        return next;
      });
    };
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
            <PrimaryButton
              onClick={onGenerate}
              disabled={filled.length === 0 || incomplete.length > 0 || !modelsReady}
              loading={busy}
            >
              {anyRunning && filled.length > 0
                ? `THÊM ${filled.length} VÀO HÀNG CHỜ`
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
            options={toSelectOptions(isOmni ? omni.aspects : veo.aspects)}
          />
          <LabeledSelect
            label="⏱ Thời lượng"
            value={isOmni ? duration : String(veo.durations[0]?.value ?? 8)}
            onChange={setDuration}
            options={durations.map((d) => ({
              value: String(d.value),
              label: d.credits ? `${d.label} · ${d.credits} credit` : d.label,
            }))}
            disabled={durations.length <= 1}
          />
          <LabeledSelect label="🎬 Model" value={model} onChange={setModel} options={toSelectOptions(modelOptions)} className="min-w-[240px]" />
        </div>
        {!isOmni && (
          <div className="mt-2 text-[11px] text-ink-dim">
            Veo mã hoá thời lượng trong tên model và bản local chưa có key 4s/6s
            cho ảnh→video, nên clip luôn 8s. Chọn OMNI Flash nếu cần đổi thời lượng.
          </div>
        )}
        {laneNote && <div className="mt-2 text-[12px] text-warn">⚠ {laneNote}</div>}
      </Card>

      <Card>
        <div className="mb-1 text-[13px] font-semibold text-ink">
          📁  Bước 1: Chọn ảnh · ✏️  Bước 2: Nhập prompt tương ứng
        </div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-mute">
          <span>💡 Mỗi dòng = 1 video (ảnh + prompt của chính dòng đó).</span>
          <GhostButton onClick={() => setRows((r) => [...r, newRow()])}>
            ➕ Thêm dòng
          </GhostButton>
          <GhostButton onClick={() => fileRef.current?.click()}>
            📄 Import prompt.txt
          </GhostButton>
          <input ref={fileRef} type="file" accept=".txt" hidden onChange={onImport} />
          <GhostButton
            onClick={() => {
              setRows([newRow()]);
              setPickerFor(null);
            }}
          >
            🗑 Xóa hết
          </GhostButton>
          <span className="ml-auto text-ink-dim">{filled.length} dòng sẵn sàng</span>
        </div>

        <div className="flex flex-col gap-2">
          {rows.map((row, i) => {
            const missing =
              (row.mediaId || row.prompt.trim()) &&
              !(row.mediaId && row.prompt.trim());
            return (
              <div
                key={row.key}
                className={cn(
                  "rounded-(--radius-card) border p-2",
                  missing ? "border-danger" : "border-line",
                )}
              >
                <div className="flex items-start gap-2">
                  <span className="w-6 shrink-0 pt-2 text-center text-[12px] text-ink-dim">
                    {i + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setPickerFor(pickerFor === row.key ? null : row.key)
                    }
                    className="h-20 w-20 shrink-0 overflow-hidden rounded-md border border-line bg-black text-[11px] text-ink-dim"
                  >
                    {row.mediaId ? (
                      <img
                        src={mediaUrl(row.mediaId)}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      "(Click chọn)"
                    )}
                  </button>
                  <textarea
                    value={row.prompt}
                    onChange={(e) => patchRow(row.key, { prompt: e.target.value })}
                    placeholder="Prompt cho ảnh này..."
                    className="h-20 min-w-0 flex-1 resize-y rounded-(--radius-ctl) border border-line bg-card p-2 text-[13px] text-ink placeholder:text-ink-dim"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setRows((prev) =>
                        prev.length === 1
                          ? [newRow()]
                          : prev.filter((r) => r.key !== row.key),
                      );
                      if (pickerFor === row.key) setPickerFor(null);
                    }}
                    className="shrink-0 px-1 pt-2 text-[13px] text-danger"
                    aria-label={`Xóa dòng ${i + 1}`}
                  >
                    🗑
                  </button>
                </div>
                {pickerFor === row.key && (
                  <MediaPicker
                    className="mt-2"
                    label={`🖼 Ảnh cho dòng ${i + 1}`}
                    value={row.mediaId}
                    onChange={(mediaId) => {
                      patchRow(row.key, { mediaId });
                      if (mediaId) setPickerFor(null);
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>

        {incomplete.length > 0 && (
          <div className="mt-2 text-[12px] text-danger">
            Dòng thiếu: {incomplete.map(({ n }) => n).join(", ")} — mỗi dòng cần
            cả ảnh lẫn prompt.
          </div>
        )}
        {importError && <div className="mt-2 text-[12px] text-danger">{importError}</div>}
        {error && <div className="mt-2 text-[12px] text-danger">{error}</div>}
      </Card>

      <JobList tool={TOOL} kind="video" />
    </div>
  );
}
