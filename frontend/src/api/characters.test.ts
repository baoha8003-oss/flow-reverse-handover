import { beforeEach, describe, expect, it, vi } from "vitest";
import { createCharacterOnFlow, saveCharacter } from "./client";

/** Two ways to register a character, and they must not be confused.
 *
 * `createCharacterOnFlow` asks Flow to MAKE one and keeps the id Flow returns.
 * `saveCharacter` records an id the user already has. They differ by one path
 * segment, and sending a create to the plain endpoint would store a record with
 * no `entityId` that looks registered and cannot be dispatched — the
 * "reads as configured, silently does nothing" failure this codebase keeps
 * finding. So the path each one posts to is pinned here.
 *
 * The create must also never carry an `entityId`: the whole point is that Flow
 * issues it. A client that sent one would be guessing at the id it is asking
 * for.
 */

const calls: Array<{ url: string; init: RequestInit }> = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          id: "c1",
          name: "MaiAnh",
          projectId: "p",
          entityId: "e-from-flow",
          voice: "Achernar",
          voiceStyle: "",
          info: "",
          mediaId: "",
          usable: true,
        }),
    } as unknown as Response);
  });
});

function sentBody(): Record<string, unknown> {
  return JSON.parse(String(calls[0].init.body)) as Record<string, unknown>;
}

describe("creating a character on Flow", () => {
  it("posts to the create-on-flow path, not the plain registry", async () => {
    await createCharacterOnFlow(7, { name: "MaiAnh" });
    expect(calls[0].url).toBe("/api/boards/7/characters/create-on-flow");
    expect(calls[0].init.method).toBe("POST");
  });

  it("never sends an entity id, because Flow is the one issuing it", async () => {
    await createCharacterOnFlow(7, { name: "MaiAnh", mediaId: "m1" });
    expect(sentBody()).not.toHaveProperty("entityId");
  });

  it("returns the id Flow gave back so the node can link to it", async () => {
    const saved = await createCharacterOnFlow(7, { name: "MaiAnh" });
    expect(saved.entityId).toBe("e-from-flow");
    expect(saved.id).toBe("c1");
  });

  it("surfaces a refusal rather than resolving with nothing", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({
        ok: false,
        status: 502,
        json: () => Promise.resolve({ detail: "Flow refused the character: nope" }),
      } as unknown as Response),
    );
    await expect(createCharacterOnFlow(7, { name: "X" })).rejects.toThrow(/refused/);
  });
});

describe("registering a character made by hand", () => {
  it("still posts to the plain registry path", async () => {
    await saveCharacter(7, { name: "MaiAnh", entityId: "pasted-id" });
    expect(calls[0].url).toBe("/api/boards/7/characters");
  });

  it("carries the pasted id through", async () => {
    await saveCharacter(7, { name: "MaiAnh", entityId: "pasted-id" });
    expect(sentBody().entityId).toBe("pasted-id");
  });
});
