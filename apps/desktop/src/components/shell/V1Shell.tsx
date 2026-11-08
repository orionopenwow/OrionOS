/**
 * OrionOS 1.0 gaps in the HUD, mounted with one line in App.tsx: Guide Mode (step card + ring) and the privacy
 * dashboard window. Open them from the HUD's Settings window, or with window events:
 *   window.dispatchEvent(new Event('orion:open-guide'))     -> "Show me how to…"
 *   window.dispatchEvent(new Event('orion:open-privacy'))   -> the privacy dashboard
 */
import { useEffect, useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import GuideMode, { OPEN_GUIDE_EVENT } from './GuideMode';
import { onOrionOpen } from '../../lib/menu';
import PrivacyDashboard, { OPEN_PRIVACY_EVENT } from './PrivacyDashboard';
import GamesWindow from './GamesWindow'; // [games]
import VoiceWindow from '../voice/VoiceWindow'; // [voice2] Orion's voice + first-time prompt

export default function V1Shell({ mobile }: { mobile?: boolean }) {
  const [privacy, setPrivacy] = useState(false);
  const [games, setGames] = useState(false); // [games]

  useEffect(() => {
    const open = () => setPrivacy(true);
    window.addEventListener(OPEN_PRIVACY_EVENT, open);
    return () => window.removeEventListener(OPEN_PRIVACY_EVENT, open);
  }, []);
  // [simple-menu] the menu's "privacy" and "guide" items
  useEffect(() => onOrionOpen('privacy', () => setPrivacy(true)), []);
  useEffect(() => onOrionOpen('games', () => setGames(true)), []); // [games]
  useEffect(() => onOrionOpen('guide', () => window.dispatchEvent(new Event(OPEN_GUIDE_EVENT))), []);

  return (
    <>
      <GuideMode />
      <VoiceWindow mobile={mobile} /> {/* [voice2] */}
      <AnimatePresence>
        {privacy && <PrivacyDashboard key="privacy" mobile={mobile} onClose={() => setPrivacy(false)} />}
        {games && <GamesWindow key="games" mobile={mobile} onClose={() => setGames(false)} />}
      </AnimatePresence>
    </>
  );
}
