import { useState } from "react";
import { createReference, mediaUrl, humanizeBackendError } from "@/api/client";
import { useJobsStore, type Job, type JobStatus } from "@/store/jobs";
import { cn } from "@/lib/utils";

const STATUS_META: Record<JobStatus, { label: string; tone: string }> = {
  queued: { label: "ĐANG CHỜ", tone: "text-primary" },
  running: { label: "ĐANG TẠO", tone: "text-warn" },
  done: { label: "HOÀN THÀNH", tone: "text-ok" },
  failed: { label: "LỖI", tone: "text-danger" },
  canceled: { label: "HỦY", tone: "text-[#f97316]" },
  timeout: { label: "QUÁ GIỜ", tone: "text-danger" },
};

// `status` is a free-text column on the backend and arrives as unvalidated
// JSON, so an unrecognised value must not index into undefined and take the
// whole tab down with it.
const UNKNOWN_STATUS = { label: "KHÔNG RÕ", tone: "text-ink-mute" };

export function JobList({
  tool,
  kind = "video",
}: {
  tool: string;
  kind?: "image" | "video";
}) {
  // Select the stable jobs array and filter in the body — a selector that
  // returns a fresh .filter() array on every call is a zustand v5 footgun
  // (new reference each render).
  const allJobs = useJobsStore((s) => s.jobs);
  const jobs = allJobs.filter((j) => j.tool === tool);
  if (jobs.length === 0) return null;
  return (
    <div className="mt-4 flex flex-col gap-2">
      {jobs.map((j) => (
        <JobCard key={j.requestId} job={j} kind={kind} />
      ))}
    </div>
  );
}

/** One result, with a way to keep it.
 *
 * The reference library was readable but not writable: `MediaPicker` lists it,
 * yet nothing in the tabs ever put anything in, so it stayed permanently
 * empty. Saving from here closes the loop — generate, keep, reuse as a
 * reference in any other tab — and lives in JobList so every tab gets it
 * rather than each one growing its own button.
 *
 * Only images are offered. A reference is used as an input image for
 * generation, and a video cannot serve that role.
 */
function ResultMedia({
  mediaId,
  kind,
  prompt,
}: {
  mediaId: string;
  kind: "image" | "video";
  prompt: string;
}) {
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );

  async function save() {
    setState("saving");
    try {
      await createReference({
        media_id: mediaId,
        kind: "image",
        // The prompt is the only description on hand, and a library of
        // unlabelled thumbnails is unsearchable.
        label: prompt.slice(0, 80),
        ai_brief: prompt.slice(0, 500),
      });
      setState("saved");
    } catch {
      // Most often: already saved (the media_id column is unique).
      setState("error");
    }
  }

  return (
    <div className="flex flex-col gap-1">
      {kind === "image" ? (
        <img
          src={mediaUrl(mediaId)}
          alt=""
          className="h-40 rounded-md border border-line bg-black object-contain"
        />
      ) : (
        <video
          src={mediaUrl(mediaId)}
          controls
          className="h-40 rounded-md border border-line bg-black"
        />
      )}
      {kind === "image" && (
        <button
          type="button"
          onClick={() => void save()}
          disabled={state === "saving" || state === "saved"}
          className="rounded-(--radius-ctl) border border-line bg-card px-2 py-1 text-[11px] text-ink hover:bg-elevated disabled:opacity-60"
        >
          {state === "saved"
            ? "✓ Đã lưu thư viện"
            : state === "saving"
              ? "Đang lưu…"
              : state === "error"
                ? "Đã có trong thư viện"
                : "💾 Lưu vào thư viện"}
        </button>
      )}
    </div>
  );
}

function JobCard({ job, kind }: { job: Job; kind: "image" | "video" }) {
  const meta = STATUS_META[job.status] ?? UNKNOWN_STATUS;
  const media = job.mediaIds;
  // Per-slot failures are the most common Veo outcome (content filter on one
  // variant); without this the card just silently shows fewer clips.
  const slotErrors = job.slotErrors.filter((e): e is string => Boolean(e));
  return (
    <div className="rounded-(--radius-card) border border-line bg-card p-3">
      <div className="flex items-center gap-2">
        <span className={cn("text-[12px] font-semibold", meta.tone)}>
          {meta.label}
        </span>
        <span className="truncate text-[13px] text-ink" title={job.prompt}>
          {job.prompt}
        </span>
        {job.stale && (
          <span className="ml-auto shrink-0 text-[11px] text-warn">
            mất kết nối agent — vẫn đang thử lại
          </span>
        )}
      </div>
      {job.modelNote && (
        <div className="mt-1 text-[12px] text-warn">⚠ {job.modelNote}</div>
      )}
      {job.error && (
        <div className="mt-1 text-[12px] text-danger">
          {humanizeBackendError(job.error) ?? job.error}
        </div>
      )}
      {slotErrors.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-[12px] text-danger">
          {slotErrors.map((e, i) => (
            <li key={`${e}-${i}`}>{humanizeBackendError(e) ?? e}</li>
          ))}
        </ul>
      )}
      {media.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {media.map((mid) => (
            <ResultMedia key={mid} mediaId={mid} kind={kind} prompt={job.prompt} />
          ))}
        </div>
      )}
    </div>
  );
}
