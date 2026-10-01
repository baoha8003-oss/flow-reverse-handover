import { useCallback, useEffect, useState } from "react";
import {
  getPostprodLibrary,
  getPostprodStatus,
  mediaUrl,
  type LibraryItem,
  type PostprodStatus,
} from "@/api/client";

/** Everything the post-production screens need to know about this machine.
 *
 * Two facts drive both screens:
 *   - what the local toolchain can do (ffmpeg / RealESRGAN / a TTS key), so a
 *     button is hidden rather than offered and then failing at click time;
 *   - which files may be used as input, because those endpoints take
 *     filesystem paths confined to allowed roots — the UI must pick a real
 *     path, never invent one.
 *
 * Renders are refetched after every job so a finished output is immediately
 * available as the input of the next step (concat → subtitles → logo).
 */
export function useLibrary() {
  const [status, setStatus] = useState<PostprodStatus | null>(null);
  const [sources, setSources] = useState<LibraryItem[]>([]);
  const [renders, setRenders] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [lib, st] = await Promise.all([
        getPostprodLibrary(),
        getPostprodStatus(),
      ]);
      setSources(lib.sources);
      setRenders(lib.renders);
      setStatus(st);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đọc được thư viện.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { status, sources, renders, loading, error, refresh };
}

/** Where to point a <video>/<img> at, for either kind of library item. */
export function previewUrl(item: LibraryItem): string | null {
  if (item.url) return item.url;
  if (item.mediaId) return mediaUrl(item.mediaId);
  return null;
}

export function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A filename that will not collide with an earlier run of the same step. */
export function stampedName(prefix: string, ext: string): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
    `-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${prefix}-${stamp}.${ext}`;
}
