# Firefox as a normal .deb from Mozilla's own APT repository (packages.mozilla.org), never Ubuntu's snap stub.
# If Mozilla's repository can't be reached, falls back to the Mozilla Team PPA (same .deb build, same pinning).
# Used two ways: by os/image/os-apps.sh while OrionOS is built/provisioned, and as the root install recipe
# "firefox" (repair / reinstall). POSIX sh (the recipe runner uses sh). Safe to re-run.
#   ORION_FIREFOX_SOURCE=auto|mozilla|ppa   (default auto: Mozilla first, then the PPA)
set -eu
export DEBIAN_FRONTEND=noninteractive
SOURCE="${ORION_FIREFOX_SOURCE:-auto}"
KEYS=/etc/apt/keyrings
# Mozilla's APT signing key, as published in https://support.mozilla.org/kb/install-firefox-linux
MOZ_FPR=35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3
# "Launchpad PPA for Mozilla Team" signing key (ppa:mozillateam/ppa)
PPA_FPR=0AB215679C571D1C8325275B9BDB3D89CE49EC21
CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-noble}")"
say() { printf '[firefox] %s\n' "$*"; }

if ! command -v curl >/dev/null || ! command -v gpg >/dev/null; then
  apt-get update -q >/dev/null || true
  apt-get install -y -q --no-install-recommends ca-certificates curl gpg >/dev/null
fi
install -d -m 0755 "$KEYS"
GNUPGHOME="$(mktemp -d)"; export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT

# Ubuntu's own "firefox" is only a stub that installs the snap: never let it in (Mozilla's instructions, + its locales).
cat > /etc/apt/preferences.d/orion-no-firefox-snap <<'EOF'
# OrionOS: Firefox comes from Mozilla's .deb, never from Ubuntu's snap transitional package.
Package: firefox*
Pin: release o=Ubuntu
Pin-Priority: -1
EOF

# key_ok FILE FINGERPRINT: the file holds exactly one primary key, with that fingerprint.
key_ok() {
  got="$(gpg --batch --show-keys --with-colons "$1" 2>/dev/null | awk -F: '$1=="pub"{p=1;next} p&&$1=="fpr"{print $10;p=0}')"
  [ "$got" = "$2" ]
}
# fetch_key URL FILE FINGERPRINT: download (unless a verified copy is already there) and check the fingerprint.
fetch_key() {
  if [ -s "$2" ] && key_ok "$2" "$3"; then return 0; fi
  tmp="$(mktemp)"
  if ! curl -fsSL --retry 2 "$1" -o "$tmp"; then rm -f "$tmp"; say "could not download the key from $1"; return 1; fi
  if ! key_ok "$tmp" "$3"; then rm -f "$tmp"; say "!! key from $1 does NOT have fingerprint $3; refusing it"; return 1; fi
  install -m 0644 "$tmp" "$2"; rm -f "$tmp"
}
# refresh LISTFILE: update just that one source (a dead source must not break the rest of apt).
refresh() {
  apt-get update -q -o Dir::Etc::sourcelist="sources.list.d/$1" -o Dir::Etc::sourceparts=- \
    -o APT::Get::List-Cleanup=0 >/dev/null 2>&1
}

use_mozilla() {
  fetch_key https://packages.mozilla.org/apt/repo-signing-key.gpg "$KEYS/packages.mozilla.org.asc" "$MOZ_FPR" || return 1
  echo "deb [signed-by=$KEYS/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
    > /etc/apt/sources.list.d/mozilla.list
  printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > /etc/apt/preferences.d/mozilla
  if refresh mozilla.list && apt-cache policy firefox | grep -q 'packages.mozilla.org'; then return 0; fi
  say "Mozilla's repository did not answer"
  rm -f /etc/apt/sources.list.d/mozilla.list /etc/apt/preferences.d/mozilla
  return 1
}

use_ppa() {
  fetch_key "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x$PPA_FPR" "$KEYS/mozillateam-ppa.asc" "$PPA_FPR" || return 1
  echo "deb [signed-by=$KEYS/mozillateam-ppa.asc] https://ppa.launchpadcontent.net/mozillateam/ppa/ubuntu $CODENAME main" \
    > /etc/apt/sources.list.d/mozillateam-ppa.list
  # Only Firefox comes from the PPA; everything else stays Ubuntu's.
  printf 'Package: firefox*\nPin: release o=LP-PPA-mozillateam\nPin-Priority: 1001\n' > /etc/apt/preferences.d/mozillateam-firefox
  if refresh mozillateam-ppa.list && apt-cache policy firefox | grep -q 'ppa.launchpadcontent.net/mozillateam'; then return 0; fi
  say "the Mozilla Team PPA did not answer"
  rm -f /etc/apt/sources.list.d/mozillateam-ppa.list /etc/apt/preferences.d/mozillateam-firefox
  return 1
}

case "$SOURCE" in
  mozilla) use_mozilla ;;
  ppa)     use_ppa ;;
  *)       use_mozilla || { say "falling back to the Mozilla Team PPA"; use_ppa; } ;;
esac || { say "!! no Firefox source could be reached; nothing installed"; exit 1; }

# Browser security fixes arrive with the automatic updates like Ubuntu's own (harden.sh turns those on).
cat > /etc/apt/apt.conf.d/52orion-firefox-updates <<'EOF'
// OrionOS: Firefox (Mozilla's repository or the Mozilla Team PPA) is kept up to date automatically.
Unattended-Upgrade::Origins-Pattern { "site=packages.mozilla.org"; "origin=LP-PPA-mozillateam"; };
EOF

apt-get install -y -q --no-install-recommends firefox
# The PPA build registers itself as the system browser; make sure Mozilla's does too.
if [ -x /usr/bin/firefox ] && ! update-alternatives --query x-www-browser 2>/dev/null | grep -q '/usr/bin/firefox'; then
  update-alternatives --install /usr/bin/x-www-browser x-www-browser /usr/bin/firefox 200 >/dev/null 2>&1 || true
fi
say "installed: $(dpkg-query -W -f='${Version}' firefox 2>/dev/null) from $(apt-cache policy firefox | awk '/\*\*\*/{getline; print $2; exit}')"
