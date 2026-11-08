# Orion Home (the launcher)

Orion Mobile now includes a home screen and a live wallpaper with Orion's living black hole. Both are optional.
The Orion app icon still opens the chat screen as before.

## Set Orion as your Home screen

1. Install the app as usual (`build-apk.ps1`, or Android Studio).
2. Press the Home button. If Android asks which Home app to use, pick **Orion Home** and tap **Always**.
   If it doesn't ask: open **Settings**, then **Apps**, then **Default apps**, then **Home app**, and pick
   **Orion Home**. Some phones put this under **Settings**, **Apps**, **Choose default apps**.

On the home screen:

| Do this | What happens |
|---|---|
| Swipe up | The glass app list. Type to search. Long-press an app for **App info** or **Pin to taskbar**. |
| Swipe down | Approvals waiting on your PC. Tap one to open it in the chat screen. Approving still needs your fingerprint there. |
| Swipe with two fingers | The agents working on your PC. |
| Hold the black hole | Talk to Orion. The mic is on only while you hold, and a red "Listening" label shows it. Let go to send. |
| Tap a star | Opens that approval in the chat screen. |
| Tap a robot | What that agent is doing, with **Open on PC**, **Pause** and **Stop**. Your PC refuses to stop an agent it didn't start, and the phone shows that answer as is. |
| Long-press empty space | **Set as wallpaper**, **Change Home app**, **Open Orion chat**. |
| Tap the PC line or the Running/Frozen chip | Opens the chat screen (Freeze and Unfreeze need your fingerprint there). |

The black hole moves gently at up to 30 frames a second and stops completely when the screen is off or another
app is in front. It turns into a still picture when Battery Saver is on, or when **Remove animations** is on
(Settings, Accessibility). Phones without OpenGL ES 3.0 always show the still picture. Every 10 seconds, while
the home screen is showing, it asks your PC how things are, and the black hole reacts. When Orion is thinking it
spins a little faster and warmer. When you talk, the ring follows your voice. When a message arrives, a small
comet falls in.

**On a big screen** (an external monitor in Android's desktop mode, or a tablet) the same home screen lays out
as the Orion glass desktop. The black hole sits in the middle, chat is on the left, approvals and agents are on
the right, and there is a taskbar of your pinned apps at the bottom left.

### Undo it

Open **Settings**, then **Apps**, then **Default apps**, then **Home app**, and pick your old launcher (for
example "Pixel Launcher" or "One UI Home"). You can also long-press empty space on the Orion home screen and tap
**Change Home app**. Uninstalling Orion Mobile also puts your old Home screen back.

## Set the black hole as your wallpaper

1. Long-press empty space on the Orion home screen and tap **Set as wallpaper**. If you don't use Orion Home,
   open your phone's wallpaper settings, choose **Live wallpapers**, and pick **Orion black hole**.
2. Android shows a preview. Tap **Set wallpaper**. Choose **Home and lock screens** to have it on the lock
   screen too, if your phone offers that choice. Some phones only allow live wallpapers on the home screen.

The wallpaper follows the same rules as the home screen. It draws nothing while hidden, shows a still picture
on Battery Saver, and asks your PC how things are every 30 seconds while it is showing.

### Undo it

Open your phone's wallpaper settings and pick any other wallpaper.

## For developers

- Code: `HomeActivity.kt` (home screen and desktop mode), `BlackHoleRenderer.kt` (the shader host and mood),
  `BlackHoleView.kt` (GLSurfaceView), `BlackHoleWallpaper.kt` (live wallpaper, its own EGL14 context),
  `OrbitsView.kt`, `AppDrawer.kt`, `Glass.kt`, `Talk.kt`, and `PairApi.kt` (network calls, signed by `Lab.kt`).
- The shader is `assets/blackhole.frag`, an unchanged copy of `os/theme/blackhole/blackhole.frag`. After editing
  the original, copy it again: `cp os/theme/blackhole/blackhole.frag apps/mobile/app/src/main/assets/`.
  The painting is `res/drawable-nodpi/black_hole_core.png`, a copy of `apps/desktop/public/black-hole-core.png`.
- Fonts (SIL Open Font License 1.1) are copied from `os/theme/fonts` into `res/font`. Android allows only fonts
  in that folder, so their licences are in `assets/licences/`.
- PC routes used: `GET /phone/home` (read key), `POST /phone/agent {"id","action":"open"|"pause"|"stop"}`
  (read key; the PC answers `{"message"}`, or an `{"error"}` that the phone shows as is), plus the existing
  `/phone/voice` and `/phone/chat`. If the PC doesn't have `/phone/home` yet, the home screen falls back to
  `/phone/pending` and `/phone/health`.
