# Testing Orion: the Test Center

One window walks you through everything worth checking on your PC and your phone. Most of it Orion checks by
itself. For the rest, it tells you exactly what to tap or say, one step at a time.

## On Windows

1. Double-click **Start Here.cmd** in the Orion folder. It walks you through everything below, one step at a time,
   and does the commands for you. Enter always picks the recommended answer (the capital letter in [Y/n]).
   - **Update**: puts Claude's latest update in place (it checks and backs up first).
   - **Save to GitHub** (only if you say yes): your work goes on its own branch, never main.
   - **Tools**: checks Node.js, Python and Rust, offers to install Claude Code and asks you to sign in to it once.
   - **Start Orion**: starts Orion and the Test Center, and waits until Orion answers (the very first start can
     take several minutes).
   - **Phone app**: builds Orion Mobile. If Java 17 or the Android SDK is missing it offers to set them up (it says
     how big they are first, and Android's licences are yours to read and accept). Then it installs the app over a
     USB cable, or shows a QR code your phone scans to download it over your Wi-Fi (for 15 minutes).
   - **Pair**: opens the Phone panel in the Orion window and waits while you pair your phone.
   - **Phone checks**: on your phone, Menu > Test. The wizard follows along.
   - **The rest**: shows the Test Center's Next card live until you press Enter, then copies the report so you can
     paste it into your chat with Claude.
   - **OrionOS VM** (optional): offers to start Try OrionOS VM.cmd.

   Type **Q** at any question to stop. Run it again and it carries on where you stopped; finished steps are ticked
   and skipped. Type **R** at the first question to start over. It never approves, sends, merges or pushes anything
   for you (except Save to GitHub when you say yes).

### Doing it by hand

1. Double-click **Test Orion.cmd** in the Orion folder. It starts Orion if it isn't running (the very first start
   can take several minutes) and opens the **Test Center** in the Orion window.
2. The automatic checks start by themselves. If they don't, press **Run all automatic checks**.
3. Follow the **Next** card. It says what to do in one sentence and has one big button:
   **Run**, **Done**, **It didn't work** or **Skip**.
4. Keep Orion Mobile open on your phone. Phone steps show up there too (Test Center in the app), and anything you
   do on the phone ticks itself off on the PC.
5. When the card says "All checked", press **Copy report** and paste it wherever you like. The report has no keys,
   no names and nothing personal in it.

## On OrionOS

1. Press **Super+T** (or open **Test Orion** from the app menu, or type `orion-ctl test` in a terminal). The first-run
   setup also offers "Test everything now" on its last screen.
2. Then the same as steps 3 to 5 of "Doing it by hand" above. `orion-ctl test report` prints the report in the terminal.

- **Bring my stuff** (not in the Test Center yet; check it by hand once): make a kit on Windows with
  `Move my stuff to Orion.cmd` (choose "just the move kit"), then on OrionOS run `orion-move-in bring`. You should
  see one summary, one question, then "[1/6] ... [6/6]" progress; Firefox shows your bookmarks, asks to turn on
  your extensions, and `orion-move-in userscripts` lists your Tampermonkey scripts with Install links. Press
  Ctrl+C during apps and run it again: it carries on. **Games:** Settings > Games lists games on the Windows drive.

## Any time

- Say or type **"Orion, test everything"**.
- The main menu opens it too.
- **Start over** at the bottom of the window clears every result.

## What the groups check

Start-up, Look, Voice, Phone, Agents, Projects, Skills, Safety, Self-change, and (only where they apply) OrionOS
and Windows items. The practice mission uses a stand-in agent in a throwaway folder that is deleted afterwards.
The test card for your fingerprint does nothing when you approve it. The Test Center never approves anything for
you, never sends a message and never goes on the internet.

## Only real hardware can check these

A virtual machine or a quick look on the PC can't prove these. Do them on the real computer and the real phone:

- Starting the computer from the **USB stick**, and opening your **vault** with your passphrase.
- **Sleep and lock**: closing the lid, and the password when you open it.
- **Restart into Windows** and back.
- **Wi-Fi** on the laptop itself.
- The **microphone and speakers** (Orion speaks; you say whether you heard it).
- The **fingerprint** on your phone, and **Pocket Orion** on the phone when the PC is off.
- How smooth the **black hole** looks on your graphics card.
