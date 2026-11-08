# Developer basics: git, GitHub CLI, Node.js + npm (OpenWoW), build tools, Python venvs.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git gh curl jq unzip p7zip-full ripgrep build-essential cmake pkg-config nodejs npm python3-venv
