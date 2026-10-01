/** "Tên" / "SĐT:" chips from the packaged app's header.
 *
 * The original ships a real person's name and phone number in
 * branding_state.json — that is someone else's PII and is NOT migrated.
 * These render blank until a local settings screen lets the user fill in
 * their own, so the chrome matches the screenshot without carrying the
 * previous owner's details. */
export function BrandingChips() {
  return (
    <div className="hidden items-center gap-2 text-[12px] text-ink-mute md:flex">
      <span className="rounded-md bg-card px-2.5 py-1">
        Tên: <span className="text-ink-dim">—</span>
      </span>
      <span className="rounded-md bg-card px-2.5 py-1">
        SĐT: <span className="text-ink-dim">—</span>
      </span>
    </div>
  );
}
