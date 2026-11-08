# Art Update boot chain + command line assets.  make -C os/theme/boot <target>
#   frames   the Plymouth frame sequence from the real shader (os/theme/blackhole/blackhole.frag), plus a contact
#            sheet (/tmp/orion-boot-sheet.png) and a 4K lock-screen still (os/theme/lock.png)
#   assets   vault-prompt stars/orbit and the GRUB theme pictures
#   sounds   the boot swell + chime (OGG)
#   cursor   the Orion cursor theme (xcursorgen)
#   logo     the ANSI black hole for `orion`
#   all      everything above
REPO := $(abspath ../../..)
.PHONY: all frames assets sounds cursor logo
all: assets sounds cursor logo frames
frames:
	node $(REPO)/os/theme/boot/render-frames.mjs --sheet /tmp/orion-boot-sheet.png --lock-still $(REPO)/os/theme/lock.png
assets:
	python3 $(REPO)/os/theme/boot/make-assets.py
sounds:
	python3 $(REPO)/os/theme/boot/make-sounds.py
cursor:
	python3 $(REPO)/os/theme/cursor/make-cursor.py
logo:
	python3 $(REPO)/os/theme/shell/make-logo.py >/dev/null
