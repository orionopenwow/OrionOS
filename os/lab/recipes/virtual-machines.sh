# Virtual machines on OrionOS with KVM, which is built into Linux: QEMU, libvirt, virt-install and virt-manager.
# Used by orion-boardroom-vm (the boardroom sign-off VM, managed by orion-admin) and for your own VMs
# (virt-manager as yourself uses "QEMU User session" VMs, no admin needed). Start it with:
# orion-ctl install virtual-machines. Safe to re-run.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
  virtinst virt-manager genisoimage dnsmasq-base osinfo-db ca-certificates curl
systemctl enable --now libvirtd >/dev/null 2>&1 || true
# The admin account manages the system VMs (the boardroom). The everyday user "orion", where Orion's agents run,
# deliberately is NOT in the libvirt group: it can't reach the boardroom's disk or memory.
id orion-admin >/dev/null 2>&1 && usermod -aG libvirt,kvm orion-admin || true
# "kvm" only lets the everyday user run its own fast VMs; it gives no access to the system VMs.
id orion >/dev/null 2>&1 && usermod -aG kvm orion || true
[ -e /dev/kvm ] && echo "KVM is ready." || echo "Note: /dev/kvm is missing. Turn on virtualisation (VT-x / AMD-V / SVM) in the firmware settings."
