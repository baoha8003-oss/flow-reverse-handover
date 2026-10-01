import { useEffect, useRef, useState } from "react";
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
import { useAppConfigStore } from "@/store/appConfig";
import { mediaUrl } from "@/api/client";
import { cn } from "@/lib/utils";
import {
  aspectFromConfig,
  creditsFor,
  resolutionFromConfig,
  toSelectOptions,
  useLane,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Đồng bộ nhân vật — keep the same faces across a batch of clips.
 *
 * Runs on the reference-image video path (`gen_video_omni`), which is the only
 * dispatch that carries several reference images into one generation.
 *
 * Two rules come straight from the packaged tool and are enforced here rather
 * than left to fail at Flow: at most 10 characters in the cast, and at most 3
 * called by any single prompt — beyond that the model stops holding the
 * likenesses apart.
 *
 * A prompt names characters with `{Tên}`. Only the characters a line actually
 * calls are sent with it, so a 10-character cast does not push all ten refs
 * into every clip.
 */

const REQUEST_TYPE = "gen_video_omni";
/** Its own ui key. `gen_video_omni` is also what Image-to-Video dispatches for
 * OMNI Flash, and sharing a key would make each tab list — and cancel — the
 * other's jobs. */
const TOOL = "character-sync";
const MAX_CHARACTERS = 10;
const MAX_PER_PROMPT = 3;
const MAX_PROMPT_FILE_BYTES = 1_000_000;

// This tab dispatches on OMNI Flash, so its aspects, lengths and credit
// prices are the OMNI lane of /api/models — not a second copy of the table.

export interface Character {
  key: number;
  mediaId: string;
  name: string;
}

let charKey = 0;

/** Character names a prompt calls, in `{Tên}` order, de-duplicated. */
export function namesInPrompt(prompt: string): string[] {
  const out: string[] = [];
  for (const m of prompt.matchAll(/\{([^{}]+)\}/g)) {
    const name = m[1]!.trim();
    if (name && !out.some((n) => n.toLowerCase() === name.toLowerCase())) {
      out.push(name);
    }
  }
  return out;
}

export interface ResolvedLine {
  prompt: string;
  refIds: string[];
  unknown: string[];
  tooMany: boolean;
}

/** Match a prompt's `{Tên}` tokens against the cast.
 *
 * Names are compared case-insensitively — a user who typed "Lan" in the cast
 * and "{lan}" in the prompt means the same person, and failing that dispatch
 * over capitalisation would be pure friction.
 *
 * The braces are stripped from the text that reaches Flow: they are this
 * tool's syntax, not something the model should try to render.
 */
export function resolveLine(line: string, cast: Character[]): ResolvedLine {
  const called = namesInPrompt(line);
  const refIds: string[] = [];
  const unknown: string[] = [];
  for (const name of called) {
    const hit = cast.find(
      (c) => c.name.trim().toLowerCase() === name.toLowerCase(),
    );
    if (hit) refIds.push(hit.mediaId);
    else unknown.push(name);
  }
  return {
    prompt: line.replace(/\{([^{}]+)\}/g, (_m, n: string) => n.trim()),
    refIds,
    unknown,
    tooMany: refIds.length > MAX_PER_PROMPT,
  };
}

export function CharacterSyncTab() {
  const loadConfig = useAppConfigStore((s) => s.load);
  const configLoaded = useAppConfigStore((s) => s.loaded);
  const configValues = useAppConfigStore((s) => s.values);
  const enqueueRow = useJobsStore((s) => s.enqueueRow);
  const cancelAll = useJobsStore((s) => s.cancelAll);
  const busy = useJobsStore((s) => s.busy[TOOL] ?? false);
  const storeError = useJobsStore((s) => s.error[TOOL] ?? null);
  const jobs = useJobsStore((s) => s.jobs);
  const projectName = useJobsStore((s) => s.projectName);

  const [cast, setCast] = useState<Character[]>([]);
  const [adding, setAdding] = useState(false);
  const [prompts, setPrompts] = useState("");
  const loadModels = useModelsStore((s) => s.load);
  const modelsReady = useModelsReady();
  const lane = useLane("omni");

  const [aspect, setAspect] = useState("");
  const [duration, setDuration] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const seeded = useRef(false);

  useEffect(() => {
    void loadConfig();
    void loadModels();
  }, [loadConfig, loadModels]);

  useEffect(() => {
    // Wait for BOTH: seeding from settings alone, before the registry has
    // landed, leaves every control on "" — and an empty quality does not
    // fail at the backend, it resolves to the pricier `fast` lane. `lane`
    // must be in the deps or the effect can never re-run once it does land.
    if (seeded.current || !canSeed(configLoaded, lane.aspects)) return;
    seeded.current = true;
    setAspect(aspectFromConfig(configValues["VIDEO_ASPECT_RATIO"], lane.aspects));
  }, [configLoaded, configValues, lane]);

  const anyRunning = jobs.some(
    (j) => j.tool === TOOL && (j.status === "queued" || j.status === "running"),
  );

  const lines = prompts.split("\n").filter((l) => l.trim().length > 0);
  const resolved = lines.map((l) => resolveLine(l.trim(), cast));
  const unnamed = cast.filter((c) => !c.name.trim());

  function onGenerate() {
    if (lines.length === 0) {
      setValidation("Hãy nhập ít nhất một prompt.");
      return;
    }
    if (cast.length === 0) {
      setValidation("Hãy thêm ảnh nhân vật và đặt tên cho từng người.");
      return;
    }
    if (unnamed.length > 0) {
      setValidation(
        `Nhân vật chưa có tên: ${unnamed
          .map((c) => `#${cast.indexOf(c) + 1}`)
          .join(", ")}`,
      );
      return;
    }
    // Report every bad line at once — fixing them one dispatch at a time is
    // the sort of thing that makes a batch tool useless.
    const noRef = resolved
      .map((r, i) => (r.refIds.length === 0 ? i + 1 : 0))
      .filter(Boolean);
    if (noRef.length > 0) {
      setValidation(
        `Dòng ${noRef.join(", ")} chưa gọi tên nhân vật nào. Dùng cú pháp {Tên nhân vật} trong prompt.`,
      );
      return;
    }
    const bad = resolved
      .map((r, i) => (r.unknown.length > 0 ? `${i + 1} (${r.unknown.join(", ")})` : ""))
      .filter(Boolean);
    if (bad.length > 0) {
      setValidation(`Tên không có trong danh sách nhân vật — dòng ${bad.join("; ")}.`);
      return;
    }
    const over = resolved
      .map((r, i) => (r.tooMany ? i + 1 : 0))
      .filter(Boolean);
    if (over.length > 0) {
      setValidation(
        `Dòng ${over.join(", ")} gọi quá ${MAX_PER_PROMPT} nhân vật. Tách bớt để video giữ được nét mặt.`,
      );
      return;
    }
    setValidation(null);

    void enqueueRow(
      REQUEST_TYPE,
      resolved.map((r) => ({
        prompt: r.prompt,
        params: toParams({
          kind: "omni",
          aspect,
          durationS: Number(duration),
          resolution: resolutionFromConfig(configValues["VIDEO_RESOLUTION"]),
          refs: r.refIds,
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

  const totalCredits = lines.length * (creditsFor(lane.durations, duration) ?? 0);

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
          <LabeledSelect
            label="📐 Tỉ lệ"
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
              label: d.credits ? `${d.label} · ${d.credits} credit` : d.label,
            }))}
            className="min-w-[170px]"
          />
          <div className="ml-auto flex items-end gap-2">
            <PrimaryButton
              onClick={onGenerate}
              disabled={lines.length === 0 || !modelsReady}
              loading={busy}
            >
              {anyRunning && lines.length > 0
                ? `THÊM ${lines.length} VÀO HÀNG CHỜ`
                : "TẠO VIDEO ĐỒNG NHẤT NHÂN VẬT"}
            </PrimaryButton>
            <StopButton onClick={() => void cancelAll(TOOL)} disabled={!anyRunning}>
              {anyRunning ? "DỪNG ĐANG CHẠY" : "DỪNG"}
            </StopButton>
          </div>
        </div>
        {lines.length > 0 && (
          <p className="mt-2 text-[12px] text-warn">
            ⚠ {lines.length} video × {duration}s ≈ {totalCredits} credit.
          </p>
        )}
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="mb-1 text-[13px] font-semibold text-ink">
            ✏️ Prompt cho từng video
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-mute">
            <span>
              💡 Mỗi dòng một video. Gọi nhân vật bằng{" "}
              <code className="text-ink">{"{Tên nhân vật}"}</code>, tối đa{" "}
              {MAX_PER_PROMPT} người mỗi dòng.
            </span>
            <GhostButton onClick={() => fileRef.current?.click()}>
              📄 Import prompt.txt
            </GhostButton>
            <input ref={fileRef} type="file" accept=".txt" hidden onChange={onImport} />
            <span className="ml-auto text-ink-dim">{lines.length} prompt</span>
          </div>
          <textarea
            value={prompts}
            onChange={(e) => setPrompts(e.target.value)}
            placeholder={"{Lan} bước vào quán cà phê và vẫy tay chào {Minh}"}
            className="h-56 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[13px] text-ink placeholder:text-ink-dim"
          />
          {lines.length > 0 && (
            <div className="mt-2 flex flex-col gap-1">
              {resolved.map((r, i) => (
                <p
                  key={i}
                  className={cn(
                    "text-[11px]",
                    r.unknown.length > 0 || r.tooMany || r.refIds.length === 0
                      ? "text-danger"
                      : "text-ink-dim",
                  )}
                >
                  Dòng {i + 1}:{" "}
                  {r.refIds.length === 0
                    ? "chưa gọi nhân vật nào"
                    : `${r.refIds.length} nhân vật`}
                  {r.unknown.length > 0 && ` · không có: ${r.unknown.join(", ")}`}
                  {r.tooMany && ` · quá ${MAX_PER_PROMPT} người`}
                </p>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-ink">
            👤 Nhân vật
            <span className="ml-auto text-[11px] font-normal text-ink-dim">
              {cast.length}/{MAX_CHARACTERS} · tên là bắt buộc
            </span>
          </div>

          <div className="flex flex-col gap-2">
            {cast.map((c, i) => (
              <div
                key={c.key}
                className={cn(
                  "flex items-center gap-2 rounded-(--radius-card) border p-2",
                  c.name.trim() ? "border-line" : "border-danger",
                )}
              >
                <img
                  src={mediaUrl(c.mediaId)}
                  alt=""
                  className="h-14 w-14 shrink-0 rounded-md border border-line bg-black object-cover"
                />
                <input
                  value={c.name}
                  onChange={(e) =>
                    setCast((prev) =>
                      prev.map((x) =>
                        x.key === c.key ? { ...x, name: e.target.value } : x,
                      ),
                    )
                  }
                  placeholder={`Tên nhân vật #${i + 1}`}
                  className="h-9 min-w-0 flex-1 rounded-(--radius-ctl) border border-line bg-surface px-2 text-[13px] text-ink placeholder:text-ink-dim"
                />
                <button
                  type="button"
                  onClick={() => setCast((prev) => prev.filter((x) => x.key !== c.key))}
                  className="px-1 text-[13px] text-danger"
                  aria-label={`Xóa nhân vật ${i + 1}`}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>

          {cast.length >= MAX_CHARACTERS ? (
            <div className="mt-2 text-[12px] text-warn">
              Đã đủ tối đa {MAX_CHARACTERS} nhân vật.
            </div>
          ) : adding ? (
            <MediaPicker
              className="mt-2"
              label="🖼 Thêm ảnh nhân vật"
              value={null}
              onChange={(mediaId) => {
                if (mediaId) {
                  setCast((prev) => [...prev, { key: ++charKey, mediaId, name: "" }]);
                  setAdding(false);
                }
              }}
            />
          ) : (
            <GhostButton onClick={() => setAdding(true)}>
              📂 Thêm ảnh nhân vật
            </GhostButton>
          )}
        </Card>
      </div>

      {(validation || importError || storeError) && (
        <Card>
          {validation && <p className="text-[12px] text-danger">{validation}</p>}
          {importError && <p className="text-[12px] text-danger">{importError}</p>}
          {storeError && <p className="text-[12px] text-danger">{storeError}</p>}
        </Card>
      )}

      <JobList tool={TOOL} />
    </div>
  );
}
