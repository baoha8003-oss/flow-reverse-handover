import { useEffect, useRef, useState } from "react";
import { Card, LabeledSelect, PrimaryButton, StopButton, GhostButton } from "@/ui/primitives";
import { MediaPicker } from "@/ui/MediaPicker";
import { JobList } from "@/features/jobs/JobList";
import { ModelsBanner } from "@/components/ModelsBanner";
import { useJobsStore } from "@/store/jobs";
import { toParams } from "@/lib/generationSpec";
import { useAppConfigStore } from "@/store/appConfig";
import { mediaUrl } from "@/api/client";
import { cn } from "@/lib/utils";
import {
  aspectFromConfig,
  defaultValue,
  resolutionFromConfig,
  toSelectOptions,
  useLane,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Video Start-End — a clip that travels from a first frame to a last one.
 *
 * The tab is shaped by one fact, and the fact changed. Flow's first→last-frame
 * endpoint used to have exactly six model keys, all in Veo's `_s_fast` family:
 * no Lite, no Quality, no Lower-Priority, no 4s/6s variant. Since the September
 * 2026 transport migration none of those keys is accepted and Veo's end-image
 * payload has never been captured on the new one — the capability moved to Omni
 * (`nprQif`), which DOES take four lengths and two resolutions, and which bills
 * 15-30 credits by length.
 *
 * So the locked 8s duration became a real choice, and the model select offers
 * what the registry says rather than a literal. The reason for being strict here
 * is unchanged and now sharper: sending a Veo key to this lane would make Flow
 * drop the end frame and bill for an ordinary clip, so the backend refuses it
 * outright instead of substituting.
 */

const REQUEST_TYPE = "gen_video";
const TOOL = "video-start-end";
const MAX_PROMPT_FILE_BYTES = 1_000_000;

// Aspects, lane and lengths all come from /api/models. Only OMNI serves this
// lane now, so the registry lists it first and every Veo label it still shows
// carries the reason it will be refused.

interface Row {
  key: number;
  startId: string | null;
  endId: string | null;
  prompt: string;
}

let rowKey = 0;
const newRow = (): Row => ({ key: ++rowKey, startId: null, endId: null, prompt: "" });

export function VideoStartEndTab() {
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
      (j) => j.tool === TOOL && (j.status === "queued" || j.status === "running"),
    ),
  );

  const loadModels = useModelsStore((s) => s.load);
  const modelsReady = useModelsReady();
  const lane = useLane("startEnd");

  const [aspect, setAspect] = useState("");
  const [duration, setDuration] = useState("");
  const [rows, setRows] = useState<Row[]>([newRow()]);
  const [picker, setPicker] = useState<{ key: number; slot: "start" | "end" } | null>(
    null,
  );
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const seeded = useRef(false);

  useEffect(() => {
    void loadConfig();
    void loadModels();
    void rehydrate();
  }, [loadConfig, loadModels, rehydrate]);

  useEffect(() => {
    // Wait for BOTH: seeding from settings alone, before the registry has
    // landed, leaves every control on "" — and an empty quality does not
    // fail at the backend, it resolves to the pricier `fast` lane. `lane`
    // must be in the deps or the effect can never re-run once it does land.
    if (seeded.current || !canSeed(configLoaded, lane.aspects)) return;
    seeded.current = true;
    setAspect(aspectFromConfig(configValues["VIDEO_ASPECT_RATIO"], lane.aspects));
    // Honour the configured length when this lane has it. It was pinned to 8s
    // because Veo's FL family had one length; OMNI has four and bills by them,
    // so discarding the user's choice here would charge for a clip they did not
    // ask for.
    const wanted = Number(configValues["VIDEO_DURATION_SECONDS"]);
    const usable = lane.durations.some((d) => d.value === wanted);
    setDuration(String(usable ? wanted : (lane.durations[0]?.value ?? 8)));
  }, [configLoaded, configValues, lane]);

  const complete = (r: Row) =>
    Boolean(r.startId && r.endId && r.prompt.trim() && r.startId !== r.endId);
  const filled = rows.filter(complete);
  const problems = rows
    .map((r, i) => ({ r, n: i + 1 }))
    .filter(({ r }) => (r.startId || r.endId || r.prompt.trim()) && !complete(r));

  function patchRow(key: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function onGenerate() {
    void enqueueRow(
      REQUEST_TYPE,
      filled.map((r) => ({
        prompt: r.prompt.trim(),
        // Only OMNI serves first→last on this transport — Veo's end-image
        // payload was never captured — so the registry lists `omni` first and
        // this takes whatever it offers rather than naming a lane here a
        // second time. A Veo lane would be refused, for free, by the backend.
        params: toParams({
          kind: "startEnd",
          aspect,
          quality: defaultValue(lane.qualities, "omni"),
          start: r.startId as string,
          end: r.endId as string,
          // Both are part of OMNI's model key, and `toParams` drops them on a
          // lane that would not read them.
          durationS: Number(duration),
          resolution: resolutionFromConfig(configValues["VIDEO_RESOLUTION"]),
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
    reader.onload = () => {
      const lines = String(reader.result || "")
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
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
              disabled={filled.length === 0 || problems.length > 0 || !modelsReady}
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
          <LabeledSelect label="📏 Tỷ lệ" value={aspect} onChange={setAspect} options={toSelectOptions(lane.aspects)} />
          <LabeledSelect
            label="⏱ Thời lượng"
            value={duration}
            onChange={setDuration}
            options={lane.durations.map((d) => ({
              value: String(d.value),
              label: d.credits ? `${d.label} · ${d.credits} credit` : d.label,
            }))}
          />
          <LabeledSelect
            label="🎬 Model"
            value="fast"
            onChange={() => undefined}
            options={[{ value: "fast", label: "Veo 3.1 - Fast" }]}
            disabled
            className="min-w-[240px]"
          />
        </div>
        <div className="mt-2 text-[11px] text-ink-dim">
          Chế độ đầu-cuối chỉ có một họ model (Fast, 8s). Lite, Quality,
          Lower Priority và 4s/6s không tồn tại ở chế độ này nên bị khoá — chọn
          được cũng sẽ bị đổi về Fast.
        </div>
      </Card>

      <Card>
        <div className="mb-1 text-[13px] font-semibold text-ink">
          🎞 Ảnh đầu → Ảnh cuối, kèm prompt cho mỗi clip
        </div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-mute">
          <span>💡 Mỗi dòng = 1 video chuyển từ ảnh đầu sang ảnh cuối.</span>
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
              setPicker(null);
            }}
          >
            🗑 Xóa hết
          </GhostButton>
          <span className="ml-auto text-ink-dim">{filled.length} dòng sẵn sàng</span>
        </div>

        <div className="flex flex-col gap-2">
          {rows.map((row, i) => {
            const bad =
              (row.startId || row.endId || row.prompt.trim()) && !complete(row);
            return (
              <div
                key={row.key}
                className={cn(
                  "rounded-(--radius-card) border p-2",
                  bad ? "border-danger" : "border-line",
                )}
              >
                <div className="flex items-start gap-2">
                  <span className="w-6 shrink-0 pt-2 text-center text-[12px] text-ink-dim">
                    {i + 1}
                  </span>
                  <FrameSlot
                    label="Ảnh đầu"
                    mediaId={row.startId}
                    onClick={() =>
                      setPicker(
                        picker?.key === row.key && picker.slot === "start"
                          ? null
                          : { key: row.key, slot: "start" },
                      )
                    }
                  />
                  <span className="pt-8 text-[13px] text-ink-dim">→</span>
                  <FrameSlot
                    label="Ảnh cuối"
                    mediaId={row.endId}
                    onClick={() =>
                      setPicker(
                        picker?.key === row.key && picker.slot === "end"
                          ? null
                          : { key: row.key, slot: "end" },
                      )
                    }
                  />
                  <textarea
                    value={row.prompt}
                    onChange={(e) => patchRow(row.key, { prompt: e.target.value })}
                    placeholder="Mô tả chuyển động giữa hai khung hình..."
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
                      if (picker?.key === row.key) setPicker(null);
                    }}
                    className="shrink-0 px-1 pt-2 text-[13px] text-danger"
                    aria-label={`Xóa dòng ${i + 1}`}
                  >
                    🗑
                  </button>
                </div>

                {row.startId && row.startId === row.endId && (
                  <div className="mt-1 text-[12px] text-danger">
                    Ảnh đầu và ảnh cuối đang là cùng một ảnh — clip sẽ không
                    chuyển đi đâu cả.
                  </div>
                )}

                {picker?.key === row.key && (
                  <MediaPicker
                    className="mt-2"
                    label={
                      picker.slot === "start"
                        ? `🖼 Ảnh đầu — dòng ${i + 1}`
                        : `🖼 Ảnh cuối — dòng ${i + 1}`
                    }
                    value={picker.slot === "start" ? row.startId : row.endId}
                    onChange={(mediaId) => {
                      patchRow(
                        row.key,
                        picker.slot === "start"
                          ? { startId: mediaId }
                          : { endId: mediaId },
                      );
                      if (mediaId) setPicker(null);
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>

        {problems.length > 0 && (
          <div className="mt-2 text-[12px] text-danger">
            Dòng chưa đủ: {problems.map(({ n }) => n).join(", ")} — mỗi dòng cần
            ảnh đầu, ảnh cuối khác nhau và một prompt.
          </div>
        )}
        {importError && <div className="mt-2 text-[12px] text-danger">{importError}</div>}
        {error && <div className="mt-2 text-[12px] text-danger">{error}</div>}
      </Card>

      <JobList tool={TOOL} kind="video" />
    </div>
  );
}

function FrameSlot({
  label,
  mediaId,
  onClick,
}: {
  label: string;
  mediaId: string | null;
  onClick: () => void;
}) {
  return (
    <div className="flex shrink-0 flex-col items-center gap-1">
      <button
        type="button"
        onClick={onClick}
        className="h-20 w-20 overflow-hidden rounded-md border border-line bg-black text-[11px] text-ink-dim"
      >
        {mediaId ? (
          <img src={mediaUrl(mediaId)} alt="" className="h-full w-full object-cover" />
        ) : (
          "(Click chọn)"
        )}
      </button>
      <span className="text-[10px] text-ink-dim">{label}</span>
    </div>
  );
}
