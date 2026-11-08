package com.orion.mobile

import android.app.Notification
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Telephony
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/*
 * One inbox. With notification access turned on (Settings > Notifications > Device & app notifications, or the
 * Phone setup screen), Orion reads message notifications from the messaging apps you pick and keeps them
 * ON THIS PHONE ONLY: a small file, last 7 days, clearable any time. Nothing leaves the phone unless you turn on
 * "Mirror inbox to my PC". Replies are drafted on request and sent only when you tap Send.
 */

/** One message as Orion keeps it. [mine] is true for replies you sent from the inbox (or your own lines). */
data class InboxItem(
    val id: String, val pkg: String, val app: String, val sender: String, val text: String,
    val ts: Long, val thread: String, val title: String, val mine: Boolean,
) {
    fun toJson(): JSONObject = JSONObject().put("id", id).put("pkg", pkg).put("app", app).put("sender", sender)
        .put("text", text).put("ts", ts).put("thread", thread).put("title", title).put("mine", mine)

    companion object {
        fun from(j: JSONObject) = InboxItem(j.optString("id"), j.optString("pkg"), j.optString("app"),
            j.optString("sender"), j.optString("text"), j.optLong("ts"), j.optString("thread"),
            j.optString("title"), j.optBoolean("mine"))
    }
}

/** The phone-only store and the inbox settings. */
object InboxStore {
    private const val FILE = "orion-inbox.json"
    private const val KEEP_MS = 7L * 24 * 3600 * 1000
    private const val MAX_ITEMS = 1500
    private val lock = Any()

    /** Messaging apps Orion knows. "sms" stands for whichever app is the phone's default texting app. */
    val KNOWN: List<Pair<String, String>> = listOf(
        "com.discord" to "Discord",
        "com.facebook.orca" to "Messenger",
        "org.telegram.messenger" to "Telegram",
        "com.whatsapp" to "WhatsApp",
        "org.thoughtcrime.securesms" to "Signal",
        "sms" to "Texts (your default SMS app)",
        "com.google.android.apps.messaging" to "Google Messages",
        "com.instagram.android" to "Instagram",
    )

    /** Fires on the main thread whenever the inbox changes (the Inbox screen listens while open). */
    @Volatile var onChange: (() -> Unit)? = null
    private val main = Handler(Looper.getMainLooper())

    private fun prefs(c: Context) = c.getSharedPreferences("orion_system", Context.MODE_PRIVATE)

    fun enabledApps(c: Context): Set<String> =
        prefs(c).getStringSet("inbox_apps", null) ?: KNOWN.map { it.first }.toSet()

    fun setAppEnabled(c: Context, key: String, on: Boolean) {
        val s = enabledApps(c).toMutableSet()
        if (on) s.add(key) else s.remove(key)
        prefs(c).edit().putStringSet("inbox_apps", s).apply()
    }

    /** Extra package names you added by hand, comma separated. */
    fun extraApps(c: Context): List<String> =
        (prefs(c).getString("inbox_extra", "") ?: "").split(',', ' ', '\n').map { it.trim() }.filter { it.isNotEmpty() }

    fun setExtraApps(c: Context, s: String) = prefs(c).edit().putString("inbox_extra", s.trim()).apply()

    fun mirror(c: Context) = prefs(c).getBoolean("inbox_mirror", false)   // off by default
    fun setMirror(c: Context, on: Boolean) = prefs(c).edit().putBoolean("inbox_mirror", on).apply()

    fun watched(c: Context, pkg: String): Boolean {
        val on = enabledApps(c)
        if (pkg in on) return true
        if (pkg == "org.telegram.messenger.web" && "org.telegram.messenger" in on) return true
        if (pkg == "com.whatsapp.w4b" && "com.whatsapp" in on) return true
        if ("sms" in on && pkg == (try { Telephony.Sms.getDefaultSmsPackage(c) } catch (e: Exception) { null })) return true
        return pkg in extraApps(c)
    }

    fun all(c: Context): List<InboxItem> = synchronized(lock) { load(c) }

    /** Adds new items (skipping ones already kept); returns only the ones that were new. */
    fun add(c: Context, items: List<InboxItem>): List<InboxItem> = synchronized(lock) {
        val cur = load(c).toMutableList()
        val ids = cur.map { it.id }.toHashSet()
        val fresh = items.filter { it.id !in ids && System.currentTimeMillis() - it.ts < KEEP_MS }
            .filter { n -> cur.none { o -> o.thread == n.thread && o.sender == n.sender && o.text == n.text &&
                kotlin.math.abs(o.ts - n.ts) < 120_000 } }
            .distinctBy { it.id }
        if (fresh.isEmpty()) return emptyList()
        cur.addAll(fresh)
        save(c, cur)
        notifyChange()
        fresh
    }

    fun clear(c: Context) = synchronized(lock) { File(c.filesDir, FILE).delete(); notifyChange() }

    fun clearThread(c: Context, thread: String) = synchronized(lock) {
        save(c, load(c).filter { it.thread != thread }); notifyChange()
    }

    private fun notifyChange() { onChange?.let { cb -> main.post { cb() } } }

    private fun load(c: Context): List<InboxItem> {
        val f = File(c.filesDir, FILE)
        if (!f.exists()) return emptyList()
        return try {
            val a = JSONArray(f.readText())
            val cutoff = System.currentTimeMillis() - KEEP_MS
            (0 until a.length()).map { InboxItem.from(a.getJSONObject(it)) }.filter { it.ts >= cutoff }
        } catch (e: Exception) { emptyList() }
    }

    private fun save(c: Context, items: List<InboxItem>) {
        val cutoff = System.currentTimeMillis() - KEEP_MS
        val keep = items.filter { it.ts >= cutoff }.sortedBy { it.ts }.takeLast(MAX_ITEMS)
        val a = JSONArray(); keep.forEach { a.put(it.toJson()) }
        val f = File(c.filesDir, FILE)
        val tmp = File(c.filesDir, "$FILE.tmp")
        tmp.writeText(a.toString())
        tmp.renameTo(f)
    }

    fun id(vararg parts: String): String = Lab.sha256hex(parts.joinToString("\u0001").toByteArray()).take(24)
}

/**
 * Reads message notifications. The reply button of each conversation (its RemoteInput) and its tap action are
 * kept in memory only, while the notification is still showing, so "Send" can answer through the app itself.
 */
class InboxListener : NotificationListenerService() {

    override fun onListenerConnected() {
        super.onListenerConnected()
        connected = true
        try { activeNotifications?.forEach { capture(it) } } catch (_: Exception) { }
    }

    override fun onListenerDisconnected() {
        connected = false
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        try { capture(sbn) } catch (_: Exception) { }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        // The notification is gone, so its reply button is too.
        val dead = handles.filterValues { it.sbnKey == sbn.key }.keys
        dead.forEach { handles.remove(it) }
        if (dead.isNotEmpty()) InboxStore.onChange?.let { cb -> Handler(Looper.getMainLooper()).post { cb() } }
    }

    private fun capture(sbn: StatusBarNotification) {
        val pkg = sbn.packageName ?: return
        if (pkg == packageName || !InboxStore.watched(this, pkg)) return
        val n = sbn.notification ?: return
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        if (n.flags and Notification.FLAG_ONGOING_EVENT != 0) return   // "Discord is running", calls, uploads
        val ex = n.extras ?: return
        val appLabel = label(pkg)
        val convTitle = ex.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString()
        val title = ex.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        val thread = pkg + "|" + (n.shortcutId ?: convTitle ?: title ?: sbn.tag ?: sbn.id.toString())
        val shownTitle = convTitle ?: title ?: appLabel

        val out = mutableListOf<InboxItem>()
        val msgs = ex.getParcelableArray(Notification.EXTRA_MESSAGES)
        if (msgs != null && msgs.isNotEmpty()) {
            for (p in msgs) {
                val b = p as? Bundle ?: continue
                val text = b.getCharSequence("text")?.toString() ?: continue
                val ts = b.getLong("time", n.`when`)
                val person = b.getParcelable<android.app.Person>("sender_person")
                val senderCs = person?.name ?: b.getCharSequence("sender")
                val mine = senderCs == null
                val sender = senderCs?.toString() ?: "You"
                out += InboxItem(InboxStore.id(thread, sender, text, (ts / 1000).toString()), pkg, appLabel, sender,
                    text, ts, thread, shownTitle, mine)
            }
        } else {
            if (n.category != null && n.category != Notification.CATEGORY_MESSAGE) return
            val text = (ex.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: ex.getCharSequence(Notification.EXTRA_TEXT))
                ?.toString()?.takeIf { it.isNotBlank() } ?: return
            val sender = title ?: appLabel
            val ts = if (n.`when` > 0) n.`when` else sbn.postTime
            out += InboxItem(InboxStore.id(thread, sender, text, (ts / 1000).toString()), pkg, appLabel, sender,
                text, ts, thread, shownTitle, false)
        }

        // Remember how to answer this conversation (only while the notification exists).
        val reply = n.actions?.firstOrNull { a ->
            a.remoteInputs?.any { it.allowFreeFormInput } == true &&
                (a.semanticAction == Notification.Action.SEMANTIC_ACTION_REPLY || a.semanticAction == Notification.Action.SEMANTIC_ACTION_NONE)
        } ?: n.actions?.firstOrNull { a -> a.remoteInputs?.any { it.allowFreeFormInput } == true }
        handles[thread] = Handle(sbn.key, reply, n.contentIntent)

        val fresh = InboxStore.add(this, out)
        if (fresh.isNotEmpty() && InboxStore.mirror(this)) mirror(fresh)
    }

    private fun mirror(items: List<InboxItem>) {
        val app = applicationContext
        Thread {
            try {
                val a = JSONArray()
                items.filter { !it.mine }.forEach {
                    a.put(JSONObject().put("app", it.app).put("sender", it.sender).put("text", it.text)
                        .put("ts", it.ts / 1000).put("thread", it.thread))
                }
                if (a.length() > 0) SystemApi(app).mirrorInbox(a)
            } catch (_: Exception) { }   // best effort: the phone copy is the real one
        }.start()
    }

    private fun label(pkg: String): String = try {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (e: Exception) { InboxStore.KNOWN.firstOrNull { it.first == pkg }?.second ?: pkg }

    class Handle(val sbnKey: String, val reply: Notification.Action?, val open: PendingIntent?)

    companion object {
        @Volatile var connected = false
        private val handles = java.util.concurrent.ConcurrentHashMap<String, Handle>()

        fun canReply(thread: String) = handles[thread]?.reply != null
        fun canOpen(thread: String) = handles[thread]?.open != null

        fun component(c: Context) = ComponentName(c, InboxListener::class.java)

        /**
         * Sends [text] through the messaging app's own reply button. Only ever called from the Send button in the
         * Inbox screen, after you tap it. Returns false if the notification (and so its reply button) is gone.
         */
        fun sendReply(c: Context, thread: String, text: String): Boolean {
            val action = handles[thread]?.reply ?: return false
            val inputs = action.remoteInputs ?: return false
            val intent = Intent()
            val results = Bundle()
            for (ri in inputs) if (ri.allowFreeFormInput) results.putCharSequence(ri.resultKey, text)
            RemoteInput.addResultsToIntent(inputs, intent, results)
            RemoteInput.setResultsSource(intent, RemoteInput.SOURCE_FREE_FORM_INPUT)
            return try { action.actionIntent.send(c, 0, intent); true } catch (e: Exception) { false }
        }

        fun open(c: Context, thread: String, pkg: String): Boolean {
            handles[thread]?.open?.let { pi -> try { pi.send(); return true } catch (_: Exception) { } }
            val launch = c.packageManager.getLaunchIntentForPackage(pkg) ?: return false
            c.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return true
        }
    }
}
