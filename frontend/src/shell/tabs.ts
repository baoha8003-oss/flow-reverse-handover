/** The tool's navigation model, faithful to the packaged app's screenshot.
 *
 * Top pills run left→right across the TopBar. The VEO3 pill hosts the
 * sidebar of tool tabs (grouped VIDEO TOOLS / IMAGE TOOLS / SYSTEM); the
 * other pills are their own full-width views.
 *
 * Vietnamese labels and subtitles are copied byte-for-byte from the
 * screenshot the user is matching. Do not translate or reword them — see
 * docs/ui-spec.md for the label contract. Icons/colors approximate the
 * screenshot's gradient squares and can be refined without touching wiring.
 */

export type TopPillId =
  | "veo3"
  | "grok"
  | "nhat-ky"
  | "workflow"
  | "ung-ho";

export interface TopPill {
  id: TopPillId;
  label: string;
  icon: string;
}

export const TOP_PILLS: TopPill[] = [
  { id: "veo3", label: "VEO3", icon: "☀" },
  { id: "grok", label: "GROK", icon: "⚡" },
  { id: "nhat-ky", label: "NHẬT KÝ", icon: "📕" },
  { id: "workflow", label: "WORKFLOW", icon: "🔗" },
  { id: "ung-ho", label: "ỦNG HỘ TÁC GIẢ", icon: "❤️" },
];

export type SidebarGroup = "VIDEO TOOLS" | "IMAGE TOOLS" | "SYSTEM";

export type ToolTabId =
  | "text-to-video"
  | "image-to-video"
  | "video-start-end"
  | "character-sync"
  | "idea-to-video"
  | "analyze-video"
  | "subtitle-logo"
  | "text-to-image"
  | "image-to-image"
  | "affiliate"
  | "upscale-image"
  | "upscale-video"
  | "settings"
  | "cut-merge"
  | "video-clone"
  | "guide";

export interface ToolTab {
  id: ToolTabId;
  group: SidebarGroup;
  title: string;
  subtitle: string;
  /** Emoji shown in the sidebar icon square + the tool header. */
  icon: string;
  /** Two hues for the icon square's gradient (from → to). */
  from: string;
  to: string;
}

export const TOOL_TABS: ToolTab[] = [
  // VIDEO TOOLS — the seven items visible in the screenshot's sidebar.
  {
    id: "text-to-video",
    group: "VIDEO TOOLS",
    title: "Text to Video",
    subtitle: "Tạo video từ prompt",
    icon: "🎬",
    from: "#7c3aed",
    to: "#4f46e5",
  },
  {
    id: "image-to-video",
    group: "VIDEO TOOLS",
    title: "Image to Video",
    subtitle: "Tạo video từ ảnh",
    icon: "🖼",
    from: "#0ea5e9",
    to: "#2563eb",
  },
  {
    id: "video-start-end",
    group: "VIDEO TOOLS",
    title: "Video Start-End",
    subtitle: "Làm video đầu - cuối",
    icon: "🎞",
    from: "#6366f1",
    to: "#8b5cf6",
  },
  {
    id: "character-sync",
    group: "VIDEO TOOLS",
    title: "Character Sync",
    subtitle: "Đồng nhất nhân vật",
    icon: "👤",
    from: "#f59e0b",
    to: "#d97706",
  },
  {
    id: "idea-to-video",
    group: "VIDEO TOOLS",
    title: "Idea to Video",
    subtitle: "Biến ý tưởng thành video",
    icon: "💡",
    from: "#10b981",
    to: "#059669",
  },
  {
    id: "analyze-video",
    group: "VIDEO TOOLS",
    title: "Phân tích video",
    subtitle: "Phân tích & lấy prompt video",
    icon: "📊",
    from: "#ef4444",
    to: "#b91c1c",
  },
  {
    id: "subtitle-logo",
    group: "VIDEO TOOLS",
    title: "Phụ Đề & Xóa Logo",
    subtitle: "Tạo phụ đề & xóa watermark",
    icon: "📝",
    from: "#ec4899",
    to: "#db2777",
  },
  // IMAGE TOOLS
  {
    id: "text-to-image",
    group: "IMAGE TOOLS",
    title: "Text to Image",
    subtitle: "Tạo ảnh từ prompt",
    icon: "🎨",
    from: "#ec4899",
    to: "#8b5cf6",
  },
  {
    id: "image-to-image",
    group: "IMAGE TOOLS",
    title: "Image to Image",
    subtitle: "Tạo ảnh từ ảnh tham chiếu",
    icon: "🖌",
    from: "#f97316",
    to: "#ea580c",
  },
  {
    id: "affiliate",
    group: "IMAGE TOOLS",
    title: "Affiliate Sản Phẩm",
    subtitle: "Tạo ảnh quảng cáo sản phẩm",
    icon: "🛍",
    from: "#14b8a6",
    to: "#0d9488",
  },
  {
    id: "upscale-image",
    group: "IMAGE TOOLS",
    title: "UP SCALE IMAGE 4K",
    subtitle: "Nâng cấp chất lượng ảnh",
    icon: "🔍",
    from: "#3b82f6",
    to: "#0ea5e9",
  },
  {
    id: "upscale-video",
    group: "IMAGE TOOLS",
    title: "Upscale Video",
    subtitle: "Nâng chất lượng video (local)",
    icon: "🎥",
    from: "#6366f1",
    to: "#4338ca",
  },
  // SYSTEM
  {
    id: "settings",
    group: "SYSTEM",
    title: "Settings",
    subtitle: "Cài đặt hệ thống",
    icon: "⚙️",
    from: "#64748b",
    to: "#475569",
  },
  {
    id: "cut-merge",
    group: "SYSTEM",
    title: "Cut & Merge Video",
    subtitle: "Cắt, ghép, lồng tiếng, nhạc nền",
    icon: "✂️",
    from: "#0ea5e9",
    to: "#6366f1",
  },
  {
    id: "video-clone",
    group: "SYSTEM",
    title: "Video Clone",
    subtitle: "Tạo lại video từ link",
    icon: "🧬",
    from: "#a855f7",
    to: "#7c3aed",
  },
  {
    id: "guide",
    group: "SYSTEM",
    title: "Hướng dẫn",
    subtitle: "Cách dùng tool",
    icon: "📖",
    from: "#22d3ee",
    to: "#0891b2",
  },
];

export const SIDEBAR_GROUPS: SidebarGroup[] = [
  "VIDEO TOOLS",
  "IMAGE TOOLS",
  "SYSTEM",
];

export function tabsInGroup(group: SidebarGroup): ToolTab[] {
  return TOOL_TABS.filter((t) => t.group === group);
}

export function findTab(id: ToolTabId): ToolTab | undefined {
  return TOOL_TABS.find((t) => t.id === id);
}
