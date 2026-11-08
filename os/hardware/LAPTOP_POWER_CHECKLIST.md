# Laptop power: the real-laptop test

`docs/ORIONOS_COMPLETE_PLAN.md` Part 1, "Laptop power": sleep, hibernate, lid close and low-battery warnings,
tested on your laptop. None of this can be tested in the VM (no battery, no lid, no real sleep), so it is a
checklist for the first install on the laptop (and again after big updates). Tick each line; anything that fails,
write down what happened and run `journalctl -b -1 -p warning` after the next start.

Before you start: charge to at least 30%, have the charger nearby, plug in nothing else, and check
`orion-power status` shows the battery.

## Sleep and wake
- [ ] **Sleep from Settings.** Settings > Power > Sleep now. The screen goes dark within 5 seconds and the power
      light changes (blinks or turns off).
- [ ] **Wake with a key.** Press a key or the power button. The lock screen (or Orion) is back within 5 seconds.
- [ ] **Everything came back.** Open windows are where they were, sound plays (`wpctl status`), the mouse pad and
      keyboard work, the Orion window answers.
- [ ] **Wi-Fi after waking.** Within 15 seconds Wi-Fi is connected again by itself (Settings > Wi-Fi shows the
      network; a web page loads). If not: `nmcli radio wifi off; nmcli radio wifi on` and note the Wi-Fi card
      (`lspci -nnk | grep -A3 -i net`).
- [ ] **Bluetooth after waking.** A paired mouse or headphones reconnect (or reconnect with one click).
- [ ] **Sleep 10 times in a row** (close and open the lid every minute or so). Nothing freezes, the battery
      percentage keeps making sense.

## Lid
- [ ] **Lid closed on battery** sleeps (the choice in Settings > Power > "When I close the lid").
- [ ] **Lid closed while plugged in** does what "When I close the lid while plugged in" says.
- [ ] **Lid closed with a monitor plugged in:** the computer keeps running, the laptop panel turns off, everything
      moves to the monitor. Opening the lid brings the panel back (os/hardware/README.md).
- [ ] **Unplug the monitor with the lid closed:** the computer goes to sleep (no monitor left).

## Battery
- [ ] **Low-battery notes.** Unplug and use the laptop (or run `stress-ng --cpu 0` if it's installed) until it is
      low: a note "Battery low: 15%" appears once, then "Battery very low: 7%" (red) once.
- [ ] **Automatic sleep at 4%.** At 4% a note says it is going to sleep, and it sleeps. Plug in, wake it: it does
      not go straight back to sleep (you get 3 minutes), and nothing was lost.
- [ ] **Power mode follows the battery** (Settings > Power > Save power on battery, on by default). With
      Performance picked: unplug, and the mode changes to Balanced; below 20% it changes to Power saver; plug in,
      and Performance comes back. (`powerprofilesctl get` shows the mode.)
- [ ] **Percentage is right.** Settings > Power and `orion-power status` show the same number as the BIOS or
      Windows did on the same charge, within a few percent.
- [ ] **Charging shows.** Plug in: Settings > Power says "Charging"; full says "Full".

## Brightness and keys
- [ ] **Brightness keys** (Fn + sun keys) make the screen brighter and dimmer, all the way down without going
      black. If not, `brightnessctl -l` lists what the laptop offers; note it.
- [ ] **Volume and mute keys** change the sound; the mic-mute key toggles Orion's hot mic.
- [ ] **Keyboard backlight** (if it has one) still works after waking.

## Hibernate (installed laptops only, never the USB stick)
- [ ] Settings > Power > Battery care > "Sleep, then hibernate" says why it's on or off. On the USB stick it is off
      on purpose (your memory would be written outside the locked vault).
- [ ] If it can be turned on: turn it on, sleep, wait 2 hours (or test with `systemctl hibernate`). The laptop
      switches off; pressing power brings back everything as it was. If it starts fresh instead, the swap space
      isn't set up to wake from: turn the switch off again and note it.

## When something fails
Write down: the laptop model, what you did, what happened, and the time. Then
`journalctl -b -1 -u systemd-logind -u systemd-suspend -u NetworkManager --since "-30 min"` (after the next start)
usually shows why. Battery warnings: `orion-power once` shows what the watcher sees right now.
