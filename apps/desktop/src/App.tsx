import { useState, useEffect } from 'react';
import { soundscape } from './lib/soundscape';
import Singularity, { type GalacticMode, type SwallowItem } from './components/Singularity';
import CosmicBackgroundCanvas from './components/CosmicBackgroundCanvas';
import SpaceCurve from './components/SpaceCurve'; // [look] depth layer (after-1.0 plan §3)
import FloatingVoiceTranscript from './components/FloatingVoiceTranscript';
import GlobalDropAndPasteZone from './components/GlobalDropAndPasteZone';
import FloatingWidgetsLayer, { WIDGET_META, type WidgetState } from './components/FloatingWidgetsLayer';
import EarthContextWindow, { type ContextTab, type ToolOptionId } from './components/EarthContextWindow';
import BibleOverlays from './components/bible/BibleOverlays';
import VipSatellites from './components/bible/VipSatellites';
import WidgetDock from './components/widgets/WidgetDock';
import AgentPet from './components/widgets/AgentPet';
import AgentSwarm from './components/swarm/AgentSwarm';
import ComputeMeter from './components/widgets/ComputeMeter';
import MobileGlance from './components/widgets/MobileGlance';
import { useIsMobile } from './lib/device';
import Taskbar, { type TaskbarTile } from './components/glass/Taskbar';
import HudSettingsWindow from './components/glass/HudSettingsWindow';
import { taskbarClick, useWindowManager } from './lib/windowManager';
import { useArrival } from './lib/arrival';
import { startPointerVars } from './lib/parallax';
import { useCalmMotion } from './lib/hudPrefs';
import { useHudAccess } from './lib/hudAccess';
import { Activity, Bot, Chrome, Gamepad2, Layers, Inbox, MousePointer, Settings2, Users } from 'lucide-react';
import { TOUR_EVENT, type TourCommand } from './demo/tourBus';
import { dropToVault, isPipelineAsset, uploadAsset } from './lib/assets';
import ProactiveInsightCard from './components/ProactiveInsightCard';
import DailyRoutines from './components/DailyRoutines';
import VideoScout from './components/VideoScout';
import MissionControl from './components/missions/MissionControl'; // Phase 3: Mission Control
import TitleBar from './components/shell/TitleBar';
import BootOverlay from './components/shell/BootOverlay';
import UpdateBanner from './components/shell/UpdateBanner';
import MicLight from './components/shell/MicLight';
// [simple-menu] CameraLight and SafetyLight now sit in the status strip (components/menu/StatusStrip.tsx)
import GhostPreview from './components/selfprog/GhostPreview'; // Phase 8: Living Orion ghost preview
import HardwareCard from './components/shell/HardwareCard'; // OrionOS: hardware report card while trying Orion
import { appColor, focusAppWindow, useAppWindows } from './lib/appWindows'; // OrionOS H13: real app windows
import SkillCards from './components/shell/SkillCards';
import V1Shell from './components/shell/V1Shell'; // [v1gaps] Guide Mode + privacy dashboard
import TestCenter from './components/testcenter/TestCenter'; // [testcenter] Orion Test Center
import CommandConsole, { menuIcon } from './components/console/CommandConsole'; // [look] the one console (was the menu + chat)
import OrionButton, { useOrionButtonPos } from './components/console/OrionButton'; // [look]
import { inTaskbarCorner } from './lib/orionButton';
import StatusStrip from './components/menu/StatusStrip'; // [simple-menu]
import ProjectsWindow from './components/projects/ProjectsWindow'; // [simple-menu] Start a project with Claude
import SelfHistory from './components/selfprog/SelfHistory'; // [simple-menu] More > Self-changes history
import { MENU, onOrionOpen, openOrion } from './lib/menu'; // [simple-menu]
import { windowManager } from './lib/windowManager'; // [simple-menu]
import PhonePanel from './components/phone/PhonePanel'; // [phone2] pair + test the phone (orion:open 'phone')
import { useOrionSocket } from './hooks/useOrionSocket';
import { useMicAnalyser } from './hooks/useMicAnalyser';
import { useSpeechRecognition } from './hooks/useSpeechRecognition';
import { cosmicAudio } from './lib/cosmicAudio';
import { escapeLeavesField } from './lib/keyboard';
import { AnimatePresence, motion } from 'framer-motion';
import type { CoreState } from '@orion/shared-types';

export default function App() {
  const {
    feed,
    telemetry,
    agents,
    clients,
    memories,
    responses,
    pending,
    latestBrowserTab,
    proactiveInsights,
    gameBugs,
    personalityMode,
    ttsVoices,
    ttsSettings,
    workflowProgress,
    audioAnalyser,
    isAudioPlaying,
    waveformBroadcast,
    send,
    setPersonality,
    setTtsVoice,
    executeScreenAction,
    executeScreenWorkflow,
    sendTelegram,
    sendDiscordBugResponse,
    dismissInsight,
    connected,
    approvals,
    selfEvolveStatus,
    commsThreads,
    commsDrafts,
    commsReplyResult,
    pacing,
    pacingCheckin,
    trainingExport,
    decideApproval,
    decidePatch,
    requestSelfEvolve,
    commsReply,
    requestCommsDraft,
    markCommsRead,
    ackPacing,
    consolidateMemories,
    bible,
    bibleSend,
    biblePatch,
    bibleAddAsset,
  } = useOrionSocket();
  const [focusTab, setFocusTab] = useState<{ tab: ContextTab; nonce: number; threadKey?: string; draft?: string } | null>(null);

  // Widget visibility state - all closed initially so desktop starts completely clean
  const [widgets, setWidgets] = useState<WidgetState>({
    agents: false,
    analytics: false,
    chrome: false,
    discord: false,
    screen_tasks: false,
    game_bugs: false,
    personality: false,
  });

  // [look] The chat lives in the command console now; the Orion window (messages, queue, projects, tools, life)
  // opens from the console, so every screen starts on the bare core with the one Orion button.
  const [dialogueOpen, setDialogueOpen] = useState(false);
  /** [look] Open the console's chat (it is the chat). */
  const openChat = () => openOrion('chat');
  /** [look] Open a tab of the Orion window; 'chat' goes to the console. */
  const showTab = (f: { tab: ContextTab; nonce: number; threadKey?: string; draft?: string }) => {
    if (f.tab === 'chat') return openChat();
    setDialogueOpen(true);
    setFocusTab(f);
  };
  const [settingsOpen, setSettingsOpen] = useState(false);
  const wm = useWindowManager();
  const orionShown = dialogueOpen && !wm.minimised.orion;
  useArrival(connected);
  useEffect(() => startPointerVars(), []); // [look] pointer-reactive background
  useHudAccess(); // high contrast, bigger text, zoom (Settings > Accessibility)
  const calmMotion = useCalmMotion();
  useEffect(() => {
    if (calmMotion) document.documentElement.dataset.calmMotion = '';
    else delete document.documentElement.dataset.calmMotion;
  }, [calmMotion]);
  const [micActive, setMicActive] = useState(false);
  const [spokenTranscript, setSpokenTranscript] = useState('');
  const [currentTaskText, setCurrentTaskText] = useState<string | undefined>(undefined);
  const [activeWindow, setActiveWindow] = useState<string | null>(null);
  const [isDiscordSkinActive, setIsDiscordSkinActive] = useState(false);
  const [viewportW, setViewportW] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setViewportW(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  // Desktop: when the Orion window (24px + ~520px, left) is open, glide the core into the free space on the right.
  const panelRight = 24 + Math.min(520, viewportW - 48);
  const mobile = useIsMobile();
  const coreSize = mobile ? Math.max(220, Math.min(320, viewportW - 70)) : 430;
  const coreShift = !mobile && orionShown && viewportW >= 1000 ? Math.round(panelRight / 2) : 0;
  const coreScale = !mobile && orionShown && viewportW >= 1000 && viewportW - panelRight < 560 ? 0.82 : 1;

  // Guided tour (guest demo): Orion opens menus as she talks about them.
  const [tourSpeaking, setTourSpeaking] = useState(false);
  useEffect(() => {
    const onTour = (e: Event) => {
      const c = (e as CustomEvent<TourCommand>).detail;
      if (c.speaking !== undefined) setTourSpeaking(c.speaking);
      if (c.open === false) setDialogueOpen(false);
      if (c.tab === 'chat' || (c.open === true && !c.tab)) openOrion('chat');
      else if (c.tab) {
        setDialogueOpen(true);
        setFocusTab({ tab: c.tab, nonce: Date.now() });
      }
    };
    window.addEventListener(TOUR_EVENT, onTour);
    return () => window.removeEventListener(TOUR_EVENT, onTour);
  }, []);

  // Instant speech recognition: streaming voice input directly into floating text
  const { startListening, stopListening } = useSpeechRecognition({
    onTranscriptChange: (interim) => {
      setSpokenTranscript(interim);
    },
    onFinalTranscript: (final) => {
      setSpokenTranscript((prev) => (prev ? `${prev} ${final}` : final).trim());
    },
  });

  const handleToggleMic = () => {
    const next = !micActive;
    setMicActive(next);
    if (next) {
      setSpokenTranscript('');
      cosmicAudio.playListenStart();
      startListening();
    } else {
      stopListening();
    }
  };

  // Clicking Orion directly triggers voice mode & floating speech
  const handleOrionClick = () => {
    handleToggleMic();
  };

  // When speech is completed or user confirms
  const handleVoiceSubmit = (text: string) => {
    if (!text.trim()) return;
    cosmicAudio.playCommandDetected();
    setCurrentTaskText(`Synthesizing: "${text.slice(0, 35)}..."`);
    send({
      event: 'USER_PROMPT',
      text: text.trim(),
    });
    setSpokenTranscript('');
    stopListening();
    setMicActive(false);
  };

  // Handle Drag & Drop of Images, Videos, or Documents
  const handleIngestFile = (fileName: string, fileType: string, contentPreview?: string) => {
    setCurrentTaskText(`Analyzing document: ${fileName}`);
    openChat();
    const prompt = contentPreview
      ? `[FILE INGEST: ${fileName} (${fileType})]\nContent preview:\n${contentPreview}\n\nTask: Analyze and inspect this file.`
      : `[FILE INGEST: ${fileName} (${fileType})]\nTask: Analyze this media file and summarize key findings.`;
    send({
      event: 'USER_PROMPT',
      text: prompt,
    });
  };

  // Feature Bible asset pipeline: media & models get action pills ([Remove Background], [Compress for Web]…)
  // Event horizon: every dropped file spirals into the core, then gets handled by type.
  const [swallow, setSwallow] = useState<SwallowItem | null>(null);
  const swallowFile = (label: string, kind: SwallowItem['kind']) => setSwallow({ id: Date.now(), label, kind });
  const CODE_EXT = /\.(py|ts|tsx|js|jsx|mjs|rs|go|java|cs|cpp|c|h|lua|sql|json|ya?ml|toml|sh|ps1|bat)$/i;
  const LOG_EXT = /\.(log|out|err|trace)$/i;
  const DOC_EXT = /\.(pdf|md|markdown|txt|csv|html?|docx?)$/i;

  const handleAssetFile = (file: File): boolean => {
    if (isPipelineAsset(file)) {
      swallowFile(file.name, 'asset');
      setCurrentTaskText(`Asset: ${file.name}`);
      uploadAsset(file)
        .then((asset) => bibleAddAsset(asset))
        .catch((err) => setCurrentTaskText(`Upload failed: ${String(err.message ?? err).slice(0, 60)}`));
      return true;
    }
    if (CODE_EXT.test(file.name) || LOG_EXT.test(file.name)) {
      swallowFile(file.name, 'code');
      file.text().then((text) => {
        const isLog = LOG_EXT.test(file.name) || /Traceback|Exception|Error:|panicked|FATAL/.test(text.slice(0, 20000));
        const instruction = isLog
          ? 'Diagnose this error log: give the root cause, the exact fix, and a short bug report I can paste into an issue.'
          : 'Review this file and propose a cleaner refactor as a unified diff, with a two-line explanation of what changed and why.';
        openChat();
        handleSendPrompt(`[DROPPED FILE: ${file.name}]\n${text.slice(0, 12000)}\n\nTask: ${instruction}`);
      });
      return true;
    }
    if (DOC_EXT.test(file.name) || file.type === 'application/pdf') {
      swallowFile(file.name, 'doc');
      setCurrentTaskText(`Reading ${file.name}…`);
      dropToVault(file).catch((err) => setCurrentTaskText(`Couldn't read it: ${String(err.message ?? err).slice(0, 60)}`));
      return true;
    }
    return false;
  };

  // Handle Clipboard Paste (Ctrl+V)
  const handlePasteText = (text: string) => {
    setCurrentTaskText('Analyzing pasted content...');
    openChat();
    send({
      event: 'USER_PROMPT',
      text: `[PASTED TEXT]\n${text.slice(0, 3000)}\n\nTask: Review and process this input.`,
    });
  };

  const { analyser: micAnalyser } = useMicAnalyser(micActive);
  const effectiveAnalyser = micActive ? micAnalyser : audioAnalyser;

  // Derive Core state (processing, listening, speaking/idle)
  const coreState: CoreState = pending.length > 0
    ? 'processing'
    : micActive
    ? 'listening'
    : isAudioPlaying || tourSpeaking
    ? 'processing'
    : 'idle';

  // Galactic mode: the singularity's astrophysics follow what you're doing (priority order).
  const galacticMode: GalacticMode =
    bible.circadian?.phase === 'wind_down' || bible.circadian?.phase === 'night'
      ? 'winddown'
      : bible.sanctuary || pacing?.phase === 'break_due'
      ? 'sanctuary'
      : bible.producer
      ? 'podcast'
      : micActive || isAudioPlaying || tourSpeaking
      ? 'voice'
      : pacing && ['focus', 'warming', 'break_due'].includes(pacing.phase)
      ? 'focus'
      : 'idle';
  // Ambient soundscape follows the core's mode and steps back while Orion speaks.
  useEffect(() => soundscape.setMode(galacticMode), [galacticMode]);
  useEffect(() => soundscape.duck(isAudioPlaying), [isAudioPlaying]);
  const coreCaption =
    galacticMode === 'podcast' && bible.producer
      ? `● rec ${Math.floor(bible.producer.elapsed / 60)}:${String(Math.floor(bible.producer.elapsed % 60)).padStart(2, '0')}` +
        (bible.producer.highlights.length ? ` · ${bible.producer.highlights.length} marked` : '')
      : galacticMode === 'winddown' && bible.circadian
      ? bible.circadian.trigger === 'sunset'
        ? `sun's down · bedtime ${bible.circadian.bedtime}`
        : `wind-down · bedtime ${bible.circadian.bedtime}`
      : galacticMode === 'sanctuary' && !bible.sanctuary && pacing
      ? `${Math.floor(pacing.session_minutes)} min · sanctuary time`
      : null;

  // Automatically update speech bubble when notifications/tasks or skills run
  useEffect(() => {
    if (responses.length > 0) {
      const latest = responses[0];
      if (latest?.response?.summary) {
        setCurrentTaskText(latest.response.summary.slice(0, 70));
      }
    }
  }, [responses]);

  // Track window focus from screen deltas
  useEffect(() => {
    const latestDelta = feed.find((f) => f.label.startsWith('Screen Δ'));
    if (latestDelta) {
      const title = latestDelta.detail.split(' — ')[0];
      if (title && title !== 'Unknown window') {
        setActiveWindow(title);
      }
    }
  }, [feed]);

  const anyWidgetOpen = Object.values(widgets).some(Boolean) || dialogueOpen;

  const handleDismissAll = () => {
    if (anyWidgetOpen) {
      setWidgets({
        agents: false,
        analytics: false,
        chrome: false,
        discord: false,
        screen_tasks: false,
        game_bugs: false,
        personality: false,
      });
      setDialogueOpen(false);
    }
  };

  const handleToggleWidget = (key: keyof WidgetState) => {
    setWidgets((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleSelectTool = (cmd: ToolOptionId) => {
    if (cmd === 'screen') {
      handleSendPrompt('Inspect the current screen and report all visible windows and text.');
    } else if (cmd === 'telegram') {
      handleToggleWidget('personality');
    } else if (cmd === 'dialogue') {
      openChat();
    } else {
      handleToggleWidget(cmd as keyof WidgetState);
    }
  };

  const handleSendPrompt = (text: string) => {
    setCurrentTaskText(`Executing: "${text.slice(0, 40)}..."`);
    send({
      event: 'USER_PROMPT',
      text,
    });
  };

  const handleToggleAgent = (agent: any, enabled: boolean) => {
    send({
      event: 'AGENT_TOGGLE',
      agent,
      enabled,
    });
  };

  const handleImportDiscord = (messages: any[]) => {
    setCurrentTaskText(`Ingesting ${messages.length} Discord messages...`);
    send({
      event: 'DISCORD_IMPORT',
      messages,
    });
  };

  const handleDeepScrape = (url: string) => {
    setCurrentTaskText(`Deep scraping: ${url.slice(0, 35)}...`);
    send({
      event: 'CHROME_SCRAPE_REQUEST',
      url,
    });
  };

  // Keyboard shortcut: Ctrl+Space toggles Earth Context Window, Escape hides it and all open widgets
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // [look] Ctrl+Space, Super+Space and Alt+Space all open the command console (CommandConsole handles them)
      if (e.key === 'Escape') {
        if (escapeLeavesField(e)) return;
        handleDismissAll();
        if (micActive) handleToggleMic();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [anyWidgetOpen, micActive]);

  const WIDGET_ICONS: Record<keyof WidgetState, typeof Activity> = {
    analytics: Activity,
    agents: Users,
    chrome: Chrome,
    discord: Layers,
    screen_tasks: MousePointer,
    game_bugs: Gamepad2,
    personality: Bot,
  };
  // [simple-menu] The one menu (lib/menu.ts) opens things with `orion:open`. App owns the chat tabs, Settings, the
  // floating widgets and the side widgets; other windows (projects, missions, scout, skills, privacy, guide,
  // selfchanges, camera, and the "phone" and "tests" windows) listen for their own id.
  const [sideWidgets, setSideWidgets] = useState(() => {
    try {
      return localStorage.getItem('orion.sideWidgets') === '1';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('orion.sideWidgets', sideWidgets ? '1' : '0');
    } catch {
      /* private mode: remembered for now only */
    }
  }, [sideWidgets]);
  useEffect(() => {
    const tabs: Record<string, ContextTab> = { messages: 'comms', queue: 'queue', tools: 'tools', life: 'life' }; // [look] 'chat' is the console
    const widgetIds = Object.keys(WIDGET_META) as Array<keyof WidgetState>;
    return onOrionOpen([...Object.keys(tabs), 'settings', 'widgets', ...widgetIds], (id) => {
      if (tabs[id]) {
        windowManager.restore('orion');
        setDialogueOpen(true);
        setFocusTab({ tab: tabs[id], nonce: Date.now() });
      } else if (id === 'settings') {
        windowManager.restore('settings');
        setSettingsOpen(true);
      } else if (id === 'widgets') {
        setSideWidgets((v) => !v);
      } else {
        const key = id as keyof WidgetState;
        windowManager.restore(`widget:${key}`);
        setWidgets((prev) => ({ ...prev, [key]: true }));
      }
    });
  }, []);
  const appWindows = useAppWindows(!mobile);
  const orionPos = useOrionButtonPos(); // [look] the taskbar steps aside when the button rests in its corner // OrionOS H13: the apps open in the Sway session
  const taskbarTiles = (): TaskbarTile[] => {
    // [look] The taskbar only lists what is open; the one Orion button (console) sits on its own.
    const tiles: TaskbarTile[] = [];
    if (dialogueOpen) {
      tiles.push({
        id: 'orion',
        label: 'Messages, queue and tools',
        icon: <Inbox size={19} />,
        color: '#8DB0FF',
        open: dialogueOpen,
        minimised: !!wm.minimised.orion,
        onClick: () => taskbarClick('orion', dialogueOpen, setDialogueOpen),
      });
    }
    (Object.keys(widgets) as Array<keyof WidgetState>)
      .filter((k) => widgets[k])
      .forEach((k, i) => {
        const Icon = WIDGET_ICONS[k];
        const id = `widget:${k}`;
        tiles.push({
          id,
          label: WIDGET_META[k].title,
          icon: <Icon size={19} />,
          color: WIDGET_META[k].color,
          open: true,
          minimised: !!wm.minimised[id],
          separated: i === 0,
          onClick: () => taskbarClick(id, true, () => undefined),
        });
      });
    // OrionOS H13: real app windows (Firefox, Files, ...) after Orion's own; a click brings one to the front.
    appWindows.slice(0, 12).forEach((w, i) => {
      tiles.push({
        id: `app:${w.id}`,
        label: w.title && w.title !== w.app ? `${w.app}: ${w.title}` : w.app,
        icon: <span style={{ font: '700 15px Figtree, system-ui, sans-serif' }}>{(w.app || '?').slice(0, 1).toUpperCase()}</span>,
        color: appColor(w.app_id || w.app),
        open: true,
        minimised: w.minimised,
        separated: i === 0,
        onClick: () => void focusAppWindow(w.id),
      });
    });
    if (settingsOpen) {
      tiles.push({
        id: 'settings',
        label: 'Settings',
        icon: <Settings2 size={18} />,
        color: '#B9C0E8',
        open: settingsOpen,
        minimised: !!wm.minimised.settings,
        separated: true,
        onClick: () => taskbarClick('settings', settingsOpen, setSettingsOpen),
      });
    }
    // Other glass windows (Projects, Privacy, Self-changes, Test Center, Phone...) get a tile while open or minimised.
    const known = new Set(tiles.map((t) => t.id).concat('menu', 'orion'));
    const others = Array.from(new Set([...wm.order, ...Object.keys(wm.minimised)])).filter(
      (id) => !known.has(id) && !id.startsWith('widget:') && !id.startsWith('app:'),
    );
    others.forEach((id, i) => {
      const Icon = menuIcon(id);
      const label = MENU.find((m) => m.id === id)?.label ?? id;
      tiles.push({
        id,
        label,
        icon: <Icon size={18} />,
        color: '#B7CBFF',
        open: true,
        minimised: !!wm.minimised[id],
        separated: i === 0,
        onClick: () => {
          if (wm.minimised[id]) windowManager.restore(id);
          else if (windowManager.isFocused(id)) windowManager.minimise(id);
          else windowManager.focus(id);
        },
      });
    });
    return tiles;
  };

  return (
    <GlobalDropAndPasteZone onIngestFile={handleIngestFile} onPasteText={handlePasteText} onAssetFile={handleAssetFile}>
      <div
        className={`relative flex h-screen h-[100dvh] w-screen flex-col overflow-hidden font-sans select-none text-starlight transition-[background-color,filter] duration-[3000ms] ${
          isDiscordSkinActive ? 'bg-[#1E1F22]' : 'bg-[#05070E]'
        }`}
        style={{
          // Circadian wind-down: warm and dim the whole HUD in the 90 minutes before bed.
          filter:
            bible.circadian?.phase === 'wind_down'
              ? 'sepia(0.35) saturate(0.8) brightness(0.88)'
              : bible.circadian?.phase === 'night'
              ? 'sepia(0.5) saturate(0.65) brightness(0.75)'
              : undefined,
        }}
      >
        {/* Full-scene Animated Cosmic Backdrop with Twinkling Stars & Comets */}
        {!isDiscordSkinActive && <CosmicBackgroundCanvas />}
        {!isDiscordSkinActive && <SpaceCurve /> /* [look] depth: flat edges, space curves toward the core */}

        {/* Desktop shell: frameless title bar, brain bootstrap screen, auto-update banner */}
        <div onClick={(e) => e.stopPropagation()}>
          <TitleBar />
          <BootOverlay connected={connected} />
          <UpdateBanner />
          <MicLight />
          {!mobile && <GhostPreview />}{/* Phase 8: Orion's change to itself + Undo */}
          {!mobile && <HardwareCard />}
          <SkillCards />
          <VideoScout />
          <MissionControl />
          <V1Shell mobile={mobile} /> {/* [v1gaps] */}
          <TestCenter mobile={mobile} /> {/* [testcenter] opens on orion:open 'tests' */}
          <ProjectsWindow mobile={mobile} /> {/* [simple-menu] */}
          <SelfHistory mobile={mobile} /> {/* [simple-menu] */}
          <PhonePanel mobile={mobile} /> {/* [phone2] */}
        </div>

        {/* V2 Ambient pacing: wellness check-in linked to DailyRoutines when the focus arc hits its limit */}
        <AnimatePresence>
          {pacingCheckin && (
            <motion.div
              key={pacingCheckin.id}
              initial={{ opacity: 0, y: -24, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -16, scale: 0.96 }}
              transition={{ type: 'spring', stiffness: 260, damping: 24 }}
              className="fixed inset-x-0 top-12 z-[60] mx-auto w-[400px]"
              onClick={(e) => e.stopPropagation()}
            >
              <DailyRoutines
                connected={connected}
                onRequestRoutines={() => send({ event: 'ROUTINES_REQUEST' })}
                checkin={pacingCheckin}
                onStartBreak={(kind) => ackPacing('start_break', kind)}
                onSnooze={() => ackPacing('snooze')}
                compact
                snoozeMinutes={pacing?.snooze_minutes}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Proactive Context Prompt Card (Floating top right when insight detected) */}
        <ProactiveInsightCard
          insights={proactiveInsights}
          onDismiss={dismissInsight}
          onAskOrion={(prompt) => {
            handleSendPrompt(prompt);
            openChat();
          }}
        />

        {/* =========================================================================
            CENTER STAGE: ORION IS THE HERO OF THE SCREEN
            With integrated arc waveform, gravitational lensing, and gestural radial triggers.
           ========================================================================= */}
        <div
          className="relative flex flex-1 items-center justify-center overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Exact Orion Cosmic Core with Integrated Arc Waveform & Luminescent Telemetry Jewels */}
          {/* Arrival: the camera eases in on the core. `data-orion-core` lets glass windows find the hole. */}
          <div className="orion-arrive-camera relative flex items-center justify-center my-auto">
          {/* [look] the core leans a few pixels toward the pointer (lib/parallax startPointerVars) */}
          <div className="orion-gravity">
          <motion.div
            data-orion-core
            className="relative flex flex-col items-center justify-center"
            animate={{ x: coreShift, scale: coreScale }}
            transition={{ type: 'spring', stiffness: 140, damping: 22 }}
          >
            <Singularity
              mode={galacticMode}
              state={coreState}
              analyser={effectiveAnalyser}
              broadcastWaveform={waveformBroadcast}
              size={coreSize}
              onCoreClick={handleOrionClick}
              pacing={pacing}
              highlightCount={bible.producer?.highlights.length ?? 0}
              caption={coreCaption}
              skin={bible.xp?.skin}
              swarm={bible.momentum?.swarm ?? 0}
              debris={bible.momentum?.debris ?? []}
              onDebrisClick={(tab) => {
                showTab({ tab: (tab === 'life' ? 'life' : tab === 'tools' ? 'tools' : 'queue') as ContextTab, nonce: Date.now() });
              }}
              swallow={swallow}
            />
            <VipSatellites
              threads={commsThreads}
              size={coreSize}
              onOpen={(threadKey) => {
                showTab({ tab: 'comms', nonce: Date.now(), threadKey });
              }}
            />
            <AgentSwarm size={coreSize} />
          </motion.div>
          </div>
          </div>

          {mobile && !dialogueOpen && (
            <div className="absolute inset-x-0 bottom-[150px] flex justify-center z-30">
              <MobileGlance
                bible={bible}
                send={bibleSend}
                threads={commsThreads}
                approvals={approvals}
                onOpen={(tab) => {
                  showTab({ tab, nonce: Date.now() });
                }}
              />
            </div>
          )}

          {/* Kinetic Floating Spoken Words in Space */}
          <FloatingVoiceTranscript
            active={micActive}
            transcript={spokenTranscript}
            onStop={handleToggleMic}
            onSubmit={handleVoiceSubmit}
          />
        </div>

        {/* =========================================================================
            BOTTOM-LEFT EXPANDING EARTH CONTEXT WINDOW & TOOLS HUB
            Features clean Earth button with wrench core that expands into the orbital
            context window hosting Chat History, Projects, and Tools Hub.
           ========================================================================= */}
        <div onClick={(e) => e.stopPropagation()}>
          <EarthContextWindow
            isOpen={dialogueOpen}
            onToggleOpen={setDialogueOpen}
            responses={responses}
            feed={feed}
            memories={memories}
            onSendMessage={handleSendPrompt}
            isProcessing={pending.length > 0}
            currentTask={currentTaskText}
            activeWindow={activeWindow}
            onSendTelegram={sendTelegram}
            micActive={micActive}
            onToggleMic={handleToggleMic}
            activePersonality={personalityMode}
            onSetPersonality={setPersonality}
            latestBrowserTab={latestBrowserTab}
            onSelectTool={handleSelectTool}
            activeWidgets={widgets}
            telemetry={telemetry}
            approvals={approvals}
            selfEvolveStatus={selfEvolveStatus}
            trainingExport={trainingExport}
            commsThreads={commsThreads}
            commsDrafts={commsDrafts}
            commsReplyResult={commsReplyResult}
            onDecideApproval={decideApproval}
            onPatchDecision={decidePatch}
            onRequestSelfEvolve={(request) => {
              setCurrentTaskText(`Self-evolve: "${request.slice(0, 40)}..."`);
              requestSelfEvolve(request);
            }}
            connected={connected}
            onCommsReply={commsReply}
            onRequestCommsDraft={requestCommsDraft}
            onMarkCommsRead={markCommsRead}
            onConsolidateMemories={() => {
              setCurrentTaskText('Consolidating memories & exporting fine-tune dataset...');
              consolidateMemories();
            }}
            bible={bible}
            bibleSend={bibleSend}
            focusTab={focusTab}
            mobile={mobile}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        </div>

        {/* Glass taskbar (desktop only): just the windows that are open; it moves aside for the Orion button. */}
        {!mobile && taskbarTiles().length > 0 && <Taskbar tiles={taskbarTiles()} style={{ left: inTaskbarCorner(orionPos, window.innerHeight) ? 86 : 16 }} />}
        {/* [look] the one Orion button and the full-screen command console (menu + chat in one) */}
        <CommandConsole
          mobile={mobile}
          badges={{ queue: approvals.filter((a) => a.status === 'pending').length, messages: commsThreads.filter((t) => !t.muted).reduce((n, t) => n + (t.unread || 0), 0) }}
          responses={responses}
          onSendMessage={handleSendPrompt}
          isProcessing={pending.length > 0}
          currentTask={currentTaskText}
          micActive={micActive}
          onToggleMic={handleToggleMic}
          activePersonality={personalityMode}
          onSetPersonality={setPersonality}
          onSendTelegram={sendTelegram}
          onOpenThread={(threadKey, draft) => showTab({ tab: 'comms', nonce: Date.now(), threadKey, draft })} // [focus] Open / Draft reply (never sends)
          onConsolidateMemories={() => {
            setCurrentTaskText('Consolidating memories & exporting fine-tune dataset...');
            consolidateMemories();
          }}
        />
        <OrionButton badge={approvals.filter((a) => a.status === 'pending').length} />
        <StatusStrip waiting={approvals.filter((a) => a.status === 'pending').length} camera={!mobile} />
        <div onClick={(e) => e.stopPropagation()}>
          <AnimatePresence>
            {settingsOpen && !wm.minimised.settings && (
              <HudSettingsWindow key="settings" mobile={mobile} onClose={() => setSettingsOpen(false)} />
            )}
          </AnimatePresence>
        </div>

        {/* Right-hand widgets: music, unified messages, alerts (tuck away when the panel needs the room) */}
        <AnimatePresence>
          {!mobile && sideWidgets && !(dialogueOpen && viewportW < 1500) && (
            <motion.div
              key="widget-dock"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              className="fixed top-12 right-4 z-40 max-h-[calc(100vh-200px)] overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
              <WidgetDock
                bible={bible}
                send={bibleSend}
                threads={commsThreads}
                approvals={approvals}
                pacing={pacing}
                connected={connected}
                onOpen={(t) => {
                  showTab({ tab: t.tab, nonce: Date.now(), threadKey: t.threadKey, draft: t.draft });
                }}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Coding-agent pet: naps until Claude starts working */}
        {sideWidgets && !(mobile && dialogueOpen) && (
          <div className={mobile ? 'fixed bottom-16 right-3 z-40 origin-bottom-right scale-[0.8]' : 'fixed bottom-16 right-5 z-40'}>
            <AgentPet agent={bible.agent} />
          </div>
        )}

        {!mobile && sideWidgets && <div className="fixed bottom-16 right-[104px] z-40"><ComputeMeter /></div>}

        {/* Feature Bible floating surfaces (clipboard alchemy, voice replies, assets, breathwork…) */}
        <div onClick={(e) => e.stopPropagation()}>
          <BibleOverlays bible={bible} send={bibleSend} patch={biblePatch} />
        </div>

        {/* =========================================================================
            FLOATING WIDGETS LAYER
            Independent concept widgets (Agents, Analytics, Chrome, Discord, Screen Tasks, Game Bugs, Personality)
           ========================================================================= */}
        <div onClick={(e) => e.stopPropagation()}>
          <FloatingWidgetsLayer
            widgets={widgets}
            onToggleWidget={handleToggleWidget}
            telemetry={telemetry}
            agents={agents}
            onToggleAgent={handleToggleAgent}
            activeWindow={activeWindow}
            latestBrowserTab={latestBrowserTab}
            clients={clients}
            onTriggerPrompt={handleSendPrompt}
            onDeepScrape={handleDeepScrape}
            onImportDiscord={handleImportDiscord}
            isDiscordSkinActive={isDiscordSkinActive}
            onToggleDiscordSkin={() => setIsDiscordSkinActive(!isDiscordSkinActive)}
            workflowProgress={workflowProgress}
            onExecuteWorkflow={executeScreenWorkflow}
            onExecuteAction={executeScreenAction}
            gameBugs={gameBugs}
            onSendDiscordBugResponse={sendDiscordBugResponse}
            onSendTelegram={sendTelegram}
            personality={personalityMode}
            onSetPersonality={setPersonality}
            ttsVoices={ttsVoices}
            ttsSettings={ttsSettings}
            onSetTtsVoice={setTtsVoice}
          />
        </div>
      </div>
    </GlobalDropAndPasteZone>
  );
}
