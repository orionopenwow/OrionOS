# Orion black hole shader

Orion's living black hole comes in two shaders that share one host contract (same uniforms, same screen geometry),
so everything that drives it (moods, voice, the agent-busy spin, smart reactions, the boot timeline) works on both:

| Tier | `u_quality` | Shader | What it is |
|---|---|---|---|
| Ultra | 4 | `blackhole_rt.frag` | Black hole 2.0 (after-1.0 plan §3): a real ray-march through curved space-time |
| High | 3 | `blackhole.frag` | The painted single-pass shader at full detail |
| Balanced | 2 | `blackhole.frag` | Fewer noise octaves |
| Battery saver | 1 | `blackhole.frag` | No eddies, two star depths, 75 % backing store |
| Still | 0 | `blackhole.frag` | One frame, redrawn only when something changes (reduced motion, software GL) |

Both are one full-screen fragment pass in GLSL ES 3.00 (WebGL2). The HUD core, the boot-frame renderer and the
greeter load `blackhole.frag`; the HUD switches to `blackhole_rt.frag` on Ultra.

## Ultra: the ray-marched black hole (`blackhole_rt.frag`)

- **Real geodesics.** Each pixel fires a light ray at a non-spinning (Schwarzschild) hole and bends it step by step
  with the photon equation of motion `a = -1.5 h² x / r⁵` (horizon radius 1, `h = |x × v|` conserved; leapfrog steps,
  fine near the photon sphere at r = 1.5, coarse far away; at most 170 steps). Rays start parallel, so a pixel's
  offset is its impact parameter. One screen unit is `2.598 / 0.082` horizon radii, which puts the shadow exactly
  where the painted tiers draw it, and the disk's inner edge at about 3: the innermost stable orbit.
- **Disk.** Where a ray crosses the disk plane it picks up the same painted disk (`black-hole-core.png`, the same
  Keplerian flow, rim dissolve, ripples, flare and boot reveal as `blackhole.frag`). Later crossings are the lensed
  images: the far side arching over the shadow when tilted, and the thin higher-order images that make the photon
  ring. Texture lookups inside the loop use an explicit mip level (no speckle).
- **Doppler beaming.** The gas orbits at the Keplerian speed `√(M/r)`; the shift `g = δ·√(1 - 1/r)` brightens the
  approaching (left) side as `g³` (normalised to the face-on value, so the painting keeps its brightness) and tints
  it slightly blue, the receding side slightly red. Face-on there is little real beaming, so a calm one-sided glow is
  added that fades out as the view tilts and the real effect takes over.
- **Volume.** A thin flared haze above and below the plane is gathered along each ray, which gives the disk depth
  and feeds the photon ring.
- **Horizon and sky.** Rays that fall below the horizon are pure black (the edge is anti-aliased on the critical
  impact parameter). Escaping rays look up the same three-depth starfield in their bent direction, so stars pile up
  into the Einstein ring for real.
- **Still tunable.** The photon ring keeps a highlight driven by `u_ring` and `u_voice` (it sits on the critical
  curve the march finds); the grade, glow, flash, comets and approval stars are the same code as the painted tiers.
- **Cost and fallback.** The HUD renders Ultra at 70 % of the backing store (`ultraScale`) and lets the browser
  upscale. The shader is compiled only when Ultra is first used; if it fails to compile, or frames take longer than
  ~28 ms for ~2 s (auto quality), the renderer steps down to High by itself. Auto picks Ultra only on a discrete GPU,
  High on strong integrated GPUs (Apple M, Iris Xe, Radeon 680M-class), Balanced elsewhere and Still on software GL
  or with reduced motion. WebGPU is not used yet (optional in the plan): WebGL2 runs everywhere the HUD does.
  Measured on SwiftShader (a CPU rasteriser) at 480×480: about 1.3–1.9 s per frame, against ~0.3 s for Balanced.

## The painted tiers (`blackhole.frag`)

The original painting (`apps/desktop/public/black-hole-core.png`) stays the source. The shader samples it in
polar coordinates as the accretion disk and sets it flowing.

## What it draws

- **Disk.** The painting turns with Keplerian differential rotation (ω ∝ r^-1.5), so the inner disk turns faster
  than the outer disk. Material drifts slowly inward. Two flow phases cross-fade, and the reset ripples outward by
  radius, so the spiral never winds into mush and the whole disk never pulses at once. The painting's grey-lilac
  rim dissolves into space through a ragged noise edge, cooled toward violet. No plate edge is visible.
- **Horizon.** Pure black, with a thin photon ring that is brighter on the side turning toward you (Doppler
  beaming, on the left) and a faint secondary ring just outside it.
- **Sky.** A point-lens deflection bends the starfield behind the hole. Stars pile up into a faint Einstein ring
  at the edge of the shadow. Stars sit in three depths, each twinkling and each with its own parallax.
- **Tilt.** `u_tilt = 1` shows the disk edge-on (the "Interstellar" view). The near side crosses in front of the
  shadow, and a lensed image of the far side arcs over the top, with a fainter arc underneath.
- **Boot.** The timeline is driven by `u_boot`. The shader does not draw the "orion" wordmark; the host draws it
  at 4.3 s.

## Host contract

- Draw a full-screen triangle. The shader uses only `gl_FragCoord` and `u_resolution`, with no varyings.
- Upload the painting with `UNPACK_FLIP_Y_WEBGL = true` and `UNPACK_PREMULTIPLY_ALPHA_WEBGL = true`. Without
  premultiplied alpha, the painting's transparent rim fringes. Use mipmaps, `LINEAR_MIPMAP_LINEAR` filtering and
  `CLAMP_TO_EDGE` wrapping.
- The output is premultiplied alpha. With `u_transparent = 0` the frame is opaque.

## Uniforms

| Uniform | Range / default | Meaning |
|---|---|---|
| `vec2 u_resolution` | pixels | Canvas size. The scene scales to the shorter side. |
| `float u_time` | seconds | Clock for twinkle, ripples, noise and starfall. |
| `sampler2D u_disk` | | `black-hole-core.png`, premultiplied. |
| `float u_spin` | 1 = normal | Disk rotation speed (0 stops it, which is what sleep uses). |
| `float u_temp` | -1 … +1, default 0 | Colour temperature. Negative values cool toward blue-violet; positive values warm through magenta (0.45) and crimson (0.75) to amber (1). The stars are never tinted. |
| `float u_ring` | 1 = normal | Photon ring brightness. |
| `float u_glow` | 1 = normal | Violet bloom around the hole. Values below 1 also dim the disk (down to 55 % at 0), which winddown uses. |
| `float u_turbulence` | 0 … 1, default 0.25 | Strength of the turbulent eddies that move with the flow. |
| `float u_size` | 1 = normal | Scale of the whole hole. The disk reaches 0.40 × u_size of the shorter side. |
| `float u_tilt` | 0 face-on … 1 edge-on | Viewing inclination, with the lensed arc. |
| `float u_boot` | seconds, or < 0 | Boot timeline: spark 0 → starfall 0.8 → collapse and ring flash 2.6 → accretion (disk paints in from outside, spinning up) 3.3 → wordmark (drawn by the host) 4.3 → settled ≥ 6.0. Negative means fully formed. |
| `float u_voice` | 0 … 1 | Hot-mic level. The ring and the inner glow brighten. |
| `float u_ripple` | 0 … 1 | Orion speaking. Ripples move outward through the disk. |
| `float u_quality` | 0 … 4 | 0 = Still (the host draws one frame, at full detail), 1 = Battery (no eddies, two star depths, no secondary ring), 2 = Balanced, 3 = High (more eddy octaves). 4 = Ultra is `blackhole_rt.frag` (values above 4.5 there allow 260 steps and one more noise octave). |
| `vec2 u_parallax` | -1 … 1 | Shifts the three star depths by different amounts. |

Optional extras. Leaving them unset (0) keeps the contract behaviour:

| Uniform | Meaning |
|---|---|
| `float u_flow` | Integrated spin-time (Σ dt·spin). When it is non-zero it replaces `u_time * u_spin`, so changing the spin never makes the disk jump. The HUD renderer always sets it. |
| `float u_hue` | Extra hue rotation in radians, used for HUD skins and focus mode (-0.9 gives quasar cyan). |
| `float u_flare` | 0 … 1. Smart reactions: the disk flares from the inside out (a file fed to the hole, an approval falling in). |
| `float u_ring2` | 0 … 1. Smart reactions: a soft second ring just outside the photon ring (an update is ready). |
| `vec4 u_bodies[10]` | Smart reactions: small bodies near the hole (message comets, approval stars). `xy` is the position in the disk plane (same units as the frame, seen at the current tilt), `z` the brightness (0 = slot off), `w` the colour (0 Starlight, 1 Ember, 2 Photon, 3 violet) plus the tail length in its fraction. Drawn ungraded, like the sky. |
| `float u_transparent` | 1 = overlay mode. The frame fades to transparent at its edge, and only the lensed sky near the hole is drawn, so it sits on a page that has its own starfield (the HUD). |

## Mood presets used by the HUD

Defined in `apps/desktop/src/lib/blackhole/presets.ts`:

| Mode | spin | temp | ring | glow | other |
|---|---|---|---|---|---|
| idle | 1 | 0 | 1 | 1 | |
| focus | 1.5 | -0.3 | 1.35 | 1.15 | hue -0.9 (cyan quasar) |
| voice | 1.3 | -0.15 | 1.15 | 1.1 | hue -0.35; `u_voice` live |
| podcast | 1.35 | 0.45 | 1.2 | 1.1 | turbulence 0.45 |
| sanctuary | 0.5 | 0.95 | 0.85 | 0.8 | |
| winddown | 0.25 | 0.75 | 0.5 | 0.3 | |

## Screenshot tests

```sh
node os/theme/blackhole/render.mjs --out /tmp/bh            # standard set: settled, tilt, boot timeline, moods, tiers
node os/theme/blackhole/render.mjs --frame edge:tilt=1,time=30 --frame boot:boot=2.75 --size 1080
node os/theme/blackhole/render.mjs --frame x: --bench 30     # mean frame time on this renderer
node os/theme/blackhole/render.mjs --ultra --out /tmp/bh-ultra  # the same set on the ray-marched Ultra tier
node os/theme/blackhole/render.mjs --check                   # compile + link both shaders, list their uniforms (JSON)
python3 -m pytest os/theme/blackhole/tests                   # uniform contract test, plus --check when Chromium is here
```

The script uses Playwright's Chromium from `PLAYWRIGHT_BROWSERS_PATH` (default `/opt/pw-browsers`) and never
downloads a browser. In CI it runs on SwiftShader, a CPU rasteriser, so `--bench` gives a relative cost, not
real GPU time. Measured on SwiftShader at 600×600: about 330 ms per frame at Balanced. On real GPUs, check the
HUD's frame rate with the renderer's `frameIntervalMs`.

The boot-frame renderer for Plymouth can reuse `render.mjs`: pass a list of `--frame name:boot=T` values, or
import the HTML harness from it.

## Depth and Gravity mode

- **Depth (HUD).** `apps/desktop/src/components/SpaceCurve.tsx` draws a faint violet grid that is straight at the
  screen edges and bends toward the core (`lib/spaceCurve.ts`: a lens-like pull that is zero at the rim and never
  folds), with a soft vignette down the slope and, now and then, a faint ring sliding into the well. Still with
  calm motion / reduced motion; off with Settings > Look & motion > Depth.
- **Gravity mode (real windows).** Needs the optional Hyprland session: `os/desktop/hyprland/gravity.frag` is a
  Hyprland screen shader with the same pull, applied by `orion-gravity` (off by default; paused on battery and with
  reduced motion). See `os/desktop/hyprland/hyprland.conf` and `os/image/os-hyprland.sh`.
