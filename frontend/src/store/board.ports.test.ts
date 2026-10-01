import { beforeEach, describe, expect, it, vi } from "vitest";

/** The names the canvas puts on the wires it draws.
 *
 * `EdgeCreate` has accepted `source_port`/`target_port` since P6 and the canvas
 * never sent either, so every hand-drawn wire arrived nameless. The executor
 * tells a start frame from a character reference BY the port, so nameless left
 * it guessing — and the guess it made, reference sheet as opening frame, bought
 * a paid clip of a contact sheet. It also left the `character_N` sockets
 * reachable only by importing a JSON file.
 */

const api = vi.hoisted(() => ({
  listBoards: vi.fn(),
  getBoard: vi.fn(),
  createBoard: vi.fn(),
  createEdge: vi.fn(),
  patchNode: vi.fn(),
  deleteEdge: vi.fn(),
}));

vi.mock("@/api/client", () => api);

function installStorage(): void {
  const data = new Map<string, string>();
  (globalThis as { localStorage?: Storage }).localStorage = {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: (i: number) => Array.from(data.keys())[i] ?? null,
    get length() {
      return data.size;
    },
  } as Storage;
}

installStorage();

let nextEdgeId = 100;

function node(id: number, type: string, data: Record<string, unknown> = {}) {
  return {
    id, board_id: 1, short_id: `S${id}`, type, x: 0, y: 0, w: 260, h: 180,
    status: "idle", data: { title: type, ...data },
  };
}

function detail(nodes: ReturnType<typeof node>[]) {
  return {
    board: { id: 1, name: "B", created_at: "", updated_at: "" },
    nodes,
    edges: [],
  };
}

async function loaded(nodes: ReturnType<typeof node>[]) {
  vi.resetModules();
  api.getBoard.mockResolvedValue(detail(nodes));
  const mod = await import("./board");
  await mod.useBoardStore.getState().loadInitialBoard();
  return mod.useBoardStore;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  nextEdgeId = 100;
  api.listBoards.mockResolvedValue([{ id: 1, name: "B" }]);
  api.createEdge.mockImplementation(async (input: Record<string, unknown>) => ({
    id: nextEdgeId++,
    board_id: 1,
    source_id: input.source_id,
    target_id: input.target_id,
    kind: input.kind ?? "ref",
    source_variant_idx: null,
    source_port: input.source_port ?? null,
    target_port: input.target_port ?? null,
  }));
});

describe("a hand-drawn wire arrives named", () => {
  it("numbers character sockets in wiring order", async () => {
    // The number is the order the characters appear in the prompt, so swapping
    // two of them is a different video.
    const store = await loaded([
      node(1, "character", { mediaId: "m-a" }),
      node(2, "character", { mediaId: "m-b" }),
      node(3, "video"),
    ]);

    await store.getState().addEdgeFromConnection("1", "3");
    await store.getState().addEdgeFromConnection("2", "3");

    expect(api.createEdge.mock.calls[0][0].target_port).toBe("character_1");
    expect(api.createEdge.mock.calls[1][0].target_port).toBe("character_2");
  });

  it("names a prompt wire so it is not read as an empty start frame", async () => {
    const store = await loaded([node(1, "prompt"), node(2, "video")]);

    await store.getState().addEdgeFromConnection("1", "2");

    expect(api.createEdge.mock.calls[0][0].target_port).toBe("prompt");
  });

  it("leaves the ordinary image chain unnamed for the backend to read by type", async () => {
    // `image → video` is the chain every packaged workflow is built on and the
    // backend already resolves it correctly; inventing a port here would be
    // guessing where guessing is not needed.
    const store = await loaded([node(1, "image"), node(2, "video")]);

    await store.getState().addEdgeFromConnection("1", "2");

    expect(api.createEdge.mock.calls[0][0].target_port).toBeUndefined();
  });

  it("carries the port back so the next wire can count what is taken", async () => {
    const store = await loaded([
      node(1, "character", { mediaId: "m-a" }),
      node(2, "video"),
    ]);

    await store.getState().addEdgeFromConnection("1", "2");

    const edge = store.getState().edges.at(-1);
    expect(edge?.data?.targetPort).toBe("character_1");
  });

  it("a character wired to an image is not a character socket", async () => {
    // Component mode is a video thing. On an image node the same wire is an
    // ordinary reference, which the backend already handles by type.
    const store = await loaded([
      node(1, "character", { mediaId: "m-a" }),
      node(2, "image"),
    ]);

    await store.getState().addEdgeFromConnection("1", "2");

    expect(api.createEdge.mock.calls[0][0].target_port).toBeUndefined();
  });
});
