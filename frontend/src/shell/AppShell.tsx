import { useEffect, useState } from "react";
import { useShellStore } from "@/store/shell";
import { TopBar } from "./TopBar";
import { StatusBar } from "./StatusBar";
import { Veo3Layout } from "@/views/Veo3Layout";
import { WorkflowCanvasView } from "@/views/WorkflowCanvasView";
import { GrokView, SponsorView } from "@/views/SimpleViews";
import { ActivityView } from "@/views/ActivityView";

/** Top-level chrome: the pill bar, the active pill's body, and the status
 * bar. Global overlays (dialogs, toaster, setup gate) are mounted by App
 * so they persist across pill switches.
 *
 * The stateful pills are hidden rather than unmounted: unmounting threw away
 * typed prompts and control selections on every switch, and remounted the
 * canvas — refetching the board and resetting its viewport — each time the
 * user glanced elsewhere. The canvas is mounted lazily on its first open,
 * because React Flow measures its container and would size itself against a
 * hidden (zero-height) one; after that it stays mounted. */
export function AppShell() {
  const pill = useShellStore((s) => s.pill);
  const [canvasMounted, setCanvasMounted] = useState(pill === "workflow");

  useEffect(() => {
    if (pill === "workflow") setCanvasMounted(true);
  }, [pill]);

  return (
    <div className="flex h-screen flex-col bg-bg font-sans text-ink">
      <TopBar />
      <div className="flex min-h-0 flex-1 flex-col">
        <div hidden={pill !== "veo3"} className="flex min-h-0 flex-1 flex-col">
          <Veo3Layout />
        </div>
        {canvasMounted && (
          <div
            hidden={pill !== "workflow"}
            className="flex min-h-0 flex-1 flex-col"
          >
            <WorkflowCanvasView />
          </div>
        )}
        {pill === "grok" && <GrokView />}
        {pill === "nhat-ky" && <ActivityView />}
        {pill === "ung-ho" && <SponsorView />}
      </div>
      <StatusBar />
    </div>
  );
}
