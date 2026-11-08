/**
 * Accessibility for the Orion window (complete plan step 6): high contrast, bigger text and zoom.
 * The choices come from Settings > Accessibility on OrionOS (the daemon's GET /access reads
 * ~/.config/orion/access.json); Ctrl+= / Ctrl+- / Ctrl+0 zoom this window on any system and are remembered here.
 * Reduced motion is the existing calm-motion setting (hudPrefs), which already follows the system's choice.
 * Everything is CSS variables and data attributes on <html> (styles/access.css), so components need no changes.
 */
import { useEffect } from 'react';
import { ORION_HTTP_URL } from './assets';
import '../styles/access.css';

interface Access {
  contrast: boolean;
  text_scale: number;
  hud_zoom: number;
}

const KEY = 'orion_hud_zoom';
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function localZoom(): number {
  try {
    const v = Number(window.localStorage.getItem(KEY));
    return Number.isFinite(v) && v > 0 ? clamp(v, 0.5, 3) : 1;
  } catch {
    return 1;
  }
}

function apply(a: Access, zoom: number): void {
  const root = document.documentElement;
  if (a.contrast) root.dataset.contrast = '';
  else delete root.dataset.contrast;
  root.style.setProperty('--orion-font-scale', String(clamp(a.text_scale || 1, 1, 2)));
  const z = clamp((a.hud_zoom || 1) * zoom, 0.5, 3);
  if (z === 1) root.style.removeProperty('--orion-zoom');
  else root.style.setProperty('--orion-zoom', String(z));
  root.toggleAttribute('data-zoomed', z !== 1);
}

export function useHudAccess(): void {
  useEffect(() => {
    let os: Access = { contrast: false, text_scale: 1, hud_zoom: 1 };
    let zoom = localZoom();
    let alive = true;
    const refresh = async () => {
      try {
        const r = await fetch(`${ORION_HTTP_URL}/access`);
        if (r.ok) os = { ...os, ...((await r.json()) as Partial<Access>) };
      } catch {
        /* no daemon (browser preview, Windows without OrionOS): local zoom only */
      }
      if (alive) apply(os, zoom);
    };
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey || e.metaKey) return;
      const step = e.key === '=' || e.key === '+' ? 0.1 : e.key === '-' ? -0.1 : e.key === '0' ? 0 : null;
      if (step === null) return;
      e.preventDefault();
      zoom = step === 0 ? 1 : clamp(Math.round((zoom + step) * 10) / 10, 0.5, 3);
      try {
        window.localStorage.setItem(KEY, String(zoom));
      } catch {
        /* ignore */
      }
      apply(os, zoom);
    };
    apply(os, zoom);
    void refresh();
    // Settings is a separate window: pick up its changes when you come back here, and now and then.
    const timer = window.setInterval(refresh, 30_000);
    window.addEventListener('focus', refresh);
    window.addEventListener('keydown', onKey);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('keydown', onKey);
    };
  }, []);
}
