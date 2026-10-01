export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// Map cryptic Flow / pipeline error tokens to a sentence the user can act on.
// Returns null when the token is unrecognised, so the caller falls through to
// the raw message.
export function humanizeBackendError(token: string): string | null {
  const t = token.toLowerCase();
  if (t === "paygate_tier_unknown") {
    return (
      "Flowboard doesn't know your Google Flow plan tier yet — the "
      + "extension hasn't seen a Flow request that exposes it. Open "
      + "https://labs.google/fx/tools/flow in a tab and reload it once, "
      + "then retry. Flowboard refuses to dispatch in this state to "
      + "avoid silently serving Ultra users at the Pro checkpoint."
    );
  }
  if (t === "no_media_id_in_upload_response") {
    return (
      "Google Flow accepted the upload but didn't return a media handle — "
      + "this usually means the image was silently rejected by Flow's "
      + "content filter (logos, watermarks, copyrighted brand imagery). "
      + "Try a different image or download it locally and upload as a file. "
      + "Check the agent terminal for the full Flow response."
    );
  }
  if (t.includes("captcha_failed: no current window")) {
    return (
      "Chrome has no open windows for the extension to attach a Flow tab to. "
      + "Open any Chrome window (or click the extension's '⋯ → Open Flow') "
      + "and retry — Flowboard will reuse the existing window automatically."
    );
  }
  if (t.startsWith("captcha_failed:")) {
    // CAPTCHA failures are rarely the user's fault — surface the underlying
    // reason verbatim but keep the prefix so power-users can grep for it.
    return token;
  }
  if (t.startsWith("public_error_")) {
    // Veo / Imagen content filters are returned verbatim by Flow — these
    // are already self-describing, just prettify the prefix.
    return token.replace(/^PUBLIC_ERROR_/i, "Flow rejected: ").replace(/_/g, " ");
  }
  return null;
}

async function extractErrorMessage(res: Response): Promise<string> {
  let detail: unknown;
  try {
    detail = await res.json();
  } catch {
    try {
      detail = await res.text();
    } catch {
      return `${res.status} ${res.statusText}`;
    }
  }
  const inner =
    typeof detail === "object" && detail !== null && "detail" in detail
      ? (detail as { detail: unknown }).detail
      : detail;
  if (typeof inner === "string" && inner) {
    return humanizeBackendError(inner) ?? inner;
  }
  if (inner && typeof inner === "object") {
    const obj = inner as Record<string, unknown>;
    if (typeof obj.message === "string" && obj.message) {
      return humanizeBackendError(obj.message) ?? obj.message;
    }
    try {
      return JSON.stringify(inner);
    } catch {
      // fall through
    }
  }
  return `${res.status} ${res.statusText}`;
}

export interface WsStats {
  connected: boolean;
  /** Is a Flow tab open at all. Null until a probe has run, and null is not
   *  false: colouring "not asked yet" as broken sends people to fix a browser
   *  that may be fine. */
  flow_tab_present: boolean | null;
  /** Can that tab sign a call — does the page still carry its CSRF token. This
   *  is the condition that actually blocks a dispatch. */
  at_token_present: boolean | null;
  /** How old that answer is. Shown because the field it replaced, the age of a
   *  captured Bearer token, kept ticking while every call failed — which read
   *  as a healthy bridge for a week. */
  flow_probe_age_s: number | null;
  /** Set when the last RPC failed because the page could not sign it, cleared
   *  by the next success. A sticky state outliving the condition it describes
   *  is its own bug: the user reloads the tab, generation works, and the panel
   *  still says they are signed out. */
  page_unsigned: boolean | null;
  /** Which bridge protocol is in use — "batch" since the September 2026
   *  migration. Kept visible so a stale extension build is diagnosable. */
  transport: string;
  /** Which extension build is loaded. One forgotten reload cost an afternoon of
   *  blind retries, because nothing could answer this. */
  extension_version: string | null;
  /** The RPC id of the last failure, never the envelope: `f.req` carries the
   *  prompt and, once minted, the captcha token. */
  last_failure: string | null;
  pending: number;
  request_count: number;
  success_count: number;
  failed_count: number;
  last_error: string | null;
}

export interface HealthResponse {
  ok: boolean;
  extension_connected: boolean;
  ws_stats?: WsStats;
}

export function getHealth() {
  return api<HealthResponse>("/api/health");
}

// ── DTOs ────────────────────────────────────────────────────────────────────

/** Post-production node types. They run local ffmpeg through the single
 *  `postprod` request type — no Flow call, no credits — and exist as separate
 *  types so the graph says what a step does without opening it. */
export const POSTPROD_NODE_TYPES = [
  "analyze_video",
  "merge_video",
  "edit_video",
  "extract_last_frame",
  "add_bgm",
  "create_voice",
  "align_video_voice",
  "sync_image_voice",
  "remove_watermark",
  "review_video",
] as const;

export type PostprodNodeType = (typeof POSTPROD_NODE_TYPES)[number];

export type NodeType =
  | "character"
  | "image"
  | "video"
  | "prompt"
  | "note"
  | "visual_asset"
  | "Storyboard"
  // Motion transfer. A GENERATION type, not a post-production one — it
  // dispatches the same reference-to-video request the Component mode uses.
  // The clip it is named after stays on this machine.
  | "motion_control"
  | PostprodNodeType;
export type NodeStatus = "idle" | "queued" | "running" | "done" | "error";

export interface Board {
  id: number;
  name: string;
  created_at: string;
}

export interface NodeDTO {
  id: number;
  board_id: number;
  short_id: string;
  type: NodeType;
  x: number;
  y: number;
  w: number;
  h: number;
  data: Record<string, unknown>;
  status: NodeStatus;
  created_at: string;
}

export interface EdgeDTO {
  id: number;
  board_id: number;
  source_id: number;
  target_id: number;
  kind: string;
  // null when the upstream is single-variant (or the edge hasn't been
  // pinned yet — natural fallback to source.mediaId at dispatch time).
  // 0-based index into the source node's `data.mediaIds[]` when the
  // user has explicitly picked a variant.
  source_variant_idx: number | null;
  // Named sockets. The executor tells a start frame from a character
  // reference by these, so a wire that loses them changes what gets
  // dispatched — and what it costs.
  source_port: string | null;
  target_port: string | null;
}

export interface BoardDetail {
  board: Board;
  nodes: NodeDTO[];
  edges: EdgeDTO[];
}

// ── API methods ──────────────────────────────────────────────────────────────

export function listBoards(): Promise<Board[]> {
  return api<Board[]>("/api/boards");
}

export function createBoard(name: string): Promise<Board> {
  return api<Board>("/api/boards", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function getBoard(id: number): Promise<BoardDetail> {
  return api<BoardDetail>(`/api/boards/${id}`);
}

export function patchBoard(id: number, name: string): Promise<Board> {
  return api<Board>(`/api/boards/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function deleteBoard(id: number): Promise<{ deleted: number }> {
  return api<{ deleted: number }>(`/api/boards/${id}`, { method: "DELETE" });
}

export function createNode(input: {
  board_id: number;
  type: NodeType;
  x: number;
  y: number;
  data?: object;
}): Promise<NodeDTO> {
  return api<NodeDTO>("/api/nodes", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Shallow-merge patch for `node.data` — the backend (see
 * agent/flowboard/routes/nodes.py::update_node) merges this dict into
 * the existing JSON column instead of replacing it.
 *
 * Conventions:
 *   - Keys present in the patch override existing values.
 *   - Keys absent from the patch are PRESERVED (this is what the type
 *     guarantees over a wholesale replace).
 *   - A value of `null` is the explicit "delete this key" sentinel.
 *     Use it instead of `undefined` to clear fields like `aiBrief`
 *     after a regen — `undefined` gets dropped by JSON.stringify and
 *     would leave the stale value in place after the merge.
 *   - Merge depth is ONE LEVEL. Nested dict values are wholesale-
 *     replaced, not deep-merged. None of FlowboardNodeData's current
 *     fields nest, so this is a non-issue today; revisit if a future
 *     field stores objects.
 *
 * Pre-merge call sites that built the full `data` from scratch and
 * forgot a sibling field caused a real data-loss regression
 * (`aspectRatio` was wiped on every image gen by the auto-brief
 * patch). Sticking to deltas-only with this type as the contract
 * prevents that whole class of bug.
 */
export type DataPatch = Record<string, unknown>;

export function patchNode(
  id: number,
  patch: Partial<
    Pick<Omit<NodeDTO, "data">, "x" | "y" | "w" | "h" | "status">
  > & { data?: DataPatch },
): Promise<NodeDTO> {
  return api<NodeDTO>(`/api/nodes/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteNode(id: number): Promise<{ ok: true; deleted_edges: number[] }> {
  return api<{ ok: true; deleted_edges: number[] }>(`/api/nodes/${id}`, {
    method: "DELETE",
  });
}

export function createEdge(input: {
  board_id: number;
  source_id: number;
  target_id: number;
  kind?: string;
  source_variant_idx?: number | null;
  source_port?: string | null;
  target_port?: string | null;
}): Promise<EdgeDTO> {
  return api<EdgeDTO>("/api/edges", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Update an edge's variant pin without recreating it. Pass
 * `source_variant_idx: null` explicitly to clear the pin (revert to
 * the source's active mediaId at dispatch time). Omit the field to
 * leave it untouched.
 */
export function patchEdge(
  id: number,
  patch: { source_variant_idx?: number | null },
): Promise<EdgeDTO> {
  return api<EdgeDTO>(`/api/edges/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteEdge(id: number): Promise<{ ok: true }> {
  return api<{ ok: true }>(`/api/edges/${id}`, {
    method: "DELETE",
  });
}

// ── Chat ─────────────────────────────────────────────────────────────────────

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessageDTO {
  id: number;
  board_id: number;
  role: ChatRole;
  content: string;
  mentions: string[];
  created_at: string;
}

export interface PlanDTO {
  id: number;
  board_id: number;
  spec: {
    nodes: Array<{ tmp_id?: string; type: string; params?: Record<string, unknown> }>;
    edges: Array<{ from: string; to: string; kind?: string }>;
    layout_hint?: string;
  };
  status: "draft" | "approved" | "running" | "done" | "failed";
  created_at: string;
}

export interface ChatSendResponse {
  user: ChatMessageDTO;
  assistant: ChatMessageDTO;
  plan?: PlanDTO;
}

export function listChatMessages(boardId: number) {
  return api<ChatMessageDTO[]>(`/api/boards/${boardId}/chat`);
}

export function sendChatMessage(
  boardId: number,
  message: string,
  mentions: string[],
) {
  return api<ChatSendResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ board_id: boardId, message, mentions }),
  });
}

// ── Generation ───────────────────────────────────────────────────────────────

export interface BoardProject {
  flow_project_id: string;
  created: boolean;
}

export interface RequestDTO {
  id: number;
  node_id: number | null;
  type: string;
  params: Record<string, unknown>;
  // 'canceled' = user cancelled the request from the activity bell.
  // 'timeout' = backend's 5-minute video-gen budget elapsed; the row
  // self-transitions out of running. Both are terminal states.
  status: "queued" | "running" | "done" | "failed" | "canceled" | "timeout";
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export function ensureBoardProject(boardId: number) {
  return api<BoardProject>(`/api/boards/${boardId}/project`, { method: "POST" });
}

export function getBoardProject(boardId: number) {
  return api<BoardProject>(`/api/boards/${boardId}/project`).catch(() => null);
}

// ── Auth / profile ───────────────────────────────────────────────────────

export interface AuthMe {
  // Always null, and kept so nothing here has to branch on their absence.
  //
  // These were read from Google's userinfo endpoint using the Bearer token the
  // extension captured. Since the September 2026 Flow migration no such token
  // is issued: the page signs its own calls with a cookie and a per-page CSRF
  // token, neither of which identifies the account to this app.
  email: string | null;
  name: string | null;
  picture: string | null;
  verified_email: boolean | null;
  // The plan the user picked in Settings. A LABEL — it selects nothing and
  // gates nothing. The migrated payload has no tier field, so which lanes an
  // account really has is Google's answer at dispatch time.
  paygate_tier: "PAYGATE_TIER_ONE" | "PAYGATE_TIER_TWO" | null;
  // Both null on this transport: there is no credits RPC to read. Null rather
  // than 0 on purpose — "unknown balance" and "no credits left" send someone to
  // opposite places.
  sku: string | null;
  credits: number | null;
  // What the last Flow-tab probe found, or null if none has run.
  flow_tab: FlowTabProbe | null;
}

/** Presence, never values. Whether the page carries its signing token, not what
 *  the token is; whether an email-shaped key exists in the page's own global,
 *  not the address. */
export interface FlowTabProbe {
  flowTabPresent?: boolean;
  atTokenPresent?: boolean;
  emailPresent?: boolean;
  host?: string;
  sourcePath?: string;
  error?: string;
}

export function getAuthMe() {
  return api<AuthMe>("/api/auth/me").catch(() => null);
}

export interface AuthLogoutResult {
  ok: boolean;
  // Whether the agent could push a `logout` message to the extension
  // over its open WebSocket. False when no extension is connected —
  // agent-side caches were still cleared so the dashboard reflects
  // the logged-out state immediately.
  extension_notified: boolean;
}

export function logoutExtension() {
  return api<AuthLogoutResult>("/api/auth/logout", { method: "POST" });
}

export interface AuthScanResult {
  // True when the extension WebSocket is currently connected to the
  // agent. False means the user must install / enable / open Chrome.
  extension_connected: boolean;
  // Which build is loaded. A stale one answers nothing else correctly.
  extension_version: string | null;
  // The distinction the migration made necessary, and the one that looked
  // healthy for a week: the bridge can be connected, its queue idle and its
  // metrics clean, while the page cannot sign a single call.
  flow_tab_present: boolean;
  flow_tab_signed: boolean;
  // Whether a plan has been chosen in Settings. Not choosing one is fine —
  // nothing is gated on it.
  has_paygate_tier: boolean;
  // The probe's own code when it could not sign: NO_FLOW_TAB,
  // FLOW_TAB_DISCARDED, NO_AT_TOKEN, NO_INJECTION_RESULT.
  probe_error: string | null;
}

export function scanExtension() {
  return api<AuthScanResult>("/api/auth/scan", { method: "POST" });
}

export function createRequest(body: {
  type: string;
  node_id?: number;
  params: Record<string, unknown>;
}) {
  return api<RequestDTO>("/api/requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getRequest(id: number) {
  return api<RequestDTO>(`/api/requests/${id}`);
}

// ── Plans + Pipeline runs ────────────────────────────────────────────────────

export interface PipelineRunDTO {
  id: number;
  plan_id: number;
  status: "pending" | "running" | "done" | "failed";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export function getPlan(planId: number) {
  return api<PlanDTO>(`/api/plans/${planId}`);
}

/** The plan a board is run through, or null when it has none.
 *
 * Null is a normal answer: most boards are built by hand and run a node at a
 * time. Only an imported workflow gets a plan at creation. */
export function getBoardPlan(boardId: number): Promise<PlanDTO | null> {
  return api<PlanDTO | null>(`/api/boards/${boardId}/plan`);
}

/** The plan for running this board, created or refreshed to match it.
 *
 * Refreshed, not just fetched: an imported board's plan lists the nodes that
 * existed at import, so a node added afterwards would be skipped silently.
 * Creates rows; dispatches nothing. */
export function ensureBoardPlan(
  boardId: number,
  nodeIds?: number[]
): Promise<PlanDTO> {
  // A subset scopes the next run without narrowing the plan's node list: the
  // executor still loads the whole graph so wires resolve, and only dispatches
  // inside the subset. Omitted means the whole board, which every existing
  // caller relies on — and the backend rewrites the key either way, because a
  // leftover subset would make the next full run skip everything that had
  // already succeeded.
  return api<PlanDTO>(`/api/boards/${boardId}/plan`, {
    method: "POST",
    body: JSON.stringify({ node_ids: nodeIds ?? null }),
  });
}

export function runPlan(planId: number) {
  return api<PipelineRunDTO>(`/api/plans/${planId}/run`, { method: "POST" });
}

export function getPipelineRun(runId: number) {
  return api<PipelineRunDTO>(`/api/pipeline-runs/${runId}`);
}

// ── Media ────────────────────────────────────────────────────────────────────

export interface MediaStatus {
  available: boolean;
  has_url: boolean;
  mime?: string;
  reason?: string;
}

export function getMediaStatus(mediaId: string): Promise<MediaStatus> {
  const clean = mediaId.replace(/^media\//, "");
  return api<MediaStatus>(`/api/media/${encodeURIComponent(clean)}/status`);
}

export function mediaUrl(mediaId: string): string {
  const clean = mediaId.replace(/^media\//, "");
  return `/media/${encodeURIComponent(clean)}`;
}

// ── Upload ───────────────────────────────────────────────────────────────────

export interface UploadResponse {
  media_id: string;
  mime: string;
  size: number;
  // Detected by the agent from the image bytes; one of
  // IMAGE_ASPECT_RATIO_{SQUARE,PORTRAIT,LANDSCAPE}. Optional because legacy
  // responses (or formats we couldn't sniff) skip the field.
  aspect_ratio?: string;
  width?: number;
  height?: number;
}

export async function uploadImage(
  file: File,
  projectId: string,
  nodeId?: number,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("project_id", projectId);
  if (nodeId !== undefined) form.append("node_id", String(nodeId));
  form.append("file", file);

  // Don't set Content-Type — the browser sets it with the correct boundary.
  const res = await fetch("/api/upload", { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json() as Promise<UploadResponse>;
}

export interface VisionDescribeResponse {
  media_id: string;
  description: string;
}

export interface AutoPromptResponse {
  node_id: number;
  prompt: string;
}

export interface AutoPromptBatchResponse {
  node_id: number;
  prompts: string[];
}

export async function autoPromptBatch(
  nodeId: number,
  count: number,
  opts?: { camera?: string },
): Promise<AutoPromptBatchResponse> {
  const res = await fetch("/api/prompt/auto-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_id: nodeId, count, camera: opts?.camera }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json() as Promise<AutoPromptBatchResponse>;
}

export async function autoPrompt(
  nodeId: number,
  opts?: { camera?: string; style?: string },
): Promise<AutoPromptResponse> {
  const res = await fetch("/api/prompt/auto", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      node_id: nodeId,
      camera: opts?.camera,
      style: opts?.style,
    }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json() as Promise<AutoPromptResponse>;
}

export interface VideoStyle {
  name: string;
  description: string;
}

/** Idea → an ordered shot list, before anything is dispatched.
 *
 * Returned for the user to edit: every scene becomes one paid video call, so
 * the prompts are reviewed first rather than generated straight into a queue. */
export function ideaToPrompts(body: {
  idea: string;
  scene_count: number;
  seconds_per_scene?: number;
  style?: string | null;
  dialogue_language?: string | null;
  no_dialogue?: boolean;
  /** One of `listScriptGenres()`. Brings that genre's structure and
   *  per-scene dialogue length; unset uses the general craft notes. */
  genre?: string | null;
  /** The packaged README's other two skills: orchestrating a 10–30 minute
   *  piece, and the three-act eight-sequence structure. Both shipped in the
   *  skill tree with nothing able to switch them on. */
  use_longform?: boolean;
  use_3act?: boolean;
  /** One of `listVideoFormats()` — a playbook for one shape of video. */
  format?: string | null;
}): Promise<IdeaPrompts> {
  return sendJson<IdeaPrompts>("/api/prompt/idea", "POST", body);
}

/** One rule a scene's prompt breaks, found without spending a model call.
 *
 * Both of the rules that matter cost money to discover otherwise: a `@@tag`
 * inside spoken text is read aloud in the delivered audio, and a real person's
 * name in the visual half is refused by the generator's filter after the
 * request is sent. */
export interface PromptFinding {
  scene: number;
  rule: string;
  severity: string;
  message: string;
}

export interface IdeaPrompts {
  prompts: string[];
  findings?: PromptFinding[];
}

/** A playbook for one shape of video: trailer, micro-drama, found footage.
 *
 * Separate from a genre, which is HOW it is written. Loaded only when picked:
 * a found-footage horror example is the wrong thing to put in front of a
 * Buddhist parable, and it would cost that parable its craft notes. */
export interface VideoFormat {
  key: string;
  title: string;
}

export async function listVideoFormats(): Promise<VideoFormat[]> {
  const res = await fetch("/api/prompt/formats");
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return (await res.json()) as VideoFormat[];
}

/** One of the packaged tool's eight Vietnamese script formulas. */
export interface ScriptGenre {
  key: string;
  title: string;
  /** Words of dialogue per scene, `[low, high]`, or null where the packaged
   *  README does not say — four of the eight genres. Load-bearing rather than
   *  informational where it exists: a scene is 5–8 seconds, so this is what a
   *  voice can deliver inside one without the clip needing a re-cut. Which is
   *  why the missing four are null rather than a plausible default: the label
   *  below would have stated a number nobody wrote. */
  wordsPerScene: number[] | null;
}

export async function listScriptGenres(): Promise<ScriptGenre[]> {
  const res = await fetch("/api/prompt/genres");
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return res.json() as Promise<ScriptGenre[]>;
}

export async function listVideoStyles(): Promise<VideoStyle[]> {
  const res = await fetch("/api/prompt/styles");
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json() as Promise<VideoStyle[]>;
}

export async function describeMedia(mediaId: string): Promise<VisionDescribeResponse> {
  const res = await fetch("/api/vision/describe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json() as Promise<VisionDescribeResponse>;
}

export async function uploadImageFromUrl(
  url: string,
  projectId: string,
  nodeId?: number,
): Promise<UploadResponse> {
  const res = await fetch("/api/upload-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, project_id: projectId, node_id: nodeId }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json() as Promise<UploadResponse>;
}


// ── LLM provider Settings ─────────────────────────────────────────────────
// See .omc/plans/multi-llm-provider-legacy.md → UI Specification → Frontend ↔
// backend contract for the full shape.

export type LLMProviderName = "claude" | "gemini" | "openai";
export type LLMFeature = "auto_prompt" | "vision" | "planner";
export type LLMProviderMode = "cli" | "api" | "none";
/**
 * WHICH IDENTITY the provider acts as, as opposed to `mode`, which is the
 * transport it dispatches over. The two are independent, and conflating
 * them is how a Codex signed in with a rejected API key displayed as
 * "Connected · ChatGPT CLI · OAuth" — the tagline was a hardcoded string.
 */
export type LLMAuthMode = "oauth" | "apikey" | "none";
export type LLMLastError =
  | "not_installed"
  | "not_authenticated"
  | "no_key"
  | "unreachable"
  | "unknown";

export interface LLMProviderInfo {
  name: LLMProviderName;
  supportsVision: boolean;
  available: boolean;
  configured: boolean;
  requiresKey: boolean;
  mode: LLMProviderMode;
  authMode: LLMAuthMode;
  lastError?: LLMLastError;
  lastTest?: { ok: boolean; latencyMs?: number; error?: string };
}

export interface LLMConfig {
  // null when the user hasn't picked a provider for this feature yet.
  // Backend no longer fabricates a default; the forced-setup gate uses
  // `configured` (below) to keep the dialog open until the user chooses.
  auto_prompt: LLMProviderName | null;
  vision: LLMProviderName | null;
  planner: LLMProviderName | null;
  // True only when all 3 features are pinned at the same provider —
  // the single-provider UI invariant. Drives the forced-setup dialog.
  configured: boolean;
}

export async function getLlmProviders(): Promise<LLMProviderInfo[]> {
  // Backend returns snake-case keys mapped from Python — but the route
  // already emits camelCase for the public surface. Re-typed here so
  // the spread/destructure pattern in the UI components stays clean.
  const res = await fetch("/api/llm/providers");
  if (!res.ok) throw new Error(`getLlmProviders: ${res.status}`);
  return res.json() as Promise<LLMProviderInfo[]>;
}

/**
 * Drop every cached probe and return fresh provider state.
 *
 * For the moment right after `codex login`: the backend caches auth for
 * 60s, and a user who has just signed in and still sees "API key" reads
 * that as the sign-in having failed.
 */
export async function recheckLlmProviders(): Promise<LLMProviderInfo[]> {
  const res = await fetch("/api/llm/recheck", { method: "POST" });
  if (!res.ok) throw new Error(`recheckLlmProviders: ${res.status}`);
  return res.json() as Promise<LLMProviderInfo[]>;
}

/** One provider's live state, plus what it can actually be asked to do. */
export interface LLMProviderHealth {
  name: LLMProviderName;
  available: boolean;
  authMode: string;
  mode: string;
  capabilities: { text: boolean; vision: boolean; audio: boolean };
}

/** A feature the user pinned, and whether that pin can serve today. */
export interface LLMFeatureHealth {
  feature: string;
  provider: string | null;
  ok: boolean;
  reason: string | null;
}

/** A step nobody pins, and which provider in its chain would answer. */
export interface LLMInternalHealth {
  feature: string;
  chain: string[];
  servedBy: string | null;
  ok: boolean;
}

export interface LLMHealth {
  providers: LLMProviderHealth[];
  features: LLMFeatureHealth[];
  internal: LLMInternalHealth[];
  audio: { providers: string[]; ok: boolean };
  /** Image generation is not a registry feature — it makes pixels, not
   *  text — but it is reported here because both of its paths ship off and
   *  this is the page someone checks before wondering why a node failed. */
  images: {
    sources: string[];
    ok: boolean;
    model: string;
    quality: string;
  };
}

/**
 * Which model serves each step right now, and what is broken.
 *
 * Dispatch deliberately does not substitute providers: a pinned provider
 * that cannot serve fails loudly, so you always know which model made your
 * work. This is the other half of that bargain — somewhere to see a
 * provider has died, rather than finding out when a board run fails.
 */
export async function getLlmHealth(): Promise<LLMHealth> {
  const res = await fetch("/api/llm/health");
  if (!res.ok) throw new Error(`getLlmHealth: ${res.status}`);
  return res.json() as Promise<LLMHealth>;
}

export async function getLlmConfig(): Promise<LLMConfig> {
  const res = await fetch("/api/llm/config");
  if (!res.ok) throw new Error(`getLlmConfig: ${res.status}`);
  return res.json() as Promise<LLMConfig>;
}

export async function setLlmConfig(
  partial: Partial<LLMConfig>,
): Promise<{ ok: boolean }> {
  const res = await fetch("/api/llm/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(partial),
  });
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return res.json();
}

export async function setLlmApiKey(
  name: LLMProviderName,
  apiKey: string | null,
): Promise<{ ok: boolean }> {
  // null clears the key. Backend chmods secrets.json to 0o600 after
  // every write; the key is never echoed back via getLlmProviders.
  const res = await fetch(`/api/llm/providers/${name}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey }),
  });
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return res.json();
}

export interface LlmTestResult {
  ok: boolean;
  latencyMs?: number;
  error?: string;
}

export async function testLlmProvider(
  name: LLMProviderName,
): Promise<LlmTestResult> {
  // Cost-bounded by the backend: a 1-token ping on a 120s budget (180s for
  // Gemini, which retries on quota exhaustion). The comment used to say 15s,
  // which is not a deadline anything enforces — a CLI provider measured
  // between 18 and 38 seconds, so a caller sizing its UI from 15s would show
  // a spinner that looks stuck. Returns
  // ok:false (NOT a non-200 HTTP status) on any failure mode so the
  // UI can render the error inline without try/catch boilerplate.
  const res = await fetch(`/api/llm/providers/${name}/test`, { method: "POST" });
  if (!res.ok) {
    return { ok: false, error: `HTTP ${res.status}` };
  }
  return res.json();
}


// ── Activity feed ─────────────────────────────────────────────────────────
// Read-only surface over the Request table. Captures every backend op:
// gen_image / gen_video / edit_image (worker), auto_prompt /
// auto_prompt_batch / vision / planner (LLM layer via record_activity).

export type ActivityType =
  | "auto_prompt" | "auto_prompt_batch"
  | "vision" | "planner"
  | "gen_image" | "gen_video" | "edit_image"
  | "upload" | "upload_url";
export type ActivityStatus = "queued" | "running" | "done" | "failed";

export interface ActivityListItem {
  id: number;
  type: ActivityType | string; // string fallback for forward-compat
  status: ActivityStatus | string;
  node_id: number | null;
  node_short_id: string | null;
  created_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface ActivityDetail extends ActivityListItem {
  params: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
}

export async function getActivityList(opts?: {
  limit?: number;
  beforeId?: number;
  type?: string[];
}): Promise<{ items: ActivityListItem[]; next_before_id: number | null }> {
  const search = new URLSearchParams();
  if (opts?.limit) search.set("limit", String(opts.limit));
  if (opts?.beforeId) search.set("before_id", String(opts.beforeId));
  if (opts?.type && opts.type.length > 0) search.set("type", opts.type.join(","));
  const q = search.toString();
  const res = await fetch(`/api/activity${q ? `?${q}` : ""}`);
  if (!res.ok) throw new Error(`getActivityList: ${res.status}`);
  return res.json();
}

export async function getActivityDetail(id: number): Promise<ActivityDetail> {
  const res = await fetch(`/api/activity/${id}`);
  if (!res.ok) throw new Error(`getActivityDetail: ${res.status}`);
  return res.json();
}

// Cancel a queued or running request. The activity row id IS the
// underlying Request.id, so the same numeric handle works against
// /api/requests. Backend returns 409 when the row has already settled
// (done/failed/timeout/canceled).
export async function cancelActivity(id: number): Promise<void> {
  const res = await fetch(`/api/requests/${id}/cancel`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`cancelActivity: ${res.status} ${detail}`);
  }
}


// ── References ───────────────────────────────────────────────────────────
// User-curated cross-board library of saved media. Backend mirror:
// agent/flowboard/routes/references.py + db.models.Reference.
// JSON wire format is snake_case (mirrors SQLModel column names);
// camelCase is reserved for the TS surface, so each helper maps the
// rows on the way back.

export interface ReferenceItem {
  id: number;
  mediaId: string;
  // Best-effort signed CDN URL captured at save time. May expire — the
  // canonical bytes live in storage/media/{mediaId}.{ext}; this field
  // exists purely as a re-ingest hint when the file goes missing.
  url: string | null;
  label: string;
  kind: "image" | "character" | "visual_asset" | "storyboard_shot";
  // Snapshot of the source node's aiBrief at save time; lets cross-board
  // spawn skip the re-vision call entirely.
  aiBrief: string | null;
  aspectRatio: string | null;
  tags: string[];
  pinned: boolean;
  position: number;
  sourceBoardId: number | null;
  sourceNodeShortId: string | null;
  createdAt: string;
}

// Wire-shape POST body — snake_case to match the FastAPI schema 1:1.
export interface ReferenceCreateInput {
  media_id: string;
  kind: ReferenceItem["kind"];
  label?: string;
  ai_brief?: string | null;
  aspect_ratio?: string | null;
  url?: string | null;
  source_board_id?: number | null;
  source_node_short_id?: string | null;
  tags?: string[];
}

// Wire-shape PATCH body. Same snake_case convention.
export interface ReferencePatchInput {
  label?: string;
  pinned?: boolean;
  position?: number;
  tags?: string[];
}

interface ReferenceRowWire {
  id: number;
  media_id: string;
  url: string | null;
  label: string;
  kind: string;
  ai_brief: string | null;
  aspect_ratio: string | null;
  tags: string[] | null;
  pinned: boolean;
  position: number;
  source_board_id: number | null;
  source_node_short_id: string | null;
  created_at: string;
}

function mapReferenceRow(row: ReferenceRowWire): ReferenceItem {
  // Coerce the kind string into the typed union — the backend already
  // validates against _ALLOWED_KINDS so any unknown value here would
  // mean a backend bug. Fall back to "image" defensively rather than
  // throwing, so a single bad row doesn't break the whole list render.
  const allowed: ReferenceItem["kind"][] = [
    "image",
    "character",
    "visual_asset",
    "storyboard_shot",
  ];
  const kind: ReferenceItem["kind"] = (allowed as string[]).includes(row.kind)
    ? (row.kind as ReferenceItem["kind"])
    : "image";
  return {
    id: row.id,
    mediaId: row.media_id,
    url: row.url,
    label: row.label,
    kind,
    aiBrief: row.ai_brief,
    aspectRatio: row.aspect_ratio,
    tags: Array.isArray(row.tags) ? row.tags : [],
    pinned: row.pinned,
    position: row.position,
    sourceBoardId: row.source_board_id,
    sourceNodeShortId: row.source_node_short_id,
    createdAt: row.created_at,
  };
}

export async function listReferences(params?: {
  q?: string;
  pinned_first?: boolean;
  limit?: number;
}): Promise<ReferenceItem[]> {
  const search = new URLSearchParams();
  if (params?.q) search.set("q", params.q);
  if (params?.pinned_first !== undefined) {
    search.set("pinned_first", String(params.pinned_first));
  }
  if (params?.limit !== undefined) search.set("limit", String(params.limit));
  const qs = search.toString();
  const rows = await api<ReferenceRowWire[]>(
    `/api/references${qs ? `?${qs}` : ""}`,
  );
  return rows.map(mapReferenceRow);
}

export async function createReference(
  input: ReferenceCreateInput,
): Promise<ReferenceItem> {
  const row = await api<ReferenceRowWire>("/api/references", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return mapReferenceRow(row);
}

export async function patchReference(
  id: number,
  patch: ReferencePatchInput,
): Promise<ReferenceItem> {
  const row = await api<ReferenceRowWire>(`/api/references/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return mapReferenceRow(row);
}

export async function deleteReference(id: number): Promise<void> {
  // Backend returns 204 No Content; api<T>() would choke on the empty
  // body, so we use fetch() directly and skip the JSON parse.
  const res = await fetch(`/api/references/${id}`, { method: "DELETE" });
  if (!res.ok) {
    throw new Error(`deleteReference: ${res.status} ${res.statusText}`);
  }
}

// ── Flow project sync (local → Flow, one direction) ───────────────────────

export interface BoardFlowStatus {
  board_id: number;
  board_name: string;
  flow_project_id: string | null;
  exists_on_flow: boolean;
}

export interface SyncStatusResponse {
  board_status: BoardFlowStatus[];
}

export interface SyncUpAction {
  board_id: number;
  board_name: string;
  old_flow_project_id: string | null;
  new_flow_project_id: string | null;
  status: "created" | "rebound" | "failed";
  error: string | null;
}

export interface SyncUpResponse {
  synced: SyncUpAction[];
  failed: SyncUpAction[];
  total_boards: number;
}

export function getFlowSyncStatus(): Promise<SyncStatusResponse> {
  return api<SyncStatusResponse>("/api/flow/projects");
}

export function syncBoardsUpToFlow(): Promise<SyncUpResponse> {
  return api<SyncUpResponse>("/api/flow/projects/sync-up", { method: "POST" });
}


// ── App settings ─────────────────────────────────────────────────────────
// Whitelisted key/value store: config.json defaults under user overrides.
// Credential keys (account/token/cookie) are not in the backend whitelist,
// so they never appear here and are refused on write.

/** Send JSON and surface FastAPI's `detail` on failure.
 *
 * The generic `api()` throws "400 Bad Request", which for these endpoints
 * throws away the only part worth showing: the validator explains exactly
 * what was wrong ("model 'realesrgan-x4plus' upscales by 4x only; scale=2
 * would return silently corrupted pixels"). */
async function sendJson<T>(
  path: string,
  method: string,
  body: unknown,
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const data = (await res.json()) as { detail?: unknown };
      detail =
        typeof data?.detail === "string"
          ? data.detail
          : data?.detail
            ? JSON.stringify(data.detail)
            : "";
    } catch {
      detail = "";
    }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export type SettingsMap = Record<string, unknown>;

export function getSettings(): Promise<SettingsMap> {
  return api<SettingsMap>("/api/settings");
}

export function getSettingsDefaults(): Promise<SettingsMap> {
  return api<SettingsMap>("/api/settings/defaults");
}

/** Writes are all-or-nothing: one bad value rejects the whole batch, so the
 * server's `detail` names the offending key and must reach the user. */
export function putSettings(values: SettingsMap): Promise<{ updated: string[] }> {
  return sendJson<{ updated: string[] }>("/api/settings", "PUT", { values });
}

export function resetSettings(keys?: string[]): Promise<{ removed: number }> {
  return sendJson<{ removed: number }>("/api/settings/reset", "POST", {
    keys: keys ?? null,
  });
}


// ── Post-production (local ffmpeg / RealESRGAN / Gemini TTS) ─────────────
// None of this touches Google Flow, so it keeps working with no session and
// never spends credits. Every call is synchronous: the request blocks for
// the whole render and returns the output path — there is no job id to poll.

export interface PostprodStatus {
  assets: Record<string, unknown>;
  ffmpegAvailable: boolean;
  upscaleAvailable: boolean;
  upscaleModels: string[];
  ttsKeyAvailable: boolean;
  voices: { name?: string; title?: string; prompt?: string }[];
  fonts: string[];
  bgm: string[];
}

export function getPostprodStatus(): Promise<PostprodStatus> {
  return api<PostprodStatus>("/api/postprod/status");
}

export interface LibraryItem {
  name: string;
  path: string;
  kind: "video" | "image" | "audio";
  sizeBytes: number;
  modified: number;
  /** Cached Flow media — preview via `mediaUrl(mediaId)`. */
  mediaId: string | null;
  /** A finished render — preview/download straight from this URL. */
  url: string | null;
}

/** Files that may be fed into post-production, plus what it has produced.
 *
 * The endpoints take filesystem paths confined to allowed roots, so the UI
 * never invents a path — it picks one of these. Renders are valid inputs
 * too, which is what makes concat → subtitles → logo chains possible. */
export function getPostprodLibrary(): Promise<{
  sources: LibraryItem[];
  renders: LibraryItem[];
}> {
  return api<{ sources: LibraryItem[]; renders: LibraryItem[] }>(
    "/api/postprod/library",
  );
}

export interface RenderResult {
  path: string;
  durationSeconds: number | null;
}

function postprod<T>(path: string, body: unknown): Promise<T> {
  return sendJson<T>(`/api/postprod/${path}`, "POST", body);
}

export function concatClips(body: {
  clips: string[];
  output: string;
  width?: number;
  height?: number;
  fps?: number;
}): Promise<RenderResult> {
  return postprod<RenderResult>("concat", body);
}

/** Save pasted subtitle text as an .srt the backend will accept.
 *
 * `burnSubtitles` takes a path confined to the server's allowed roots, which
 * a browser cannot write to — this is how the text gets there. */
export function saveSrt(body: {
  name: string;
  text: string;
}): Promise<{ path: string }> {
  return postprod<{ path: string }>("srt", body);
}

// ── Model registry ───────────────────────────────────────────────────────
// What THIS account can actually generate with, filtered by its plan tier.
// Replaces ~20 option lists that were typed out by hand across the tabs and
// had drifted apart from each other and from the backend.

export interface ModelOption {
  value: string;
  label: string;
  /** Set when picking this runs something else — e.g. a "Lower Priority"
   *  lane that this plan does not have and which is therefore billed. */
  note: string | null;
}

export interface DurationOption {
  value: number;
  label: string;
  credits: number | null;
  note: string | null;
}

export interface LaneInfo {
  qualities: ModelOption[];
  aspects: ModelOption[];
  durations: DurationOption[];
}

/** The Settings screen's value space — labels, not dispatch tokens, because
 *  that is what settings_store validates. */
export interface SettingsOptions {
  veoModel: ModelOption[];
  imageModel: ModelOption[];
  aspect: ModelOption[];
  duration: DurationOption[];
  /** OMNI's render resolution. New with the batch transport: the builder takes
   *  it per request, and 360p had no way to be chosen before. */
  resolution: ModelOption[];
}

export interface ModelsResponse {
  tier: string | null;
  tierLabel: string;
  settingsOptions: SettingsOptions;
  imageModels: ModelOption[];
  imageAspects: ModelOption[];
  t2v: LaneInfo;
  i2v: LaneInfo;
  startEnd: LaneInfo;
  omni: LaneInfo;
}

export function getModels(): Promise<ModelsResponse> {
  return api<ModelsResponse>("/api/models");
}

// ── Sample workflows ─────────────────────────────────────────────────────
// The packaged tool's 9 templates, served as recipes rather than importable
// boards: their node graph uses 17 types where this canvas has 7, and most of
// the rest are things done in tabs here. The prompts are the valuable part.

export interface TemplateSummary {
  file: string;
  name: string;
  description: string;
  stepCount: number;
  /** "packaged" (the exe's nine samples) · "tool" (its own Workflows folder)
   *  · "mine" (saved from this canvas). */
  source: "packaged" | "tool" | "mine";
  /** Only personal templates may be renamed, edited or deleted. Showing
   *  those actions on the other two would offer to remove a file this app
   *  does not own, with no undo. */
  writable: boolean;
}

export interface TemplateStep {
  order: number;
  type: string;
  label: string;
  doneWith: string;
  prompt: string | null;
}

export interface TemplateDetail extends TemplateSummary {
  steps: TemplateStep[];
  unmappedTypes: string[];
}

export function listTemplates(): Promise<TemplateSummary[]> {
  return api<TemplateSummary[]>("/api/templates");
}

// ── personal templates: the writable third of that list ─────────────────
//
// `writable` is not decoration. The packaged samples and the exe's own
// Workflows folder come through the same list, and offering rename/delete on
// one of those would remove a file this app does not own — with no undo.

export function saveBoardAsTemplate(
  boardId: number,
  name: string,
  description = "",
): Promise<TemplateSummary> {
  return api<TemplateSummary>("/api/templates", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ board_id: boardId, name, description }),
  });
}

export function renameTemplate(
  file: string,
  patch: { name?: string; description?: string },
): Promise<{ file: string; name: string; description: string }> {
  return api(`/api/templates/mine/${encodeURIComponent(file)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
}

/** Overwrite a personal template with a board's current state — "edit". */
export function replaceTemplate(file: string, boardId: number): Promise<TemplateSummary> {
  return api<TemplateSummary>(`/api/templates/mine/${encodeURIComponent(file)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ board_id: boardId }),
  });
}

export function deleteTemplate(file: string): Promise<{ ok: boolean }> {
  return api(`/api/templates/mine/${encodeURIComponent(file)}`, { method: "DELETE" });
}

/** The raw document, for the browser to write to a file. */
export function exportTemplate(file: string): Promise<Record<string, unknown>> {
  return api(`/api/templates/${encodeURIComponent(file)}/export`);
}

/** Save an uploaded JSON file as a personal template. Accepts this app's own
 *  export or a workflow file from the packaged tool. */
export function importTemplateJson(
  document: Record<string, unknown>,
  name = "",
): Promise<TemplateSummary> {
  return api<TemplateSummary>("/api/templates/import-json", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ document, name }),
  });
}

// ── running: retry only what broke, and stop everything ─────────────────

export function rerunFailed(boardId: number): Promise<{ id: number }> {
  return api(`/api/boards/${boardId}/rerun-failed`, { method: "POST" });
}

export function stopBoard(
  boardId: number,
): Promise<{
  runsStopped: number;
  requestsCancelled: number;
  /** Nodes taken out of `running`/`queued` and stamped `stopped_by_user`, so
   *  RUN Lỗi can find them. Without this they stayed "running" forever and
   *  RUN Lỗi answered 400: no failed node to re-run. */
  nodesReleased?: number;
}> {
  return api(`/api/boards/${boardId}/stop`, { method: "POST" });
}

export interface QueueStatus {
  paused: boolean;
  queued: number;
  running: number;
}

export function getQueueStatus(): Promise<QueueStatus> {
  return api<QueueStatus>("/api/requests/queue");
}

export function setQueuePaused(paused: boolean): Promise<{ paused: boolean }> {
  return api(`/api/requests/queue/${paused ? "pause" : "resume"}`, { method: "POST" });
}

export function clearQueue(): Promise<{ cancelled: number }> {
  return api("/api/requests/queue/clear", { method: "POST" });
}

/** Open the folder finished media is written to, in the OS file manager.
 *  The path comes from settings on the server — never from here. */
export function openOutputFolder(): Promise<{ path: string }> {
  return api("/api/media/open-output-folder", { method: "POST" });
}

export function readTemplate(file: string): Promise<TemplateDetail> {
  return api<TemplateDetail>(`/api/templates/${encodeURIComponent(file)}`);
}

/** One node's share of what a board run would dispatch. */
export interface EstimateLineItem {
  nodeId: number;
  shortId: string;
  type: string;
  title: string;
  jobs: number;
  /** null means the price is UNKNOWN, not zero. Render the difference. */
  credits: number | null;
  note: string | null;
}

export interface BoardEstimate {
  boardId: number;
  billableJobs: number;
  /** Transcription calls. Not a Flow credit and not free either. Rendered
   *  apart from both for that reason — and the SOURCE decides which of the
   *  two it is, so `transcribeSource` has to be read alongside this. */
  transcribeJobs: number;
  /** Which speech-to-text source would answer today: "gemini" (the user's
   *  own quota), "whisper-1" (real dollars per minute), "local" (free), or
   *  "" when none is configured and the step would fail. The count alone
   *  cannot say which, and the three cost wildly different things. */
  transcribeSource: string;
  /** Clip-review calls. Same footing as transcription: the user's own AI
   *  quota, never Flow credits. */
  /** AI calls that are neither a review nor a transcription: a prompt node
   *  asking a model at run time, an `analyze_video` node reading a clip. The
   *  user's own provider quota, not Flow credits. */
  llmJobs?: number;
  reviewJobs: number;
  /** Most AI reviews the board could make. The loop stops as soon as a clip
   *  clears its threshold, so `reviewJobs` is the floor and this is the
   *  ceiling; showing only the floor under-states a board that may spend four
   *  vision calls a node. */
  reviewJobsMax: number;
  /** Images drawn by OpenAI rather than Flow. Deliberately NOT part of
   *  `billableJobs`: that number means Flow credits, and these are dollars
   *  on a different account. */
  openaiImageJobs: number;
  /** A ready-made sentence about what those images cost, including whether
   *  the figure is OpenAI's published price or a third-party estimate. */
  openaiImageNote: string;
  /** Narration sent to OpenAI, counted in CHARACTERS because
   *  `/v1/audio/speech` bills that way — a job count would put a caption and
   *  a ten-minute story in the same row. */
  openaiTtsChars: number;
  openaiTtsNodes: number;
  /** How many of those nodes get their script from a wire, so their length is
   *  not measurable until the run. Reporting them as 0 characters would read
   *  as free. */
  openaiTtsUnknownNodes: number;
  openaiTtsNote: string;
  /** Generation nodes that will be skipped because they have no prompt.
   *  Several packaged templates ship with the prompt boxes blank. */
  notReadyJobs: number;
  localJobs: number;
  knownCredits: number;
  unpricedJobs: number;
  creditsAvailable: number | null;
  /** Seconds since Flow reported that balance. A cached number shown as current
   *  is the same failure as a guessed price, so the age travels with it. */
  creditsAgeS?: number | null;
  items: EstimateLineItem[];
}

/** What running this board would dispatch. Reads only — costs nothing. */
export function getBoardEstimate(
  boardId: number,
  nodeIds?: number[]
): Promise<BoardEstimate> {
  // A scoped quote for a scoped run. Quoting the whole board for a three-node
  // run makes someone cancel a run they could afford — the expensive mistake in
  // this direction.
  if (nodeIds && nodeIds.length > 0) {
    const qs = nodeIds.map((id) => `node_ids=${id}`).join("&");
    return api<BoardEstimate>(`/api/boards/${boardId}/estimate?${qs}`);
  }
  return api<BoardEstimate>(`/api/boards/${boardId}/estimate`);
}

/** What an import produced, next to what the file held.
 *
 * Both halves are returned on purpose: an import that lost a third of the
 * graph would otherwise look exactly like one that worked. */
export interface TemplateImportResult {
  boardId: number;
  planId: number;
  name: string;
  nodes: number;
  nodesInFile: number;
  edges: number;
  edgesInFile: number;
  danglingEdges: number;
  unsupported: string[];
}

/** Build a board from a packaged workflow file. Free and offline — this
 *  writes rows, it does not dispatch anything. */
export function importTemplate(file: string): Promise<TemplateImportResult> {
  return api<TemplateImportResult>(
    `/api/templates/${encodeURIComponent(file)}/import`,
    { method: "POST" },
  );
}

// ── Excel batch ──────────────────────────────────────────────────────────

export interface BatchRow {
  prompt: string;
  aspect: string | null;
  model: string | null;
  duration: number | null;
  image: string | null;
}

/** Read a spreadsheet of prompts/settings into dispatchable rows. */
export async function parseBatchSheet(
  file: File,
): Promise<{ rows: BatchRow[]; skipped: number; columns: string[] }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/batch/parse", { method: "POST", body: form });
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return res.json();
}

/** Where to point a download link for the results workbook. */
export function batchExportUrl(limit = 200, types?: string[]): string {
  const q = new URLSearchParams({ limit: String(limit) });
  if (types?.length) q.set("type", types.join(","));
  return `/api/batch/export?${q}`;
}

/** Burn a title line across the top of the video. */
export function overlayTitle(body: {
  video: string;
  output: string;
  text: string;
  size?: number;
  color?: string;
  boxOpacity?: number;
  yRatio?: number;
}): Promise<RenderResult> {
  return postprod<RenderResult>("title", body);
}

/** Pull one frame out as a cover image. */
export function grabThumbnail(body: {
  video: string;
  output: string;
  atSeconds?: number;
  width?: number;
}): Promise<{ path: string; url: string }> {
  return postprod<{ path: string; url: string }>("thumbnail", body);
}

/** Cut one video into fixed-length pieces — the counterpart to concat. */
export function cutVideo(body: {
  video: string;
  seconds: number;
  name?: string;
}): Promise<{ pieces: LibraryItem[] }> {
  return postprod<{ pieces: LibraryItem[] }>("cut", body);
}

export function burnSubtitles(body: {
  video: string;
  srt: string;
  output: string;
  font?: string;
  size?: number;
  primaryColor?: string;
  outlineColor?: string;
  outlineWidth?: number;
  marginV?: number;
}): Promise<RenderResult> {
  return postprod<RenderResult>("subtitles", body);
}

export function overlayLogo(body: {
  video: string;
  logo: string;
  output: string;
  x?: number;
  y?: number;
  width?: number | null;
  height?: number | null;
}): Promise<RenderResult> {
  return postprod<RenderResult>("logo", body);
}

export function mixBgm(body: {
  video: string;
  output: string;
  track: string;
  bgmVolume?: number;
  origVolume?: number;
  fadeIn?: number;
  fadeOut?: number;
}): Promise<RenderResult> {
  return postprod<RenderResult>("bgm", body);
}

export interface NarrateResult {
  path: string;
  voice: string;
  durationSeconds: number | null;
}

/** What re-reading this node's failed narration segments would do. Reads only. */
export function narrationSegments(boardId: number, nodeId: number) {
  return api<import("../lib/narrationReread").RereadPreview>(
    `/api/boards/${boardId}/narration/${nodeId}`,
  );
}

/** Read the failed segments again, reusing the audio already paid for.
 *
 * Sends a node id and nothing else. The script, voice and engine are re-derived
 * server-side from the board — these endpoints are unauthenticated by design, so
 * a body carrying text and a voice would be a paid text-to-speech call anyone on
 * this machine could make. */
export function rereadNarration(boardId: number, nodeId: number) {
  return sendJson<{
    requestId: number;
    mediaId: string | null;
    reread: number[];
    kept: number;
  }>(`/api/boards/${boardId}/narration`, "POST", { node_id: nodeId });
}

export function narrate(body: {
  text: string;
  voice?: string;
  persona?: string | null;
  output?: string | null;
}): Promise<NarrateResult> {
  return postprod<NarrateResult>("narrate", body);
}

export function fitNarration(body: {
  video: string;
  audio: string;
  output: string;
}): Promise<RenderResult> {
  return postprod<RenderResult>("fit-narration", body);
}

// ── Grok (xAI) ───────────────────────────────────────────────────────────
// Runs against the user's own signed-in grok.com session through the
// extension's page bridge — no API key, the way the packaged tool does it.

export function getGrokStatus(): Promise<{
  bridgeReady: boolean;
  note: string;
}> {
  return api<{ bridgeReady: boolean; note: string }>("/api/grok/status");
}

/** One harmless call through the bridge, reporting the reply's shape.
 *
 * The response layout was the one thing static analysis of the packaged tool
 * could not recover, so this replaces a guess with a real answer. */
export function probeGrok(): Promise<{
  ok: boolean;
  status: number | null;
  objectCount: number;
  keys: string[];
}> {
  return sendJson<{
    ok: boolean;
    status: number | null;
    objectCount: number;
    keys: string[];
  }>("/api/grok/probe", "POST", {});
}

export function generateGrok(body: {
  prompt: string;
  video?: boolean;
  ref_media_ids?: string[];
}): Promise<{ urls: string[]; objectCount: number }> {
  return sendJson<{ urls: string[]; objectCount: number }>(
    "/api/grok/generate",
    "POST",
    body,
  );
}

export interface ProductInfo {
  title: string | null;
  imageUrl: string | null;
  description: string | null;
  siteName: string | null;
  price: string | null;
}

/** Read a product listing's Open Graph tags — no marketplace credentials. */
export function readProduct(body: { url: string }): Promise<ProductInfo> {
  return sendJson<ProductInfo>("/api/affiliate/product", "POST", body);
}

export interface CloneResult {
  path: string;
  name: string;
  url: string;
  sizeBytes: number;
  durationSeconds: number | null;
}

/** Fetch a video from a link into the renders folder, ready to edit. */
export function cloneVideo(body: {
  url: string;
  max_height?: number;
}): Promise<CloneResult> {
  return postprod<CloneResult>("clone", body);
}

/** What's at that link, without downloading it. */
export function cloneVideoPreview(body: { url: string }): Promise<{
  title: string | null;
  uploader: string | null;
  durationSeconds: number | null;
  thumbnail: string | null;
}> {
  return postprod("clone/preview", body);
}

/** Blur out a static watermark with ffmpeg's delogo filter. */
export function delogo(body: {
  video: string;
  output: string;
  x: number;
  y: number;
  width: number;
  height: number;
}): Promise<RenderResult> {
  return postprod<RenderResult>("delogo", body);
}

/** Speech in a clip → a burnable .srt. Needs a Gemini key. */
export function transcribe(body: {
  video: string;
  name: string;
  language?: string | null;
}): Promise<{ path: string; srt: string }> {
  return postprod<{ path: string; srt: string }>("transcribe", body);
}

export interface EnsembleDraft {
  provider: string;
  prompt: string;
  error: string;
}

export interface EnsembleResult {
  node_id: number;
  prompt: string;
  /** Which provider judged. Empty when only one model answered. */
  judge: string;
  why: string;
  chosen: number | null;
  drafts: EnsembleDraft[];
}

/**
 * Every available model drafts the prompt; one of them picks the winner.
 *
 * Slower than `autoPrompt` and almost free: Claude and Codex are signed in
 * through OAuth, so their marginal cost is a subscription already paid for.
 * The drafts come back too — a prompt whose alternatives you cannot see is
 * one you cannot argue with.
 */
export function autoPromptEnsemble(body: {
  node_id: number;
  camera?: string | null;
  style?: string | null;
}): Promise<EnsembleResult> {
  return sendJson<EnsembleResult>("/api/prompt/auto/ensemble", "POST", body);
}

/** A character of a board's Flow project.
 *
 * The registry behind the `@@Name` a prompt tags: the name, the voice, and the
 * `entityId` Flow issues. Two ways in — `createCharacterOnFlow` makes the id,
 * `saveCharacter` records one the user already made in Flow's own UI. Without an
 * entity id the record still works, over the reference-image path. */
export interface FlowCharacter {
  id: string;
  name: string;
  projectId: string;
  entityId: string;
  voice: string;
  voiceStyle: string;
  info: string;
  mediaId: string;
  /** False when the entity id belongs to another project — the run refuses it,
   *  so the card says so first. */
  usable: boolean;
}

export async function listCharacters(boardId: number): Promise<FlowCharacter[]> {
  const res = await fetch(`/api/boards/${boardId}/characters`);
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return (await res.json()) as FlowCharacter[];
}

export function saveCharacter(
  boardId: number,
  body: {
    name: string;
    entityId?: string;
    voice?: string;
    voiceStyle?: string;
    info?: string;
    mediaId?: string;
    id?: string;
  },
): Promise<FlowCharacter> {
  return sendJson<FlowCharacter>(`/api/boards/${boardId}/characters`, "POST", body);
}

/** Create the Character in Flow, then register it with the id Flow gave back.
 *
 * This is the path that removes a manual step: until the create RPC was
 * recovered, a character could only be registered by opening Flow, making it by
 * hand, and pasting its id here. Zero credit — it writes a record on Flow's
 * side, it generates nothing. Flow refusing answers 502, and the caller falls
 * back to the paste path rather than losing the name the user just typed. */
export function createCharacterOnFlow(
  boardId: number,
  body: {
    name: string;
    voice?: string;
    voiceStyle?: string;
    info?: string;
    mediaId?: string;
  },
): Promise<FlowCharacter> {
  return sendJson<FlowCharacter>(
    `/api/boards/${boardId}/characters/create-on-flow`,
    "POST",
    body,
  );
}

/** One of the seven board shapes the packaged skill recognises. */
export interface Archetype {
  key: string;
  label: string;
  /** What the finished chain looks like, in words. */
  chain: string;
}

export async function listArchetypes(): Promise<Archetype[]> {
  const res = await fetch("/api/prompt/archetypes");
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return (await res.json()) as Archetype[];
}

export interface ArchetypeChoice {
  /** `explicit` · `inferred` · `ask`. On `ask` the caller must NOT build: that
   *  is the case where guessing produces a board the user has to take apart. */
  reason: string;
  archetype: Archetype | null;
  matched: string[];
  options: Archetype[];
  explanation: string;
}

/** Which shape a brief means. Free and deterministic — keyword work on the
 *  packaged skill's own recognition notes, no model call. */
export function classifyArchetype(body: {
  brief?: string;
  requested?: string | null;
}): Promise<ArchetypeChoice> {
  return sendJson<ArchetypeChoice>("/api/prompt/archetypes/classify", "POST", body);
}

/** Place the nodes and wires of one shape. Dispatches nothing: the generation
 *  nodes land empty, so the board is a scaffold rather than a bill. */
export function buildArchetypeBoard(
  boardId: number,
  body: { key: string; scene_count?: number; aspect?: string; quality?: string },
): Promise<{ planId: number; key: string; nodes: number; edges: number }> {
  return sendJson(`/api/boards/${boardId}/archetype`, "POST", body);
}

/** What the fan-out would do, without changing anything. Every line becomes a
 *  billable node, so the count belongs in front of the user first. */
export function previewFanOut(
  boardId: number,
  promptNodeId: number,
): Promise<{
  lines: number;
  templateShortId: string;
  willCreate: number;
  willUpdate: number;
  willDelete: number;
}> {
  return api(`/api/boards/${boardId}/fan-out/${promptNodeId}`);
}

/** Give each line of a prompt node its own generation node. Idempotent:
 *  pressing it twice updates and prunes rather than doubling the board. */
export function fanOutBoard(
  boardId: number,
  promptNodeId: number,
): Promise<{
  lines: number;
  created: number;
  updated: number;
  deleted: number;
  templateShortId: string;
}> {
  return sendJson(`/api/boards/${boardId}/fan-out`, "POST", {
    prompt_node_id: promptNodeId,
  });
}

export interface RelayStage {
  name: string;
  provider: string;
  output: string;
  error: string;
}

export interface RelayResult {
  prompt: string;
  beatSheet: Record<string, unknown>;
  stages: RelayStage[];
  findings: PromptFinding[];
  revisions: number;
  /** False when something survived the revision round — a mechanical rule or
   *  the verifier's own verdict. The prompt still comes back; the caller
   *  decides, because it is the caller who pays for the dispatch. */
  clean: boolean;
}

/**
 * Three models in a relay: one plans, one writes, the rules are checked for
 * free, one verifies the meaning.
 *
 * Different from `autoPromptEnsemble`, which asks every provider the same
 * question and throws two thirds of the answers away. Slower than either, and
 * the only path that checks its own output before handing it back.
 */
export function relayPrompt(body: {
  brief: string;
  cast?: string[];
  lane?: string | null;
  seconds?: number | null;
}): Promise<RelayResult> {
  return sendJson<RelayResult>("/api/prompt/relay", "POST", body);
}

export interface StoryboardScene {
  title: string;
  image: string;
  video: string;
}

export interface StoryboardResult {
  scenes: StoryboardScene[];
  nodes: number;
  edges: number;
  /** Set only when `materialize` was requested. */
  plan_id: number | null;
  spec: Record<string, unknown>;
}

/**
 * An idea to a wired board: scenes, stills, clips and the chain between.
 *
 * Costs one text call. With `materialize: false` (the default) it writes no
 * rows at all, so the scenes can be read before anything appears on the
 * canvas. Even materialised, nothing is dispatched until Run — the estimate
 * still stands between the board and the credits.
 */
export function storyboardFromIdea(body: {
  board_id: number;
  idea: string;
  scene_count?: number;
  seconds?: number;
  structure?: "chain" | "independent";
  style?: string;
  aspect?: string;
  quality?: string;
  materialize?: boolean;
}): Promise<StoryboardResult> {
  return sendJson<StoryboardResult>("/api/prompt/storyboard", "POST", body);
}

export interface LibraryPromptEntry {
  id: string;
  number: number;
  title: string;
  /** The heading's description, without the "PROMPT n — " prefix. */
  summary: string;
}

/**
 * Headings of the bundled fashion-posing prompts.
 *
 * Headings only: the 47 bodies are ~5KB each, and fetching a quarter of a
 * megabyte to fill a dropdown would be silly. `fashionPrompt` fetches the
 * one the user picks.
 */
export async function fashionPrompts(): Promise<LibraryPromptEntry[]> {
  const res = await fetch("/api/prompt/library");
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return res.json() as Promise<LibraryPromptEntry[]>;
}

export async function fashionPrompt(
  id: string,
): Promise<LibraryPromptEntry & { body: string }> {
  const res = await fetch(`/api/prompt/library/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(await extractErrorMessage(res));
  return res.json() as Promise<LibraryPromptEntry & { body: string }>;
}

export interface AnalyzedScene {
  scene_number: number | null;
  image_prompt: string;
  veo_prompt: string;
  dialogue: string;
}

export interface AnalyzeVideoResult {
  description: string;
  prompt: string;
  frames: number;
  /** Which of the four bundled prompts ran, after resolving `mode`. */
  mode: string;
  /** Empty unless the resolved mode asks for a scene-by-scene breakdown. */
  scenes: AnalyzedScene[];
  script: string;
}

/**
 * Sample frames from a clip and have a vision model read the sequence.
 *
 * `mode` is the workflow's own `analysis_mode` label, emoji included — the
 * agent resolves it to one of the four system prompts the packaged tool
 * ships, which answer with a scene-by-scene breakdown instead of a
 * paragraph. Omit it for the plain description.
 */
export function analyzeVideo(body: {
  video: string;
  frames?: number;
  mode?: string;
  language?: string;
  scene_count?: number | null;
  style?: string;
  voice?: string;
  custom?: string;
}): Promise<AnalyzeVideoResult> {
  return sendJson<AnalyzeVideoResult>("/api/vision/video", "POST", body);
}

export function upscaleMedia(body: {
  source: string;
  output: string;
  scale?: number;
  model?: string;
  targetHeight?: number | null;
  kind?: "video" | "image";
}): Promise<RenderResult> {
  return postprod<RenderResult>("upscale", body);
}


// ── Canvas agent ──────────────────────────────────────────────────────────────
//
// The agent proposes; the user confirms. `apply` writes structure and returns
// any run it suggests as an INTENT — a node id list for the existing cost
// dialog. There is no endpoint here that dispatches anything, and that is the
// point: the decision to be charged stays with the user.

export interface AgentCatalog {
  nodeTypes: string[];
  portsIn: Record<string, string[]>;
  portsOut: Record<string, string[]>;
  characterPortLimits: Record<string, number>;
}

export interface AgentFinding {
  rule: string;
  /** `error` blocks the apply; `warning` is shown and passes. */
  severity: string;
  message: string;
  span: string;
}

export interface AgentActionDTO {
  id: number;
  kind: string;
  summary: string;
  payload: Record<string, unknown>;
  /** Which AI answered. The chain can fall through to one the panel never
   *  showed, and "why does this look nothing like last time" needs it. */
  provider: string | null;
}

export interface AgentIntent {
  kind: string;
  nodeIds: number[];
  note: string;
}

export interface AgentApplyResult {
  actions: AgentActionDTO[];
  intents: AgentIntent[];
  findings: AgentFinding[];
}

export interface AgentHistoryEntry {
  id: number;
  kind: string;
  summary: string;
  provider: string | null;
  createdAt: string | null;
  undone: boolean;
}

export function getAgentCatalog(): Promise<AgentCatalog> {
  return api<AgentCatalog>("/api/agent/catalog");
}

export function applyAgentActions(
  boardId: number,
  actions: { kind: string; payload: Record<string, unknown> }[],
  provider?: string | null
): Promise<AgentApplyResult> {
  return api<AgentApplyResult>(`/api/agent/boards/${boardId}/apply`, {
    method: "POST",
    body: JSON.stringify({ actions, provider: provider ?? null }),
  });
}

export function undoAgentAction(
  boardId: number
): Promise<{ undone: number; kind: string; summary: string }> {
  return api(`/api/agent/boards/${boardId}/undo`, { method: "POST" });
}

export function getAgentHistory(
  boardId: number
): Promise<{ actions: AgentHistoryEntry[] }> {
  return api(`/api/agent/boards/${boardId}/history`);
}
