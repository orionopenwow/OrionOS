# Orion fonts

All three families are under the SIL Open Font License 1.1. The licence texts sit next to the fonts:
`OFL-Unbounded.txt`, `OFL-Figtree.txt` and `OFL-MartianMono.txt`. The files come from the google/fonts
repository (`ofl/unbounded`, `ofl/figtree`, `ofl/martianmono`). The static instances were cut from the
variable fonts with fontTools `varLib.instancer`. That is allowed under the OFL, and the names are unchanged
because these are not Reserved Font Names.

| Family | Use | Files |
|---|---|---|
| Unbounded | Wordmark, clock, headings (and GRUB via `grub-mkfont`) | `Unbounded-Variable.ttf` (wght 200–900), `Unbounded-Regular/SemiBold/Bold.ttf` |
| Figtree | All reading text: chat, cards, settings, GTK | `Figtree-Variable.ttf`, `Figtree-Italic-Variable.ttf` (wght 300–900), `Figtree-Regular/Medium/SemiBold/Bold.ttf` |
| Martian Mono | Terminal (foot), code, numbers | `MartianMono-Variable.ttf` (wght 100–800, wdth 75–112.5), `MartianMono-Regular/Bold.ttf` (default wide width, 112.5) |

Nunito stays as the fallback everywhere.

**OS install:** copy `*.ttf` to `/usr/share/fonts/truetype/orion/` and run `fc-cache -f`. The fontconfig family
names are `Unbounded`, `Figtree` and `Martian Mono`.

**Desktop app:** the HUD bundles the same families offline as woff2, through the `@fontsource-variable/*` npm
packages, which are also OFL.
