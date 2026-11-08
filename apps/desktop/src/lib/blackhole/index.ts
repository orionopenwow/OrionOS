export {
  BlackHoleRenderer,
  BLACKHOLE_UNIFORMS,
  BODY_SLOTS,
  DEFAULT_UNIFORMS,
  supportsWebGL2,
  blackHoleFragmentSource,
  blackHoleRayMarchSource,
} from './renderer';
export type { BlackHoleUniforms, BlackHoleOptions } from './renderer';
export {
  autoQuality,
  probeGpu,
  prefersReducedMotion,
  qualityName,
  readQualitySetting,
  resolveQuality,
  watchBattery,
  QUALITY_EVENT,
  QUALITY_STORAGE_KEY,
  writeQualitySetting,
} from './quality';
export type { BlackHoleQuality, QualitySetting, GpuInfo } from './quality';
export { MODE_PRESETS, SKIN_PRESETS, presetFor } from './presets';
export type { BlackHoleMode } from './presets';
export {
  applySignals,
  getBlackHoleBodies,
  getBlackHoleSignals,
  setBlackHoleBodies,
  setBlackHoleSignals,
  subscribeBlackHoleBodies,
  subscribeBlackHoleSignals,
} from './signals';
export type { BlackHoleSignals } from './signals';
export { useBlackHole } from './useBlackHole';
export type { UseBlackHoleOptions } from './useBlackHole';
export { initialReactions, reactionsReducer, reactionSignals, reactionBodies, dayTone, isAnimating } from './reactions';
export type { ReactionState, ReactionAction, CoreSignalsMessage, CometSize } from './reactions';
export { useCoreReactions, feedTheHole } from './useCoreReactions';
