package com.orion.mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.app.Notification

/**
 * Checks the lab every 15 minutes (Android's minimum for background jobs) using the read key and shows new
 * alerts (Alerts.kt). Instant alerts (LiveService) do the same within seconds while they're on. Approving always happens in the app, behind a fingerprint.
 */
class PollJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        Thread {
            try {
                check(applicationContext)
            } catch (_: Exception) {
                // Offline or Tailscale off: try again next time.
            }
            jobFinished(params, false)
        }.start()
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true

    companion object {
        private const val JOB_ID = 4201
        const val CHANNEL = "approvals"

        fun schedule(context: Context) {
            val js = context.getSystemService(JobScheduler::class.java)
            val job = JobInfo.Builder(JOB_ID, ComponentName(context, PollJob::class.java))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(15 * 60 * 1000L)
                .setPersisted(true)
                .build()
            js.schedule(job)
        }

        fun ensureChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL, "Approvals", NotificationManager.IMPORTANCE_HIGH).apply {
                        description = "Decisions waiting for your fingerprint"
                    }
                )
            }
        }

        /** Shared by the job and the app: fetch, then notify about anything new. Returns the snapshot. */
        fun check(context: Context): Lab.Snapshot {
            val lab = Lab(context)
            // v0.2 labs send every alert (approvals included) through the learning feed.
            var feedOk = try { Alerts.sync(context); true } catch (e: LabError) { false }
            if (!feedOk && Link.resolve(context)) feedOk = try { Alerts.sync(context); true } catch (e: LabError) { false }  // link: PC moved
            val snap = lab.pending()
            if (feedOk) return snap
            val prefs = context.getSharedPreferences("orion", Context.MODE_PRIVATE)
            val seen = prefs.getStringSet("seen", emptySet())!!.toMutableSet()
            val fresh = snap.decisions.filter { it.id !in seen }
            if (fresh.isNotEmpty()) {
                notify(context, fresh)
                seen.addAll(fresh.map { it.id })
                // keep the set small: only ids still pending plus the ones just announced
                val keep = snap.decisions.map { it.id }.toSet()
                prefs.edit().putStringSet("seen", seen.filter { it in keep }.toSet()).apply()
            }
            return snap
        }

        private fun notify(context: Context, fresh: List<Decision>) {
            ensureChannel(context)
            val nm = context.getSystemService(NotificationManager::class.java)
            val open = PendingIntent.getActivity(
                context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val title = if (fresh.size == 1) "Orion needs your approval" else "${fresh.size} decisions need your approval"
            val text = fresh.first().summary
            val n = Notification.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat_orion)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(fresh.joinToString("\n") { "• " + it.summary }))
                .setContentIntent(open)
                .setAutoCancel(true)
                .setColor(0xFF7C5CFF.toInt())
                .build()
            nm.notify(1, n)
        }
    }
}
