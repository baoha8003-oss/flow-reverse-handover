import { useEffect, useRef, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useBoardStore, type FlowboardNodeData, type FlowNode } from "../store/board";
import { useGenerationStore } from "../store/generation";
import { mediaUrl, patchEdge, patchNode, uploadImage, uploadImageFromUrl } from "../api/client";
import { errorLabel } from "../lib/errorLabels";
import { nodeErrorView } from "../lib/nodeErrorLine";
import {
  createCharacterOnFlow,
  fanOutBoard,
  listCharacters,
  narrationSegments,
  previewFanOut,
  rereadNarration,
  saveCharacter,
  type FlowCharacter,
} from "../api/client";
import {
  hasHoles,
  rereadNote,
  segmentLabel,
  type RereadPreview,
} from "../lib/narrationReread";
import {
  WATERMARK_CORNERS,
  watermarkCorner,
  withWatermarkCorner,
} from "../lib/watermarkFallback";
import { requestAutoBrief } from "../api/autoBrief";
import { useReferencesStore } from "../store/references";
import { useAppConfigStore } from "../store/appConfig";
import { useCanvasUiStore } from "../store/canvasUi";
import { useClipOpsStore } from "../store/clipOps";
import {
  extendReadiness,
  primarySlot,
  upscaleReadiness,
} from "../lib/clipOps";
import {
  normaliseStoryboardGrid,
  resolveStoryboardLayout,
} from "../lib/storyboardPrompt";

const ICON: Record<string, string> = {
  character: "◎",
  image: "▣",
  video: "▶",
  motion_control: "🕺",
  prompt: "✦",
  note: "✎",
  visual_asset: "◇",
  analyze_video: "◱",
  merge_video: "⧉",
  edit_video: "✂",
  extract_last_frame: "⧗",
  add_bgm: "♪",
  create_voice: "🗣",
  align_video_voice: "⇔",
  sync_image_voice: "◉",
  remove_watermark: "⌫",
  review_video: "★",
};

const STATUS_COLOR: Record<string, string> = {
  idle: "transparent",
  queued: "rgba(245, 179, 1, 0.6)",
  running: "var(--accent)",
  done: "rgba(110, 231, 183, 0.8)",
  error: "#ef4444",
};

function StatusStrip({ status }: { status?: string }) {
  const color = STATUS_COLOR[status ?? "idle"] ?? "transparent";
  const isRunning = status === "running";
  return (
    <div
      className={isRunning ? "status-strip status-strip--running" : "status-strip"}
      style={{ background: color }}
    />
  );
}

const ACCEPT_MIME = "image/png,image/jpeg,image/webp,image/gif";

/** Which service draws this image node, when there is a choice.
 *
 * Hidden unless one of the OpenAI paths has been switched on in Settings —
 * or the node already asks for OpenAI, which is the case that must stay
 * visible: a board built elsewhere (or by the agent) can carry the setting
 * on a machine where it is off, and the run will refuse. Showing the chip
 * is how that becomes fixable instead of mysterious.
 */
function ImageEngineChip({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const config = useAppConfigStore((s) => s.values);
  const offered =
    config.OPENAI_IMAGE_ENABLED === true ||
    config.OPENAI_IMAGE_RELAY_ENABLED === true;
  const engine = data.imageEngine === "openai" ? "openai" : "flow";
  if (!offered && engine === "flow") return null;

  function pick(next: "flow" | "openai") {
    if (next === engine) return;
    useBoardStore.getState().updateNodeData(rfId, { imageEngine: next });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      patchNode(dbId, { data: { imageEngine: next } }).catch(() => {});
    }
  }

  return (
    <div className="image-engine">
      {(["flow", "openai"] as const).map((key) => (
        <button
          key={key}
          type="button"
          className={`image-engine__opt${engine === key ? " is-active" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            pick(key);
          }}
          title={
            key === "flow"
              ? "Google Flow vẽ ảnh này (mặc định, tốn credit Flow)"
              : "OpenAI vẽ ảnh này (tốn tiền/quota OpenAI, không tốn credit Flow)"
          }
        >
          {key === "flow" ? "Flow" : "OpenAI"}
        </button>
      ))}
      {!offered && engine === "openai" && (
        <span className="image-engine__warn" title="Bật trong Cài đặt → Tạo ảnh bằng OpenAI">
          đang tắt
        </span>
      )}
    </div>
  );
}

function BriefHint({ data }: { data: FlowboardNodeData }) {
  if (data.autoPromptStatus === "pending") {
    return <p className="brief-hint brief-hint--pending">✨ Composing prompt…</p>;
  }
  if (data.aiBriefStatus === "pending") {
    return <p className="brief-hint brief-hint--pending">✨ Analyzing…</p>;
  }
  if (data.aiBrief) {
    return <p className="brief-hint" title={data.aiBrief}>✨ {data.aiBrief}</p>;
  }
  return null;
}

/**
 * True while the LLM layer is doing work on this node — composing an
 * auto-prompt or describing media for an aiBrief. Used to add a busy
 * treatment + disable Generate so the user can't double-fire.
 */
function isLLMBusy(data: FlowboardNodeData): boolean {
  return (
    data.autoPromptStatus === "pending"
    || data.aiBriefStatus === "pending"
  );
}

/** Which registered character this node IS.
 *
 * A character node used to be a picture with a title. The packaged tool's
 * component mode sends `referenceEntities: [{entityId}]` — ids Flow issues in
 * its own UI, per project — so a node needs a way to say which of those it
 * stands for. Without this the entity path was reachable over HTTP and nowhere
 * else, which is where audit items #3 and #13 had been sitting.
 *
 * Two ways to get one. `＋` CREATES the character on Flow and keeps the id it
 * returns — one name, one click. `⌨` registers an id the user already made in
 * Flow's own UI, which stays because it is still a valid way in (and the only
 * one when Flow refuses the create), not as a leftover.
 */
function CharacterLink({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const boardId = useBoardStore((s) => s.boardId);
  const [rows, setRows] = useState<FlowCharacter[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const linked = data.characterId ?? "";

  useEffect(() => {
    if (boardId === null) return;
    let live = true;
    listCharacters(boardId)
      .then((list) => live && setRows(list))
      // A board with no Flow project yet answers 409; that is not an error the
      // card should shout about — it resolves on the first run.
      .catch(() => live && setRows([]));
    return () => {
      live = false;
    };
  }, [boardId]);

  function link(next: string) {
    useBoardStore.getState().updateNodeData(rfId, { characterId: next || undefined });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      // `null`, not undefined: the backend merges `data`, and undefined is
      // dropped by JSON.stringify — the old link would survive the merge.
      patchNode(dbId, { data: { characterId: next || null } }).catch(() => {});
    }
  }

  /** Store what came back and point this node at it. */
  function adopt(saved: FlowCharacter) {
    setRows((prev) => [...prev.filter((r) => r.id !== saved.id), saved]);
    link(saved.id);
    setNote(null);
  }

  /** Ask Flow to create the character, then register the id it returns.
   *
   * Guarded by `busy` because a second click would create a SECOND character on
   * Flow under the same name — two entities, and the prompt tag `@@Name` can
   * then only mean one of them. */
  async function register() {
    if (boardId === null || busy) return;
    const name = window.prompt(
      "Tên nhân vật (prompt sẽ tag bằng @@Tên, chỉ A-Z 0-9 _):",
      data.title || "",
    );
    if (name === null || !name.trim()) return;
    setBusy(true);
    try {
      adopt(
        await createCharacterOnFlow(boardId, {
          name: name.trim(),
          mediaId: data.mediaId ?? "",
        }),
      );
    } catch (e) {
      // Keep the reason AND point at the way out. Flow refusing the create is
      // not the end of the road: the character can still be made in Flow's UI
      // and its id registered here.
      const why = e instanceof Error ? e.message : "không tạo được";
      setNote(`Flow từ chối tạo nhân vật: ${why} — tạo trong Flow rồi dán id bằng nút ⌨.`);
    } finally {
      setBusy(false);
    }
  }

  /** Register a character the user already made in Flow — paste its id. */
  async function registerByPaste() {
    if (boardId === null || busy) return;
    const name = window.prompt(
      "Tên nhân vật (prompt sẽ tag bằng @@Tên, chỉ A-Z 0-9 _):",
      data.title || "",
    );
    if (name === null || !name.trim()) return;
    const entityId = window.prompt(
      "Entity ID từ Flow (bỏ trống nếu chỉ dùng ảnh tham chiếu):",
      "",
    );
    if (entityId === null) return;
    setBusy(true);
    try {
      adopt(
        await saveCharacter(boardId, {
          name: name.trim(),
          entityId: entityId.trim(),
          mediaId: data.mediaId ?? "",
        }),
      );
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Không lưu được nhân vật.");
    } finally {
      setBusy(false);
    }
  }

  const current = rows.find((r) => r.id === linked);

  return (
    <div className="character-link" onClick={(e) => e.stopPropagation()}>
      <select
        value={linked}
        onChange={(e) => link(e.target.value)}
        title="Nhân vật đã đăng ký trong project Flow của board này"
      >
        <option value="">— chỉ ảnh tham chiếu —</option>
        {rows.map((row) => (
          <option key={row.id} value={row.id}>
            {row.name}
            {row.entityId ? " · entity" : ""}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => void register()}
        disabled={busy}
        title="Tạo nhân vật mới trên Flow (không tốn credit)"
      >
        {busy ? "…" : "＋"}
      </button>
      <button
        type="button"
        onClick={() => void registerByPaste()}
        disabled={busy}
        title="Dán Entity ID của nhân vật đã tạo sẵn trong Flow"
      >
        ⌨
      </button>
      {current && !current.usable && (
        <p className="character-link__warn" role="alert">
          Entity này thuộc project khác — chạy sẽ bị từ chối. Tạo lại trong đúng
          project Flow rồi dán lại id.
        </p>
      )}
      {note && (
        <p className="character-link__warn" role="alert">
          {note}
        </p>
      )}
    </div>
  );
}

function CharacterBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const mediaId = data.mediaId;
  const isProcessing = data.status === "queued" || data.status === "running";
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function persistMedia(newMediaId: string, aspectRatio?: string) {
    useBoardStore.getState().updateNodeData(rfId, {
      mediaId: newMediaId,
      status: "done",
      aiBrief: undefined,
      aspectRatio,
    });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      // Backend merges `data`, so we only need to send the deltas.
      // `null` is the explicit "clear this key" sentinel — undefined
      // gets dropped by JSON.stringify and would leave the stale brief
      // in place after the merge.
      patchNode(dbId, {
        status: "done",
        data: {
          mediaId: newMediaId,
          aiBrief: null,
          aspectRatio,
          renderedAt: new Date().toISOString(),
        },
      }).catch(() => {});
    }
    // Background vision call — fire-and-forget. Sets aiBrief on the node
    // when it returns; failure is silent.
    requestAutoBrief(rfId, newMediaId);
  }

  async function uploadOwn(file: File) {
    setError(null);
    setUploading(true);
    try {
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        setError("no project");
        return;
      }
      const dbId = parseInt(rfId, 10);
      const resp = await uploadImage(file, projectId, isNaN(dbId) ? undefined : dbId);
      persistMedia(resp.media_id, resp.aspect_ratio);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  function onPick() {
    fileInputRef.current?.click();
  }

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) uploadOwn(f);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) uploadOwn(f);
  }

  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!dragOver) setDragOver(true);
  }

  function onDragLeave(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }

  function openGenerate() {
    useGenerationStore.getState().openGenerationDialog(rfId, data.prompt ?? "");
  }

  // Filled state — show the avatar circle. Drag-drop on the avatar replaces it.
  if (mediaId) {
    return (
      <div
        className="node-body node-body--character"
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
      >
        <div
          className={`character-avatar${dragOver ? " character-avatar--over" : ""}${uploading ? " character-avatar--uploading" : ""}`}
          onClick={onPick}
          role="button"
          aria-label="Replace character image"
          tabIndex={0}
        >
          <img
            className="character-avatar__img"
            src={mediaUrl(mediaId)}
            alt={data.title}
          />
          {uploading && <span className="character-drop__overlay">…</span>}
        </div>
        <BriefHint data={data} />
        <CharacterLink rfId={rfId} data={data} />
        <button
          type="button"
          className="visual-asset__action"
          onClick={(e) => {
            e.stopPropagation();
            saveTileToLibrary({
              mediaId,
              nodeType: data.type,
              data,
            });
          }}
          title="Save this character to the library"
          aria-label="Save to library"
        >
          ★ Save
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_MIME}
          style={{ display: "none" }}
          onChange={onChange}
        />
        {error && <p className="character-drop__error" role="alert">{error}</p>}
      </div>
    );
  }

  // Empty state — compact action row (no oversized placeholder), but the
  // whole body still accepts drag-drop.
  return (
    <div
      className="node-body node-body--character"
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
    >
      <div
        className={`character-empty${dragOver ? " character-empty--over" : ""}${isProcessing ? " character-empty--processing" : ""}`}
      >
        {isProcessing ? (
          <span className="visual-asset__hint">Generating…</span>
        ) : dragOver ? (
          <span className="visual-asset__hint">Drop image</span>
        ) : (
          <>
            <button
              type="button"
              className="visual-asset__action"
              onClick={onPick}
              disabled={uploading}
            >
              {uploading ? "Uploading…" : "Upload"}
            </button>
            <button
              type="button"
              className="visual-asset__action"
              onClick={openGenerate}
              disabled={uploading}
            >
              Generate
            </button>
          </>
        )}
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT_MIME}
        style={{ display: "none" }}
        onChange={onChange}
      />
      {error && <p className="character-drop__error" role="alert">{error}</p>}
    </div>
  );
}

// ── Reference-library save helpers ────────────────────────────────────────
//
// Maps a FlowboardNodeData.type → the `kind` enum stored on a Reference
// row. Storyboard nodes are containers — each saved tile is one *shot*
// of the board, so we use the "storyboard_shot" kind there to leave
// room for shot-specific UX in the library later (e.g. surfacing the
// shot index, or grouping by parent storyboard).
type ReferenceKind = "image" | "character" | "visual_asset" | "storyboard_shot";

function referenceKindFor(nodeType: string): ReferenceKind {
  if (nodeType === "Storyboard") return "storyboard_shot";
  if (nodeType === "character") return "character";
  if (nodeType === "visual_asset") return "visual_asset";
  return "image";
}

/** Fire-and-forget save of a tile's media into the reference library.
 * Errors surface via useReferencesStore.error; UI doesn't need to
 * await for the save to succeed before letting the user keep working. */
function saveTileToLibrary(opts: {
  mediaId: string;
  nodeType: string;
  data: FlowboardNodeData;
}) {
  const { mediaId, nodeType, data } = opts;
  const label =
    typeof data.aiBrief === "string" && data.aiBrief.trim().length > 0
      ? data.aiBrief.slice(0, 80)
      : `#${data.shortId}`;
  void useReferencesStore.getState().save({
    media_id: mediaId,
    kind: referenceKindFor(nodeType),
    ai_brief: typeof data.aiBrief === "string" ? data.aiBrief : null,
    aspect_ratio: typeof data.aspectRatio === "string" ? data.aspectRatio : null,
    label,
    source_board_id: useBoardStore.getState().boardId ?? null,
    source_node_short_id:
      typeof data.shortId === "string" ? data.shortId : null,
  });
}

const MAX_IMG_RETRIES = 5;

function tileCountFor(data: FlowboardNodeData): number {
  const fromVariants = data.variantCount;
  const fromMedia = data.mediaIds?.length;
  const n = fromVariants && fromVariants > 0 ? fromVariants : fromMedia ?? 1;
  return Math.max(1, Math.min(n, 4));
}

function ImageTile({
  rfId,
  mediaId,
  isProcessing,
  alt,
  onClick,
  onUseAsRef,
  onSaveToLibrary,
}: {
  rfId: string;
  mediaId: string | undefined;
  isProcessing: boolean;
  alt: string;
  onClick?: () => void;
  /** When provided, render an overlay button on hover that pins this
   * variant to a downstream edge and triggers Generate on the target.
   * The parent only sets this when the node has multi-variant output
   * AND has a downstream image/video target — keeps the affordance
   * scoped to cases where it actually does something. */
  onUseAsRef?: () => void;
  /** When provided, render a "★" overlay (top-right corner, opposite
   * the "Use →" affordance) that snapshots this tile's media + aiBrief
   * into the cross-board reference library. Parents only pass this when
   * the tile has a real mediaId — saving a placeholder makes no sense. */
  onSaveToLibrary?: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const previewsHidden = useCanvasUiStore((s) => s.previewsHidden);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLoaded(false);
    setAttempt(0);
    return () => {
      if (retryTimerRef.current !== null) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [mediaId, rfId]);

  if (!mediaId) {
    return (
      <div
        className={`thumbnail-tile${isProcessing ? " thumbnail-tile--processing" : ""}`}
        aria-hidden="true"
      >
        <span className="thumbnail-tile__icon">▣</span>
      </div>
    );
  }

  // Previews hidden: the tile still says there IS a result, and stays
  // clickable so the viewer is one click away. Rendering nothing would be
  // indistinguishable from an empty node, which is the opposite of true.
  if (previewsHidden) {
    return (
      <div
        className="thumbnail-tile thumbnail-tile--hidden"
        role={onClick ? "button" : undefined}
        tabIndex={onClick ? 0 : undefined}
        title="Preview đang tắt — bấm để mở kết quả"
        onClick={onClick}
        onKeyDown={(e) => {
          if (!onClick) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick();
          }
        }}
      >
        <span className="thumbnail-tile__icon">✓</span>
      </div>
    );
  }

  const givenUp = attempt >= MAX_IMG_RETRIES;
  const src = attempt > 0 ? `${mediaUrl(mediaId)}?retry=${attempt}` : mediaUrl(mediaId);
  const cls =
    `thumbnail-tile thumbnail-tile--filled` +
    (onClick ? " thumbnail-tile--clickable" : "");

  return (
    <div
      className={cls}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `Open variant ${alt}` : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (!onClick) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
      {!loaded && (
        <div className="thumbnail-tile__placeholder" aria-hidden="true" />
      )}
      {!givenUp && (
        <img
          key={attempt}
          className="thumbnail-tile__img"
          src={src}
          alt={alt}
          style={loaded ? undefined : { display: "none" }}
          onLoad={() => setLoaded(true)}
          onError={() => {
            retryTimerRef.current = setTimeout(() => {
              setAttempt((a) => a + 1);
            }, 2000);
          }}
        />
      )}
      {onUseAsRef && (
        // Overlay action — visible on hover via CSS. Stops propagation
        // so clicking the chip doesn't also trigger the tile's
        // openResultViewer. Title doubles as accessible label.
        <button
          type="button"
          className="thumbnail-tile__use-btn"
          onClick={(e) => {
            e.stopPropagation();
            onUseAsRef();
          }}
          title="Use this variant as the reference for a downstream node"
          aria-label="Use this variant as reference"
        >
          Use →
        </button>
      )}
      {onSaveToLibrary && (
        // ★ overlay — top-right corner, opposite the "Use →" chip in
        // the bottom-right. Fire-and-forget save into the cross-board
        // reference library. Same stopPropagation pattern so clicking
        // the star doesn't also open the result viewer.
        <button
          type="button"
          className="thumbnail-tile__save-btn"
          onClick={(e) => {
            e.stopPropagation();
            onSaveToLibrary();
          }}
          title="Save this variant to the library"
          aria-label="Save to library"
        >
          ★
        </button>
      )}
    </div>
  );
}

// ── Variant-click → bind upstream variant to a downstream edge ───────────
//
// Workflow: user clicks "Use →" on a specific variant tile of an
// upstream multi-variant node. We find the downstream image/video
// targets connected to it, pin the chosen variant index on the right
// edge (PATCH /api/edges/{id}), refresh the local edge.data so the
// `v{N+1}` chip surfaces immediately, and then dispatch Generate on
// the target. One click → one pinned ref → one Flow API call.
//
// Multi-target case: when the upstream has 2+ outgoing edges to gen
// targets, we surface a small picker so the user disambiguates which
// downstream this variant should feed.

interface VariantTarget {
  edgeId: string;
  targetRfId: string;
  title: string;
  kind: "image" | "video";
  hasPrompt: boolean;
}

interface VariantPickerState {
  variantIdx: number;
  targets: VariantTarget[];
}

function collectGenTargets(srcRfId: string): VariantTarget[] {
  const { nodes, edges } = useBoardStore.getState();
  const out: VariantTarget[] = [];
  for (const e of edges) {
    if (e.source !== srcRfId) continue;
    const t = nodes.find((n) => n.id === e.target);
    if (!t) continue;
    if (t.data.type !== "image" && t.data.type !== "video") continue;
    out.push({
      edgeId: e.id,
      targetRfId: t.id,
      title: t.data.title || `#${t.data.shortId}`,
      kind: t.data.type as "image" | "video",
      hasPrompt: typeof t.data.prompt === "string" && t.data.prompt.trim().length > 0,
    });
  }
  return out;
}

async function applyVariantToTarget(variantIdx: number, target: VariantTarget) {
  const edgeDbId = parseInt(target.edgeId, 10);
  if (!isNaN(edgeDbId)) {
    try {
      const updated = await patchEdge(edgeDbId, {
        source_variant_idx: variantIdx,
      });
      useBoardStore.getState().updateEdgeData(target.edgeId, {
        sourceVariantIdx: updated.source_variant_idx,
      });
    } catch (err) {
      useGenerationStore.setState({
        error: `Couldn't pin variant: ${err instanceof Error ? err.message : String(err)}`,
      });
      return;
    }
  }
  // If the target doesn't have a prompt yet, we open the GenerationDialog
  // instead of dispatching blind — the dialog gives the user the
  // auto-prompt path or a place to type. The pin we just persisted will
  // apply to whichever Generate is fired from the dialog.
  const targetNode = useBoardStore
    .getState()
    .nodes.find((n) => n.id === target.targetRfId);
  if (!targetNode) return;
  const prompt = (targetNode.data.prompt ?? "").trim();
  if (!prompt) {
    useGenerationStore.getState().openGenerationDialog(target.targetRfId, "");
    return;
  }
  await useGenerationStore.getState().dispatchGeneration(target.targetRfId, {
    prompt,
    kind: target.kind,
    aspectRatio: targetNode.data.aspectRatio,
    variantCount: targetNode.data.variantCount,
  });
}

function VariantPicker({
  state,
  onPick,
  onCancel,
}: {
  state: VariantPickerState;
  onPick(target: VariantTarget): void;
  onCancel(): void;
}) {
  return (
    <div className="variant-picker" role="dialog" aria-label="Pick downstream target">
      <div className="variant-picker__heading">
        Use variant v{state.variantIdx + 1} for:
      </div>
      <ul className="variant-picker__list">
        {state.targets.map((t) => (
          <li key={t.edgeId}>
            <button
              type="button"
              className="variant-picker__btn"
              onClick={() => onPick(t)}
            >
              {t.title}
              <span className="variant-picker__kind">
                {t.kind === "video" ? "video" : "image"}
                {!t.hasPrompt ? " · empty" : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="variant-picker__cancel"
        onClick={onCancel}
      >
        Cancel
      </button>
    </div>
  );
}

function ImageBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const tileCount = tileCountFor(data);
  const ids = data.mediaIds ?? (data.mediaId ? [data.mediaId] : []);
  const hasMedia = ids.length > 0;
  const isProcessing = data.status === "queued" || data.status === "running";

  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  // Variant-picker state for the multi-downstream "Use →" flow. MUST be
  // declared above the empty-state early-return below — Rules of Hooks
  // require the same call order on every render, and the empty/filled
  // branches change which JSX renders but not which hooks run.
  const [picker, setPicker] = useState<VariantPickerState | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function persistMedia(newMediaId: string, aspectRatio?: string) {
    useBoardStore.getState().updateNodeData(rfId, {
      mediaId: newMediaId,
      mediaIds: undefined,
      variantCount: 1,
      status: "done",
      aiBrief: undefined,
      aspectRatio,
    });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      // Backend merges `data`. `null` is the explicit "delete this key"
      // sentinel — used here to drop stale variant arrays + cached brief
      // when the user replaces a generated set with a single uploaded image.
      patchNode(dbId, {
        status: "done",
        data: {
          mediaId: newMediaId,
          mediaIds: null,
          variantCount: 1,
          aiBrief: null,
          aspectRatio,
          renderedAt: new Date().toISOString(),
        },
      }).catch(() => {});
    }
    requestAutoBrief(rfId, newMediaId);
  }

  async function uploadOwn(file: File) {
    setError(null);
    setUploading(true);
    try {
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        setError("no project");
        return;
      }
      const dbId = parseInt(rfId, 10);
      const resp = await uploadImage(file, projectId, isNaN(dbId) ? undefined : dbId);
      persistMedia(resp.media_id, resp.aspect_ratio);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  function onPick() {
    fileInputRef.current?.click();
  }

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) uploadOwn(f);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) uploadOwn(f);
  }

  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!dragOver) setDragOver(true);
  }

  function onDragLeave(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }

  function openGenerate() {
    useGenerationStore.getState().openGenerationDialog(rfId, data.prompt ?? "");
  }

  const hiddenFileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={ACCEPT_MIME}
      style={{ display: "none" }}
      onChange={onChange}
    />
  );

  // Empty state — same action-bar UX as character/visual_asset so users
  // can drop a reference image directly onto an image node instead of
  // having to wire one up via a separate visual_asset node.
  if (!hasMedia && !isProcessing) {
    return (
      <div
        className="node-body node-body--image"
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
      >
        <div className={`character-empty${dragOver ? " character-empty--over" : ""}`}>
          {dragOver ? (
            <span className="visual-asset__hint">Drop image</span>
          ) : (
            <>
              <button
                type="button"
                className="visual-asset__action"
                onClick={onPick}
                disabled={uploading}
              >
                {uploading ? "Uploading…" : "Upload"}
              </button>
              <button
                type="button"
                className="visual-asset__action"
                onClick={openGenerate}
                disabled={uploading}
              >
                Generate
              </button>
            </>
          )}
        </div>
        <ImageEngineChip rfId={rfId} data={data} />
        <BriefHint data={data} />
        {hiddenFileInput}
        {error && <p className="character-drop__error" role="alert">{error}</p>}
      </div>
    );
  }

  // Variant-click flow: when this node is multi-variant AND has a
  // downstream image/video target, each tile gets a "Use →" overlay
  // button. Clicking it pins this variant on the appropriate edge and
  // dispatches Generate on the target. See `applyVariantToTarget` above.
  const isMultiVariant = ids.length >= 2;

  function onUseVariantClick(variantIdx: number) {
    const targets = collectGenTargets(rfId);
    if (targets.length === 0) {
      useGenerationStore.setState({
        error: "Connect this image to a downstream image/video target first.",
      });
      return;
    }
    if (targets.length === 1) {
      void applyVariantToTarget(variantIdx, targets[0]);
      return;
    }
    setPicker({ variantIdx, targets });
  }

  const tiles: JSX.Element[] = [];
  for (let i = 0; i < tileCount; i++) {
    const rawMid = ids[i];
    const mid = typeof rawMid === "string" && rawMid ? rawMid : undefined;
    // Click a tile → open viewer at that variant. The "Use →" overlay
    // (when present) is a separate action handled by onUseAsRef.
    const onClick = mid
      ? () => useGenerationStore.getState().openResultViewer(rfId, i)
      : undefined;
    tiles.push(
      <ImageTile
        key={i}
        rfId={rfId}
        mediaId={mid}
        isProcessing={isProcessing && !mid}
        alt={data.title}
        onClick={onClick}
        onUseAsRef={
          isMultiVariant && mid && !isProcessing
            ? () => onUseVariantClick(i)
            : undefined
        }
        onSaveToLibrary={
          mid
            ? () =>
                saveTileToLibrary({
                  mediaId: mid,
                  nodeType: data.type,
                  data,
                })
            : undefined
        }
      />
    );
  }

  return (
    <div
      className={`node-body node-body--image${dragOver ? " node-body--image--over" : ""}`}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
    >
      <div className={`thumbnail-grid thumbnail-grid--${tileCount}`}>
        {tiles}
      </div>
      {picker && (
        <VariantPicker
          state={picker}
          onPick={(target) => {
            void applyVariantToTarget(picker.variantIdx, target);
            setPicker(null);
          }}
          onCancel={() => setPicker(null)}
        />
      )}
      <ImageEngineChip rfId={rfId} data={data} />
      <BriefHint data={data} />
      {hiddenFileInput}
      {error && <p className="character-drop__error" role="alert">{error}</p>}
    </div>
  );
}

const MAX_VIDEO_RETRIES = 5;

function VideoTile({
  mediaId,
  posterMediaId,
  isProcessing,
  isError,
  slotError,
  alt,
  onClick,
}: {
  mediaId: string | undefined;
  // Upstream image's mediaId — used as the static poster so the tile
  // shows the source-image framing (subject centered, just like the
  // image-tile preview) instead of the video's frame-0 which often
  // catches a setup beat (ceiling, empty room) before the subject is
  // composed in.
  posterMediaId?: string | undefined;
  isProcessing: boolean;
  isError: boolean;
  // Per-slot error code (e.g. "PUBLIC_ERROR_UNSAFE_GENERATION") when
  // this specific variant got blocked by Veo's safety classifier. Only
  // surfaced for the partial-batch case so the tile can render a
  // distinctive ⚠ + tooltip instead of the generic empty placeholder.
  slotError?: string | null;
  alt: string;
  onClick?: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLoaded(false);
    setAttempt(0);
    return () => {
      if (retryTimerRef.current !== null) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [mediaId]);

  const blockedTitle = slotError
    ? `Variant blocked: ${slotError} — click for details`
    : undefined;

  const placeholder = (
    <div
      className={`video-placeholder${isProcessing ? " video-placeholder--processing" : ""}${isError ? " video-placeholder--error" : ""}${slotError ? " video-placeholder--blocked" : ""}`}
      aria-hidden="true"
      title={blockedTitle}
    >
      {slotError ? (
        <>
          <span className="video-blocked-icon">⚠</span>
          <span className="video-blocked-label">Blocked</span>
        </>
      ) : (
        <>
          <span className="video-play">▶</span>
          <span className="video-duration">0:00</span>
        </>
      )}
    </div>
  );

  if (!mediaId) {
    // Pending / failed tile — just the placeholder. When `slotError` is
    // set the placeholder swaps to the warning treatment above. We
    // still attach onClick so the user can click through to the
    // detail viewer to read the full error.
    const cls = `video-tile${slotError ? " video-tile--blocked" : ""}${onClick ? " video-tile--clickable" : ""}`;
    return (
      <div
        className={cls}
        role={onClick ? "button" : undefined}
        tabIndex={onClick ? 0 : undefined}
        aria-label={blockedTitle ?? (onClick ? `Open variant ${alt}` : undefined)}
        title={blockedTitle}
        onClick={onClick}
        onKeyDown={(e) => {
          if (!onClick) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick();
          }
        }}
      >
        {placeholder}
      </div>
    );
  }

  const givenUp = attempt >= MAX_VIDEO_RETRIES;
  const src = attempt > 0 ? `${mediaUrl(mediaId)}?retry=${attempt}` : mediaUrl(mediaId);
  const cls =
    `video-tile video-tile--filled` +
    (onClick ? " video-tile--clickable" : "");

  return (
    <div
      className={cls}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `Open variant ${alt}` : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (!onClick) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
      {!loaded && placeholder}
      {!givenUp && posterMediaId ? (
        // Thumbnail = static poster image (the upstream i2v source).
        // Mounting a <video> here decodes frame 0 in Chrome and
        // overrides the poster attribute, which is what made every
        // tile display the video's setup beat (often empty ceiling)
        // instead of the subject-centered framing. The full video
        // with controls plays in the ResultViewer modal — clicking
        // a tile already routes there.
        <img
          key={`poster-${attempt}`}
          className="video-tile__poster"
          src={mediaUrl(posterMediaId)}
          alt={alt}
          onLoad={() => setLoaded(true)}
          onError={() => {
            retryTimerRef.current = setTimeout(() => {
              setAttempt((a) => a + 1);
            }, 2000);
          }}
        />
      ) : !givenUp ? (
        // Fallback: no upstream poster available (orphan video node).
        // Mount the <video> directly with `preload="none"` so the
        // browser shows the bare frame instead of decoding frame 0.
        <video
          key={attempt}
          className="node-card__thumbnail"
          data-kind="video"
          src={src}
          preload="none"
          muted
          aria-label={alt}
          style={loaded ? undefined : { display: "none" }}
          onLoadedData={() => setLoaded(true)}
          onError={() => {
            retryTimerRef.current = setTimeout(() => {
              setAttempt((a) => a + 1);
            }, 2000);
          }}
        />
      ) : null}
      {posterMediaId && (
        <span className="video-tile__play-badge" aria-hidden="true">▶</span>
      )}
    </div>
  );
}

/** Upscale and extend, on a clip that already rendered.
 *
 * Both act on something the user already paid for, so the card is careful about
 * two things. The upscale lands on its OWN key — the original stays, because a
 * "make this better" button that replaces it is a loss with no undo. And an
 * operation this clip cannot support shows as a DISABLED button carrying the
 * reason, not as a missing one: "the button is not there" teaches nothing, while
 * "Flow only extends Veo clips" is the answer.
 *
 * Neither price is known. This build has never measured 1080p upscale or the
 * extension lanes, and the community capture that reported "0 credit" for the
 * free lane measured it on someone else's account. So the card says so instead
 * of implying free — the first real run is what measures it.
 */
function ClipOpsBar({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const running = useClipOpsStore((s) => s.running[rfId]);
  const note = useClipOpsStore((s) => s.notes[rfId]);
  const [paidLane, setPaidLane] = useState(false);

  // Nothing rendered yet: these operations have no subject.
  if (primarySlot(data) < 0) return null;

  const up = upscaleReadiness(data);
  const ext = extendReadiness(data);
  const busy = Boolean(running);
  const chain = data.extensionMediaIds ?? [];

  async function onExtend() {
    const prompt = window.prompt("Đoạn tiếp diễn ra thế nào?", data.prompt ?? "");
    if (prompt === null || !prompt.trim()) return;
    if (paidLane && !window.confirm(
      "Làn trả phí: Flow sẽ tính credit cho lần nối này. Tiếp tục?",
    )) {
      return;
    }
    await useClipOpsStore.getState().extendClip(rfId, prompt.trim(), { paidLane });
  }

  return (
    <div className="clip-ops" onClick={(e) => e.stopPropagation()}>
      <div className="clip-ops__row">
        <button
          type="button"
          disabled={busy || !up.ok}
          title={up.ok ? "Nâng lên 1080p — chưa đo được giá" : up.reason}
          onClick={() => void useClipOpsStore.getState().upscaleClip(rfId)}
        >
          {running?.kind === "upscale" ? "Đang nâng cấp…" : "⬆ 1080p"}
        </button>
        <button
          type="button"
          disabled={busy || !ext.ok}
          title={
            ext.ok
              ? `Nối thêm một đoạn (đoạn thứ ${ext.position}) — chưa đo được giá`
              : ext.reason
          }
          onClick={() => void onExtend()}
        >
          {running?.kind === "extend" ? "Đang nối…" : "⏵ Nối tiếp"}
        </button>
        <label className="clip-ops__lane" title="Mặc định dùng làn miễn phí">
          <input
            type="checkbox"
            checked={paidLane}
            disabled={busy}
            onChange={(e) => setPaidLane(e.target.checked)}
          />
          trả phí
        </label>
      </div>
      {data.upscaledMediaId && (
        <p className="clip-ops__out">Đã có bản 1080p (giữ cả bản gốc).</p>
      )}
      {chain.length > 0 && (
        <p className="clip-ops__out">
          {chain.length} đoạn nối — ghép lại bằng node “Ghép video”.
        </p>
      )}
      {note && (
        <p className="clip-ops__note" role="status">
          {note}
        </p>
      )}
    </div>
  );
}


function VideoBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const tileCount = tileCountFor(data);
  const ids = data.mediaIds ?? (data.mediaId ? [data.mediaId] : []);
  const isProcessing = data.status === "queued" || data.status === "running";
  const isError = data.status === "error";
  // Partial-batch case: status="done" + an error string means some
  // variants succeeded and others got blocked (filter / timeout).
  // Slot-level signal: `mediaIds[i] === null` is a positional
  // placeholder for a blocked variant — render the tile as filtered
  // rather than empty/processing.
  const isPartial = data.status === "done" && Boolean(data.error);

  // Resolve the upstream image used as the i2v source — its variants
  // become the per-tile poster so the static preview shows the same
  // subject-centered framing as the upstream image card. Multi-source
  // i2v: variant i of the video came from variant i of the upstream
  // image; single-source: every tile shares the same poster.
  const { nodes, edges } = useBoardStore.getState();
  const upstreamEdge = edges.find((e) => e.target === rfId);
  const upstreamNode = upstreamEdge
    ? nodes.find((n) => n.id === upstreamEdge.source)
    : undefined;
  const posterIds: (string | null)[] =
    upstreamNode?.data.mediaIds ??
    (upstreamNode?.data.mediaId ? [upstreamNode.data.mediaId] : []);

  const tiles: JSX.Element[] = [];
  for (let i = 0; i < tileCount; i++) {
    const rawMid = ids[i];
    const mid = typeof rawMid === "string" && rawMid ? rawMid : undefined;
    const slotError = data.slotErrors?.[i] ?? null;
    const slotBlocked = isPartial && rawMid === null;
    // Even blocked tiles get a click handler so the user can open the
    // detail viewer and read the full filter reason — without it the
    // tile is dead and the user has no way to understand why it's
    // empty.
    const onClick =
      mid || slotBlocked
        ? () => useGenerationStore.getState().openResultViewer(rfId, i)
        : undefined;
    // Pick the i-th source variant if available; fall back to the
    // first non-null source for single-source i2v where every video
    // shares it.
    const rawPoster = posterIds[i] ?? posterIds.find((p) => Boolean(p)) ?? null;
    const poster = typeof rawPoster === "string" ? rawPoster : undefined;
    tiles.push(
      <VideoTile
        key={i}
        mediaId={mid}
        posterMediaId={poster}
        isProcessing={isProcessing && !mid}
        isError={(isError && !mid) || slotBlocked}
        slotError={slotError}
        alt={data.title}
        onClick={onClick}
      />,
    );
  }

  return (
    <div className="node-body node-body--video">
      <div className={`video-grid video-grid--${tileCount}`}>
        {tiles}
      </div>
      <ReviewLoopToggle rfId={rfId} data={data} />
      <ClipOpsBar rfId={rfId} data={data} />
    </div>
  );
}


/** Why this node failed — on every kind of node.
 *
 * This lived inside `VideoBody` and nowhere else, so an image, character,
 * storyboard, upload or post-production node that failed showed the red status
 * strip with no text on it. The worst case is a refusal whose whole purpose is to
 * say what to change: set an image node to OpenAI, leave a reference image wired,
 * press ▶, and the only explanation appeared in a toast that dismisses itself on
 * a timer.
 */
function NodeErrorLine({ data }: { data: FlowboardNodeData }) {
  // The rule lives in `lib/nodeErrorLine` so it can be tested: this suite runs in
  // node with no DOM, so a card-only rule is a rule nothing checks — which is how
  // this stayed video-only for as long as it did.
  const view = nodeErrorView(data);
  if (!view) return null;
  return (
    <p
      className={`node-error${view.isError ? "" : " node-error--partial"}`}
      role={view.isError ? "alert" : "status"}
      // The code stays in the tooltip: it is what a search, a log line and a bug
      // report all match on.
      title={view.code}
    >
      {errorLabel(view.code)}
    </p>
  );
}

function VisualAssetBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const mediaId = data.mediaId;
  const isProcessing = data.status === "queued" || data.status === "running";
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refineOpen, setRefineOpen] = useState(false);
  const [refinePrompt, setRefinePrompt] = useState("");
  const [refRefreshKey, setRefRefreshKey] = useState(0);
  const [refMediaId, setRefMediaId] = useState<string | null>(null);
  const [linkMode, setLinkMode] = useState(false);
  const [linkValue, setLinkValue] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const refInputRef = useRef<HTMLInputElement>(null);

  function persistMedia(newMediaId: string, aspectRatio?: string) {
    useBoardStore.getState().updateNodeData(rfId, {
      mediaId: newMediaId,
      mediaIds: [newMediaId],
      variantCount: 1,
      status: "done",
      aiBrief: undefined,
      aspectRatio,
    });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      // Backend merges `data`, so we only need to send the deltas.
      // `null` clears aiBrief explicitly (undefined would be dropped
      // by JSON.stringify and leave the stale brief in place).
      patchNode(dbId, {
        status: "done",
        data: {
          mediaId: newMediaId,
          mediaIds: [newMediaId],
          variantCount: 1,
          aiBrief: null,
          aspectRatio,
          renderedAt: new Date().toISOString(),
        },
      }).catch(() => {});
    }
    requestAutoBrief(rfId, newMediaId);
  }

  async function uploadOwn(file: File) {
    setError(null);
    setUploading(true);
    try {
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        setError("no project");
        return;
      }
      const dbId = parseInt(rfId, 10);
      const resp = await uploadImage(file, projectId, isNaN(dbId) ? undefined : dbId);
      persistMedia(resp.media_id, resp.aspect_ratio);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function uploadFromLink(url: string) {
    const trimmed = url.trim();
    if (!trimmed) return;
    setError(null);
    setUploading(true);
    try {
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        setError("no project");
        return;
      }
      const dbId = parseInt(rfId, 10);
      const resp = await uploadImageFromUrl(
        trimmed,
        projectId,
        isNaN(dbId) ? undefined : dbId,
      );
      persistMedia(resp.media_id, resp.aspect_ratio);
      setLinkMode(false);
      setLinkValue("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "link upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function uploadRef(file: File) {
    setError(null);
    try {
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        setError("no project");
        return;
      }
      const resp = await uploadImage(file, projectId);
      setRefMediaId(resp.media_id);
      setRefRefreshKey((k) => k + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ref upload failed");
    }
  }

  async function submitRefine() {
    if (!mediaId) return;
    if (!refinePrompt.trim()) return;
    await useGenerationStore.getState().refineImage(rfId, {
      prompt: refinePrompt.trim(),
      refMediaIds: refMediaId ? [refMediaId] : [],
    });
    setRefineOpen(false);
    setRefinePrompt("");
    setRefMediaId(null);
  }

  function openGenerate() {
    useGenerationStore.getState().openGenerationDialog(rfId, data.prompt ?? "");
  }

  if (!mediaId) {
    return (
      <div className="node-body node-body--visual-asset">
        <div
          className={`visual-asset__empty${isProcessing ? " visual-asset__empty--processing" : ""}`}
        >
          {isProcessing ? (
            <span className="visual-asset__hint">Generating…</span>
          ) : linkMode ? (
            <div className="visual-asset__link-row">
              <input
                type="url"
                className="visual-asset__link-input"
                placeholder="https://… (png/jpg/webp)"
                value={linkValue}
                onChange={(e) => setLinkValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") uploadFromLink(linkValue);
                  if (e.key === "Escape") {
                    setLinkMode(false);
                    setLinkValue("");
                    setError(null);
                  }
                }}
                disabled={uploading}
                autoFocus
              />
              <button
                type="button"
                className="visual-asset__action"
                onClick={() => uploadFromLink(linkValue)}
                disabled={uploading || !linkValue.trim()}
              >
                {uploading ? "Fetching…" : "Save"}
              </button>
              <button
                type="button"
                className="visual-asset__action"
                onClick={() => {
                  setLinkMode(false);
                  setLinkValue("");
                  setError(null);
                }}
                disabled={uploading}
              >
                ×
              </button>
            </div>
          ) : (
            <>
              <button
                type="button"
                className="visual-asset__action"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? "Uploading…" : "Upload"}
              </button>
              <button
                type="button"
                className="visual-asset__action"
                onClick={() => {
                  setError(null);
                  setLinkMode(true);
                }}
                disabled={uploading}
              >
                Add link
              </button>
              <button
                type="button"
                className="visual-asset__action"
                onClick={openGenerate}
                disabled={uploading}
              >
                Generate
              </button>
            </>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadOwn(f);
            e.target.value = "";
          }}
        />
        {error && <p className="visual-asset__error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="node-body node-body--visual-asset node-body--visual-asset-with-media">
      <div className="visual-asset__media">
        <img
          className="visual-asset__image"
          src={mediaUrl(mediaId)}
          alt={data.title}
        />
        {!isProcessing && (
          <button
            type="button"
            className="visual-asset__refine-btn"
            onClick={() => setRefineOpen((o) => !o)}
            aria-label="Refine image"
          >
            Refine
          </button>
        )}
      </div>
      <BriefHint data={data} />
      {!isProcessing && (
        <button
          type="button"
          className="visual-asset__action"
          onClick={(e) => {
            e.stopPropagation();
            saveTileToLibrary({
              mediaId,
              nodeType: data.type,
              data,
            });
          }}
          title="Save this asset to the library"
          aria-label="Save to library"
        >
          ★ Save
        </button>
      )}
      {refineOpen && (
        <div className="visual-asset__refine-panel" role="region" aria-label="Refine">
          <textarea
            className="visual-asset__refine-textarea"
            placeholder="Describe the change…"
            rows={2}
            value={refinePrompt}
            onChange={(e) => setRefinePrompt(e.target.value)}
          />
          <div className="visual-asset__refine-actions">
            <button
              type="button"
              className="visual-asset__refine-ref"
              onClick={() => refInputRef.current?.click()}
            >
              {refMediaId ? `Ref ✓ (${refRefreshKey})` : "Add ref"}
            </button>
            <button
              type="button"
              className="visual-asset__refine-submit"
              disabled={!refinePrompt.trim()}
              onClick={submitRefine}
            >
              Refine →
            </button>
          </div>
          <input
            ref={refInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadRef(f);
              e.target.value = "";
            }}
          />
        </div>
      )}
      {error && <p className="visual-asset__error">{error}</p>}
    </div>
  );
}

// Shared editable body for prompt + note nodes. Both store free-form text
// in `data.prompt`; only display markup differs. Double-click swaps to a
// textarea; blur or Cmd/Ctrl+Enter saves; Esc cancels.
function EditableTextBody({
  rfId,
  data,
  variant,
}: {
  rfId: string;
  data: FlowboardNodeData;
  variant: "prompt" | "note";
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.prompt ?? "");
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing) {
      setDraft(data.prompt ?? "");
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) {
          ta.focus();
          ta.setSelectionRange(ta.value.length, ta.value.length);
        }
      });
    }
  }, [editing]);

  function save() {
    const next = draft;
    if (next !== (data.prompt ?? "")) {
      useBoardStore.getState().updateNodeData(rfId, { prompt: next });
      const dbId = parseInt(rfId, 10);
      if (!isNaN(dbId)) {
        // Backend merges `data`, so only the prompt delta needs shipping.
        patchNode(dbId, { data: { prompt: next } }).catch(() => {});
      }
    }
    setEditing(false);
  }

  if (editing) {
    return (
      <div className={`node-body node-body--${variant} node-body--${variant}-edit`}>
        <textarea
          ref={taRef}
          className={`${variant}-editor`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setEditing(false);
            } else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              save();
            }
          }}
          placeholder={
            variant === "prompt"
              ? "Style direction (e.g. cinematic warm tone, magazine editorial mood). Connect into image/video to feed downstream auto-prompt."
              : "Note, TODO, label…"
          }
        />
      </div>
    );
  }

  const text = data.prompt ?? "";
  const placeholder =
    variant === "prompt"
      ? "Double-click to add direction…"
      : "Double-click to add note…";

  return (
    <div
      className={`node-body node-body--${variant}`}
      onDoubleClick={() => setEditing(true)}
      title="Double-click to edit"
    >
      {variant === "prompt" ? (
        <>
          <pre className="prompt-text">{text || placeholder}</pre>
          <FanOutButton rfId={rfId} text={text} />
        </>
      ) : (
        <p className="note-text">{text || placeholder}</p>
      )}
    </div>
  );
}

/** Give each line of this prompt its own generation node.
 *
 * `fan_out` has been a service and an HTTP route since P7 with nothing calling
 * it: the packaged tool's `prompt_list` node is how a five-scene script becomes
 * five clips, and on this canvas the only way to reach it was curl.
 *
 * Deliberately NOT part of running the board. Fanning out during a run would
 * make the cost dialog quote three dispatches and then spend twenty — the
 * count has to be visible before the board changes, which is what the confirm
 * below is for.
 */
function FanOutButton({ rfId, text }: { rfId: string; text: string }) {
  const [busy, setBusy] = useState(false);
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  const boardId = useBoardStore((s) => s.boardId);
  if (lines.length < 2 || boardId === null) return null;

  const dbId = parseInt(rfId, 10);
  if (isNaN(dbId)) return null;

  async function run(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try {
      const preview = await previewFanOut(boardId!, dbId);
      const message =
        `${preview.lines} dòng → tạo ${preview.willCreate} node mới` +
        (preview.willUpdate > 0 ? `, cập nhật ${preview.willUpdate}` : "") +
        (preview.willDelete > 0 ? `, xoá ${preview.willDelete}` : "") +
        ". Chưa chạy gì, chưa tốn credit. Tiếp tục?";
      if (!window.confirm(message)) return;
      await fanOutBoard(boardId!, dbId);
      await useBoardStore.getState().refreshBoardState();
    } catch {
      // The board is unchanged on failure; the next press says so again.
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className="prompt-fanout"
      disabled={busy}
      title="Mỗi dòng thành một node sinh riêng (bấm lại thì cập nhật, không nhân đôi)"
      onClick={(e) => void run(e)}
    >
      ⑃ Tách {lines.length} dòng
    </button>
  );
}

// ── Storyboard ────────────────────────────────────────────────────────────
// Storyboard is a thin image-node wrapper. It dispatches via the standard
// `gen_image` handler with a locked prompt template that asks Flow to render
// the user's topic as a single composite NxN grid (see
// frontend/src/lib/storyboardPrompt.ts). Rendering reuses `ImageBody` — up
// to 4 composite variants in the tile grid — with a small `2×2`/`2×3`/`2×4`
// corner badge (flipped for portrait composites) reminding the user of
// the active layout.

function StoryboardBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  // Show the concrete rows × cols (post-orientation flip), not the
  // user-picker key. So a node with grid="2x3" on a portrait composite
  // shows "3×2" — matches what Flow actually rendered.
  const g = normaliseStoryboardGrid(data.storyboardGrid);
  const { rows, cols } = resolveStoryboardLayout(g, data.aspectRatio);
  const label = `${rows}×${cols}`;
  return (
    <div className="storyboard-wrap">
      <span
        className="storyboard-grid-badge"
        title={`Composite layout: ${label} (${rows * cols} panels)`}
      >
        {label}
      </span>
      <ImageBody rfId={rfId} data={data} />
    </div>
  );
}

/** Settings worth showing on the face of a post-production card, in the
 *  order they matter. Everything else stays in `sourceSettings` — a card
 *  that lists forty ffmpeg flags is a wall, not a summary. */
const POSTPROD_SUMMARY_KEYS: [string, string][] = [
  ["engine", "Engine"],
  ["voice", "Giọng"],
  ["gemini_model", "Model"],
  ["analysis_mode", "Chế độ"],
  ["style", "Phong cách"],
  ["upscale_resolution", "Upscale"],
  ["video_speed", "Tốc độ"],
  ["effect", "Hiệu ứng"],
  ["zoom_speed", "Tốc độ zoom"],
  ["aspect_ratio", "Tỉ lệ"],
  ["enable_sub", "Phụ đề"],
  ["enable_bgm", "Nhạc nền"],
  ["bgm_volume", "Âm lượng nhạc"],
  ["mute_original_audio", "Tắt tiếng gốc"],
];

/** The only editable thing on a post-production card: where the CPU
 *  watermark pass paints when the detector declines or cannot run. The box
 *  arrived only inside imported workflow files until now, so the fallback was
 *  reachable over HTTP and nowhere else. */
function WatermarkFallback({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const settings = data.sourceSettings ?? {};

  function pick(key: string) {
    const next = withWatermarkCorner(settings, key);
    useBoardStore.getState().updateNodeData(rfId, { sourceSettings: next });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      patchNode(dbId, { data: { sourceSettings: next } }).catch(() => {});
    }
  }

  return (
    <label
      className="postprod-pick"
      title="Máy dò của bộ công cụ chạy trước. Khi nó không thấy gì hoặc không chạy được, MI-GAN (CPU) xoá đúng vùng góc này."
      onClick={(e) => e.stopPropagation()}
    >
      <span>Vùng dự phòng</span>
      <select
        value={watermarkCorner(settings)}
        onChange={(e) => pick(e.target.value)}
      >
        {WATERMARK_CORNERS.map((c) => (
          <option key={c.key} value={c.key}>
            {c.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The switch for the clip-review loop, on the node that produces the clip.
 *
 * `review_loop.settings_from` has read this off `merged_settings` all along and
 * the key was not in that function's allow-list, so the loop was off for every
 * node unconditionally and nothing in the app could turn it on — which also made
 * `estimate.reviewJobs` always count zero.
 *
 * Written into `sourceSettings` rather than onto `data`, because that is the
 * shape `merged_settings` reads first and the one an exported template carries.
 */
function ReviewLoopToggle({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const settings = data.sourceSettings ?? {};
  const on = Boolean(settings["review_loop"]);

  function toggle(next: boolean) {
    const merged = { ...settings, review_loop: next };
    useBoardStore.getState().updateNodeData(rfId, { sourceSettings: merged });
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      patchNode(dbId, { data: { sourceSettings: merged } }).catch(() => {});
    }
  }

  return (
    <label
      className="postprod-pick"
      title={
        "Sau khi clip xong, chấm điểm bằng AI rồi tự sinh lại nếu chưa đạt. "
        + "Mỗi lượt chấm tốn quota AI của bạn, và mỗi lần sinh lại tốn credit "
        + "Flow — trừ khi làn đang chạy là làn 0 credit. Hộp thoại Chạy hiện số "
        + "lượt chấm trước khi bắt đầu."
      }
      onClick={(e) => e.stopPropagation()}
    >
      <span>Tự chấm &amp; sửa clip</span>
      <input
        type="checkbox"
        checked={on}
        onChange={(e) => toggle(e.target.checked)}
      />
    </label>
  );
}

/** Read the failed narration segments again — the finished ones stay bought.
 *
 * Only on a `create_voice` node, and only once one has run: the control asks the
 * backend which segments are holes, and there is nothing to ask about before the
 * first read. The preview is fetched rather than derived here because the answer
 * depends on a record on disk that only the agent can see.
 *
 * The button says what it will spend BEFORE it spends it. That is the whole point
 * — the capability it restores is "one failed segment costs one segment", and a
 * user cannot tell that from a button labelled "retry".
 */
function NarrationReread({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const boardId = useBoardStore((s) => s.boardId);
  const [preview, setPreview] = useState<RereadPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const dbId = parseInt(rfId, 10);

  // Only worth asking once the node has read something. `narrate_failed_segments`
  // is the error the op leaves when it left holes, and `mediaId` covers the case
  // where an earlier attempt finished — a later edit can still leave holes.
  const ranBefore =
    Boolean(data.mediaId) || Boolean(data.error?.startsWith("narrate_failed_segments"));

  useEffect(() => {
    if (boardId === null || isNaN(dbId) || !ranBefore) return;
    let live = true;
    narrationSegments(boardId, dbId)
      .then((p) => live && setPreview(p))
      // A 409 here is the ordinary case: nothing to re-read, no record, script
      // changed. The control simply does not appear, which is the honest answer.
      .catch(() => live && setPreview(null));
    return () => {
      live = false;
    };
  }, [boardId, dbId, ranBefore, data.error, data.mediaId]);

  if (!hasHoles(preview) || preview === null) return null;

  async function run() {
    if (boardId === null || busy) return;
    setBusy(true);
    setNote(null);
    try {
      const out = await rereadNarration(boardId, dbId);
      setNote(`Đã đọc lại ${out.reread.length} đoạn, giữ ${out.kept} đoạn.`);
      setPreview(null);
      await useBoardStore.getState().refreshBoardState();
    } catch (e) {
      setNote(e instanceof Error ? errorLabel(e.message) : "Không đọc lại được.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="narration-reread" onClick={(e) => e.stopPropagation()}>
      <p className="narration-reread__note">{rereadNote(preview)}</p>
      <ul className="narration-reread__rows">
        {preview.segments.slice(0, 8).map((seg) => (
          <li key={seg.index} title={seg.preview}>
            {segmentLabel(seg)}
          </li>
        ))}
      </ul>
      <button type="button" disabled={busy} onClick={() => void run()}>
        {busy ? "Đang đọc lại…" : `Đọc lại ${preview.read.length} đoạn lỗi`}
      </button>
      {note && <p className="narration-reread__msg">{note}</p>}
    </div>
  );
}


function PostprodBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  const settings = data.sourceSettings ?? {};
  const rows = POSTPROD_SUMMARY_KEYS.filter(([k]) => {
    const v = settings[k];
    return v !== undefined && v !== null && v !== "";
  }).slice(0, 5);

  return (
    <div className="postprod-body">
      {data.mediaId && (
        <div className="postprod-out">✓ đã có kết quả</div>
      )}
      {data.type === "remove_watermark" && (
        <WatermarkFallback rfId={rfId} data={data} />
      )}
      {data.type === "create_voice" && (
        <NarrationReread rfId={rfId} data={data} />
      )}
      {rows.length === 0 ? (
        <div className="postprod-empty">
          Chưa cấu hình — nối video vào rồi bấm chạy.
        </div>
      ) : (
        <dl className="postprod-settings">
          {rows.map(([key, label]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd>{String(settings[key])}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function NodeBody({ rfId, data }: { rfId: string; data: FlowboardNodeData }) {
  return (
    <>
      {bodyFor(rfId, data)}
      {/* One place, because every type passes through here. `note` is excluded:
          it never dispatches, so it has no failure of its own to report. */}
      {data.type !== "note" && <NodeErrorLine data={data} />}
    </>
  );
}

function bodyFor(rfId: string, data: FlowboardNodeData) {
  switch (data.type) {
    case "character":
      return <CharacterBody rfId={rfId} data={data} />;
    case "image":
      return <ImageBody rfId={rfId} data={data} />;
    case "video":
      return <VideoBody rfId={rfId} data={data} />;
    case "prompt":
      return <EditableTextBody rfId={rfId} data={data} variant="prompt" />;
    case "note":
      return <EditableTextBody rfId={rfId} data={data} variant="note" />;
    case "visual_asset":
      return <VisualAssetBody rfId={rfId} data={data} />;
    case "Storyboard":
      return <StoryboardBody rfId={rfId} data={data} />;
    default:
      // Post-production nodes. They carry settings, not a prompt, so the
      // card shows what the step is configured to do rather than an empty
      // preview frame.
      return <PostprodBody rfId={rfId} data={data} />;
  }
}

function downloadExt(type: string): string {
  if (type === "video") return "mp4";
  return "png";
}

export function NodeCard(props: NodeProps<FlowNode>) {
  const data = props.data;
  const isNote = data.type === "note";
  const isGenerable = ["image", "prompt", "video", "visual_asset", "character", "Storyboard"].includes(data.type);
  const isRunning = data.status === "running";
  const llmBusy = isLLMBusy(data);
  const downloadable = !!data.mediaId && data.type !== "prompt" && data.type !== "note";

  function handleGenerate(e: React.MouseEvent) {
    e.stopPropagation();
    if (llmBusy) return; // guard: backend still composing for this node
    useGenerationStore.getState().openGenerationDialog(props.id, data.prompt ?? "");
  }

  function handleDownload(e: React.MouseEvent) {
    e.stopPropagation();
    // Download every variant, not just the first. `mediaIds` is the full
    // list — `mediaId` is just the active variant — so a 4-variant image
    // node was previously losing 3 of its 4 outputs. Filter out null
    // placeholders that the partial-batch path may leave in `mediaIds`.
    const rawIds =
      data.mediaIds && data.mediaIds.length > 0
        ? data.mediaIds
        : data.mediaId
          ? [data.mediaId]
          : [];
    const ids = rawIds.filter((m): m is string => typeof m === "string" && m.length > 0);
    if (ids.length === 0) return;
    const safeTitle = (data.title || data.type).replace(/[^A-Za-z0-9_-]+/g, "_");
    const ext = downloadExt(data.type);
    // `<a download>` only honours the suggested filename when the resource
    // is same-origin — `/media/<id>` *is* same-origin (proxied by FastAPI),
    // so the title-based filename sticks.
    ids.forEach((mid, i) => {
      const a = document.createElement("a");
      a.href = mediaUrl(mid);
      const suffix = ids.length > 1 ? `-${i + 1}` : "";
      a.download = `${safeTitle}-${data.shortId}${suffix}.${ext}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    });
  }

  return (
    <div
      className={`node-card${isNote ? " node-card--note" : ""}${
        props.selected ? " node-card--selected" : ""
      }${llmBusy ? " node-card--llm-busy" : ""}`}
    >
      <StatusStrip status={data.status} />
      <Handle type="target" position={Position.Left} className="node-handle" />

      <div className="node-header">
        <span className="node-icon" aria-hidden="true">{ICON[data.type] ?? "□"}</span>
        <span className="node-title">{data.title}</span>
        {llmBusy && (
          // Compact pill so the busy state reads at a glance even if the
          // body is collapsed. Title is contextual: composing vs. analysing.
          <span className="node-header__llm-pill" aria-live="polite">
            <span className="node-header__llm-spinner" aria-hidden="true" />
            {data.autoPromptStatus === "pending" ? "Composing…" : "Analyzing…"}
          </span>
        )}
        <div className="node-header__actions">
          {downloadable && (
            <button
              className="node-header__btn"
              onClick={handleDownload}
              aria-label="Download media"
              title="Download"
              tabIndex={0}
            >
              ⬇
            </button>
          )}
          {isGenerable && (
            <button
              className={`node-header__btn${isRunning ? " node-header__btn--running" : ""}`}
              onClick={handleGenerate}
              aria-label="Generate from this node"
              title={llmBusy ? "Backend is still composing — try again in a moment" : "Generate"}
              tabIndex={0}
              disabled={llmBusy}
            >
              ▶
            </button>
          )}
        </div>
        <span className="node-short-id">#{data.shortId}</span>
      </div>

      <NodeBody rfId={props.id} data={data} />

      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}
