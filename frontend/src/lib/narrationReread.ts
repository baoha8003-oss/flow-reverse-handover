/**
 * What re-reading the failed narration segments will and will not cost.
 *
 * A long script is read segment by segment, and the record beside the output
 * says which segments were bought. Until now that record could never be read
 * back, so one failed segment out of forty cost forty segments to fix.
 *
 * The number this produces is the whole point of the control: the user has to
 * see that the finished segments are KEPT before pressing a button that spends
 * text-to-speech quota. Pure so it can be tested without a DOM — this repo has
 * no component-test harness, which is why the decisions live in `lib/`.
 */

export interface SegmentView {
  index: number;
  status: string;
  chars: number;
  preview: string;
  attempts: number;
  error: string | null;
}

export interface RereadPreview {
  segments: SegmentView[];
  read: number[];
  readChars: number;
  kept: number;
  keptChars: number;
  engine: string | null;
  voice: string | null;
  /** False when the record predates the voice field — allowed, but said out loud. */
  voiceKnown: boolean;
}

/** `gpt-4o-mini-tts`, published per million characters. Mirrors the backend's
 *  own note so the two cannot quote different prices. */
export const OPENAI_TTS_USD_PER_MILLION = 12;

function vnd(n: number): string {
  return n.toLocaleString("vi-VN");
}

/**
 * The sentence shown above the button.
 *
 * Says what is kept, not only what is read. "Read 2 segments" alone leaves the
 * user to wonder whether the other thirty-eight are about to be charged again —
 * which is the exact fear this feature exists to remove, so the answer belongs
 * in the same sentence.
 */
export function rereadNote(preview: RereadPreview): string {
  const n = preview.read.length;
  if (n === 0) return "Mọi đoạn đều đã đọc xong.";
  const parts = [
    `Sẽ đọc lại ${n} đoạn (~${vnd(preview.readChars)} ký tự).`,
  ];
  if (preview.kept > 0) {
    parts.push(
      `${preview.kept} đoạn đã xong được giữ nguyên — không tính lại.`,
    );
  }
  if (preview.engine === "openai") {
    const usd = (preview.readChars / 1_000_000) * OPENAI_TTS_USD_PER_MILLION;
    parts.push(`Tốn khoảng $${usd.toFixed(4)} quota OpenAI.`);
  } else {
    parts.push("Tốn quota Gemini.");
  }
  if (!preview.voiceKnown) {
    // The record predates the voice field, so the guard that refuses a voice
    // change cannot fire. Said rather than hidden: the user is the only one who
    // knows whether the voice setting has been touched since.
    parts.push(
      "Bản ghi cũ không lưu giọng, nên không kiểm được giọng có đổi hay chưa.",
    );
  }
  return parts.join(" ");
}

/** A short label per segment row: which ones are holes. */
export function segmentLabel(seg: SegmentView): string {
  const head = `#${seg.index + 1}`;
  if (seg.status === "done") return `${head} ✓`;
  const why = seg.error ? ` — ${seg.error}` : "";
  return `${head} ✗${why}`;
}

/** True when there is anything to re-read. Drives whether the button shows. */
export function hasHoles(preview: RereadPreview | null): boolean {
  return preview !== null && preview.read.length > 0;
}
