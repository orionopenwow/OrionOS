# Voicebox for Orion's voice (optional): more natural voices, voices designed from a description, delivery styles.
# Voicebox is MIT-licensed (https://github.com/jamiepine/voicebox). It has no Linux download yet, so this follows
# its own build-from-source steps (docs/content/docs/developer/setup.mdx and its justfile `setup-python`): a git clone
# from GitHub, Python 3.12 packages from PyPI (PyTorch from download.pytorch.org, as voice.sh does), then only the
# Python server runs, as the session user, bound to 127.0.0.1:17493 (never the network).
# Notes: its requirements also pull a few packages straight from GitHub and one from k2-fsa.github.io (LuxTTS);
# voice models download from Hugging Face the first time each engine is used, inside Voicebox.
# Size: about 6 GB of packages, plus 0.35-8 GB per voice model. A graphics card (NVIDIA) makes it much faster.
set -eu
export DEBIAN_FRONTEND=noninteractive
U=orion
DIR=/opt/voicebox
apt-get install -y -q --no-install-recommends git python3.12-venv ffmpeg libsndfile1
if [ -d "$DIR/src/.git" ]; then
  git -C "$DIR/src" pull --ff-only -q
else
  install -d -m 755 "$DIR"
  git clone -q --depth 1 https://github.com/jamiepine/voicebox.git "$DIR/src"
fi
[ -x "$DIR/venv/bin/python" ] || python3.12 -m venv "$DIR/venv"
P="$DIR/venv/bin/python"
$P -m pip install -q --upgrade pip
if [ -e /proc/driver/nvidia/version ] || [ -d /sys/module/nvidia ]; then
  $P -m pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cu128
else
  $P -m pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu
fi
$P -m pip install -q -r "$DIR/src/backend/requirements.txt"
# the same --no-deps installs Voicebox's own setup does (their pins clash with Python 3.12)
$P -m pip install -q --no-deps chatterbox-tts
$P -m pip install -q --no-deps hume-tada || echo "TADA didn't install; the other engines still work"
$P -m pip install -q "git+https://github.com/QwenLM/Qwen3-TTS.git" || echo "Qwen3-TTS from GitHub didn't install; qwen-tts from PyPI stays"
install -d -m 755 -o "$U" -g "$U" "/home/$U/.local/share/voicebox"
cat > /etc/systemd/system/orion-voicebox.service <<EOF
[Unit]
Description=Voicebox voice studio for Orion (local only, 127.0.0.1:17493)
After=network-online.target

[Service]
User=$U
WorkingDirectory=$DIR/src
ExecStart=$P -m backend.main --host 127.0.0.1 --port 17493 --data-dir /home/$U/.local/share/voicebox
Restart=on-failure
Nice=5

[Install]
WantedBy=multi-user.target
EOF
chmod -R a+rX "$DIR"
systemctl daemon-reload
systemctl enable --now orion-voicebox.service
for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS -m 3 http://127.0.0.1:17493/health >/dev/null 2>&1 && break; sleep 3; done
echo "Voicebox is running on 127.0.0.1:17493"
