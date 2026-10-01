import type { AgentActionDTO, AgentFinding, AgentIntent } from "../api/client";

/** Reading an agent proposal, as pure functions.
 *
 * The panel around this is a few divs; the decisions are here so they can be
 * tested without a DOM. Two of them are not cosmetic:
 *
 * - **Apply stays disabled while any finding is an error.** The findings arrive
 *   from the same rules the run enforces, so an error means the board would be
 *   refused after approval — and approval is the only moment the user had to
 *   say no.
 * - **A run intent is never rendered as "running".** The agent cannot dispatch;
 *   the label has to read as an offer, or the panel claims a charge happened.
 */

/** How many nodes make a list worth drawing as a diagram instead. Below this a
 *  diagram is more work to read than the three lines it replaces. */
export const DIAGRAM_THRESHOLD = 4;

export interface AgentPlanView {
  /** One line per change, in the order they were applied. */
  lines: string[];
  /** Blocking findings, which are also why `canApply` is false. */
  errors: AgentFinding[];
  /** Non-blocking findings, shown but not in the way. */
  warnings: AgentFinding[];
  canApply: boolean;
  /** Which AI answered, when they all agree. Mixed providers in one batch is
   *  possible through the chain, and naming one of them would be wrong. */
  provider: string | null;
  /** What the agent suggests running, phrased as an offer. */
  runOffer: string | null;
  runNodeIds: number[];
  /** Mermaid source, or null when the plan is small enough to just read. */
  diagram: string | null;
}

function splitFindings(findings: AgentFinding[]) {
  const errors = findings.filter((f) => f.severity === "error");
  const warnings = findings.filter((f) => f.severity !== "error");
  return { errors, warnings };
}

function soleProvider(actions: AgentActionDTO[]): string | null {
  const named = new Set(
    actions.map((a) => a.provider).filter((p): p is string => !!p)
  );
  return named.size === 1 ? [...named][0] : null;
}

/** `node_f0c631` → `n_f0c631`: Mermaid ids cannot carry every character a
 *  short id might, and a broken diagram renders as an error block. */
function safeId(value: string | number): string {
  return `n${String(value).replace(/[^A-Za-z0-9_]/g, "_")}`;
}

function label(action: AgentActionDTO): string {
  const payload = action.payload ?? {};
  const title = typeof payload.title === "string" ? payload.title : "";
  const type = typeof payload.type === "string" ? payload.type : action.kind;
  return (title || type).replace(/["\[\]{}|]/g, " ").trim() || type;
}

/** A flowchart of what the plan builds, for a plan too big to read as a list. */
export function toMermaid(actions: AgentActionDTO[]): string | null {
  const created = actions.filter((a) => a.kind === "create_node");
  const wires = actions.filter((a) => a.kind === "connect_nodes");
  if (created.length + wires.length < DIAGRAM_THRESHOLD) return null;

  const lines = ["flowchart LR"];
  for (const action of created) {
    const id = action.payload?.node_id ?? action.id;
    lines.push(`  ${safeId(id as string | number)}["${label(action)}"]`);
  }
  for (const wire of wires) {
    const payload = wire.payload ?? {};
    const from = payload.source;
    const to = payload.target;
    if (from === undefined || to === undefined) continue;
    const port = typeof payload.target_port === "string" ? payload.target_port : "";
    const arrow = port ? `-- ${port} -->` : "-->";
    lines.push(
      `  ${safeId(from as string | number)} ${arrow} ${safeId(to as string | number)}`
    );
  }
  // A diagram with nodes but no wires is a list with extra steps.
  return wires.length > 0 ? lines.join("\n") : null;
}

export function planView(result: {
  actions: AgentActionDTO[];
  intents: AgentIntent[];
  findings: AgentFinding[];
}): AgentPlanView {
  const { errors, warnings } = splitFindings(result.findings ?? []);
  const intent = (result.intents ?? [])[0] ?? null;
  const ids = intent?.nodeIds ?? [];
  return {
    lines: (result.actions ?? []).map((a) => a.summary || a.kind),
    errors,
    warnings,
    canApply: errors.length === 0,
    provider: soleProvider(result.actions ?? []),
    // Phrased as an offer, never as a state: the agent has not run anything
    // and must not look like it has.
    runOffer:
      ids.length > 0
        ? `Đề xuất chạy ${ids.length} node — bấm Chạy để xem giá trước`
        : null,
    runNodeIds: ids,
    diagram: toMermaid(result.actions ?? []),
  };
}
