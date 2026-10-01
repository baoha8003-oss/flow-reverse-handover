import { useEffect, useState } from "react";
import {
  getBoardEstimate,
  type BoardEstimate,
  type EstimateLineItem,
} from "@/api/client";

/** What a board run will dispatch, shown before it runs.
 *
 * An imported workflow is a dozen nodes that become dozens of billable
 * calls. The number that protects the user is the JOB COUNT — it is derived
 * from the graph and it is exact. Credits are quoted only for the one lane
 * with a published price table; everywhere else the dialog says the price is
 * unknown rather than printing a total the account will not match.
 *
 * The confirm button stays disabled until the estimate has loaded. A Run
 * button you can press while the cost is still loading is the same button
 * with an extra step.
 */

interface Props {
  boardId: number;
  boardName: string;
  /** Price only these nodes. Omitted means the whole board, which is what the
   *  Run button sends. The canvas agent uses it to offer "run just these". */
  nodeIds?: number[];
  onConfirm: () => void;
  onCancel: () => void;
}

function Row({ item }: { item: EstimateLineItem }) {
  const free = item.credits === 0;
  return (
    <tr className={free ? "opacity-60" : undefined}>
      <td className="py-1 pr-3 font-mono text-[11px] text-ink-dim">
        #{item.shortId}
      </td>
      <td className="py-1 pr-3">{item.title}</td>
      <td className="py-1 pr-3 text-right tabular-nums">{item.jobs}</td>
      <td className="py-1 text-right tabular-nums">
        {item.jobs === 0 ? (
          <span className="text-warn" title={item.note ?? undefined}>
            bỏ qua
          </span>
        ) : free ? (
          <span className="text-ink-dim">miễn phí</span>
        ) : item.credits === null ? (
          <span className="text-warn" title={item.note ?? undefined}>
            ?
          </span>
        ) : (
          `${item.credits}`
        )}
      </td>
    </tr>
  );
}

/** What a transcription actually costs, by source.
 *
 * This used to be one hardcoded sentence — "tốn quota Gemini của bạn" — from
 * when Gemini was the only path that took audio. It is now wrong in both
 * directions: whisper-1 bills real dollars per minute of audio, and local
 * faster-whisper bills nothing at all. A cost dialog that names the wrong
 * payer is worse than one that stays quiet, because the user acts on it.
 */
function transcribeCostNote(source: string): string {
  switch (source) {
    case "gemini":
      return "tốn quota Gemini của bạn, không tốn credit Flow.";
    case "whisper-1":
      return "tốn tiền OpenAI (khoảng $0.006 mỗi phút audio), không tốn credit Flow.";
    case "local":
      return "chạy faster-whisper trên máy này — miễn phí, chỉ tốn CPU.";
    default:
      return "chưa bật nguồn phiên âm nào — bước này sẽ báo lỗi. Mở Cài đặt để bật.";
  }
}

export function RunCostDialog({
  boardId,
  boardName,
  nodeIds,
  onConfirm,
  onCancel,
}: Props) {
  const [estimate, setEstimate] = useState<BoardEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A stable key for an array prop: `nodeIds` in the dependency list directly
  // would be a fresh identity on every render, so the quote would refetch in a
  // loop while the dialog is open.
  const scopeKey = (nodeIds ?? []).join(",");

  useEffect(() => {
    let live = true;
    getBoardEstimate(boardId, scopeKey ? scopeKey.split(",").map(Number) : undefined)
      .then((e) => live && setEstimate(e))
      .catch((e) =>
        live &&
        setError(e instanceof Error ? e.message : "Không tính được chi phí."),
      );
    return () => {
      live = false;
    };
  }, [boardId, scopeKey]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // Everything with a cost, plus the not-ready nodes: those are exactly the
  // ones the user has to go and fill in, so hiding them would leave them
  // wondering why the board does so little.
  const billable = estimate?.items.filter((i) => i.credits !== 0 || i.jobs === 0) ?? [];
  // Readable again: `nzlxg` answers directly, and five other replies — the poll
  // among them — carry the balance for free, so it is usually current. The guard
  // below was kept through the migration precisely for this moment.
  const balance = estimate?.creditsAvailable ?? null;
  const balanceAgeS = estimate?.creditsAgeS ?? null;
  // A cached number shown as current is the same failure as a guessed price, so
  // anything older than a few minutes is labelled instead of presented bare.
  const balanceIsStale = balanceAgeS !== null && balanceAgeS > 180;
  // Only a warning when both numbers are known. "Unknown price vs known
  // balance" cannot be compared, and a red banner on a guess is noise.
  const overBudget =
    estimate !== null &&
    balance !== null &&
    // A stale balance must not raise a red banner: the number may already have
    // moved, and a false alarm teaches people to click through real ones.
    !balanceIsStale &&
    estimate.unpricedJobs === 0 &&
    estimate.knownCredits > balance;

  return (
    <div className="run-cost-backdrop" onClick={onCancel}>
      <div
        className="run-cost-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Xác nhận chi phí"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-[14px] font-bold text-ink">Chạy “{boardName}”?</h2>

        {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
        {!estimate && !error && (
          <p className="mt-2 text-[12px] text-ink-mute">Đang tính…</p>
        )}

        {estimate && (
          <>
            <p className="mt-2 text-[13px] text-ink">
              <strong>{estimate.billableJobs}</strong> lần gọi có tính tiền
              {estimate.localJobs > 0 && (
                <> · {estimate.localJobs} bước xử lý tại máy (miễn phí)</>
              )}
            </p>
            {estimate.transcribeJobs > 0 && (
              <p
                className={
                  estimate.transcribeSource
                    ? "mt-1 text-[12px] text-ink-mute"
                    : "mt-1 text-[12px] text-warn"
                }
              >
                + {estimate.transcribeJobs} lần phiên âm để làm phụ đề —{" "}
                {transcribeCostNote(estimate.transcribeSource)}
              </p>
            )}
            {estimate.reviewJobs > 0 && (
              <p className="mt-1 text-[12px] text-ink-mute">
                +{" "}
                {(estimate.reviewJobsMax ?? estimate.reviewJobs)
                  > estimate.reviewJobs
                  ? `${estimate.reviewJobs}–${estimate.reviewJobsMax}`
                  : estimate.reviewJobs}{" "}
                lần chấm clip bằng AI — tốn quota AI của bạn, không tốn credit
                Flow.
              </p>
            )}
            {(estimate.llmJobs ?? 0) > 0 && (
              <p className="mt-1 text-[12px] text-ink-mute">
                + {estimate.llmJobs} lần gọi AI để viết prompt / đọc video —
                tốn quota AI của bạn, không tốn credit Flow.
              </p>
            )}
            {estimate.openaiImageJobs > 0 && (
              <p className="mt-1 text-[12px] text-ink-mute">
                + {estimate.openaiImageNote}
              </p>
            )}
            {(estimate.openaiTtsNodes ?? 0) > 0 && (
              <p className="mt-1 text-[12px] text-ink-mute">
                + {estimate.openaiTtsNote}
              </p>
            )}
            {estimate.notReadyJobs > 0 && (
              <p className="mt-1 text-[12px] text-warn">
                {estimate.notReadyJobs} bước chưa có prompt nên sẽ bị bỏ qua —
                điền prompt vào các node bên dưới rồi chạy lại.
              </p>
            )}
            <p className="mt-1 text-[12px] text-ink-mute">
              {estimate.unpricedJobs > 0 ? (
                <>
                  Trong đó {estimate.unpricedJobs} lần chưa có bảng giá công bố
                  nên không quy ra credit được
                  {estimate.knownCredits > 0 && (
                    <> (phần tính được: {estimate.knownCredits} credit)</>
                  )}
                  .
                </>
              ) : (
                <>Ước tính {estimate.knownCredits} credit.</>
              )}
              {balance !== null ? (
                <>
                  {" "}
                  Bạn đang có {balance} credit
                  {balanceIsStale && balanceAgeS !== null ? (
                    <> (đọc {Math.round(balanceAgeS / 60)} phút trước)</>
                  ) : null}
                  .
                </>
              ) : (
                // Said out loud rather than left blank. The line used to be
                // here, so its absence reads as "you have none" — and "unknown"
                // and "none left" send someone to opposite places.
                <> Số credit còn lại: không đọc được (xem trong Flow).</>
              )}
            </p>

            {overBudget && (
              <p className="mt-2 rounded-(--radius-ctl) bg-danger/10 p-2 text-[12px] text-danger">
                Chi phí ước tính vượt quá số credit đang có — board sẽ dừng
                giữa chừng.
              </p>
            )}

            {billable.length > 0 && (
              <div className="mt-3 max-h-[220px] overflow-y-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-left text-[11px] text-ink-dim">
                      <th className="pb-1 pr-3 font-normal">Node</th>
                      <th className="pb-1 pr-3 font-normal">Bước</th>
                      <th className="pb-1 pr-3 text-right font-normal">Lần</th>
                      <th className="pb-1 text-right font-normal">Credit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {billable.map((item) => (
                      <Row key={item.nodeId} item={item} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="run-cost-cancel" onClick={onCancel}>
            Huỷ
          </button>
          <button
            type="button"
            className="run-cost-confirm"
            disabled={!estimate}
            onClick={onConfirm}
          >
            {estimate
              ? `Chạy ${estimate.billableJobs} lần tính tiền`
              : "Đang tính…"}
          </button>
        </div>
      </div>
    </div>
  );
}
