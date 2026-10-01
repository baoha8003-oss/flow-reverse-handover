import { Component, type ErrorInfo, type ReactNode } from "react";

/** Last line of defence for the whole app.
 *
 * Backend rows arrive as unvalidated JSON and are cast to typed DTOs, so a
 * field the UI indexes on (a status enum, say) can be a value no one
 * anticipated. Without a boundary, one such row blanks the entire window and
 * takes any unsent prompt text with it. */
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("unhandled UI error", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-bg p-8 text-center font-sans text-ink">
        <div className="text-[15px] font-semibold">Giao diện gặp lỗi</div>
        <div className="max-w-[640px] text-[13px] text-ink-mute">
          {error.message}
        </div>
        <p className="text-[12px] text-ink-dim">
          Các job đang chạy vẫn tiếp tục ở agent — tải lại trang sẽ hiện lại
          chúng.
        </p>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="h-9 rounded-(--radius-ctl) border border-line bg-card px-4 text-[13px] text-ink hover:bg-elevated"
        >
          Thử hiển thị lại
        </button>
      </div>
    );
  }
}
