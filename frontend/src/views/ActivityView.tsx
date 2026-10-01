import { useMemo, useState } from "react";
import { useActivityFeed } from "@/components/activity/useActivityFeed";
import { ActivityDetailModal } from "@/components/activity/ActivityDetailModal";
import {
  formatDuration,
  metaFor,
  relativeTime,
} from "@/components/activity/activity-meta";
import {
  batchExportUrl,
  cancelActivity,
  type ActivityListItem,
} from "@/api/client";
import { Card, GhostButton, StopButton } from "@/ui/primitives";

/** NHẬT KÝ — the full-page job log behind the top pill.
 *
 * Reuses the bell's feed (`useActivityFeed`) rather than opening a second
 * poller against the same endpoint, and the same detail modal. What is new
 * here is the page framing: a type filter, Vietnamese status wording, and
 * bulk cancel for work that is still in flight.
 */

/** Backend status → the packaged app's wording (docs/ui-spec.md).
 *
 * `timeout` deliberately reads as LỖI rather than getting a label of its own:
 * it is an auto-cancel after the polling budget runs out, and to the person
 * looking at the row it failed. `canceled` stays distinct because they did it.
 */
const STATUS_VI: Record<string, { label: string; cls: string }> = {
  queued: { label: "ĐANG CHỜ", cls: "text-ink-mute" },
  running: { label: "ĐANG TẠO", cls: "text-primary" },
  done: { label: "HOÀN THÀNH", cls: "text-ok" },
  failed: { label: "LỖI", cls: "text-danger" },
  canceled: { label: "HỦY", cls: "text-ink-dim" },
  timeout: { label: "LỖI", cls: "text-danger" },
};

function statusVi(status: string) {
  return STATUS_VI[status] ?? { label: status.toUpperCase(), cls: "text-ink-mute" };
}

const TYPE_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "tất cả" },
  { value: "gen_video", label: "Tạo video" },
  { value: "gen_video_text", label: "Video từ prompt" },
  { value: "gen_video_omni", label: "Video OMNI" },
  { value: "gen_image", label: "Tạo ảnh" },
  { value: "edit_image", label: "Sửa ảnh" },
  { value: "upload", label: "Tải ảnh lên" },
  { value: "auto_prompt", label: "Auto-Prompt" },
  { value: "vision", label: "Vision" },
];

const LIVE = new Set(["queued", "running"]);

export function ActivityView() {
  const { items, nextBeforeId, loading, runningCount, refresh, loadMore } =
    useActivityFeed(true);
  const [type, setType] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);
  const [canceling, setCanceling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filtering client-side keeps one poller and one cache: the feed already
  // holds the page, and refetching per filter would double the request rate
  // for a list the user is only narrowing visually.
  const rows = useMemo(
    () => (type ? items.filter((it) => it.type === type) : items),
    [items, type],
  );
  const liveRows = useMemo(() => rows.filter((r) => LIVE.has(r.status)), [rows]);

  async function stopAll() {
    if (liveRows.length === 0) return;
    setCanceling(true);
    setError(null);
    // Settled rows answer 409, which is not a failure worth showing — the
    // job simply finished between render and click.
    const results = await Promise.allSettled(
      liveRows.map((r) => cancelActivity(r.id)),
    );
    const failed = results.filter((r) => r.status === "rejected").length;
    if (failed === results.length && failed > 0) {
      setError("Không dừng được job nào — có thể chúng đã xong.");
    }
    await refresh();
    setCanceling(false);
  }

  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-4">
        <Card>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[15px] font-extrabold text-ink">📊 NHẬT KÝ</h1>
            <label className="flex items-center gap-2">
              <span className="text-[12px] text-ink-mute">Lọc:</span>
              <select
                value={type}
                onChange={(e) => setType(e.target.value)}
                className="h-9 rounded-(--radius-ctl) border border-grape-deep bg-grape px-3 text-[13px] font-medium text-white"
              >
                {TYPE_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <GhostButton onClick={() => void refresh()}>🔄 Làm mới</GhostButton>
            <a
              href={batchExportUrl(500, type ? [type] : undefined)}
              className="flex h-9 items-center rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink hover:bg-elevated"
            >
              📊 Xuất Excel
            </a>
            <StopButton
              onClick={() => void stopAll()}
              disabled={liveRows.length === 0 || canceling}
            >
              ⏸️ DỪNG LẠI{liveRows.length > 0 ? ` (${liveRows.length})` : ""}
            </StopButton>
            <span className="ml-auto text-[12px] text-ink-dim">
              {runningCount > 0
                ? `⏳ Đang chờ ${runningCount} job hoàn thành...`
                : `${rows.length} dòng`}
            </span>
          </div>
          {error && (
            <p className="mt-2 text-[12px] text-danger">{error}</p>
          )}
        </Card>

        <Card className="p-0">
          {loading && rows.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-ink-mute">
              Đang tải nhật ký…
            </p>
          ) : rows.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-ink-mute">
              Chưa có job nào.
            </p>
          ) : (
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-[12px] text-ink-mute">
                  <th className="px-4 py-2 font-medium">#</th>
                  <th className="px-4 py-2 font-medium">Loại</th>
                  <th className="px-4 py-2 font-medium">Trạng thái</th>
                  <th className="px-4 py-2 font-medium">Thời gian</th>
                  <th className="px-4 py-2 font-medium">Mất</th>
                  <th className="px-4 py-2 font-medium"> </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((it) => (
                  <ActivityTableRow
                    key={it.id}
                    item={it}
                    onOpen={() => setDetailId(it.id)}
                    onCanceled={() => void refresh()}
                  />
                ))}
              </tbody>
            </table>
          )}
          {nextBeforeId != null && (
            <div className="border-t border-line p-3 text-center">
              <GhostButton onClick={() => void loadMore()}>
                Tải thêm
              </GhostButton>
            </div>
          )}
        </Card>
      </div>

      <ActivityDetailModal
        activityId={detailId}
        onClose={() => setDetailId(null)}
      />
    </main>
  );
}

function ActivityTableRow({
  item,
  onOpen,
  onCanceled,
}: {
  item: ActivityListItem;
  onOpen: () => void;
  onCanceled: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const s = statusVi(item.status);
  const live = LIVE.has(item.status);

  async function stop(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try {
      await cancelActivity(item.id);
    } catch {
      // 409 = already settled; the refresh below shows the real state.
    }
    onCanceled();
    setBusy(false);
  }

  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer border-b border-line/60 hover:bg-elevated"
    >
      <td className="px-4 py-2 text-ink-dim">{item.id}</td>
      <td className="px-4 py-2 text-ink">{metaFor(item.type).label}</td>
      <td className={`px-4 py-2 font-semibold ${s.cls}`}>{s.label}</td>
      <td className="px-4 py-2 text-ink-mute">
        {item.created_at ? relativeTime(item.created_at) : ""}
      </td>
      <td className="px-4 py-2 text-ink-mute">
        {formatDuration(item.duration_ms)}
      </td>
      <td className="px-4 py-2 text-right">
        {live && (
          <button
            type="button"
            onClick={stop}
            disabled={busy}
            className="rounded-(--radius-ctl) border border-line px-2 py-1 text-[12px] text-danger hover:bg-card disabled:opacity-40"
          >
            Dừng
          </button>
        )}
      </td>
    </tr>
  );
}
