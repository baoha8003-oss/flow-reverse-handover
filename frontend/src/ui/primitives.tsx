import type { ReactNode, SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** Shared UI primitives for the tool tabs, styled to the packaged app's
 * look (dark surfaces, purple selects, blue CTA, red stop). */

export function Card({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-(--radius-card) border border-line bg-surface p-4",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function LabeledSelect({
  label,
  value,
  onChange,
  options,
  className,
  hint,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
  /** One line under the control. `NumberField` and `TextField` already take
   *  one; a select could not, so a caveat about which lane honours a value had
   *  nowhere to go except into every option label. */
  hint?: string;
} & Pick<SelectHTMLAttributes<HTMLSelectElement>, "disabled">) {
  return (
    <label className={cn("flex flex-col gap-1", className)}>
      <span className="text-[12px] text-ink-mute">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        // Forwarded, not just declared: a caller passing `disabled` and
        // getting a live control back is worse than no prop at all.
        disabled={disabled}
        className="h-9 rounded-(--radius-ctl) border border-grape-deep bg-grape px-3 text-[13px] font-medium text-white disabled:opacity-70"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && <span className="text-[11px] text-ink-dim">{hint}</span>}
    </label>
  );
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  loading,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading}
      className={cn(
        "h-9 rounded-(--radius-ctl) bg-gradient-to-r from-primary to-[#38bdf8] px-4 text-[13px] font-semibold text-white",
        "hover:brightness-110 disabled:opacity-50",
      )}
    >
      {children}
    </button>
  );
}

export function StopButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "h-9 rounded-(--radius-ctl) bg-gradient-to-r from-danger to-[#fb923c] px-4 text-[13px] font-semibold text-white",
        "hover:brightness-110 disabled:opacity-40",
      )}
    >
      {children}
    </button>
  );
}

export function GhostButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-9 rounded-(--radius-ctl) border border-line bg-card px-3 text-[13px] text-ink hover:bg-elevated"
    >
      {children}
    </button>
  );
}
