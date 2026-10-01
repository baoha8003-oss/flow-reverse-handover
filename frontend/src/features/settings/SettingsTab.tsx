import { useCallback, useEffect, useState } from "react";
import { Card, LabeledSelect, PrimaryButton, GhostButton } from "@/ui/primitives";
import { AiProvidersSection } from "@/components/settings/AiProvidersSection";
import {
  getSettings,
  putSettings,
  resetSettings,
  type SettingsMap,
} from "@/api/client";
import { useAppConfigStore } from "@/store/appConfig";
import { useModelsStore, useSettingsOptions, toSelectOptions } from "@/store/models";

/** Settings — the server-side key/value store (`/api/settings`).
 *
 * Not to be confused with `store/settings.ts`, which is the canvas's own
 * localStorage model preference. This screen edits config.json defaults
 * layered under user overrides, shared by every tab.
 *
 * **Only keys that actually change behaviour are shown.** The backend
 * whitelist carries ~50 keys for parity with the packaged tool, but most have
 * no consumer in this build: storing them would render a control that quietly
 * does nothing. See docs/spec.md §6.1 for the live/inert table. Values are
 * written in config.json's own format (labels like "Veo 3.1 - Lite", "9:16")
 * because that is what the tabs read back when they seed their controls.
 */

// The four option lists here come from /api/models — the same tables the
// tabs dispatch on, projected onto the labels config.json stores. Typed out
// locally they had drifted: 4–10s was offered as a video length when a Pro
// account only accepts 8s on Veo.

/** Bounds mirrored from settings_store._INT_RANGES. Clamping here turns a
 * whole-batch 400 into a value the user can actually save. */
const INT_BOUNDS: Record<string, [number, number]> = {
  MULTI_VIDEO: [1, 20],
  RETRY_WITH_ERROR: [0, 20],
  OUTPUT_COUNT: [1, 10],
  VIDEO_DURATION_SECONDS: [1, 60],
};

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <h2 className="text-[13px] font-extrabold text-ink">{title}</h2>
      {hint && <p className="mt-1 text-[12px] text-ink-dim">{hint}</p>}
      <div className="mt-3 flex flex-wrap gap-3">{children}</div>
    </Card>
  );
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  hint,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min: number;
  max: number;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[12px] text-ink-mute">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className="h-9 w-[110px] rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink"
      />
      {hint && <span className="text-[11px] text-ink-dim">{hint}</span>}
    </label>
  );
}

function TextField({
  label,
  value,
  onChange,
  hint,
  wide,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  wide?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[12px] text-ink-mute">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`h-9 rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink ${
          wide ? "w-[420px] max-w-full" : "w-[220px]"
        }`}
      />
      {hint && <span className="text-[11px] text-ink-dim">{hint}</span>}
    </label>
  );
}

function ToggleField({
  label,
  value,
  onChange,
  hint,
  warn,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
  warn?: boolean;
}) {
  return (
    <label className="flex w-full max-w-[520px] cursor-pointer flex-col gap-1">
      <span className="flex items-center gap-2 text-[12px] text-ink">
        <input
          type="checkbox"
          checked={value}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </span>
      {hint && (
        <span className={`text-[11px] ${warn ? "text-warn" : "text-ink-dim"}`}>
          {hint}
        </span>
      )}
    </label>
  );
}

export function SettingsTab() {
  const reloadAppConfig = useAppConfigStore((s) => s.load);
  const loadModels = useModelsStore((s) => s.load);
  const modelOptions = useSettingsOptions();

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  const [draft, setDraft] = useState<SettingsMap>({});
  const [saved, setSaved] = useState<SettingsMap>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const values = await getSettings();
      setSaved(values);
      setDraft(values);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đọc được cài đặt.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const str = (key: string, fallback = ""): string => {
    const v = draft[key];
    return v === undefined || v === null ? fallback : String(v);
  };
  const num = (key: string, fallback: number): number => {
    const n = Number(draft[key]);
    return Number.isFinite(n) ? n : fallback;
  };
  const flag = (key: string): boolean => draft[key] === true;
  const set = (key: string, value: unknown) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setNote(null);
  };
  const setInt = (key: string, n: number) => {
    const [lo, hi] = INT_BOUNDS[key] ?? [0, Number.MAX_SAFE_INTEGER];
    set(key, Math.max(lo, Math.min(hi, Math.round(n))));
  };

  // Only send what actually changed. The endpoint is all-or-nothing, so a
  // full-map PUT would let one untouched legacy value reject the whole save.
  const changed = Object.keys(draft).filter(
    (k) => JSON.stringify(draft[k]) !== JSON.stringify(saved[k]),
  );
  const dirty = changed.length > 0;

  async function save() {
    if (!dirty) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const patch: SettingsMap = {};
      for (const k of changed) patch[k] = draft[k];
      await putSettings(patch);
      setSaved(draft);
      // Tabs seed their controls from this store, so refresh it or the new
      // defaults only appear after a page reload.
      await reloadAppConfig();
      setNote(`Đã lưu ${changed.length} thay đổi.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lưu thất bại.");
    } finally {
      setBusy(false);
    }
  }

  async function restoreDefaults() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const { removed } = await resetSettings();
      await load();
      await reloadAppConfig();
      setNote(`Đã trả về mặc định (${removed} khoá).`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không trả về mặc định được.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <Card>
        <p className="text-[13px] text-ink-mute">Đang tải cài đặt…</p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Section
        title="🔑 Tài khoản Google Flow"
        hint="Từ tháng 9/2026 Flow ký mọi lệnh ngay trong tab đã đăng nhập và không còn phát token để app đọc gói hay số dư. Hai ô dưới đây là thứ duy nhất app còn biết — chúng CHỈ đổi bảng giá và danh sách lane, không chặn chạy và không đổi model."
      >
        <LabeledSelect
          label="Gói Flow"
          value={str("FLOW_PAYGATE_TIER", "")}
          onChange={(v) => set("FLOW_PAYGATE_TIER", v)}
          options={[
            { value: "", label: "Chưa chọn" },
            { value: "PAYGATE_TIER_ONE", label: "Pro" },
            { value: "PAYGATE_TIER_TWO", label: "Ultra" },
          ]}
          className="min-w-[200px]"
        />
        <TextField
          label="Project Flow dự phòng"
          value={str("FLOW_PROJECT_ID")}
          onChange={(v) => set("FLOW_PROJECT_ID", v)}
          hint="Để trống thì mỗi board tự tạo project trên Flow. Nếu Flow từ chối tạo, app dùng uuid dán ở đây — lấy từ URL project trong Flow UI."
        />
      </Section>

      <Section
        title="🎬 Tạo video"
        hint="Giá trị mặc định cho các tab tạo video. Đổi ở đây thì tab lấy theo khi mở lần sau."
      >
        <LabeledSelect
          label="Model mặc định"
          value={str("VEO_MODEL", "Veo 3.1 - Lite [Lower Priority]")}
          onChange={(v) => set("VEO_MODEL", v)}
          options={toSelectOptions(modelOptions.veoModel)}
          className="min-w-[260px]"
        />
        <LabeledSelect
          label="📐 Tỉ lệ"
          value={str("VIDEO_ASPECT_RATIO", "9:16")}
          onChange={(v) => set("VIDEO_ASPECT_RATIO", v)}
          options={toSelectOptions(modelOptions.aspect)}
        />
        <LabeledSelect
          label="⏱ Thời lượng"
          value={str("VIDEO_DURATION_SECONDS", "8")}
          onChange={(v) => setInt("VIDEO_DURATION_SECONDS", Number(v))}
          options={modelOptions.duration.map((d) => ({
            value: String(d.value),
            label: d.note ? `${d.label} (${d.note.replace(/\.$/, "")})` : d.label,
          }))}
        />
        <LabeledSelect
          label="🖥 Độ phân giải"
          value={str("VIDEO_RESOLUTION", "720p")}
          onChange={(v) => set("VIDEO_RESOLUTION", v)}
          options={toSelectOptions(modelOptions.resolution)}
          hint="Chỉ áp dụng cho làn OMNI — Veo luôn theo model key."
        />
        <NumberField
          label="Chạy song song"
          value={num("MULTI_VIDEO", 4)}
          onChange={(n) => setInt("MULTI_VIDEO", n)}
          min={1}
          max={20}
          hint="Số job cùng lúc"
        />
        <NumberField
          label="Thử lại khi lỗi"
          value={num("RETRY_WITH_ERROR", 3)}
          onChange={(n) => setInt("RETRY_WITH_ERROR", n)}
          min={0}
          max={20}
          hint="Lần thử cho job mới"
        />
      </Section>

      <Section title="🎨 Tạo ảnh">
        <LabeledSelect
          label="Model ảnh"
          value={str("CREATE_IMAGE_MODEL", "Nano Banana 2")}
          onChange={(v) => set("CREATE_IMAGE_MODEL", v)}
          options={toSelectOptions(modelOptions.imageModel)}
          className="min-w-[220px]"
        />
        <NumberField
          label="Số ảnh mỗi prompt"
          value={num("OUTPUT_COUNT", 1)}
          onChange={(n) => setInt("OUTPUT_COUNT", n)}
          min={1}
          max={10}
        />
      </Section>

      <Section
        title="🖌 Tạo ảnh bằng OpenAI (tuỳ chọn)"
        hint="Đường tạo ảnh thứ hai, dùng khi Flow hỏng hoặc từ chối prompt. Mặc định TẮT cả hai — một đường tốn tiền thật, một đường tốn quota ChatGPT của bạn. Chọn engine 'openai' trên từng node ảnh để dùng."
      >
        <ToggleField
          label="Bật đường API key (khuyên dùng)"
          value={flag("OPENAI_IMAGE_ENABLED")}
          onChange={(v) => set("OPENAI_IMAGE_ENABLED", v)}
          hint="Cần API key OpenAI thật ở mục AI Providers bên dưới. Đăng nhập ChatGPT KHÔNG dùng được cho đường này. Tính tiền theo từng ảnh."
        />
        <ToggleField
          label="Bật đường ChatGPT qua Codex (không chính thức)"
          value={flag("OPENAI_IMAGE_RELAY_ENABLED")}
          onChange={(v) => set("OPENAI_IMAGE_RELAY_ENABLED", v)}
          warn
          hint="⚠️ Không phải API công khai của OpenAI — dùng lại phiên đăng nhập Codex. Ngốn quota ChatGPT gấp 3–5 lần một lượt chat, và có thể hỏng sau bất kỳ bản cập nhật Codex nào. Token hết hạn thì chạy `codex login`."
        />
        <ToggleField
          label="Ưu tiên đường ChatGPT thay vì API key"
          value={flag("OPENAI_IMAGE_PREFER_RELAY")}
          onChange={(v) => set("OPENAI_IMAGE_PREFER_RELAY", v)}
          hint="Mặc định API key chạy trước vì đường ChatGPT tiêu chính cái quota bạn đang dùng để làm việc. Bật cái này nếu bạn muốn tiêu quota thay vì tiêu tiền."
        />
        <TextField
          label="Model"
          value={str("OPENAI_IMAGE_MODEL", "gpt-image-2")}
          onChange={(v) => set("OPENAI_IMAGE_MODEL", v)}
          hint="Mặc định gpt-image-2. Cả họ gpt-image-1 bị OpenAI ngừng ngày 01/12/2026."
        />
        <LabeledSelect
          label="Chất lượng"
          value={str("OPENAI_IMAGE_QUALITY", "low")}
          onChange={(v) => set("OPENAI_IMAGE_QUALITY", v)}
          options={[
            { value: "low", label: "low — rẻ nhất (~$0.005/ảnh)" },
            { value: "medium", label: "medium (~$0.04/ảnh)" },
            { value: "high", label: "high (~$0.17/ảnh)" },
            { value: "auto", label: "auto — để OpenAI tự chọn" },
          ]}
          className="min-w-[240px]"
        />
      </Section>

      <Section
        title="💬 Phụ đề & phiên âm"
        hint="Nguồn phiên âm quyết định phụ đề tốn gì: quota Gemini, tiền OpenAI, hay miễn phí trên máy này. Bảng chi phí trước khi chạy sẽ nói đúng nguồn nào đang bật."
      >
        <ToggleField
          label="Bật phiên âm offline (faster-whisper)"
          value={flag("LOCAL_STT_ENABLED")}
          onChange={(v) => set("LOCAL_STT_ENABLED", v)}
          hint="Miễn phí và là nguồn duy nhất cho timing karaoke chuẩn từng từ. Lần chạy đầu tải model vài trăm MB, nên phải bật tay."
        />
        <ToggleField
          label="Bật phiên âm bằng whisper-1 (OpenAI)"
          value={flag("OPENAI_STT_ENABLED")}
          onChange={(v) => set("OPENAI_STT_ENABLED", v)}
          warn
          hint="⚠️ Tính tiền thật ~$0.006 mỗi phút audio, trên cùng khoá OpenAI dùng để viết kịch bản. Tắt thì Gemini hỏng sẽ rơi xuống bản offline chứ không âm thầm tiêu tiền."
        />
        <TextField
          label="Model offline"
          value={str("LOCAL_STT_MODEL", "small")}
          onChange={(v) => set("LOCAL_STT_MODEL", v)}
          hint="tiny / base / small / medium / large-v3 — càng lớn càng chính xác và càng chậm."
        />
      </Section>

      <Section
        title="🎙 Giọng đọc bằng OpenAI (tuỳ chọn)"
        hint="Mặc định TẮT. Endpoint /v1/audio/speech chính thức của OpenAI, dùng cho node Lồng tiếng có engine 'ChatGPT' — 6 node trong 9 mẫu đóng gói ghi như vậy. Tắt thì các node đó vẫn chạy bằng Gemini và bảng chi phí sẽ nói rõ đã thay giọng nào."
      >
        <ToggleField
          label="Bật giọng đọc OpenAI"
          value={flag("OPENAI_TTS_ENABLED")}
          onChange={(v) => set("OPENAI_TTS_ENABLED", v)}
          warn
          hint="⚠️ Tính tiền theo KÝ TỰ (~$0.60 / 1 triệu ký tự, tra 18/09/2026), nên một truyện dài không phải khoản làm tròn như một tấm ảnh. Cần API key OpenAI thật ở mục AI Providers."
        />
        <TextField
          label="Model"
          value={str("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")}
          onChange={(v) => set("OPENAI_TTS_MODEL", v)}
          hint="Mặc định gpt-4o-mini-tts."
        />
        <LabeledSelect
          label="Giọng mặc định"
          value={str("OPENAI_TTS_VOICE", "alloy")}
          onChange={(v) => set("OPENAI_TTS_VOICE", v)}
          options={[
            { value: "alloy", label: "alloy — trung tính" },
            { value: "ash", label: "ash" },
            { value: "ballad", label: "ballad" },
            { value: "coral", label: "coral" },
            { value: "echo", label: "echo" },
            { value: "fable", label: "fable" },
            { value: "nova", label: "nova" },
            { value: "onyx", label: "onyx — trầm" },
            { value: "sage", label: "sage" },
            { value: "shimmer", label: "shimmer" },
            { value: "verse", label: "verse" },
          ]}
          className="min-w-[240px]"
        />
      </Section>

      <Section
        title="📁 Lưu trữ"
        hint="Mỗi video/ảnh xong được lưu 2 nơi: trong app để xem ngay, và ra thư mục dưới đây theo tên dự án."
      >
        <TextField
          label="Thư mục lưu media"
          value={str("VIDEO_OUTPUT_DIR")}
          onChange={(v) => set("VIDEO_OUTPUT_DIR", v)}
          hint="Ví dụ: D:/TOOL_VIDEO/VIDEO_OUT"
          wide
        />
        <TextField
          label="Dự án hiện tại"
          value={str("CURRENT_PROJECT", "default_project")}
          onChange={(v) => set("CURRENT_PROJECT", v)}
          hint="Tên thư mục con trong thư mục trên"
        />
      </Section>

      <Card>
        <h2 className="text-[13px] font-extrabold text-ink">🤖 AI Providers</h2>
        <p className="mt-1 text-[12px] text-ink-dim">
          Chỉ dùng cho tự động viết prompt, phân tích ảnh và planner. Các tab tạo
          video/ảnh KHÔNG cần mục này.
        </p>
        <div className="mt-3">
          <AiProvidersSection />
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <PrimaryButton onClick={save} disabled={!dirty} loading={busy}>
            Lưu cài đặt
          </PrimaryButton>
          <GhostButton onClick={restoreDefaults}>Trả về mặc định</GhostButton>
          {dirty && (
            <span className="text-[12px] text-warn">
              {changed.length} thay đổi chưa lưu
            </span>
          )}
          {note && <span className="text-[12px] text-ok">{note}</span>}
          {error && <span className="text-[12px] text-danger">{error}</span>}
        </div>
        <p className="mt-3 text-[11px] text-ink-dim">
          Tài khoản, token và cookie không nằm ở đây: backend không cho đọc hay
          ghi các khoá đó. Một số khoá của bản đóng gói (độ phân giải, seed, tự
          upscale, phụ đề, logo…) hiện chưa nối vào hành vi nên không hiện ở
          đây — xem docs/spec.md §6.1.
        </p>
      </Card>
    </div>
  );
}
