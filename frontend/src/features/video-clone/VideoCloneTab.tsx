import { useState } from "react";
import { useTabState } from "@/lib/useTabState";
import { Card, GhostButton, LabeledSelect, PrimaryButton } from "@/ui/primitives";
import { prettySize } from "@/features/postprod/useLibrary";
import {
  cloneVideo,
  cloneVideoPreview,
  type CloneResult,
} from "@/api/client";

/** Video Clone — pull a clip off a link so it can be worked on locally.
 *
 * The download lands in the renders folder, which every post-production
 * screen already accepts as input, so a cloned clip goes straight into
 * Cut & Merge, Phụ đề, or Upscale without another step.
 *
 * "Clone" here means fetch, not regenerate: nothing is sent to Flow and no
 * credits are spent.
 */

const HEIGHT_OPTIONS = [
  { value: "720", label: "720p (nhẹ)" },
  { value: "1080", label: "1080p" },
  { value: "1440", label: "1440p" },
  { value: "2160", label: "4K (nặng)" },
];

interface Preview {
  title: string | null;
  uploader: string | null;
  durationSeconds: number | null;
  thumbnail: string | null;
}

function prettyDuration(s: number | null): string {
  if (!s || s <= 0) return "";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function VideoCloneTab() {
  // Remembered across reloads, the way the packaged tool keeps
  // `data_clone.json`: the link and the target height are what someone would
  // otherwise re-type every session. The transient things below — preview,
  // result, error — are deliberately NOT remembered: showing yesterday's
  // result beside today's empty box reads as if it had just run.
  const [saved, saveTab] = useTabState("video-clone", { url: "", height: "1080" });
  const url = saved.url;
  const height = saved.height;
  const setUrl = (next: string) => saveTab({ url: next });
  const setHeight = (next: string) => saveTab({ height: next });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<CloneResult | null>(null);
  const [busy, setBusy] = useState<"probe" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function probe() {
    setBusy("probe");
    setError(null);
    setPreview(null);
    setResult(null);
    try {
      setPreview(await cloneVideoPreview({ url }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đọc được link.");
    } finally {
      setBusy(null);
    }
  }

  async function download() {
    setBusy("download");
    setError(null);
    setResult(null);
    try {
      setResult(await cloneVideo({ url, max_height: Number(height) }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Tải thất bại.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Card>
        <h2 className="text-[13px] font-extrabold text-ink">🔗 Dán link video</h2>
        <p className="mt-1 text-[12px] text-ink-dim">
          Tải clip về máy để cắt, ghép, làm phụ đề hay upscale. Chỉ là tải
          xuống — không gửi gì lên Flow và không tốn credit.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://..."
            className="h-9 min-w-[280px] flex-1 rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink placeholder:text-ink-dim"
          />
          <LabeledSelect
            label="Chất lượng tối đa"
            value={height}
            onChange={setHeight}
            options={HEIGHT_OPTIONS}
            className="min-w-[170px]"
          />
          <GhostButton onClick={() => void probe()}>
            {busy === "probe" ? "Đang xem…" : "👁 Xem trước"}
          </GhostButton>
          <PrimaryButton
            onClick={() => void download()}
            disabled={url.trim().length === 0}
            loading={busy === "download"}
          >
            ⬇ TẢI VỀ
          </PrimaryButton>
        </div>
      </Card>

      {preview && (
        <Card>
          <div className="flex flex-wrap gap-4">
            {preview.thumbnail && (
              <img
                src={preview.thumbnail}
                alt=""
                className="h-[90px] rounded-(--radius-ctl) border border-line object-cover"
              />
            )}
            <div className="min-w-0">
              <p className="text-[13px] font-semibold text-ink">
                {preview.title || "(không có tiêu đề)"}
              </p>
              <p className="mt-1 text-[12px] text-ink-mute">
                {preview.uploader || "—"}
                {preview.durationSeconds
                  ? ` · ${prettyDuration(preview.durationSeconds)}`
                  : ""}
              </p>
            </div>
          </div>
        </Card>
      )}

      {(busy === "download" || error || result) && (
        <Card>
          {busy === "download" && (
            <p className="text-[13px] text-ink-mute">
              Đang tải… video dài có thể mất vài phút. Cứ để tab này mở.
            </p>
          )}
          {error && <p className="text-[13px] text-danger">{error}</p>}
          {result && (
            <div>
              <p className="text-[13px] font-semibold text-ok">✅ Đã tải xong</p>
              <video
                src={result.url}
                controls
                className="mt-3 max-h-[360px] rounded-(--radius-ctl) border border-line"
              />
              <p className="mt-2 text-[11px] text-ink-dim">
                {result.name} · {prettySize(result.sizeBytes)}
                {result.durationSeconds
                  ? ` · ${prettyDuration(result.durationSeconds)}`
                  : ""}{" "}
                ·{" "}
                <a
                  href={result.url}
                  download={result.name}
                  className="text-primary underline"
                >
                  Tải về máy
                </a>
              </p>
              <p className="mt-2 text-[12px] text-ink-mute">
                Clip này giờ đã có trong thư viện của Cut &amp; Merge, Phụ đề
                &amp; Xóa Logo và Upscale.
              </p>
            </div>
          )}
        </Card>
      )}

      <Card>
        <p className="text-[11px] text-ink-dim">
          Chỉ tải nội dung bạn có quyền sử dụng. Trang web đổi trình phát khá
          thường xuyên — nếu một link báo lỗi, nâng cấp yt-dlp trong môi trường
          agent thường là đủ.
        </p>
      </Card>
    </div>
  );
}
