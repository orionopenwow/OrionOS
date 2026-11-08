import { useState } from 'react';
import {
  Volume2,
  VolumeX,
  Zap,
  GraduationCap,
  Music,
  SendHorizontal,
  Bot,
  Sliders,
  Check,
} from 'lucide-react';
import type { PersonalityMode } from '@orion/shared-types';
import { cosmicAudio } from '../lib/cosmicAudio';

interface Props {
  personality: PersonalityMode;
  onSetPersonality: (mode: PersonalityMode) => void;
  ttsVoices: Array<{ id: string; name: string }>;
  ttsSettings: Record<string, unknown>;
  onSetTtsVoice: (voiceId?: string, rate?: number, volume?: number) => void;
  onSendTelegram?: (text: string) => void;
}

export default function PersonalityVoicePanel({
  personality,
  onSetPersonality,
  ttsVoices,
  ttsSettings,
  onSetTtsVoice,
  onSendTelegram,
}: Props) {
  const [selectedVoice, setSelectedVoice] = useState<string>(
    (ttsSettings.current_voice_id as string) || (ttsVoices[0]?.id ?? '')
  );
  const [speechRate, setSpeechRate] = useState<number>((ttsSettings.rate as number) || 185);
  const [speechVolume, setSpeechVolume] = useState<number>((ttsSettings.volume as number) || 0.9);

  const [audioMuted, setAudioMuted] = useState<boolean>(!cosmicAudio.enabled);
  const [audioVolume, setAudioVolume] = useState<number>(cosmicAudio.volume);
  const [telegramMsg, setTelegramMsg] = useState('');
  const [telegramStatus, setTelegramStatus] = useState(false);

  const handleToggleSound = () => {
    const next = !audioMuted;
    setAudioMuted(next);
    cosmicAudio.setMuted(next);
    if (!next) {
      cosmicAudio.playCommandDetected();
    }
  };

  const handleAudioVolumeChange = (vol: number) => {
    setAudioVolume(vol);
    cosmicAudio.setVolume(vol);
  };

  const handleApplyVoice = (voiceId: string) => {
    setSelectedVoice(voiceId);
    onSetTtsVoice(voiceId, speechRate, speechVolume);
  };

  const handleRateChange = (rate: number) => {
    setSpeechRate(rate);
    onSetTtsVoice(selectedVoice, rate, speechVolume);
  };

  const handleVolumeChange = (vol: number) => {
    setSpeechVolume(vol);
    onSetTtsVoice(selectedVoice, speechRate, vol);
  };

  const handleSendTelegram = () => {
    if (!telegramMsg.trim() || !onSendTelegram) return;
    onSendTelegram(telegramMsg.trim());
    setTelegramMsg('');
    setTelegramStatus(true);
    setTimeout(() => setTelegramStatus(false), 2000);
  };

  return (
    <div className="flex h-full w-full gap-5 p-6 font-sans">
      {/* Column 1: Personality & Tone */}
      <div className="flex w-1/2 flex-col rounded-2xl border border-white/10 bg-[#070913]/80 p-5 backdrop-blur-xl shadow-2xl">
        <div className="flex items-center gap-2 pb-3 border-b border-white/10">
          <span className="p-1.5 rounded-lg bg-[#C084FC]/20 text-[#C084FC] border border-[#C084FC]/30">
            <Bot size={16} />
          </span>
          <div>
            <div className="font-display text-sm font-bold tracking-wider text-white">ORION PERSONALITY & TONE</div>
            <div className="text-[10px] font-mono text-white/50">Adaptive LLM demeanor & speech behavior</div>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          <div className="text-[11px] font-mono uppercase tracking-wider text-white/40">
            Select Tone
          </div>

          {/* Concise Companion */}
          <button
            onClick={() => onSetPersonality('concise_companion')}
            className={`w-full text-left p-4 rounded-xl border transition-all ${
              personality === 'concise_companion'
                ? 'border-[#00E5FF]/60 bg-[#00E5FF]/10 shadow-[0_0_20px_rgba(0,229,255,0.15)]'
                : 'border-white/5 bg-white/[0.02] hover:bg-white/[0.05]'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="p-1.5 rounded-lg bg-[#00E5FF]/20 text-[#00E5FF]">
                  <Zap size={16} />
                </span>
                <span className="text-sm font-bold text-white">Concise Companion</span>
              </div>
              {personality === 'concise_companion' && (
                <span className="px-2 py-0.5 rounded-full bg-[#00E5FF]/20 border border-[#00E5FF]/40 text-[10px] font-mono text-[#00E5FF]">
                  ACTIVE
                </span>
              )}
            </div>
            <p className="mt-2 text-xs text-white/70 leading-relaxed">
              Fast, friendly, punchy desktop partner. Delivers direct answers, avoids filler text, and focuses on quick action execution.
            </p>
          </button>

          {/* Detailed Researcher */}
          <button
            onClick={() => onSetPersonality('detailed_researcher')}
            className={`w-full text-left p-4 rounded-xl border transition-all ${
              personality === 'detailed_researcher'
                ? 'border-[#C084FC]/60 bg-[#C084FC]/10 shadow-[0_0_20px_rgba(192,132,252,0.15)]'
                : 'border-white/5 bg-white/[0.02] hover:bg-white/[0.05]'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="p-1.5 rounded-lg bg-[#C084FC]/20 text-[#C084FC]">
                  <GraduationCap size={16} />
                </span>
                <span className="text-sm font-bold text-white">Detailed Researcher</span>
              </div>
              {personality === 'detailed_researcher' && (
                <span className="px-2 py-0.5 rounded-full bg-[#C084FC]/20 border border-[#C084FC]/40 text-[10px] font-mono text-[#C084FC]">
                  ACTIVE
                </span>
              )}
            </div>
            <p className="mt-2 text-xs text-white/70 leading-relaxed">
              Analytical, academic precision. Provides deep architectural breakdowns, explores edge cases, and provides structured explanations with code snippets.
            </p>
          </button>
        </div>

        {/* Telegram remote text bridge */}
        <div className="mt-auto pt-4 border-t border-white/10">
          <div className="flex items-center gap-2 mb-2">
            <SendHorizontal size={14} className="text-[#00E5FF]" />
            <span className="text-xs font-mono uppercase tracking-wider text-white/70">
              Telegram Output Dispatch
            </span>
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={telegramMsg}
              onChange={(e) => setTelegramMsg(e.target.value)}
              placeholder="Text message or task output to phone..."
              className="flex-1 rounded-xl border border-white/10 bg-black/40 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-[#00E5FF]/50 focus:outline-none"
              onKeyDown={(e) => e.key === 'Enter' && handleSendTelegram()}
            />
            <button
              onClick={handleSendTelegram}
              disabled={!telegramMsg.trim()}
              className="flex items-center gap-1 px-3 py-1.5 rounded-xl bg-[#00E5FF]/15 hover:bg-[#00E5FF]/25 text-[#00E5FF] text-xs font-mono border border-[#00E5FF]/30 disabled:opacity-30 transition-all"
            >
              {telegramStatus ? <Check size={12} /> : <SendHorizontal size={12} />}
              <span>{telegramStatus ? 'Sent' : 'Send'}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Column 2: Voice & Sound Effects */}
      <div className="flex w-1/2 flex-col rounded-2xl border border-white/10 bg-[#070913]/80 p-5 backdrop-blur-xl shadow-2xl space-y-4 overflow-y-auto custom-scrollbar">
        {/* Voice Selection */}
        <div className="pb-3 border-b border-white/10">
          <div className="flex items-center gap-2 mb-3">
            <span className="p-1.5 rounded-lg bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/30">
              <Sliders size={16} />
            </span>
            <div>
              <div className="font-display text-sm font-bold tracking-wider text-white">TTS VOICE SELECTION</div>
              <div className="text-[10px] font-mono text-white/50">Native offline desktop speech synthesizer</div>
            </div>
          </div>

          <label className="text-[11px] font-mono text-white/60 block mb-1.5">Installed System Voices</label>
          <select
            value={selectedVoice}
            onChange={(e) => handleApplyVoice(e.target.value)}
            className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white focus:border-[#3DFFA8]/50 focus:outline-none"
          >
            {ttsVoices.length === 0 ? (
              <option value="">Default System Voice</option>
            ) : (
              ttsVoices.map((v) => (
                <option key={v.id} value={v.id} className="bg-[#0B0E1E] text-white">
                  {v.name}
                </option>
              ))
            )}
          </select>

          {/* Rate Slider */}
          <div className="mt-3">
            <div className="flex justify-between text-xs font-mono text-white/60 mb-1">
              <span>Speech Rate (WPM)</span>
              <span className="text-[#3DFFA8]">{speechRate}</span>
            </div>
            <input
              type="range"
              min={120}
              max={280}
              value={speechRate}
              onChange={(e) => handleRateChange(parseInt(e.target.value, 10))}
              className="w-full accent-[#3DFFA8] cursor-pointer"
            />
          </div>

          {/* Volume Slider */}
          <div className="mt-3">
            <div className="flex justify-between text-xs font-mono text-white/60 mb-1">
              <span>Speech Volume</span>
              <span className="text-[#3DFFA8]">{Math.round(speechVolume * 100)}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={speechVolume}
              onChange={(e) => handleVolumeChange(parseFloat(e.target.value))}
              className="w-full accent-[#3DFFA8] cursor-pointer"
            />
          </div>
        </div>

        {/* Cosmic Ambient Sound Effects */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="p-1.5 rounded-lg bg-[#E9D5FF]/20 text-[#E9D5FF] border border-[#E9D5FF]/30">
                <Music size={16} />
              </span>
              <div>
                <div className="font-display text-sm font-bold tracking-wider text-white">COSMIC SOUND EFFECTS</div>
                <div className="text-[10px] font-mono text-white/50">Celestial chimes for cues & events</div>
              </div>
            </div>
            <button
              onClick={handleToggleSound}
              className={`p-2 rounded-xl border transition-all ${
                audioMuted
                  ? 'bg-red-500/20 text-red-400 border-red-500/30'
                  : 'bg-[#3DFFA8]/20 text-[#3DFFA8] border-[#3DFFA8]/30 shadow-[0_0_12px_rgba(61,255,168,0.2)]'
              }`}
              title={audioMuted ? 'Unmute Cosmic Chimes' : 'Mute Cosmic Chimes'}
            >
              {audioMuted ? <VolumeX size={16} /> : <Volume2 size={16} />}
            </button>
          </div>

          <div className="space-y-2 text-xs font-mono text-white/70">
            <div className="flex justify-between items-center p-2 rounded-lg bg-white/[0.02] border border-white/5">
              <span>Listening Chime</span>
              <button
                onClick={() => cosmicAudio.playListenStart()}
                className="px-2 py-0.5 rounded bg-white/10 hover:bg-white/20 text-[10px] text-white"
              >
                Preview
              </button>
            </div>
            <div className="flex justify-between items-center p-2 rounded-lg bg-white/[0.02] border border-white/5">
              <span>Command Detected Chime</span>
              <button
                onClick={() => cosmicAudio.playCommandDetected()}
                className="px-2 py-0.5 rounded bg-white/10 hover:bg-white/20 text-[10px] text-white"
              >
                Preview
              </button>
            </div>
            <div className="flex justify-between items-center p-2 rounded-lg bg-white/[0.02] border border-white/5">
              <span>Task Finished Chime</span>
              <button
                onClick={() => cosmicAudio.playTaskFinished()}
                className="px-2 py-0.5 rounded bg-white/10 hover:bg-white/20 text-[10px] text-white"
              >
                Preview
              </button>
            </div>
            <div className="flex justify-between items-center p-2 rounded-lg bg-white/[0.02] border border-white/5">
              <span>Proactive Insight Chime</span>
              <button
                onClick={() => cosmicAudio.playProactiveInsight()}
                className="px-2 py-0.5 rounded bg-white/10 hover:bg-white/20 text-[10px] text-white"
              >
                Preview
              </button>
            </div>
          </div>

          {/* Audio Chime Volume */}
          <div className="mt-3">
            <div className="flex justify-between text-xs font-mono text-white/60 mb-1">
              <span>Chime Volume</span>
              <span className="text-[#C084FC]">{Math.round(audioVolume * 100)}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={audioVolume}
              onChange={(e) => handleAudioVolumeChange(parseFloat(e.target.value))}
              className="w-full accent-[#C084FC] cursor-pointer"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
