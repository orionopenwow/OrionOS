# Event Horizon (OrionOS)

The VS Code side of the OrionOS Art Update (§4 "Code editor", §7 "Event Horizon"):

- the same dark glass as the rest of OrionOS (Void `#05070E`, Event `#0B0F1E`, Edge `#262D50`), violet
  (Accretion `#7C5CFF`) and cyan (Photon `#00E5FF`) accents, Starlight `#D8DEFF` text;
- a violet status bar;
- the integrated terminal uses the Event Horizon 16 colours (identical to the foot terminal theme);
- Martian Mono for code and the terminal, minimap on (set as defaults; your own settings win).

On OrionOS, SwayFX makes the VS Code window slightly see-through (`opacity 0.90`) and blurs what's behind it.

Build the .vsix (offline):  `npx @vscode/vsce package --allow-missing-repository` (in this folder).
Install it: `code --install-extension event-horizon-1.0.0.vsix` (or Codium: `codium --install-extension …`).

Licence: MIT (theme JSON and icon are original OrionOS work).
