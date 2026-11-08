# Visual Studio Code from Microsoft's APT repository.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q gpg wget apt-transport-https
# Same paths as Microsoft's own instructions, so the package's post-install script doesn't add a conflicting copy.
rm -f /etc/apt/sources.list.d/vscode.list
wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes -o /usr/share/keyrings/microsoft.gpg
cat > /etc/apt/sources.list.d/vscode.sources <<'SRC'
Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: amd64,arm64,armhf
Signed-By: /usr/share/keyrings/microsoft.gpg
SRC
apt-get update -q
apt-get install -y -q code
