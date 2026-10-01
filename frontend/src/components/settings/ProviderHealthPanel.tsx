import { useCallback, useEffect, useState } from "react";
import { getLlmHealth, type LLMHealth } from "../../api/client";

/**
 * What is running on what, and what is broken.
 *
 * This panel exists because dispatch refuses to substitute providers. When
 * the provider you pinned cannot serve, the call fails instead of quietly
 * using another one — so you always know which model made your work. The
 * cost of that choice is that a dead provider has no other way to announce
 * itself, and without somewhere to look, the first symptom is a board run
 * failing half an hour later. That happened here: the Gemini CLI stopped
 * accepting individual accounts, and everything that routed through it
 * simply stopped, with nothing on screen saying why.
 *
 * Three sections, because three things fail differently:
 *
 *  - **Pinned features** — you chose these. A dead one stays dead until you
 *    re-pin, so it needs to be the loudest row.
 *  - **Internal steps** — rewriting a prompt after a review, scoring a clip,
 *    drafting a script, emitting canvas actions. Nobody picks a provider for
 *    these, so they walk a preference order; what matters is who would
 *    answer today and whether anyone can.
 *  - **Audio** — subtitles and karaoke timing. The one capability with a
 *    single supplier, which is exactly why it gets its own line: a general
 *    "providers look fine" reading is what makes "why did subtitles stop?"
 *    hard to answer.
 */

const FEATURE_LABELS: Record<string, string> = {
  auto_prompt: "Viết prompt tự động",
  vision: "Đọc ảnh / video",
  planner: "Lập kế hoạch canvas",
  revise: "Sửa prompt sau khi chấm",
  review: "Chấm điểm clip",
  script_writer: "Viết kịch bản",
  canvas_agent: "Trợ lý canvas",
};

const REASON_LABELS: Record<string, string> = {
  "not pinned": "chưa chọn provider",
  "unknown provider": "tên provider không tồn tại — kiểm tra lại cài đặt",
  "provider unavailable": "provider không dùng được",
};

function label(feature: string): string {
  return FEATURE_LABELS[feature] ?? feature;
}

export function ProviderHealthPanel() {
  const [health, setHealth] = useState<LLMHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHealth(await getLlmHealth());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="provider-health provider-health--error" role="alert">
        Không đọc được trạng thái provider: {error}
        <button type="button" onClick={() => void load()}>Thử lại</button>
      </div>
    );
  }
  if (!health) return <div className="provider-health provider-health--loading">Đang kiểm tra…</div>;

  const brokenPins = health.features.filter((f) => !f.ok);
  const brokenChains = health.internal.filter((f) => !f.ok);
  const allWell = brokenPins.length === 0 && brokenChains.length === 0 && health.audio.ok;

  return (
    <div className="provider-health">
      <div className="provider-health__head">
        <span className="provider-health__title">Tình trạng AI</span>
        <button type="button" className="provider-health__refresh" onClick={() => void load()}>
          Kiểm tra lại
        </button>
      </div>

      {allWell && (
        <div className="provider-health__ok">Mọi bước đều có model phục vụ.</div>
      )}

      <ul className="provider-health__list">
        {health.features.map((f) => (
          <li key={f.feature} className={f.ok ? "is-ok" : "is-down"}>
            <span className="provider-health__feature">{label(f.feature)}</span>
            <span className="provider-health__value">
              {f.ok
                ? f.provider
                : (REASON_LABELS[f.reason ?? ""] ?? f.reason ?? "không dùng được")}
            </span>
          </li>
        ))}

        {health.internal.map((f) => (
          <li key={f.feature} className={f.ok ? "is-ok" : "is-down"}>
            <span className="provider-health__feature">{label(f.feature)}</span>
            <span className="provider-health__value">
              {f.ok ? f.servedBy : `không provider nào chạy được (${f.chain.join(" → ")})`}
            </span>
          </li>
        ))}

        <li className={health.audio.ok ? "is-ok" : "is-down"}>
          <span className="provider-health__feature">Phụ đề / karaoke (âm thanh)</span>
          <span className="provider-health__value">
            {health.audio.ok
              ? health.audio.providers.join(", ")
              : "không provider nào đọc được âm thanh — cần khoá Gemini"}
          </span>
        </li>

        {/* Off is the correct state here, not a fault: Flow draws every
            image unless someone deliberately switched this on. So the row
            reports what is available rather than flagging a problem. */}
        <li className={health.images.ok ? "is-ok" : undefined}>
          <span className="provider-health__feature">Tạo ảnh bằng OpenAI</span>
          <span className="provider-health__value">
            {health.images.ok
              ? `${health.images.sources.join(" → ")} · ${health.images.model} (${health.images.quality})`
              : "đang tắt — Flow lo toàn bộ ảnh"}
          </span>
        </li>
      </ul>

      {/* Named separately from the rows above: a provider can be signed in
          and still be the wrong one to ask, and knowing which is which is
          what turns "it broke" into "I know what to fix". */}
      <div className="provider-health__providers">
        {health.providers.map((p) => (
          <span key={p.name} className={p.available ? "is-ok" : "is-down"}>
            {p.name}
            {p.available ? "" : " (chết)"}
            {p.capabilities.audio ? " · âm thanh" : ""}
          </span>
        ))}
      </div>
    </div>
  );
}
