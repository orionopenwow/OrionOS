# Orion Art Update: plan (not started)

Status: plan only, written 2026-10-16; all design decisions made the same night. Saved into the repo as
`docs/ORION_ART_UPDATE_PLAN.md`; the render the plan calls the "windows" look is `docs/assets/ui-reference/07-art-update-windows.png`. Nothing in the repo has changed. Concept renders, the boot clip and a
working prototype of the new black hole shader are on the "Orion Art Update" artifact page. The source for those
renders sits outside the repo in the build workspace (`/home/claude/artupdate`).

**Goal:** keep everything that makes Orion look like Orion: the black hole in the centre, the painted PNG, and the
violet/cyan on deep space. Make it feel alive, make booting in feel like an event, and give it a proper brand:
logo, fonts, terminal look.

## 1. What's wrong today

- **The core is a picture turning on the spot.** `Singularity.tsx` stacks two copies of `black-hole-core.png` and
  rotates them with CSS. The painting has a soft grey-lilac rim, so on the void background you see a round plate
  with an edge. Because it turns as one rigid disc, it reads as a spinning image, not a black hole.
- **The boot is a turning PNG plus "OrionOS" in plain Sans.** That's in the Plymouth theme
  (`os/image/files/usr/share/plymouth/themes/orion`). The GRUB menu is plain text.
- **The fonts don't feel like Orion's own.**
  - The interface uses Nunito and Arial Rounded.
  - The terminal (foot) and Sway use stock fonts and colours.
  - There is no logo, only the word "orion".

## 2. The living black hole (the heart of the update)

One WebGL shader replaces the two rotating images. The prototype already exists (`bh.js`, one pass).
**The original painting stays the source:** the shader reads the PNG instead of replacing it.

- **Spins like the real thing.** The inside turns faster than the outside (Keplerian rotation), so the spiral arms
  wind and flow. Material slowly drifts inward. A cross-fade between two flow phases keeps it from winding into
  mush.
- **True black centre.** The event horizon is pure black, with a thin photon ring that is brighter on the side
  turning toward you (Doppler beaming) and a faint second ring just outside.
- **No edges.** The painting's rim dissolves into space, cooled toward violet instead of grey.
- **Space bends around it.** The stars behind are lensed, piling up into a faint Einstein ring at the shadow's
  edge. Stars twinkle in three depths.
- **Keeps today's moods.** The existing modes (idle, focus, voice, podcast, sanctuary, winddown) become shader
  settings for spin speed, colour temperature, ring brightness and glow, so the rest of the HUD doesn't change.
- **Cost.** About 2 ms of GPU per frame at 1080p on an integrated GPU (to be measured). The disk can render at half
  resolution and be upscaled. It pauses when hidden or when a full-screen game (WoW) is running.

## 3. Booting in: a full experience (about 6 seconds, never slower than the boot itself)

The same shader drives every stage, so it is one continuous shot from power button to desktop.

| Time | What you see | Runs in |
|---|---|---|
| 0.0 s | Black. A single violet spark fades in at the centre. | GRUB, then Plymouth |
| 0.8 s | Star fall: stars stream inward in a spiral, faster and faster. | Plymouth |
| 2.6 s | Collapse: the spark goes dark and the photon ring ignites with a flash. | Plymouth |
| 3.3 s | Accretion: the painted disk paints itself in from the outside, spinning up. | Plymouth |
| 4.3 s | "orion" condenses out of star dust beneath it. | Plymouth |
| — | Vault unlock (USB stick): the passphrase appears as stars on an orbit line, and the newest one glows cyan. A wrong passphrase sends the stars spinning off the line. | Plymouth |
| — | Login (if password mode): the starry greeter from Phase 1, now with the same hole. | greetd greeter |
| arrival | The camera eases in, the windows fade in as glass, and the taskbar tiles rise in at the bottom left. | HUD |

**How it's built:**
- **Plymouth can't run shaders,** so the boot clip is rendered ahead of time from the same shader into a short PNG
  frame sequence: about 90 frames of the centre region at 720p, roughly 15–25 MB in the initramfs (to be checked).
- **Plymouth plays the frames against boot progress.** If the machine is still loading, it loops the "forming"
  part; it never adds waiting time.
- **Hand-off:** each stage ends on exactly the frame the next one starts on. Phase 1's `ORION_LOGIN_HANDOFF`
  already exists for this. Sway gets a black background so nothing flashes in between.
- **GRUB theme:** star background, the mark, Unbounded converted with `grub-mkfont`, and violet highlights.
- **Shutdown** plays it backwards: panels spiral into the hole, the ring flares and then fades to a single spark.
  **Sleep** slows the disk to a stop and dims it.
- **Boot sound (on by default):** a low swell, and a soft chime when the ring ignites. It can be turned off in
  Settings.

## 4. The desktop: more alive, still calm

**The reference look (decided 2026-10-16) is the "windows" render, applied to everything.** Orion's own panels
(chat, Next up, the terminal) are the same frosted glass windows as normal apps, with the belt dots, a title and
the right-side buttons. There is no radial or orbit toolbar anywhere.

- **Glass lit by the core.** On top of the window style below, the edge of each window picks up a faint tint from
  the side facing the black hole.
- **Taskbar.** Glass tiles at the bottom left, with the Orion mark first. Open apps get a violet tile with a glow;
  hover lifts a tile slightly.
- **Agent robots in orbit** (from Phase 1), with faint trails. A robot that finishes leaves a short sparkle. A
  stuck robot glows ember.
- **Parallax space.** Three star depths move slightly with the mouse (or tilt, on a tablet).
- **Rare events.** A shooting star, or a comet that curves around the hole: a few per hour at most, never while
  you type. They can be turned off.
- **New windows** grow out of the horizon; minimised windows spiral into the disk.
  - This works now inside the HUD.
  - For normal app windows, Sway itself would need to change; see decision 3.
- **Lock screen and wallpapers:** 4K stills rendered from the shader, for swaylock and anywhere Orion isn't running.

### Window style (chosen 2026-10-16)

The reference shows large frosted-glass windows with the black hole glowing through them. The concept render
"windows" on the artifact page shows Orion's version.

- **Glass:**
  - Dark, see-through glass (about 34% tint), lightly blurred so the black hole and stars glow through.
  - Rounded corners (16 px), a soft deep shadow and a thin light edge.
  - The window in focus gets a violet edge glow; windows behind it are slightly dimmer.
- **Title bar:**
  - Three dots on the left in Orion colours, a nod to Orion's belt: close is rose, minimise is ember, full size is
    aurora.
  - Tabs with an icon, and a "+" for a new tab.
  - Minimise / maximise / close also on the right, for people used to Windows.
- **File manager:** themed Thunar.
  - Toolbar: back / forward / up, a breadcrumb path pill and a search box.
  - Sidebar sections: Places (each with its own colour), Devices (Vault, Orion USB) and Network.
  - Large Orion folders in violet, blue and cyan gradients.
  - A status bar showing item count, what's selected and free space in the vault.
- **Code editor:** a VS Code "Event Horizon" theme.
  - The same glass, a file tree, Martian Mono with the terminal colours, and a minimap.
  - A violet status bar that also shows Orion's own notes (for example "fix approved").
- **Taskbar:** glass tiles at the bottom left, with the Orion mark first, as in the reference.
- **How it's done for normal apps:**
  - **Compositor:** SwayFX (https://github.com/wlrfx/swayfx), with blur, `corner_radius`, shadows, and
    `default_dim_inactive` for the dimmer background windows. Window see-through comes from the GTK theme plus
    Sway's `opacity`.
  - **Building it:** SwayFX isn't packaged for Ubuntu, so the image build compiles it (and scenefx). Which
    wlroots version it needs on 24.04 must be checked first. If it can't be built cleanly, the fallback is plain
    Sway with opaque glass-coloured windows.
  - **Themes:** a GTK 3/4 theme "Orion Glass" covers Thunar, settings and dialogs. A VS Code theme extension
    covers the editor, and the foot config covers the terminal.

## 5. Smart things: the black hole reacts to what's happening

These all use existing daemon signals, and each one is a shader input. The aim is to be readable at a glance,
never noisy.

- **Listening and talking.** While the hot mic hears you, the ring brightens with your voice level. When Orion
  answers, ripples move outward through the disk in time with its voice.
- **Thinking.** While models are working, the disk spins faster and the ring warms up. The compute meter's spend
  shows up as heat, so an expensive hour literally looks hotter.
- **Messages fall in.** A new message enters as a small comet that spirals into the disk. Urgent ones are ember.
  Digest ones are tiny and arrive in a batch.
- **Approvals orbit.** Each decision waiting for you is a bright star circling just outside the ring. Approve it
  and it falls in with a flash; reject it and it drifts away and fades.
- **Feeding the hole.** Drop a file on the core to save it to the vault; the disk flares as it swallows it.
- **Time of day.** Cool violet at night, a touch of cyan at dawn, dimmer and slower in quiet hours and winddown.
- **Idle show.** After a few minutes idle, the view slowly tilts until the disk is seen edge-on with a lensed arc
  over the top (the Interstellar look). Moving the mouse brings it back face-on. Lock follows later.
- **System health.** High CPU makes the disk more turbulent, and low battery makes it smaller and dimmer. An
  update that is ready shows as a soft second ring.

## 6. Brand: logo, fonts, colours

- **Mark.** The black hole as a circle with its photon ring, bright on one side, and Orion's belt (three stars)
  drifting toward its edge.
  - Lockups: "orion" and "orion os".
  - Versions: app icon, small sizes, and one for light paper and print.
  - Used for: the phone app icon and splash, the GRUB menu, the USB stick name, favicons and the mako
    notifications icon.
- **Fonts** (all free OFL fonts, bundled offline, Nunito kept as the fallback):
  - **Unbounded** for the wordmark, clock and headings. It's wide, round and confident, and keeps the friendliness
    of today's rounded fonts.
  - **Figtree** for everything you read: chat, cards and settings.
  - **Martian Mono** for the terminal, code and numbers. It's a wide mono with a space name that fits.
- **Colours.** These keep today's palette and add one warm colour:
  - Void `#05070E`
  - Event `#0B0F1E`
  - Edge `#262D50`
  - Accretion `#7C5CFF`
  - Photon `#00E5FF`
  - Starlight `#D8DEFF`
  - Ember `#FFC66B`: the only warm colour, for things that need you (stuck, urgent).

## 7. Command line: "Event Horizon"

- **foot terminal theme** (`os/lab/foot.ini`):
  - Background: Void, slightly translucent.
  - Text: Starlight.
  - Cursor: a Photon block with a soft glow.
  - Font: Martian Mono.
  - The 16 colours, in normal / bright pairs:

    | Colour | Normal | Bright |
    |---|---|---|
    | black | `#0B0F1E` | `#262D50` |
    | red (rose) | `#FF5C8A` | `#FF8AAD` |
    | green (aurora mint) | `#5CFFB0` | `#9BFFD0` |
    | yellow (ember) | `#FFC66B` | `#FFDCA0` |
    | blue | `#6E8BFF` | `#9DB0FF` |
    | magenta (violet) | `#9D85FF` | `#C2B3FF` |
    | cyan | `#00E5FF` | `#7FF2FF` |
    | white | `#D8DEFF` | `#FFFFFF` |
- **Prompt** (Starship, or a small hand-written one): a violet `◉`, the folder in cyan, the git branch in violet,
  changes in ember, and how long the last command took. Typing happens after a violet `›` on the next line.
- **`orion` with no arguments** shows a small ANSI black hole next to system info (fastfetch with an Orion logo),
  and a short greeting ("Morning") plus what's waiting.
- **Long commands** that finish while you're looking elsewhere send a small star to the core and a notification.
- **Matching themes** for VS Code, GTK/Qt (dark Adwaita recoloured), wofi, mako notifications, swaylock, Thunar,
  plus an Orion cursor whose busy cursor is a tiny spinning disk.

## 8. Quality settings and accessibility

- **Graphics quality:** Ultra / Balanced / Battery saver / Still, chosen automatically from the GPU and battery
  and changeable in Settings.
  - VMs and QEMU get "Still" or Balanced with software rendering.
  - A full-screen game always pauses the effects.
- **Reduced motion** shows a still render and simple fades everywhere, including the boot (a fade-in) and the
  login.
- **Contrast:** text never sits directly on the bright part of the disk; panels keep enough contrast for small
  text.

## 9. Build order (when you say go)

1. **Core:** shader into the HUD.
   - Replace the Singularity images, keeping the modes API.
   - Quality tiers, pause rules.
   - Screenshot tests in headless Chromium (the method used for these concepts).
   - An fps check.
2. **Boot chain:**
   - Frame renderer.
   - New Plymouth theme with progress-driven playback and the orbit vault prompt.
   - GRUB theme.
   - Greeter on the same shader.
   - Hand-off, shutdown and sleep.
   - Tested in QEMU like 1.0 (Secure Boot on).
3. **Brand:**
   - Mark and app icons for desktop, phone and favicon.
   - Fonts bundled, with type tokens in `theme/colors.ts` and `index.css`.
4. **Desktop polish:**
   - The SwayFX build in the image (blur, corners, shadows, dimmed background windows).
   - The "Orion Glass" GTK theme, Orion folders and icons, and the taskbar.
   - Lit glass, parallax, rare events and window grow/spiral (HUD).
   - Wallpapers and lock screen.
5. **Command line:** foot theme, prompt, `orion` fetch, VS Code/GTK/wofi/mako/cursor themes.
6. **Smart reactions:** voice, thinking/compute, messages, approvals, feeding, time of day, idle show, system
   health.
7. **Check:** QEMU boot video, screenshots of each stage, fps on the laptop, reduced-motion pass.

Parallel agents can split it: stages 1 and 3, stages 2 and 5, and stage 4. Stage 6 follows stage 1.

## 10. Decisions for you

Decided on 2026-10-16:

- **Black hole design:** approved.
- **Fonts:** Unbounded / Figtree / Martian Mono, approved.
- **Window style:** frosted see-through glass, per the reference. This means SwayFX, built from source.
- **Overall look:** the "windows" render for everything, including Orion's own panels. The taskbar is glass tiles
  at the bottom left. No radial or orbit toolbar.

- **Logo:** the black hole with the belt stars, kept as shown.
- **Liveliness:** calm. Rare events (a shooting star or comet a few times an hour at most) and gentle reactions.
- **Boot sound:** on by default. A low swell, then a soft chime when the ring ignites. It follows the system volume
  and mute, and can be turned off in Settings.

All design decisions are made. Next step: build when you say go, and only once the other conversation's work in
the repo is finished or merged, so the two don't edit the same files.
## 11. Notes from saving this plan into the repo

- **The other conversation's repo work is finished.** It touched these files:
  - `os/image/build.sh` (safe file copy and permissions);
  - `os/image/files/usr/local/bin/orion-vault` (the vault is also allowed on a disk that holds only OrionOS);
  - `os/windows/Try-OrionOS-VM.ps1` and `Try OrionOS VM.cmd`;
  - `.gitignore`;
  - the plan docs.

  The art update doesn't need those files, except that stage 2 (boot chain) builds through `build.sh`.
- **The compositor choice now agrees across the plans:** the art update picks SwayFX, which is also the 1.0 plan's
  recommendation. Optional Gravity mode in the after-1.0 plan (bending the real windows) still needs a compositor
  with full-screen shaders (Hyprland or Wayfire). SwayFX covers blur, corners, shadows and dimming, but not
  warping.
- **The boot references:** the 1.0 plan's boot section has `05-boot-hardware-check.png` (constellation wordmark +
  real hardware-check lines) and `06-boot-logo.png`. This plan's boot timeline replaces the logo screen.
  **Decided: keep the boot clean.** The hardware-check screen is dropped: no check text during boot. If a check
  fails, a plain message appears instead.
- **Privacy:** the `orion` command greets with "Morning" (or the name you choose in Settings). No name is built
  into the image or the docs.
