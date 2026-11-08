# Orion's voice

Orion speaks with **Kokoro**, built in, by default. If you want a richer voice, Orion can also speak through
**Voicebox**, a free (MIT) voice studio that runs on your own computer. Nothing about your voice or Orion's
speech leaves the computer: Orion only talks to Voicebox at `127.0.0.1` and refuses any other address.

Choose the voice in any of these places. They all use the same settings:

- **OrionOS first-run**: the "Choose Orion's voice" step, straight after "Voice". Run it again any time with
  `orion-voice-setup`, or open **Settings > Orion > Orion's voice**.
- **The Orion window (HUD)**: Menu > More > **Orion's voice**, or Settings > Orion's voice. The first time, a small
  card asks "Choose Orion's voice?".
- **Start Here (Windows)**: at the end it opens the voice page in the Orion window.

In each one you can hear samples, pick a voice, set the speed and warmth, type a sentence for Orion to say, say
something and hear it back, and (with Voicebox) design a voice by describing it in words.

> Only use your own voice or a voice you have permission to use.
> Orion never clones a voice by itself. A cloned voice made in Voicebox can only be picked after you tick that line.

## Recommended free voices

| Voice model | Sounds | On a normal PC (no graphics card) | With a graphics card | Download | Licence | Languages | Copies a voice? |
|---|---|---|---|---|---|---|---|
| **Kokoro 82M** (built in) | Clear, pleasant, a little flat | Faster than real time | Instant | ~350 MB | Apache-2.0 | English, Spanish, French, Hindi, Italian, Portuguese, Japanese, Chinese | No (about 50 ready voices) |
| **Qwen3-TTS 1.7B** (Voicebox) | Very natural, follows delivery notes | Slow (several seconds per sentence) | Real time or better | ~3.5 GB | Apache-2.0 | Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian | Yes |
| **Qwen3-TTS 0.6B** (Voicebox) | Natural, a bit less rich | Usable, slow | Fast | ~1.2 GB | Apache-2.0 | Same 10 | Yes |
| **Qwen CustomVoice** (Voicebox) | 9 ready voices, delivery in plain words ("calm, with a smile") | Slow | Real time | 1.2-3.5 GB | Apache-2.0 | Same 10 | No (presets) |
| **Qwen VoiceDesign 1.7B** (Voicebox) | A new voice from a written description | Slow | Real time | ~3.5 GB | Apache-2.0 | Same 10 | No (made from words) |
| **Chatterbox Turbo** (Voicebox) | Expressive English, `[laugh]` and `[sigh]` tags | Usable | Very fast (350M) | ~1.5 GB | MIT | English | Yes |
| **Chatterbox Multilingual** (Voicebox) | Natural, many languages | Slow | Fast | ~3 GB | MIT | 23 languages | Yes |
| **LuxTTS** (Voicebox) | Good, 48 kHz | Very fast (made for CPUs) | Very fast | ~300 MB | **Not stated in Voicebox; check the model card (YatharthS/LuxTTS) before commercial use** | English | Yes |
| **HumeAI TADA 1B / 3B** (Voicebox) | Natural, long passages | Too slow | Good | 4 GB / 8 GB | **Not a plain free licence: built on Meta's Llama 3.2, so Llama's community licence terms likely apply. Check before commercial use** | English (1B); 10 languages (3B) | Yes |
| **OmniVoice** (Voicebox) | 600+ languages | Too slow | Fast | ~3.3 GB | **Non-commercial only** (weights CC-BY-NC). Voicebox labels it so; don't use it for anything you sell | 600+ | Yes |
| **Piper** (only if already installed) | Robotic but clear | Instant, even on tiny computers | Instant | 20-120 MB per voice | Code MIT (original rhasspy/piper; the newer piper1 is GPL-3.0); **each voice has its own licence** | Many | No |

Notes:
- Chatterbox adds an inaudible watermark to what it says (Resemble AI's "Perth" watermark). That's by design.
- Voice models download from Hugging Face the first time Voicebox uses each one. Kokoro comes with Orion's voice
  add-on.
- Speeds are rough: a recent 8-core CPU, or an NVIDIA card with 8 GB or more.

## What to pick

- **Most computers, laptops, no graphics card**: keep **Kokoro**. It's quick, clear and needs nothing else.
  Try the samples; "Nicole and Heart" is Orion's usual voice, "Michael" and "Adam" are deeper. Add a little warmth
  if it sounds sharp.
- **A PC with an NVIDIA graphics card (8 GB or more)**: install Voicebox and use **Qwen3-TTS 1.7B**: pick a Qwen
  preset (CustomVoice) and write a delivery note, or describe a voice and let VoiceDesign make it. For fast, lively
  English, **Chatterbox Turbo** is a great second choice.
- **Avoid** OmniVoice for anything commercial, and check TADA's and LuxTTS's terms first.

If Voicebox is closed or not installed, Orion simply speaks with Kokoro and says so in plain words ("Voicebox isn't
running, so Orion used its built-in voice").

## Setting it up

### Kokoro (built in)
Nothing to do once voice is installed (OrionOS first-run "Voice", or Settings > Set up my voice). On Windows it
comes with Orion's requirements.

### Voicebox
- **OrionOS**: answer yes to "Install Voicebox now?" at the end of "Choose Orion's voice" (or run
  `orion-voice-setup` again). This runs the `voicebox` install recipe: Voicebox has no Linux download yet, so it
  follows Voicebox's own build-from-source steps: a copy of the code from GitHub, Python 3.12 packages from PyPI
  (PyTorch with NVIDIA support when there's an NVIDIA card), then starts only Voicebox's server as your user, on
  `127.0.0.1:17493`, as the `orion-voicebox` service. About 6 GB plus the voice models you use. Log:
  `/var/log/orion-recipes/voicebox.log`.
- **Windows**: download the Voicebox installer from its GitHub releases page
  (`github.com/jamiepine/voicebox/releases`), install it and open it once. It serves the same local address.
- Then open "Orion's voice" again: Voicebox shows as available, with its voices and Qwen presets.

### Piper (optional, tiny)
If `piper` is on the computer and voice files (`*.onnx`) are in `~/.orion/models/piper/`, Piper appears as a third
choice. Orion doesn't install it.

## For developers

- Engine layer: `apps/daemon/voice_engines.py` (settings in `<data dir>/voice/settings.json`: engine, voice, speed,
  warmth, delivery, chosen). Orion's normal speaking path and the phone's voice go through it; with Kokoro and no
  warmth it calls Kokoro exactly as before.
- Routes (daemon key needed): `GET /voice/engines`, `POST /voice/set`, `POST /voice/preview {text, out: "hud"|"wav"}`,
  `POST /voice/design {description}`, `POST /voice/hear` (a few seconds of audio, kept in memory only).
- Voicebox calls used: `GET /health`, `GET /profiles`, `GET /profiles/presets/{engine}`, `POST /profiles`
  (`voice_type` preset or designed) and `POST /v1/audio/speech` (returns a WAV).
- Change the Voicebox address with `ORION_VOICEBOX_URL` (it must still be 127.0.0.1, localhost or ::1).
- Warmth is Orion's own gentle softening of the top end, so it works with every engine. There's no pitch control:
  none of these engines offers it cleanly.
