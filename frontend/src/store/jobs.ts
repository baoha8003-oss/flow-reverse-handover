import { create } from "zustand";
import {
  createRequest,
  getRequest,
  getAuthMe,
  cancelActivity,
  getActivityList,
  listBoards,
  createBoard,
  ensureBoardProject,
  type RequestDTO,
} from "@/api/client";

/** Generation jobs for the tabbed tools.
 *
 * Tab mode has no canvas node, so a job is keyed by its backend request id
 * (not a React Flow node id). A Flow project is required for every dispatch;
 * since a project is 1:1 with a board, tab mode binds to the board named by
 * the CURRENT_PROJECT setting and reuses the verified board→flow-project
 * binding. The backend worker (concurrent + retry) owns the actual Flow
 * calls; this store dispatches, polls, and rehydrates.
 *
 * Polling is ONE store-level loop over the live jobs, not one loop per job:
 * a 50-prompt batch used to open 50 independent loops (~33 requests/second
 * against a single-process agent that is also running the Flow worker), and
 * none of them could be stopped once started.
 */

export const DEFAULT_PROJECT_NAME = "default_project";
const POLL_INTERVAL_MS = 1500;
const MAX_NETWORK_RETRIES = 8;
/** Tools whose rows this store owns, used when rehydrating from /api/activity. */
const TAB_TOOLS = ["gen_video_text", "gen_image", "gen_video", "gen_video_omni"];
/** How many past rows to restore. Each one costs a follow-up request for its
 * prompt and media, so this is a UI convenience, not a full history. */
const REHYDRATE_LIMIT = 12;

export type JobStatus = RequestDTO["status"];

export interface Job {
  requestId: number;
  tool: string;
  prompt: string;
  status: JobStatus;
  mediaIds: string[];
  /** Per-slot error codes aligned to mediaIds (content filter, timeout…). */
  slotErrors: (string | null)[];
  error: string | null;
  /** Set when the backend ran a different model lane than the one requested. */
  modelNote: string | null;
  /** True while polling can't reach the agent; the row is not failed yet. */
  stale: boolean;
  createdAt: number;
}

interface JobsState {
  projectId: string | null;
  projectName: string;
  jobs: Job[];
  /** Busy/error are per-tool: one tab's dispatch must not disable another's. */
  busy: Record<string, boolean>;
  error: Record<string, string | null>;
  ensureProject: (name?: string) => Promise<string | null>;
  enqueue: (
    tool: string,
    prompts: string[],
    params: Record<string, unknown>,
  ) => Promise<void>;
  /** Dispatch rows that each carry their own params (image→video, where the
   * source image differs per row). `requestType` is what the backend runs;
   * `uiTool` is which tab owns the resulting jobs — they differ when a tab
   * routes some models to another handler (OMNI Flash). */
  enqueueRow: (
    requestType: string,
    items: { prompt: string; params: Record<string, unknown> }[],
    uiTool?: string,
  ) => Promise<void>;
  cancelAll: (tool: string) => Promise<void>;
  rehydrate: () => Promise<void>;
}

/** Which tab owns the jobs a given request type produces. */
const UI_TOOL_BY_TYPE: Record<string, string> = {
  gen_video_omni: "gen_video",
};

function mediaIdsFrom(result: Record<string, unknown>): string[] {
  const raw = result?.media_ids;
  if (!Array.isArray(raw)) return [];
  return raw.filter((m): m is string => typeof m === "string" && m.length > 0);
}

function slotErrorsFrom(result: Record<string, unknown>): (string | null)[] {
  const raw = result?.slot_errors;
  if (!Array.isArray(raw)) return [];
  return raw.map((e) => (typeof e === "string" ? e : null));
}

/** The backend reports when it had to run a cheaper/different lane than the
 * one the label promised. Surfacing it is the whole point: a request labelled
 * "Lower Priority" that quietly ran on a paid model is the user's money. */
function modelNoteFrom(result: Record<string, unknown>): string | null {
  const subs = result?.model_substitutions;
  if (!Array.isArray(subs) || subs.length === 0) return null;
  const parts: string[] = [];
  for (const s of subs) {
    if (typeof s !== "object" || s === null) continue;
    const { field, requested, effective } = s as Record<string, unknown>;
    if (field === "quality") {
      parts.push(`model "${String(requested)}" → chạy "${String(effective)}"`);
    } else if (field === "duration_s") {
      parts.push(`thời lượng ${String(requested)}s → ${String(effective)}s`);
    }
  }
  if (parts.length === 0) return null;
  return `Gói của bạn không có làn này: ${parts.join(", ")} (có tính credit).`;
}

/** In-flight project bootstrap, so N prompts dispatched at once don't each
 * create their own board. */
let projectInFlight: Promise<string | null> | null = null;
/** Monotonic counter for rows that never reached the backend — Date.now()
 * collides when two dispatches fail inside the same millisecond. */
let syntheticId = 0;
/** Both tabs mount together and both ask to rehydrate; do it once. */
let rehydrated = false;

export const useJobsStore = create<JobsState>((set, get) => ({
  projectId: null,
  projectName: DEFAULT_PROJECT_NAME,
  jobs: [],
  busy: {},
  error: {},

  async ensureProject(name) {
    const cached = get().projectId;
    if (cached) return cached;
    if (projectInFlight) return projectInFlight;

    const wanted = (name || get().projectName || DEFAULT_PROJECT_NAME).trim();
    projectInFlight = (async () => {
      const boards = await listBoards();
      // Bind by NAME, not "whatever board happens to be first": the board
      // list has no guaranteed order, so an install that has used the canvas
      // would silently file tab jobs under the canvas board's Flow project
      // while the UI showed a different name.
      const match = boards.find((b) => b.name === wanted);
      const board = match ?? (await createBoard(wanted));
      const proj = await ensureBoardProject(board.id);
      set({ projectId: proj.flow_project_id, projectName: board.name });
      return proj.flow_project_id;
    })();
    try {
      return await projectInFlight;
    } finally {
      projectInFlight = null;
    }
  },

  async enqueue(tool, prompts, params) {
    // Same params for every prompt — the text-driven tabs.
    const cleaned = prompts.map((p) => p.trim()).filter((p) => p.length > 0);
    await get().enqueueRow(
      tool,
      cleaned.map((prompt) => ({ prompt, params })),
    );
  },

  async enqueueRow(requestType, items, uiTool) {
    const tool = uiTool ?? UI_TOOL_BY_TYPE[requestType] ?? requestType;
    if (get().busy[tool]) return; // double-submit guard lives with the state
    const cleaned = items
      .map((it) => ({ ...it, prompt: it.prompt.trim() }))
      .filter((it) => it.prompt.length > 0);
    if (cleaned.length === 0) {
      setError(set, tool, "Nhập ít nhất một prompt.");
      return;
    }
    setBusy(set, tool, true);
    setError(set, tool, null);

    // Pre-flight the plan tier exactly like the canvas path does. Without
    // it, an extension that hasn't seen a Flow request yet turns one click
    // into N failed rows all showing the raw `paygate_tier_unknown` token.
    const me = await getAuthMe();
    if (!me?.paygate_tier) {
      setBusy(set, tool, false);
      setError(
        set,
        tool,
        "Chưa nhận diện được gói Flow của bạn. Mở tab Flow "
          + "(labs.google/fx/tools/flow), tải lại một lần rồi thử lại.",
      );
      return;
    }

    let projectId: string | null;
    try {
      projectId = await get().ensureProject();
    } catch (e) {
      // A stale cached project id is the usual cause; drop it so the next
      // attempt re-resolves instead of failing forever.
      set({ projectId: null });
      setBusy(set, tool, false);
      setError(set, tool, `Không lấy được dự án Flow: ${String(e)}`);
      return;
    }
    if (!projectId) {
      setBusy(set, tool, false);
      setError(set, tool, "Chưa có dự án Flow.");
      return;
    }

    const created: Job[] = [];
    for (const item of cleaned) {
      try {
        const row = await createRequest({
          type: requestType,
          // No paygate_tier: the backend resolves it from the live
          // extension signal, so the UI never guesses the tier.
          params: { ...item.params, prompt: item.prompt, project_id: projectId },
        });
        created.push(blankJob(row.id, tool, item.prompt, row.status));
      } catch (e) {
        // A dispatch that never reached the queue still shows as a failed
        // row so the user sees which prompt didn't start.
        const failed = blankJob(--syntheticId, tool, item.prompt, "failed");
        failed.error = String(e);
        created.push(failed);
      }
    }
    // A dispatch rejected for the project means the cached flow_project_id is
    // stale (its board was deleted, or the database was reset). Drop it, or
    // every later dispatch fails the same way until the page is reloaded.
    if (created.some((j) => j.error?.includes("invalid_project_id"))) {
      set({ projectId: null });
    }
    set((s) => ({ jobs: [...created, ...s.jobs] }));
    setBusy(set, tool, false);
    startPolling(set, get);
    // If the first rehydrate failed (agent down at mount) this is the only
    // thing left to retry it: both tabs mount once and stay mounted, so
    // there is no second mount to trigger it again. No-ops once it succeeds.
    void get().rehydrate();
  },

  async cancelAll(tool) {
    // Scoped to ONE tool: the image tab's DỪNG must not kill running video
    // jobs the user never asked to stop.
    const running = get().jobs.filter(
      (j) =>
        j.tool === tool &&
        j.requestId > 0 &&
        (j.status === "queued" || j.status === "running"),
    );
    const results = await Promise.allSettled(
      running.map((j) => cancelActivity(j.requestId)),
    );
    const failures = results.filter((r) => r.status === "rejected").length;
    if (failures > 0) {
      setError(set, tool, `Không dừng được ${failures}/${running.length} job.`);
    }
  },

  async rehydrate() {
    // Jobs live only in memory, so a reload (or a poll that gave up during a
    // network blip) used to orphan work the backend was still doing. Rebuild
    // from the activity log instead. Both tabs mount at once and each calls
    // this, so it runs once per session, not once per tab.
    if (rehydrated) return;
    rehydrated = true;
    let items;
    try {
      items = (await getActivityList({ limit: REHYDRATE_LIMIT, type: TAB_TOOLS }))
        .items;
    } catch {
      rehydrated = false; // agent down — a later dispatch retries this
      return; // the tab still renders
    }
    const known = new Set(get().jobs.map((j) => j.requestId));
    const restored: Job[] = [];
    for (const it of items) {
      if (known.has(it.id)) continue;
      // `gen_image` is NOT tab-owned: the canvas dispatches it too (see
      // store/generation.ts and the backend pipeline executor). Adopting a
      // canvas row would put someone else's job in this tab's list — and
      // then DỪNG would cancel work the user never started here. A canvas
      // row carries a node_id; a tab dispatch never does.
      if (it.node_id !== null && it.node_id !== undefined) continue;
      const type = String(it.type);
      const job = blankJob(
        it.id,
        UI_TOOL_BY_TYPE[type] ?? type,
        "",
        it.status as JobStatus,
      );
      job.createdAt = Date.parse(it.created_at) || Date.now();
      restored.push(job);
    }
    if (restored.length > 0) {
      set((s) => ({
        jobs: [...s.jobs, ...restored].sort((a, b) => b.createdAt - a.createdAt),
      }));
      // Fill in prompt/result for the restored rows, then resume polling any
      // that are still live. Sequential on purpose: firing one request per
      // restored row in parallel put a burst at app start on a single-process
      // agent that is also running the Flow worker.
      void (async () => {
        for (const job of restored) await refreshOnce(set, job.requestId);
      })();
      startPolling(set, get);
    }
  },
}));

function blankJob(
  requestId: number,
  tool: string,
  prompt: string,
  status: JobStatus,
): Job {
  return {
    requestId,
    tool,
    prompt,
    status,
    mediaIds: [],
    slotErrors: [],
    error: null,
    modelNote: null,
    stale: false,
    createdAt: Date.now(),
  };
}

function setBusy(set: SetFn, tool: string, value: boolean): void {
  set((s) => ({ busy: { ...s.busy, [tool]: value } }));
}

function setError(set: SetFn, tool: string, value: string | null): void {
  set((s) => ({ error: { ...s.error, [tool]: value } }));
}

type SetFn = (fn: (s: JobsState) => Partial<JobsState>) => void;

const TERMINAL: JobStatus[] = ["done", "failed", "canceled", "timeout"];

function isLive(job: Job): boolean {
  return job.requestId > 0 && !TERMINAL.includes(job.status);
}

/** Fields worth reading off a finished row. */
function jobPatchFrom(row: RequestDTO): Partial<Job> {
  const patchData: Partial<Job> = {
    status: row.status,
    error: row.error,
    stale: false,
  };
  if (typeof row.params?.prompt === "string") {
    patchData.prompt = row.params.prompt;
  }
  if (row.status === "done" || row.status === "timeout") {
    patchData.mediaIds = mediaIdsFrom(row.result);
    patchData.slotErrors = slotErrorsFrom(row.result);
    patchData.modelNote = modelNoteFrom(row.result);
  }
  return patchData;
}

async function refreshOnce(set: SetFn, requestId: number): Promise<void> {
  try {
    patch(set, requestId, jobPatchFrom(await getRequest(requestId)));
  } catch {
    /* the poll loop reports connectivity problems */
  }
}

// ── single poll loop ──────────────────────────────────────────────────────
// `polling` is the guard, NOT the timer handle. tick() has to clear its own
// handle when it starts, so using the handle as the guard left it null for
// the whole time the tick spent awaiting the network — and any dispatch
// arriving in that window opened a second, permanent chain. That is the
// N-concurrent-loops bug this single poller was written to remove, so the
// guard has to stay true across the await, not just between ticks.
let polling = false;
let networkRetries = 0;

function startPolling(set: SetFn, get: () => JobsState): void {
  if (polling) return; // already running
  polling = true;
  const tick = async () => {
    const live = get().jobs.filter(isLive);
    if (live.length === 0) {
      networkRetries = 0;
      polling = false; // nothing to watch; the next enqueue restarts the loop
      return;
    }
    let hadNetworkError = false;
    for (const job of live) {
      try {
        patch(set, job.requestId, jobPatchFrom(await getRequest(job.requestId)));
      } catch {
        hadNetworkError = true;
      }
    }
    if (hadNetworkError) {
      // Mark stale rather than failed: the backend is still working, and
      // stamping `failed` after a 12-second blip stranded live jobs whose
      // media then became unreachable from the UI.
      networkRetries += 1;
      if (networkRetries >= MAX_NETWORK_RETRIES) {
        for (const job of live) patch(set, job.requestId, { stale: true });
      }
    } else {
      networkRetries = 0;
    }
    setTimeout(() => void tick(), POLL_INTERVAL_MS);
  };
  setTimeout(() => void tick(), POLL_INTERVAL_MS);
}

function patch(set: SetFn, requestId: number, data: Partial<Job>): void {
  set((s) => {
    const idx = s.jobs.findIndex((j) => j.requestId === requestId);
    if (idx < 0) return {};
    const current = s.jobs[idx];
    // No-op guard: without it every tick allocated a fresh jobs array and
    // re-rendered every subscriber even when nothing had changed.
    let changed = false;
    for (const [k, v] of Object.entries(data) as [keyof Job, unknown][]) {
      const before = current[k];
      if (Array.isArray(before) && Array.isArray(v)) {
        if (before.length !== v.length || before.some((x, i) => x !== v[i])) {
          changed = true;
          break;
        }
      } else if (before !== v) {
        changed = true;
        break;
      }
    }
    if (!changed) return {};
    const jobs = s.jobs.slice();
    jobs[idx] = { ...current, ...data };
    return { jobs };
  });
}
