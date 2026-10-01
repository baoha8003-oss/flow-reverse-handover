/** Where the CPU watermark pass should paint, as a canvas setting.
 *
 * The packaged tool runs its detector first — its own log line is "Xóa logo
 * Gemini: ưu tiên GPU, tự chuyển CPU nếu GPU không khả dụng" — and the CPU
 * half of that (MI-GAN, shipped beside the executable) fills a hole someone
 * points at rather than finding one. So it needs a box, and the box lived
 * only inside imported workflow files as `veo_logo_*` percentages: the canvas
 * had no way to write one, which left the whole fallback reachable over HTTP
 * and nowhere else.
 *
 * Corners, not four number fields. A generator's mark sits in one, and the
 * packaged workflow's own box is 20% x 10% of the frame — these are that box,
 * moved to the corner picked.
 */

export const WATERMARK_BOX = { w: 20, h: 10 };

export const WATERMARK_CORNERS: {
  key: string;
  label: string;
  x?: number;
  y?: number;
}[] = [
  { key: "none", label: "Không (chỉ máy dò)" },
  { key: "tr", label: "Trên phải", x: 78, y: 2 },
  { key: "tl", label: "Trên trái", x: 2, y: 2 },
  { key: "br", label: "Dưới phải", x: 78, y: 88 },
  { key: "bl", label: "Dưới trái", x: 2, y: 88 },
];

const BOX_KEYS = [
  "veo_logo_x_pct",
  "veo_logo_y_pct",
  "veo_logo_w_pct",
  "veo_logo_h_pct",
];

/** Which corner a box is in, so an imported workflow's own box reads back.
 *
 * A number, not anything `Number()` will take: the backend's own `_number`
 * refuses strings and booleans, and the packaged files write `""` for unset
 * (`veo_logo_image: ""`). `Number("")` is 0, which read back as "top left" —
 * the picker claiming a box the backend does not see. The `typeof` is also
 * what narrows these for the comparison below — dropping it leaves `unknown`
 * and `tsc` says so, which is why the runtime behaviour alone looks the same.
 */
export function watermarkCorner(settings: Record<string, unknown>): string {
  const x = settings["veo_logo_x_pct"];
  const y = settings["veo_logo_y_pct"];
  if (typeof x !== "number" || typeof y !== "number") return "none";
  if (!Number.isFinite(x) || !Number.isFinite(y)) return "none";
  return `${y >= 50 ? "b" : "t"}${x >= 50 ? "r" : "l"}`;
}

/** The node's settings with the fallback box moved, or removed for "none".
 *
 * Returns the WHOLE settings object because that is what has to be sent: the
 * backend shallow-merges `data`, so a nested partial would not merge — the
 * four keys would land and every other setting on the node would be dropped.
 */
export function withWatermarkCorner(
  settings: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...settings };
  BOX_KEYS.forEach((k) => delete next[k]);
  const corner = WATERMARK_CORNERS.find((c) => c.key === key);
  if (corner && corner.x !== undefined && corner.y !== undefined) {
    next["veo_logo_x_pct"] = corner.x;
    next["veo_logo_y_pct"] = corner.y;
    next["veo_logo_w_pct"] = WATERMARK_BOX.w;
    next["veo_logo_h_pct"] = WATERMARK_BOX.h;
  }
  return next;
}
