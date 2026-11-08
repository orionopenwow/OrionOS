package com.orion.mobile

import android.app.Activity
import android.app.PendingIntent
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.Toast

/*
 * Quick Settings tiles (pull down twice, tap the pencil, drag the Orion tiles in):
 *   Freeze     shows whether Orion is frozen. Freezing is one tap (like saying "Orion, freeze");
 *              unfreezing needs your fingerprint, exactly like the Unfreeze button in the app.
 *   Mic        turns your PC's hot mic on or off (POST /phone/hotmic). The PC shows a notice each time.
 *   Approvals  opens the Approvals pane. Approving still needs your fingerprint there.
 *   Scout      sends the link on your clipboard to Video Scout on the PC (POST /phone/scout).
 * Tiles can't show a fingerprint prompt or read the clipboard themselves, so those two open TileActionActivity,
 * a tiny see-through screen that does it and closes.
 */

private val tileUi = Handler(Looper.getMainLooper())

private fun TileService.paint(state: Int, label: String, sub: String?) {
    val t = qsTile ?: return
    t.state = state
    t.label = label
    if (Build.VERSION.SDK_INT >= 29) t.subtitle = sub
    t.updateTile()
}

/** startActivityAndCollapse: Android 14+ (targetSdk 34) only accepts a PendingIntent; older ones take the Intent. */
internal fun TileService.openAndCollapse(intent: Intent) {
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    if (Build.VERSION.SDK_INT >= 34) {
        val pi = PendingIntent.getActivity(this, intent.component?.hashCode() ?: 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        try {
            TileService::class.java.getMethod("startActivityAndCollapse", PendingIntent::class.java).invoke(this, pi)
            return
        } catch (_: Exception) { }
    }
    @Suppress("DEPRECATION")
    startActivityAndCollapse(intent)
}

private fun Context.toast(s: String) = tileUi.post { Toast.makeText(this, s, Toast.LENGTH_LONG).show() }

// ===================================================================== Freeze
class FreezeTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        val lab = Lab(this)
        if (!lab.paired) { paint(Tile.STATE_UNAVAILABLE, "Freeze", "Not paired"); return }
        val cached = prefs().getBoolean(KEY, false)
        paint(if (cached) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE, if (cached) "Frozen" else "Freeze", "Checking")
        Thread {
            val frozen = try { SystemApi(this).frozen() } catch (e: Exception) { null }
            tileUi.post {
                if (frozen == null) paint(if (cached) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE,
                    if (cached) "Frozen" else "Freeze", "PC not reachable")
                else { prefs().edit().putBoolean(KEY, frozen).apply(); show(frozen) }
            }
        }.start()
    }

    private fun show(frozen: Boolean) =
        paint(if (frozen) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE, if (frozen) "Frozen" else "Freeze",
            if (frozen) "Tap to unfreeze" else "Running")

    override fun onClick() {
        super.onClick()
        if (!Lab(this).paired) { toast("Pair this phone with your PC first."); return }
        if (prefs().getBoolean(KEY, false)) {
            // Unfreezing is the approval: fingerprint, on the see-through screen.
            openAndCollapse(Intent(this, TileActionActivity::class.java).putExtra(TileActionActivity.EXTRA_DO, "unfreeze"))
            return
        }
        paint(Tile.STATE_ACTIVE, "Freezing", null)
        Thread {
            try {
                // Same as saying "Orion, freeze" in the chat: freezing is always allowed without the fingerprint.
                val r = Lab(this).chat("freeze", false)
                prefs().edit().putBoolean(KEY, true).apply()
                tileUi.post { show(true) }
                toast(r.optString("reply").ifEmpty { "Frozen." })
            } catch (e: Exception) {
                tileUi.post { show(false) }
                toast(e.message ?: "Couldn't freeze.")
            }
        }.start()
    }

    private fun prefs() = getSharedPreferences("orion_system", Context.MODE_PRIVATE)
    companion object { const val KEY = "tile_frozen" }
}

// ===================================================================== PC hot mic
class MicTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        if (!Lab(this).paired) { paint(Tile.STATE_UNAVAILABLE, "PC mic", "Not paired"); return }
        show(prefs().getBoolean(KEY, false), null)
        Thread {
            val on = try { SystemApi(this).hotMicState().let { if (it.has("listening")) it.optBoolean("listening") else null } }
                     catch (e: Exception) { null }
            if (on != null) tileUi.post { prefs().edit().putBoolean(KEY, on).apply(); show(on, null) }
        }.start()
    }

    private fun show(on: Boolean, sub: String?) =
        paint(if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE, "PC mic", sub ?: if (on) "Listening" else "Off")

    override fun onClick() {
        super.onClick()
        if (!Lab(this).paired) { toast("Pair this phone with your PC first."); return }
        val want = !prefs().getBoolean(KEY, false)
        if (want) {  // listening on the PC is sensitive: turning it on needs your fingerprint
            openAndCollapse(Intent(this, TileActionActivity::class.java).putExtra(TileActionActivity.EXTRA_DO, "micon"))
            return
        }
        show(want, "Turning off")
        Thread {
            try {
                val st = SystemApi(this).hotMic(want)
                val now = if (st.has("listening")) st.optBoolean("listening") else want
                prefs().edit().putBoolean(KEY, now).apply()
                tileUi.post { show(now, null) }
            } catch (e: Exception) {
                tileUi.post { show(!want, "Failed") }
                toast(e.message ?: "Couldn't reach your PC.")
            }
        }.start()
    }

    private fun prefs() = getSharedPreferences("orion_system", Context.MODE_PRIVATE)
    companion object { const val KEY = "tile_hotmic" }
}

// ===================================================================== Approvals
class ApprovalsTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        paint(if (Lab(this).paired) Tile.STATE_INACTIVE else Tile.STATE_UNAVAILABLE, "Approvals", null)
    }

    override fun onClick() {
        super.onClick()
        openAndCollapse(Intent(this, MainActivity::class.java).putExtra(Alerts.EXTRA_KIND, "approval"))
    }
}

// ===================================================================== Scout this link
class ScoutTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        paint(if (Lab(this).paired) Tile.STATE_INACTIVE else Tile.STATE_UNAVAILABLE, "Scout link", null)
    }

    override fun onClick() {
        super.onClick()
        openAndCollapse(Intent(this, TileActionActivity::class.java).putExtra(TileActionActivity.EXTRA_DO, "scout"))
    }
}

/**
 * See-through helper for the tiles. "scout": Android 10+ only lets an app read the clipboard while one of its
 * screens has focus, so we wait for focus, read it, send it and close. "unfreeze": the fingerprint prompt.
 */
class TileActionActivity : Activity() {
    private var done = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        when (intent.getStringExtra(EXTRA_DO)) { "unfreeze" -> unfreeze(); "micon" -> micOn() }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && !done && intent.getStringExtra(EXTRA_DO) == "scout") { done = true; scout() }
    }

    private fun scout() {
        val cm = getSystemService(ClipboardManager::class.java)
        val text = try { cm?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString() } catch (e: Exception) { null }
        val link = text?.let { Regex("https?://\\S+").find(it)?.value }
        if (link == null) { Toast.makeText(this, "Copy a video link first, then tap Scout.", Toast.LENGTH_LONG).show(); finish(); return }
        val app = applicationContext
        Thread {
            try {
                val r = PocketFallback.scout(app, Lab(app), link)   // PC off: Orion on this phone
                app.toast(PocketFallback.shown(r).ifEmpty { "Scouting it on your PC." })
            } catch (e: Exception) { app.toast(e.message ?: "Couldn't reach your PC.") }
        }.start()
        finish()
    }

    private fun unfreeze() {
        val sig = try { Keys.signatureForApproval() } catch (e: Exception) {
            Toast.makeText(this, "Open Orion to unfreeze: the approval key needs attention.", Toast.LENGTH_LONG).show()
            finish(); return
        }
        val prompt = BiometricPrompt.Builder(this)
            .setTitle("Unfreeze Orion").setSubtitle("Kill switch")
            .setDescription("Lets updates, self-changes and agents run again.")
            .setAllowedAuthenticators(BIOMETRIC_STRONG)
            .setNegativeButton("Cancel", mainExecutor) { _, _ -> finish() }
            .build()
        prompt.authenticate(BiometricPrompt.CryptoObject(sig), CancellationSignal(), mainExecutor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    val unlocked = result.cryptoObject?.signature ?: run { finish(); return }
                    val app = applicationContext
                    Thread {
                        try {
                            val st = Lab(app).setFrozen(false, unlocked)
                            app.getSharedPreferences("orion_system", Context.MODE_PRIVATE).edit()
                                .putBoolean(FreezeTile.KEY, st.optBoolean("frozen")).apply()
                            app.toast(if (st.optBoolean("frozen")) "Still frozen." else "Orion is running again.")
                            TileService.requestListeningState(app, android.content.ComponentName(app, FreezeTile::class.java))
                        } catch (e: Exception) { app.toast(e.message ?: "Couldn't unfreeze.") }
                    }.start()
                    finish()
                }
                override fun onAuthenticationError(code: Int, msg: CharSequence) { finish() }
            })
    }

    private fun micOn() {
        val sig = try { Keys.signatureForApproval() } catch (e: Exception) {
            Toast.makeText(this, "Open Orion to turn the PC mic on: the approval key needs attention.", Toast.LENGTH_LONG).show()
            finish(); return
        }
        val prompt = BiometricPrompt.Builder(this)
            .setTitle("Turn on your PC's mic").setSubtitle("Hot mic")
            .setDescription("Your PC starts listening for \"Orion\". It shows a light while it listens.")
            .setAllowedAuthenticators(BIOMETRIC_STRONG)
            .setNegativeButton("Cancel", mainExecutor) { _, _ -> finish() }
            .build()
        prompt.authenticate(BiometricPrompt.CryptoObject(sig), CancellationSignal(), mainExecutor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    val unlocked = result.cryptoObject?.signature ?: run { finish(); return }
                    val app = applicationContext
                    Thread {
                        try {
                            val st = Lab(app).hotMicOn(unlocked)
                            val on = st.optBoolean("listening", true)
                            app.getSharedPreferences("orion_system", Context.MODE_PRIVATE).edit()
                                .putBoolean(MicTile.KEY, on).apply()
                            app.toast(if (on) "Your PC is listening." else "Your PC's mic stayed off.")
                            TileService.requestListeningState(app, android.content.ComponentName(app, MicTile::class.java))
                        } catch (e: Exception) { app.toast(e.message ?: "Couldn't reach your PC.") }
                    }.start()
                    finish()
                }
                override fun onAuthenticationError(code: Int, msg: CharSequence) { finish() }
            })
    }

    companion object { const val EXTRA_DO = "orion_tile_do" }
}
