/** True when a key event comes from a field the user is typing in. */
export function isTypingTarget(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

/**
 * First Escape inside a text field just leaves the field (so an in-progress reply or edit is not
 * lost by the panel closing); the next Escape closes panels as usual.
 */
export function escapeLeavesField(e: KeyboardEvent): boolean {
  if (e.key !== 'Escape' || !isTypingTarget(e)) return false;
  (e.target as HTMLElement).blur();
  return true;
}
