# The starry login screen

Pure black with slowly drifting stars that shift with the mouse, the time, your name and picture, and one password
field. The password is checked first (by greetd); only then do the stars, clock and field get pulled into a
spiral, fall into a point, and the black hole is born before Orion starts. A wrong password gives a gentle shake
and "That's not it". Esc clears the field, Caps Lock is announced, everything works from the keyboard.

## Two ways in (and never a bare text prompt)

The login screen is what greetd always shows when nobody is logged in: after logging out, or if the session stops.
Under the clock it has two buttons:

- **Start Orion again**: the normal Orion session.
- **Rescue desktop**: plain Sway with a terminal, your files, a browser and the Rescue panel; no Orion screen
  (os/rescue/orion-rescue-session). For when the Orion screen itself is what's broken.

With a password set (`LOGIN_MODE=password`) the field is there and either button starts its session once the
password is right. While OrionOS starts without a password (`LOGIN_MODE=auto`: the VM lab, the USB stick, a fresh
install before first-run) there is no field; the buttons start straight away (`orion-login-auto-ok`, a PAM check
that only answers yes in that mode, for the login user, on greetd). The text login (agreety) is only used if the
graphical screen can't run. `greetd_config.py` writes `/etc/greetd/config.toml` for both modes.

## Turning it on

The VM lab and the USB stick start straight into Orion (the stick's vault passphrase already protects it). On a
computer OrionOS is installed on, first-run asks for a password and turns this on.

```
orion-ctl login password            # ask for my password (offers to set one if you have none)
orion-ctl login auto                # straight in again
orion-ctl login reduced-motion on   # a simple fade instead of the swirl
orion-ctl login status
```

or **Settings > Login screen**. Changing it needs the orion-admin password; it applies at the next start or log out.
The setting lives in `/etc/orion/login.conf`.

## How it fits together

| File | What |
|---|---|
| `orion-greeter-start` | what greetd runs (as `_greetd`): `cage -s -- orion-greeter`, or the text login (agreety) if cage/WebKit is missing or the screen stops without starting your session |
| `orion-greeter` | GTK4 + WebKitGTK 6 full-screen window showing `web/`; bridges the page to greetd. No network (ephemeral session, only its own page) |
| `greetd_ipc.py` | the greetd protocol (create_session, auth messages, start_session); the password is sent once, never logged |
| `web/` | the page: canvas stars, the swirl and the black hole, the field |
| `greeter_logic.py` | which session each button starts, what the page is told (tested without GTK) |
| `greetd_config.py` | writes greetd's config: the graphical screen always, plus the power-on auto-login in auto mode |
| `orion-login-auto-ok` | PAM helper: the buttons work without a password only while `LOGIN_MODE=auto` |
| `orion-login-mode` | root side of `orion-ctl login` (writes `/etc/greetd/config.toml` from `greetd-password.toml` or the lab's `greetd.toml`) |

`os/lab/provision.sh` installs it to `/opt/orion/greeter` with `cage`, `python3-gi`, `gir1.2-gtk-4.0` and
`gir1.2-webkit-6.0`. If anything goes wrong, Ctrl+Alt+F2 still has a text login.

Try it without greetd: open `web/index.html` in a browser (the password is `orion`), or `./orion-greeter --try`
on a desktop with WebKitGTK 6. Tests: `python -m pytest -q os/greeter/tests` and `python os/greeter/tests/shoot.py`
(screenshots in `screenshots/`).

## The black hole (Art Update)

At install time `os/image/art-boot.sh` wraps the shared shader (`os/theme/blackhole/blackhole.frag`) and the painted
disk into `web/shader.js` (`make-shader-js.py`; the page may not fetch files). The login screen then starts on
exactly the boot splash's last frame (the hole centred, "orion" beneath it, the splash's vignette), lets the wordmark
and vignette fade while the hole settles a little smaller so the clock (top) and the field (bottom) stay off the
disk, and after the right password pulls the clock, name and field into the hole, spins the disk up, flares the
ring and settles back to normal size before the session starts. Without `shader.js` or WebGL2 (VMs with software
rendering) it is the 2D sky described above. `python os/greeter/tests/shoot.py --hole <frag>` tests that mode.

## Hand-off to the HUD

The session is started with `ORION_LOGIN_HANDOFF=blackhole` in its environment (sway passes it on to the HUD). The
greeter's last frame is the black hole on black, centred, so the HUD can continue it: when the variable is set,
start on a black screen with the Singularity at the centre at about the greeter's size (8.5% of the shorter screen
side for the horizon) and ease it into its usual place, instead of the normal intro. Sway should also open on a
plain black background in that case (`output * bg #000000 solid_color` until the HUD is up) so the wallpaper
doesn't flash in between.
