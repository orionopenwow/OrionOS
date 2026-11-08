# Command console: design brief

Owner feedback on the full-screen console (Nov 2026): "all blends together ... it's all too white and the same,
and not fun or descriptive and simple." Earlier direction still stands: deep cobalt accent, gunmetal, matte (no
glow), rounded, thicker strokes and icon lines, readable text, space visible behind, at most five focus cards that
go away when chatting.

## What was wrong

Everything had the same weight. 26 feature rows with a white title and a white-ish description each, five focus
cards of the same size, shape and colour, three or four equal buttons on every card, every heading in the same
display font, and cobalt used for everything. Nothing told the eye where to go first.

## Principles

1. **One hero.** The "Now" card is the most important thing on screen, so it is visibly bigger, has its own
   layout, a big progress bar with the percentage, and one button. Size means importance; nothing else competes
   with it. (Dashboard practice: one primary item per view, the rest supporting. Apple's widget guidance makes the
   same point: one idea per widget, the most important information biggest.)
2. **Colour is a category, not decoration.** Each kind of card has one colour role, used only on small parts (a
   left bar, the icon chip, the small label): Now = cobalt, Message = teal, Waiting for your OK = amber, Needs you =
   ember, Next up = lilac. Feature groups do the same: Work = cobalt, Life = peach, Connect = teal, System = steel.
   Big areas stay gunmetal. (Material 3 colour roles: accent colours are assigned a job and used on small
   elements; GitHub's labels and Linear's status colours work the same way.)
3. **Three text tiers that really differ.** Titles white and heavy; body soft grey; meta small and dim or tinted.
   All three pass WCAG AA on the card surfaces (numbers below). We overcorrected to all-white; this fixes it.
4. **One primary action per card.** The obvious next step is a button. Pin, Not important and Draft a reply move
   into a small "More" menu on each card that works with the keyboard and is named for screen readers. (Linear's
   triage and Raycast both keep one default action on Enter and put the rest in an actions menu.)
5. **Show less, explain on demand.** Feature sides become small icon tiles (icon + short name). The description
   shows in a line under the group when you point at or tab to a tile, and stays in search results and in what
   screen readers hear. Sides are quieter than the middle, so the centre dominates. (Progressive disclosure; macOS
   Control Centre shows a grid of compact tiles and expands only what you touch.)
6. **Fun but calm.** A greeting with a live detail ("Evening. 1 thing running, 2 waiting for your OK and
   2 messages."), soft tinted icon chips, a small lift on hover, and a friendly "All clear" made from the Orion mark.
   No glow, no neon; motion turns off with reduced motion.
7. **Different shapes for different things.** The hero is a wide panel; the other cards sit two to a row; features
   are a grid of tiles. You can tell them apart when squinting, before reading a word.

## Tokens

| Role | Colour | On card #1D222A |
| --- | --- | --- |
| Title text | #FFFFFF | 16.0 : 1 |
| Body text | #C3CBD7 | 9.8 : 1 |
| Meta text | #9AA6B8 | 6.5 : 1 (5.3 : 1 on #2A313B) |
| Now / Work | #8DB0FF on #2E6BFF chips | 7.5 : 1 |
| Message / Connect | #4FD1C5 | 8.6 : 1 |
| Waiting for your OK | #F5B942 | 9.1 : 1 |
| Needs you | #FF8A6B | 6.9 : 1 |
| Next up | #B79CFF | 7.0 : 1 |
| Life | #FFB98A | 9.6 : 1 |
| System | #A9B6C8 | 7.8 : 1 |

The body and meta tiers also replace the HUD-wide text tokens (`--orion-text-2`, `--orion-text-3`, Tailwind
`ink-2`, `ink-3`), so other windows get the same three tiers without a separate sweep. The kind and group colours
live as CSS variables (`--role-*`, `--cat-*`) at the end of `styles/glass.css`.

Type: Unbounded only for the greeting and the hero title. Everything else is Figtree, with weight doing the work.

## Sources

The repo's network rule (`[network]` in CLAUDE.md: only package registries and GitHub clones) means no web pages
were fetched for this pass. These are the public references the principles above come from:

- Material Design 3, colour roles: https://m3.material.io/styles/color/roles
- Apple Human Interface Guidelines, Widgets and Layout: https://developer.apple.com/design/human-interface-guidelines/widgets
- Apple HIG, Color (use colour to communicate, not decorate): https://developer.apple.com/design/human-interface-guidelines/color
- Nielsen Norman Group, Progressive disclosure: https://www.nngroup.com/articles/progressive-disclosure/
- Nielsen Norman Group, Visual hierarchy in UX: https://www.nngroup.com/articles/visual-hierarchy-ux-definition/
- Raycast's action panel (one default action, the rest in a menu): https://manual.raycast.com/action-panel
- Linear triage (one decision per item, status colours): https://linear.app/docs/triage
- WCAG 2.2 contrast minimum: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum
