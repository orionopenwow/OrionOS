package com.orion.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/**
 * Alerts from the lab's feed (/phone/feed), shown at the level the lab learned for each type:
 *   urgent → sound + vibration, normal → a regular notification, digest → silent, bundled into one.
 * Opening or swiping away an alert is reported back (/phone/feedback) so the lab can learn.
 */
object Alerts {
    const val URGENT = "orion_urgent"
    const val NORMAL = "orion_normal"
    const val DIGEST = "orion_digest"
    const val SERVICE = "orion_service"
    private const val DIGEST_GROUP = "orion.digest"
    private const val DIGEST_SUMMARY_ID = 9000
    const val EXTRA_ALERT = "alert_id"
    const val EXTRA_KIND = "alert_type"
    const val EXTRA_CARD = "card_id"

    fun ensureChannels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        fun ch(id: String, name: String, importance: Int, desc: String, silent: Boolean = false) {
            if (nm.getNotificationChannel(id) != null) return
            nm.createNotificationChannel(NotificationChannel(id, name, importance).apply {
                description = desc
                if (silent) { setSound(null, null); enableVibration(false) } else enableVibration(importance >= NotificationManager.IMPORTANCE_HIGH)
            })
        }
        ch(URGENT, "Urgent", NotificationManager.IMPORTANCE_HIGH, "Approvals and things Orion learned you want right away")
        ch(NORMAL, "Normal", NotificationManager.IMPORTANCE_DEFAULT, "Regular alerts")
        ch(DIGEST, "Digest", NotificationManager.IMPORTANCE_LOW, "Bundled, silent alerts you usually check later", silent = true)
        ch(SERVICE, "Connection", NotificationManager.IMPORTANCE_MIN, "Shows while instant alerts are on", silent = true)
    }

    private fun prefs(c: Context) = c.getSharedPreferences("orion", Context.MODE_PRIVATE)

    var Context.lastSeq: Long
        get() = prefs(this).getLong("feed_seq", 0L)
        set(v) { prefs(this).edit().putLong("feed_seq", v).apply() }

    /** Fetch new alerts (optionally waiting for one), show them, and send queued feedback. Returns how many arrived. */
    fun sync(context: Context, waitS: Int = 0): Int {
        val lab = Lab(context)
        flushFeedback(context, lab)
        var n = 0
        var wait = waitS
        for (page in 0 until 10) {   // [link] a backlog arrives page by page ("more"), nothing skipped
            val out = lab.feed(context.lastSeq, wait)
            val arr = out.optJSONArray("alerts") ?: JSONArray()
            for (i in 0 until arr.length()) show(context, arr.getJSONObject(i))   // same seq -> same notification id
            n += arr.length()
            val last = out.optLong("last", context.lastSeq)
            // [link] "reset": the PC's alert numbers start lower than ours (its alert store was replaced)
            context.lastSeq = if (out.optBoolean("reset")) last else maxOf(context.lastSeq, last)
            prefs(context).edit().putString("policy", (out.optJSONArray("policy") ?: JSONArray()).toString()).apply()
            if (!out.optBoolean("more")) break
            wait = 0
        }
        return n
    }

    fun policy(context: Context): JSONArray =
        try { JSONArray(prefs(context).getString("policy", "[]")) } catch (e: Exception) { JSONArray() }

    private fun show(context: Context, a: JSONObject) {
        ensureChannels(context)
        val nm = context.getSystemService(NotificationManager::class.java)
        val id = a.optString("id")
        val level = a.optString("level", "normal")
        val nid = (a.optLong("seq") % 8000).toInt() + 1
        val open = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(EXTRA_ALERT, id).putExtra(EXTRA_KIND, a.optString("type"))
            .putExtra(EXTRA_CARD, a.optString("card").takeIf { it.isNotEmpty() && it != "null" })
        val dismiss = Intent(context, FeedbackReceiver::class.java).putExtra(EXTRA_ALERT, id)
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val b = Notification.Builder(context, when (level) { "urgent" -> URGENT; "digest" -> DIGEST; else -> NORMAL })
            .setSmallIcon(R.drawable.ic_stat_orion)
            .setContentTitle(a.optString("title"))
            .setContentText(a.optString("body"))
            .setStyle(Notification.BigTextStyle().bigText(a.optString("body")))
            .setContentIntent(PendingIntent.getActivity(context, nid, open, flags))
            .setDeleteIntent(PendingIntent.getBroadcast(context, nid, dismiss, flags))
            .setAutoCancel(true)
            .setColor(0xFF7C5CFF.toInt())
        if (level == "digest") b.setGroup(DIGEST_GROUP)
        nm.notify(nid, b.build())
        if (level == "digest") {
            nm.notify(DIGEST_SUMMARY_ID, Notification.Builder(context, DIGEST)
                .setSmallIcon(R.drawable.ic_stat_orion)
                .setContentTitle("Orion digest")
                .setGroup(DIGEST_GROUP).setGroupSummary(true).setAutoCancel(true)
                .setColor(0xFF7C5CFF.toInt())
                .build())
        }
    }

    // ------------------------------------------------------------------ feedback (opened / dismissed)
    fun record(context: Context, alertId: String?, action: String) {
        if (alertId.isNullOrEmpty()) return
        val p = prefs(context)
        val q = try { JSONArray(p.getString("feedback", "[]")) } catch (e: Exception) { JSONArray() }
        q.put(JSONObject().put("id", alertId).put("action", action).put("at", System.currentTimeMillis() / 1000.0))
        p.edit().putString("feedback", q.toString()).apply()
    }

    private fun flushFeedback(context: Context, lab: Lab) {
        val p = prefs(context)
        val q = try { JSONArray(p.getString("feedback", "[]")) } catch (e: Exception) { JSONArray() }
        if (q.length() == 0) return
        lab.feedback(q)
        p.edit().putString("feedback", "[]").apply()
    }
}

/** Swiping a notification away tells Orion you didn't need it. */
class FeedbackReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Alerts.record(context, intent.getStringExtra(Alerts.EXTRA_ALERT), "dismissed")
    }
}

/** After a restart, bring back instant alerts and the 15-minute check. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!Lab(context).paired) return
        PollJob.schedule(context)
        if (LiveService.enabled(context)) LiveService.start(context)
    }
}
