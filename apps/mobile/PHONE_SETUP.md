# Orion phone system (Android Stage 1)

The parts of Orion that hook into Android itself: the assistant button, Quick Settings tiles, one inbox,
optional call screening, sending files to your PC, and a setup checklist. The home screen, black hole and app
drawer are separate (the launcher work). Everything here is plain Android, no extra libraries, and each part can
be switched off from the same Android page that switched it on.

Open it from Orion: **Settings (gear) > Phone setup and inbox**.

## Phone setup checklist (PhoneSetupActivity.kt)

One screen with each permission or role, whether it is on, and a button to the right Android page:

| Item | What it does | Where Android keeps it |
|---|---|---|
| Home screen | Orion as your home screen | Settings > Apps > Default apps > Home app |
| Digital assistant | Long-press power opens Orion | Settings > Apps > Default apps > Digital assistant app |
| Notification access | Fills the inbox | Settings > Notifications > Device & app notifications |
| Call screening | Optional, for unknown callers | Settings > Apps > Default apps > Caller ID & spam app |
| Live wallpaper | The black hole wallpaper (once the launcher update adds it) | Wallpaper picker |
| Microphone | Hold-to-talk and the assistant | App permissions |

The top of the screen shows a **PC status card** (name, online, CPU, battery, uptime) from `GET /phone/home`'s
`pc` field.

## Digital assistant (AssistService.kt)

1. Settings > Apps > Default apps > **Digital assistant app** > pick **Orion**.
2. On some phones also: Settings > System > Gestures > Press and hold power button > Digital assistant.
3. Long-press power (or the assist gesture). A glass sheet opens and says **Listening** with a pulsing dot.
   Tap **Done** to send, **Cancel** to throw it away. It stops by itself after 30 seconds.

The clip goes to your PC's `/phone/voice` (the same call and recording settings as hold-to-talk), and the sheet
shows what Orion heard and its reply, spoken aloud. Recording stops the moment the sheet goes away. It is not
offered on the lock screen. The sheet also opens for the `ACTION_ASSIST` and `ACTION_VOICE_COMMAND` intents
(assist button, headset button). Nothing said here can approve anything.

Android insists an assistant names a speech recognizer; Orion's (`AssistRecognitionService`) declines politely
because Orion's speech-to-text runs on your PC. Other apps keep using the phone's normal one.

## Quick Settings tiles (Tiles.kt)

Swipe down twice, tap the pencil, drag in the Orion tiles.

| Tile | Tap | PC route |
|---|---|---|
| Freeze | Not frozen: freezes at once (same as saying "Orion, freeze"). Frozen: asks for your fingerprint, then unfreezes. Shows the current state. | `/phone/chat` "freeze" (read key); `POST /phone/unfreeze` (fingerprint key); state from `/phone/health` |
| PC mic | Turns the PC's hot mic on or off. The PC shows a notice each time. | `POST /phone/hotmic {"state":"on"\|"off"}`, `GET /phone/hotmic` |
| Approvals | Opens the Approvals pane (approving still needs the fingerprint). | — |
| Scout link | Sends the link on your clipboard to Video Scout. | `POST /phone/scout` |

Android 10+ only lets an app read the clipboard while its screen has focus, and tiles can't show a fingerprint
prompt, so Scout and Unfreeze briefly open a see-through screen (`TileActionActivity`) that does it and closes.

## One inbox (InboxListener.kt, InboxActivity.kt)

- Turn on notification access for **Orion inbox** (Phone setup > Notification access).
- Orion keeps message notifications from the apps you pick. Defaults: Discord, Messenger, Telegram, WhatsApp,
  Signal, your default texting app, Google Messages, Instagram. Add others by package name in **Inbox settings**.
- Kept **on this phone only**, in one small file, for 7 days. **Clear the whole inbox** (or one chat) any time.
- Open a chat, tap **Draft reply**: your PC drafts it in your style (`POST /phone/draft`). Edit it in the box.
- **Send** goes out through the messaging app's own reply button, and only when you tap it. Orion never sends on
  its own. Send works while that message's notification is still on the phone; once it's gone, use **Open app**.
- **Mirror inbox to my PC** (off by default): new messages are also copied to your own PC (`POST /phone/inbox`,
  items `{"app","sender","text","ts" (seconds),"thread"}`). Nothing is sent to anyone.

## Call screening (CallScreen.kt) — optional, off by default

Phone setup > Unknown callers: **Off**, **Ring + notice** (it rings and Orion shows "Unknown caller"), or
**Silence to voicemail** (the ring is silenced and the call goes to voicemail; Orion tells you afterwards).
Needs the call screening role and contacts permission. Contacts always ring as normal; if Orion can't check your
contacts, every call rings as normal. Live call transcripts aren't included.

## Send to PC (PhoneToPc.kt)

Share a photo, video or file and pick **Send to PC**. If the free LocalSend app is installed, Orion hands the
files straight to it (Orion's PC side already receives LocalSend). If not, Orion explains how to get it.

## For developers

- New files only: `AssistService.kt`, `Tiles.kt`, `InboxListener.kt`, `InboxActivity.kt`, `CallScreen.kt`,
  `PhoneToPc.kt`, `PhoneSetupActivity.kt`, `SystemApi.kt` (read-key calls, same signing as `Lab.kt`),
  `SysUi.kt` (glass view helpers). Resources: `res/xml/orion_assist_service.xml`,
  `res/values/system_themes.xml` (`OrionSheet`), `res/drawable/ic_tile_*.xml`.
- Manifest entries sit between the `Phone system` markers. `MainActivity.kt` has one marked line (the settings
  button).
- Opening screens from other code: `Intent(ctx, InboxActivity::class.java)` (optional extra
  `InboxActivity.EXTRA_THREAD`), `PhoneSetupActivity`, `AssistActivity` (Orion listening),
  `SendToPcActivity`. `PcCard.build(activity)` returns the PC status card view.
- PC routes this needs beyond v0.2: `GET /phone/home`, `POST /phone/draft`, `POST /phone/inbox`,
  `POST /phone/hotmic {"state":"on|off"}` and `GET /phone/hotmic` (both answering the hot mic status, including
  `"listening": bool`). Missing routes show "update Orion on the PC" instead of failing.
