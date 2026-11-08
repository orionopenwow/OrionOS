/**
 * The Orion mark (Art Update §6): the black hole as a circle with its photon ring, bright on one side, and
 * Orion's belt drifting toward its edge.
 *
 * art-core is making the real file at `os/theme/brand/mark.svg`. This component picks it up automatically
 * once it exists (the glob below is empty until then) and otherwise draws a simple inline stand-in.
 * Everything that shows the mark in the HUD goes through this one component, so swapping is one place.
 */
const brandMark = import.meta.glob('../../../../../os/theme/brand/mark.svg', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;
const MARK_URL: string | undefined = Object.values(brandMark)[0];

export default function OrionMark({ size = 24, className = '' }: { size?: number; className?: string }) {
  if (MARK_URL) {
    return <img src={MARK_URL} width={size} height={size} alt="" aria-hidden="true" className={className} draggable={false} />;
  }
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" className={className}>
      <defs>
        <linearGradient id="orion-mark-ring" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#2E6BFF" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#8DB0FF" />
          <stop offset="100%" stopColor="#DCE7FF" />
        </linearGradient>
        <radialGradient id="orion-mark-glow" cx="50%" cy="50%" r="50%">
          <stop offset="55%" stopColor="#2E6BFF" stopOpacity="0" />
          <stop offset="80%" stopColor="#2E6BFF" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#2E6BFF" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="15" cy="17" r="13" fill="url(#orion-mark-glow)" />
      <circle cx="15" cy="17" r="8.2" fill="#000" />
      <circle cx="15" cy="17" r="8.6" fill="none" stroke="url(#orion-mark-ring)" strokeWidth="2.1" />
      {/* Orion's belt drifting toward the edge */}
      <circle cx="21.6" cy="7.4" r="1.15" fill="#DDE5F0" />
      <circle cx="25.2" cy="5.6" r="1.25" fill="#DDE5F0" />
      <circle cx="28.8" cy="3.6" r="1.1" fill="#7FF2FF" />
    </svg>
  );
}
