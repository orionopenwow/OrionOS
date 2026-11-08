#!/usr/bin/env bash
# Boot an OrionOS image in QEMU the way a PC boots a USB stick: UEFI with Secure Boot ON (Microsoft keys), the image
# as a removable USB drive with extra free space for the vault. Logs the kernel console to serial.log and can
# take screenshots.
#
#   os/image/test-boot.sh start <image.img> [workdir]     boots it in the background
#   os/image/test-boot.sh shot <name> [workdir]           screenshot -> <workdir>/<name>.png
#   os/image/test-boot.sh keys "<keys>" [workdir]         type (e.g. "ret", "down ret", or text via "type:hello")
#   os/image/test-boot.sh stop [workdir]
set -euo pipefail
CMD="$1"; shift
W="${2:-${ORION_TEST_DIR:-/var/tmp/orion-test}}"
mon() { python3 - "$W/mon.sock" "$1" <<'PY'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.2); s.recv(4096)
s.sendall((sys.argv[2] + "\n").encode()); time.sleep(0.5); s.close()
PY
}
case "$CMD" in
  start)
    IMG="$1"; mkdir -p "$W"; rm -f "$W/serial.log"
    cp --sparse=always "$IMG" "$W/stick.img"
    # kernel messages to serial for the test (the real stick stays quiet)
    sed 's/ quiet splash/ console=tty0 console=ttyS0,115200 systemd.debug-shell=1 orion.hud_nojit splash/' "$(dirname "$0")/grub.cfg" > "$W/grub.cfg"
    MTOOLS_SKIP_CHECK=1 mcopy -o -i "$W/stick.img@@1M" "$W/grub.cfg" ::/EFI/BOOT/grub.cfg
    MTOOLS_SKIP_CHECK=1 mcopy -o -i "$W/stick.img@@1M" "$W/grub.cfg" ::/EFI/ubuntu/grub.cfg
    truncate -s "${ORION_TEST_STICK_GB:-16}G" "$W/stick.img"     # a 16 GB stick: room for the vault
    cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd "$W/vars.fd"
    [ -f "$W/pc.img" ] || truncate -s 40G "$W/pc.img"            # the "PC's" internal drive, for install tests
    nohup qemu-system-x86_64 -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
      -drive if=pflash,format=raw,unit=0,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd,readonly=on \
      -drive if=pflash,format=raw,unit=1,file="$W/vars.fd" \
      -m "${ORION_TEST_RAM_MB:-3072}" -smp 2 -cpu max \
      -device qemu-xhci -drive file="$W/stick.img",format=raw,if=none,id=stick -device usb-storage,drive=stick,removable=on \
      -drive file="$W/pc.img",format=raw,if=virtio \
      ${ORION_TEST_EXTRA_DISK:+-drive file=$ORION_TEST_EXTRA_DISK,format=raw,if=virtio,readonly=on} \
      -nic user,model=virtio-net-pci -vga std -display none \
      -serial file:"$W/serial.log" -monitor unix:"$W/mon.sock",server,nowait \
      -pidfile "$W/qemu.pid" > "$W/qemu.out" 2>&1 &
    sleep 2; echo "booting; serial: $W/serial.log" ;;
  shot) mon "screendump $W/$1.ppm"; sleep 1; python3 -c "from PIL import Image; Image.open('$W/$1.ppm').save('$W/$1.png')"; echo "$W/$1.png" ;;
  keys)
    for k in $1; do
      case "$k" in type:*) t="${k#type:}"; for ((i=0;i<${#t};i++)); do c="${t:$i:1}"; case "$c" in [A-Z]) mon "sendkey shift-${c,,}";; " ") mon "sendkey spc";; "-") mon "sendkey minus";; ".") mon "sendkey dot";; *) mon "sendkey $c";; esac; done ;;
        *) mon "sendkey $k" ;; esac
    done ;;
  type)  # type a whole line (letters, digits, spaces and common shell characters), then Enter
    python3 - "$W/mon.sock" "$1" <<'PY'
import socket, sys, time
m = {" ": "spc", "-": "minus", ".": "dot", "/": "slash", "|": "shift-backslash", ">": "shift-dot", "<": "shift-comma",
     "_": "shift-minus", "=": "equal", ":": "shift-semicolon", ";": "semicolon", "'": "apostrophe", '"': "shift-apostrophe",
     ",": "comma", "*": "shift-8", "&": "shift-7", "$": "shift-4", "~": "shift-grave_accent", "@": "shift-2", "+": "shift-equal",
     "(": "shift-9", ")": "shift-0", "!": "shift-1", "\n": "ret"}
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.2); s.recv(4096)
for c in sys.argv[2] + "\n":
    k = m.get(c) or (("shift-" + c.lower()) if c.isupper() else c)
    s.sendall(("sendkey " + k + "\n").encode()); time.sleep(0.06)
time.sleep(0.3); s.close()
PY
    ;;
  stop) [ -f "$W/qemu.pid" ] && kill "$(cat "$W/qemu.pid")" 2>/dev/null || true; echo stopped ;;
esac
