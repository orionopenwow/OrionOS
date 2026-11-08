# Orion's brain on this computer: Ollama, plus the biggest model that runs comfortably in this much memory.
set -eu
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
systemctl enable --now ollama
RAM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
if [ "$RAM_GB" -ge 30 ]; then MODEL=qwen2.5:14b
elif [ "$RAM_GB" -ge 14 ]; then MODEL=llama3.1:8b
else MODEL=llama3.2:3b; fi
for i in 1 2 3 4 5; do ollama list >/dev/null 2>&1 && break; sleep 2; done
ollama pull "$MODEL"
sed -i "s#^OLLAMA_URL=.*#OLLAMA_URL=http://127.0.0.1:11434#" /etc/orion/daemon.env
grep -q '^OLLAMA_MODEL=' /etc/orion/daemon.env && sed -i "s#^OLLAMA_MODEL=.*#OLLAMA_MODEL=$MODEL#" /etc/orion/daemon.env \
  || printf 'OLLAMA_MODEL=%s\n' "$MODEL" >> /etc/orion/daemon.env
echo "Orion's brain: $MODEL"
