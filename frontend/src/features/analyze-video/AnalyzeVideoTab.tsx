import { useState } from "react";
import { Card, GhostButton, LabeledSelect, PrimaryButton } from "@/ui/primitives";
import { ClipPicker } from "@/features/postprod/ClipPicker";
import { useLibrary } from "@/features/postprod/useLibrary";
import {
  analyzeVideo,
  type AnalyzeVideoResult,
  type LibraryItem,
} from "@/api/client";

/** Phân tích video — read a clip back into words, and into a prompt.
 *
 * Vision models take images, so the backend samples frames and tells the
 * model they are one sequence; that is what makes it describe the motion
 * instead of narrating six unrelated pictures.
 *
 * The recreation prompt is the useful half: copy it into Text to Video to
 * make something in the same vein, which is the whole reason the packaged
 * tool has this screen.
 *
 * "Kiểu phân tích" picks between the four system prompts the packaged tool
 * ships. The last three ask for a scene-by-scene breakdown rather than a
 * paragraph — one image prompt, one Veo prompt and one line of dialogue per
 * scene — which is what makes a copied video reproducible instead of merely
 * described.
 */

const FRAME_OPTIONS = [
  { value: "4", label: "4 khung (nhanh, rẻ)" },
  { value: "6", label: "6 khung" },
  { value: "8", label: "8 khung" },
  { value: "12", label: "12 khung (kỹ nhất)" },
];

/** Labels are the packaged tool's own, emoji included: the agent matches on
 * substrings so an imported workflow's `analysis_mode` resolves the same way. */
const MODE_OPTIONS = [
  { value: "", label: "Mô tả ngắn (mặc định)" },
  { value: "📹 Video thường", label: "📹 Video thường — chia cảnh" },
  { value: "📹 Video thường TEXT", label: "📝 Video thường TEXT" },
  { value: "🧍 Video Người Que", label: "🧍 Video Người Que" },
  { value: "💊 Video sức khỏe", label: "💊 Video sức khỏe" },
];

export function AnalyzeVideoTab() {
  const { sources, renders, loading, error, refresh } = useLibrary();
  const [selected, setSelected] = useState<string[]>([]);
  const [frames, setFrames] = useState("6");
  const [mode, setMode] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeVideoResult | null>(null);
  const [copied, setCopied] = useState(false);

  const pool: LibraryItem[] = [...renders, ...sources];

  async function run() {
    setBusy(true);
    setFailure(null);
    setResult(null);
    try {
      setResult(
        await analyzeVideo({
          video: selected[0]!,
          frames: Number(frames),
          mode,
        }),
      );
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Phân tích thất bại.");
    } finally {
      setBusy(false);
    }
  }

  async function copyPrompt() {
    if (!result?.prompt) return;
    try {
      await navigator.clipboard.writeText(result.prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked — the text is on screen and selectable anyway.
    }
  }

  if (loading) {
    return (
      <Card>
        <p className="text-[13px] text-ink-mute">Đang tải thư viện…</p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-[13px] font-extrabold text-ink">
            🎥 Chọn video cần phân tích
          </h2>
          <GhostButton onClick={() => void refresh()}>🔄 Làm mới</GhostButton>
        </div>
        <p className="mt-1 text-[12px] text-ink-dim">
          Tool lấy vài khung hình rải đều rồi đọc chúng như một chuỗi, nên mô tả
          được cả chuyển động chứ không chỉ từng ảnh rời.
        </p>
        <div className="mt-3">
          <ClipPicker
            items={pool}
            selected={selected}
            onToggle={(p) => setSelected((s) => (s[0] === p ? [] : [p]))}
            emptyNote="Chưa có video nào trong thư viện."
          />
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <LabeledSelect
            label="Số khung lấy mẫu"
            value={frames}
            onChange={setFrames}
            options={FRAME_OPTIONS}
            className="min-w-[200px]"
          />
          <LabeledSelect
            label="Kiểu phân tích"
            value={mode}
            onChange={setMode}
            options={MODE_OPTIONS}
            className="min-w-[220px]"
          />
          <PrimaryButton
            onClick={() => void run()}
            disabled={selected.length === 0}
            loading={busy}
          >
            🔍 PHÂN TÍCH
          </PrimaryButton>
          <span className="text-[12px] text-ink-dim">
            Chỉ gọi AI đọc ảnh — không tốn credit video.
          </span>
        </div>
      </Card>

      {error && (
        <Card>
          <p className="text-[12px] text-danger">{error}</p>
        </Card>
      )}

      {(busy || failure || result) && (
        <Card>
          {busy && (
            <p className="text-[13px] text-ink-mute">Đang đọc khung hình…</p>
          )}
          {failure && <p className="text-[13px] text-danger">{failure}</p>}
          {result && (
            <div className="flex flex-col gap-3">
              <div>
                <div className="text-[13px] font-semibold text-ink">
                  📖 Nội dung ({result.frames} khung)
                </div>
                <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-ink-mute">
                  {result.description}
                </p>
              </div>
              {result.prompt && (
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-[13px] font-semibold text-ink">
                      ✍️ Prompt tái tạo
                    </span>
                    <GhostButton onClick={() => void copyPrompt()}>
                      {copied ? "✓ Đã copy" : "📋 Copy prompt"}
                    </GhostButton>
                    <span className="text-[11px] text-ink-dim">
                      Dán sang tab Text to Video để tạo clip tương tự.
                    </span>
                  </div>
                  <textarea
                    readOnly
                    value={result.prompt}
                    className="mt-2 h-32 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[12px] text-ink"
                  />
                </div>
              )}
              {result.scenes.length > 0 && (
                <div>
                  <div className="text-[13px] font-semibold text-ink">
                    🎬 {result.scenes.length} cảnh
                  </div>
                  <div className="mt-2 flex flex-col gap-2">
                    {result.scenes.map((scene, i) => (
                      <div
                        key={scene.scene_number ?? i}
                        className="rounded-(--radius-ctl) border border-line bg-card p-3"
                      >
                        <div className="text-[12px] font-semibold text-ink">
                          Cảnh {scene.scene_number ?? i + 1}
                        </div>
                        {scene.image_prompt && (
                          <p className="mt-1 text-[12px] text-ink-mute">
                            <span className="text-ink-dim">Ảnh: </span>
                            {scene.image_prompt}
                          </p>
                        )}
                        {scene.veo_prompt && (
                          <p className="mt-1 text-[12px] text-ink-mute">
                            <span className="text-ink-dim">Video: </span>
                            {scene.veo_prompt}
                          </p>
                        )}
                        {scene.dialogue && (
                          <p className="mt-1 text-[12px] text-ink-mute">
                            <span className="text-ink-dim">Thoại: </span>
                            {scene.dialogue}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
