import { useState } from "react";
import { Card, GhostButton, LabeledSelect, PrimaryButton } from "@/ui/primitives";
import { ClipPicker } from "@/features/postprod/ClipPicker";
import {
  prettySize,
  stampedName,
  useLibrary,
} from "@/features/postprod/useLibrary";
import {
  burnSubtitles,
  delogo,
  saveSrt,
  transcribe,
  type LibraryItem,
  type RenderResult,
} from "@/api/client";

/** Phụ Đề & Xóa Logo.
 *
 * Subtitles come in two ways: let Gemini transcribe the clip's speech, or
 * paste an .srt you already have. Either way the text lands in the same
 * editable box before it is burned in, because burning is irreversible.
 *
 * "Xóa logo" is ffmpeg's `delogo`, which rebuilds the rectangle from the
 * pixels around it. It hides a mark; it does not recover what was behind it.
 * Saying so on the screen is fairer than letting the result surprise people.
 */

type Mode = "subtitle" | "logo";

export function SubtitleLogoTab() {
  const { status, sources, renders, loading, error, refresh } = useLibrary();
  const [mode, setMode] = useState<Mode>("subtitle");
  const [selected, setSelected] = useState<string[]>([]);
  const [srtText, setSrtText] = useState("");
  const [language, setLanguage] = useState("Tiếng Việt");
  const [size, setSize] = useState(48);
  const [box, setBox] = useState({ x: 24, y: 24, width: 220, height: 70 });
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<RenderResult | null>(null);

  const pool: LibraryItem[] = [...renders, ...sources];
  const video = selected[0];

  async function guarded(label: string, job: () => Promise<void>) {
    setBusy(label);
    setFailure(null);
    try {
      await job();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : `${label} thất bại.`);
    } finally {
      setBusy(null);
    }
  }

  const autoTranscribe = () =>
    guarded("Nghe và ghi lời", async () => {
      const res = await transcribe({
        video: video!,
        name: stampedName("phude", "srt"),
        language: language || null,
      });
      setSrtText(res.srt);
    });

  const burn = () =>
    guarded("Khắc phụ đề", async () => {
      setResult(null);
      const { path } = await saveSrt({
        name: stampedName("phude", "srt"),
        text: srtText,
      });
      const out = await burnSubtitles({
        video: video!,
        srt: path,
        output: stampedName("phude", "mp4"),
        size,
      });
      setResult(out);
      await refresh();
    });

  const removeLogo = () =>
    guarded("Xóa logo", async () => {
      setResult(null);
      const out = await delogo({
        video: video!,
        output: stampedName("xoalogo", "mp4"),
        ...box,
      });
      setResult(out);
      await refresh();
    });

  if (loading) {
    return (
      <Card>
        <p className="text-[13px] text-ink-mute">Đang tải thư viện…</p>
      </Card>
    );
  }

  if (status && !status.ffmpegAvailable) {
    return (
      <Card>
        <p className="text-[13px] text-danger">
          Không tìm thấy ffmpeg — tab này cần ffmpeg để khắc phụ đề và xoá logo.
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Card>
        <div className="flex flex-wrap gap-2">
          {(
            [
              ["subtitle", "📝 Phụ đề"],
              ["logo", "🚫 Xóa logo"],
            ] as [Mode, string][]
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => {
                setMode(id);
                setFailure(null);
              }}
              className={`h-9 rounded-(--radius-ctl) border px-3 text-[13px] ${
                mode === id
                  ? "border-primary bg-grape text-white"
                  : "border-line bg-card text-ink hover:bg-elevated"
              }`}
            >
              {label}
            </button>
          ))}
          <GhostButton onClick={() => void refresh()}>🔄 Làm mới</GhostButton>
        </div>
      </Card>

      <Card>
        <h2 className="text-[13px] font-extrabold text-ink">🎥 Chọn video</h2>
        <div className="mt-3">
          <ClipPicker
            items={pool}
            selected={selected}
            onToggle={(p) => setSelected((s) => (s[0] === p ? [] : [p]))}
            emptyNote="Chưa có video nào trong thư viện."
          />
        </div>
      </Card>

      {mode === "subtitle" && (
        <Card>
          <div className="flex flex-wrap items-end gap-3">
            <LabeledSelect
              label="Ngôn ngữ phụ đề"
              value={language}
              onChange={setLanguage}
              options={[
                { value: "Tiếng Việt", label: "Tiếng Việt" },
                { value: "English", label: "English" },
                { value: "", label: "Theo tiếng trong video" },
              ]}
              className="min-w-[200px]"
            />
            <PrimaryButton
              onClick={() => void autoTranscribe()}
              disabled={!video || !status?.ttsKeyAvailable}
              loading={busy === "Nghe và ghi lời"}
            >
              🎧 NGHE & TỰ VIẾT PHỤ ĐỀ
            </PrimaryButton>
            {!status?.ttsKeyAvailable && (
              <span className="text-[12px] text-warn">
                Cần Gemini API key (Settings → AI Providers) để tự viết phụ đề.
              </span>
            )}
          </div>

          <div className="mt-3 text-[12px] text-ink-mute">
            Sửa thoải mái trước khi khắc — khắc rồi là không gỡ ra được.
          </div>
          <textarea
            value={srtText}
            onChange={(e) => setSrtText(e.target.value)}
            placeholder={"1\n00:00:00,000 --> 00:00:03,000\nDòng phụ đề đầu tiên"}
            className="mt-2 h-48 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 font-mono text-[12px] text-ink placeholder:text-ink-dim"
          />
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">Cỡ chữ</span>
              <input
                type="number"
                min={1}
                max={400}
                value={size}
                onChange={(e) => setSize(Number(e.target.value) || 48)}
                className="h-9 w-[100px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <PrimaryButton
              onClick={() => void burn()}
              disabled={!video || srtText.trim().length === 0}
              loading={busy === "Khắc phụ đề"}
            >
              🔥 KHẮC VÀO VIDEO
            </PrimaryButton>
          </div>
        </Card>
      )}

      {mode === "logo" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">
            🚫 Vùng cần xoá
          </h2>
          <p className="mt-1 text-[12px] text-ink-dim">
            Nhập toạ độ khung chữ nhật phủ lên logo (đơn vị pixel, gốc ở góc
            trên bên trái). Phủ vừa khít thôi — khung càng to thì vùng bị làm
            nhoè càng rộng.
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            {(
              [
                ["x", "X"],
                ["y", "Y"],
                ["width", "Rộng"],
                ["height", "Cao"],
              ] as [keyof typeof box, string][]
            ).map(([key, label]) => (
              <label key={key} className="flex flex-col gap-1">
                <span className="text-[12px] text-ink-mute">{label}</span>
                <input
                  type="number"
                  min={key === "width" || key === "height" ? 1 : 0}
                  value={box[key]}
                  onChange={(e) =>
                    setBox((b) => ({
                      ...b,
                      [key]: Math.max(0, Number(e.target.value) || 0),
                    }))
                  }
                  className="h-9 w-[95px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
                />
              </label>
            ))}
            <PrimaryButton
              onClick={() => void removeLogo()}
              disabled={!video || box.width < 1 || box.height < 1}
              loading={busy === "Xóa logo"}
            >
              🚫 XOÁ LOGO
            </PrimaryButton>
          </div>
          <p className="mt-3 text-[11px] text-warn">
            ⚠ Cách này dựng lại vùng đó từ các điểm ảnh xung quanh — nó che
            được logo chứ không khôi phục được ảnh gốc phía sau. Nền càng trơn
            thì càng khó nhận ra.
          </p>
        </Card>
      )}

      {error && (
        <Card>
          <p className="text-[12px] text-danger">{error}</p>
        </Card>
      )}

      {(busy || failure || result) && (
        <Card>
          {busy && (
            <p className="text-[13px] text-ink-mute">
              {busy}… clip dài có thể mất vài phút.
            </p>
          )}
          {failure && <p className="text-[13px] text-danger">{failure}</p>}
          {result && <ResultBlock result={result} renders={renders} />}
        </Card>
      )}
    </div>
  );
}

function ResultBlock({
  result,
  renders,
}: {
  result: RenderResult;
  renders: LibraryItem[];
}) {
  // The API returns an absolute filesystem path, which a browser cannot open;
  // the refetched render list carries the served URL.
  const item = renders.find((r) => r.path === result.path);
  return (
    <div>
      <p className="text-[13px] font-semibold text-ok">✅ Xong</p>
      {item?.url ? (
        <div className="mt-3">
          <video
            src={item.url}
            controls
            className="max-h-[360px] rounded-(--radius-ctl) border border-line"
          />
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
