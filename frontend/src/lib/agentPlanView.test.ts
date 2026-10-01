import { describe, expect, it } from "vitest";
import { DIAGRAM_THRESHOLD, planView, toMermaid } from "./agentPlanView";
import type { AgentActionDTO, AgentFinding } from "../api/client";

function action(over: Partial<AgentActionDTO> = {}): AgentActionDTO {
  return {
    id: 1,
    kind: "create_node",
    summary: "Thêm node image",
    payload: {},
    provider: null,
    ...over,
  };
}

function finding(over: Partial<AgentFinding> = {}): AgentFinding {
  return { rule: "unknown_tag", severity: "error", message: "x", span: "", ...over };
}

describe("apply gate", () => {
  it("blocks apply while any finding is an error", () => {
    const view = planView({ actions: [action()], intents: [], findings: [finding()] });
    expect(view.canApply).toBe(false);
    expect(view.errors).toHaveLength(1);
  });

  it("allows apply when only warnings are present", () => {
    const view = planView({
      actions: [action()],
      intents: [],
      findings: [finding({ severity: "warning" })],
    });
    expect(view.canApply).toBe(true);
    expect(view.warnings).toHaveLength(1);
    expect(view.errors).toHaveLength(0);
  });

  it("allows apply on a clean plan", () => {
    expect(planView({ actions: [action()], intents: [], findings: [] }).canApply).toBe(true);
  });
});

describe("run offers", () => {
  it("phrases a run as an offer, never as a state", () => {
    const view = planView({
      actions: [],
      intents: [{ kind: "run_node", nodeIds: [4, 5], note: "Chưa chạy gì" }],
      findings: [],
    });
    expect(view.runOffer).toContain("Đề xuất");
    expect(view.runOffer).toContain("xem giá");
    expect(view.runOffer).not.toMatch(/đang chạy|đã chạy/i);
    expect(view.runNodeIds).toEqual([4, 5]);
  });

  it("has no offer when the agent suggested no run", () => {
    const view = planView({ actions: [action()], intents: [], findings: [] });
    expect(view.runOffer).toBeNull();
    expect(view.runNodeIds).toEqual([]);
  });
});

describe("provider badge", () => {
  it("names the provider when every action came from one", () => {
    const view = planView({
      actions: [action({ provider: "claude" }), action({ id: 2, provider: "claude" })],
      intents: [],
      findings: [],
    });
    expect(view.provider).toBe("claude");
  });

  it("names nobody when a batch mixed providers", () => {
    const view = planView({
      actions: [action({ provider: "claude" }), action({ id: 2, provider: "gemini" })],
      intents: [],
      findings: [],
    });
    expect(view.provider).toBeNull();
  });

  it("names nobody when no provider was recorded", () => {
    expect(planView({ actions: [action()], intents: [], findings: [] }).provider).toBeNull();
  });
});

describe("diagram", () => {
  const wired = (count: number): AgentActionDTO[] => {
    const nodes: AgentActionDTO[] = [];
    for (let i = 1; i <= count; i++) {
      nodes.push(action({ id: i, payload: { node_id: i, title: `N${i}`, type: "image" } }));
    }
    for (let i = 1; i < count; i++) {
      nodes.push(
        action({
          id: 100 + i,
          kind: "connect_nodes",
          payload: { source: i, target: i + 1, target_port: "start_frame" },
        })
      );
    }
    return nodes;
  };

  it("stays null for a plan short enough to just read", () => {
    expect(toMermaid(wired(2))).toBeNull();
  });

  it("draws once the plan passes the threshold", () => {
    const diagram = toMermaid(wired(DIAGRAM_THRESHOLD));
    expect(diagram).toContain("flowchart LR");
    expect(diagram).toContain("start_frame");
  });

  it("stays null when there are nodes but no wires", () => {
    // A diagram of disconnected boxes is a list with extra steps.
    const nodes = [1, 2, 3, 4, 5].map((i) =>
      action({ id: i, payload: { node_id: i, title: `N${i}` } })
    );
    expect(toMermaid(nodes)).toBeNull();
  });

  it("does not let a title break the diagram", () => {
    const actions = [
      action({ id: 1, payload: { node_id: 1, title: 'Ảnh "chính" [1]' } }),
      action({ id: 2, payload: { node_id: 2, title: "Clip" } }),
      action({ id: 3, payload: { node_id: 3, title: "Ghép" } }),
      action({
        id: 4,
        kind: "connect_nodes",
        payload: { source: 1, target: 2, target_port: "start_frame" },
      }),
    ];
    const diagram = toMermaid(actions) ?? "";
    expect(diagram).not.toContain('"Ảnh "chính"');
    expect(diagram).toContain("flowchart LR");
  });

  it("makes ids safe for mermaid", () => {
    const actions = [
      action({ id: 1, payload: { node_id: "node_f0-c6.31", title: "A" } }),
      action({ id: 2, payload: { node_id: "node_b2", title: "B" } }),
      action({ id: 3, payload: { node_id: "node_c3", title: "C" } }),
      action({
        id: 4,
        kind: "connect_nodes",
        payload: { source: "node_f0-c6.31", target: "node_b2" },
      }),
    ];
    const diagram = toMermaid(actions) ?? "";
    expect(diagram).toContain("nnode_f0_c6_31");
    expect(diagram).not.toContain("node_f0-c6.31");
  });

  it("skips a wire with a missing end instead of emitting a broken line", () => {
    const actions = [
      action({ id: 1, payload: { node_id: 1, title: "A" } }),
      action({ id: 2, payload: { node_id: 2, title: "B" } }),
      action({ id: 3, kind: "connect_nodes", payload: { source: 1, target: 2 } }),
      action({ id: 4, kind: "connect_nodes", payload: { source: 1 } }),
    ];
    const diagram = toMermaid(actions) ?? "";
    expect(diagram.split("\n").filter((l) => l.includes("-->"))).toHaveLength(1);
  });
});

describe("lines", () => {
  it("falls back to the kind when a summary is missing", () => {
    const view = planView({
      actions: [action({ summary: "" })],
      intents: [],
      findings: [],
    });
    expect(view.lines).toEqual(["create_node"]);
  });

  it("survives a result with nothing in it", () => {
    const view = planView({ actions: [], intents: [], findings: [] });
    expect(view.lines).toEqual([]);
    expect(view.canApply).toBe(true);
    expect(view.diagram).toBeNull();
  });
});
