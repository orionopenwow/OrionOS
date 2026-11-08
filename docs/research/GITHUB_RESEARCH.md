# OrionOS: GitHub open-source survey for new features

Research date: 7 November 2026. Research only. No Orion repository was edited.

---

## Executive summary

- **Scope.** About 720 GitHub repository URLs were checked with a shallow git clone to get the last commit date and the licence file. Some of these are old names that redirect to the same repo, and 44 guessed URLs did not exist. On top of that, 30 GitHub topic pages were read, showing about 600 more repos with star counts. **96 repos made the shortlist** (section 4). The full table of checked repos is in the appendix.
- **Orion already covers a lot.** The Orion code already uses or wraps: faster-whisper, Kokoro, openWakeWord, webrtcvad, LanceDB, Tesseract, yt-dlp, rembg, MediaPipe, restic, snapper, ufw, minisign, Tailscale, mDNS through Android NsdManager, LocalSend, wtype, mako, gitleaks plus its own secret shield, an injection firewall, a collision guard and an MCP server. Section 3 lists what not to rebuild.
- **Best value for the effort** (full top 25 in section 5):
  1. **Swap the phone's polling for push and race several connection paths.** Use the UnifiedPush protocol through ntfy or Sunup, ConnectivityManager callbacks, and racing between LAN, mDNS and Tailscale addresses.
  2. **Add `anthropic-experimental/sandbox-runtime` (bubblewrap) around Mission Control agents.**
  3. **Add `silero-vad` and `pipecat-ai/smart-turn`** so Orion knows when the speaker has finished.
  4. **Make the phone wire format KDE Connect compatible** (Valent and kcd show the protocol), so the phone also works with existing KDE Connect clients.
  5. **Use `systemd/mkosi` together with `systemd-sysupdate`, or `silitics/rugix`,** for the A/B image.
  6. **Use `sherpa-onnx` and `moonshine`** for STT on the phone and on low-end machines.
  7. **Use `RapidOCR` or PaddleOCR** in place of Tesseract for screen OCR.
  8. **Use `sqlite-vec`** as a light second vector store.
  9. **Use `llama-swap`** to manage several local models.
  10. **Use `godogen` and the Godot MCP servers** for "make a game with Claude".
  11. **Use `swhkd` or keyd** for compositor-independent hotkeys.
  12. **Use `syft`, `grype` and `cosign`** to give signed updates an SBOM.
- **The phone–PC link (section 7).** Today it uses a 15-minute JobScheduler poll plus an optional foreground poller. The biggest gains are:
  - a push wake-up through UnifiedPush or ntfy, done with intents so no library is needed;
  - re-checking connections the moment the network changes;
  - racing all known PC addresses;
  - a self-hosted Headscale server, with an iroh or DERP relay as fallback;
  - mDNS plus UDP-broadcast discovery, the way KDE Connect does it.
- **Licence traps to avoid.** Several popular projects are not freely licensed: screenpipe now uses a commercial licence, plus Arize Phoenix (Elastic), GitButler (FSL), tldraw, Remotion, Fish-Speech, IndexTTS, Hunyuan3D (which excludes the EU and UK), Anytype, the RunAnywhere SDKs and Claude Code itself (proprietary). The AGPL ones are fine to run as separate services, but must not be linked into Orion's core: claude-squad, trufflehog, the mautrix bridges, RustDesk and LibreTranslate.

---

## 1. Method and how far to trust the numbers

- **GitHub's REST API and plain github.com pages were blocked** from this sandbox (HTTP 403). Three methods were used instead:
  1. **Activity.** `git clone --depth 1 --filter=blob:none` on each repo, then `git log -1`. This gives the last commit date on the default branch, checked on 2026-11-07. It is reliable.
  2. **Licence.** The repo's root licence file(s) were read and matched against known licence texts. Unclear cases were read by hand, including mkosi, river, pgvector, Joplin, screenpipe, tldraw, Remotion, Fish-Speech, ZeroTier, Moonshine, GitButler and IndexTTS. This is reliable for the repo's code, but **model weights often carry a separate licence**, which this survey did not fully check.
  3. **Stars.** These come from GitHub topic pages and repo pages fetched through a web-reader tool on 2026-11-07. The numbers are rounded as GitHub shows them (for example "12k"). Topic pages sometimes showed a "last updated" date a few weeks old, which looks like caching. Where a row says **"★ n/v"**, the star count was not verified. Activity and licence were still checked for that row.
- Star counts on 2026 AI-tooling repos look inflated in places. For example, the `claude-code` topic lists skill packs with 100k–245k stars ([topic page](https://github.com/topics/claude-code?o=desc&s=stars)). Do not treat stars as a quality signal for that category.
- **Topic pages used as sources:**
  - Desktop, devices and voice: [wayland](https://github.com/topics/wayland?o=desc&s=stars), [quickshell](https://github.com/topics/quickshell?o=desc&s=stars), [kdeconnect](https://github.com/topics/kdeconnect?o=desc&s=stars), [scrcpy](https://github.com/topics/scrcpy?o=desc&s=stars), [android-launcher](https://github.com/topics/android-launcher?o=desc&s=stars), [voice-assistant](https://github.com/topics/voice-assistant?o=desc&s=stars), [speech-to-text](https://github.com/topics/speech-to-text?o=desc&s=stars), [speech-recognition](https://github.com/topics/speech-recognition?o=desc&s=stars), [tts](https://github.com/topics/tts?o=desc&s=stars), [text-to-speech (recent)](https://github.com/topics/text-to-speech?o=desc&s=updated).
  - AI, documents and inference: [ocr](https://github.com/topics/ocr?o=desc&s=stars), [vector-database](https://github.com/topics/vector-database?o=desc&s=stars), [llm-inference](https://github.com/topics/llm-inference?o=desc&s=stars), [llama-cpp](https://github.com/topics/llama-cpp?o=desc&s=stars), [on-device-ai](https://github.com/topics/on-device-ai?o=desc&s=stars), [mcp-server](https://github.com/topics/mcp-server?o=desc&s=stars), [computer-use](https://github.com/topics/computer-use).
  - Coding agents and security: [claude-code](https://github.com/topics/claude-code?o=desc&s=stars), [git-worktrees](https://github.com/topics/git-worktrees), [code-review](https://github.com/topics/code-review?o=desc&s=stars), [prompt-injection](https://github.com/topics/prompt-injection?o=desc&s=stars), [sandbox (stars)](https://github.com/topics/sandbox?o=desc&s=stars), [sandbox (recent)](https://github.com/topics/sandbox?o=desc&s=updated), [sbom](https://github.com/topics/sbom?o=desc&s=stars).
  - Networking and storage: [nat-traversal](https://github.com/topics/nat-traversal?o=desc&s=stars), [wireguard](https://github.com/topics/wireguard?o=desc&s=stars), [p2p](https://github.com/topics/p2p?o=desc&s=stars), [file-sharing](https://github.com/topics/file-sharing?o=desc&s=stars), [backup](https://github.com/topics/backup?o=desc&s=stars).
  - Apps and games: [note-taking](https://github.com/topics/note-taking?o=desc&s=stars), [screen-recorder](https://github.com/topics/screen-recorder?o=desc&s=stars), [matrix-org](https://github.com/topics/matrix-org?o=desc&s=stars), [godot](https://github.com/topics/godot?o=desc&s=stars), [godot-mcp](https://github.com/topics/godot-mcp), [procedural-generation](https://github.com/topics/procedural-generation?o=desc&s=stars).
- **Pages that could not be read:** the `matrix-bridge`, `push-notifications` and `immutable` topic pages (site error or rate limit), and gittrend.io (needed a permission that was not granted).
- **What "Orion already has" is based on:** a read-only scan of the local Orion tree, covering `apps/daemon/requirements*.txt` and the module names in `apps/daemon` and `apps/mobile`. One rule from that tree matters a lot here: the Android app uses **no third-party libraries**. Every phone-side idea below is therefore described as "implement this protocol with platform APIs", not "add this library".

Columns used in every shortlist table:
- **Effort:** S is under 2 days, M is about 1–2 weeks, L is more.
- **Verdict:** *adopt* (ship it), *try* (prototype it), *watch* (track it), *skip*.

---

## 2. Orion feature map (what each section feeds)

| Area | Orion features touched |
|---|---|
| Wayland shell | Orion HUD on Sway/SwayFX, hotkeys/macros, skill recorder (AT-SPI), screen share, Settings app |
| Local AI | Daemon assistant, voice (STT/TTS/wake), screen OCR, Video Scout, memory/knowledge vault, MCP bus |
| Coding agents | Mission Control, worktrees, two-model review, self-reprogramming with protected core, secret shield, injection firewall |
| Phone ↔ PC | Orion Mobile, pairing (QR + mDNS), approvals with the fingerprint key, Pocket Orion, one inbox |
| OS reliability | USB vault, A/B signed updates, snapper snapshots, backups, firewall/hardening, crash healer |
| Creative | Craft suite, screen recording, notes, unified messaging, email/calendar, passwords, file sync |
| Game dev | "Make a game with Claude" starters |

---

## 3. Already in Orion: don't rebuild (and what to change instead)

| Already built or used (local scan) | Overlapping repos | Advice |
|---|---|---|
| Voice: `faster-whisper`, `kokoro`, `webrtcvad`, `openwakeword` (requirements.txt / requirements-optional.txt) | whisper.cpp, Kokoro-FastAPI, RealtimeSTT, speaches, Wyoming | Keep the stack. Swap only **webrtcvad → silero-vad** and add **smart-turn** (section 4.2). Don't adopt a second full voice framework such as Pipecat or LiveKit Agents unless real-time duplex calls are needed. |
| Knowledge vault on **LanceDB** | qdrant, chroma, sqlite-vec | No migration needed. sqlite-vec is optional, for the phone or Pocket Orion. |
| Screen OCR with **pytesseract** | PaddleOCR, RapidOCR, docTR | Add RapidOCR as an optional faster or more accurate backend. Keep Tesseract as the fallback. |
| Video Scout (**yt-dlp** + whisper + Tesseract) | pyvideotrans, WhisperX | Only add WhisperX if speaker labels are wanted. |
| Backups with **restic**, snapshots with **snapper** | borg, kopia, backrest, btrbk, timeshift | Don't add a second backup engine. Consider `backrest` (GPL-3) as a UI reference only. `grub-btrfs` is useful if the bootloader is GRUB. |
| Remote phone access through **Tailscale** | headscale, NetBird, EasyTier, ZeroTier | Keep it. Offer **Headscale** for self-hosting (section 7). |
| Phone file send through **LocalSend** hand-off | croc, PairDrop, sendme | Already right. Don't build file transfer. |
| Pairing with QR + **NsdManager mDNS** (`PairPc.kt`) | KDE Connect, Valent, kcd | Keep it, and add UDP-broadcast fallback and address memory (section 7). |
| **Secret shield** + gitleaks in CI | trufflehog, detect-secrets | Skip trufflehog (AGPL, duplicate work). Optionally add gitleaks rules. |
| **Injection firewall**, **collision guard**, agent safety | llm-guard, pipelock, NeMo Guardrails | Borrow test cases (AgentDojo), don't swap engines. |
| MCP server/bus (`mcp_server.py`, `mcp_tools.py`) | fastmcp, mcp-proxy, docker/mcp-gateway | Only add **mcp-scan** and **MCP Inspector** as dev tools. |
| Macro recorder (pynput / evdev), wtype | ydotool (AGPL), kanata, keyd | Add **swhkd** or **keyd** for global hotkeys. Avoid ydotool because of its AGPL licence and daemon. |
| Presence and gestures (MediaPipe) | – | Done. |
| Background removal (rembg) | IOPaint, Real-ESRGAN | Extend, don't replace. |
| Notifications on Linux (mako is referenced) | SwayNotificationCenter, dunst | Optional: swaync gives a notification panel and do-not-disturb out of the box. |
| Minisign for signing | cosign, TUF | Keep minisign for the USB updates. Add TUF-style metadata only if more than one update channel appears. |

---

## 4. Shortlist by area

### 4.1 Wayland desktop and shell

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [WillPower3309/swayfx](https://github.com/WillPower3309/swayfx) | 2.4k★ ([repo](https://github.com/WillPower3309/swayfx)); 2026-10-31 | MIT | Sway fork with blur, shadows, rounded corners | Already the base. Track it for blur-under-HUD glass. | – | Small team | adopt (in use) |
| [swaywm/sway](https://github.com/swaywm/sway) | 17.4k★ (topic/wayland); 2026-09-21 | MIT | i3-compatible compositor | Fallback base, IPC for window rules | – | Low | adopt (in use) |
| [hyprwm/Hyprland](https://github.com/hyprwm/Hyprland) | 38.8k★ (topic/wayland); 2026-11-06 | BSD-3 | Dynamic tiling compositor with animations and gestures | The optional Hyprland session. Native workspace gestures and plugins. | M | Fast-moving API | adopt (optional) |
| [niri-wm/niri](https://github.com/niri-wm/niri) | 28.3k★ (topic/wayland); 2026-11-07 | GPL-3.0 | Scrollable-tiling compositor | A third session option, if users want "strip" layouts | M | GPL (fine as a separate binary) | watch |
| [noctalia-dev/noctalia](https://github.com/noctalia-dev/noctalia) | ~11.9k★ ([gittrend snapshot](https://gittrend.io/repo/noctalia-dev/noctalia-shell)); 2026-11-07 | MIT | Qt/QML Wayland shell for Sway, Hyprland, Niri and labwc | Reference for panels, control centre and lock screen. Its MIT licence allows copying patterns into the HUD. | S (study) | Young project | try (reference) |
| [AvengeMedia/DankMaterialShell](https://github.com/AvengeMedia/DankMaterialShell) | 8.4k★ (topic/quickshell); 2026-11-07 | MIT | Quickshell + Go desktop shell | Same use: patterns for the notification centre, OSD and power menu | S | Young | watch |
| [caelestia-dots/shell](https://github.com/caelestia-dots/shell) | 12.9k★ (topic/quickshell); 2026-11-07 | GPL-3.0 | "Morphing" animated shell | Visual inspiration for the HUD's black-hole transitions. GPL means no copying into MIT code. | – | GPL | watch |
| [quickshell-mirror/quickshell](https://github.com/quickshell-mirror/quickshell) | 3.1k★ ([repo](https://github.com/quickshell-mirror/quickshell)); 2026-10-21 | LGPL-3.0 | QtQuick toolkit for building shells | Possible light panel or lock screen if Tauri is too heavy for lock screen and greeter | M | Main development is off GitHub (git.outfoxxed.me) | watch |
| [Alexays/Waybar](https://github.com/Alexays/Waybar) | 12k★ (topic/wayland); 2026-11-05 | MIT | Status bar | Fallback bar in safe mode when the HUD crashes | S | Low | adopt (safe mode) |
| [ErikReider/SwayNotificationCenter](https://github.com/ErikReider/SwayNotificationCenter) | ★ n/v; 2026-06-25 | GPL-3.0 | Notification daemon with panel and do-not-disturb | Optional replacement for mako. Do-not-disturb ties to Orion's "quiet" core signal. | S | GPL binary only | try |
| [dunst-project/dunst](https://github.com/dunst-project/dunst) | 5.6k★ (topic/wayland); 2026-10-25 | BSD-3 | Light notification daemon | Alternative to mako | S | Low | skip (mako fine) |
| [abenz1267/walker](https://github.com/abenz1267/walker) | ★ n/v; 2026-10-08 | GPL-3.0 | Wayland app launcher with providers | Reference for the "one simple menu" providers (apps, calc, clipboard) | S | GPL | watch |
| [emersion/xdg-desktop-portal-wlr](https://github.com/emersion/xdg-desktop-portal-wlr) | ★ n/v; 2026-10-29 | MIT | ScreenCast/Screenshot portal for wlroots | Needed for browser and agent screen sharing on Sway. Ensure it ships in the image. | S | Low | adopt |
| [hyprwm/xdg-desktop-portal-hyprland](https://github.com/hyprwm/xdg-desktop-portal-hyprland) | ★ n/v; 2026-11-01 | BSD-3 | Same, for Hyprland | Hyprland session | S | Low | adopt |
| [alex-courtis/way-displays](https://github.com/alex-courtis/way-displays) | ★ n/v; 2026-11-05 | MIT | Auto display layout, scaling and hot-plug for wlroots | "Plug in a monitor and it just works", including fractional scale | S | Low | adopt |
| [nwg-piotr/nwg-displays](https://github.com/nwg-piotr/nwg-displays) | ★ n/v; 2026-10-20 | MIT | GUI display arranger for Sway/Hyprland | Embed or launch from Orion Settings → Displays | S | Low | try |
| [Ferdi265/wl-mirror](https://github.com/Ferdi265/wl-mirror) | ★ n/v; 2026-10-26 | GPL-3.0 | Mirror an output into a window | Presentation and projector mode | S | GPL binary | adopt |
| [waycrate/swhkd](https://github.com/waycrate/swhkd) | ★ n/v; 2026-04-26 | BSD-2 | Display-server-independent hotkey daemon (sxhkd syntax) | Global Orion hotkeys that work on Sway, Hyprland and the TTY | S | Slower activity | try |
| [rvaiya/keyd](https://github.com/rvaiya/keyd) | ★ n/v; 2026-06-01 | MIT | Kernel-level key remapping daemon | Hyper key / "Orion key" layer, caps-to-escape | S | Low | adopt |
| [jtroo/kanata](https://github.com/jtroo/kanata) | ★ n/v; 2026-11-02 | LGPL-3.0 | Advanced cross-platform key remapper (Linux and Windows) | Same layer on both Windows and Linux Orion | S | Low | try |
| [espanso/espanso](https://github.com/espanso/espanso) | ★ n/v; 2026-10-24 | GPL-3.0 | Text expander with Wayland support | Macros: ":addr", ":sig"; Orion can write espanso YAML from recorded skills | S | GPL binary | try |
| [jjsullivan5196/wvkbd](https://github.com/jjsullivan5196/wvkbd) | ★ n/v; 2026-10-27 | GPL-3.0 | On-screen keyboard for wlroots | Touch and tablet mode, accessibility | S | GPL binary | adopt |
| [odilia-app/odilia](https://github.com/odilia-app/odilia) | ★ n/v; 2026-05-28 | Apache-2.0 / MIT | Fast screen reader in Rust | Reference for AT-SPI event handling in the skill recorder | S | Early stage | watch |
| [odilia-app/atspi](https://github.com/odilia-app/atspi) | ★ n/v; 2026-07-27 | Apache-2.0 / MIT | Pure-Rust AT-SPI2 bindings (zbus) | Read AT-SPI from the Tauri (Rust) side with no Python-GI, for faster element picking in the HUD | M | Low | try |
| [GNOME/at-spi2-core](https://github.com/GNOME/at-spi2-core) | ★ n/v; 2026-11-03 | LGPL-2.1 | AT-SPI bus | Already used through python3-gi | – | – | adopt (in use) |
| [InioX/matugen](https://github.com/InioX/matugen) | ★ n/v; 2026-11-05 | GPL-2.0 | Material You palette from a wallpaper | Theme GTK, Qt and terminal from the black-hole colours | S | GPL binary | try |
| [nwg-piotr/nwg-look](https://github.com/nwg-piotr/nwg-look) | ★ n/v; 2026-10-14 | MIT | GTK settings editor for wlroots | Settings → Appearance back end | S | Low | try |
| [feschber/lan-mouse](https://github.com/feschber/lan-mouse) | 5.4k★ (topic/wayland); 2026-10-25 | GPL-3.0 | Software KVM: share mouse and keyboard over LAN, Wayland-native | Control a Windows PC or laptop from Orion with one keyboard | S | GPL binary | try |
| [LizardByte/Sunshine](https://github.com/LizardByte/Sunshine) | ★ n/v; 2026-11-07 | GPL-3.0 | Low-latency game-stream host (Moonlight protocol) | "Use my PC from my phone" remote desktop over Tailscale | M | GPL binary | try |
| [any1/wayvnc](https://github.com/any1/wayvnc) | ★ n/v; 2026-10-04 | ISC | VNC server for wlroots | Lightweight remote help / phone view of the desktop | S | Low | adopt |
| [rustdesk/rustdesk](https://github.com/rustdesk/rustdesk) | 129k★ (topic/p2p); 2026-11-07 | AGPL-3.0 | Self-hostable remote desktop | Optional app only. Duplicates Sunshine and wayvnc. | – | AGPL | skip |
| [wwmm/easyeffects](https://github.com/wwmm/easyeffects) | ★ n/v; 2026-11-07 | GPL-3.0 | PipeWire effects (noise suppression, EQ) | Clean up the mic before wake word and STT | S | GPL binary | adopt |

Also checked and judged less useful: labwc, river (GPL-3.0 per its LICENSES folder), cosmic-comp, cage (use it for kiosk or a "Pocket" mode), eww, ags/astal, tofi, bemenu, rofi, kanshi (the GitHub mirror is stale; development is on sourcehut), wdisplays, swww, mpvpaper, swaylock(-effects), hyprlock/hypridle, grim/slurp/swappy, wf-recorder, cliphist, wl-clipboard, wtype, ydotool (AGPL), touchegg and libinput-gestures (X11-only), fusuma, kmonad, input-remapper, Weylus (AGPL), deskflow, input-leap, Maliit, fcitx5, wluma, Clight, hyprsunset, wl-gammarelay-rs, xwayland-satellite, uwsm (MIT; worth using to start the session cleanly with systemd), autotiling and pywal16. All of these are in the appendix.

### 4.2 Local AI (inference, voice, vision, RAG, agents, MCP)

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 132.5k★ ([repo](https://github.com/ggml-org/llama.cpp)); 2026-11-07 | MIT | Core GGUF inference | Already used through Ollama. Run `llama-server` directly for Vulkan on AMD/Intel and speculative decoding. | M | Low | adopt (direct, optional) |
| [ollama/ollama](https://github.com/ollama/ollama) | 184.7k★ ([repo](https://github.com/ollama/ollama)); 2026-11-06 | MIT | Model manager and server | In use | – | – | adopt (in use) |
| [mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap) | ★ n/v; 2026-11-07 | MIT | OpenAI-compatible proxy that hot-swaps llama.cpp, vLLM and others by model name | One endpoint for chat, embeddings, vision and code models. Keeps one big model warm and swaps the rest. | S | Low | try |
| [containers/ramalama](https://github.com/containers/ramalama) | ★ n/v; 2026-11-06 | MIT | Runs models in podman containers with GPU detection | Sandboxed model serving with no host libraries; fits the protected core | M | Low | watch |
| [mudler/LocalAI](https://github.com/mudler/LocalAI) | 49.9k★ (topic/tts); 2026-11-07 | MIT | All-in-one OpenAI-compatible local server (LLM, TTS, STT, images) | Overlaps with Orion's own daemon | – | Duplicate | skip |
| [mozilla-ai/llamafile](https://github.com/mozilla-ai/llamafile) | 26.3k★ (topic/llama-cpp); 2026-11-05 | Apache-2.0 | One-file LLM executables | Ship a tiny rescue model on the USB vault that runs with no install | S | Low | try |
| [lemonade-sdk/lemonade](https://github.com/lemonade-sdk/lemonade) | ★ n/v; 2026-11-07 | Apache-2.0 | Local LLM server tuned for AMD GPUs and NPUs | Use the Ryzen AI NPU when one is present | M | Vendor-led | watch |
| [vllm-project/vllm](https://github.com/vllm-project/vllm) | ★ n/v; 2026-11-07 | Apache-2.0 | High-throughput serving | Only for big NVIDIA rigs (Boardroom server) | M | Heavy | watch |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 14.2k★ (topic/speech-to-text); 2026-11-07 | Apache-2.0 | Offline ASR, TTS, VAD, diarization and keyword spotting on ONNX; Android/Kotlin and Python | One engine for **Pocket Orion and the phone** (it has Android builds), plus streaming STT on weak PCs. Its keyword spotting can back up openWakeWord. | M | Low | adopt |
| [moonshine-ai/moonshine](https://github.com/moonshine-ai/moonshine) | ★ n/v; 2026-11-05 | MIT code; English and streaming models MIT; legacy non-English models non-commercial (LICENSE) | Tiny fast STT built for edge devices | Fast commands on laptops and phones. Use only the MIT models. | S | Model licence split | try |
| [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | ★ n/v; 2026-10-22 | MIT | Neural voice activity detection | Replace webrtcvad. Fewer false triggers from fans and TV. | S | Low | adopt |
| [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn) | ★ n/v; 2026-01-29 | BSD-2 | End-of-turn detection model | Orion stops cutting the speaker off mid-sentence, and replies faster when they're done | S | Small model | adopt |
| [pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat) | 17.1k★ (topic/voice-assistant); 2026-11-07 | BSD-2 | Real-time voice-agent framework | Only if Orion adds full-duplex phone calls | L | Overlap | watch |
| [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | 10.1k★ (topic/speech-to-text); 2026-10-30 | MIT | Low-latency STT with VAD and wake word | Reference implementation for streaming partial transcripts in the HUD | S | Overlap | watch |
| [cjpais/Handy](https://github.com/cjpais/Handy) | 29.5k★ (topic/speech-to-text); 2026-11-06 | MIT | Offline push-to-talk dictation app (Tauri) | Same stack as the HUD. Borrow its "dictate into any text field" flow for Wayland. | S | Low | try (reference) |
| [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) | ★ n/v; 2026-11-07 | GPL-3.0 (the old rhasspy/piper was MIT and is now inactive) | Fast light neural TTS | Low-end fallback voice when Kokoro is too slow. Run it as a separate binary because of the GPL. | S | GPL | try |
| [hexgrad/kokoro](https://github.com/hexgrad/kokoro) | 9.8k★ ([repo](https://github.com/hexgrad/kokoro)); 2025-08-06 | Apache-2.0 | 82M TTS | In use | – | Slow upstream | adopt (in use) |
| [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox) | ★ n/v; 2026-11-04 | MIT | Expressive TTS with zero-shot voice cloning and watermarking | The owner's own voice for Craft narration, **only with recorded consent**. Its built-in watermark fits the rule against hiding identity. | M | GPU needed | try |
| [myshell-ai/OpenVoice](https://github.com/myshell-ai/OpenVoice) | 38.1k★ (topic/tts); 2025-04-19 | MIT | Voice cloning | Older and inactive. Prefer Chatterbox. | – | Stale | skip |
| [fishaudio/fish-speech](https://github.com/fishaudio/fish-speech) | 33.4k★ (topic/tts); 2026-10-30 | Fish Audio Community Licence (not open source) | TTS | – | – | Licence | skip |
| [index-tts/index-tts](https://github.com/index-tts/index-tts) | 24.7k★ (topic/tts); 2026-09-30 | bilibili model licence (restrictive) | TTS | – | – | Licence | skip |
| [dscripka/openWakeWord](https://github.com/dscripka/openWakeWord) | 2.7k★, last release v0.6.0 on 2024-02-11 ([repo](https://github.com/dscripka/openWakeWord)); last commit 2025-12-30 | Apache-2.0 | Wake word | In use. Upstream is slow, so keep sherpa-onnx keyword spotting as plan B. | – | Maintenance | adopt (in use) |
| [pyannote/pyannote-audio](https://github.com/pyannote/pyannote-audio) | ★ n/v; 2026-11-07 | MIT (some models are gated on Hugging Face) | Speaker diarization | "Who said what" in meeting notes and Video Scout | M | Model access | try |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 23.6k★ (topic/speech-to-text); 2026-10-22 | BSD-2 | Word timestamps + diarization on faster-whisper | Subtitle-accurate Video Scout clips and Craft captions | S | Low | try |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 92.4k★ (topic/ocr); 2026-09-16 | Apache-2.0 | Accurate multilingual OCR and layout | Better screen OCR and document parsing | M | Heavy framework | try |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | ★ n/v; 2026-11-04 | Apache-2.0 | PaddleOCR models on ONNX Runtime, pip-light | **Drop-in screen-OCR upgrade** with no Paddle install | S | Low | adopt |
| [mindee/doctr](https://github.com/mindee/doctr) | ★ n/v; 2026-11-06 | Apache-2.0 | Document OCR | Alternative to RapidOCR | S | – | skip (pick one) |
| [hiroi-sora/Umi-OCR](https://github.com/hiroi-sora/Umi-OCR) | 48.5k★ (topic/ocr); 2025-11-20 | MIT | Offline screenshot OCR app | UX reference for "select area → text" | – | Windows-first | watch |
| [ocrmypdf/OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | 35.3k★ (topic/ocr); 2026-11-07 | MPL-2.0 | Adds a searchable text layer to scanned PDFs | Knowledge vault ingests scans | S | Low | adopt |
| [docling-project/docling](https://github.com/docling-project/docling) | ★ n/v; 2026-11-07 | MIT | PDF/DOCX/PPTX → structured Markdown/JSON | Better vault ingestion than pypdf (tables, layout) | S | Model download | adopt |
| [microsoft/markitdown](https://github.com/microsoft/markitdown) | ★ n/v; 2026-11-06 | MIT | Converts Office, PDF, HTML and more to Markdown, light | Fast path for simple documents and email attachments | S | Low | adopt |
| [opendatalab/MinerU](https://github.com/opendatalab/MinerU) | 83.3k★ (topic/ocr); 2026-11-04 | Apache-2.0 (root LICENSE.md, as checked) | High-quality PDF → Markdown | Heavy alternative to Docling | M | Size; earlier versions used a different licence, so check the version you ship | watch |
| [datalab-to/chandra](https://github.com/datalab-to/chandra) | 12.7k★ (topic/ocr); 2026-10-22 | Apache-2.0 code (check the model weights) | OCR for complex tables, forms and handwriting | Handwritten notes into the vault | M | Model licence not checked | watch |
| [vikhyat/moondream](https://github.com/vikhyat/moondream) | ★ n/v; 2026-04-19 | Apache-2.0 | Tiny vision-language model | "What's on my screen?" locally on weak GPUs | S | Low | try |
| [QwenLM/Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) | ★ n/v; 2026-01-30 | Apache-2.0 (repo) | Strong open VLM with GUI grounding | Local computer-use and grounding model through Ollama/llama.cpp | M | Size | try |
| [microsoft/OmniParser](https://github.com/microsoft/OmniParser) | ★ n/v; 2026-07-19 | CC-BY-4.0 repo; model weights carry their own licences (check them) | Screenshot → clickable UI elements | Vision fallback for the skill recorder when AT-SPI has no tree (games, Electron) | M | Model licence | try |
| [trycua/cua](https://github.com/trycua/cua) | 31.7k★ ([repo](https://github.com/trycua/cua)); 2026-11-07 | MIT | Computer-use drivers, Linux sandboxes, benchmarks | Run GUI agents in a **throwaway Linux sandbox**, not the owner's desktop. Benchmark Orion's skill replay. | M | Fast-moving | try |
| [simular-ai/Agent-S](https://github.com/simular-ai/Agent-S) | ★ n/v; 2026-10-21 | Apache-2.0 | Open computer-use agent framework | Reference planner and grounder split | M | – | watch |
| [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | 38.7k★ (topic/mcp-server); 2026-10-09 | Apache-2.0 | Multimodal GUI agent app | Reference; model is large | M | Size | watch |
| [OpenAdaptAI/OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt) | ★ n/v; 2026-11-07 | MIT | Learns workflows from recorded demonstrations | The same idea as Orion's skill recorder. Compare approaches and don't merge. | S | Overlap | watch |
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | ★ n/v; 2026-10-29 | MIT | LLM browser automation | Browser chores via CDP (Orion has `cdp_bridge.py`) | S | Overlap | watch |
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | ★ n/v; 2026-11-06 | Apache-2.0 | Browser MCP server | Bundle it on the MCP bus for coding agents' web tests | S | Low | adopt |
| [ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) | 49.7k★ (topic/mcp-server); 2026-11-06 | Apache-2.0 | DevTools for coding agents | Mission Control: agents debug the HUD (React) live | S | Low | adopt |
| [oraios/serena](https://github.com/oraios/serena) | 28.5k★ (topic/mcp-server); 2026-10-28 | GPL (LICENSE) | Semantic code retrieval and editing MCP over language servers | Cheaper, more precise context for coding agents in worktrees | S | GPL (runs as a separate MCP process) | try |
| [upstash/context7](https://github.com/upstash/context7) | 61.2k★ (topic/mcp-server); 2026-11-06 | MIT | Up-to-date library docs for agents | Fewer hallucinated APIs | S | Calls Upstash's cloud service by default, which breaks local-first | watch |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | ★ n/v; 2026-11-04 | Apache-2.0 | Reference MCP servers (filesystem, git, fetch, memory) | Bundle git and filesystem, each scoped to a worktree | S | Low | adopt |
| [modelcontextprotocol/inspector](https://github.com/modelcontextprotocol/inspector) | ★ n/v; 2026-11-04 | Apache-2.0 | MCP debugging UI | Dev tool for Orion's MCP bus | S | Low | adopt (dev) |
| [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) | ★ n/v; 2026-05-17 | Apache-2.0 / MIT | Vector search inside SQLite | Pocket Orion and phone-side memory with no server; tiny | S | Slower upstream | try |
| [lancedb/lancedb](https://github.com/lancedb/lancedb) | 11.4k★ (topic/vector-database); 2026-11-07 | Apache-2.0 | Embedded vector DB | In use | – | – | adopt (in use) |
| [qdrant/fastembed](https://github.com/qdrant/fastembed) | ★ n/v; 2026-10-29 | Apache-2.0 | ONNX embeddings without PyTorch | Replace the optional sentence-transformers (about 2 GB of PyTorch) with roughly 100 MB of ONNX | S | Low | adopt |
| [neuml/txtai](https://github.com/neuml/txtai) | 12.9k★ (topic/vector-database); 2026-11-06 | Apache-2.0 | All-in-one embeddings, RAG and pipelines | Overlaps the vault | – | Duplicate | skip |
| [getzep/graphiti](https://github.com/getzep/graphiti) | ★ n/v; 2026-11-06 | Apache-2.0 | Time-aware knowledge graph memory | "Promise keeper" and people memory: who said what, and when | M | Needs a graph DB | watch |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | ★ n/v; 2026-11-06 | Apache-2.0 | Agent memory layer | Overlaps memory_core | – | Duplicate | skip |
| [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) | ★ n/v; 2026-10-14 | MIT | Graph-based RAG | Experiment for the large vault | M | – | watch |
| [mozilla/translations](https://github.com/mozilla/translations) | ★ n/v; 2026-10-29 | MPL-2.0 | Firefox's local Bergamot translation models and pipeline | Offline translation of inbox messages and screen text | M | Model packaging | try |
| [argosopentech/argos-translate](https://github.com/argosopentech/argos-translate) | ★ n/v; 2026-08-08 | MIT | Offline translation library (CTranslate2) | Simpler Python path than Bergamot | S | Quality varies | try |
| [LibreTranslate/LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) | ★ n/v; 2026-10-25 | AGPL-3.0 | Translation API server | Argos is enough on its own | – | AGPL | skip |
| [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice) | 9.1k★ (topic/speech-recognition); 2026-09-30 | MIT (repo) | Multilingual ASR plus emotion and audio events | Emotion tags for the "heat" core signal; experimental | M | Model terms not checked | watch |

### 4.3 Coding-agent tooling (Mission Control)

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime) | 6.7k★ ([repo](https://github.com/anthropic-experimental/sandbox-runtime)); 2026-11-06 | Apache-2.0 | OS-level filesystem and network sandbox using **bubblewrap + seccomp + proxy**; built for agents and MCP servers | Wrap every Mission Control agent: write access only to its worktree, network only to allowed domains | S | Experimental label | adopt |
| [containers/bubblewrap](https://github.com/containers/bubblewrap) | ★ n/v; 2026-10-14 | LGPL-2.0+ | Unprivileged sandbox | Underlies the line above. Also sandboxes Video Scout's yt-dlp and ffmpeg. | S | Low | adopt |
| [eugene1g/agent-safehouse](https://github.com/eugene1g/agent-safehouse) | 2.1k★ (topic/sandbox recent); 2026-11-02 | Apache-2.0 | Sandbox profiles for local AI agents (tagged claude-code) | Compare its profiles with srt | S | Young | watch |
| [google/gvisor](https://github.com/google/gvisor) | 19.2k★ (topic/sandbox); 2026-11-07 | Apache-2.0 | User-space kernel sandbox | Stronger isolation for untrusted repos and code from the internet | M | Performance | try |
| [firecracker-microvm/firecracker](https://github.com/firecracker-microvm/firecracker) | 36.3k★ (topic/sandbox); 2026-11-06 | Apache-2.0 | MicroVMs | Hardest isolation tier: running unknown code that agents fetched | L | KVM, image management | watch |
| [superradcompany/microsandbox](https://github.com/superradcompany/microsandbox) | 7.9k★ (topic/sandbox); 2026-11-07 (checked under its old name microsandbox/microsandbox) | Apache-2.0 | Self-hosted microVM sandboxes with an SDK | Easier microVM tier than raw Firecracker | M | Young | try |
| [dagger/container-use](https://github.com/dagger/container-use) | ★ n/v; 2026-08-12 | Apache-2.0 | Container plus git branch per agent, through MCP | Same idea as Orion's worktrees. Borrow the "environment per agent" UX only. | – | Overlap | watch |
| [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) | 8.3k★ ([repo](https://github.com/smtg-ai/claude-squad)); 2026-08-20 | AGPL-3.0 | Terminal manager for parallel agents in worktrees | Orion already does this | – | AGPL, duplicate | skip |
| [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 28.1k★, marked "sunsetting" ([repo](https://github.com/BloopAI/vibe-kanban)); 2026-11-03 | Apache-2.0 | Kanban that runs agents in isolated workspaces | Borrow ideas (diff review, PR flow) only; it is being wound down | – | Ending | skip |
| [Untrivial-ai/agent-orchestrator](https://github.com/Untrivial-ai/agent-orchestrator) | 10.2k★ (topic/git-worktrees); 2026-11-08 | Apache-2.0 | Orchestrator that handles CI, merge conflicts and reviews for fleets of agents | Compare the merge-conflict handling with Orion's collision_guard | S | Very young, star-inflated category | watch |
| [sipyourdrink-ltd/bernstein](https://github.com/sipyourdrink-ltd/bernstein) | 1k★ (topic/git-worktrees); 2026-11-07 | Apache-2.0 | Deterministic, reproducible parallel agent runs in worktrees | Idea: replayable mission logs | S | Beta | watch |
| [coder/agentapi](https://github.com/coder/agentapi) | ★ n/v; 2026-09-13 | MIT | HTTP API over Claude Code, Codex, Gemini and other CLIs | One driver for all agent CLIs instead of a parser per tool | S | Low | try |
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | ★ n/v; 2026-09-03 | MIT | Minimal (~100 lines) coding agent that works with any model | A fully local coding agent on Ollama for offline Mission Control | S | Low | try |
| [sst/opencode](https://github.com/sst/opencode) | ★ n/v; 2026-11-07 | MIT | Open terminal coding agent with any provider | Fourth agent option that works with local models | S | Low | try |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | ★ n/v; 2026-05-22 | Apache-2.0 | Git-native pair programmer | Local-model coding agent | S | Slowing (last commit May 2026) | watch |
| [anthropics/claude-code-security-review](https://github.com/anthropics/claude-code-security-review) | ★ n/v; 2026-02-11 | MIT | Security-review prompts and action | Feed the second reviewer in Orion's two-model review | S | Low | adopt |
| [qodo-ai/pr-agent](https://github.com/qodo-ai/pr-agent) | 12.9k★ (topic/code-review, now The-PR-Agent/pr-agent); 2026-11-08 | MIT | AI PR reviewer | Review prompts and diff chunking ideas | S | Overlap | watch |
| [reviewdog/reviewdog](https://github.com/reviewdog/reviewdog) | 9.6k★ (topic/code-review); 2026-11-07 | MIT | Turns linter output into review comments | Pipe ruff, semgrep and gitleaks into the review panel | S | Low | adopt |
| [semgrep/semgrep](https://github.com/semgrep/semgrep) | ★ n/v; 2026-11-02 | LGPL-2.1 (engine) | Static analysis | Deterministic gate before an AI change is merged | S | Some rules are under separate licences | adopt |
| [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) | 28.8k★ (topic/llm-inference); 2026-10-23 | MIT | Secret scanning | In use | – | – | adopt (in use) |
| [anchore/syft](https://github.com/anchore/syft) + [anchore/grype](https://github.com/anchore/grype) | syft 9.6k★ (topic/sbom); 2026-11-06 | Apache-2.0 | SBOM generation + vulnerability scanning | SBOM for each OrionOS image and each daemon release; grype checks run during "dep autopilot" | S | Low | adopt |
| [google/osv-scanner](https://github.com/google/osv-scanner) | ★ n/v; 2026-11-05 | Apache-2.0 | Scans lockfiles against the OSV vulnerability database | Lighter check for pip and npm lockfiles in agent worktrees | S | Low | adopt |
| [sigstore/cosign](https://github.com/sigstore/cosign) | ★ n/v; 2026-11-04 | Apache-2.0 | Signing and attestations | Optional: attach SBOM attestations next to the minisign signature | S | Keyless signing needs the network | try |
| [sigstore/gitsign](https://github.com/sigstore/gitsign) | ★ n/v; 2026-11-04 | Apache-2.0 | Keyless git commit signing | Mark which agent made which commit (provenance) | S | Network | watch |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | ★ n/v; 2026-11-07 | MIT | LLM evals and red-teaming | Regression tests for Orion's prompts and the injection firewall | S | Low | adopt |
| [NVIDIA/garak](https://github.com/NVIDIA/garak) | ★ n/v; 2026-11-06 | Apache-2.0 | LLM vulnerability scanner | Periodic red-team of local models | S | Low | try |
| [ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo) | 803★ (topic/prompt-injection); 2026-06-02 | MIT | Benchmark of prompt-injection attacks on agents | Test suite for injection_firewall and the email/inbox tools | S | Low | adopt |
| [invariantlabs-ai/mcp-scan](https://github.com/invariantlabs-ai/mcp-scan) | ★ n/v; 2026-11-06 | Apache-2.0 | Scans MCP servers for tool poisoning | Run before any new MCP server joins the bus | S | Some modes call a remote service; check | try |
| [luckyPipewrench/pipelock](https://github.com/luckyPipewrench/pipelock) | 835★ (topic/prompt-injection); 2026-11-07 | Apache-2.0 | Egress firewall for agents and MCP | Ideas for per-agent domain allowlists | S | Young | watch |
| [google-research/camel-prompt-injection](https://github.com/google-research/camel-prompt-injection) | ★ n/v; 2025-06-20 | Apache-2.0 | Reference code for CaMeL (keeps control flow and data flow apart) | Design pattern for the "untrusted inbox text never chooses tools" rule | M | Research code | try (design) |
| [protectai/llm-guard](https://github.com/protectai/llm-guard) | 3.2k★ (topic/prompt-injection); 2026-07-09 | MIT | Input/output scanners | Overlaps the injection firewall | – | Duplicate | skip |
| [HypothesisWorks/hypothesis](https://github.com/HypothesisWorks/hypothesis) | ★ n/v; 2026-11-06 | MPL-2.0 | Property-based testing | Agents write property tests for the daemon | S | Low | adopt |
| [boxed/mutmut](https://github.com/boxed/mutmut) | ★ n/v; 2026-11-07 | BSD-3 | Mutation testing | Checks whether AI-written tests actually catch bugs, as part of the proof gate | S | Slow on big suites | try |
| [jj-vcs/jj](https://github.com/jj-vcs/jj) | ★ n/v; 2026-11-07 | Apache-2.0 | Git-compatible VCS with first-class undo | An operation log could make agent rollback safer | M | Agents need to learn it | watch |

### 4.4 Phone ↔ PC

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [KDE/kdeconnect-kde](https://github.com/KDE/kdeconnect-kde) / [kdeconnect-android](https://github.com/KDE/kdeconnect-android) | ~4.0k★ ([repo](https://github.com/KDE/kdeconnect-kde)); 2026-11-07 / 2026-11-06 | GPL-2/3 | Phone ↔ PC suite | Protocol reference (port 1716, TLS with fingerprint trust). Don't embed it; GPL code can't go into the no-libraries app. | – | GPL | watch (protocol) |
| [andyholmes/valent](https://github.com/andyholmes/valent) | 923★ (topic/kdeconnect); 2026-03-14 | GPL-3.0 | GTK KDE Connect implementation, alpha | Run it on Sway so stock KDE Connect phones (and GrapheneOS) also work | S | Alpha | try |
| [bethropolis/kcd](https://pkg.go.dev/github.com/bethropolis/kcd) | stars not checked | MIT ([pkg.go.dev](https://pkg.go.dev/github.com/bethropolis/kcd)) | **Headless** KDE Connect protocol v8 daemon: UDP 1716 broadcast + mDNS `_kdeconnect._udp`, TLS, fingerprint pairing | MIT reference for adding a KDE Connect-compatible listener to the daemon, or run it alongside | S | Single maintainer | try |
| [GSConnect](https://github.com/GSConnect/gnome-shell-extension-gsconnect) | 3.8k★ (topic/kdeconnect); 2026-10-18 | GPL-2.0+ | GNOME Shell extension | GNOME only | – | – | skip |
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | 152.6k★, release v5.0.1 ([repo](https://github.com/Genymobile/scrcpy)); 2026-10-08 | Apache-2.0 | Mirror and control Android over ADB | "Show my phone on the HUD"; drive phone apps from a PC agent | S | Needs ADB / wireless debugging | adopt |
| [localsend/localsend](https://github.com/localsend/localsend) | 92.7k★ (topic/file-sharing); 2026-11-07 | Apache-2.0 | LAN file sharing | In use | – | – | adopt (in use) |
| [syncthing/syncthing](https://github.com/syncthing/syncthing) | 90.2k★ (topic/p2p); 2026-11-02 | MPL-2.0 | Continuous P2P file sync | Sync phone photos and notes to the PC vault | S | Low | adopt |
| [researchxxl/syncthing-android](https://github.com/researchxxl/syncthing-android) | ★ n/v; 2026-11-06 | MPL-2.0 | Maintained Android fork. The official syncthing-android's last commit was 2024-12-03. | Recommend this fork (or Catfriend1's) to users | – | Fork churn | adopt (recommend) |
| [tailscale/tailscale](https://github.com/tailscale/tailscale) | 37.1k★ (topic/wireguard); 2026-11-06 | BSD-3 | WireGuard mesh with NAT traversal and DERP relays | In use | – | Uses Tailscale's coordination server by default | adopt (in use) |
| [juanfont/headscale](https://github.com/juanfont/headscale) | 44.3k★ (topic/wireguard); 2026-11-06 | BSD-3 | Self-hosted Tailscale control server | "Own your link": the Boardroom server or the PC hosts the control plane | M | You run it and keep it up | try |
| [netbirdio/netbird](https://github.com/netbirdio/netbird) | 29.8k★ (topic/nat-traversal); 2026-11-07 | Client BSD-3, management AGPL-3.0 | WireGuard mesh, fully self-hostable | Alternative to Headscale | M | AGPL server | watch |
| [EasyTier/EasyTier](https://github.com/EasyTier/EasyTier) | 14.6k★ (topic/p2p); 2026-11-08 | LGPL-3.0 | Decentralised mesh VPN with no central server | Fallback when Tailscale's servers can't be reached | M | Mostly Chinese-language community | watch |
| [n0-computer/iroh](https://github.com/n0-computer/iroh) | 12.9k★ (topic/p2p); 2026-11-05 | Apache-2.0 / MIT | QUIC + hole punching + relays, **dial by public key** | Future PC ↔ Pocket link with no VPN app on the phone. The PC side is Rust (Tauri). The phone side can't use it under the no-libraries rule, but Pocket Orion in the Linux terminal could. | L | API churn | try (Pocket) |
| [n0-computer/sendme](https://github.com/n0-computer/sendme) | ★ n/v; 2026-10-29 | Apache-2.0 / MIT | File send over iroh | Large files over the internet without Tailscale | S | – | watch |
| [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy) + [ntfy-android](https://github.com/binwiederhier/ntfy-android) | 33.5k★ ([repo](https://github.com/binwiederhier/ntfy)); 2026-11-03 / 2026-10-19 | Apache-2.0 / GPL-2.0 dual (server), Apache-2.0 (Android) | Self-hostable HTTP pub-sub push; the Android app is a **UnifiedPush distributor** | The PC daemon publishes "approval waiting" to a ntfy topic. The phone wakes instantly instead of waiting for the 15-minute poll. | S–M | You run it | adopt |
| [UnifiedPush/android-connector](https://github.com/UnifiedPush/android-connector) | ★ n/v; 2025-07-01 | Apache-2.0 | Library for receiving UnifiedPush | Can't be used as a library under the no-libraries rule. The protocol is plain Android intents, so implement the receiver directly (≈150 lines). Distributors: ntfy, Sunup, NextPush ([Nextcloud blog, 2026-09-01](https://nextcloud.com/blog/unifiedpush-android-nextcloud-talk/)). | S | Low | adopt (protocol) |
| [gotify/server](https://github.com/gotify/server) | ★ n/v; 2026-10-25 | MIT | Self-hosted push server | ntfy has the better UnifiedPush story | – | – | skip |
| [coturn/coturn](https://github.com/coturn/coturn) | ★ n/v; 2026-11-06 | BSD-3 | TURN/STUN server | Relay for WebRTC phone-call or screen streams if added | S | – | watch |
| [aiortc/aiortc](https://github.com/aiortc/aiortc) | ★ n/v; 2026-11-07 | BSD-3 | WebRTC in Python | Daemon-side live voice or video with the phone (Talk) | M | – | try |
| [warner/python-spake2](https://github.com/warner/python-spake2) | ★ n/v; 2024-09-25 | MIT | SPAKE2 password-authenticated key exchange | Pair by typing a 6-digit code when the camera or QR can't be used. Same idea as magic-wormhole. | S | Inactive, but the code is small | try |
| [hbldh/bleak](https://github.com/hbldh/bleak) | ★ n/v; 2026-11-07 | MIT | Bluetooth LE client | BLE "PC is here" beacon and wake-up nudge when Wi-Fi is off | M | BLE quirks | watch |
| [RikkaApps/Shizuku](https://github.com/RikkaApps/Shizuku) | ★ n/v; 2025-06-18 | Apache-2.0 | System API access via ADB without root | Optional deeper phone automation | – | Slowing | watch |
| [droidrun/droidrun](https://github.com/droidrun/droidrun) | 9.8k★ ([repo](https://github.com/droidrun/droidrun)); 2026-11-04 | MIT | LLM phone agent (ADB) with local LLM support | "Do this on my phone" missions from the PC, behind approvals | M | ADB security | try |
| [google-ai-edge/gallery](https://github.com/google-ai-edge/gallery) | 25.2k★ ([repo](https://github.com/google-ai-edge/gallery)); 2026-11-06 | Apache-2.0 | On-device GenAI showcase (LiteRT-LM, Gemma) | Reference for on-phone models when the PC is off | S | – | watch |
| [google-ai-edge/LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) | 6.2k★ (topic/on-device-ai); 2026-11-07 | Apache-2.0 | Edge LLM runtime | On-phone Orion brain. It is a library, so it would need a separate optional app or an exception to the no-libraries rule. | L | Rule conflict | watch |
| [a-ghorbani/pocketpal-ai](https://github.com/a-ghorbani/pocketpal-ai) | 9.0k★ ([repo](https://github.com/a-ghorbani/pocketpal-ai)); 2026-11-07 | MIT | On-phone LLM app (llama.rn) | Recommend it as a companion; Orion Mobile could hand prompts to it via intent | – | – | watch |
| [shubham0204/SmolChat-Android](https://github.com/shubham0204/SmolChat-Android) | ★ n/v; 2026-11-01 | Apache-2.0 | Native Kotlin llama.cpp chat | Closest code style to Orion Mobile (Kotlin + JNI) | – | – | watch |
| [MM2-0/Kvaesitso](https://github.com/MM2-0/Kvaesitso) | ★ n/v; 2026-11-04 | GPL-3.0 | Search-focused launcher | Search UX reference only | – | GPL | watch |
| [markusfisch/PieLauncher](https://github.com/markusfisch/PieLauncher) | 565★ (topic/android-launcher); 2026-11-02 | MIT | Pie-menu launcher | MIT gesture-menu ideas for the black-hole launcher | S | – | try (reference) |
| [ImranR98/Obtainium](https://github.com/ImranR98/Obtainium) | ★ n/v; 2026-11-01 | GPL-3.0 | Installs and updates APKs straight from GitHub releases | Update channel for Orion Mobile on GrapheneOS, with no Play Store | S | – | adopt (document) |
| [soupslurpr/AppVerifier](https://github.com/soupslurpr/AppVerifier) | ★ n/v; 2026-09-18 | Apache-2.0 | Verifies an APK's signing-certificate hash | Publish Orion Mobile's cert hash so users can verify it | S | – | adopt (document) |
| [accrescent/accrescent](https://github.com/accrescent/accrescent) | ★ n/v; 2026-11-06 | Apache-2.0 / ISC | Security-focused Android app store (GrapheneOS ecosystem) | Possible future distribution channel | M | Store review | watch |
| [GrapheneOS/Auditor](https://github.com/GrapheneOS/Auditor) | ★ n/v; 2026-11-02 | MIT | Hardware attestation of a phone's OS integrity | The PC checks that the approving phone has a verified OS before trusting fingerprint-signed approvals | M | Needs a supported device | try |
| [seedvault-app/seedvault](https://github.com/seedvault-app/seedvault) | ★ n/v; 2026-11-05 | Apache-2.0 and others (LICENSES) | Android backup (used in GrapheneOS) | Point Seedvault at the PC vault through WebDAV/SMB | S | – | watch |

### 4.5 OS reliability and distro tooling

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [systemd/mkosi](https://github.com/systemd/mkosi) | 2.0k★ ([repo](https://github.com/systemd/mkosi)); 2026-11-05 | LGPL-2.1+ (LICENSES folder) | Declarative image builder (Ubuntu supported) producing UKIs, verity and sysupdate artefacts | Replace hand-rolled `os/image/build.sh` steps with a reproducible config. Native A/B partitions and signed unified kernel images. | M | Learning curve | adopt |
| systemd-sysupdate ([systemd/systemd](https://github.com/systemd/systemd)) | ★ n/v; 2026-11-07 | LGPL-2.1+ | A/B partition and image updater | Pairs with mkosi for the "A/B signed updates" | M | Partition layout change | adopt |
| [silitics/rugix](https://github.com/silitics/rugix) | ★ n/v; 2026-11-02 | Apache-2.0 / MIT | A/B OTA updates + image building, works for Debian-based systems | Simpler alternative if mkosi with sysupdate is too much | M | Small team | try |
| [rauc/rauc](https://github.com/rauc/rauc) | ★ n/v; 2026-11-07 | LGPL-2.1 | Embedded-grade A/B updater | Industrial-strength alternative | M | Embedded focus | watch |
| [containers/bootc](https://github.com/containers/bootc) | 2.3k★ ([repo](https://github.com/containers/bootc)); 2026-11-06 | Apache-2.0 / MIT | OS updates shipped as container images (OSTree) | Long-term option. Mainly Fedora/CentOS today, so it doesn't fit Ubuntu. | L | Distro mismatch | watch |
| [vanilla-os/ABRoot](https://github.com/vanilla-os/ABRoot) | ★ n/v; 2026-08-25 | GPL-3.0 | A/B root for Debian-based Vanilla OS | Closest Debian-family prior art; read the design | S | GPL | watch |
| [openSUSE/snapper](https://github.com/openSUSE/snapper) | ★ n/v; 2026-11-04 | GPL-2.0 | btrfs snapshots | In use | – | – | adopt (in use) |
| [Antynea/grub-btrfs](https://github.com/Antynea/grub-btrfs) | ★ n/v; 2026-08-24 | GPL-3.0 | Boot any snapshot from the GRUB menu | One-key "boot yesterday's system" | S | Only applies if GRUB is used | adopt (if GRUB) |
| [digint/btrbk](https://github.com/digint/btrbk) | ★ n/v; 2026-07-19 | GPL-3.0 | Sends btrfs snapshots to another disk | Snapshot shipping to the USB vault | S | Overlaps restic | watch |
| [restic/restic](https://github.com/restic/restic) | 36k★ (topic/backup); 2026-11-06 | BSD-2 | Backups | In use | – | – | adopt (in use) |
| [garethgeorge/backrest](https://github.com/garethgeorge/backrest) | ★ n/v; 2026-07-27 | GPL-3.0 | Web UI and scheduler for restic | UX reference for the Settings backup page | – | GPL | watch |
| [Foxboron/sbctl](https://github.com/Foxboron/sbctl) | ★ n/v; 2026-09-06 | MIT | Secure Boot key manager | Owner-controlled Secure Boot keys for USB vault boots | M | Firmware variance | try |
| systemd-cryptenroll ([systemd](https://github.com/systemd/systemd)) | – | LGPL-2.1+ | TPM2 / FIDO2 LUKS unlock | TPM unlock bound to PCRs + a PIN. Add the phone as a FIDO2 key later. | S | Recovery-key UX | adopt |
| [latchset/clevis](https://github.com/latchset/clevis) | ★ n/v; 2026-10-29 | GPL-3.0 | Automated unlock (TPM, Tang network server) | Unlock at home when on the home network (Tang) | M | – | watch |
| [USBGuard/usbguard](https://github.com/USBGuard/usbguard) | ★ n/v; 2026-09-07 | GPL-2.0 | USB device allow/deny policy | Block unknown USB devices (BadUSB); allow the owner's vault stick only | S | Lock-out risk; needs an approval UX | adopt |
| [evilsocket/opensnitch](https://github.com/evilsocket/opensnitch) | ★ n/v; 2026-07-27 | GPL-3.0 | Per-app outbound firewall | "Exposure map" made real: block apps from phoning home, with approve-by-phone | M | Prompt fatigue | try |
| [safing/portmaster](https://github.com/safing/portmaster) | ★ n/v; 2026-10-30 | GPL-3.0 | Privacy firewall with DNS filtering | Alternative to OpenSnitch | M | Heavier | watch |
| [a13xp0p0v/kernel-hardening-checker](https://github.com/a13xp0p0v/kernel-hardening-checker) | ★ n/v; 2026-09-19 | GPL-3.0 | Checks kernel config and sysctl hardening | Settings → Security score | S | – | adopt |
| [CISOfy/lynis](https://github.com/CISOfy/lynis) | ★ n/v; 2026-09-16 | GPL-3.0 | Security audit | Weekly audit for the health monitor | S | – | adopt |
| [rfjakob/earlyoom](https://github.com/rfjakob/earlyoom) | ★ n/v; 2026-10-26 | MIT | Early OOM killer | Stops the desktop freezing when a model is too big. Prefer systemd-oomd if that is already on. | S | – | adopt |
| [fwupd/fwupd](https://github.com/fwupd/fwupd) | ★ n/v; 2026-11-06 | LGPL-2.1 | Firmware updates (LVFS) | Firmware updates inside the Orion update flow | S | – | adopt |
| [rust-minidump/rust-minidump](https://github.com/rust-minidump/rust-minidump) | ★ n/v; 2026-11-03 | MIT | Minidump processing | Local-only crash reports for the Tauri HUD, read by the crash healer | M | – | try |
| [getsentry/sentry](https://github.com/getsentry/sentry) | ★ n/v; 2026-11-07 | FSL (not open source) | Crash reporting | – | – | Licence; telemetry model | skip |
| [canonical/ubuntu-image](https://github.com/canonical/ubuntu-image) | ★ n/v; 2026-11-04 | GPL-3.0 | Builds Ubuntu images (classic and core) | Alternative to mkosi that Canonical supports | M | Snap-centric | watch |
| [calamares/calamares](https://github.com/calamares/calamares) | ★ n/v; 2025-05-06 | GPL-3.0+ | Distro installer | Orion has its own installer. Use this only if a GUI partitioner is needed. | M | Activity slowed (last commit May 2025) | watch |
| [topgrade-rs/topgrade](https://github.com/topgrade-rs/topgrade) | ★ n/v; 2026-11-07 | GPL-3.0 | One command to update apt, flatpak, pip and more | Reference for the "update everything" button | – | – | watch |

### 4.6 Creative and productivity

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [obsproject/obs-studio](https://github.com/obsproject/obs-studio) | ★ n/v; 2026-11-03 | GPL-2.0 | Recording and streaming | Craft video capture, driven through obs-websocket | S | – | adopt |
| [SeaDve/Kooha](https://github.com/SeaDve/Kooha) | 3.5k★ (topic/screen-recorder); 2026-11-06 | GPL-3.0 | Simple Wayland screen recorder | One-click "record this" in the menu | S | – | adopt |
| [siddharthvaddem/openscreen](https://github.com/siddharthvaddem/openscreen) | 40k★ (topic/screen-recorder); 2026-10-16 | MIT | Screen Studio-style demo recorder (auto zoom) | Polished product demos made by Craft | M | Cross-platform UI; Wayland capture not checked | try |
| [CapSoftware/Cap](https://github.com/CapSoftware/Cap) | 23k★ (topic/screen-recorder); 2026-11-07 | AGPL-3.0 | Loom alternative | – | – | AGPL | skip |
| [Satty-org/Satty](https://github.com/Satty-org/Satty) | ★ n/v; 2026-11-06 | MPL-2.0 | Screenshot annotation (Wayland) | Annotate before sharing to the phone or agents | S | – | adopt |
| [mifi/lossless-cut](https://github.com/mifi/lossless-cut) | ★ n/v; 2026-11-03 | GPL-2.0 | Lossless trimming | Fast Video Scout clip cuts | S | – | try |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | ★ n/v; 2026-11-07 | Unlicense | Removes silence and dead air automatically | Craft: rough-cut recordings automatically | S | – | adopt |
| [KDE/kdenlive](https://github.com/KDE/kdenlive) | ★ n/v; 2026-11-07 | GPL-3.0 | Video editor | The default NLE (video editor) app | – | – | adopt (bundle) |
| [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) | ★ n/v; 2026-11-06 | GPL-3.0 | Node-based image and video generation | PhotoCraft back end through its HTTP API, as a separate process | M | GPU, GPL | try |
| [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) | ★ n/v; 2026-11-08 | MIT | Diffusion in C++ (ggml), CPU/Vulkan | Light local image generation with no Python stack | S | – | try |
| [Acly/krita-ai-diffusion](https://github.com/Acly/krita-ai-diffusion) | ★ n/v; 2026-10-03 | GPL-3.0 | AI inpainting inside Krita | Art suite: Krita + ComfyUI | S | – | adopt (bundle) |
| [Sanster/IOPaint](https://github.com/Sanster/IOPaint) | ★ n/v; 2025-04-29 | Apache-2.0 | Object removal / inpainting | PhotoCraft "remove object" | S | Slowing (last commit Apr 2025) | try |
| [chaiNNer-org/chaiNNer](https://github.com/chaiNNer-org/chaiNNer) | ★ n/v; 2026-11-02 | GPL-3.0 | Node-based image-processing pipelines (upscalers) | Batch upscale and restore for the art suite | S | – | try |
| [adefossez/demucs](https://github.com/adefossez/demucs) | ★ n/v; 2026-10-26 | MIT | Stem separation | Separate a song into vocals, drums and so on for Craft audio | S | The original repo is archived; use this fork | try |
| [chidiwilliams/buzz](https://github.com/chidiwilliams/buzz) | ★ n/v; 2026-11-07 | MIT | Offline transcription app | Overlaps Orion | – | Duplicate | skip |
| [silverbulletmd/silverbullet](https://github.com/silverbulletmd/silverbullet) | ★ n/v; 2026-11-06 | MIT | Markdown PKM, scriptable | Notes app over a plain-Markdown vault that Orion can read and write | S | – | try |
| [laurent22/joplin](https://github.com/laurent22/joplin) | 56.8k★ (topic/note-taking); 2026-11-06 | AGPL-3.0+ (except sub-folders with their own licence) | Notes with end-to-end encrypted sync | User-side app only | – | AGPL | watch |
| [logseq/logseq](https://github.com/logseq/logseq) | 45.3k★ (topic/note-taking); 2026-11-06 | AGPL-3.0 | Outliner PKM | Same | – | AGPL | watch |
| [toeverything/AFFiNE](https://github.com/toeverything/AFFiNE) | ★ n/v; 2026-11-06 | MIT (root LICENSE; check the server's licence) | Notion-like docs and whiteboard | Optional workspace app | – | Mixed licence | watch |
| [mautrix/whatsapp](https://github.com/mautrix/whatsapp), [/signal](https://github.com/mautrix/signal), [/telegram](https://github.com/mautrix/telegram), [/gmessages](https://github.com/mautrix/gmessages), [/meta](https://github.com/mautrix/meta), [/discord](https://github.com/mautrix/discord), [/slack](https://github.com/mautrix/slack) | whatsapp 1.9k★, telegram 1.8k★, signal 681★, discord 509★, meta 469★ (topic/matrix-org); all 2026-11-05 to 2026-11-07 | AGPL-3.0 | Matrix puppeting bridges for the owner's own accounts | **One inbox on the PC** without phone-notification scraping. Run each bridge as a separate local service against a local homeserver. | M | AGPL (fine as a separate service); account ToS risk for WhatsApp/Meta | try |
| [matrix-construct/tuwunel](https://github.com/matrix-construct/tuwunel) / [continuwuity](https://github.com/continuwuity/continuwuity) | ★ n/v; 2026-11-07 / 2026-11-05 | Apache-2.0 | Lightweight Rust Matrix homeservers (conduwuit successors) | Tiny local homeserver just for the bridges | S | Forks churn | try |
| [beeper/bridge-manager](https://github.com/beeper/bridge-manager) | ★ n/v; 2026-11-02 | Apache-2.0 | CLI to run mautrix bridges | Simplifies bridge setup (it can run self-hosted) | S | Beeper-oriented | watch |
| [42wim/matterbridge](https://github.com/42wim/matterbridge) | ★ n/v; 2024-08-27 | Apache-2.0 | Multi-protocol chat bridge | Inactive | – | Stale | skip |
| [pimalaya/himalaya](https://github.com/pimalaya/himalaya) | ★ n/v; 2026-11-06 | Apache-2.0 / MIT | Email CLI (IMAP/SMTP) with JSON output | Orion's email read and draft through a CLI; Orion never sends without a tap | S | – | adopt |
| [pimutils/vdirsyncer](https://github.com/pimutils/vdirsyncer) + [khal](https://github.com/pimutils/khal) | ★ n/v; 2026-09-03 / 2026-10-20 | BSD-3 / MIT | CalDAV/CardDAV sync + CLI calendar | Local calendar files the assistant can read | S | – | adopt |
| [Kozea/Radicale](https://github.com/Kozea/Radicale) | ★ n/v; 2026-11-07 | GPL-3.0 | Small CalDAV/CardDAV server | Self-host calendar and contacts for phone + PC (DAVx5) | S | – | try |
| [keepassxreboot/keepassxc](https://github.com/keepassxreboot/keepassxc) | ★ n/v; 2026-11-05 | GPL-2/3 | Password manager with Secret Service support | Default vault. Orion never reads secrets; it only asks the user to unlock. | S | – | adopt (bundle) |
| [dani-garcia/vaultwarden](https://github.com/dani-garcia/vaultwarden) | ★ n/v; 2026-11-04 | AGPL-3.0 | Bitwarden-compatible server | Optional self-hosted family passwords | M | AGPL service | watch |
| [rclone/rclone](https://github.com/rclone/rclone) | ★ n/v; 2026-11-06 | MIT | Sync and mount for 70+ storage providers | Move-my-stuff (Drive, OneDrive to vault), and the restic back end | S | – | adopt |
| [immich-app/immich](https://github.com/immich-app/immich) | ★ n/v; 2026-11-07 | AGPL-3.0 | Self-hosted photos with local ML | Phone photo backup to the PC with face and CLIP search | M | AGPL service, heavy | try |
| [paperless-ngx/paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | 47.8k★ (topic/ocr); 2026-11-07 | GPL-3.0 | Document archive with OCR | "Scan receipts with the phone → filed on the PC" | M | – | watch |
| [Stirling-Tools/Stirling-PDF](https://github.com/Stirling-Tools/Stirling-PDF) | ★ n/v; 2026-11-06 | MIT (root) | PDF toolbox | PDF merge, split and sign skills | S | Some parts may be proprietary; check | watch |
| [excalidraw/excalidraw](https://github.com/excalidraw/excalidraw) | ★ n/v; 2026-11-05 | MIT | Whiteboard (React) | Embed in the HUD (React) for sketch-to-plan | S | – | adopt |
| [ActivityWatch/activitywatch](https://github.com/ActivityWatch/activitywatch) | ★ n/v; 2026-11-06 | MPL-2.0 | Local time tracking | Pacing tracker data source | S | Wayland watcher support is limited | try |
| [screenpipe/screenpipe](https://github.com/screenpipe/screenpipe) | 21k★ (topic/speech-to-text); 2026-11-06 | **Screenpipe Commercial License** | Screen and audio recall | – | – | Licence | skip |

### 4.7 Game dev ("make a game with Claude")

| Repo | Stars / last commit | Licence | What it does | How Orion would use it | Effort | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| [godotengine/godot](https://github.com/godotengine/godot) | 118k★ (topic/godot); 2026-11-07 | MIT | Game engine | Default engine for starters | – | – | adopt |
| [htdt/godogen](https://github.com/htdt/godogen) | 7.2k★ ([repo](https://github.com/htdt/godogen)); probe n/v | MIT | Claude Code / Codex skill that builds whole games (Godot, Bevy, three.js), makes assets, runs the engine and checks the result | **The "make a game" mission template.** Wrap it as a Mission Control recipe. | S | Young | adopt |
| [Donchitos/Claude-Code-Game-Studios](https://github.com/Donchitos/Claude-Code-Game-Studios) | 31.9k★ ([repo](https://github.com/Donchitos/Claude-Code-Game-Studios)) | MIT | 49 agent roles and 74 skills that act as a game studio | Take only the roles you need (designer, QA). The full set is heavy on tokens. | S | Token cost | try |
| [Coding-Solo/godot-mcp](https://github.com/Coding-Solo/godot-mcp) | 6k★ (topic/godot); 2026-04-16 | MIT | MCP: run, debug and edit Godot projects | Put it on the MCP bus | S | Slowing | adopt |
| [beckettlab/beckett-godot-mcp](https://github.com/beckettlab/beckett-godot-mcp) | 15★ (topic/godot-mcp); 2026-11-01 | MIT | In-editor MCP with no Node.js | Lighter alternative | S | Tiny project | watch |
| [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) | ★ n/v; 2026-11-04 | MIT | Blender control through MCP | Asset blocking-out and 3D Craft | S | Runs arbitrary Python in Blender; sandbox it | try |
| [MikeSchulze/gdUnit4](https://github.com/MikeSchulze/gdUnit4) | ★ n/v; 2026-11-06 | MIT | Godot unit and scene tests | Lets the proof gate check AI-made games | S | – | adopt |
| [limbonaut/limboai](https://github.com/limbonaut/limboai) / [bitbrain/beehave](https://github.com/bitbrain/beehave) | ★ n/v; 2026-10-30 / 2026-09-20 | MIT | Behaviour trees and state machines | NPC AI in starters | S | – | try |
| [nathanhoad/godot_dialogue_manager](https://github.com/nathanhoad/godot_dialogue_manager) | 3.9k★ (topic/godot); 2026-11-06 | MIT | Dialogue scripting | Story-game starter; pairs with StoryTold | S | – | adopt |
| [bevyengine/bevy](https://github.com/bevyengine/bevy) + [TheBevyFlock/bevy_new_2d](https://github.com/TheBevyFlock/bevy_new_2d) | ★ n/v; 2026-11-07 / 2026-11-05 | MIT / Apache-2.0 | Rust ECS engine + jam template | "Rust game" starter | S | API churn | try |
| [phaserjs/phaser](https://github.com/phaserjs/phaser) | ★ n/v; 2026-08-21 | MIT | HTML5 2D engine | Instant browser games that run inside the HUD or the phone | S | – | adopt |
| [nesbox/TIC-80](https://github.com/nesbox/TIC-80) | ★ n/v; 2026-11-07 | MIT | Fantasy console | "First game in 10 minutes" starter for kids | S | – | try |
| [Orama-Interactive/Pixelorama](https://github.com/Orama-Interactive/Pixelorama) | 10.5k★ (topic/godot); 2026-11-07 | MIT | Pixel-art editor | Bundled sprite editor | S | – | adopt |
| [deepnight/ldtk](https://github.com/deepnight/ldtk) / [mapeditor/tiled](https://github.com/mapeditor/tiled) | ★ n/v; 2026-10-06 / 2026-10-24 | MIT / GPL-2 app (BSD libraries) | Level editors | Level JSON that agents can write | S | – | try |
| [mxgmn/WaveFunctionCollapse](https://github.com/mxgmn/WaveFunctionCollapse) / [MarkovJunior](https://github.com/mxgmn/MarkovJunior) | 25.4k★ / 8.5k★ (topic/procedural-generation) | MIT | Procedural generation | Procedural level starters | S | – | try |
| [Auburn/FastNoiseLite](https://github.com/Auburn/FastNoiseLite) | 3.5k★ (topic/procedural-generation); 2026-06-21 | MIT | Noise library | Terrain and black-hole shader noise | S | – | adopt |
| [RodZill4/material-maker](https://github.com/RodZill4/material-maker) | 6.0k★ (topic/procedural-generation); 2026-10-24 | MIT | Procedural textures (Godot) | Texture generation | S | – | try |
| [microsoft/TRELLIS](https://github.com/microsoft/TRELLIS) / [VAST-AI-Research/TripoSR](https://github.com/VAST-AI-Research/TripoSR) | ★ n/v; 2026-10-27 / 2026-06-04 | MIT (code; check the weights) | Image → 3D mesh | Prop generation for starters | M | GPU, weight licences | try |
| [Tencent-Hunyuan/Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2) | ★ n/v | Community licence, **not valid in the EU, UK or South Korea** | Image → 3D | – | – | Licence | skip |
| [chr15m/jsfxr](https://github.com/chr15m/jsfxr) | ★ n/v; 2026-05-05 | Unlicense | Retro sound-effect generator | SFX for starters | S | – | adopt |

**Shortlist count:** 96 rows. Some rows cover a bundle of repos (the mautrix bridges, syft and grype, vdirsyncer and khal), so the total is about 110 repos.

---

## 5. Top 25 recommendations, ranked by value for effort

| # | Recommendation | Repos | Feature it improves | Effort |
|---|---|---|---|---|
| 1 | Push wake-up instead of polling: the daemon publishes to self-hosted ntfy, and Orion Mobile implements the UnifiedPush receiver intents | [ntfy](https://github.com/binwiederhier/ntfy), [ntfy-android](https://github.com/binwiederhier/ntfy-android), [UnifiedPush](https://github.com/UnifiedPush/android-connector) | Approvals arrive in seconds without the battery-draining LiveService | S–M |
| 2 | Sandbox every coding agent | [sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime), [bubblewrap](https://github.com/containers/bubblewrap) | Mission Control safety; protected core | S |
| 3 | Better turn-taking | [silero-vad](https://github.com/snakers4/silero-vad), [smart-turn](https://github.com/pipecat-ai/smart-turn) | Voice feels natural | S |
| 4 | Screen OCR upgrade | [RapidOCR](https://github.com/RapidAI/RapidOCR) | Screen OCR, Video Scout | S |
| 5 | Lighter embeddings | [fastembed](https://github.com/qdrant/fastembed) | Knowledge vault without 2 GB of PyTorch | S |
| 6 | Better document ingestion | [docling](https://github.com/docling-project/docling), [markitdown](https://github.com/microsoft/markitdown), [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | Vault, email attachments | S |
| 7 | SBOM and vulnerability scans for images and releases | [syft](https://github.com/anchore/syft), [grype](https://github.com/anchore/grype), [osv-scanner](https://github.com/google/osv-scanner) | Signed updates, dep autopilot | S |
| 8 | Godot "make a game" recipe | [godogen](https://github.com/htdt/godogen), [godot-mcp](https://github.com/Coding-Solo/godot-mcp), [gdUnit4](https://github.com/MikeSchulze/gdUnit4) | Game starters with a test gate | S |
| 9 | Network-change reconnect + racing several addresses on the phone | Android platform APIs; design from [Tailscale NAT post](https://tailscale.com/blog/nat-traversal-improvements-pt-1) | Link reliability (section 7) | S |
| 10 | Prompt-injection regression tests | [agentdojo](https://github.com/ethz-spylab/agentdojo), [promptfoo](https://github.com/promptfoo/promptfoo) | Injection firewall | S |
| 11 | Global hotkeys and key layers | [keyd](https://github.com/rvaiya/keyd), [swhkd](https://github.com/waycrate/swhkd) | Hotkeys/macros on every compositor | S |
| 12 | Display hot-plug and fractional scaling | [way-displays](https://github.com/alex-courtis/way-displays), [wl-mirror](https://github.com/Ferdi265/wl-mirror) | "It just works" monitors and presenting | S |
| 13 | USB device policy | [usbguard](https://github.com/USBGuard/usbguard) | USB vault and hardening | S |
| 14 | Phone mirror and control on the HUD | [scrcpy](https://github.com/Genymobile/scrcpy) | Phone ↔ PC | S |
| 15 | MCP dev and browser servers | [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp), [playwright-mcp](https://github.com/microsoft/playwright-mcp), [MCP servers](https://github.com/modelcontextprotocol/servers), [inspector](https://github.com/modelcontextprotocol/inspector) | MCP bus for coding agents | S |
| 16 | One proxy for many local models | [llama-swap](https://github.com/mostlygeek/llama-swap) | Model manager | S |
| 17 | Reproducible A/B image | [mkosi](https://github.com/systemd/mkosi) + systemd-sysupdate, or [rugix](https://github.com/silitics/rugix) | USB vault, A/B signed updates | M |
| 18 | TPM + PIN unlock | systemd-cryptenroll ([systemd](https://github.com/systemd/systemd)), [sbctl](https://github.com/Foxboron/sbctl) | Encrypted vault UX | M |
| 19 | KDE Connect-compatible listener | [kcd](https://pkg.go.dev/github.com/bethropolis/kcd), [Valent](https://github.com/andyholmes/valent) | Works with any KDE Connect phone; fallback when Orion Mobile isn't installed | M |
| 20 | Phone STT and on-device fallback | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [moonshine](https://github.com/moonshine-ai/moonshine) | Pocket Orion, weak PCs | M |
| 21 | Self-hosted control plane | [headscale](https://github.com/juanfont/headscale) | Phone link with no third-party coordination server | M |
| 22 | Unified inbox from real bridges | [mautrix/*](https://github.com/mautrix/whatsapp), [tuwunel](https://github.com/matrix-construct/tuwunel) | One inbox on the PC, not only phone notifications | M |
| 23 | GUI agents in a throwaway Linux sandbox | [cua](https://github.com/trycua/cua), [OmniParser](https://github.com/microsoft/OmniParser) | Safe computer-use; skill-recorder vision fallback | M |
| 24 | Fully local coding agent | [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), [opencode](https://github.com/sst/opencode), [agentapi](https://github.com/coder/agentapi) | Mission Control offline | S–M |
| 25 | Outbound firewall with phone approval | [opensnitch](https://github.com/evilsocket/opensnitch) | Exposure map, privacy | M |

---

## 6. Fifteen new feature ideas these repos make possible

1. **Instant approvals with no battery drain ("Wake Ping").** The daemon pushes a tiny "something is waiting" ping. The phone wakes, fetches the details over the signed API and shows the fingerprint prompt. No content goes through the push server. Repos: [ntfy](https://github.com/binwiederhier/ntfy), [ntfy-android](https://github.com/binwiederhier/ntfy-android), [UnifiedPush](https://github.com/UnifiedPush/android-connector).
2. **Agent Cages.** Every Mission Control agent gets a visible "cage card": which folder it can write to, which domains it can reach, and a live list of blocked attempts. The owner approves widening a cage from the phone. Repos: [sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime), [bubblewrap](https://github.com/containers/bubblewrap), [pipelock](https://github.com/luckyPipewrench/pipelock) (ideas), [gvisor](https://github.com/google/gvisor) as a stronger tier.
3. **Game Forge.** "Make me a platformer about my dog" builds a Godot project, generates sprites and SFX, runs gdUnit4 tests and records a 20-second trailer for approval. Repos: [godogen](https://github.com/htdt/godogen), [godot-mcp](https://github.com/Coding-Solo/godot-mcp), [gdUnit4](https://github.com/MikeSchulze/gdUnit4), [Pixelorama](https://github.com/Orama-Interactive/Pixelorama), [jsfxr](https://github.com/chr15m/jsfxr), [Kooha](https://github.com/SeaDve/Kooha).
4. **Phone on the HUD.** The phone's screen appears as a glass window in the HUD, and an approved agent mission can drive phone apps. Repos: [scrcpy](https://github.com/Genymobile/scrcpy), [droidrun](https://github.com/droidrun/droidrun).
5. **Real one inbox.** WhatsApp, Signal, Telegram, Google Messages, Discord and Slack arrive on the PC through local bridges. Orion drafts replies, and they are only sent after the owner taps. Repos: [mautrix bridges](https://github.com/mautrix/signal), [tuwunel](https://github.com/matrix-construct/tuwunel), [bridge-manager](https://github.com/beeper/bridge-manager).
6. **Phone Trust Check.** Before accepting a fingerprint-signed approval for money or public posts, the PC checks a hardware attestation that the phone's OS is unmodified. Repo: [GrapheneOS/Auditor](https://github.com/GrapheneOS/Auditor).
7. **Boot Yesterday.** A "go back to how it was this morning" button. It boots a btrfs snapshot, and with A/B images falls back to the last good image automatically if the new one fails to boot. Repos: [grub-btrfs](https://github.com/Antynea/grub-btrfs), [snapper](https://github.com/openSUSE/snapper), [mkosi](https://github.com/systemd/mkosi) / systemd-sysupdate, [rugix](https://github.com/silitics/rugix).
8. **Unlock with PC + phone.** LUKS unlocks automatically with the TPM at home. Away from home it asks for a PIN, or later a phone FIDO2 key. Repos: systemd-cryptenroll, [clevis](https://github.com/latchset/clevis) (Tang for "home network" unlock), [sbctl](https://github.com/Foxboron/sbctl).
9. **USB Gatekeeper.** Unknown USB devices are blocked until approved on the phone. The vault stick is always allowed. Repo: [usbguard](https://github.com/USBGuard/usbguard).
10. **Who-Talks-Out map.** A live map of which app contacted which server, with block/allow by tap. It feeds the existing Exposure Map. Repos: [opensnitch](https://github.com/evilsocket/opensnitch), [portmaster](https://github.com/safing/portmaster).
11. **Meeting Mode.** Local transcription with speaker names, live translation subtitles, and an action-item list into Promise Keeper. Repos: [whisperX](https://github.com/m-bain/whisperX), [pyannote-audio](https://github.com/pyannote/pyannote-audio), [mozilla/translations](https://github.com/mozilla/translations), [argos-translate](https://github.com/argosopentech/argos-translate), [easyeffects](https://github.com/wwmm/easyeffects) for clean audio.
12. **Doc Drop.** Drop any PDF, scan, slide deck or phone photo of a receipt. Orion extracts clean Markdown and tables, files it in the vault and makes it searchable offline. Repos: [docling](https://github.com/docling-project/docling), [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF), [RapidOCR](https://github.com/RapidAI/RapidOCR), [fastembed](https://github.com/qdrant/fastembed), [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) (optional).
13. **Ghost Desktop.** GUI agents work in a disposable Linux desktop the owner can watch in a HUD window. They never touch the real desktop until the owner approves "replay on my desktop". Repos: [cua](https://github.com/trycua/cua), [OmniParser](https://github.com/microsoft/OmniParser), [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL), [wayvnc](https://github.com/any1/wayvnc).
14. **Offline Brain on the stick.** A single-file rescue model plus a small STT model on the USB vault, so Orion can help repair a machine with no install and no network. Repos: [llamafile](https://github.com/mozilla-ai/llamafile), [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [moonshine](https://github.com/moonshine-ai/moonshine).
15. **Supply-Chain Receipt.** Every update and every agent-made release carries an SBOM, a vulnerability report and a signature. The phone shows "3 new packages, 0 known issues" before the owner approves. Repos: [syft](https://github.com/anchore/syft), [grype](https://github.com/anchore/grype), [osv-scanner](https://github.com/google/osv-scanner), [cosign](https://github.com/sigstore/cosign) (optional), with minisign kept as is.

---

## 7. Making the phone–PC link more stable

**How the link works now** (from the local code):
- Pairing finds the PC with `NsdManager` mDNS (`_orion._tcp`) or a QR code (`PairPc.kt`).
- Remote access goes through Tailscale.
- Background updates come from a **15-minute `JobScheduler` poll** (`PollJob.kt`; the comment notes 15 minutes is Android's minimum).
- An optional foreground `LiveService` polls within seconds.
- Files go through a LocalSend hand-off (`PhoneToPc.kt`).
- The Android app allows **no third-party libraries**, so every technique below uses only platform APIs or plain protocols.

### 7.1 Techniques, in order of impact

1. **Push wake-up, then pull** (biggest win for both battery and speed).
   - The daemon POSTs a content-free ping to a ntfy topic. Host ntfy on the PC or the Boardroom box and reach it over Tailscale, or use ntfy.sh with a random topic and no payload.
   - On the phone, ntfy acts as the UnifiedPush **distributor**. Orion Mobile registers through the UnifiedPush broadcast-intent protocol, with no library needed, and wakes on each message to fetch `/phone/home` with its signed read key.
   - This removes the need to run LiveService all the time. Keep the 15-minute job as a safety net.
   - Sources: [ntfy](https://github.com/binwiederhier/ntfy), [ntfy-android](https://github.com/binwiederhier/ntfy-android), [UnifiedPush connector (reference)](https://github.com/UnifiedPush/android-connector), [Nextcloud's UnifiedPush explainer (2026-09-01)](https://nextcloud.com/blog/unifiedpush-android-nextcloud-talk/). That explainer also warns that always-on background connections drain battery and get killed by battery optimisation. That is a reason to rely on one distributor app rather than Orion's own socket.
2. **React to network changes immediately.**
   - Register `ConnectivityManager.registerDefaultNetworkCallback` (API 24+, so fine at minSdk 30).
   - On `onAvailable` or `onCapabilitiesChanged` (Wi-Fi joined, VPN up), run one check straight away instead of waiting up to 15 minutes.
   - When the phone joins the home Wi-Fi, also restart NSD discovery.
3. **Race every known path** ("happy eyeballs").
   - Store every address the PC has ever shown: LAN IPv4, IPv6, mDNS hostname, Tailscale 100.x and MagicDNS name, and the Headscale or relay URL.
   - On each connect, try them in parallel with a short stagger (≈250 ms). Use the first that answers `/phone/home` with the right pinned certificate or key, and remember the winner.
   - This is the same idea as Tailscale's "magicsock path racing" across IPv4, IPv6 and different network interfaces ([Tailscale, 2025-10-15](https://tailscale.com/blog/nat-traversal-improvements-pt-1)).
4. **Discovery with two methods, like KDE Connect.**
   - mDNS can be filtered on some routers and mesh Wi-Fi.
   - KDE Connect implementations use **UDP broadcast on port 1716 plus mDNS `_kdeconnect._udp`**, and reconnect to remembered IPs even when broadcast is off ([kcd docs](https://pkg.go.dev/github.com/bethropolis/kcd)).
   - Add a UDP broadcast "Orion here" beacon on a fixed port as a fallback, and always try the last known IP first.
5. **Pin identity, not addresses.**
   - Trust the PC by its key fingerprint (as KDE Connect does: SHA-256 of self-signed certs, [kcd](https://pkg.go.dev/github.com/bethropolis/kcd)). An IP change or DHCP lease change then never breaks pairing.
   - Orion already signs requests with phone keys; add a pinned **PC** key check on responses.
6. **Relay fallback that always works.**
   - Tailscale's own numbers: about 90% of connections go direct. The rest use DERP relays over HTTPS on port 443, which work even when UDP is blocked ([Tailscale](https://tailscale.com/blog/nat-traversal-improvements-pt-1)).
   - Keep Tailscale, and self-host **[Headscale](https://github.com/juanfont/headscale)** (BSD-3) plus a DERP server, so the link doesn't depend on a third party.
   - For a future link with no VPN, **[iroh](https://github.com/n0-computer/iroh)** gives QUIC hole punching with relays and "dial by public key". Use it from Rust on the PC and in Pocket Orion's Linux terminal.
7. **Keep NAT mappings alive** across router reboots. Tailscale added NAT-PMP/PCP "epoch" monitoring to re-create port mappings after a router reset ([same post](https://tailscale.com/blog/nat-traversal-improvements-pt-1)). If Orion opens ports itself (it shouldn't by default), use [miniupnp](https://github.com/miniupnp/miniupnp) (BSD) and refresh the mapping when the router's epoch resets.
8. **Long-poll or Server-Sent Events while the app is in the foreground.** When the app or launcher is visible, hold one HTTP request open (≤ 60 s), not a loop of quick polls. Close it on `onStop`. This gives near-instant updates with no foreground service.
9. **Back off with jitter and a circuit breaker.**
   - On failure, retry at 2 s → 4 s → 8 s … up to 5 minutes, with ±20% jitter.
   - Reset the backoff on any network-change callback.
   - Show "PC asleep" and "Tailscale off" states distinctly. `PairPc.kt` already detects VPN state.
10. **Wake the PC.** If the PC is asleep, send a Wake-on-LAN magic packet on the LAN, or ask an always-on Boardroom box to send it over Tailscale. Then race the paths again.
11. **Pair without a camera.** Add 6-digit code pairing with SPAKE2, as magic-wormhole does. It protects against an attacker on the same Wi-Fi without needing QR. Repos: [python-spake2](https://github.com/warner/python-spake2) (MIT) on the PC; on the phone, a small hand port on top of the platform's java.security / javax.crypto APIs.
12. **Phone-side health card.** Show the last success, which path won (LAN, mDNS, Tailscale or relay), the time a push took, and the current backoff. Debugging drops from guesswork to one glance. Tailscale's `netcheck` is the model ([post](https://tailscale.com/blog/nat-traversal-improvements-pt-1)).
13. **Compatibility mode.** Run [Valent](https://github.com/andyholmes/valent), or speak the KDE Connect protocol in the daemon (MIT reference [kcd](https://pkg.go.dev/github.com/bethropolis/kcd)), so a stock KDE Connect app gives notifications, clipboard and files even before Orion Mobile is installed, or on a second phone.
14. **Battery rules on GrapheneOS and stock Android.**
   - Ask once for the "unrestricted battery" exemption, but only if the owner turns on instant alerts.
   - Prefer the distributor (ntfy) holding the single socket.
   - Never hold wakelocks while waiting.

### 7.2 Repos behind this section

| Repo | Role |
|---|---|
| [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy) | Push server |
| [binwiederhier/ntfy-android](https://github.com/binwiederhier/ntfy-android) | UnifiedPush distributor |
| [UnifiedPush/android-connector](https://github.com/UnifiedPush/android-connector) | Protocol reference |
| [juanfont/headscale](https://github.com/juanfont/headscale) | Self-hosted control plane |
| [tailscale/tailscale](https://github.com/tailscale/tailscale) | Mesh in use; DERP server |
| [n0-computer/iroh](https://github.com/n0-computer/iroh) | Library-level P2P with relays |
| [EasyTier/EasyTier](https://github.com/EasyTier/EasyTier) | Fallback mesh with no central server |
| [andyholmes/valent](https://github.com/andyholmes/valent) | KDE Connect compatibility |
| [kcd](https://pkg.go.dev/github.com/bethropolis/kcd) | KDE Connect compatibility, headless and MIT |
| [miniupnp/miniupnp](https://github.com/miniupnp/miniupnp) | Port mapping |
| [paullouisageneau/libjuice](https://github.com/paullouisageneau/libjuice) (MPL-2.0) / [pion/webrtc](https://github.com/pion/webrtc) / [aiortc](https://github.com/aiortc/aiortc) | ICE/WebRTC for media streams |
| [coturn/coturn](https://github.com/coturn/coturn) | TURN relay |
| [warner/python-spake2](https://github.com/warner/python-spake2) | Code pairing |
| [syncthing/syncthing](https://github.com/syncthing/syncthing) | Robust file sync with its own relays |

---

## 8. Skip list: duplicates, licence problems, inactive projects

- **Duplicates Orion's own work.**
  - Coding-agent orchestration: claude-squad (AGPL), vibe-kanban (sunsetting), crystal, container-use, Vigla, the worktree tools on [topic/git-worktrees](https://github.com/topics/git-worktrees).
  - Assistant frameworks: LocalAI, Open WebUI, AnythingLLM, Leon, Jan, GPT4All (last commit 2025-05-27).
  - Memory: mem0, txtai, Letta.
  - Transcription apps: Buzz, RealtimeSTT.
  - Guards: llm-guard and Rebuff (Rebuff's last commit was 2024-01-25).
  - Backup engines: borg, kopia.
  - File send: croc, PairDrop.
  - Agent swarms: ruflo / claude-flow.
- **Licence problems** (checked in each repo's licence file):
  - Commercial or other non-free licences: [screenpipe](https://github.com/mediar-ai/screenpipe) (commercial), [tldraw](https://github.com/tldraw/tldraw), [remotion](https://github.com/remotion-dev/remotion) (company licence), [gitbutler](https://github.com/gitbutlerapp/gitbutler) (FSL-1.1-MIT), [Arize Phoenix](https://github.com/Arize-ai/phoenix) (Elastic), [Sentry](https://github.com/getsentry/sentry) (FSL).
  - Model or community licences: [fish-speech](https://github.com/fishaudio/fish-speech), [index-tts](https://github.com/index-tts/index-tts), [Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2), [stable-fast-3d](https://github.com/Stability-AI/stable-fast-3d), [RunAnywhere SDKs](https://github.com/RunanywhereAI/runanywhere-sdks).
  - Source-available: [anytype-ts](https://github.com/anyproto/anytype-ts).
  - Proprietary: [claude-code](https://github.com/anthropics/claude-code). It is "All rights reserved", so drive the installed CLI but never vendor it.
  - Mixed licences:
    - [ZeroTier](https://github.com/zerotier/ZeroTierOne): MPL core plus a `nonfree/` folder.
    - [meilisearch](https://github.com/meilisearch/meilisearch): MIT plus BUSL enterprise edition.
    - [ydotool](https://github.com/ReimuNotMoe/ydotool): AGPL.
    - [trufflehog](https://github.com/trufflesecurity/trufflehog): AGPL, and duplicates the secret shield.
- **Inactive or slowing** (last commit date from git):
  - The official syncthing-android (2024-12-03); use the forks.
  - rhasspy/piper (2025-08-26; development moved to the GPL piper1-gpl).
  - coqui-ai/TTS (2024-02-10; use the idiap fork if needed).
  - matterbridge (2024-08-27).
  - AUTOMATIC1111 (2024-07-27).
  - Peek (2025-09-24).
  - OpenDoas (2022).
  - The emersion/kanshi GitHub mirror (2021; development is on sourcehut).
  - teejee2008/timeshift (moved to linuxmint/timeshift).
  - Real-ESRGAN (2024-04-03).
  - GroundingDINO (2024-08-12).

---

## Appendix A: every repo URL checked (last commit date and licence signals)

Generated on 2026-11-07 with a shallow, blob-less `git clone` per repo. "Licence signals" are keywords found in the root licence file(s):
- `GPL;v3` means GPL-3.0.
- `LGPL;GPL` means LGPL (the LGPL text quotes the GPL).
- `CC` usually means a REUSE `LICENSES/` folder with several licences.
- `NONCOMMERCIAL?` is only a warning flag. It also fires on harmless phrases (for example yt-dlp's Unlicense), so check by hand.
- `UNRECOGNISED` and `NO-LICENSE-FILE-AT-ROOT` need a manual look. Several were resolved in the main text: mkosi LGPL-2.1+, river GPL-3.0, pgvector PostgreSQL licence, raylib zlib, Joplin AGPL-3.0+, calamares GPL-3.0+, termux-app GPLv3, timeshift GPL-2.0+, kdeconnect-kde and GSConnect GPL, Claude Code proprietary.

Old repo names that redirect appear twice (for example `LGFae/swww` and `Horus645/swww`, `DS4SD/docling` and `docling-project/docling`). 44 guessed URLs did not exist and are left out.

| Repo | Last commit (default branch, via git) | Licence signals in root licence file(s) |
|---|---|---|
| [2noise/ChatTTS](https://github.com/2noise/ChatTTS) | 2026-04-11 | AGPL;GPL; |
| [42wim/matterbridge](https://github.com/42wim/matterbridge) | 2024-08-27 | Apache-2.0; |
| [89luca89/distrobox](https://github.com/89luca89/distrobox) | 2026-11-02 | GPL;v3; |
| [89luca89/lilipod](https://github.com/89luca89/lilipod) | 2026-02-13 | GPL;v3; |
| [AUTOMATIC1111/stable-diffusion-webui](https://github.com/AUTOMATIC1111/stable-diffusion-webui) | 2024-07-27 | AGPL;GPL; |
| [Acly/krita-ai-diffusion](https://github.com/Acly/krita-ai-diffusion) | 2026-10-03 | GPL;v3; |
| [ActivityWatch/activitywatch](https://github.com/ActivityWatch/activitywatch) | 2026-11-06 | GPL;MPL-2.0; |
| [AdguardTeam/AdGuardHome](https://github.com/AdguardTeam/AdGuardHome) | 2026-11-03 | GPL;v3; |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | 2026-05-22 | Apache-2.0; |
| [Alexays/Waybar](https://github.com/Alexays/Waybar) | 2026-11-05 | MIT; |
| [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | 2026-11-07 | MIT; |
| [Anjok07/ultimatevocalremovergui](https://github.com/Anjok07/ultimatevocalremovergui) | 2026-10-06 | NO-LICENSE-FILE-AT-ROOT |
| [Antynea/grub-btrfs](https://github.com/Antynea/grub-btrfs) | 2026-08-24 | GPL;v3; |
| [AppFlowy-IO/AppFlowy](https://github.com/AppFlowy-IO/AppFlowy) | 2026-10-28 | AGPL;GPL; |
| [ArchiveBox/ArchiveBox](https://github.com/ArchiveBox/ArchiveBox) | 2026-11-07 | MIT; |
| [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) | 2026-11-07 | Elastic; |
| [AsamK/signal-cli](https://github.com/AsamK/signal-cli) | 2026-11-07 | GPL;v3; |
| [Auburn/FastNoiseLite](https://github.com/Auburn/FastNoiseLite) | 2026-06-21 | MIT; |
| [Audacity/audacity](https://github.com/Audacity/audacity) | 2026-11-06 | LGPL;GPL;v2;CC; |
| [AvengeMedia/DankMaterialShell](https://github.com/AvengeMedia/DankMaterialShell) | 2026-11-07 | MIT; |
| [Aylur/ags](https://github.com/Aylur/ags) | 2026-04-09 | GPL;v3; |
| [Aylur/astal](https://github.com/Aylur/astal) | 2026-11-08 | LGPL;GPL; |
| [Beep6581/RawTherapee](https://github.com/Beep6581/RawTherapee) | 2026-10-13 | GPL;v3; |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | 2026-11-07 | Apache-2.0;MIT;BSD; |
| [Blaizzy/mlx-vlm](https://github.com/Blaizzy/mlx-vlm) | 2026-11-07 | MIT; |
| [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 2026-11-03 | Apache-2.0; |
| [BorisTheBrave/DeBroglie](https://github.com/BorisTheBrave/DeBroglie) | 2025-12-21 | MIT; |
| [BoundingBoxSoftware/Materialize](https://github.com/BoundingBoxSoftware/Materialize) | 2018-10-23 | GPL;v3; |
| [CISOfy/lynis](https://github.com/CISOfy/lynis) | 2026-09-16 | GPL;v3; |
| [CapSoftware/Cap](https://github.com/CapSoftware/Cap) | 2026-11-07 | AGPL;GPL; |
| [Catfriend1/syncthing-android](https://github.com/Catfriend1/syncthing-android) | 2026-11-06 | GPL;MPL-2.0; |
| [ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) | 2026-11-06 | Apache-2.0; |
| [Cloudef/bemenu](https://github.com/Cloudef/bemenu) | 2025-08-29 | LGPL;GPL;v3; |
| [Coding-Solo/godot-mcp](https://github.com/Coding-Solo/godot-mcp) | 2026-04-16 | MIT; |
| [Codium-ai/cover-agent](https://github.com/Codium-ai/cover-agent) | 2025-06-23 | AGPL;GPL; |
| [Codium-ai/pr-agent](https://github.com/Codium-ai/pr-agent) | 2026-11-08 | MIT; |
| [DS4SD/docling](https://github.com/DS4SD/docling) | 2026-11-07 | MIT; |
| [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | 2026-11-05 | MIT; |
| [Droid-ify/client](https://github.com/Droid-ify/client) | 2026-09-13 | GPL;v3; |
| [Duncaen/OpenDoas](https://github.com/Duncaen/OpenDoas) | 2022-02-21 | UNRECOGNISED |
| [EasyTier/EasyTier](https://github.com/EasyTier/EasyTier) | 2026-11-08 | LGPL;v3; |
| [ErikReider/SwayNotificationCenter](https://github.com/ErikReider/SwayNotificationCenter) | 2026-06-25 | GPL;v3; |
| [FedeDP/Clight](https://github.com/FedeDP/Clight) | 2026-03-04 | GPL;v3; |
| [Ferdi265/wl-mirror](https://github.com/Ferdi265/wl-mirror) | 2026-10-26 | GPL;v3; |
| [FiloSottile/age](https://github.com/FiloSottile/age) | 2026-08-29 | BSD; |
| [FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) | 2026-10-27 | MIT; |
| [Foxboron/sbctl](https://github.com/Foxboron/sbctl) | 2026-09-06 | MIT; |
| [FreshRSS/FreshRSS](https://github.com/FreshRSS/FreshRSS) | 2026-11-07 | AGPL;GPL; |
| [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice) | 2026-09-30 | MIT; |
| [FyroxEngine/Fyrox](https://github.com/FyroxEngine/Fyrox) | 2026-11-05 | MIT; |
| [GDQuest/godot-open-rpg](https://github.com/GDQuest/godot-open-rpg) | 2026-10-13 | MIT; |
| [GNOME/at-spi2-core](https://github.com/GNOME/at-spi2-core) | 2026-11-03 | LGPL;GPL; |
| [GNOME/gimp](https://github.com/GNOME/gimp) | 2026-11-07 | LGPL;GPL;v3;CC; |
| [GNOME/gnome-network-displays](https://github.com/GNOME/gnome-network-displays) | 2026-10-27 | GPL;v3; |
| [GNOME/gnome-remote-desktop](https://github.com/GNOME/gnome-remote-desktop) | 2026-11-05 | LGPL;GPL;v2; |
| [GNOME/orca](https://github.com/GNOME/orca) | 2026-11-07 | LGPL;GPL; |
| [GNOME/pitivi](https://github.com/GNOME/pitivi) | 2026-10-08 | UNRECOGNISED |
| [GSConnect/gnome-shell-extension-gsconnect](https://github.com/GSConnect/gnome-shell-extension-gsconnect) | 2026-10-18 | CC; |
| [Genymobile/gnirehtet](https://github.com/Genymobile/gnirehtet) | 2023-07-09 | Apache-2.0; |
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | 2026-10-08 | Apache-2.0; |
| [GhostNaN/mpvpaper](https://github.com/GhostNaN/mpvpaper) | 2026-08-28 | GPL;v3; |
| [GlitchTip/glitchtip](https://github.com/GlitchTip/glitchtip) | 2022-08-21 | NO-LICENSE-FILE-AT-ROOT |
| [GodotSteam/GodotSteam](https://github.com/GodotSteam/GodotSteam) | 2025-09-22 | NO-LICENSE-FILE-AT-ROOT |
| [GrapheneOS/Auditor](https://github.com/GrapheneOS/Auditor) | 2026-11-02 | MIT; |
| [GrapheneOS/Camera](https://github.com/GrapheneOS/Camera) | 2026-11-02 | MIT; |
| [H-M-H/Weylus](https://github.com/H-M-H/Weylus) | 2026-02-16 | AGPL;GPL; |
| [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) | 2026-10-14 | MIT; |
| [Hjdskes/cage](https://github.com/Hjdskes/cage) | 2026-11-01 | MIT; |
| [Horus645/swww](https://github.com/Horus645/swww) | 2025-10-31 | GPL;v3; |
| [HypothesisWorks/hypothesis](https://github.com/HypothesisWorks/hypothesis) | 2026-11-06 | MPL-2.0; |
| [IDEA-Research/GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) | 2024-08-12 | Apache-2.0; |
| [ImranR98/Obtainium](https://github.com/ImranR98/Obtainium) | 2026-11-01 | GPL;v3; |
| [InioX/matugen](https://github.com/InioX/matugen) | 2026-11-05 | LGPL;GPL;v2; |
| [JaidedAI/EasyOCR](https://github.com/JaidedAI/EasyOCR) | 2025-12-05 | Apache-2.0; |
| [JannisX11/blockbench](https://github.com/JannisX11/blockbench) | 2026-10-18 | GPL;v3; |
| [Jondolf/avian](https://github.com/Jondolf/avian) | 2026-11-04 | Apache-2.0;MIT; |
| [JoseExposito/touchegg](https://github.com/JoseExposito/touchegg) | 2025-06-14 | GPL;v3; |
| [KDE/kdeconnect-android](https://github.com/KDE/kdeconnect-android) | 2026-11-06 | LGPL;GPL;v2; |
| [KDE/kdeconnect-kde](https://github.com/KDE/kdeconnect-kde) | 2026-11-07 | CC; |
| [KDE/kdenlive](https://github.com/KDE/kdenlive) | 2026-11-07 | GPL;v3;CC; |
| [KDE/krfb](https://github.com/KDE/krfb) | 2026-11-07 | LGPL;GPL;v2;NONCOMMERCIAL?; |
| [KDE/krita](https://github.com/KDE/krita) | 2026-11-07 | GPL;v3;BSD;CC; |
| [KenneyNL/Starter-Kit-3D-Platformer](https://github.com/KenneyNL/Starter-Kit-3D-Platformer) | 2026-03-12 | MIT; |
| [KhronosGroup/glTF-Blender-IO](https://github.com/KhronosGroup/glTF-Blender-IO) | 2026-11-05 | Apache-2.0; |
| [Kilbex/Vigla](https://github.com/Kilbex/Vigla) | 2026-07-27 | Apache-2.0; |
| [Kilo-Org/kilocode](https://github.com/Kilo-Org/kilocode) | 2026-11-07 | MIT; |
| [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | 2026-10-30 | MIT; |
| [KoljaB/RealtimeTTS](https://github.com/KoljaB/RealtimeTTS) | 2026-11-06 | MIT; |
| [Kozea/Radicale](https://github.com/Kozea/Radicale) | 2026-11-07 | GPL;v3; |
| [Kunzisoft/KeePassDX](https://github.com/Kunzisoft/KeePassDX) | 2026-10-31 | GPL;v3; |
| [LGFae/swww](https://github.com/LGFae/swww) | 2025-10-31 | GPL;v3; |
| [LMMS/lmms](https://github.com/LMMS/lmms) | 2026-11-04 | LGPL;GPL;v2; |
| [LawnchairLauncher/lawnchair](https://github.com/LawnchairLauncher/lawnchair) | 2026-11-08 | Apache-2.0; |
| [LibreSprite/LibreSprite](https://github.com/LibreSprite/LibreSprite) | 2026-10-30 | LGPL;GPL;v2; |
| [LibreTranslate/LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) | 2026-10-25 | AGPL;GPL; |
| [LizardByte/Sunshine](https://github.com/LizardByte/Sunshine) | 2026-11-07 | GPL;v3; |
| [LostRuins/koboldcpp](https://github.com/LostRuins/koboldcpp) | 2026-11-02 | AGPL;GPL; |
| [MM2-0/Kvaesitso](https://github.com/MM2-0/Kvaesitso) | 2026-11-04 | GPL;v3; |
| [MaxVerevkin/wl-gammarelay-rs](https://github.com/MaxVerevkin/wl-gammarelay-rs) | 2025-08-12 | GPL;v3; |
| [Michael-A-Kuykendall/shimmy](https://github.com/Michael-A-Kuykendall/shimmy) | 2026-11-07 | Apache-2.0; |
| [MikeSchulze/gdUnit4](https://github.com/MikeSchulze/gdUnit4) | 2026-11-06 | MIT; |
| [Mininglamp-AI/Mano-P](https://github.com/Mininglamp-AI/Mano-P) | 2026-06-25 | Apache-2.0; |
| [Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm) | 2026-11-05 | MIT; |
| [Mobile-Artificial-Intelligence/maid](https://github.com/Mobile-Artificial-Intelligence/maid) | 2026-10-29 | MIT; |
| [Mozilla-Ocho/llamafile](https://github.com/Mozilla-Ocho/llamafile) | 2026-11-05 | Apache-2.0; |
| [NVIDIA/NeMo](https://github.com/NVIDIA/NeMo) | 2026-11-06 | Apache-2.0; |
| [NVIDIA/NeMo-Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | 2026-11-06 | Apache-2.0;MIT; |
| [NVIDIA/SkillSpector](https://github.com/NVIDIA/SkillSpector) | 2026-11-06 | Apache-2.0; |
| [NVIDIA/garak](https://github.com/NVIDIA/garak) | 2026-11-06 | Apache-2.0; |
| [Neamar/KISS](https://github.com/Neamar/KISS) | 2026-11-07 | GPL;v3; |
| [NexaAI/nexa-sdk](https://github.com/NexaAI/nexa-sdk) | 2026-11-05 | BSD; |
| [OHF-Voice/linux-voice-assistant](https://github.com/OHF-Voice/linux-voice-assistant) | 2026-11-02 | Apache-2.0; |
| [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) | 2026-11-07 | GPL;v3; |
| [OlauncherCF/OlauncherCF](https://github.com/OlauncherCF/OlauncherCF) | 2024-08-23 | GPL;v3; |
| [OpenAdaptAI/OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt) | 2026-11-07 | MIT; |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | 2026-11-07 | MIT; |
| [OpenInterpreter/open-interpreter](https://github.com/OpenInterpreter/open-interpreter) | 2026-11-07 | Apache-2.0; |
| [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) | 2026-11-07 | MIT; |
| [OpenShot/openshot-qt](https://github.com/OpenShot/openshot-qt) | 2026-11-06 | GPL;v3; |
| [Orama-Interactive/Pixelorama](https://github.com/Orama-Interactive/Pixelorama) | 2026-11-07 | MIT; |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 2026-09-16 | Apache-2.0; |
| [QwenAudio/SenseVoice](https://github.com/QwenAudio/SenseVoice) | 2026-09-30 | MIT; |
| [QwenLM/Qwen2.5-VL](https://github.com/QwenLM/Qwen2.5-VL) | 2026-01-30 | Apache-2.0; |
| [QwenLM/Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) | 2026-01-30 | Apache-2.0; |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | 2026-11-04 | Apache-2.0; |
| [ReimuNotMoe/ydotool](https://github.com/ReimuNotMoe/ydotool) | 2025-12-22 | AGPL;GPL; |
| [RikkaApps/Shizuku](https://github.com/RikkaApps/Shizuku) | 2025-06-18 | Apache-2.0; |
| [RodZill4/material-maker](https://github.com/RodZill4/material-maker) | 2026-10-24 | MIT; |
| [RooCodeInc/Roo-Code](https://github.com/RooCodeInc/Roo-Code) | 2026-05-15 | Apache-2.0; |
| [RunanywhereAI/runanywhere-sdks](https://github.com/RunanywhereAI/runanywhere-sdks) | 2026-11-04 | Apache-2.0;NONCOMMERCIAL?; |
| [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | 2026-07-16 | MIT; |
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | 2026-09-03 | MIT; |
| [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS) | 2026-11-03 | MIT; |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 2026-11-06 | MIT; |
| [Sanster/IOPaint](https://github.com/Sanster/IOPaint) | 2025-04-29 | Apache-2.0; |
| [Satty-org/Satty](https://github.com/Satty-org/Satty) | 2026-11-06 | GPL;MPL-2.0; |
| [SeaDve/Kooha](https://github.com/SeaDve/Kooha) | 2026-11-06 | GPL;v3; |
| [SmartToolFactory/SmartToolFactory](https://github.com/SmartToolFactory/SmartToolFactory) | 2023-12-24 | Apache-2.0; |
| [Stability-AI/stable-fast-3d](https://github.com/Stability-AI/stable-fast-3d) | 2025-01-22 | NONCOMMERCIAL?; |
| [Stirling-Tools/Stirling-PDF](https://github.com/Stirling-Tools/Stirling-PDF) | 2026-11-06 | MIT; |
| [Supreeeme/xwayland-satellite](https://github.com/Supreeeme/xwayland-satellite) | 2026-11-05 | GPL;MPL-2.0; |
| [Tencent-Hunyuan/Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2) | 2025-10-28 | UNRECOGNISED |
| [Tencent/AI-Infra-Guard](https://github.com/Tencent/AI-Infra-Guard) | 2026-11-05 | Apache-2.0; |
| [Tencent/Hunyuan3D-2](https://github.com/Tencent/Hunyuan3D-2) | 2025-10-28 | UNRECOGNISED |
| [TheBevyFlock/bevy_cli](https://github.com/TheBevyFlock/bevy_cli) | 2026-11-07 | Apache-2.0;MIT; |
| [TheBevyFlock/bevy_new_2d](https://github.com/TheBevyFlock/bevy_new_2d) | 2026-11-05 | Apache-2.0;MIT;CC; |
| [Tiiny-AI/PowerInfer](https://github.com/Tiiny-AI/PowerInfer) | 2026-05-11 | MIT; |
| [TriliumNext/Notes](https://github.com/TriliumNext/Notes) | 2025-06-24 | AGPL;GPL; |
| [TriliumNext/Trilium](https://github.com/TriliumNext/Trilium) | 2026-11-08 | AGPL;GPL; |
| [Ttanasart-pt/Pixel-Composer](https://github.com/Ttanasart-pt/Pixel-Composer) | 2026-11-07 | MIT; |
| [TurixAI/TuriX-CUA](https://github.com/TurixAI/TuriX-CUA) | 2026-09-14 | MIT; |
| [USBGuard/usbguard](https://github.com/USBGuard/usbguard) | 2026-09-07 | LGPL;GPL;v2; |
| [UnifiedPush/android-connector](https://github.com/UnifiedPush/android-connector) | 2025-07-01 | Apache-2.0; |
| [UnifiedPush/common-proxies](https://github.com/UnifiedPush/common-proxies) | 2025-01-29 | MIT; |
| [Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured) | 2026-11-06 | Apache-2.0; |
| [Untrivial-ai/agent-orchestrator](https://github.com/Untrivial-ai/agent-orchestrator) | 2026-11-08 | Apache-2.0; |
| [VAST-AI-Research/TripoSR](https://github.com/VAST-AI-Research/TripoSR) | 2026-06-04 | MIT; |
| [Vali-98/ChatterUI](https://github.com/Vali-98/ChatterUI) | 2026-10-01 | AGPL;GPL; |
| [Vencord/Vesktop](https://github.com/Vencord/Vesktop) | 2026-11-05 | GPL;v3; |
| [VikParuchuri/marker](https://github.com/VikParuchuri/marker) | 2026-10-29 | Apache-2.0; |
| [Vladimir-csp/uwsm](https://github.com/Vladimir-csp/uwsm) | 2026-11-07 | MIT; |
| [WillPower3309/swayfx](https://github.com/WillPower3309/swayfx) | 2026-10-31 | MIT; |
| [WireGuard/wireguard-android](https://github.com/WireGuard/wireguard-android) | 2026-03-17 | Apache-2.0; |
| [WireGuard/wireguard-go](https://github.com/WireGuard/wireguard-go) | 2026-10-25 | MIT; |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | 2026-11-07 | Unlicense;NONCOMMERCIAL?; |
| [YaLTeR/niri](https://github.com/YaLTeR/niri) | 2026-11-07 | GPL;v3; |
| [Yelp/detect-secrets](https://github.com/Yelp/detect-secrets) | 2026-04-02 | Apache-2.0; |
| [Zyphra/Zonos](https://github.com/Zyphra/Zonos) | 2025-03-05 | Apache-2.0; |
| [a-ghorbani/pocketpal-ai](https://github.com/a-ghorbani/pocketpal-ai) | 2026-11-07 | MIT; |
| [a13xp0p0v/kernel-hardening-checker](https://github.com/a13xp0p0v/kernel-hardening-checker) | 2026-09-19 | GPL;v3; |
| [abenz1267/walker](https://github.com/abenz1267/walker) | 2026-10-08 | GPL;v3; |
| [abrt/abrt](https://github.com/abrt/abrt) | 2026-10-23 | LGPL;GPL;v2; |
| [accrescent/accrescent](https://github.com/accrescent/accrescent) | 2026-11-06 | Apache-2.0;ISC; |
| [adefossez/demucs](https://github.com/adefossez/demucs) | 2026-10-26 | MIT; |
| [aevyrie/bevy_mod_picking](https://github.com/aevyrie/bevy_mod_picking) | 2025-03-04 | Apache-2.0;MIT; |
| [agno-agi/agno](https://github.com/agno-agi/agno) | 2026-11-06 | Apache-2.0; |
| [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) | 2026-11-04 | MIT; |
| [aigengame/godot-agent](https://github.com/aigengame/godot-agent) | 2026-11-02 | MIT; |
| [aiortc/aiortc](https://github.com/aiortc/aiortc) | 2026-11-07 | BSD; |
| [aistra0528/Hail](https://github.com/aistra0528/Hail) | 2026-10-28 | GPL;v3; |
| [alex-courtis/way-displays](https://github.com/alex-courtis/way-displays) | 2026-11-05 | MIT; |
| [alibaba/MNN](https://github.com/alibaba/MNN) | 2026-11-07 | Apache-2.0; |
| [alphacep/vosk-api](https://github.com/alphacep/vosk-api) | 2026-10-15 | Apache-2.0; |
| [ammen99/wf-recorder](https://github.com/ammen99/wf-recorder) | 2026-04-12 | MIT; |
| [anchore/grype](https://github.com/anchore/grype) | 2026-11-06 | Apache-2.0; |
| [anchore/syft](https://github.com/anchore/syft) | 2026-11-06 | Apache-2.0; |
| [andyholmes/valent](https://github.com/andyholmes/valent) | 2026-03-14 | GPL;CC; |
| [anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime) | 2026-11-06 | Apache-2.0; |
| [anthropics/claude-code](https://github.com/anthropics/claude-code) | 2026-11-06 | UNRECOGNISED |
| [anthropics/claude-code-action](https://github.com/anthropics/claude-code-action) | 2026-11-06 | MIT; |
| [anthropics/claude-code-security-review](https://github.com/anthropics/claude-code-security-review) | 2026-02-11 | MIT; |
| [any1/wayvnc](https://github.com/any1/wayvnc) | 2026-10-04 | ISC; |
| [anyproto/anytype-ts](https://github.com/anyproto/anytype-ts) | 2026-11-03 | NONCOMMERCIAL?; |
| [aquasecurity/tracee](https://github.com/aquasecurity/tracee) | 2026-09-29 | Apache-2.0; |
| [aquasecurity/trivy](https://github.com/aquasecurity/trivy) | 2026-11-06 | Apache-2.0; |
| [archlinux/archinstall](https://github.com/archlinux/archinstall) | 2026-10-03 | GPL;v3; |
| [ardour/ardour](https://github.com/ardour/ardour) | 2026-11-07 | LGPL;GPL;v2; |
| [argosopentech/argos-translate](https://github.com/argosopentech/argos-translate) | 2026-08-08 | MIT; |
| [armory3d/armorpaint](https://github.com/armory3d/armorpaint) | 2026-11-07 | Zlib; |
| [artizirk/wdisplays](https://github.com/artizirk/wdisplays) | 2026-09-28 | CC; |
| [aseprite/aseprite](https://github.com/aseprite/aseprite) | 2026-10-29 | NO-LICENSE-FILE-AT-ROOT |
| [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) | 2026-05-17 | Apache-2.0;MIT; |
| [astral-sh/ruff](https://github.com/astral-sh/ruff) | 2026-11-07 | MIT; |
| [astral-sh/uv](https://github.com/astral-sh/uv) | 2026-11-06 | Apache-2.0;MIT; |
| [atx/wtype](https://github.com/atx/wtype) | 2026-10-20 | MIT; |
| [audacity/audacity](https://github.com/audacity/audacity) | 2026-11-06 | LGPL;GPL;v2;CC; |
| [b4rtaz/distributed-llama](https://github.com/b4rtaz/distributed-llama) | 2026-11-07 | MIT; |
| [beckettlab/beckett-godot-mcp](https://github.com/beckettlab/beckett-godot-mcp) | 2026-11-01 | MIT; |
| [beeper/bridge-manager](https://github.com/beeper/bridge-manager) | 2026-11-02 | Apache-2.0; |
| [bevyengine/bevy](https://github.com/bevyengine/bevy) | 2026-11-07 | Apache-2.0;MIT; |
| [bevyengine/bevy_editor_prototypes](https://github.com/bevyengine/bevy_editor_prototypes) | 2026-04-14 | Apache-2.0;MIT; |
| [bevyengine/bevy_github_ci_template](https://github.com/bevyengine/bevy_github_ci_template) | 2026-10-19 | Apache-2.0;MIT;CC; |
| [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy) | 2026-11-03 | LGPL;GPL;v2;Apache-2.0; |
| [binwiederhier/ntfy-android](https://github.com/binwiederhier/ntfy-android) | 2026-10-19 | Apache-2.0; |
| [bitbrain/beehave](https://github.com/bitbrain/beehave) | 2026-09-20 | MIT; |
| [bitwarden/android](https://github.com/bitwarden/android) | 2026-11-06 | GPL;v3; |
| [bitwarden/clients](https://github.com/bitwarden/clients) | 2026-11-06 | GPL;v3; |
| [blender/blender](https://github.com/blender/blender) | 2026-11-07 | GPL; |
| [block/goose](https://github.com/block/goose) | 2026-11-06 | Apache-2.0; |
| [borgbackup/borg](https://github.com/borgbackup/borg) | 2026-11-06 | BSD; |
| [borgbase/vorta](https://github.com/borgbase/vorta) | 2026-11-03 | GPL;v3; |
| [borgmatic-collective/borgmatic](https://github.com/borgmatic-collective/borgmatic) | 2026-11-06 | GPL;v3; |
| [boxed/mutmut](https://github.com/boxed/mutmut) | 2026-11-07 | BSD; |
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | 2026-10-29 | MIT; |
| [bugaevc/wl-clipboard](https://github.com/bugaevc/wl-clipboard) | 2026-09-15 | GPL;v3; |
| [bugsink/bugsink](https://github.com/bugsink/bugsink) | 2026-11-06 | UNRECOGNISED |
| [bulletmark/libinput-gestures](https://github.com/bulletmark/libinput-gestures) | 2026-08-17 | NO-LICENSE-FILE-AT-ROOT |
| [bytebot-ai/bytebot](https://github.com/bytebot-ai/bytebot) | 2025-09-11 | Apache-2.0; |
| [bytedance/UI-TARS](https://github.com/bytedance/UI-TARS) | 2025-09-05 | Apache-2.0; |
| [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | 2026-10-09 | Apache-2.0; |
| [cactus-compute/cactus](https://github.com/cactus-compute/cactus) | 2026-11-07 | Apache-2.0; |
| [caelestia-dots/shell](https://github.com/caelestia-dots/shell) | 2026-11-07 | GPL;v3; |
| [calamares/calamares](https://github.com/calamares/calamares) | 2025-05-06 | CC; |
| [calcom/cal.com](https://github.com/calcom/cal.com) | 2026-11-04 | MIT; |
| [canonical/snapd](https://github.com/canonical/snapd) | 2026-11-06 | GPL;v3; |
| [canonical/subiquity](https://github.com/canonical/subiquity) | 2026-11-05 | AGPL;GPL;v3; |
| [canonical/ubuntu-desktop-provision](https://github.com/canonical/ubuntu-desktop-provision) | 2026-11-06 | GPL;v3; |
| [canonical/ubuntu-image](https://github.com/canonical/ubuntu-image) | 2026-11-04 | GPL;v3; |
| [canopyai/Orpheus-TTS](https://github.com/canopyai/Orpheus-TTS) | 2025-12-05 | Apache-2.0; |
| [casey/just](https://github.com/casey/just) | 2026-11-01 | CC; |
| [catppuccin/catppuccin](https://github.com/catppuccin/catppuccin) | 2026-07-25 | MIT; |
| [chaiNNer-org/chaiNNer](https://github.com/chaiNNer-org/chaiNNer) | 2026-11-02 | GPL;v3; |
| [chidiwilliams/buzz](https://github.com/chidiwilliams/buzz) | 2026-11-07 | MIT; |
| [chr15m/jsfxr](https://github.com/chr15m/jsfxr) | 2026-05-05 | Unlicense;NONCOMMERCIAL?; |
| [chroma-core/chroma](https://github.com/chroma-core/chroma) | 2026-11-06 | Apache-2.0; |
| [cjpais/Handy](https://github.com/cjpais/Handy) | 2026-11-06 | MIT; |
| [cline/cline](https://github.com/cline/cline) | 2026-11-06 | Apache-2.0; |
| [coder/agentapi](https://github.com/coder/agentapi) | 2026-09-13 | MIT; |
| [coleam00/Archon](https://github.com/coleam00/Archon) | 2026-11-07 | MIT; |
| [collabora/WhisperLive](https://github.com/collabora/WhisperLive) | 2026-11-02 | MIT; |
| [comet-ml/opik](https://github.com/comet-ml/opik) | 2026-11-06 | Apache-2.0; |
| [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) | 2026-11-06 | GPL;v3; |
| [containers/bootc](https://github.com/containers/bootc) | 2026-11-06 | Apache-2.0;MIT; |
| [containers/bubblewrap](https://github.com/containers/bubblewrap) | 2026-10-14 | LGPL;GPL; |
| [containers/podman](https://github.com/containers/podman) | 2026-11-06 | Apache-2.0; |
| [containers/ramalama](https://github.com/containers/ramalama) | 2026-11-06 | MIT; |
| [continuedev/continue](https://github.com/continuedev/continue) | 2026-07-20 | Apache-2.0; |
| [continuwuity/continuwuity](https://github.com/continuwuity/continuwuity) | 2026-11-05 | Apache-2.0; |
| [coqui-ai/TTS](https://github.com/coqui-ai/TTS) | 2024-02-10 | GPL;MPL-2.0; |
| [coreos/rpm-ostree](https://github.com/coreos/rpm-ostree) | 2026-11-07 | LGPL;GPL;v2; |
| [coturn/coturn](https://github.com/coturn/coturn) | 2026-11-06 | BSD; |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | 2026-11-07 | MIT; |
| [crowdsecurity/crowdsec](https://github.com/crowdsecurity/crowdsec) | 2026-10-30 | MIT; |
| [cryptomator/android](https://github.com/cryptomator/android) | 2026-11-05 | GPL;v3; |
| [dagger/container-use](https://github.com/dagger/container-use) | 2026-08-12 | Apache-2.0; |
| [dandavison/delta](https://github.com/dandavison/delta) | 2026-10-04 | MIT; |
| [dani-garcia/vaultwarden](https://github.com/dani-garcia/vaultwarden) | 2026-11-04 | AGPL;GPL; |
| [danielgatis/rembg](https://github.com/danielgatis/rembg) | 2026-11-05 | MIT; |
| [darktable-org/darktable](https://github.com/darktable-org/darktable) | 2026-11-07 | GPL;v3; |
| [datalab-to/chandra](https://github.com/datalab-to/chandra) | 2026-10-22 | Apache-2.0; |
| [datalab-to/marker](https://github.com/datalab-to/marker) | 2026-10-29 | Apache-2.0; |
| [davatorium/rofi](https://github.com/davatorium/rofi) | 2026-11-07 | MIT; |
| [daytonaio/daytona](https://github.com/daytonaio/daytona) | 2026-06-25 | NO-LICENSE-FILE-AT-ROOT |
| [deepnight/ldtk](https://github.com/deepnight/ldtk) | 2026-10-06 | MIT; |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | 2026-11-06 | Apache-2.0; |
| [defold/defold](https://github.com/defold/defold) | 2026-11-07 | UNRECOGNISED |
| [deskflow/deskflow](https://github.com/deskflow/deskflow) | 2026-11-06 | LGPL;GPL;v2; |
| [devcontainers/cli](https://github.com/devcontainers/cli) | 2026-10-30 | MIT; |
| [dialogic-godot/dialogic](https://github.com/dialogic-godot/dialogic) | 2026-11-07 | MIT; |
| [digint/btrbk](https://github.com/digint/btrbk) | 2026-07-19 | GPL;v3; |
| [dimforge/rapier](https://github.com/dimforge/rapier) | 2026-10-10 | Apache-2.0; |
| [docker/mcp-gateway](https://github.com/docker/mcp-gateway) | 2026-10-26 | MIT; |
| [docling-project/docling](https://github.com/docling-project/docling) | 2026-11-07 | MIT; |
| [droidrun/droidrun](https://github.com/droidrun/droidrun) | 2026-11-04 | MIT; |
| [dscripka/openWakeWord](https://github.com/dscripka/openWakeWord) | 2025-12-30 | Apache-2.0; |
| [dunst-project/dunst](https://github.com/dunst-project/dunst) | 2026-10-25 | BSD; |
| [dylanaraps/pywal](https://github.com/dylanaraps/pywal) | 2021-09-09 | MIT; |
| [e2b-dev/E2B](https://github.com/e2b-dev/E2B) | 2026-11-06 | Apache-2.0; |
| [e2b-dev/open-computer-use](https://github.com/e2b-dev/open-computer-use) | 2026-10-23 | Apache-2.0; |
| [eclipse-mosquitto/mosquitto](https://github.com/eclipse-mosquitto/mosquitto) | 2026-11-05 | UNRECOGNISED |
| [ee0pdt/Godot-MCP](https://github.com/ee0pdt/Godot-MCP) | 2025-03-19 | MIT; |
| [element-hq/dendrite](https://github.com/element-hq/dendrite) | 2026-05-20 | AGPL;GPL; |
| [element-hq/element-web](https://github.com/element-hq/element-web) | 2026-11-06 | AGPL;GPL;v3; |
| [element-hq/element-x-android](https://github.com/element-hq/element-x-android) | 2026-11-06 | AGPL;GPL; |
| [element-hq/synapse](https://github.com/element-hq/synapse) | 2026-11-06 | AGPL;GPL; |
| [elkowar/eww](https://github.com/elkowar/eww) | 2026-07-17 | MIT; |
| [emersion/grim](https://github.com/emersion/grim) | 2022-03-30 | MIT; |
| [emersion/kanshi](https://github.com/emersion/kanshi) | 2021-09-15 | MIT; |
| [emersion/mako](https://github.com/emersion/mako) | 2026-10-25 | MIT; |
| [emersion/slurp](https://github.com/emersion/slurp) | 2026-05-10 | MIT; |
| [emersion/xdg-desktop-portal-wlr](https://github.com/emersion/xdg-desktop-portal-wlr) | 2026-10-29 | MIT; |
| [emqx/nanomq](https://github.com/emqx/nanomq) | 2026-11-06 | MIT; |
| [espanso/espanso](https://github.com/espanso/espanso) | 2026-10-24 | GPL;v3; |
| [ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo) | 2026-06-02 | MIT; |
| [eugene1g/agent-safehouse](https://github.com/eugene1g/agent-safehouse) | 2026-11-02 | Apache-2.0; |
| [evilsocket/opensnitch](https://github.com/evilsocket/opensnitch) | 2026-07-27 | GPL;v3; |
| [excalidraw/excalidraw](https://github.com/excalidraw/excalidraw) | 2026-11-05 | MIT; |
| [exo-explore/exo](https://github.com/exo-explore/exo) | 2026-11-06 | Apache-2.0; |
| [eylles/pywal16](https://github.com/eylles/pywal16) | 2026-11-06 | MIT; |
| [facebookincubator/oomd](https://github.com/facebookincubator/oomd) | 2026-11-04 | LGPL;GPL;v2; |
| [facebookresearch/demucs](https://github.com/facebookresearch/demucs) | 2023-11-16 | MIT; |
| [facebookresearch/sam2](https://github.com/facebookresearch/sam2) | 2024-12-15 | Apache-2.0;BSD; |
| [fail2ban/fail2ban](https://github.com/fail2ban/fail2ban) | 2026-11-07 | LGPL;GPL;v2; |
| [falcosecurity/falco](https://github.com/falcosecurity/falco) | 2026-11-06 | Apache-2.0; |
| [fcitx/fcitx5](https://github.com/fcitx/fcitx5) | 2026-11-04 | UNRECOGNISED |
| [feschber/lan-mouse](https://github.com/feschber/lan-mouse) | 2026-10-25 | GPL;v3; |
| [fikrikarim/parlor](https://github.com/fikrikarim/parlor) | 2026-08-04 | Apache-2.0; |
| [firecracker-microvm/firecracker](https://github.com/firecracker-microvm/firecracker) | 2026-11-06 | Apache-2.0; |
| [firewalld/firewalld](https://github.com/firewalld/firewalld) | 2026-10-31 | LGPL;GPL;v2; |
| [fishaudio/fish-speech](https://github.com/fishaudio/fish-speech) | 2026-10-30 | NONCOMMERCIAL?; |
| [flagdizero/jenny-android-ai-agent](https://github.com/flagdizero/jenny-android-ai-agent) | 2026-11-04 | AGPL;GPL; |
| [flameshot-org/flameshot](https://github.com/flameshot-org/flameshot) | 2026-11-07 | GPL;v3; |
| [flatpak/xdg-desktop-portal](https://github.com/flatpak/xdg-desktop-portal) | 2026-11-06 | CC; |
| [flynn/noise](https://github.com/flynn/noise) | 2024-02-01 | BSD; |
| [fosrl/pangolin](https://github.com/fosrl/pangolin) | 2026-11-06 | AGPL;GPL; |
| [fwupd/fwupd](https://github.com/fwupd/fwupd) | 2026-11-06 | LGPL;GPL; |
| [gabm/Satty](https://github.com/gabm/Satty) | 2026-11-06 | GPL;MPL-2.0; |
| [garethgeorge/backrest](https://github.com/garethgeorge/backrest) | 2026-07-27 | GPL;v3; |
| [getsentry/sentry](https://github.com/getsentry/sentry) | 2026-11-07 | Apache-2.0;NONCOMMERCIAL?; |
| [getsops/sops](https://github.com/getsops/sops) | 2026-11-03 | GPL;MPL-2.0; |
| [getzep/graphiti](https://github.com/getzep/graphiti) | 2026-11-06 | Apache-2.0; |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 2026-11-07 | MIT; |
| [ggml-org/llama.vim](https://github.com/ggml-org/llama.vim) | 2026-09-29 | MIT; |
| [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 2026-11-07 | MIT; |
| [girlbossceo/conduwuit](https://github.com/girlbossceo/conduwuit) | 2026-05-29 | Apache-2.0; |
| [gitbutlerapp/gitbutler](https://github.com/gitbutlerapp/gitbutler) | 2026-11-07 | MIT;NONCOMMERCIAL?; |
| [github/github-mcp-server](https://github.com/github/github-mcp-server) | 2026-11-04 | MIT; |
| [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) | 2026-10-23 | MIT; |
| [go-vikunja/vikunja](https://github.com/go-vikunja/vikunja) | 2026-11-07 | AGPL;GPL; |
| [godotengine/godot](https://github.com/godotengine/godot) | 2026-11-07 | MIT; |
| [godotengine/godot-demo-projects](https://github.com/godotengine/godot-demo-projects) | 2026-10-08 | MIT; |
| [godotengine/godot-git-plugin](https://github.com/godotengine/godot-git-plugin) | 2026-10-21 | MIT; |
| [gomuks/gomuks](https://github.com/gomuks/gomuks) | 2026-11-06 | AGPL;GPL;MPL-2.0; |
| [google-ai-edge/LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) | 2026-11-07 | Apache-2.0; |
| [google-ai-edge/gallery](https://github.com/google-ai-edge/gallery) | 2026-11-06 | Apache-2.0; |
| [google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) | 2026-11-06 | Apache-2.0; |
| [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | 2026-11-06 | Apache-2.0; |
| [google-research/camel-prompt-injection](https://github.com/google-research/camel-prompt-injection) | 2025-06-20 | Apache-2.0; |
| [google/fscrypt](https://github.com/google/fscrypt) | 2026-09-09 | Apache-2.0; |
| [google/gvisor](https://github.com/google/gvisor) | 2026-11-07 | Apache-2.0; |
| [google/nsjail](https://github.com/google/nsjail) | 2026-10-28 | Apache-2.0; |
| [google/osv-scanner](https://github.com/google/osv-scanner) | 2026-11-05 | Apache-2.0; |
| [gopasspw/gopass](https://github.com/gopasspw/gopass) | 2026-11-05 | MIT; |
| [gotify/server](https://github.com/gotify/server) | 2026-10-25 | MIT;CC; |
| [grumdrig/jsfxr](https://github.com/grumdrig/jsfxr) | 2021-11-13 | Unlicense;NONCOMMERCIAL?; |
| [guillaumekln/faster-whisper](https://github.com/guillaumekln/faster-whisper) | 2026-11-06 | MIT; |
| [haiwen/seafile](https://github.com/haiwen/seafile) | 2026-10-16 | LGPL;GPL;v2; |
| [hbldh/bleak](https://github.com/hbldh/bleak) | 2026-11-07 | MIT; |
| [healthchecks/healthchecks](https://github.com/healthchecks/healthchecks) | 2026-11-05 | BSD; |
| [hexgrad/kokoro](https://github.com/hexgrad/kokoro) | 2025-08-06 | Apache-2.0; |
| [hiroi-sora/Umi-OCR](https://github.com/hiroi-sora/Umi-OCR) | 2025-11-20 | MIT; |
| [hoarder-app/hoarder](https://github.com/hoarder-app/hoarder) | 2026-11-07 | AGPL;GPL; |
| [home-assistant/core](https://github.com/home-assistant/core) | 2026-11-08 | Apache-2.0; |
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | 2026-10-06 | Apache-2.0; |
| [huggingface/speech-to-speech](https://github.com/huggingface/speech-to-speech) | 2026-11-06 | Apache-2.0; |
| [huggingface/text-embeddings-inference](https://github.com/huggingface/text-embeddings-inference) | 2026-11-06 | Apache-2.0; |
| [huggingface/text-generation-inference](https://github.com/huggingface/text-generation-inference) | 2026-03-21 | Apache-2.0; |
| [huggingface/transformers.js](https://github.com/huggingface/transformers.js) | 2026-10-28 | Apache-2.0; |
| [humanlayer/humanlayer](https://github.com/humanlayer/humanlayer) | 2026-11-02 | Apache-2.0; |
| [hyprwm/Hyprland](https://github.com/hyprwm/Hyprland) | 2026-11-06 | BSD; |
| [hyprwm/hypridle](https://github.com/hyprwm/hypridle) | 2026-11-03 | BSD; |
| [hyprwm/hyprlock](https://github.com/hyprwm/hyprlock) | 2026-11-06 | BSD; |
| [hyprwm/hyprpicker](https://github.com/hyprwm/hyprpicker) | 2026-11-01 | BSD; |
| [hyprwm/hyprsunset](https://github.com/hyprwm/hyprsunset) | 2026-10-27 | BSD; |
| [hyprwm/xdg-desktop-portal-hyprland](https://github.com/hyprwm/xdg-desktop-portal-hyprland) | 2026-11-01 | BSD; |
| [iberianpig/fusuma](https://github.com/iberianpig/fusuma) | 2026-04-29 | MIT; |
| [ictnlp/LLaMA-Omni](https://github.com/ictnlp/LLaMA-Omni) | 2025-05-19 | Apache-2.0; |
| [ideasman42/nerd-dictation](https://github.com/ideasman42/nerd-dictation) | 2025-10-10 | GPL;v3; |
| [idiap/coqui-ai-TTS](https://github.com/idiap/coqui-ai-TTS) | 2026-10-24 | GPL;MPL-2.0; |
| [ikawrakow/ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) | 2026-11-07 | MIT; |
| [immich-app/immich](https://github.com/immich-app/immich) | 2026-11-07 | AGPL;GPL; |
| [in-toto/in-toto](https://github.com/in-toto/in-toto) | 2026-08-27 | Apache-2.0; |
| [index-tts/index-tts](https://github.com/index-tts/index-tts) | 2026-09-30 | NONCOMMERCIAL?; |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 2026-11-06 | Apache-2.0; |
| [inkscape/inkscape](https://github.com/inkscape/inkscape) | 2022-03-03 | NO-LICENSE-FILE-AT-ROOT |
| [input-leap/input-leap](https://github.com/input-leap/input-leap) | 2025-11-28 | LGPL;GPL;v2; |
| [invariantlabs-ai/invariant](https://github.com/invariantlabs-ai/invariant) | 2026-01-12 | Apache-2.0; |
| [invariantlabs-ai/mcp-scan](https://github.com/invariantlabs-ai/mcp-scan) | 2026-11-06 | Apache-2.0; |
| [invoke-ai/InvokeAI](https://github.com/invoke-ai/InvokeAI) | 2026-11-07 | Apache-2.0;BSD;NONCOMMERCIAL?; |
| [jakobhellermann/bevy-inspector-egui](https://github.com/jakobhellermann/bevy-inspector-egui) | 2026-10-17 | Apache-2.0;MIT; |
| [janhq/jan](https://github.com/janhq/jan) | 2026-11-05 | Apache-2.0; |
| [jasonjmcghee/rem](https://github.com/jasonjmcghee/rem) | 2024-05-16 | MIT; |
| [jdx/mise](https://github.com/jdx/mise) | 2026-11-07 | MIT; |
| [jedisct1/libsodium](https://github.com/jedisct1/libsodium) | 2026-11-07 | ISC; |
| [jellyfin/jellyfin](https://github.com/jellyfin/jellyfin) | 2026-11-06 | LGPL;GPL;v2; |
| [jesseduffield/lazygit](https://github.com/jesseduffield/lazygit) | 2026-11-07 | MIT; |
| [jianchang512/pyvideotrans](https://github.com/jianchang512/pyvideotrans) | 2026-11-06 | GPL;v3; |
| [jirutka/swaylock-effects](https://github.com/jirutka/swaylock-effects) | 2023-12-30 | MIT; |
| [jj-vcs/jj](https://github.com/jj-vcs/jj) | 2026-11-07 | Apache-2.0; |
| [jjsullivan5196/wvkbd](https://github.com/jjsullivan5196/wvkbd) | 2026-10-27 | GPL;v3;MIT; |
| [jlowin/fastmcp](https://github.com/jlowin/fastmcp) | 2026-11-06 | Apache-2.0; |
| [jmdns/jmdns](https://github.com/jmdns/jmdns) | 2026-11-03 | Apache-2.0; |
| [jordansissel/xdotool](https://github.com/jordansissel/xdotool) | 2026-09-20 | NO-LICENSE-FILE-AT-ROOT |
| [jtheoof/swappy](https://github.com/jtheoof/swappy) | 2026-11-07 | MIT; |
| [jtroo/kanata](https://github.com/jtroo/kanata) | 2026-11-02 | LGPL;v3; |
| [juanfont/headscale](https://github.com/juanfont/headscale) | 2026-11-06 | BSD; |
| [juanmc2005/diart](https://github.com/juanmc2005/diart) | 2025-02-12 | MIT; |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 2026-11-07 | Apache-2.0; |
| [karakeep-app/karakeep](https://github.com/karakeep-app/karakeep) | 2026-11-07 | AGPL;GPL; |
| [kata-containers/kata-containers](https://github.com/kata-containers/kata-containers) | 2026-11-07 | Apache-2.0; |
| [katanemo/plano](https://github.com/katanemo/plano) | 2026-10-24 | Apache-2.0; |
| [keepassxreboot/keepassxc](https://github.com/keepassxreboot/keepassxc) | 2026-11-05 | GPL;BSD;CC; |
| [khoj-ai/khoj](https://github.com/khoj-ai/khoj) | 2026-08-01 | AGPL;GPL; |
| [kinvolk/flatcar](https://github.com/kinvolk/flatcar) | 2026-11-06 | Apache-2.0; |
| [kmonad/kmonad](https://github.com/kmonad/kmonad) | 2026-06-15 | MIT; |
| [kopia/kopia](https://github.com/kopia/kopia) | 2026-11-02 | Apache-2.0; |
| [kyutai-labs/delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling) | 2026-01-26 | Apache-2.0;MIT; |
| [kyutai-labs/moshi](https://github.com/kyutai-labs/moshi) | 2026-05-16 | Apache-2.0;MIT; |
| [kyutai-labs/unmute](https://github.com/kyutai-labs/unmute) | 2026-09-09 | MIT; |
| [labwc/labwc](https://github.com/labwc/labwc) | 2026-11-05 | LGPL;GPL;v2; |
| [lancedb/lancedb](https://github.com/lancedb/lancedb) | 2026-11-07 | Apache-2.0; |
| [landlock-lsm/rust-landlock](https://github.com/landlock-lsm/rust-landlock) | 2026-10-21 | Apache-2.0;MIT; |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 2026-11-06 | MIT; |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | 2026-11-07 | MIT; |
| [latchset/clevis](https://github.com/latchset/clevis) | 2026-10-29 | GPL;v3; |
| [lathiat/avahi](https://github.com/lathiat/avahi) | 2026-11-01 | LGPL;GPL; |
| [laurent22/joplin](https://github.com/laurent22/joplin) | 2026-11-06 | UNRECOGNISED |
| [lbonn/rofi](https://github.com/lbonn/rofi) | 2025-09-12 | MIT; |
| [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) | 2026-11-08 | MIT; |
| [lemonade-sdk/lemonade](https://github.com/lemonade-sdk/lemonade) | 2026-11-07 | Apache-2.0; |
| [leon-ai/leon](https://github.com/leon-ai/leon) | 2026-11-07 | MIT; |
| [letta-ai/letta](https://github.com/letta-ai/letta) | 2026-10-18 | Apache-2.0; |
| [libgdx/libgdx](https://github.com/libgdx/libgdx) | 2026-11-03 | Apache-2.0; |
| [libp2p/go-libp2p](https://github.com/libp2p/go-libp2p) | 2026-10-23 | MIT; |
| [libp2p/rust-libp2p](https://github.com/libp2p/rust-libp2p) | 2026-11-05 | MIT; |
| [lima-vm/lima](https://github.com/lima-vm/lima) | 2026-11-08 | Apache-2.0; |
| [limbonaut/limboai](https://github.com/limbonaut/limboai) | 2026-10-30 | MIT; |
| [linuxkit/linuxkit](https://github.com/linuxkit/linuxkit) | 2026-11-05 | Apache-2.0; |
| [linuxmint/live-installer](https://github.com/linuxmint/live-installer) | 2026-10-27 | NO-LICENSE-FILE-AT-ROOT |
| [linuxmint/timeshift](https://github.com/linuxmint/timeshift) | 2026-10-27 | UNRECOGNISED |
| [livekit/agents](https://github.com/livekit/agents) | 2026-11-06 | Apache-2.0; |
| [lllyasviel/Fooocus](https://github.com/lllyasviel/Fooocus) | 2025-09-02 | GPL;v3; |
| [lmms/lmms](https://github.com/lmms/lmms) | 2026-11-04 | LGPL;GPL;v2; |
| [lmstudio-ai/lms](https://github.com/lmstudio-ai/lms) | 2026-10-14 | MIT; |
| [localsend/localsend](https://github.com/localsend/localsend) | 2026-11-07 | Apache-2.0; |
| [logseq/logseq](https://github.com/logseq/logseq) | 2026-11-06 | AGPL;GPL; |
| [louislam/uptime-kuma](https://github.com/louislam/uptime-kuma) | 2026-11-08 | MIT; |
| [love2d/love](https://github.com/love2d/love) | 2026-11-04 | Zlib; |
| [luckyPipewrench/pipelock](https://github.com/luckyPipewrench/pipelock) | 2026-11-07 | Apache-2.0; |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 2026-10-22 | BSD; |
| [magic-wormhole/magic-wormhole](https://github.com/magic-wormhole/magic-wormhole) | 2026-11-06 | MIT; |
| [maliit/keyboard](https://github.com/maliit/keyboard) | 2024-09-05 | LGPL;GPL;v3;BSD; |
| [mapeditor/tiled](https://github.com/mapeditor/tiled) | 2026-10-24 | Apache-2.0;BSD; |
| [markusfisch/PieLauncher](https://github.com/markusfisch/PieLauncher) | 2026-11-02 | MIT; |
| [martinvonz/jj](https://github.com/martinvonz/jj) | 2026-11-07 | Apache-2.0; |
| [matrix-construct/tuwunel](https://github.com/matrix-construct/tuwunel) | 2026-11-07 | Apache-2.0; |
| [matrix-org/dendrite](https://github.com/matrix-org/dendrite) | 2024-11-25 | Apache-2.0; |
| [matrix-org/matrix-rust-sdk](https://github.com/matrix-org/matrix-rust-sdk) | 2026-11-06 | Apache-2.0; |
| [mautrix/discord](https://github.com/mautrix/discord) | 2026-11-05 | AGPL;GPL; |
| [mautrix/gmessages](https://github.com/mautrix/gmessages) | 2026-11-07 | AGPL;GPL; |
| [mautrix/meta](https://github.com/mautrix/meta) | 2026-11-07 | AGPL;GPL; |
| [mautrix/signal](https://github.com/mautrix/signal) | 2026-11-05 | AGPL;GPL; |
| [mautrix/slack](https://github.com/mautrix/slack) | 2026-11-07 | AGPL;GPL; |
| [mautrix/telegram](https://github.com/mautrix/telegram) | 2026-11-07 | AGPL;GPL; |
| [mautrix/whatsapp](https://github.com/mautrix/whatsapp) | 2026-11-07 | AGPL;GPL; |
| [maximbaz/wluma](https://github.com/maximbaz/wluma) | 2026-11-05 | ISC; |
| [mcginty/snow](https://github.com/mcginty/snow) | 2026-03-25 | Apache-2.0;MIT; |
| [mcp-use/mcp-use](https://github.com/mcp-use/mcp-use) | 2026-11-05 | MIT; |
| [mediar-ai/screenpipe](https://github.com/mediar-ai/screenpipe) | 2026-11-06 | NONCOMMERCIAL?; |
| [meilisearch/meilisearch](https://github.com/meilisearch/meilisearch) | 2026-11-04 | MIT;BUSL; |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | 2026-11-06 | Apache-2.0; |
| [mendersoftware/mender](https://github.com/mendersoftware/mender) | 2026-10-28 | Apache-2.0; |
| [menloresearch/jan](https://github.com/menloresearch/jan) | 2026-11-05 | Apache-2.0; |
| [meta-llama/PurpleLlama](https://github.com/meta-llama/PurpleLlama) | 2026-10-30 | LlamaLic; |
| [metallkopf/konnect](https://github.com/metallkopf/konnect) | 2025-05-24 | LGPL;GPL;v2; |
| [michaelfeil/infinity](https://github.com/michaelfeil/infinity) | 2026-11-07 | MIT; |
| [microsandbox/microsandbox](https://github.com/microsandbox/microsandbox) | 2026-11-07 | Apache-2.0; |
| [microsoft/BitNet](https://github.com/microsoft/BitNet) | 2026-07-27 | MIT; |
| [microsoft/OmniParser](https://github.com/microsoft/OmniParser) | 2026-07-19 | CC; |
| [microsoft/TRELLIS](https://github.com/microsoft/TRELLIS) | 2026-10-27 | MIT; |
| [microsoft/autogen](https://github.com/microsoft/autogen) | 2026-10-28 | MIT;CC; |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | 2026-11-01 | MIT; |
| [microsoft/magentic-ui](https://github.com/microsoft/magentic-ui) | 2026-07-23 | MIT; |
| [microsoft/markitdown](https://github.com/microsoft/markitdown) | 2026-11-06 | MIT; |
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | 2026-11-06 | Apache-2.0; |
| [microsoft/presidio](https://github.com/microsoft/presidio) | 2026-11-02 | MIT; |
| [mifi/lossless-cut](https://github.com/mifi/lossless-cut) | 2026-11-03 | LGPL;GPL;v2; |
| [mindee/doctr](https://github.com/mindee/doctr) | 2026-11-06 | Apache-2.0; |
| [miniupnp/miniupnp](https://github.com/miniupnp/miniupnp) | 2026-11-04 | BSD; |
| [mkiol/dsnote](https://github.com/mkiol/dsnote) | 2026-11-05 | GPL;MPL-2.0; |
| [mlc-ai/mlc-llm](https://github.com/mlc-ai/mlc-llm) | 2026-10-30 | Apache-2.0; |
| [mlc-ai/web-llm](https://github.com/mlc-ai/web-llm) | 2026-11-03 | Apache-2.0; |
| [mltframework/mlt](https://github.com/mltframework/mlt) | 2026-11-07 | LGPL;GPL; |
| [mltframework/shotcut](https://github.com/mltframework/shotcut) | 2026-11-04 | GPL;v3; |
| [mmulet/term.everything](https://github.com/mmulet/term.everything) | 2026-03-18 | AGPL;GPL; |
| [modelcontextprotocol/inspector](https://github.com/modelcontextprotocol/inspector) | 2026-11-04 | Apache-2.0; |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | 2026-11-05 | MIT; |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 2026-11-04 | Apache-2.0; |
| [modelscope/FunASR](https://github.com/modelscope/FunASR) | 2026-11-08 | MIT; |
| [mollyim/mollyim-android](https://github.com/mollyim/mollyim-android) | 2026-09-08 | AGPL;GPL; |
| [moonlight-stream/moonlight-qt](https://github.com/moonlight-stream/moonlight-qt) | 2026-11-05 | GPL;v3; |
| [moonshine-ai/moonshine](https://github.com/moonshine-ai/moonshine) | 2026-11-05 | MIT;NONCOMMERCIAL?; |
| [mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap) | 2026-11-07 | MIT; |
| [mozilla-ai/llamafile](https://github.com/mozilla-ai/llamafile) | 2026-11-05 | Apache-2.0; |
| [mozilla/translations](https://github.com/mozilla/translations) | 2026-10-29 | GPL;MPL-2.0; |
| [mpv-player/mpv](https://github.com/mpv-player/mpv) | 2026-11-07 | LGPL;GPL;v2; |
| [mudler/LocalAI](https://github.com/mudler/LocalAI) | 2026-11-07 | MIT; |
| [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router) | 2026-10-10 | MIT; |
| [mvo5/unattended-upgrades](https://github.com/mvo5/unattended-upgrades) | 2026-11-02 | LGPL;GPL;v2; |
| [mxgmn/MarkovJunior](https://github.com/mxgmn/MarkovJunior) | 2026-03-19 | MIT; |
| [mxgmn/WaveFunctionCollapse](https://github.com/mxgmn/WaveFunctionCollapse) | 2026-03-21 | MIT; |
| [mybigday/llama.rn](https://github.com/mybigday/llama.rn) | 2026-11-07 | MIT; |
| [myshell-ai/MeloTTS](https://github.com/myshell-ai/MeloTTS) | 2024-12-24 | MIT; |
| [myshell-ai/OpenVoice](https://github.com/myshell-ai/OpenVoice) | 2025-04-19 | MIT; |
| [n0-computer/iroh](https://github.com/n0-computer/iroh) | 2026-11-05 | Apache-2.0;MIT; |
| [n0-computer/sendme](https://github.com/n0-computer/sendme) | 2026-10-29 | Apache-2.0;MIT; |
| [nari-labs/dia](https://github.com/nari-labs/dia) | 2025-11-19 | Apache-2.0; |
| [nathanhoad/godot_dialogue_manager](https://github.com/nathanhoad/godot_dialogue_manager) | 2026-11-06 | MIT; |
| [nektos/act](https://github.com/nektos/act) | 2026-11-04 | MIT; |
| [nesbox/TIC-80](https://github.com/nesbox/TIC-80) | 2026-11-07 | MIT; |
| [netbirdio/netbird](https://github.com/netbirdio/netbird) | 2026-11-07 | AGPL;BSD; |
| [netblue30/firejail](https://github.com/netblue30/firejail) | 2026-11-07 | LGPL;GPL;v2; |
| [netdata/netdata](https://github.com/netdata/netdata) | 2026-11-07 | GPL;v3; |
| [neuml/txtai](https://github.com/neuml/txtai) | 2026-11-06 | Apache-2.0; |
| [nextcloud/server](https://github.com/nextcloud/server) | 2026-11-07 | AGPL;GPL;CC; |
| [niri-wm/niri](https://github.com/niri-wm/niri) | 2026-11-07 | GPL;v3; |
| [nix-community/disko](https://github.com/nix-community/disko) | 2026-10-20 | MIT; |
| [noctalia-dev/noctalia](https://github.com/noctalia-dev/noctalia) | 2026-11-07 | MIT; |
| [noctalia-dev/noctalia-shell](https://github.com/noctalia-dev/noctalia-shell) | 2026-11-07 | MIT; |
| [nomic-ai/gpt4all](https://github.com/nomic-ai/gpt4all) | 2025-05-27 | MIT; |
| [notmuch/notmuch](https://github.com/notmuch/notmuch) | 2026-10-15 | GPL;v3; |
| [nwg-piotr/autotiling](https://github.com/nwg-piotr/autotiling) | 2026-07-13 | GPL;v3; |
| [nwg-piotr/nwg-displays](https://github.com/nwg-piotr/nwg-displays) | 2026-10-20 | MIT; |
| [nwg-piotr/nwg-look](https://github.com/nwg-piotr/nwg-look) | 2026-10-14 | MIT; |
| [nwg-piotr/nwg-shell](https://github.com/nwg-piotr/nwg-shell) | 2026-09-29 | MIT;CC; |
| [o3de/o3de](https://github.com/o3de/o3de) | 2026-11-07 | Apache-2.0;MIT; |
| [obsproject/obs-studio](https://github.com/obsproject/obs-studio) | 2026-11-03 | LGPL;GPL;v2; |
| [ocrmypdf/OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | 2026-11-07 | GPL;MPL-2.0; |
| [odilia-app/atspi](https://github.com/odilia-app/atspi) | 2026-07-27 | Apache-2.0;MIT; |
| [odilia-app/odilia](https://github.com/odilia-app/odilia) | 2026-05-28 | Apache-2.0;MIT; |
| [olive-editor/olive](https://github.com/olive-editor/olive) | 2024-12-05 | GPL;v3; |
| [ollama/ollama](https://github.com/ollama/ollama) | 2026-11-06 | MIT; |
| [open-webui/open-webui](https://github.com/open-webui/open-webui) | 2026-11-03 | MIT;BSD; |
| [openSUSE/snapper](https://github.com/openSUSE/snapper) | 2026-11-04 | LGPL;GPL;v2; |
| [openai/codex](https://github.com/openai/codex) | 2026-11-07 | Apache-2.0; |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | 2026-11-06 | MIT; |
| [openai/whisper](https://github.com/openai/whisper) | 2026-08-31 | MIT; |
| [openatx/uiautomator2](https://github.com/openatx/uiautomator2) | 2026-11-02 | MIT; |
| [openbmb/MiniCPM-V](https://github.com/openbmb/MiniCPM-V) | 2026-10-30 | Apache-2.0; |
| [opendatalab/MinerU](https://github.com/opendatalab/MinerU) | 2026-11-04 | Apache-2.0; |
| [opendataloader-project/opendataloader-pdf](https://github.com/opendataloader-project/opendataloader-pdf) | 2026-11-06 | Apache-2.0; |
| [openrecall/openrecall](https://github.com/openrecall/openrecall) | 2025-09-24 | AGPL;GPL; |
| [oraios/serena](https://github.com/oraios/serena) | 2026-10-28 | GPL; |
| [osbuild/bootc-image-builder](https://github.com/osbuild/bootc-image-builder) | 2026-11-02 | Apache-2.0; |
| [osquery/osquery](https://github.com/osquery/osquery) | 2026-11-06 | LGPL;GPL;v2;Apache-2.0; |
| [ossec/ossec-hids](https://github.com/ossec/ossec-hids) | 2026-11-07 | LGPL;GPL;v2; |
| [ossf/scorecard](https://github.com/ossf/scorecard) | 2026-11-07 | Apache-2.0; |
| [ostreedev/ostree](https://github.com/ostreedev/ostree) | 2026-11-03 | LGPL;GPL;v2; |
| [paperless-ngx/paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | 2026-11-07 | GPL;v3; |
| [paullouisageneau/libjuice](https://github.com/paullouisageneau/libjuice) | 2026-11-05 | GPL;MPL-2.0; |
| [penpot/penpot](https://github.com/penpot/penpot) | 2026-11-07 | GPL;MPL-2.0; |
| [pgvector/pgvector](https://github.com/pgvector/pgvector) | 2026-11-04 | UNRECOGNISED |
| [phaserjs/phaser](https://github.com/phaserjs/phaser) | 2026-08-21 | MIT; |
| [philj56/tofi](https://github.com/philj56/tofi) | 2024-12-30 | MIT; |
| [photoprism/photoprism](https://github.com/photoprism/photoprism) | 2026-10-29 | AGPL;GPL; |
| [phw/peek](https://github.com/phw/peek) | 2025-09-24 | GPL;v3; |
| [pi-hole/pi-hole](https://github.com/pi-hole/pi-hole) | 2026-11-06 | EUPL; |
| [pimalaya/himalaya](https://github.com/pimalaya/himalaya) | 2026-11-06 | Apache-2.0;MIT; |
| [pimutils/khal](https://github.com/pimutils/khal) | 2026-10-20 | MIT; |
| [pimutils/vdirsyncer](https://github.com/pimutils/vdirsyncer) | 2026-09-03 | BSD; |
| [pion/webrtc](https://github.com/pion/webrtc) | 2026-11-06 | MIT; |
| [pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat) | 2026-11-07 | BSD; |
| [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn) | 2026-01-29 | BSD; |
| [pocketbase/pocketbase](https://github.com/pocketbase/pocketbase) | 2026-10-26 | MIT; |
| [pop-os/cosmic-comp](https://github.com/pop-os/cosmic-comp) | 2026-11-06 | GPL;v3; |
| [pot-app/pot-desktop](https://github.com/pot-app/pot-desktop) | 2026-07-04 | GPL;v3; |
| [pre-commit/pre-commit](https://github.com/pre-commit/pre-commit) | 2026-11-04 | MIT; |
| [prometheus/node_exporter](https://github.com/prometheus/node_exporter) | 2026-11-02 | Apache-2.0; |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | 2026-11-07 | MIT; |
| [protectai/llm-guard](https://github.com/protectai/llm-guard) | 2026-07-09 | MIT; |
| [protectai/rebuff](https://github.com/protectai/rebuff) | 2024-01-25 | Apache-2.0; |
| [pyannote/pyannote-audio](https://github.com/pyannote/pyannote-audio) | 2026-11-07 | MIT; |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 2026-11-07 | MIT; |
| [pystardust/ani-cli](https://github.com/pystardust/ani-cli) | 2026-11-07 | GPL;v3; |
| [python-zeroconf/python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) | 2026-11-06 | LGPL;GPL; |
| [pytorch/executorch](https://github.com/pytorch/executorch) | 2026-11-07 | BSD; |
| [qdrant/fastembed](https://github.com/qdrant/fastembed) | 2026-10-29 | Apache-2.0; |
| [qdrant/qdrant](https://github.com/qdrant/qdrant) | 2026-10-21 | Apache-2.0; |
| [qodo-ai/pr-agent](https://github.com/qodo-ai/pr-agent) | 2026-11-08 | MIT; |
| [qodo-ai/qodo-cover](https://github.com/qodo-ai/qodo-cover) | 2025-06-23 | AGPL;GPL; |
| [quickshell-mirror/quickshell](https://github.com/quickshell-mirror/quickshell) | 2026-10-21 | LGPL;GPL;v3; |
| [quickwit-oss/tantivy](https://github.com/quickwit-oss/tantivy) | 2026-11-05 | MIT; |
| [quinn-rs/quinn](https://github.com/quinn-rs/quinn) | 2026-11-06 | Apache-2.0;MIT; |
| [rauc/rauc](https://github.com/rauc/rauc) | 2026-11-07 | LGPL;GPL;CC; |
| [raysan5/raylib](https://github.com/raysan5/raylib) | 2026-11-07 | UNRECOGNISED |
| [rclone/rclone](https://github.com/rclone/rclone) | 2026-11-06 | MIT; |
| [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | 2026-11-07 | MIT;NONCOMMERCIAL?; |
| [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | 2026-11-06 | Apache-2.0; |
| [researchxxl/syncthing-android](https://github.com/researchxxl/syncthing-android) | 2026-11-06 | GPL;MPL-2.0; |
| [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox) | 2026-11-04 | MIT; |
| [restic/rest-server](https://github.com/restic/rest-server) | 2026-11-05 | BSD; |
| [restic/restic](https://github.com/restic/restic) | 2026-11-06 | BSD; |
| [returntocorp/semgrep](https://github.com/returntocorp/semgrep) | 2026-11-02 | LGPL;GPL; |
| [reviewdog/reviewdog](https://github.com/reviewdog/reviewdog) | 2026-11-07 | MIT; |
| [rfjakob/earlyoom](https://github.com/rfjakob/earlyoom) | 2026-10-26 | MIT; |
| [rhasspy/piper](https://github.com/rhasspy/piper) | 2025-08-26 | MIT; |
| [rhasspy/rhasspy3](https://github.com/rhasspy/rhasspy3) | 2023-04-21 | MIT; |
| [rhasspy/wyoming](https://github.com/rhasspy/wyoming) | 2026-11-05 | MIT; |
| [riverwm/river](https://github.com/riverwm/river) | 2026-11-05 | UNRECOGNISED |
| [roboflow/supervision](https://github.com/roboflow/supervision) | 2026-11-05 | MIT; |
| [run-llama/liteparse](https://github.com/run-llama/liteparse) | 2026-11-07 | Apache-2.0; |
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | 2026-11-03 | MIT; |
| [rust-minidump/rust-minidump](https://github.com/rust-minidump/rust-minidump) | 2026-11-03 | MIT; |
| [rustdesk/rustdesk](https://github.com/rustdesk/rustdesk) | 2026-11-07 | AGPL;GPL; |
| [rustic-rs/rustic](https://github.com/rustic-rs/rustic) | 2026-09-01 | Apache-2.0;MIT; |
| [ruvnet/claude-flow](https://github.com/ruvnet/claude-flow) | 2026-11-06 | MIT; |
| [rvaiya/keyd](https://github.com/rvaiya/keyd) | 2026-06-01 | MIT; |
| [rweather/noise-java](https://github.com/rweather/noise-java) | 2022-08-03 | MIT; |
| [ryoppippi/ccusage](https://github.com/ryoppippi/ccusage) | 2026-11-07 | UNRECOGNISED |
| [safing/portmaster](https://github.com/safing/portmaster) | 2026-10-30 | GPL;v3; |
| [sbabic/swupdate](https://github.com/sbabic/swupdate) | 2026-11-06 | LGPL;GPL;v2;CC; |
| [schemathesis/schemathesis](https://github.com/schemathesis/schemathesis) | 2026-11-08 | MIT; |
| [schlagmichdoch/PairDrop](https://github.com/schlagmichdoch/PairDrop) | 2026-04-22 | GPL;v3; |
| [schollz/croc](https://github.com/schollz/croc) | 2026-11-07 | MIT; |
| [screenpipe/screenpipe](https://github.com/screenpipe/screenpipe) | 2026-11-06 | NONCOMMERCIAL?; |
| [seedvault-app/seedvault](https://github.com/seedvault-app/seedvault) | 2026-11-05 | UNRECOGNISED |
| [semgrep/semgrep](https://github.com/semgrep/semgrep) | 2026-11-02 | LGPL;GPL; |
| [sentriz/cliphist](https://github.com/sentriz/cliphist) | 2026-10-07 | GPL;v3; |
| [sezanzeb/input-remapper](https://github.com/sezanzeb/input-remapper) | 2026-10-21 | GPL;v3; |
| [sgl-project/sglang](https://github.com/sgl-project/sglang) | 2026-11-07 | Apache-2.0; |
| [showlab/ShowUI](https://github.com/showlab/ShowUI) | 2026-04-24 | Apache-2.0; |
| [shubham0204/SmolChat-Android](https://github.com/shubham0204/SmolChat-Android) | 2026-11-01 | Apache-2.0; |
| [siddharthvaddem/openscreen](https://github.com/siddharthvaddem/openscreen) | 2026-10-16 | MIT; |
| [sigstore/cosign](https://github.com/sigstore/cosign) | 2026-11-04 | Apache-2.0; |
| [sigstore/gitsign](https://github.com/sigstore/gitsign) | 2026-11-04 | Apache-2.0; |
| [silitics/rugix](https://github.com/silitics/rugix) | 2026-11-02 | Apache-2.0;MIT; |
| [silverbulletmd/silverbullet](https://github.com/silverbulletmd/silverbullet) | 2026-11-06 | MIT; |
| [simular-ai/Agent-S](https://github.com/simular-ai/Agent-S) | 2026-10-21 | Apache-2.0; |
| [sipyourdrink-ltd/bernstein](https://github.com/sipyourdrink-ltd/bernstein) | 2026-11-07 | Apache-2.0; |
| [siyuan-note/siyuan](https://github.com/siyuan-note/siyuan) | 2026-10-28 | AGPL;GPL; |
| [slackhq/nebula](https://github.com/slackhq/nebula) | 2026-10-29 | MIT; |
| [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) | 2026-08-20 | AGPL;GPL; |
| [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | 2026-10-22 | MIT; |
| [snapdrop/snapdrop](https://github.com/snapdrop/snapdrop) | 2025-02-10 | GPL;v3; |
| [soupslurpr/AppVerifier](https://github.com/soupslurpr/AppVerifier) | 2026-09-18 | Apache-2.0;ISC; |
| [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) | 2026-11-07 | MIT; |
| [speaches-ai/speaches](https://github.com/speaches-ai/speaches) | 2026-04-17 | MIT; |
| [sst/opencode](https://github.com/sst/opencode) | 2026-11-07 | MIT; |
| [stability-AI/stable-fast-3d](https://github.com/stability-AI/stable-fast-3d) | 2025-01-22 | NONCOMMERCIAL?; |
| [stirling-tools/Stirling-PDF](https://github.com/stirling-tools/Stirling-PDF) | 2026-11-06 | MIT; |
| [str4d/rage](https://github.com/str4d/rage) | 2026-07-14 | Apache-2.0;MIT; |
| [stravu/crystal](https://github.com/stravu/crystal) | 2026-02-26 | MIT; |
| [streetwriters/notesnook](https://github.com/streetwriters/notesnook) | 2026-11-04 | GPL;v3; |
| [super-productivity/super-productivity](https://github.com/super-productivity/super-productivity) | 2026-11-07 | MIT; |
| [superagent-ai/superagent](https://github.com/superagent-ai/superagent) | 2026-08-25 | MIT; |
| [swaywm/sway](https://github.com/swaywm/sway) | 2026-09-21 | MIT; |
| [swaywm/swayidle](https://github.com/swaywm/swayidle) | 2026-10-25 | MIT; |
| [swaywm/swaylock](https://github.com/swaywm/swaylock) | 2026-10-29 | MIT; |
| [syncthing/syncthing](https://github.com/syncthing/syncthing) | 2026-11-02 | GPL;MPL-2.0; |
| [syncthing/syncthing-android](https://github.com/syncthing/syncthing-android) | 2024-12-03 | GPL;MPL-2.0; |
| [systemd/mkosi](https://github.com/systemd/mkosi) | 2026-11-05 | UNRECOGNISED |
| [systemd/systemd](https://github.com/systemd/systemd) | 2026-11-07 | LGPL;GPL;v2;CC; |
| [tailscale/tailscale](https://github.com/tailscale/tailscale) | 2026-11-07 | BSD; |
| [tailscale/tailscale-android](https://github.com/tailscale/tailscale-android) | 2026-11-06 | BSD; |
| [tanujnotes/Olauncher](https://github.com/tanujnotes/Olauncher) | 2026-11-05 | GPL;v3; |
| [tauri-apps/plugins-workspace](https://github.com/tauri-apps/plugins-workspace) | 2026-11-06 | Apache-2.0;MIT;CC; |
| [tauri-apps/tauri](https://github.com/tauri-apps/tauri) | 2026-11-05 | Apache-2.0;MIT;CC; |
| [teejee2008/timeshift](https://github.com/teejee2008/timeshift) | 2022-10-16 | LGPL;GPL;v3; |
| [tenacityteam/tenacity](https://github.com/tenacityteam/tenacity) | 2026-10-29 | LGPL;GPL;v2;CC; |
| [terl/lazysodium-android](https://github.com/terl/lazysodium-android) | 2025-05-31 | GPL;MPL-2.0; |
| [termux/termux-api](https://github.com/termux/termux-api) | 2026-09-17 | NO-LICENSE-FILE-AT-ROOT |
| [termux/termux-app](https://github.com/termux/termux-app) | 2026-09-26 | UNRECOGNISED |
| [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract) | 2026-11-02 | Apache-2.0; |
| [theupdateframework/go-tuf](https://github.com/theupdateframework/go-tuf) | 2026-11-04 | Apache-2.0; |
| [theupdateframework/python-tuf](https://github.com/theupdateframework/python-tuf) | 2026-11-05 | Apache-2.0;MIT; |
| [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | 2026-09-01 | MIT; |
| [thunderbird/thunderbird-android](https://github.com/thunderbird/thunderbird-android) | 2026-11-06 | Apache-2.0; |
| [thunderbird/thunderbolt](https://github.com/thunderbird/thunderbolt) | 2026-11-07 | GPL;MPL-2.0; |
| [tink-crypto/tink-java](https://github.com/tink-crypto/tink-java) | 2026-11-06 | Apache-2.0; |
| [tldraw/tldraw](https://github.com/tldraw/tldraw) | 2026-11-07 | NONCOMMERCIAL?; |
| [toeverything/AFFiNE](https://github.com/toeverything/AFFiNE) | 2026-11-06 | MIT; |
| [tonarino/innernet](https://github.com/tonarino/innernet) | 2026-07-28 | MIT; |
| [topgrade-rs/topgrade](https://github.com/topgrade-rs/topgrade) | 2026-11-07 | GPL;v3; |
| [tpm2-software/tpm2-tools](https://github.com/tpm2-software/tpm2-tools) | 2026-09-28 | NO-LICENSE-FILE-AT-ROOT |
| [trevp/noise](https://github.com/trevp/noise) | 2018-07-11 | NO-LICENSE-FILE-AT-ROOT |
| [trufflesecurity/trufflehog](https://github.com/trufflesecurity/trufflehog) | 2026-11-05 | AGPL;GPL; |
| [trycua/cua](https://github.com/trycua/cua) | 2026-11-07 | MIT; |
| [tulir/gomuks](https://github.com/tulir/gomuks) | 2026-11-06 | AGPL;GPL;MPL-2.0; |
| [tyrrrz/LightBulb](https://github.com/tyrrrz/LightBulb) | 2026-11-06 | MIT; |
| [ublue-os/bluefin](https://github.com/ublue-os/bluefin) | 2026-11-07 | Apache-2.0; |
| [ublue-os/main](https://github.com/ublue-os/main) | 2026-11-07 | Apache-2.0; |
| [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming) | 2025-11-12 | MIT; |
| [ulyssa/iamb](https://github.com/ulyssa/iamb) | 2026-11-03 | Apache-2.0; |
| [unslothai/unsloth](https://github.com/unslothai/unsloth) | 2026-11-07 | AGPL;GPL;Apache-2.0; |
| [upscayl/upscayl](https://github.com/upscayl/upscayl) | 2026-03-27 | AGPL;GPL; |
| [upstash/context7](https://github.com/upstash/context7) | 2026-11-06 | MIT; |
| [usefulsensors/moonshine](https://github.com/usefulsensors/moonshine) | 2026-11-05 | MIT;NONCOMMERCIAL?; |
| [uutils/coreutils](https://github.com/uutils/coreutils) | 2026-11-07 | MIT; |
| [vanilla-os/ABRoot](https://github.com/vanilla-os/ABRoot) | 2026-08-25 | GPL;v3; |
| [vikhyat/moondream](https://github.com/vikhyat/moondream) | 2026-04-19 | Apache-2.0; |
| [vkohaupt/vokoscreenNG](https://github.com/vkohaupt/vokoscreenNG) | 2026-11-07 | LGPL;GPL;v2; |
| [vllm-project/vllm](https://github.com/vllm-project/vllm) | 2026-11-07 | Apache-2.0; |
| [warner/python-spake2](https://github.com/warner/python-spake2) | 2024-09-25 | MIT; |
| [watchexec/watchexec](https://github.com/watchexec/watchexec) | 2026-11-04 | Apache-2.0; |
| [waycrate/swhkd](https://github.com/waycrate/swhkd) | 2026-04-26 | BSD; |
| [waycrate/wayshot](https://github.com/waycrate/wayshot) | 2026-11-07 | GPL;v3;BSD; |
| [wazuh/wazuh](https://github.com/wazuh/wazuh) | 2026-11-06 | AGPL;LGPL;GPL;v2; |
| [webrtc-rs/webrtc](https://github.com/webrtc-rs/webrtc) | 2026-11-06 | Apache-2.0;MIT; |
| [werman/noise-suppression-for-voice](https://github.com/werman/noise-suppression-for-voice) | 2024-05-18 | GPL;v3; |
| [wireguard/wireguard-go](https://github.com/wireguard/wireguard-go) | 2026-10-25 | MIT; |
| [wshobson/agents](https://github.com/wshobson/agents) | 2026-11-05 | MIT; |
| [wwmm/easyeffects](https://github.com/wwmm/easyeffects) | 2026-11-07 | GPL;v3; |
| [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | 2024-04-03 | BSD; |
| [xushengfeng/eSearch](https://github.com/xushengfeng/eSearch) | 2026-11-08 | GPL;v3; |
| [yggdrasil-network/yggdrasil-go](https://github.com/yggdrasil-network/yggdrasil-go) | 2026-10-24 | LGPL;v3; |
| [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 2026-11-04 | Unlicense;NONCOMMERCIAL?; |
| [zadam/trilium](https://github.com/zadam/trilium) | 2026-11-08 | AGPL;GPL; |
| [zerotier/ZeroTierOne](https://github.com/zerotier/ZeroTierOne) | 2026-07-22 | GPL;MPL-2.0; |
| [zrythm/zrythm](https://github.com/zrythm/zrythm) | 2026-10-25 | CC; |
