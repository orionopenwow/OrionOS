/** Tiny event bus the guided tour uses to drive the HUD (open the panel, switch tabs, animate the core). */
import type { ContextTab } from '../components/EarthContextWindow';

export const TOUR_EVENT = 'orion:tour';
export interface TourCommand {
  open?: boolean;
  tab?: ContextTab;
  speaking?: boolean;
}
export function tourCommand(c: TourCommand) {
  window.dispatchEvent(new CustomEvent<TourCommand>(TOUR_EVENT, { detail: c }));
}
