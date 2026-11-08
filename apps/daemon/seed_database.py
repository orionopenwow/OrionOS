"""Orion Massive Initial Database Seeder.

Injects a comprehensive, multi-domain foundational knowledge base into LanceDB (~/.orion/memory_db).
Covers:
  1. Orion Lore, Architecture, Directives & Personality
  2. Windows Desktop Systems, PowerShell & CLI Mastery
  3. Software Engineering, Modern React/TypeScript, Python & Rust
  4. AI, Vector Embeddings, RAG & Local SLM Routing
  5. Browser Automation via Chrome DevTools Protocol (CDP)
  6. Game Development, Graphics Shaders & Bug Triage
  7. Astrophysics, Stellar Evolution & Orion Constellation Cartography
  8. Autonomous Productivity Workflows & Daily Routines
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import List, Tuple

from memory_core import MemoryCore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("orion.seed")

# ==============================================================================
# 1. ORION CORE LORE, DIRECTIVES & ARCHITECTURAL FOUNDATION
# ==============================================================================
ORION_LORE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "Orion Identity:\n"
        "Orion is a desktop assistant that works quietly in the background and helps when asked. "
        "Orion balances silent, non-intrusive background perception "
        "with quick, decisive action. Orion speaks in plain, normal words with no space, sci-fi or fantasy flavour; "
        "its manner is calm, confident, friendly and loyal, preferring clear answers over chatter.",
    ),
    (
        "consolidated",
        "The Three Directives of Orion:\n"
        "Directive 1: Ambient Non-Intrusion — Observe silently through perceptual screen deltas (pHash) and active "
        "application focus. Never steal user focus or produce disruptive popups unless an urgent issue arises.\n"
        "Directive 2: Local-First Sovereignty — All screen captures, OCR data, vector memories, and local SLM queries "
        "remain strictly on the user's local machine. Cloud failover is used only when complex frontier reasoning is needed.\n"
        "Directive 3: Continuous Memory Evolution — Ingest daily activities, screen context, and discussions into LanceDB; "
        "consolidate open loops during nightly routines so memory grows richer and sharper over time.",
    ),
    (
        "consolidated",
        "Orion Dual-Tier Orchestration Architecture:\n"
        "Tier 1 (Local SLM): Powered by local models via Ollama (such as Llama 3.2 3B or Qwen 2.5 7B). Handles instant "
        "summaries, intent classification, entity extraction, and simple conversational replies sub-200ms with zero cloud cost.\n"
        "Tier 2 (Cloud Frontier): Dispatches complex coding tasks, multi-step macros, and deep architectural reasoning "
        "to frontier models (Gemini 1.5 Flash/Pro, Claude 3.5 Sonnet, GPT-4o) with strict Pydantic JSON schema validation "
        "and automatic error repair.",
    ),
    (
        "consolidated",
        "Orion Perceptual Screen Vision Subsystem:\n"
        "Orion uses Windows GDI BitBlt to capture the primary display at 5-second intervals. It computes a 64-bit perceptual "
        "hash (pHash) using imagehash. If the Hamming distance delta is below threshold 5 and the focused window has not "
        "changed, the frame is visually identical and OCR/LLM processing is bypassed. This saves over 85% of GPU/CPU cycles "
        "and thousands of tokens per hour.",
    ),
    (
        "consolidated",
        "Orion Persistent Vector Memory Core:\n"
        "Built on LanceDB, a serverless, persistent, columnar vector database backed by Apache Arrow. Vectors are normalized "
        "to 1536 dimensions. Cosine similarity is computed as 1 - (distance / 2). Memories are partitioned across six sources: "
        "'ocr', 'browser', 'voice', 'discord', 'user', and 'consolidated'.",
    ),
    (
        "consolidated",
        "Orion Visual Hierarchy & Portal Aesthetics:\n"
        "Orion's visual embodiment consists of: (1) Back Ring Arc at z-index 1; (2) Flat 2D Vortex Core spinning with "
        "internal cosmic gas texture at z-index 2; (3) Gold Bezel Frame at z-index 3; (4) Front Purple Ring Arc at z-index 4, "
        "traversing diagonally with gyroscopic wobble (rotateZ -25deg, rotateX 60deg); (5) Mirrored Audio Waveform at z-index 5. "
        "Procedural canvas particles render stars and shooting comets without texture boundary artifacts.",
    ),
    (
        "consolidated",
        "Orion Real-Time Voice Subsystem:\n"
        "Orion features full-duplex speech interaction. Input speech is captured through Web Speech API and streaming faster-whisper "
        "with WebRTC VAD, displaying spoken words as floating ethereal kinetic typography directly in space beside Orion. "
        "Output speech uses native local zero-cloud OS text-to-speech (pyttsx3 / SAPI5) with adjustable rate and volume.",
    ),
]

# ==============================================================================
# 2. WINDOWS SYSTEMS ENGINEERING & POWERSHELL AUTOMATIONS
# ==============================================================================
WINDOWS_KNOWLEDGE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "PowerShell Process & Service Automation:\n"
        "To inspect high-resource processes: `Get-Process | Sort-Object CPU -Descending | Select-Object -First 10`.\n"
        "To gracefully stop hung processes: `Stop-Process -Name <proc> -Force`.\n"
        "To query Windows services: `Get-Service | Where-Object {$_.Status -eq 'Running'}`.\n"
        "To restart a stubborn background service: `Restart-Service -Name <service_name> -Force`.",
    ),
    (
        "consolidated",
        "Windows Network Diagnostics & DNS Tuning:\n"
        "Flush DNS cache: `Clear-DnsClientCache` (or `ipconfig /flushdns`).\n"
        "Test port and TCP socket reachability: `Test-NetConnection -ComputerName 127.0.0.1 -Port 9120`.\n"
        "Inspect active TCP listening ports and PID mapping: `netstat -ano | findstr LISTENING`.\n"
        "Kill process hogging a specific port: `Stop-Process -Id (Get-NetTCPConnection -LocalPort 9120).OwningProcess -Force`.",
    ),
    (
        "consolidated",
        "Windows Registry & Startup Optimization:\n"
        "Inspect startup programs in registry: `Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'`.\n"
        "System-wide startup entries: `Get-ItemProperty -Path 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'`.\n"
        "Always ensure backups before modifying registry keys via `reg export` or PowerShell `Copy-Item`.",
    ),
    (
        "consolidated",
        "Windows GDI BitBlt & Display Subsystem Quirks:\n"
        "When the Windows desktop is locked, in a UAC secure desktop prompt, or in a headless RDP session, the graphics "
        "subsystem disables GDI BitBlt, causing `mss.exception.ScreenShotError`. Orion handles this gracefully by detecting "
        "the GDI lock, sleeping 1.0 second, and resuming capture automatically when the user unlocks the desktop.",
    ),
    (
        "consolidated",
        "Essential Windows Power Shortcuts:\n"
        "- Win + V: Clipboard history cloud/local toggle.\n"
        "- Win + Shift + S: Snipping Tool selective region screen capture.\n"
        "- Win + Ctrl + D: Create instant virtual desktop; Win + Ctrl + Left/Right: Navigate virtual desktops.\n"
        "- Ctrl + Shift + Esc: Direct Task Manager launch.\n"
        "- Win + X: Power User quick menu (Terminal Admin, Disk Management, Device Manager).",
    ),
    (
        "consolidated",
        "PowerShell File & Directory Bulk Operations:\n"
        "Recursively search files by extension: `Get-ChildItem -Path . -Recurse -Filter '*.log' | Where-Object {$_.Length -gt 10MB}`.\n"
        "Compute SHA256 checksum: `Get-FileHash -Algorithm SHA256 .\\archive.zip`.\n"
        "Monitor directory changes live: Use `System.IO.FileSystemWatcher` with registered `Created`, `Changed`, and `Deleted` events.",
    ),
]

# ==============================================================================
# 3. SOFTWARE ARCHITECTURE, CODING & MODERN PARADIGMS
# ==============================================================================
CODING_KNOWLEDGE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "TypeScript Clean Architecture & Strict Types:\n"
        "Always favor union types over enums for bundle size and tree-shaking efficiency. Use `as const` assertions for "
        "immutable lookup maps. Utilize mapped types and conditional types `type NonNullable<T> = T extends null | undefined ? never : T` "
        "to ensure strict null safety without unnecessary runtime overhead.",
    ),
    (
        "consolidated",
        "React Performance & Framer Motion Integration:\n"
        "Wrap complex animation properties in `useMotionValue` and `useSpring` rather than triggering React component re-renders. "
        "Isolate heavy canvas backdrops into self-contained components using `requestAnimationFrame` and direct 2D context "
        "buffers rather than React state hooks. Use `React.memo` on static UI frames.",
    ),
    (
        "consolidated",
        "FastAPI & High-Throughput Asynchronous IPC:\n"
        "Never run blocking CPU-bound or synchronous I/O operations directly in an async FastAPI route. Offload them to "
        "`asyncio.get_running_loop().run_in_executor(None, fn)` to prevent blocking the event loop. For WebSocket hubs, "
        "maintain thread-safe client registries with heartbeat pings every 30 seconds to drop stale connections.",
    ),
    (
        "consolidated",
        "Pydantic v2 Schema Validation & JSON Repair:\n"
        "In LLM pipelines, model responses can occasionally suffer from truncated JSON or invalid markdown wrapping. "
        "Strip leading/trailing markdown code fences ````json...````, find the outermost `{` and `}`, and parse. If validation fails, "
        "perform a single corrective round-trip prompting the model with the exact validation error array.",
    ),
    (
        "consolidated",
        "Tauri v2 Desktop Architecture:\n"
        "Tauri combines a web frontend with a high-performance native Rust core. It avoids Chromium's heavy RAM footprint by "
        "leveraging the platform's native webview (WebView2 on Windows). IPC invokes between JavaScript and Rust run through "
        "asynchronous binary-serialized messages with zero-cost memory mapping.",
    ),
    (
        "consolidated",
        "Headless Chrome DevTools Protocol (CDP) & Playwright Automation:\n"
        "CDP allows deep programmatic control over Chromium browser sessions. Orion's CDP bridge connects to headless "
        "Chromium to evaluate JavaScript, click complex DOM elements, extract sanitized page innerText up to 8,000 characters, "
        "and bypass bot detection using `--disable-blink-features=AutomationControlled`.",
    ),
    (
        "consolidated",
        "AST (Abstract Syntax Tree) Inspection & Automated Code Repairs:\n"
        "When Orion detects an error in the active IDE, it analyzes the call stack and surrounding code block. It parses "
        "the syntax tree to isolate the target function, determines type signatures and variable scope, and constructs a "
        "surgical 1-click replacement snippet preserving original comments and formatting.",
    ),
]

# ==============================================================================
# 4. AI, EMBEDDINGS, VECTOR SEARCH & SLM ROUTING
# ==============================================================================
AI_KNOWLEDGE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "Vector Embeddings & Semantic Search Mathematics:\n"
        "Text embeddings map semantic meaning into dense high-dimensional geometric space. For unit-normalized vectors A and B, "
        "cosine similarity equals their dot product: `cos(θ) = A · B`. In LanceDB with Euclidean distance d, cosine similarity "
        "is derived as `cos = 1 - (d^2 / 2)`. Zero-padding lower-dimension vectors (e.g. 384 or 768 up to 1536) strictly preserves "
        "orthogonal dot product relationships.",
    ),
    (
        "consolidated",
        "Quantization & Local SLM Inference:\n"
        "Modern Small Language Models (SLMs) like Llama 3.2 (1B and 3B) and Qwen 2.5 (3B and 7B) achieve near-frontier reasoning "
        "using 4-bit and 8-bit quantization (GGUF Q4_K_M, Q8_0). On modern CPUs with AVX-512 and GPUs with FP16 tensor cores, "
        "they generate 40-90 tokens per second with less than 3GB VRAM footprint.",
    ),
    (
        "consolidated",
        "Retrieval-Augmented Generation (RAG) Best Practices:\n"
        "1. Chunking: Keep chunks between 300-800 characters with 10% overlap to preserve sentence boundaries.\n"
        "2. Pre-filtering: Filter vector queries by metadata ('source' or time threshold) before vector distance scanning.\n"
        "3. Re-ranking: When query results have similar cosine distances, prioritize recency and source reliability.\n"
        "4. Context Injection: Format injected memories clearly with source and score tags for the orchestrator.",
    ),
]

# ==============================================================================
# 5. ASTROPHYSICS, STELLAR EVOLUTION & ORION CONSTELLATION
# ==============================================================================
ASTROPHYSICS_KNOWLEDGE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "The Constellation Orion — Anatomy & Stellar Jewels:\n"
        "Orion is among the most recognizable and brilliant constellations in the night sky, straddling the celestial equator. "
        "Notable stars include Betelgeuse (Alpha Orionis, distinct red supergiant marking the right shoulder), Rigel (Beta Orionis, "
        "immensely luminous blue-white supergiant marking the left foot), Bellatrix (Gamma Orionis), and Saiph (Kappa Orionis).",
    ),
    (
        "consolidated",
        "Orion's Belt — The Three Kings:\n"
        "Orion's Belt is formed by an exquisite, almost straight asterism of three blue supergiant stars: Alnitak (Zeta Orionis), "
        "Alnilam (Epsilon Orionis), and Mintaka (Delta Orionis). Spanning approximately 3 degrees of arc, they lie between 1,200 "
        "and 2,000 light-years from Earth and serve as celestial waypoints to locate Sirius to the southeast and Aldebaran to the northwest.",
    ),
    (
        "consolidated",
        "The Great Orion Nebula (Messier 42 / NGC 1976):\n"
        "Located in Orion's Sword just south of the belt, M42 is one of the closest and most active stellar nurseries to Earth "
        "(1,344 light-years). At its heart lies the Trapezium Cluster, four massive young O-type and B-type stars whose intense "
        "ultraviolet radiation ionizes the surrounding hydrogen gas cloud, creating brilliant emission and reflection nebulae.",
    ),
    (
        "consolidated",
        "Betelgeuse & Supernova Evolution:\n"
        "Betelgeuse is a semi-regular pulsating red supergiant with a radius exceeding 700 times that of our Sun. Having exhausted "
        "hydrogen in its core, it is currently fusing helium and heavier elements. When its iron core collapses, it will explode "
        "as a Type II supernova, shining brighter than the full moon and visible in daylight for weeks.",
    ),
    (
        "consolidated",
        "Horsehead Nebula (Barnard 33):\n"
        "A dark absorption nebula located just south of Alnitak in Orion's belt. Formed of dense cosmic dust and cold gas, it is "
        "silhouetted against the bright pink hydrogen emission nebula IC 434. The dust swirl resembles a horse's head rising from "
        "stellar clouds.",
    ),
    (
        "consolidated",
        "Cosmic Physics & Gravitational Waves:\n"
        "Gravitational waves are ripples in the fabric of spacetime caused by asymmetric accelerated masses, such as merging binary "
        "black holes or neutron stars. First directly detected by LIGO in 2015 (GW150914), they travel at the speed of light, "
        "carrying untainted information about the universe's most extreme cosmic events.",
    ),
]

# ==============================================================================
# 6. AUTONOMOUS PRODUCTIVITY WORKFLOWS & ROUTINE RECIPES
# ==============================================================================
ROUTINES_KNOWLEDGE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "Routine Recipe: Autonomous Morning Developer Standup:\n"
        "Trigger: Routine Optimizer on morning boot.\n"
        "Steps: (1) Query LanceDB for git commits and active window titles from past 24 hours; (2) Group actions into: What was completed, "
        "What is currently in progress, and What blockers exist; (3) Format into 3 concise bullet points ready for team updates.",
    ),
    (
        "consolidated",
        "Routine Recipe: Code Review & Quality Assurance Checklist:\n"
        "1. Check bounds and edge cases (null/undefined checks, empty arrays, division by zero).\n"
        "2. Validate async error handling (try/catch, unhandled promise rejections, finally cleanup).\n"
        "3. Security: Check for injection vectors, unsanitized innerHTML, secret leaks in client bundles.\n"
        "4. Performance: Identify memory leaks, non-memoized heavy renders, unbounded loops.\n"
        "5. Maintain documentation integrity and preserve unrelated existing comments.",
    ),
    (
        "consolidated",
        "Routine Recipe: Deep Work Focus Sanctuary Protocol:\n"
        "When user opens an IDE (VS Code, Cursor, IntelliJ) and types actively for >15 minutes:\n"
        "- Mute non-critical OS notifications.\n"
        "- Suppress speculative Orion popups.\n"
        "- Buffer incoming Discord or browser notifications in memory.\n"
        "- When user switches to browser or pauses for >5 minutes, present a quiet single-bullet summary of queued items.",
    ),
    (
        "consolidated",
        "Routine Recipe: Nightly Memory Consolidation & Pruning:\n"
        "Executed at 3:00 AM daily by the daemon:\n"
        "- Retrieve all OCR and browser records from the past 24 hours.\n"
        "- Run clustering to merge repetitive window deltas into high-level project milestones.\n"
        "- Write consolidated summary into LanceDB with source='consolidated'.\n"
        "- Retain raw OCR records for 7 days before pruning to maintain ultra-fast vector index performance.",
    ),
    (
        "consolidated",
        "Routine Recipe: Discord Bug & Feedback Triage Pipeline:\n"
        "When messages arrive in watched Discord channels:\n"
        "- Evaluate text against actionable regex (bug, crash, exception, repro steps, feature request).\n"
        "- Extract reproduction steps, OS version, and game/app build number.\n"
        "- Format as structured GameBugRecord with priority rating (P0/P1/P2/P3).\n"
        "- Ingest into LanceDB and surface in the Game Bug Tracker Hub.",
    ),
]

# ==============================================================================
# 9. OPENWOW ARCHITECTURE, REALM SYSTEMS & ORION CARETAKER KNOWLEDGE
# ==============================================================================
OPENWOW_KNOWLEDGE: List[Tuple[str, str]] = [
    (
        "consolidated",
        "OpenWoW Overview & 'Vanilla Remastered' Philosophy:\n"
        "OpenWoW is a free, open-source home for World of Warcraft 1.12 playable straight from a web browser, plus all "
        "tooling needed to mod, host, and nurture a community around it. Pitch is 'Vanilla Remastered': the classic 1.12 "
        "game preserved with modern conveniences and original new content, not a cash grab. Currently in private alpha with "
        "Discord testers. Players bring their own 1.12 game files; OpenWoW never ships or modifies proprietary assets.",
    ),
    (
        "consolidated",
        "OpenWoW Client Runtimes & Ways to Play:\n"
        "1. Browser Client: WebGPU / Rust rebuild of the 1.12 client (the 'wenilla' engine based on 'benilla'), served directly "
        "by the realm. Playable in any web tab on desktop or mobile WebGPU devices; installable to home screen as a PWA.\n"
        "2. Desktop Launcher (OpenWoW.exe): Selects realm and runtime engine (OpenWoW WebGPU, in-browser, or stock 1.12 WoW.exe). "
        "Manages realmlist, auto-updates, addon management, and support desk.\n"
        "3. Solo / Offline: Integrated setup of SPP Classics (CMaNGOS with playerbots) on local PC for zero-latency single-player.\n"
        "4. Host Your Own: Peer-to-peer invite links ('Host for friends') or full standalone dedicated realm with website and Server Studio.",
    ),
    (
        "consolidated",
        "OpenWoW Sign-in & Authentication Architecture (v1.16.1):\n"
        "Features an atmospheric themed login page with one-tap Discord OAuth2 sign-in, persistent 60-day session cookies, and "
        "'Continue as...' fast-resume. Includes free guest accounts allowing new players to jump straight into gameplay without "
        "signing up; guest progress and characters can be linked to a Discord account later with complete state preservation.",
    ),
    (
        "consolidated",
        "OpenWoW Classic+ Realm Content & Systems:\n"
        "- Expanded modern race/class combinations with correctly scaled starting equipment, weapon proficiencies, and stats.\n"
        "- Better defaults: native auto-looting, instant quest dialogue text, optimized camera angles.\n"
        "- Stardust Economy: universal cosmetic currency traded at Vaelis the Starwright's cosmic vendor located outside Stormwind and Orgrimmar banks.\n"
        "- Progression QoL: auto-learned spells/skills upon leveling up, free talent respecs, dual-spec switching, starter hunter pet, "
        "AoE area looting, smoothed leveling curve, 388 vanilla achievements, authentic WoW loading screens, and class-grid fixes.",
    ),
    (
        "consolidated",
        "OpenWoW Native Addon Suite:\n"
        "All addons are active by default with authentic classic WoW UI styling and can be toggled on/off individually:\n"
        "- Combat & Raids: Meters (Details!-grade damage/healing), Threat, Casting bars, Raid unit frames, Nameplates.\n"
        "- Questing: Quests (minimap tracker ring, quest POI pins, searchable log, corpse arrow).\n"
        "- Items: Bags (unified view with gear sets), Prices, Vendor junk selling, Mail batching, Loot tracking.\n"
        "- Social: Social, Guilds, Guild Hub (rosters, calendar, DKP, loot council), Chat (themes, spam filter), Dungeons (LFG board).\n"
        "- Character: Persona (roleplay profiles with artwork), Talents planner, 388 Achievements, Essentials (cooldowns, range colors).",
    ),
    (
        "consolidated",
        "OpenWoW Creator Studio & The Model Porter:\n"
        "Creator studio tool within OpenWoW: drop a rigged .glb model or modern .m2 asset to automatically convert it into a 1.12 "
        "creature or rideable mount complete with skeleton, bone weights, animations, and textures. Features an interactive 3D WebGPU "
        "preview and one-click 'Add to OpenWoW'. Supported by The Workshop mod marketplace for addons and repacks. Strict tip-jar "
        "rule: contributions never buy player power, items, or game time.",
    ),
    (
        "consolidated",
        "OpenWoW Continuous Testing & The Herald Test Bot:\n"
        "Automated continuous delivery: Ship.bat stages updates, Release.bat packages releases, and realm container images rebuild. "
        "Web and addon fixes deploy within ~60 seconds. Testing is spearheaded by Herald: an automated Human Hunter bot that logs "
        "into the live realm nightly inside a headless browser to test questing, combat, pathfinding, and inventory. Herald Lite "
        "executes pre-flight verification on staging before updates hit production.",
    ),
    (
        "consolidated",
        "Orion as OpenWoW Realm Caretaker & The 'Green OK Button' Principle:\n"
        "Orion acts as OpenWoW's autonomous AI maintainer and realm caretaker:\n"
        "- Ingests and analyzes Herald nightly playtest reports, pinpointing regressions and packet anomalies.\n"
        "- Classifies community Discord bug reports (P0-P3) and formulates automated fix PRs in sandboxed worktrees.\n"
        "- Drafts RFC proposals and balance tweaks for transparent community voting.\n"
        "- Human Sovereignty ('Green OK Button'): Orion operates under earned autonomy, but can NEVER approve its own work. "
        "All code merges to main, production deployments, monetary transactions, and player bans require human sign-off via the "
        "isolated Boardroom VM.",
    ),
]


async def seed_knowledge_base(memory: MemoryCore, force: bool = False) -> int:
    """Injects the full curated Orion foundational database into LanceDB."""
    current_count = memory.count()
    if current_count > 10 and not force:
        log.info("Knowledge base already seeded (%d records present). Skipping.", current_count)
        return current_count

    all_records: List[Tuple[str, str]] = (
        ORION_LORE
        + WINDOWS_KNOWLEDGE
        + CODING_KNOWLEDGE
        + AI_KNOWLEDGE
        + ASTROPHYSICS_KNOWLEDGE
        + ROUTINES_KNOWLEDGE
        + OPENWOW_KNOWLEDGE
    )

    log.info("Beginning batch ingestion of %d foundational knowledge records...", len(all_records))
    ingested = await memory.ingest_batch(all_records)
    log.info("Successfully seeded %d knowledge records into LanceDB. New total: %d", ingested, memory.count())
    return memory.count()


if __name__ == "__main__":
    async def main():
        mem = MemoryCore()
        await seed_knowledge_base(mem, force=True)

    asyncio.run(main())
