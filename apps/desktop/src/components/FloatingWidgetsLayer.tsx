import { AnimatePresence } from 'framer-motion';
import { Activity, Bot, Chrome, Gamepad2, Layers, MousePointer, Users } from 'lucide-react';
import GlassWindow from './glass/GlassWindow';
import { useWindowManager } from '../lib/windowManager';
import TranslucentAgentCards from './TranslucentAgentCards';
import SystemAnalyticsDial from './SystemAnalyticsDial';
import ChromeContextWindow from './ChromeContextWindow';
import DiscordImportSkin from './DiscordImportSkin';
import GameBugTrackerHub from './GameBugTrackerHub';
import ScreenTasksPanel from './ScreenTasksPanel';
import PersonalityVoicePanel from './PersonalityVoicePanel';
import type {
  TelemetrySnapshot,
  AgentStates,
  ClientInfo,
  GameBugRecord,
  PersonalityMode,
  ScreenWorkflowProgressEvent,
} from '@orion/shared-types';

/** Each open widget is an Orion Glass window (id `widget:<key>`) with its own taskbar tile. */
export const WIDGET_META: Record<keyof WidgetState, { title: string; color: string }> = {
  analytics: { title: 'Cosmic telemetry', color: '#00E5FF' },
  agents: { title: 'Agent crew', color: '#5CFFB0' },
  chrome: { title: 'Chrome live scraper', color: '#FF7043' },
  discord: { title: 'Discord community', color: '#8C95FF' },
  screen_tasks: { title: 'Screen tasks', color: '#5CFFB0' },
  game_bugs: { title: 'Game bug hub', color: '#FFC66B' },
  personality: { title: 'Personality & voice', color: '#FFC66B' },
};

export interface WidgetState {
  agents: boolean;
  analytics: boolean;
  chrome: boolean;
  discord: boolean;
  screen_tasks: boolean;
  game_bugs: boolean;
  personality: boolean;
}

interface Props {
  widgets: WidgetState;
  onToggleWidget: (key: keyof WidgetState) => void;
  telemetry: TelemetrySnapshot | null;
  agents: AgentStates;
  onToggleAgent: (agentId: any, enabled: boolean) => void;
  activeWindow?: string | null;
  latestBrowserTab?: any;
  clients: ClientInfo[];
  onTriggerPrompt: (prompt: string) => void;
  onDeepScrape: (url: string) => void;
  onImportDiscord: (messages: any[]) => void;
  isDiscordSkinActive: boolean;
  onToggleDiscordSkin: () => void;
  // Screen tasks
  workflowProgress: ScreenWorkflowProgressEvent | null;
  onExecuteWorkflow: (steps: Array<Record<string, unknown>>) => void;
  onExecuteAction: (action: Record<string, unknown>) => void;
  // Game bugs
  gameBugs: GameBugRecord[];
  onSendDiscordBugResponse: (bugId: string, response: string) => void;
  onSendTelegram: (text: string) => void;
  // Personality & Voice
  personality: PersonalityMode;
  onSetPersonality: (mode: PersonalityMode) => void;
  ttsVoices: Array<{ id: string; name: string }>;
  ttsSettings: Record<string, unknown>;
  onSetTtsVoice: (voiceId?: string, rate?: number, volume?: number) => void;
}

export default function FloatingWidgetsLayer({
  widgets,
  onToggleWidget,
  telemetry,
  agents,
  onToggleAgent,
  activeWindow,
  latestBrowserTab,
  clients,
  onTriggerPrompt,
  onDeepScrape,
  onImportDiscord,
  isDiscordSkinActive,
  onToggleDiscordSkin,
  workflowProgress,
  onExecuteWorkflow,
  onExecuteAction,
  gameBugs,
  onSendDiscordBugResponse,
  onSendTelegram,
  personality,
  onSetPersonality,
  ttsVoices,
  ttsSettings,
  onSetTtsVoice,
}: Props) {
  const wm = useWindowManager();
  const shown = (k: keyof WidgetState) => widgets[k] && !wm.minimised[`widget:${k}`];
  const close = (k: keyof WidgetState) => () => onToggleWidget(k);

  return (
    <>
      <AnimatePresence>
        {shown('analytics') && (
          <GlassWindow
            key="analytics"
            id="widget:analytics"
            title="Cosmic telemetry"
            icon={<Activity size={14} className="text-[#00E5FF]" />}
            onClose={close('analytics')}
            className="top-14 right-4 w-[460px] max-w-[calc(50vw-220px)] max-h-[460px]"
            bodyClassName="overflow-y-auto"
          >
            <SystemAnalyticsDial telemetry={telemetry} />
          </GlassWindow>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {shown('agents') && (
          <GlassWindow
            key="agents"
            id="widget:agents"
            title="Agent crew"
            icon={<Users size={14} className="text-[#5CFFB0]" />}
            onClose={close('agents')}
            className="bottom-[88px] right-4 w-[500px] max-w-[calc(50vw-220px)] max-h-[70vh]"
            bodyClassName="overflow-y-auto p-4"
          >
            <TranslucentAgentCards telemetry={telemetry} agents={agents} onToggleAgent={onToggleAgent} activeWindow={activeWindow} />
          </GlassWindow>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {shown('chrome') && (
          <GlassWindow
            key="chrome"
            id="widget:chrome"
            title="Chrome live scraper"
            icon={<Chrome size={14} className="text-[#FF7043]" />}
            onClose={close('chrome')}
            className="bottom-[88px] left-[560px] w-[460px] max-w-[calc(50vw-220px)] max-h-[460px]"
            bodyClassName="overflow-y-auto"
          >
            <ChromeContextWindow
              tabContext={latestBrowserTab}
              clients={clients}
              onTriggerAction={onTriggerPrompt}
              onDeepScrape={onDeepScrape}
            />
          </GlassWindow>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {shown('discord') && (
          <GlassWindow
            key="discord"
            id="widget:discord"
            title="Discord community"
            icon={<Layers size={14} className="text-[#8C95FF]" />}
            onClose={close('discord')}
            className="top-16 right-16 w-[560px] max-w-[90vw] max-h-[80vh]"
            bodyClassName="overflow-y-auto"
          >
            <DiscordImportSkin
              onImportMessages={onImportDiscord}
              onAskAboutDiscord={onTriggerPrompt}
              isDiscordSkinActive={isDiscordSkinActive}
              onToggleDiscordSkin={onToggleDiscordSkin}
            />
          </GlassWindow>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {shown('screen_tasks') && (
          <GlassWindow
            key="screen_tasks"
            id="widget:screen_tasks"
            title="Screen tasks"
            icon={<MousePointer size={14} className="text-[#5CFFB0]" />}
            onClose={close('screen_tasks')}
            className="inset-x-0 mx-auto top-14 bottom-[88px] w-[min(1024px,calc(100vw-80px))]"
            bodyClassName="overflow-y-auto"
          >
            <ScreenTasksPanel workflowProgress={workflowProgress} onExecuteWorkflow={onExecuteWorkflow} onExecuteAction={onExecuteAction} />
          </GlassWindow>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {shown('game_bugs') && (
          <GlassWindow
            key="game_bugs"
            id="widget:game_bugs"
            title="Game bug hub"
            icon={<Gamepad2 size={14} className="text-[#FFC66B]" />}
            onClose={close('game_bugs')}
            className="inset-x-0 mx-auto top-14 bottom-[88px] w-[min(1152px,calc(100vw-80px))]"
            bodyClassName="overflow-y-auto"
          >
            <GameBugTrackerHub bugs={gameBugs} onSendResponse={onSendDiscordBugResponse} onSendTelegram={onSendTelegram} />
          </GlassWindow>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {shown('personality') && (
          <GlassWindow
            key="personality"
            id="widget:personality"
            title="Personality & voice"
            icon={<Bot size={14} className="text-[#FFC66B]" />}
            onClose={close('personality')}
            className="inset-x-0 mx-auto top-16 bottom-[96px] w-[min(896px,calc(100vw-96px))]"
            bodyClassName="overflow-y-auto"
          >
            <PersonalityVoicePanel
              personality={personality}
              onSetPersonality={onSetPersonality}
              ttsVoices={ttsVoices}
              ttsSettings={ttsSettings}
              onSetTtsVoice={onSetTtsVoice}
              onSendTelegram={onSendTelegram}
            />
          </GlassWindow>
        )}
      </AnimatePresence>
    </>
  );
}
