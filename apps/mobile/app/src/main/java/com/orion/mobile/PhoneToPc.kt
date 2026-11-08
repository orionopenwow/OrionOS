package com.orion.mobile

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import org.json.JSONObject

/*
 * Phone-to-PC: "Send to PC" in the share sheet for photos, videos and files. Orion's PC side already receives
 * files with LocalSend (free, open source), so when the LocalSend app is on this phone we hand the files
 * straight to it; otherwise we explain how to get it. Nothing is uploaded by Orion itself.
 * Also the PC status card (GET /phone/home "pc"), used on the Phone setup screen.
 */

object LocalSend {
    const val PKG = "org.localsend.localsend_app"

    fun installed(c: Context): Boolean = try { c.packageManager.getPackageInfo(PKG, 0); true } catch (e: Exception) { false }

    fun openStore(a: Activity) {
        try { a.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$PKG"))) }
        catch (e: ActivityNotFoundException) {
            a.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://localsend.org")))
        }
    }
}

class SendToPcActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val act = intent.action
        val sharing = act == Intent.ACTION_SEND || act == Intent.ACTION_SEND_MULTIPLE
        if (sharing && LocalSend.installed(this) && handOff()) { finish(); return }
        explain(sharing)
    }

    /** Passes the shared files to LocalSend with the same read permission we were given. */
    private fun handOff(): Boolean {
        val out = Intent(intent.action).apply {
            type = intent.type
            intent.extras?.let { putExtras(it) }
            clipData = intent.clipData
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            setPackage(LocalSend.PKG)
        }
        return try { startActivity(out); true } catch (e: Exception) { false }
    }

    private fun explain(sharing: Boolean) {
        val col = SysUi.page(this, "Send to PC", "Your PC receives files with LocalSend, a free app that sends files over your home Wi-Fi.")
        val c = SysUi.card(this)
        if (LocalSend.installed(this)) {
            c.addView(SysUi.title(this, "LocalSend is installed", SysUi.green))
            c.addView(SysUi.text(this, "Share a photo or file and pick \"Send to PC\": Orion opens LocalSend with it. " +
                "Choose your PC in LocalSend's list. Your PC must be on and on the same Wi-Fi."))
            c.addView(SysUi.button(this, "Open LocalSend", primary = true) {
                packageManager.getLaunchIntentForPackage(LocalSend.PKG)?.let { startActivity(it) }
            })
        } else {
            c.addView(SysUi.title(this, "Get LocalSend first", SysUi.amber))
            c.addView(SysUi.text(this, "1. Install LocalSend from the Play Store (or F-Droid).\n" +
                "2. On your PC, Orion already listens for LocalSend: nothing to set up there.\n" +
                "3. Share again and pick \"Send to PC\"." + if (sharing) "\n\nThe file you just shared wasn't sent." else ""))
            c.addView(SysUi.button(this, "Get LocalSend", primary = true) { LocalSend.openStore(this) })
        }
        col.addView(c)
        col.addView(SysUi.button(this, "Close", primary = false) { finish() })
    }
}

/** The PC status card: name, online, CPU, battery, uptime from GET /phone/home's "pc" field. */
object PcCard {
    fun build(a: Activity): View {
        val c = SysUi.card(a)
        val title = SysUi.title(a, "Your PC")
        val line = SysUi.text(a, "Checking...", 14f, SysUi.quiet)
        c.addView(title); c.addView(line)
        if (!Lab(a).paired) { line.text = "Not paired yet. Open Orion to pair this phone with your PC."; return c }
        Thread {
            val res: Result<JSONObject> = try { Result.success(SystemApi(a).home()) } catch (e: Exception) { Result.failure(e) }
            a.runOnUiThread {
                res.onSuccess { home ->
                    val pc = home.optJSONObject("pc")
                    if (pc == null) { line.text = "Your PC answered, but didn't send its status."; return@onSuccess }
                    title.text = pc.optString("name").ifEmpty { "Your PC" }
                    val online = pc.optBoolean("online", true)
                    val parts = mutableListOf(if (online) "Online" else "Offline")
                    if (!pc.isNull("cpu")) parts += "CPU ${pc.optDouble("cpu").toInt()}%"
                    if (!pc.isNull("battery")) parts += "battery ${pc.optInt("battery")}%"
                    val up = pc.optLong("uptime_s", -1)
                    if (up >= 0) parts += "up " + uptime(up)
                    if (home.optBoolean("frozen")) parts += "frozen"
                    line.text = parts.joinToString(" · ")
                    line.setTextColor(if (online) SysUi.ink else SysUi.amber)
                    home.optJSONObject("health")?.optString("text")?.takeIf { it.isNotEmpty() }?.let {
                        c.addView(SysUi.note(a, it))
                    }
                }.onFailure { e ->
                    line.text = if (e is SystemApi.MissingRoute) "Your PC's Orion is older: update it to see its status here."
                        else "Offline or not reachable. ${e.message ?: ""}".trim()
                    line.setTextColor(SysUi.amber)
                }
            }
        }.start()
        c.addView(LinearLayout(a).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(SysUi.pill(a, "Send files to PC") { a.startActivity(Intent(a, SendToPcActivity::class.java)) })
        })
        return c
    }

    private fun uptime(s: Long): String = when {
        s < 3600 -> "${s / 60} min"
        s < 86400 -> "${s / 3600} h"
        else -> "${s / 86400} d"
    }
}
