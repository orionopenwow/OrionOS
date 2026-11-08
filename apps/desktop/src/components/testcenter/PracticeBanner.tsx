/**
 * Practice banner for the Test Center's "screen change shows a preview first" test.
 * The Test Center proposes changing only the colour below; you decide whether it is applied.
 */
export const PRACTICE_COLOUR = '#2E6BFF';

export default function PracticeBanner() {
  return (
    <div className="rounded-xl px-3 py-2 text-[12px] font-semibold text-white"
      style={{ background: `${PRACTICE_COLOUR}33`, border: `1px solid ${PRACTICE_COLOUR}` }}>
      Practice banner: a screen change test can recolour this.
    </div>
  );
}
