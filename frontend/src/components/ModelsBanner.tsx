import { useModelsError, useModelsReady } from "@/store/models";

/** Why the generate button is disabled, when it is.
 *
 * The tabs read their model / aspect / duration options from `/api/models`.
 * Until that lands the controls hold nothing, and dispatching with nothing
 * is not harmless: `resolve_video_model(tier, aspect, "")` falls back to
 * `fast`, a pricier lane than the `lite` these tabs used to default to. So
 * dispatch waits — and a button that is disabled without saying why is its
 * own bug, which is what this renders.
 */
export function ModelsBanner() {
  const ready = useModelsReady();
  const error = useModelsError();

  if (ready) return null;

  return (
    <div
      className={`rounded-(--radius-ctl) border px-3 py-2 text-[12px] ${
        error
          ? "border-danger bg-danger/10 text-danger"
          : "border-line bg-card text-ink-mute"
      }`}
    >
      {error ? (
        <>
          Không đọc được danh sách model từ agent ({error}). Chưa tạo được cho
          tới khi đọc được — nếu cứ chạy, tham số rỗng sẽ rơi về làn đắt hơn.
        </>
      ) : (
        <>Đang đọc danh sách model của tài khoản…</>
      )}
    </div>
  );
}
