import type { LibraryItem } from "@/api/client";
import { previewUrl, prettySize } from "./useLibrary";
import { cn } from "@/lib/utils";

/** Pick files the backend will actually accept.
 *
 * Post-production endpoints take filesystem paths confined to the app's
 * storage and asset roots, so the UI offers the real files rather than a
 * free-text path the server would reject. Selection is by path because that
 * is the value every endpoint wants.
 *
 * `order` turns the checkmark into a position number: concat joins clips in
 * the order given, so "which one is second" has to be visible.
 */
export function ClipPicker({
  items,
  selected,
  onToggle,
  kind = "video",
  ordered = false,
  emptyNote,
}: {
  items: LibraryItem[];
  selected: string[];
  onToggle: (path: string) => void;
  kind?: "video" | "image" | "audio";
  ordered?: boolean;
  emptyNote?: string;
}) {
  const visible = items.filter((i) => i.kind === kind);

  if (visible.length === 0) {
    return (
      <p className="text-[12px] text-ink-dim">
        {emptyNote ?? "Chưa có file nào dùng được."}
      </p>
    );
  }

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
      {visible.map((item) => {
        const at = selected.indexOf(item.path);
        const on = at >= 0;
        const url = previewUrl(item);
        return (
          <button
            key={item.path}
            type="button"
            onClick={() => onToggle(item.path)}
            className={cn(
              "relative overflow-hidden rounded-(--radius-ctl) border bg-card text-left",
              on ? "border-primary" : "border-line hover:border-ink-dim",
            )}
          >
            <div className="flex h-[84px] items-center justify-center bg-bg">
              {kind === "image" && url ? (
                <img
                  src={url}
                  alt={item.name}
                  className="h-full w-full object-cover"
                />
              ) : kind === "video" && url ? (
                // No autoplay and no preload: a grid of clips would otherwise
                // start fetching every file the moment the tab opens.
                <video src={url} className="h-full w-full object-cover" preload="metadata" />
              ) : (
                <span className="text-[22px]">
                  {kind === "audio" ? "🎵" : "🎬"}
                </span>
              )}
            </div>
            {on && (
              <span className="absolute right-1 top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-[11px] font-bold text-white">
                {ordered ? at + 1 : "✓"}
              </span>
            )}
            <div className="px-2 py-1">
              <p className="truncate text-[11px] text-ink" title={item.name}>
                {item.name}
              </p>
              <p className="text-[10px] text-ink-dim">
                {prettySize(item.sizeBytes)}
              </p>
            </div>
          </button>
        );
      })}
    </div>
  );
}
