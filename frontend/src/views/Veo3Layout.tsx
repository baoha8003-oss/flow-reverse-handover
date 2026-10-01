import { useEffect, useState } from "react";
import { useShellStore } from "@/store/shell";
import { Veo3Sidebar } from "@/shell/Veo3Sidebar";
import { ToolHeader } from "@/shell/ToolHeader";
import { findTab } from "@/shell/tabs";
import { TextToVideoTab } from "@/features/text-to-video/TextToVideoTab";
import { TextToImageTab } from "@/features/text-to-image/TextToImageTab";
import { ImageToVideoTab } from "@/features/image-to-video/ImageToVideoTab";
import { ImageToImageTab } from "@/features/image-to-image/ImageToImageTab";
import { VideoStartEndTab } from "@/features/video-start-end/VideoStartEndTab";
import { SettingsTab } from "@/features/settings/SettingsTab";
import { CutMergeTab } from "@/features/cut-merge/CutMergeTab";
import { UpscaleTab } from "@/features/upscale/UpscaleTab";
import { CharacterSyncTab } from "@/features/character-sync/CharacterSyncTab";
import { GuideTab } from "@/features/guide/GuideTab";
import { IdeaToVideoTab } from "@/features/idea-to-video/IdeaToVideoTab";
import { AnalyzeVideoTab } from "@/features/analyze-video/AnalyzeVideoTab";
import { SubtitleLogoTab } from "@/features/subtitle-logo/SubtitleLogoTab";
import { VideoCloneTab } from "@/features/video-clone/VideoCloneTab";
import { AffiliateTab } from "@/features/affiliate/AffiliateTab";

/** Tabs with a real implementation. Every tab in `tabs.ts` is currently on
 * this list, so the placeholder below is unreachable — it stays as the guard
 * for the next tab someone adds to the sidebar before wiring it up here. */
const BUILT_TABS: string[] = [
  "text-to-video",
  "text-to-image",
  "image-to-video",
  "image-to-image",
  "video-start-end",
  "character-sync",
  "idea-to-video",
  "analyze-video",
  "subtitle-logo",
  "affiliate",
  "video-clone",
  "settings",
  "cut-merge",
  "upscale-image",
  "upscale-video",
  "guide",
];

/** The VEO3 pill's body: the tool sidebar plus the active tab's panel.
 *
 * Built tabs stay mounted and are hidden when inactive — a 200-line prompt
 * list and the control selections live in component state, and unmounting
 * threw them away the moment the user looked at another tab. */
/** Tabs that cost something to mount: each fetches the post-production
 * library and renders <video> thumbnails. Mounting all of them up front would
 * fire those requests before the user ever opens one, so they mount on first
 * visit and stay mounted afterwards — keeping typed subtitle/narration text
 * across tab switches the way the built generation tabs keep their prompts. */
const LAZY_TABS: string[] = [
  "settings",
  "cut-merge",
  "upscale-image",
  "upscale-video",
  "analyze-video",
  "subtitle-logo",
  "video-clone",
  "guide",
];

export function Veo3Layout() {
  const tabId = useShellStore((s) => s.tab);
  const tab = findTab(tabId);
  const isBuilt = BUILT_TABS.includes(tabId);
  const [visited, setVisited] = useState<Set<string>>(
    () => new Set(LAZY_TABS.includes(tabId) ? [tabId] : []),
  );

  useEffect(() => {
    if (!LAZY_TABS.includes(tabId) || visited.has(tabId)) return;
    setVisited((prev) => new Set(prev).add(tabId));
  }, [tabId, visited]);

  return (
    <div className="flex min-h-0 flex-1">
      <Veo3Sidebar />
      <main className="min-w-0 flex-1 overflow-y-auto px-5 py-4">
        {tab && (
          <div className="mx-auto max-w-[1180px]">
            <ToolHeader tab={tab} />
            <div hidden={tabId !== "text-to-video"}>
              <TextToVideoTab />
            </div>
            <div hidden={tabId !== "text-to-image"}>
              <TextToImageTab />
            </div>
            <div hidden={tabId !== "image-to-video"}>
              <ImageToVideoTab />
            </div>
            <div hidden={tabId !== "image-to-image"}>
              <ImageToImageTab />
            </div>
            <div hidden={tabId !== "video-start-end"}>
              <VideoStartEndTab />
            </div>
            <div hidden={tabId !== "character-sync"}>
              <CharacterSyncTab />
            </div>
            <div hidden={tabId !== "idea-to-video"}>
              <IdeaToVideoTab />
            </div>
            {visited.has("guide") && (
              <div hidden={tabId !== "guide"}>
                <GuideTab />
              </div>
            )}
            <div hidden={tabId !== "affiliate"}>
              <AffiliateTab />
            </div>
            {visited.has("video-clone") && (
              <div hidden={tabId !== "video-clone"}>
                <VideoCloneTab />
              </div>
            )}
            {visited.has("analyze-video") && (
              <div hidden={tabId !== "analyze-video"}>
                <AnalyzeVideoTab />
              </div>
            )}
            {visited.has("subtitle-logo") && (
              <div hidden={tabId !== "subtitle-logo"}>
                <SubtitleLogoTab />
              </div>
            )}
            {visited.has("settings") && (
              <div hidden={tabId !== "settings"}>
                <SettingsTab />
              </div>
            )}
            {visited.has("cut-merge") && (
              <div hidden={tabId !== "cut-merge"}>
                <CutMergeTab />
              </div>
            )}
            {visited.has("upscale-image") && (
              <div hidden={tabId !== "upscale-image"}>
                <UpscaleTab kind="image" />
              </div>
            )}
            {visited.has("upscale-video") && (
              <div hidden={tabId !== "upscale-video"}>
                <UpscaleTab kind="video" />
              </div>
            )}
            {!isBuilt && (
              <div className="rounded-(--radius-card) border border-line bg-surface p-8 text-center text-ink-mute">
                <p className="text-[14px]">
                  Tab{" "}
                  <span className="font-semibold text-ink">{tab.title}</span> sẽ
                  được dựng ở phase sau.
                </p>
                <p className="mt-1 text-[12px] text-ink-dim">{tab.subtitle}</p>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
