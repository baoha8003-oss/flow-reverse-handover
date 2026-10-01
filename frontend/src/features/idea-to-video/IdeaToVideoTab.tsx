import { useEffect, useRef, useState } from "react";
import {
  Card,
  GhostButton,
  LabeledSelect,
  PrimaryButton,
  StopButton,
} from "@/ui/primitives";
import { JobList } from "@/features/jobs/JobList";
import { toParams } from "@/lib/generationSpec";
import { ModelsBanner } from "@/components/ModelsBanner";
import { useJobsStore } from "@/store/jobs";
import { useAppConfigStore } from "@/store/appConfig";
import {
  ideaToPrompts,
  listScriptGenres,
  listVideoFormats,
  listVideoStyles,
  type PromptFinding,
  type ScriptGenre,
  type VideoFormat,
  type VideoStyle,
} from "@/api/client";
import {
  aspectFromConfig,
  noteFor,
  qualityFromLabel,
  toSelectOptions,
  useLane,
  canSeed,
  useModelsReady,
  useModelsStore,
} from "@/store/models";

/** Ý tưởng → Video — paste an idea, get an ordered shot list, then dispatch.
 *
 * Two steps on purpose. The model writes the scenes, but every scene is one
 * paid video call, so the prompts land in editable boxes first; nothing is
 * dispatched until the user has read them. The packaged tool works the same
 * way, and it is the difference between a typo costing a keystroke and a typo
 * costing credits.
 *
 * Character consistency is handled in the prompt, not by the model's memory:
 * the backend asks for the same concrete description of each person in every
 * scene, because the generator starts from nothing on each clip.
 */

const REQUEST_TYPE = "gen_video_text";
/** Its own ui key. The dispatch type is the same one Text to Video uses, and
 * sharing a key would make each tab list — and DỪNG — the other's jobs. */
const TOOL = "idea-to-video";
const MAX_SCENES = 20;

// Lanes and aspects come from /api/models — the same registry Text to Video
// reads, so the two tabs cannot drift apart again.

const DIALOGUE_OPTIONS = [
  { value: "", label: "Không lời thoại" },
  { value: "Tiếng Việt", label: "Lời thoại: Tiếng Việt" },
  { value: "English", label: "Lời thoại: English" },
];

export function IdeaToVideoTab() {
  const loadConfig = useAppConfigStore((s) => s.load);
  const configLoaded = useAppConfigStore((s) => s.loaded);
  const configValues = useAppConfigStore((s) => s.values);
  const enqueueRow = useJobsStore((s) => s.enqueueRow);
  const cancelAll = useJobsStore((s) => s.cancelAll);
  const busy = useJobsStore((s) => s.busy[TOOL] ?? false);
  const storeError = useJobsStore((s) => s.error[TOOL] ?? null);
  const jobs = useJobsStore((s) => s.jobs);

  const [idea, setIdea] = useState("");
  const [sceneCount, setSceneCount] = useState(3);
  const [styleName, setStyleName] = useState("");
  const [styleList, setStyleList] = useState<VideoStyle[]>([]);
  const [dialogue, setDialogue] = useState("Tiếng Việt");
  // The packaged tool writes in eight named Vietnamese formulas — sử thi,
  // Phật pháp, đạo lý, drama lật kèo and so on. Each carries its own
  // structure and, more consequentially, its own per-scene dialogue
  // length: a scene is 5–8 seconds, so 30 words is the ceiling a voice
  // fits inside one. Leaving this unset keeps the general craft notes.
  const [genre, setGenre] = useState("");
  const [genreList, setGenreList] = useState<ScriptGenre[]>([]);
  // The shape of the video, which is a different question from the genre: a
  // trailer and a micro-drama are written from different playbooks, and the
  // skill tree ships one for each. Unset loads none of them — they are
  // mutually irrelevant, so loading all five would spend the prompt budget
  // teaching four wrong shapes.
  const [videoFormat, setVideoFormat] = useState("");
  const [formatList, setFormatList] = useState<VideoFormat[]>([]);
  // The README's other two skills. Off by default: each one is real material
  // competing for the same prompt budget as the craft notes.
  const [useLongform, setUseLongform] = useState(false);
  const [use3act, setUse3act] = useState(false);
  const loadModels = useModelsStore((s) => s.load);
  const modelsReady = useModelsReady();
  const lane = useLane("t2v");

  const [aspect, setAspect] = useState("");
  const [model, setModel] = useState("");
  const [prompts, setPrompts] = useState<string[] | null>(null);
  // What the free rule check found, per scene. Shown rather than enforced
  // here: the user is the one who knows whether a name belongs in the
  // narration (allowed) or in the picture (refused by the filter). The
  // executor refuses the two that cost money if they reach a dispatch.
  const [findings, setFindings] = useState<PromptFinding[]>([]);
  const seeded = useRef(false);
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void loadConfig();
    void loadModels();
    void listVideoStyles()
      .then(setStyleList)
      .catch(() => setStyleList([]));
    // A missing asset library means no genres, which is a smaller list,
    // not a broken tab — the generic path still writes scenes.
    void listScriptGenres()
      .then(setGenreList)
      .catch(() => setGenreList([]));
    void listVideoFormats()
      .then(setFormatList)
      .catch(() => setFormatList([]));
  }, [loadConfig, loadModels]);

  useEffect(() => {
    // Wait for BOTH: seeding from settings alone, before the registry has
    // landed, leaves every control on "" — and an empty quality does not
    // fail at the backend, it resolves to the pricier `fast` lane. `lane`
    // must be in the deps or the effect can never re-run once it does land.
    if (seeded.current || !canSeed(configLoaded, lane.qualities)) return;
    seeded.current = true;
    setAspect(aspectFromConfig(configValues["VIDEO_ASPECT_RATIO"], lane.aspects));
    setModel(qualityFromLabel(configValues["VEO_MODEL"], lane.qualities));
    const n = Number(configValues["IDEA_SCENE_COUNT"]);
    if (Number.isInteger(n) && n >= 1) setSceneCount(Math.min(n, MAX_SCENES));
    const s = configValues["IDEA_STYLE"];
    if (typeof s === "string" && s) setStyleName(s);
  }, [configLoaded, configValues, lane]);

  const anyRunning = jobs.some(
    (j) => j.tool === TOOL && (j.status === "queued" || j.status === "running"),
  );
  const laneNote = noteFor(lane.qualities, model);

  async function compose() {
    if (!idea.trim()) {
      setError("Hãy nhập ý tưởng trước.");
      return;
    }
    setComposing(true);
    setError(null);
    try {
      const res = await ideaToPrompts({
        idea,
        scene_count: sceneCount,
        style: styleName || null,
        dialogue_language: dialogue || null,
        no_dialogue: !dialogue,
        genre: genre || null,
        format: videoFormat || null,
        use_longform: useLongform,
        use_3act: use3act,
      });
      setPrompts(res.prompts);
      setFindings(res.findings ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không dựng được prompt.");
    } finally {
      setComposing(false);
    }
  }

  function dispatch() {
    const usable = (prompts ?? []).map((p) => p.trim()).filter(Boolean);
    if (usable.length === 0) return;
    void enqueueRow(
      REQUEST_TYPE,
      usable.map((prompt) => ({
        prompt,
        params: toParams({
          kind: "t2v",
          aspect,
          quality: model,
          // One scene is one 8s clip; the shot list is written to that beat.
          durationS: 8,
        }),
      })),
      TOOL,
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <ModelsBanner />
      <Card>
        <div className="mb-1 text-[13px] font-semibold text-ink">
          💡 Ý tưởng / kịch bản
        </div>
        <p className="mb-2 text-[12px] text-ink-mute">
          Viết ý tưởng bằng tiếng Việt cũng được. Tool dựng thành từng cảnh nối
          tiếp nhau, giữ cùng nhân vật xuyên suốt.
        </p>
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="Ví dụ: Một cô gái bán hàng rong ở phố cổ Hà Nội, từ sáng sớm tới chiều tà..."
          className="h-32 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[13px] text-ink placeholder:text-ink-dim"
        />
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[12px] text-ink-mute">🎬 Số cảnh</span>
            <input
              type="number"
              min={1}
              max={MAX_SCENES}
              value={sceneCount}
              onChange={(e) =>
                setSceneCount(
                  Math.max(1, Math.min(MAX_SCENES, Number(e.target.value) || 1)),
                )
              }
              className="h-9 w-[90px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
            />
          </label>
          <LabeledSelect
            label="🎨 Phong cách"
            value={styleName}
            onChange={setStyleName}
            options={[
              { value: "", label: "Không chọn" },
              ...styleList.map((s) => ({ value: s.name, label: s.name })),
            ]}
            className="min-w-[190px]"
          />
          {genreList.length > 0 && (
            <LabeledSelect
              label="📖 Thể loại kịch bản"
              value={genre}
              onChange={setGenre}
              options={[
                { value: "", label: "Không chọn" },
                ...genreList.map((g) => ({
                  value: g.key,
                  label: g.wordsPerScene
                    ? `${g.title} (${g.wordsPerScene[0]}–${g.wordsPerScene[1]} từ/cảnh)`
                    : g.title,
                })),
              ]}
              className="min-w-[260px]"
            />
          )}
          {formatList.length > 0 && (
            <LabeledSelect
              label="🎞 Dạng video"
              value={videoFormat}
              onChange={setVideoFormat}
              options={[
                { value: "", label: "Không chọn" },
                ...formatList.map((f) => ({ value: f.key, label: f.title })),
              ]}
              className="min-w-[220px]"
            />
          )}
          <LabeledSelect
            label="🗣 Lời thoại"
            value={dialogue}
            onChange={setDialogue}
            options={DIALOGUE_OPTIONS}
            className="min-w-[190px]"
          />
          <PrimaryButton
            onClick={() => void compose()}
            disabled={idea.trim().length === 0}
            loading={composing}
          >
            ✍️ DỰNG {sceneCount} PROMPT
          </PrimaryButton>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-4">
          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink">
            <input
              type="checkbox"
              checked={useLongform}
              onChange={(e) => setUseLongform(e.target.checked)}
            />
            Phim dài 10–30′ (điều phối theo sequence)
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink">
            <input
              type="checkbox"
              checked={use3act}
              onChange={(e) => setUse3act(e.target.checked)}
            />
            Cấu trúc 3 hồi · 8 sequence
          </label>
        </div>
        <p className="mt-2 text-[11px] text-ink-dim">
          Bước này chỉ gọi AI viết chữ — chưa tốn credit video.
        </p>
      </Card>

      {prompts && (
        <Card>
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <span className="text-[13px] font-semibold text-ink">
              📝 {prompts.length} cảnh — sửa thoải mái trước khi tạo
            </span>
            <GhostButton onClick={() => setPrompts(null)}>Bỏ</GhostButton>
          </div>
          <div className="flex flex-col gap-2">
            {prompts.map((p, i) => (
              <div key={i} className="flex gap-2">
                <span className="mt-2 w-6 shrink-0 text-right text-[12px] text-ink-dim">
                  {i + 1}
                </span>
                <div className="w-full">
                  <textarea
                    value={p}
                    onChange={(e) =>
                      setPrompts((prev) =>
                        (prev ?? []).map((x, j) => (j === i ? e.target.value : x)),
                      )
                    }
                    className="h-20 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-2 text-[12px] text-ink"
                  />
                  {findings
                    .filter((f) => f.scene === i + 1)
                    .map((f, k) => (
                      <p
                        key={k}
                        className={`mt-1 text-[11px] ${
                          f.severity === "error" ? "text-warn" : "text-ink-dim"
                        }`}
                      >
                        ⚠ {f.message}
                      </p>
                    ))}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <LabeledSelect
              label="📐 Tỉ lệ"
              value={aspect}
              onChange={setAspect}
              options={toSelectOptions(lane.aspects)}
            />
            <LabeledSelect
              label="🎥 Model"
              value={model}
              onChange={setModel}
              options={toSelectOptions(lane.qualities)}
              className="min-w-[240px]"
            />
            <div className="ml-auto flex items-end gap-2">
              <PrimaryButton onClick={dispatch} disabled={!modelsReady} loading={busy}>
                {anyRunning
                  ? `THÊM ${prompts.length} VÀO HÀNG CHỜ`
                  : `TẠO ${prompts.length} VIDEO`}
              </PrimaryButton>
              <StopButton
                onClick={() => void cancelAll(TOOL)}
                disabled={!anyRunning}
              >
                {anyRunning ? "DỪNG ĐANG CHẠY" : "DỪNG"}
              </StopButton>
            </div>
          </div>
          {laneNote && (
            <div className="mt-2 text-[12px] text-warn">⚠ {laneNote}</div>
          )}
          <p className="mt-1 text-[11px] text-ink-dim">
            Mỗi cảnh là một video 8 giây riêng và CÓ tính credit. Ghép lại ở tab
            Cut &amp; Merge sau khi xong.
          </p>
        </Card>
      )}

      {(error || storeError) && (
        <Card>
          {error && <p className="text-[12px] text-danger">{error}</p>}
          {storeError && <p className="text-[12px] text-danger">{storeError}</p>}
        </Card>
      )}

      <JobList tool={TOOL} />
    </div>
  );
}
