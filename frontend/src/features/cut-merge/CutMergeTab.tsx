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
  concatClips,
  cutVideo,
  grabThumbnail,
  overlayTitle,
  fitNarration,
  mixBgm,
  narrate,
  overlayLogo,
  saveSrt,
  type LibraryItem,
  type RenderResult,
} from "@/api/client";

/** Cut & Merge Video — the local post-production suite.
 *
 * Everything here runs on this machine through ffmpeg and (for narration) one
 * Gemini TTS call with the user's own key. None of it touches Google Flow, so
 * it works with no session and never spends credits.
 *
 * Each step writes into the renders folder and the library is refetched
 * afterwards, which is what lets outputs feed the next step — ghép → phụ đề →
 * logo → nhạc nền is one chain without leaving the tab.
 *
 * Every endpoint is synchronous: the HTTP request blocks for the whole
 * re-encode and there is no job id to poll. A long clip therefore leaves the
 * button spinning for minutes, so each step says so rather than looking hung.
 */

type StepId =
  | "merge"
  | "cut"
  | "subtitles"
  | "logo"
  | "title"
  | "bgm"
  | "narrate";

const STEPS: { id: StepId; label: string }[] = [
  { id: "merge", label: "🔗 Ghép video" },
  { id: "cut", label: "✂️ Cắt video" },
  { id: "subtitles", label: "📝 Phụ đề" },
  { id: "logo", label: "🖼 Logo" },
  { id: "title", label: "🏷 Tiêu đề & ảnh bìa" },
  { id: "bgm", label: "🎶 Nhạc nền" },
  { id: "narrate", label: "🗣️ Lồng tiếng" },
];

/** One video is chosen per step; concat is the exception and takes many. */
function pickOne(selected: string[], path: string): string[] {
  return selected[0] === path ? [] : [path];
}

function toggleMany(selected: string[], path: string): string[] {
  return selected.includes(path)
    ? selected.filter((p) => p !== path)
    : [...selected, path];
}

export function CutMergeTab() {
  const { status, sources, renders, loading, error, refresh } = useLibrary();
  const [step, setStep] = useState<StepId>("merge");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<RenderResult | null>(null);
  const [resultName, setResultName] = useState<string | null>(null);

  // Anything already rendered is a valid input, so the two lists are offered
  // together with renders first — that is almost always what you want next.
  const videoPool: LibraryItem[] = [...renders, ...sources];

  const [mergeSel, setMergeSel] = useState<string[]>([]);
  const [videoSel, setVideoSel] = useState<string[]>([]);
  const [logoSel, setLogoSel] = useState<string[]>([]);

  const [srtText, setSrtText] = useState("");
  const [subSize, setSubSize] = useState(48);
  const [bgmTrack, setBgmTrack] = useState("");
  const [bgmVolume, setBgmVolume] = useState(0.3);
  const [origVolume, setOrigVolume] = useState(1);
  const [narrText, setNarrText] = useState("");
  const [voice, setVoice] = useState("Kore");
  const [logoX, setLogoX] = useState(40);
  const [logoY, setLogoY] = useState(40);
  const [cutSeconds, setCutSeconds] = useState(8);
  const [cutPieces, setCutPieces] = useState<LibraryItem[]>([]);
  const [titleText, setTitleText] = useState("");
  const [titleSize, setTitleSize] = useState(64);
  const [thumbAt, setThumbAt] = useState(1);
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);

  /** Cutting returns a list of pieces rather than one render, so it does not
   * go through `run` — but it shares the same busy/failure reporting. */
  async function runCut() {
    setBusy(true);
    setFailure(null);
    setResult(null);
    setCutPieces([]);
    try {
      const { pieces } = await cutVideo({
        video: videoSel[0]!,
        seconds: cutSeconds,
        name: stampedName("cat", "mp4").replace(/\.mp4$/, ""),
      });
      setCutPieces(pieces);
      await refresh();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Cắt video thất bại.");
    } finally {
      setBusy(false);
    }
  }

  /** A thumbnail is an image, not a render, so it reports separately. */
  async function runThumb() {
    setBusy(true);
    setFailure(null);
    setThumbUrl(null);
    try {
      const { url } = await grabThumbnail({
        video: videoSel[0]!,
        output: stampedName("anhbia", "jpg"),
        atSeconds: thumbAt,
      });
      setThumbUrl(url);
      await refresh();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Lấy ảnh bìa thất bại.");
    } finally {
      setBusy(false);
    }
  }

  async function run(job: () => Promise<RenderResult>, name: string) {
    setBusy(true);
    setFailure(null);
    setResult(null);
    try {
      const res = await job();
      setResult(res);
      setResultName(name);
      // The output becomes a legal input for the next step only once the
      // library has seen it.
      await refresh();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Xử lý thất bại.");
    } finally {
      setBusy(false);
    }
  }

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
          Không tìm thấy ffmpeg trong thư mục asset. Toàn bộ tab này cần ffmpeg
          nên tạm thời chưa dùng được.
        </p>
      </Card>
    );
  }

  const voices = status?.voices ?? [];

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Card>
        <div className="flex flex-wrap gap-2">
          {STEPS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setStep(s.id);
                setFailure(null);
              }}
              className={`h-9 rounded-(--radius-ctl) border px-3 text-[13px] ${
                step === s.id
                  ? "border-primary bg-grape text-white"
                  : "border-line bg-card text-ink hover:bg-elevated"
              }`}
            >
              {s.label}
            </button>
          ))}
          <GhostButton onClick={() => void refresh()}>🔄 Làm mới</GhostButton>
        </div>
        <p className="mt-2 text-[11px] text-ink-dim">
          Chạy bằng ffmpeg trên máy này — không tốn credit và không cần phiên
          Flow. Kết quả của mỗi bước dùng được làm đầu vào cho bước sau.
        </p>
      </Card>

      {error && (
        <Card>
          <p className="text-[12px] text-danger">{error}</p>
        </Card>
      )}

      {step === "merge" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">
            🔗 Chọn video muốn ghép
          </h2>
          <p className="mt-1 text-[12px] text-ink-dim">
            Bấm theo đúng thứ tự muốn ghép — số trên ảnh là vị trí. Mọi clip được
            chuẩn hoá về cùng khung hình và fps trước khi nối.
          </p>
          <div className="mt-3">
            <ClipPicker
              items={videoPool}
              selected={mergeSel}
              onToggle={(p) => setMergeSel((s) => toggleMany(s, p))}
              ordered
              emptyNote="Chưa có video nào. Tạo vài clip ở tab Text to Video trước."
            />
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <PrimaryButton
              onClick={() =>
                void run(
                  () =>
                    concatClips({
                      clips: mergeSel,
                      output: stampedName("ghep", "mp4"),
                    }),
                  "Ghép video",
                )
              }
              disabled={mergeSel.length < 2}
              loading={busy}
            >
              🚀 Ghép {mergeSel.length > 0 ? `${mergeSel.length} clip` : ""}
            </PrimaryButton>
            {mergeSel.length > 0 && (
              <GhostButton onClick={() => setMergeSel([])}>Bỏ chọn</GhostButton>
            )}
            {mergeSel.length === 1 && (
              <span className="text-[12px] text-warn">Cần ít nhất 2 clip.</span>
            )}
          </div>
        </Card>
      )}

      {step !== "merge" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">
            🎥 Chọn video nguồn
          </h2>
          {step === "cut" && (
            <p className="mt-1 text-[12px] text-ink-dim">
              Cắt thành nhiều mảnh đều nhau. Mảnh nào cũng dùng lại được ngay ở
              các bước khác.
            </p>
          )}
          <div className="mt-3">
            <ClipPicker
              items={videoPool}
              selected={videoSel}
              onToggle={(p) => setVideoSel((s) => pickOne(s, p))}
              emptyNote="Chưa có video nào."
            />
          </div>
        </Card>
      )}

      {step === "cut" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">✂️ Cắt video</h2>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">
                Độ dài mỗi mảnh (giây)
              </span>
              <input
                type="number"
                min={1}
                max={3600}
                value={cutSeconds}
                onChange={(e) =>
                  setCutSeconds(Math.max(1, Number(e.target.value) || 1))
                }
                className="h-9 w-[120px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <PrimaryButton
              onClick={() => void runCut()}
              disabled={videoSel.length === 0}
              loading={busy}
            >
              ✂️ CẮT
            </PrimaryButton>
          </div>
          {cutPieces.length > 0 && (
            <div className="mt-4">
              <p className="text-[13px] font-semibold text-ok">
                ✅ {cutPieces.length} mảnh
              </p>
              <div className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
                {cutPieces.map((p) => (
                  <div
                    key={p.path}
                    className="overflow-hidden rounded-(--radius-ctl) border border-line bg-card"
                  >
                    {p.url && (
                      <video
                        src={p.url}
                        controls
                        preload="metadata"
                        className="h-[90px] w-full bg-bg object-cover"
                      />
                    )}
                    <p className="truncate px-2 py-1 text-[11px] text-ink">
                      {p.name}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {step === "subtitles" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">📝 Phụ đề</h2>
          <p className="mt-1 text-[12px] text-ink-dim">
            Dán nội dung .srt vào đây. File được lưu kèm BOM UTF-8 để dấu tiếng
            Việt không bị vỡ khi ffmpeg khắc vào video.
          </p>
          <textarea
            value={srtText}
            onChange={(e) => setSrtText(e.target.value)}
            placeholder={"1\n00:00:00,000 --> 00:00:03,000\nDòng phụ đề đầu tiên"}
            className="mt-3 h-40 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 font-mono text-[12px] text-ink placeholder:text-ink-dim"
          />
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">Cỡ chữ</span>
              <input
                type="number"
                min={1}
                max={400}
                value={subSize}
                onChange={(e) => setSubSize(Number(e.target.value) || 48)}
                className="h-9 w-[100px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <PrimaryButton
              onClick={() =>
                void run(async () => {
                  const { path } = await saveSrt({
                    name: stampedName("phude", "srt"),
                    text: srtText,
                  });
                  return burnSubtitles({
                    video: videoSel[0]!,
                    srt: path,
                    output: stampedName("phude", "mp4"),
                    size: subSize,
                  });
                }, "Khắc phụ đề")
              }
              disabled={videoSel.length === 0 || srtText.trim().length === 0}
              loading={busy}
            >
              🚀 Khắc phụ đề
            </PrimaryButton>
          </div>
        </Card>
      )}

      {step === "logo" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">🖼 Chọn logo</h2>
          <div className="mt-3">
            <ClipPicker
              items={[...renders, ...sources]}
              kind="image"
              selected={logoSel}
              onToggle={(p) => setLogoSel((s) => pickOne(s, p))}
              emptyNote="Chưa có ảnh nào trong thư viện. Tạo ảnh ở tab Text to Image trước."
            />
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">X</span>
              <input
                type="number"
                min={0}
                value={logoX}
                onChange={(e) => setLogoX(Number(e.target.value) || 0)}
                className="h-9 w-[90px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">Y</span>
              <input
                type="number"
                min={0}
                value={logoY}
                onChange={(e) => setLogoY(Number(e.target.value) || 0)}
                className="h-9 w-[90px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <PrimaryButton
              onClick={() =>
                void run(
                  () =>
                    overlayLogo({
                      video: videoSel[0]!,
                      logo: logoSel[0]!,
                      output: stampedName("logo", "mp4"),
                      x: logoX,
                      y: logoY,
                    }),
                  "Chèn logo",
                )
              }
              disabled={videoSel.length === 0 || logoSel.length === 0}
              loading={busy}
            >
              🚀 Chèn logo
            </PrimaryButton>
          </div>
        </Card>
      )}

      {step === "title" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">
            🏷 Tiêu đề & ảnh bìa
          </h2>
          <p className="mt-1 text-[12px] text-ink-dim">
            Hai mảnh cuối của bước lắp ráp: khắc một dòng tiêu đề lên đầu video,
            và lấy một khung hình làm ảnh bìa.
          </p>
          <input
            value={titleText}
            onChange={(e) => setTitleText(e.target.value)}
            placeholder="Dòng tiêu đề hiện trên video..."
            className="mt-3 h-9 w-full rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink placeholder:text-ink-dim"
          />
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">Cỡ chữ</span>
              <input
                type="number"
                min={1}
                max={400}
                value={titleSize}
                onChange={(e) => setTitleSize(Number(e.target.value) || 64)}
                className="h-9 w-[100px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <PrimaryButton
              onClick={() =>
                void run(
                  () =>
                    overlayTitle({
                      video: videoSel[0]!,
                      output: stampedName("tieude", "mp4"),
                      text: titleText,
                      size: titleSize,
                    }),
                  "Khắc tiêu đề",
                )
              }
              disabled={videoSel.length === 0 || titleText.trim().length === 0}
              loading={busy}
            >
              🏷 KHẮC TIÊU ĐỀ
            </PrimaryButton>
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-line pt-4">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] text-ink-mute">Ảnh bìa tại giây</span>
              <input
                type="number"
                min={0}
                step={0.5}
                value={thumbAt}
                onChange={(e) => setThumbAt(Number(e.target.value) || 0)}
                className="h-9 w-[120px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
              />
            </label>
            <GhostButton onClick={() => void runThumb()}>
              🖼 LẤY ẢNH BÌA
            </GhostButton>
            {thumbUrl && (
              <a href={thumbUrl} target="_blank" rel="noopener noreferrer">
                <img
                  src={thumbUrl}
                  alt="ảnh bìa"
                  className="h-[70px] rounded-(--radius-ctl) border border-line"
                />
              </a>
            )}
          </div>
        </Card>
      )}

      {step === "bgm" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">🎶 Nhạc nền</h2>
          {(status?.bgm.length ?? 0) === 0 ? (
            <p className="mt-2 text-[12px] text-warn">
              Không tìm thấy nhạc nền trong thư viện asset (thư mục nhac_nen).
            </p>
          ) : (
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <LabeledSelect
                label="🎵 Chọn nhạc nền"
                value={bgmTrack || status!.bgm[0]!}
                onChange={setBgmTrack}
                options={status!.bgm.map((b) => ({ value: b, label: b }))}
                className="min-w-[260px]"
              />
              <label className="flex flex-col gap-1">
                <span className="text-[12px] text-ink-mute">
                  🔊 Âm lượng nhạc nền: {bgmVolume.toFixed(2)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.05}
                  value={bgmVolume}
                  onChange={(e) => setBgmVolume(Number(e.target.value))}
                  className="w-[180px]"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[12px] text-ink-mute">
                  🔊 Âm lượng video gốc: {origVolume.toFixed(2)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.05}
                  value={origVolume}
                  onChange={(e) => setOrigVolume(Number(e.target.value))}
                  className="w-[180px]"
                />
              </label>
              <PrimaryButton
                onClick={() =>
                  void run(
                    () =>
                      mixBgm({
                        video: videoSel[0]!,
                        output: stampedName("nhacnen", "mp4"),
                        track: bgmTrack || status!.bgm[0]!,
                        bgmVolume,
                        origVolume,
                      }),
                    "Trộn nhạc nền",
                  )
                }
                disabled={videoSel.length === 0}
                loading={busy}
              >
                🚀 Trộn nhạc nền
              </PrimaryButton>
            </div>
          )}
        </Card>
      )}

      {step === "narrate" && (
        <Card>
          <h2 className="text-[13px] font-extrabold text-ink">🗣️ Lồng tiếng</h2>
          {!status?.ttsKeyAvailable ? (
            <p className="mt-2 text-[12px] text-warn">
              Chưa có Gemini API key nên không tạo được giọng đọc. Thêm key vào
              data_general/gemini_api_key.txt hoặc biến môi trường GEMINI_API_KEY.
            </p>
          ) : (
            <>
              <textarea
                value={narrText}
                onChange={(e) => setNarrText(e.target.value)}
                placeholder="Nội dung cần đọc..."
                className="mt-3 h-32 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[13px] text-ink placeholder:text-ink-dim"
              />
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <LabeledSelect
                  label="Giọng đọc"
                  value={voice}
                  onChange={setVoice}
                  options={voices.map((v) => {
                    const name = String(v.name ?? "");
                    return { value: name, label: name };
                  })}
                  className="min-w-[180px]"
                />
                <PrimaryButton
                  onClick={() =>
                    void run(async () => {
                      const audio = await narrate({
                        text: narrText,
                        voice,
                        output: stampedName("giongdoc", "wav"),
                      });
                      // Fit the picture to the narration so the clip ends when
                      // the sentence does, instead of cutting mid-word.
                      return fitNarration({
                        video: videoSel[0]!,
                        audio: audio.path,
                        output: stampedName("longtieng", "mp4"),
                      });
                    }, "Lồng tiếng")
                  }
                  disabled={videoSel.length === 0 || narrText.trim().length === 0}
                  loading={busy}
                >
                  🚀 Tạo & ghép giọng đọc
                </PrimaryButton>
              </div>
            </>
          )}
        </Card>
      )}

      {(busy || result || failure) && (
        <Card>
          {busy && (
            <p className="text-[13px] text-ink-mute">
              Đang xử lý trên máy… clip dài có thể mất vài phút. Cứ để tab này
              mở.
            </p>
          )}
          {failure && <p className="text-[13px] text-danger">{failure}</p>}
          {result && (
            <div>
              <p className="text-[13px] font-semibold text-ok">
                ✅ {resultName} xong
                {result.durationSeconds
                  ? ` — ${result.durationSeconds.toFixed(1)}s`
                  : ""}
              </p>
              <ResultPreview result={result} renders={renders} />
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

/** Show the finished file. The API returns an absolute filesystem path, which
 * a browser cannot open, so match it against the freshly refetched render
 * list to get the served URL. */
function ResultPreview({
  result,
  renders,
}: {
  result: RenderResult;
  renders: LibraryItem[];
}) {
  const item = renders.find((r) => r.path === result.path);
  if (!item?.url) {
    return (
      <p className="mt-2 break-all text-[11px] text-ink-dim">{result.path}</p>
    );
  }
  return (
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
  );
}
