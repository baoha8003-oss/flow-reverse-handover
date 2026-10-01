import { useReactFlow } from "@xyflow/react";
import { useBoardStore } from "../store/board";
import { useCanvasUiStore } from "../store/canvasUi";
import type { NodeType } from "../store/board";

interface Chip {
  type: NodeType;
  icon: string;
  label: string;
}

const CHIPS: Chip[] = [
  { type: "character", icon: "◎", label: "Character" },
  { type: "image", icon: "▣", label: "Image" },
  { type: "Storyboard", icon: "▦", label: "Storyboard" },
  { type: "video", icon: "▶", label: "Video" },
  { type: "visual_asset", icon: "◇", label: "Visual asset" },
  { type: "prompt", icon: "✦", label: "Prompt" },
  { type: "note", icon: "✎", label: "Note" },
  // Post-production — local ffmpeg, no credits.
  { type: "analyze_video", icon: "◱", label: "Phân tích video" },
  { type: "merge_video", icon: "⧉", label: "Ghép video" },
  { type: "edit_video", icon: "✂", label: "Sửa video" },
  { type: "extract_last_frame", icon: "⧗", label: "Frame cuối" },
  { type: "add_bgm", icon: "♪", label: "Nhạc nền" },
  { type: "create_voice", icon: "🗣", label: "Giọng đọc" },
  { type: "align_video_voice", icon: "⇔", label: "Khớp giọng" },
  { type: "sync_image_voice", icon: "◉", label: "Ảnh + giọng" },
  { type: "remove_watermark", icon: "⌫", label: "Xoá watermark" },
  { type: "review_video", icon: "★", label: "Chấm điểm clip" },
];

export function AddNodePalette() {
  const { screenToFlowPosition } = useReactFlow();
  const addNodeOfType = useBoardStore((s) => s.addNodeOfType);
  const collapsed = useCanvasUiStore((s) => s.paletteCollapsed);
  const toggle = useCanvasUiStore((s) => s.togglePalette);

  function handleAdd(type: NodeType) {
    const position = screenToFlowPosition({
      x: window.innerWidth / 2,
      y: window.innerHeight / 2,
    });
    addNodeOfType(type, position);
  }

  return (
    <div
      className={`add-node-palette${collapsed ? " add-node-palette--collapsed" : ""}`}
      aria-label="Add node"
    >
      {/* Seventeen chips is a wide strip across the bottom of the canvas.
          Collapsing keeps the affordance visible — the chips come back with
          one click — rather than removing the only way to add a node. */}
      <button
        type="button"
        className="add-node-plus"
        onClick={toggle}
        aria-expanded={!collapsed}
        title={collapsed ? "Mở thư viện node" : "Thu gọn thư viện node"}
      >
        {collapsed ? "+" : "−"}
      </button>
      {!collapsed && CHIPS.map((chip) => (
        <button
          key={chip.type}
          className="add-node-chip"
          aria-label={`Add ${chip.label} node`}
          onClick={() => handleAdd(chip.type)}
        >
          <span aria-hidden="true">{chip.icon}</span>
          {chip.label}
        </button>
      ))}
    </div>
  );
}
