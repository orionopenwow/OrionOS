package com.orion.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.Context
import android.net.Uri
import android.provider.ContactsContract
import android.telecom.Call
import android.telecom.CallScreeningService

/*
 * Optional call screening (off by default). Needs Orion to hold the "call screening" role, which you grant from
 * Phone setup. For a caller who isn't in your contacts, Orion either lets it ring and shows an "Unknown caller"
 * notice, or silences the ring so it goes to voicemail, whichever you chose. Contacts are never touched.
 * No live transcript of calls (out of scope for now).
 */

object CallScreenSettings {
    const val OFF = "off"
    const val RING = "ring"          // let it ring + "Unknown caller" notice
    const val SILENCE = "silence"    // silence the ring; the call goes to voicemail as if you didn't answer
    const val CHANNEL = "orion_calls"

    private fun prefs(c: Context) = c.getSharedPreferences("orion_system", Context.MODE_PRIVATE)
    fun mode(c: Context): String = prefs(c).getString("call_mode", OFF) ?: OFF
    fun setMode(c: Context, m: String) = prefs(c).edit().putString("call_mode", m).apply()

    fun roleHeld(c: Context): Boolean = try {
        c.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
    } catch (e: Exception) { false }
}

class OrionCallScreen : CallScreeningService() {
    override fun onScreenCall(details: Call.Details) {
        val allow = CallResponse.Builder().build()
        try {
            val mode = CallScreenSettings.mode(this)
            if (mode == CallScreenSettings.OFF || details.callDirection != Call.Details.DIRECTION_INCOMING) {
                respondToCall(details, allow); return
            }
            val number = details.handle?.schemeSpecificPart ?: ""
            // Only act when we know for sure the caller isn't a contact (a hidden number never is one).
            // If we can't check (no contacts permission, lookup failed), the call rings untouched.
            val known = inContacts(number) ?: run { respondToCall(details, allow); return }
            if (known) { respondToCall(details, allow); return }
            if (mode == CallScreenSettings.SILENCE) {
                respondToCall(details, CallResponse.Builder().setSilenceCall(true).build())
                notifyUnknown(number, silenced = true)
            } else {
                respondToCall(details, allow)
                notifyUnknown(number, silenced = false)
            }
        } catch (e: Exception) {
            try { respondToCall(details, allow) } catch (_: Exception) { }   // when in doubt, let it ring
        }
    }

    /** true = a contact, false = not a contact, null = can't tell (so we leave the call alone). */
    private fun inContacts(number: String): Boolean? {
        if (number.isBlank()) return false  // hidden / withheld number: never a contact
        if (checkSelfPermission(android.Manifest.permission.READ_CONTACTS) != 0) return null
        return try {
            val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
            contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup._ID), null, null, null)?.use { it.count > 0 }
        } catch (e: Exception) { null }
    }

    private fun notifyUnknown(number: String, silenced: Boolean) {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CallScreenSettings.CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CallScreenSettings.CHANNEL, "Unknown callers",
                NotificationManager.IMPORTANCE_DEFAULT).apply { description = "Calls from numbers not in your contacts" })
        }
        val who = number.ifBlank { "a hidden number" }
        val n = Notification.Builder(this, CallScreenSettings.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_orion)
            .setContentTitle(if (silenced) "Silenced an unknown caller" else "Unknown caller")
            .setContentText(if (silenced) "$who, not in your contacts. It went to voicemail." else "$who isn't in your contacts.")
            .setCategory(Notification.CATEGORY_CALL)
            .setAutoCancel(true)
            .build()
        nm.notify(7100 + (number.hashCode() and 0xff), n)
    }
}
