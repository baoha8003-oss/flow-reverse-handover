import { useEffect, useState } from "react";
import {
  generateGrok,
  getGrokStatus,
  mediaUrl,
  probeGrok,
} from "@/api/client";
import { MediaPicker } from "@/ui/MediaPicker";

/** Full-width views for the non-VEO3 pills. */

/** GROK — images and video through the user's own grok.com session.
 *
 * Mined from the packaged tool: it never touches `api.x.ai`. It drives a
 * signed-in Chrome and calls grok.com's own endpoints in four modes
 * (create-image / image-to-image / text-to-video / image-to-video). This does
 * the same through the extension's page bridge — no API key, no second
 * browser — which is why an open, signed-in grok.com tab is required.
 */
export function GrokView() {
  const [status, setStatus] = useState<{
    bridgeReady: boolean;
    note: string;
  } | null>(null);
  const [mode, setMode] = useState<"image" | "video">("image");
  const [prompt, setPrompt] = useState("");
  const [refId, setRefId] = useState<string | null>(null);
  const [busy, setBusy] = useState<"probe" | "gen" | null>(null);
  const [urls, setUrls] = useState<string[]>([]);
  const [shape, setShape] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getGrokStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  async function run(kind: "probe" | "gen") {
    setBusy(kind);
    setError(null);
    if (kind === "gen") setUrls([]);
    else setShape(null);
    try {
      if (kind === "probe") {
        setShape((await probeGrok()).keys);
      } else {
        const r = await generateGrok({
          prompt,
          video: mode === "video",
          ref_media_ids: refId ? [refId] : [],
        });
        setUrls(r.urls);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gọi Grok thất bại.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
      <div className="mx-auto flex max-w-[860px] flex-col gap-4">
        <div className="rounded-(--radius-card) border border-line bg-surface p-5">
          <h1 className="text-[16px] font-extrabold text-ink">⚡ GROK</h1>
          <p className="mt-2 text-[13px] leading-relaxed text-ink-mute">
            Chạy bằng <b>phiên đăng nhập grok.com của bạn</b>, không cần API key
            — đúng cách bản đóng gói làm. Khác ở chỗ nó phải lái một Chrome
            riêng, còn bản này dùng extension đã có sẵn.
          </p>
          <p className="mt-2 text-[12px] text-warn">
            ⚠ Cần mở sẵn một tab <b>grok.com</b> đã đăng nhập. Việc này tiêu
            quota tài khoản Grok của bạn, không phải credit Flow.
          </p>
          {status && <p className="mt-2 text-[12px] text-ink-dim">{status.note}</p>}
        </div>

        <div className="rounded-(--radius-card) border border-line bg-surface p-5">
          <div className="flex flex-wrap items-center gap-2">
            {(["image", "video"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`h-9 rounded-(--radius-ctl) border px-3 text-[13px] ${
                  mode === m
                    ? "border-primary bg-grape text-white"
                    : "border-line bg-card text-ink hover:bg-elevated"
                }`}
              >
                {m === "image" ? "🎨 Tạo ảnh" : "🎬 Tạo video"}
              </button>
            ))}
            <span className="text-[11px] text-ink-dim">
              Thêm ảnh tham chiếu bên dưới để thành image-to-
              {mode === "image" ? "image" : "video"}.
            </span>
          </div>

          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Mô tả thứ bạn muốn Grok tạo..."
            className="mt-3 h-28 w-full resize-y rounded-(--radius-ctl) border border-line bg-card p-3 text-[13px] text-ink placeholder:text-ink-dim"
          />

          <div className="mt-3">
            {refId ? (
              <div className="flex items-center gap-3">
                <img
                  src={mediaUrl(refId)}
                  alt=""
                  className="h-20 w-20 rounded-(--radius-ctl) border border-line bg-black object-cover"
                />
                <button
                  type="button"
                  onClick={() => setRefId(null)}
                  className="h-9 rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink hover:bg-elevated"
                >
                  Bỏ ảnh
                </button>
              </div>
            ) : (
              <MediaPicker
                label="🖼 Ảnh tham chiếu (tuỳ chọn)"
                value={null}
                onChange={(id) => id && setRefId(id)}
              />
            )}
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void run("gen")}
              disabled={busy !== null || prompt.trim().length === 0}
              className="h-9 rounded-(--radius-ctl) bg-gradient-to-r from-primary to-[#38bdf8] px-4 text-[13px] font-semibold text-white disabled:opacity-50"
            >
              {busy === "gen" ? "Đang tạo…" : "TẠO"}
            </button>
            <button
              type="button"
              onClick={() => void run("probe")}
              disabled={busy !== null}
              className="h-9 rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink hover:bg-elevated disabled:opacity-40"
            >
              🔍 Dò kết nối
            </button>
          </div>
          {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
        </div>

        {shape && (
          <div className="rounded-(--radius-card) border border-line bg-surface p-5">
            <div className="text-[13px] font-semibold text-ink">
              Grok trả về các trường:
            </div>
            <p className="mt-1 break-words text-[12px] text-ink-mute">
              {shape.length ? shape.join(", ") : "(không có object nào)"}
            </p>
            <p className="mt-2 text-[11px] text-ink-dim">
              Hình dạng phản hồi là thứ duy nhất không đọc được từ file exe, nên
              đây là bằng chứng thật để chỉnh phần đọc kết quả cho khớp.
            </p>
          </div>
        )}

        {urls.length > 0 && (
          <div className="rounded-(--radius-card) border border-line bg-surface p-5">
            <div className="text-[13px] font-semibold text-ok">
              ✅ {urls.length} kết quả
            </div>
            <div className="mt-3 flex flex-wrap gap-3">
              {urls.map((u) =>
                u.toLowerCase().endsWith(".mp4") ? (
                  <video
                    key={u}
                    src={u}
                    controls
                    className="max-h-[300px] rounded-(--radius-ctl) border border-line"
                  />
                ) : (
                  <img
                    key={u}
                    src={u}
                    alt=""
                    className="max-h-[300px] rounded-(--radius-ctl) border border-line"
                  />
                ),
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

/** ỦNG HỘ TÁC GIẢ — static page. The QR images are copied into
 * `public/sponsor/` so Vite serves them; importing straight out of `docs/`
 * would reach outside the frontend root the dev server may read from. */
export function SponsorView() {
  const cards = [
    {
      src: "/sponsor/sponsor-qr-vn.jpg",
      title: "📱 Việt Nam",
      note: "MoMo · VietQR · napas247",
    },
    {
      src: "/sponsor/sponsor-qr-binance.png",
      title: "💰 Binance Pay",
      note: "Crypto / xuyên biên giới",
    },
  ];
  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-5 py-8">
      <div className="mx-auto max-w-[760px] text-center">
        <h1 className="text-[18px] font-extrabold text-ink">
          ❤️ ỦNG HỘ TÁC GIẢ
        </h1>
        <p className="mx-auto mt-2 max-w-[520px] text-[13px] leading-relaxed text-ink-mute">
          Tool chạy hoàn toàn trên máy bạn và không thu phí. Nếu nó giúp được
          việc, một ly cà phê là quá đủ để tiếp sức.
        </p>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {cards.map((c) => (
            <div
              key={c.src}
              className="rounded-(--radius-card) border border-line bg-surface p-4"
            >
              <img
                src={c.src}
                alt={c.title}
                className="mx-auto w-full max-w-[240px] rounded-(--radius-ctl)"
              />
              <div className="mt-3 text-[13px] font-semibold text-ink">
                {c.title}
              </div>
              <div className="text-[12px] text-ink-dim">{c.note}</div>
            </div>
          ))}
        </div>
        <p className="mt-6 text-[12px] text-ink-dim">
          Không ủng hộ cũng không sao — mọi tính năng đều mở, không có bản khoá.
        </p>
      </div>
    </main>
  );
}
