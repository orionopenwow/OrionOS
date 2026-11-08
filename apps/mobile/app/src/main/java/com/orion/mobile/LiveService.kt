package com.orion.mobile

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder

/**
 * "Instant alerts": keeps one waiting request open to the lab (a long-poll, ~50 s at a time), so alerts arrive
 * within seconds instead of every 15 minutes. Android requires a small ongoing notification while it runs.
 * Turn it off in Orion > Alerts to save battery; the 15-minute check keeps working either way.
 */
class LiveService : Service() {
    @Volatile private var running = false
    private var worker: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Alerts.ensureChannels(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, Alerts.SERVICE)
            .setSmallIcon(R.drawable.ic_stat_orion)
            .setContentTitle("Orion is connected")
            .setContentText("Instant alerts are on. Turn off in Orion > Alerts.")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(7, n, SPECIAL_USE) else startForeground(7, n)
        if (!running) {
            running = true
            Link.watch(this)  // link: network changes wake this loop and find the PC again
            worker = Thread {
                var tries = 0
                while (running) {
                    try {
                        Alerts.sync(this, waitS = 50)
                        tries = 0
                    } catch (e: Exception) {
                        // Off Wi-Fi or the PC asleep: wait a growing, random while (at most 2 minutes, so the
                        // phones of a household never retry in step). A network change ends the wait early.
                        if (tries >= 2) Link.resolve(this)
                        val ms = Link.backoffMs(tries, 3_000L, 120_000L).coerceAtLeast(2_000L)
                        tries++
                        try { Thread.sleep(ms) } catch (_: InterruptedException) { if (!running) break }
                    }
                }
            }.apply { isDaemon = true; start() }
            Link.onWake(wake)
        }
        return START_STICKY
    }

    private val wake: () -> Unit = { worker?.interrupt() }  // link

    override fun onDestroy() {
        Link.offWake(wake)  // link
        running = false
        worker?.interrupt()
        super.onDestroy()
    }

    companion object {
        private const val SPECIAL_USE = 0x40000000 // ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE (API 34)

        fun enabled(c: Context) = c.getSharedPreferences("orion", Context.MODE_PRIVATE).getBoolean("instant", true)

        fun setEnabled(c: Context, on: Boolean) {
            c.getSharedPreferences("orion", Context.MODE_PRIVATE).edit().putBoolean("instant", on).apply()
            if (on) start(c) else c.stopService(Intent(c, LiveService::class.java))
        }

        fun start(c: Context) {
            try { c.startForegroundService(Intent(c, LiveService::class.java)) } catch (_: Exception) { }
        }
    }
}
