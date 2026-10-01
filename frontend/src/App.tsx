import { AppShell } from "./shell/AppShell";
import { Toaster } from "./components/Toaster";
import { GenerationDialog } from "./components/GenerationDialog";
import { ResultViewer } from "./components/ResultViewer";
import { ErrorBoundary } from "./components/ErrorBoundary";

export function App() {
  return (
    <ErrorBoundary>
      <AppShell />
      {/* Global overlays — persist across pill/tab switches. The dialog and
          viewer serve the canvas (WORKFLOW pill); the tabs use store/jobs.

          No forced AI-provider gate. It made sense when this app was only a
          canvas, where every action ran through an LLM. The generation tabs
          don't use one at all, so an undismissable dialog at boot locks the
          user out of the whole tool over a dependency their task never
          needs — and it did exactly that when the CLI it defaulted to
          couldn't sign in. The LLM paths still fail loudly with a clear
          message, and the canvas toolbar's "Setup AI" button is the way in
          when someone actually wants those features. */}
      <Toaster />
      <GenerationDialog />
      <ResultViewer />
    </ErrorBoundary>
  );
}
