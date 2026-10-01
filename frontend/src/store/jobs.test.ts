import { beforeEach, describe, expect, it, vi } from "vitest";

/** Tests for the tab-mode jobs store.
 *
 * This module owns real money and real user work: it decides what gets
 * dispatched to Flow, what gets cancelled, and whether a running job stays
 * visible. It also holds module-level state (the single poll timer, the
 * in-flight project promise, the rehydrate latch), so every test imports a
 * fresh copy via resetModules rather than sharing one across cases.
 */

const api = vi.hoisted(() => ({
  createRequest: vi.fn(),
  getRequest: vi.fn(),
  getAuthMe: vi.fn(),
  cancelActivity: vi.fn(),
  getActivityList: vi.fn(),
  listBoards: vi.fn(),
  createBoard: vi.fn(),
  ensureBoardProject: vi.fn(),
}));

vi.mock("@/api/client", () => api);

const PRO = { paygate_tier: "PAYGATE_TIER_ONE" };

/** request id → the prompt that was dispatched under it. */
const dispatched = new Map<number, string>();

/** A backend row shaped like RequestDTO. */
function row(id: number, over: Record<string, unknown> = {}) {
  return {
    id,
    node_id: null,
    type: "gen_video_text",
    params: { prompt: `p${id}` },
    status: "queued",
    result: {},
    error: null,
    created_at: new Date().toISOString(),
    finished_at: null,
    ...over,
  };
}

async function freshStore() {
  vi.resetModules();
  const mod = await import("./jobs");
  return mod.useJobsStore;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  api.getAuthMe.mockResolvedValue(PRO);
  api.listBoards.mockResolvedValue([{ id: 7, name: "default_project" }]);
  api.ensureBoardProject.mockResolvedValue({ flow_project_id: "proj-abc" });
  api.getActivityList.mockResolvedValue({ items: [], next_before_id: null });
  api.cancelActivity.mockResolvedValue(undefined);
  // The fake backend echoes back what it was sent, the way the real one
  // does — a row's params carry the prompt the client dispatched.
  let next = 100;
  dispatched.clear();
  api.createRequest.mockImplementation(
    async (body: { type: string; params: Record<string, unknown> }) => {
      const id = next++;
      dispatched.set(id, String(body.params.prompt ?? ""));
      return row(id, { type: body.type, params: body.params });
    },
  );
  api.getRequest.mockImplementation(async (id: number) =>
    row(id, { params: { prompt: dispatched.get(id) ?? `p${id}` } }),
  );
});

describe("project binding", () => {
  it("binds the board by NAME, not whichever board comes back first", async () => {
    // /api/boards has no ORDER BY, so "boards[0]" silently filed tab jobs
    // under the canvas board's Flow project while the UI showed another name.
    api.listBoards.mockResolvedValue([
      { id: 3, name: "canvas-scratch" },
      { id: 7, name: "default_project" },
    ]);
    const useJobs = await freshStore();

    await useJobs.getState().ensureProject();

    expect(api.ensureBoardProject).toHaveBeenCalledWith(7);
    expect(api.createBoard).not.toHaveBeenCalled();
    expect(useJobs.getState().projectName).toBe("default_project");
  });

  it("creates the named board when it does not exist yet", async () => {
    api.listBoards.mockResolvedValue([]);
    api.createBoard.mockResolvedValue({ id: 9, name: "default_project" });
    const useJobs = await freshStore();

    await useJobs.getState().ensureProject();

    expect(api.createBoard).toHaveBeenCalledWith("default_project");
    expect(api.ensureBoardProject).toHaveBeenCalledWith(9);
  });

  it("de-dupes concurrent bootstraps so one click cannot create two boards", async () => {
    api.listBoards.mockResolvedValue([]);
    api.createBoard.mockResolvedValue({ id: 9, name: "default_project" });
    const useJobs = await freshStore();

    const [a, b] = await Promise.all([
      useJobs.getState().ensureProject(),
      useJobs.getState().ensureProject(),
    ]);

    expect(a).toBe("proj-abc");
    expect(b).toBe("proj-abc");
    expect(api.createBoard).toHaveBeenCalledTimes(1);
  });
});

describe("enqueue", () => {
  it("refuses to dispatch when the plan tier is unknown", async () => {
    // Without this guard one click became N failed rows all showing the raw
    // `paygate_tier_unknown` token.
    api.getAuthMe.mockResolvedValue({ paygate_tier: null });
    const useJobs = await freshStore();

    await useJobs.getState().enqueue("gen_video_text", ["a", "b"], {});

    expect(api.createRequest).not.toHaveBeenCalled();
    expect(useJobs.getState().jobs).toHaveLength(0);
    expect(useJobs.getState().error["gen_video_text"]).toMatch(/gói Flow/i);
    expect(useJobs.getState().busy["gen_video_text"]).toBe(false);
  });

  it("dispatches one request per non-empty prompt line", async () => {
    const useJobs = await freshStore();

    await useJobs
      .getState()
      .enqueue("gen_video_text", ["a", "  ", "b"], { duration_s: 8 });

    expect(api.createRequest).toHaveBeenCalledTimes(2);
    expect(api.createRequest.mock.calls[0][0]).toEqual({
      type: "gen_video_text",
      params: { duration_s: 8, prompt: "a", project_id: "proj-abc" },
    });
    // The tier is resolved by the backend from the live extension signal;
    // the UI must never guess it into the payload.
    expect(api.createRequest.mock.calls[0][0].params).not.toHaveProperty(
      "paygate_tier",
    );
    expect(useJobs.getState().jobs).toHaveLength(2);
  });

  it("keeps busy and error separate per tool", async () => {
    api.createRequest.mockRejectedValueOnce(new Error("boom"));
    const useJobs = await freshStore();

    await useJobs.getState().enqueue("gen_image", [], {});

    expect(useJobs.getState().error["gen_image"]).toBeTruthy();
    // The video tab must not render the image tab's error.
    expect(useJobs.getState().error["gen_video_text"]).toBeUndefined();
  });

  it("gives failed-to-dispatch rows distinct ids within the same millisecond", async () => {
    // `-Date.now()` collided for two rejections in the same tick, which made
    // React reuse one row for both.
    api.createRequest.mockRejectedValue(new Error("agent down"));
    const useJobs = await freshStore();

    await useJobs.getState().enqueue("gen_video_text", ["a", "b"], {});

    const ids = useJobs.getState().jobs.map((j) => j.requestId);
    expect(new Set(ids).size).toBe(2);
    expect(ids.every((i) => i < 0)).toBe(true);
  });

  it("ignores a second submit while the first is still dispatching", async () => {
    let release: (v: unknown) => void = () => undefined;
    api.getAuthMe.mockReturnValue(new Promise((r) => (release = r)));
    const useJobs = await freshStore();

    const first = useJobs.getState().enqueue("gen_video_text", ["a"], {});
    const second = useJobs.getState().enqueue("gen_video_text", ["a"], {});
    release(PRO);
    await Promise.all([first, second]);

    expect(api.createRequest).toHaveBeenCalledTimes(1);
  });
});

describe("cancelAll", () => {
  it("cancels only the requested tool's live jobs", async () => {
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["v"], {});
    await useJobs.getState().enqueue("gen_image", ["i"], {});
    const videoId = useJobs
      .getState()
      .jobs.find((j) => j.tool === "gen_video_text")!.requestId;

    await useJobs.getState().cancelAll("gen_image");

    const cancelled = api.cancelActivity.mock.calls.map((c) => c[0]);
    expect(cancelled).toHaveLength(1);
    expect(cancelled).not.toContain(videoId);
  });

  it("surfaces a cancel failure instead of swallowing it", async () => {
    api.cancelActivity.mockRejectedValue(new Error("409"));
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["v"], {});

    await useJobs.getState().cancelAll("gen_video_text");

    expect(useJobs.getState().error["gen_video_text"]).toMatch(/không dừng được/i);
  });
});

describe("polling", () => {
  it("polls every live job from ONE loop and stops when all are terminal", async () => {
    vi.useFakeTimers();
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["a", "b"], {});
    api.getRequest.mockClear();

    api.getRequest.mockImplementation(async (id: number) =>
      row(id, {
        status: "done",
        result: { media_ids: [`m${id}`], slot_errors: [null] },
      }),
    );
    await vi.advanceTimersByTimeAsync(1500);

    expect(api.getRequest).toHaveBeenCalledTimes(2); // one tick, both jobs
    const jobs = useJobs.getState().jobs;
    expect(jobs.map((j) => j.status)).toEqual(["done", "done"]);
    expect(jobs[0].mediaIds).toEqual([`m${jobs[0].requestId}`]);

    // Everything is terminal, so the loop must not schedule another tick.
    api.getRequest.mockClear();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(api.getRequest).not.toHaveBeenCalled();
  });

  it("marks a job stale on a long outage instead of declaring it failed", async () => {
    // The backend keeps working through a network blip; stamping `failed`
    // stopped polling and made the finished media unreachable from the UI.
    vi.useFakeTimers();
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["a"], {});

    api.getRequest.mockRejectedValue(new Error("network"));
    await vi.advanceTimersByTimeAsync(1500 * 10);

    const job = useJobs.getState().jobs[0];
    expect(job.stale).toBe(true);
    expect(job.status).toBe("queued"); // NOT failed

    // And it recovers on its own once the agent is back.
    api.getRequest.mockImplementation(async (id: number) =>
      row(id, { status: "done", result: { media_ids: ["m1"] } }),
    );
    await vi.advanceTimersByTimeAsync(1500);
    expect(useJobs.getState().jobs[0].status).toBe("done");
    expect(useJobs.getState().jobs[0].stale).toBe(false);
  });

  it("does not allocate a new jobs array when nothing changed", async () => {
    // Every tick used to produce a fresh array, re-rendering every subscriber
    // even when no field had moved.
    vi.useFakeTimers();
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["a"], {});
    const before = useJobs.getState().jobs;

    await vi.advanceTimersByTimeAsync(1500 * 3);

    expect(useJobs.getState().jobs).toBe(before); // same reference
  });

  it("surfaces the backend's model substitution on the finished job", async () => {
    vi.useFakeTimers();
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["a"], {});

    api.getRequest.mockImplementation(async (id: number) =>
      row(id, {
        status: "done",
        result: {
          media_ids: ["m1"],
          model_substitutions: [
            { field: "quality", requested: "lite_relaxed", effective: "lite" },
          ],
        },
      }),
    );
    await vi.advanceTimersByTimeAsync(1500);

    expect(useJobs.getState().jobs[0].modelNote).toMatch(/lite_relaxed/);
    expect(useJobs.getState().jobs[0].modelNote).toMatch(/credit/i);
  });
});

describe("rehydrate", () => {
  it("restores jobs the backend is still running after a reload", async () => {
    api.getActivityList.mockResolvedValue({
      items: [
        {
          id: 55,
          type: "gen_video_text",
          status: "running",
          node_id: null,
          node_short_id: null,
          created_at: new Date().toISOString(),
          finished_at: null,
          duration_ms: null,
        },
      ],
      next_before_id: null,
    });
    api.getRequest.mockImplementation(async (id: number) =>
      row(id, { status: "running", params: { prompt: "restored prompt" } }),
    );
    const useJobs = await freshStore();

    await useJobs.getState().rehydrate();
    await vi.waitFor(() =>
      expect(useJobs.getState().jobs[0].prompt).toBe("restored prompt"),
    );

    expect(useJobs.getState().jobs).toHaveLength(1);
    expect(useJobs.getState().jobs[0].requestId).toBe(55);
  });

  it("runs once even though both tabs ask for it", async () => {
    const useJobs = await freshStore();

    await useJobs.getState().rehydrate();
    await useJobs.getState().rehydrate();

    expect(api.getActivityList).toHaveBeenCalledTimes(1);
  });

  it("stays retryable when the agent is down", async () => {
    api.getActivityList.mockRejectedValueOnce(new Error("offline"));
    const useJobs = await freshStore();

    await useJobs.getState().rehydrate();
    await useJobs.getState().rehydrate();

    expect(api.getActivityList).toHaveBeenCalledTimes(2);
  });
});

describe("regressions found by adversarial review", () => {
  it("never runs two poll chains, even when a dispatch lands mid-tick", async () => {
    // `startPolling` guarded on the timer HANDLE, but tick() cleared that
    // handle at its start and only reassigned it after awaiting every job.
    // A dispatch arriving inside that window opened a second permanent chain
    // — the exact N-loops bug the single-poller rewrite existed to remove.
    vi.useFakeTimers();
    const useJobs = await freshStore();

    let releaseFirstPoll: (() => void) | null = null;
    api.getRequest.mockImplementationOnce(
      (id: number) =>
        new Promise((resolve) => {
          releaseFirstPoll = () =>
            resolve(row(id, { params: { prompt: dispatched.get(id) ?? "" } }));
        }),
    );

    await useJobs.getState().enqueue("gen_video_text", ["a"], {});
    // Enter tick(): the poll is now parked on the pending getRequest, which
    // is precisely when the guard used to be open.
    await vi.advanceTimersByTimeAsync(1500);
    expect(releaseFirstPoll).not.toBeNull();

    // A second dispatch lands while the first tick is still awaiting.
    await useJobs.getState().enqueue("gen_video_text", ["b"], {});
    releaseFirstPoll!();
    await vi.advanceTimersByTimeAsync(0);

    // One interval, two live jobs → exactly two polls if there is one chain.
    api.getRequest.mockClear();
    await vi.advanceTimersByTimeAsync(1500);
    expect(api.getRequest).toHaveBeenCalledTimes(2);
  });

  it("does not adopt canvas jobs during rehydrate", async () => {
    // gen_image is dispatched by the canvas too (store/generation.ts and the
    // backend pipeline), so filtering on type alone pulls canvas rows into
    // the tab's list. Canvas rows carry a node_id; tab rows never do.
    api.getActivityList.mockResolvedValue({
      items: [
        {
          id: 900,
          type: "gen_image",
          status: "running",
          node_id: 42, // ← belongs to a canvas node
          node_short_id: "n42",
          created_at: new Date().toISOString(),
          finished_at: null,
          duration_ms: null,
        },
        {
          id: 901,
          type: "gen_image",
          status: "running",
          node_id: null, // ← dispatched from the tab
          node_short_id: null,
          created_at: new Date().toISOString(),
          finished_at: null,
          duration_ms: null,
        },
      ],
      next_before_id: null,
    });
    const useJobs = await freshStore();

    await useJobs.getState().rehydrate();

    const ids = useJobs.getState().jobs.map((j) => j.requestId);
    expect(ids).toEqual([901]);
  });

  it("cancelAll never touches a job the tab did not dispatch", async () => {
    api.getActivityList.mockResolvedValue({
      items: [
        {
          id: 900,
          type: "gen_image",
          status: "running",
          node_id: 42,
          node_short_id: "n42",
          created_at: new Date().toISOString(),
          finished_at: null,
          duration_ms: null,
        },
      ],
      next_before_id: null,
    });
    const useJobs = await freshStore();

    await useJobs.getState().rehydrate();
    await useJobs.getState().cancelAll("gen_image");

    expect(api.cancelActivity).not.toHaveBeenCalled();
  });
});

describe("important issues from the same review", () => {
  it("does not leave a finished job permanently marked stale", async () => {
    // stale is decided from the snapshot taken at the START of the tick, so
    // a job that completed during that tick used to be stamped stale — and
    // being terminal, it was never polled again to clear it.
    vi.useFakeTimers();
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["a", "b"], {});
    const [first, second] = useJobs.getState().jobs.map((j) => j.requestId);

    // Every tick: one job finishes, the other's poll fails.
    api.getRequest.mockImplementation(async (id: number) => {
      if (id === first) {
        return row(id, { status: "done", result: { media_ids: ["m1"] } });
      }
      throw new Error("network");
    });
    await vi.advanceTimersByTimeAsync(1500 * 10);

    const done = useJobs.getState().jobs.find((j) => j.requestId === first)!;
    expect(done.status).toBe("done");
    expect(done.stale).toBe(false);
    // The one that really is unreachable stays flagged.
    const stuck = useJobs.getState().jobs.find((j) => j.requestId === second)!;
    expect(stuck.stale).toBe(true);
  });

  it("retries rehydrate on a later dispatch when the agent was down at mount", async () => {
    // Both tabs mount once and stay mounted, so a failed first rehydrate had
    // nothing left to re-trigger it: a job running from a previous session
    // stayed invisible for the rest of this one.
    api.getActivityList.mockRejectedValueOnce(new Error("offline"));
    const useJobs = await freshStore();
    await useJobs.getState().rehydrate();
    expect(useJobs.getState().jobs).toHaveLength(0);

    api.getActivityList.mockResolvedValue({
      items: [
        {
          id: 77,
          type: "gen_video_text",
          status: "running",
          node_id: null,
          node_short_id: null,
          created_at: new Date().toISOString(),
          finished_at: null,
          duration_ms: null,
        },
      ],
      next_before_id: null,
    });

    await useJobs.getState().enqueue("gen_video_text", ["a"], {});
    await vi.waitFor(() =>
      expect(
        useJobs.getState().jobs.some((j) => j.requestId === 77),
      ).toBe(true),
    );
  });

  it("drops a stale project id when the backend rejects it", async () => {
    // The cached flow_project_id outlives a deleted board, and every later
    // dispatch fails the same way until a full reload.
    const useJobs = await freshStore();
    await useJobs.getState().enqueue("gen_video_text", ["a"], {});
    expect(useJobs.getState().projectId).toBe("proj-abc");

    api.createRequest.mockRejectedValue(new Error("invalid_project_id"));
    await useJobs.getState().enqueue("gen_video_text", ["b"], {});

    expect(useJobs.getState().projectId).toBeNull();
  });
});

describe("rehydrate load shape", () => {
  it("restores rows one at a time instead of bursting the agent", async () => {
    // The agent is a single process that also runs the Flow worker, so
    // firing one request per restored row at app start put a burst exactly
    // where it hurts most.
    const rows = Array.from({ length: 6 }, (_, i) => ({
      id: 500 + i,
      type: "gen_video_text",
      status: "done",
      node_id: null,
      node_short_id: null,
      created_at: new Date().toISOString(),
      finished_at: null,
      duration_ms: null,
    }));
    api.getActivityList.mockResolvedValue({ items: rows, next_before_id: null });

    let inFlight = 0;
    let peak = 0;
    api.getRequest.mockImplementation(async (id: number) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 1));
      inFlight -= 1;
      return row(id, { status: "done" });
    });

    const useJobs = await freshStore();
    await useJobs.getState().rehydrate();
    await vi.waitFor(() => expect(api.getRequest).toHaveBeenCalledTimes(6));

    expect(peak).toBe(1);
  });
});

describe("enqueueRow (per-row params)", () => {
  it("keeps each row's own params with its own prompt", async () => {
    // Pairing images to prompts BY POSITION across two parallel lists is the
    // original's number-one silent bug: one missing prompt shifts the whole
    // batch, runs anyway, and bills for it. Rows carry both halves together.
    const useJobs = await freshStore();

    await useJobs.getState().enqueueRow("gen_video", [
      { prompt: "a", params: { start_media_id: "img-a" } },
      { prompt: "b", params: { start_media_id: "img-b" } },
    ]);

    const sent = api.createRequest.mock.calls.map((c) => c[0].params);
    expect(sent[0].prompt).toBe("a");
    expect(sent[0].start_media_id).toBe("img-a");
    expect(sent[1].prompt).toBe("b");
    expect(sent[1].start_media_id).toBe("img-b");
  });

  it("files OMNI jobs under the tab that dispatched them", async () => {
    // OMNI Flash is a different backend handler but the same tab, so its
    // jobs must land in that tab's list — otherwise the user watches an
    // empty panel while the work runs.
    const useJobs = await freshStore();

    await useJobs
      .getState()
      .enqueueRow("gen_video_omni", [{ prompt: "a", params: { duration_s: 6 } }]);

    expect(api.createRequest.mock.calls[0][0].type).toBe("gen_video_omni");
    expect(useJobs.getState().jobs[0].tool).toBe("gen_video");
  });

  it("still refuses to dispatch when no row has a prompt", async () => {
    const useJobs = await freshStore();
    await useJobs
      .getState()
      .enqueueRow("gen_video", [{ prompt: "   ", params: { start_media_id: "x" } }]);
    expect(api.createRequest).not.toHaveBeenCalled();
    expect(useJobs.getState().error["gen_video"]).toBeTruthy();
  });
});
