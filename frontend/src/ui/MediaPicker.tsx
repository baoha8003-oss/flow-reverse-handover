import { useEffect, useRef, useState } from "react";
import {
  listReferences,
  mediaUrl,
  uploadImage,
  uploadImageFromUrl,
  humanizeBackendError,
  type ReferenceItem,
} from "@/api/client";
import { useJobsStore } from "@/store/jobs";
import { cn } from "@/lib/utils";

/** Picks the source image every image-driven tab needs.
 *
 * Four ways in, because each covers a case the others can't: a local file,
 * a URL, the saved reference library, and the images this session just
 * generated (the common one — make an image in the image tab, animate it in
 * the video tab without a round trip through the filesystem).
 *
 * Upload needs a Flow project id, so the picker borrows the same
 * board→project binding the dispatch path uses rather than asking the caller
 * to thread one through.
 */

type Tab = "upload" | "url" | "library" | "recent";

const TABS: { id: Tab; label: string }[] = [
  { id: "upload", label: "📁 Tải ảnh lên" },
  { id: "url", label: "🔗 Từ URL" },
  { id: "library", label: "📚 Thư viện" },
  { id: "recent", label: "🕒 Vừa tạo" },
];

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export function MediaPicker({
  value,
  onChange,
  label = "🖼 Ảnh nguồn",
  className,
}: {
  /** Selected Flow media id, or null when nothing is picked yet. */
  value: string | null;
  onChange: (mediaId: string | null) => void;
  label?: string;
  className?: string;
}) {
  const [tab, setTab] = useState<Tab>("upload");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [refs, setRefs] = useState<ReferenceItem[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const ensureProject = useJobsStore((s) => s.ensureProject);
  // Images this session produced, newest first — the most likely source.
  const recent = useJobsStore((s) => s.jobs);
  const recentImages = recent
    .filter((j) => j.tool === "gen_image" && j.mediaIds.length > 0)
    .flatMap((j) => j.mediaIds)
    .slice(0, 24);

  useEffect(() => {
    if (tab !== "library" || refs !== null) return;
    let alive = true;
    void (async () => {
      try {
        const items = await listReferences({ pinned_first: true, limit: 60 });
        if (alive) setRefs(items);
      } catch {
        if (alive) setRefs([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, [tab, refs]);

  async function withProject<T>(fn: (projectId: string) => Promise<T>) {
    setError(null);
    setBusy(true);
    try {
      const projectId = await ensureProject();
      if (!projectId) {
        setError("Chưa có dự án Flow — mở tab Flow rồi thử lại.");
        return;
      }
      await fn(projectId);
    } catch (e) {
      const raw = String(e);
      setError(humanizeBackendError(raw) ?? `Tải ảnh thất bại: ${raw}`);
    } finally {
      setBusy(false);
    }
  }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setError("Ảnh quá lớn (tối đa 20 MB).");
      return;
    }
    void withProject(async (projectId) => {
      const res = await uploadImage(file, projectId);
      onChange(res.media_id);
    });
  }

  function onUrl() {
    const trimmed = url.trim();
    if (!trimmed) return;
    void withProject(async (projectId) => {
      const res = await uploadImageFromUrl(trimmed, projectId);
      onChange(res.media_id);
      setUrl("");
    });
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-center gap-2">
        <span className="text-[12px] text-ink-mute">{label}</span>
        {value && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="text-[11px] text-danger hover:underline"
          >
            Bỏ chọn
          </button>
        )}
      </div>

      {value ? (
        <div className="flex items-start gap-3">
          <img
            src={mediaUrl(value)}
            alt=""
            className="h-32 rounded-md border border-line bg-black object-contain"
          />
          <span className="text-[11px] text-ink-dim">Đã chọn ảnh</span>
        </div>
      ) : (
        <div className="rounded-(--radius-card) border border-line bg-card p-3">
          <div className="mb-2 flex flex-wrap gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={cn(
                  "rounded-(--radius-ctl) px-2 py-1 text-[12px]",
                  tab === t.id
                    ? "bg-grape text-white"
                    : "text-ink-mute hover:bg-elevated",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "upload" && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
                className="h-9 rounded-(--radius-ctl) border border-line bg-elevated px-3 text-[13px] text-ink disabled:opacity-50"
              >
                {busy ? "Đang tải…" : "Chọn ảnh từ máy"}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                hidden
                onChange={onFile}
              />
              <span className="text-[11px] text-ink-dim">Tối đa 20 MB</span>
            </div>
          )}

          {tab === "url" && (
            <div className="flex items-center gap-2">
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://…"
                className="h-9 min-w-0 flex-1 rounded-(--radius-ctl) border border-line bg-surface px-3 text-[13px] text-ink placeholder:text-ink-dim"
              />
              <button
                type="button"
                disabled={busy || url.trim().length === 0}
                onClick={onUrl}
                className="h-9 rounded-(--radius-ctl) border border-line bg-elevated px-3 text-[13px] text-ink disabled:opacity-50"
              >
                {busy ? "Đang tải…" : "Lấy ảnh"}
              </button>
            </div>
          )}

          {tab === "library" && (
            <MediaGrid
              ids={(refs ?? []).map((r) => r.mediaId)}
              loading={refs === null}
              empty="Thư viện chưa có ảnh nào."
              onPick={onChange}
            />
          )}

          {tab === "recent" && (
            <MediaGrid
              ids={recentImages}
              loading={false}
              empty="Chưa tạo ảnh nào trong phiên này."
              onPick={onChange}
            />
          )}
        </div>
      )}

      {error && <div className="text-[12px] text-danger">{error}</div>}
    </div>
  );
}

function MediaGrid({
  ids,
  loading,
  empty,
  onPick,
}: {
  ids: string[];
  loading: boolean;
  empty: string;
  onPick: (mediaId: string) => void;
}) {
  if (loading) {
    return <div className="text-[12px] text-ink-dim">Đang tải…</div>;
  }
  if (ids.length === 0) {
    return <div className="text-[12px] text-ink-dim">{empty}</div>;
  }
  return (
    <div className="flex max-h-56 flex-wrap gap-2 overflow-y-auto">
      {ids.map((id) => (
        <button
          key={id}
          type="button"
          onClick={() => onPick(id)}
          className="rounded-md border border-line hover:border-accent"
        >
          <img
            src={mediaUrl(id)}
            alt=""
            className="h-24 w-24 rounded-md bg-black object-cover"
          />
        </button>
      ))}
    </div>
  );
}
