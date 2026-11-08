# Voice for Orion: phone voice chat, the hot mic (with your voice profile) and Orion's own voice.
# Installs into Orion's Python at /opt/orion/venv. CPU-only PyTorch keeps it near 1.5 GB instead of several.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q --no-install-recommends libportaudio2 espeak-ng ffmpeg
V=/opt/orion/venv/bin/pip
$V install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu
$V install -q "faster-whisper>=1.0.0" "kokoro>=0.8.4" "speechbrain>=1.0" "sounddevice>=0.5.0" "webrtcvad>=2.0.10" soundfile
/opt/orion/venv/bin/python -c "import faster_whisper, speechbrain, sounddevice, webrtcvad, kokoro; print('voice ok')"
# [v1gaps] wake word "Orion" (off until turned on; needs a custom orion.onnx in ~/.orion/models/wake): optional
$V install -q "openwakeword>=0.6" || echo "openWakeWord didn't install; the hot mic keeps the Whisper wake check"
chmod -R a+rX /opt/orion/venv
