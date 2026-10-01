import { useState } from "react";
import { Card, GhostButton, LabeledSelect, PrimaryButton } from "@/ui/primitives";
import { ClipPicker } from "@/features/postprod/ClipPicker";
import {
  prettySize,
  stampedName,
  useLibrary,
} from "@/features/postprod/useLibrary";
import { upscaleMedia, type LibraryItem, type RenderResult } from "@/api/client";

/** Upscale — RealESRGAN running locally, offline, for free.
 *
 * The scale is NOT a user choice, deliberately. The bundled models are fixed
 * 4x networks and the binary happily accepts `-s 2` against one: it exits 0
 * and writes a correctly sized image of corrupted pixels (measured 4.5 dB
 * PSNR against 26 dB at `-s 4`). The backend rejects that combination, and
 * offering a "2K" option here would just be a button that always errors. The
 * ratio therefore comes from the model name, and an arbitrary output size is
 * reached by resampling afterwards via `targetHeight` — which the video path
 * supports and the image path does not.
 */

/** Mirrors the backend's `_MODEL_SCALE_RE`: the ratio lives in the name. */
export function modelScale(model: string): number {
  const m = /x(\d+)|(\d+)x/.exec(model);
  const raw = m?.[1] ?? m?.[2];
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 4;
}

const HEIGHT_OPTIONS = [
  { value: "0", label: "Giữ nguyên (theo model)" },
  { value: "1080", label: "1080p" },
  { value: "1440", label: "1440p" },
  { value: "2160", label: "4K (2160p)" },
];

export function UpscaleTab({ kind }: { kind: "image" | "video" }) {
  const { status, sources, renders, loading, error, refresh } = useLibrary();
  const [selected, setSelected] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [height, setHeight] = useState("0");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<RenderResult | null>(null);

  const pool: LibraryItem[] = [...renders, ...sources];
  const models = status?.upscaleModels ?? [];
  const activeModel = model || models[0] || "";
  const scale = modelScale(activeModel);

  if (loading) {
    return (
      <Card>
        <p className="text-[13px] text-ink-mute">Đang tải thư viện…</p>
      </Card>
    );
  }

  if (status && !status.upscaleAvailable) {
    return (
      <Card>
        <p className="text-[13px] text-danger">
          Không tìm thấy RealESRGAN trong thư mục asset, nên chưa upscale được.
          Cần binary realesrgan-ncnn-vulkan kèm thư mục models.
        </p>
      </Card>
    );
  }

  if (kind === "video" && status && !status.ffmpegAvailable) {
    return (
      <Card>
        <p className="text-[13px] text-danger">
          Upscale video cần ffmpeg để tách và ghép lại khung hình, mà hiện không
          tìm thấy ffmpeg.
        </p>
      </Card>
    );
  }

  async function run() {
    setBusy(true);
    setFailure(null);
    setResult(null);
    try {
      const targetHeight =
        kind === "video" && height !== "0" ? Number(height) : null;
      const res = await upscaleMedia({
        source: selected[0]!,
        output: stampedName("upscale", kind === "video" ? "mp4" : "png"),
        scale,
        model: activeModel,
        targetHeight,
        kind,
      });
      setResult(res);
      await refresh();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Upscale thất bại.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-[13px] font-extrabold text-ink">
            {kind === "video" ? "🎥 Chọn video" : "📸 Chọn ảnh"}
          </h2>
          <GhostButton onClick={() => void refresh()}>🔄 Làm mới</GhostButton>
        </div>
        <p className="mt-1 text-[12px] text-ink-dim">
          Chạy bằng RealESRGAN trên máy này — offline, không tốn credit.
          {kind === "video" && " Tiếng của video gốc được giữ nguyên."}
        </p>
        <div className="mt-3">
          <ClipPicker
            items={pool}
            kind={kind}
            selected={selected}
            onToggle={(p) => setSelected((s) => (s[0] === p ? [] : [p]))}
            emptyNote={
              kind === "video"
                ? "Chưa có video nào trong thư viện."
                : "Chưa có ảnh nào trong thư viện."
            }
          />
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <LabeledSelect
            label="Mô hình AI"
            value={activeModel}
            onChange={setModel}
            options={models.map((m) => ({ value: m, label: m }))}
            className="min-w-[240px]"
          />
          {kind === "video" && (
            <LabeledSelect
              label="Chiều cao đích"
              value={height}
              onChange={setHeight}
              options={HEIGHT_OPTIONS}
              className="min-w-[200px]"
            />
          )}
          <PrimaryButton
            onClick={() => void run()}
            disabled={selected.length === 0 || !activeModel}
            loading={busy}
          >
            🚀 CHẠY UPSCALE
          </PrimaryButton>
          <span className="text-[12px] text-ink-dim">
            Phóng {scale}× theo model
            {kind === "video" && height !== "0"
              ? `, rồi co về ${height}p`
              : ""}
          </span>
        </div>
        {kind === "video" && (
          <p className="mt-3 text-[11px] text-ink-dim">
            Mỗi khung hình được ghi ra PNG trước khi xử lý, nên clip dài rất tốn
            đĩa và thời gian — server chặn nguồn dài quá 5 phút.
          </p>
        )}
      </Card>

      {error && (
        <Card>
          <p className="text-[12px] text-danger">{error}</p>
        </Card>
      )}

      {(busy || result || failure) && (
        <Card>
          {busy && (
            <p className="text-[13px] text-ink-mute">
              Đang upscale… việc này chạy đồng bộ và có thể mất vài phút. Cứ để
              tab này mở.
            </p>
          )}
          {failure && <p className="text-[13px] text-danger">{failure}</p>}
          {result && <UpscaleResult result={result} renders={renders} />}
        </Card>
      )}
    </div>
  );
}

function UpscaleResult({
  result,
  renders,
}: {
  result: RenderResult;
  renders: LibraryItem[];
}) {
  // The API answers with an absolute filesystem path, which a browser cannot
  // open; the refetched render list carries the served URL for it.
  const item = renders.find((r) => r.path === result.path);
  return (
    <div>
      <p className="text-[13px] font-semibold text-ok">✅ Hoàn thành!</p>
      {item?.url ? (
        <div className="mt-3">
          {item.kind === "video" ? (
            <video
              src={item.url}
              controls
              className="max-h-[360px] rounded-(--radius-ctl) border border-line"
            />
          ) : (
            <img
              src={item.url}
              alt={item.name}
              className="max-h-[360px] rounded-(--radius-ctl) border border-line"
            />
          )}
          <p className="mt-2 text-[11px] text-ink-dim">
            {item.name} · {prettySize(item.sizeBytes)} ·{" "}
            <a
              href={item.url}
              download={item.name}
              className="text-primary underline"
            >
              Tải về
            </a>
          </p>
        </div>
      ) : (
        <p className="mt-2 break-all text-[11px] text-ink-dim">{result.path}</p>
      )}
    </div>
  );
}
