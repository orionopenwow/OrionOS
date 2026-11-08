package com.orion.mobile

import android.app.Activity
import android.app.NotificationManager
import android.app.WallpaperManager
import android.app.role.RoleManager
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.service.wallpaper.WallpaperService
import android.widget.LinearLayout
import android.widget.Toast

/**
 * Phone setup: one checklist of everything Orion can do on this phone, each with its state and a button that
 * opens the right Android settings page. Also the PC status card, the inbox, call screening and Quick Settings.
 */
class PhoneSetupActivity : Activity() {

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun roles() = getSystemService(RoleManager::class.java)
    private fun held(role: String) = try { roles().isRoleAvailable(role) && roles().isRoleHeld(role) } catch (e: Exception) { false }

    private fun render() {
        val col = SysUi.page(this, "Phone setup", "Turn on what you want. Each one can be undone from the same Android page.")
        col.addView(PcCard.build(this))
        col.addView(SysUi.button(this, "Missions: send work to the PC", primary = false) { MissionsActivity.open(this) })
        col.addView(PocketCard.build(this))   // PC off: Orion on this phone

        col.addView(SysUi.section(this, "Checklist"))
        item(col, "Home screen", held(RoleManager.ROLE_HOME),
            "Makes Orion your home screen. Undo: Settings > Apps > Default apps > Home app.", "Set as home") {
            requestRole(RoleManager.ROLE_HOME) { open(Settings.ACTION_HOME_SETTINGS) }
        }
        item(col, "Digital assistant", held(RoleManager.ROLE_ASSISTANT),
            "Long-press the power button to talk to Orion. Android doesn't let apps switch this on themselves: " +
                "open Settings > Apps > Default apps > Digital assistant app, tap Default digital assistant app and pick Orion. " +
                "On some phones also set Settings > System > Gestures > Press and hold power button to Digital assistant.", "Open default apps") {
            open(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS, Settings.ACTION_VOICE_INPUT_SETTINGS)
        }
        item(col, "Notification access", notificationAccess(),
            "Fills your inbox from your messaging apps. Messages stay on this phone. Find Orion on the next page and turn it on.",
            "Open notification access") { openNotificationAccess() }
        item(col, "Call screening", CallScreenSettings.roleHeld(this),
            "Optional. Lets Orion handle calls from numbers that aren't in your contacts (choose how below).",
            "Allow call screening") { requestRole(RoleManager.ROLE_CALL_SCREENING) { open(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS) } }
        val wp = ownWallpaper()
        item(col, "Live wallpaper", wallpaperOn(),
            if (wp != null) "The black hole as your wallpaper and lock screen background."
            else "The black hole wallpaper comes with the Orion launcher update. You can pick it here once it's installed.",
            "Set wallpaper") { setWallpaper(wp) }
        item(col, "Microphone", checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == 0,
            "For hold-to-talk and the assistant. Orion only records while you hold the mic or the sheet says Listening.",
            "Allow microphone") {
            if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == 0) open(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            else requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 41)
        }

        // ---------------------------------------------------------------- inbox
        col.addView(SysUi.section(this, "One inbox"))
        val inbox = SysUi.card(this)
        val n = InboxStore.all(this).size
        inbox.addView(SysUi.text(this, if (n == 0) "No messages kept yet." else "$n messages from the last 7 days, kept on this phone."))
        inbox.addView(SysUi.button(this, "Open inbox", primary = true) { startActivity(Intent(this, InboxActivity::class.java)) })
        col.addView(inbox)

        // ---------------------------------------------------------------- call screening choice
        col.addView(SysUi.section(this, "Unknown callers"))
        val calls = SysUi.card(this)
        val mode = CallScreenSettings.mode(this)
        calls.addView(SysUi.text(this, "For numbers that aren't in your contacts. Contacts always ring as normal. " +
            "Live call transcripts aren't included."))
        val r = SysUi.row(this)
        listOf(CallScreenSettings.OFF to "Off", CallScreenSettings.RING to "Ring + notice", CallScreenSettings.SILENCE to "Silence to voicemail")
            .forEach { (m, label) ->
                r.addView(SysUi.pill(this, label, if (mode == m) SysUi.cyan else SysUi.ink) { chooseCallMode(m) })
            }
        calls.addView(android.widget.HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(r) })
        if (mode != CallScreenSettings.OFF && !CallScreenSettings.roleHeld(this))
            calls.addView(SysUi.note(this, "Not active yet: tap \"Allow call screening\" above."))
        if (mode != CallScreenSettings.OFF && checkSelfPermission(android.Manifest.permission.READ_CONTACTS) != 0)
            calls.addView(SysUi.note(this, "Not active yet: Orion needs to read your contacts to know who's unknown. " +
                "Without it, every call rings as normal."))
        col.addView(calls)

        // ---------------------------------------------------------------- tiles
        col.addView(SysUi.section(this, "Quick Settings tiles"))
        val tiles = SysUi.card(this)
        tiles.addView(SysUi.text(this, "Swipe down twice from the top, tap the pencil, and drag in Orion's tiles: " +
            "Freeze, PC mic, Approvals and Scout link. Unfreezing and approving still need your fingerprint."))
        col.addView(tiles)
    }

    private fun item(col: LinearLayout, name: String, on: Boolean, what: String, action: String, go: () -> Unit) {
        val c = SysUi.card(this)
        val head = SysUi.row(this)
        head.addView(SysUi.weighted(SysUi.title(this, name)))
        head.addView(SysUi.text(this, if (on) "On" else "Off", 14f, if (on) SysUi.green else SysUi.quiet))
        c.addView(head)
        c.addView(SysUi.note(this, what))
        c.addView(SysUi.button(this, if (on) "Change" else action, primary = !on) { go() })
        col.addView(c)
    }

    // ---------------------------------------------------------------- states
    private fun notificationAccess() = try {
        getSystemService(NotificationManager::class.java).isNotificationListenerAccessGranted(InboxListener.component(this))
    } catch (e: Exception) { false }

    private fun ownWallpaper(): ComponentName? = try {
        packageManager.queryIntentServices(Intent(WallpaperService.SERVICE_INTERFACE).setPackage(packageName), 0)
            .firstOrNull()?.serviceInfo?.let { ComponentName(it.packageName, it.name) }
    } catch (e: Exception) { null }

    private fun wallpaperOn() = try { WallpaperManager.getInstance(this).wallpaperInfo?.packageName == packageName } catch (e: Exception) { false }

    // ---------------------------------------------------------------- actions
    private var afterRole: (() -> Unit)? = null

    private fun requestRole(role: String, fallback: () -> Unit) {
        val rm = roles()
        if (!rm.isRoleAvailable(role)) { fallback(); return }
        if (rm.isRoleHeld(role)) { fallback(); return }   // already on: the settings page is where you undo it
        afterRole = fallback
        try { startActivityForResult(rm.createRequestRoleIntent(role), REQ_ROLE) } catch (e: Exception) { fallback() }
    }

    @Deprecated("Activity result for the role dialog")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_ROLE) {
            // Android quietly refuses the dialog if you've said no before; send you to the settings page instead.
            if (resultCode != RESULT_OK) afterRole?.invoke()
            afterRole = null
        }
    }

    private fun chooseCallMode(m: String) {
        CallScreenSettings.setMode(this, m)
        if (m != CallScreenSettings.OFF) {
            // One Android dialog at a time: contacts first, then the call screening role (see the result below).
            if (checkSelfPermission(android.Manifest.permission.READ_CONTACTS) != 0)
                requestPermissions(arrayOf(android.Manifest.permission.READ_CONTACTS), REQ_CONTACTS)
            else if (!CallScreenSettings.roleHeld(this)) requestRole(RoleManager.ROLE_CALL_SCREENING) { }
        }
        render()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode == REQ_CONTACTS && !CallScreenSettings.roleHeld(this)) requestRole(RoleManager.ROLE_CALL_SCREENING) { }
        render()
    }

    private fun openNotificationAccess() {
        try {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, InboxListener.component(this).flattenToString()))
        } catch (e: Exception) { open("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS") }
    }

    private fun setWallpaper(c: ComponentName?) {
        try {
            if (c != null) startActivity(Intent(WallpaperManager.ACTION_CHANGE_LIVE_WALLPAPER)
                .putExtra(WallpaperManager.EXTRA_LIVE_WALLPAPER_COMPONENT, c))
            else startActivity(Intent(WallpaperManager.ACTION_LIVE_WALLPAPER_CHOOSER))
        } catch (e: Exception) { open(Settings.ACTION_DISPLAY_SETTINGS) }
    }

    /** Opens the first settings page this phone has; falls back to the app's own info page. */
    private fun open(vararg actions: String) {
        for (a in actions) {
            try { startActivity(Intent(a)); return } catch (_: Exception) { }
        }
        try {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        } catch (e: Exception) { Toast.makeText(this, "Open your phone's Settings app.", Toast.LENGTH_LONG).show() }
    }

    companion object { private const val REQ_ROLE = 51; private const val REQ_CONTACTS = 42 }
}
