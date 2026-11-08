package com.orion.mobile

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

/**
 * Pocket Orion fallback: when your PC can't be reached, chat, voice and Video Scout go to the small Orion running
 * inside Android 16's Linux terminal on this phone (os/phone/orion-pocket-setup.sh, os/phone/pocket/orion_pocket.py).
 *
 * Pocket Orion serves the same signed /phone/... routes at http://127.0.0.1:9121 (the Terminal app's port
 * forwarding) and is paired separately with the same phone keys (its own code: `orion-pocket pair`).
 *
 * Only [chat], [voice] and [scout] ever go there. Approvals, freezing, agents, missions, the hot mic and the
 * inbox mirror always go to the PC: Pocket Orion refuses those routes too. Answers that came from the pocket
 * carry "pocket": true, and [shown] puts [NOTICE] above them, so you always know the PC didn't answer.
 *
 * Call everything here from a background thread.
 */
object PocketFallback {
    const val POCKET_URL = "http://127.0.0.1:9121"
    const val NOTICE = "PC is off — using Orion on this phone"
    private const val PREFS = "orion"
    private const val KEY_ON = "pocket_enabled"
    private const val KEY_PAIRED = "pocket_paired"

    /** True while the last answer came from Pocket Orion (for status lines; each answer also says so itself). */
    @Volatile
    var usingPocket: Boolean = false
        private set

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun enabled(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_ON, false)

    fun setEnabled(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean(KEY_ON, on).apply()

    fun paired(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_PAIRED, false)

    /** Is something listening at this address? A plain connect, no request (cheap, no signature used). */
    fun reachable(url: String, timeoutMs: Int = 1500): Boolean = try {
        val u = URL(url)
        Socket().use { it.connect(InetSocketAddress(u.host, if (u.port > 0) u.port else u.defaultPort), timeoutMs) }
        true
    } catch (e: Exception) {
        false
    }

    /** Pocket Orion answers /pocket/hello without signing; anything else on that port is ignored. */
    fun pocketUp(timeoutMs: Int = 1500): Boolean = try {
        val conn = (URL("$POCKET_URL/pocket/hello").openConnection() as HttpURLConnection).apply {
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
        }
        try {
            conn.responseCode == 200 &&
                JSONObject(conn.inputStream.use { it.readBytes() }.toString(Charsets.UTF_8)).optString("orion") == "pocket"
        } finally {
            conn.disconnect()
        }
    } catch (e: Exception) {
        false
    }

    /** Turned on, paired and answering right now. */
    fun ready(ctx: Context): Boolean = enabled(ctx) && paired(ctx) && pocketUp()

    /** A Lab that talks to Pocket Orion. The saved PC address is never touched. */
    private fun pocketLab(ctx: Context): Lab = Lab(ctx.applicationContext).apply { urlOverride = POCKET_URL }

    /** Pairs this phone's keys with Pocket Orion. Returns the fingerprint to compare in the terminal. */
    fun pair(ctx: Context, code: String, phoneName: String): String {
        if (!pocketUp()) throw LabError("Pocket Orion isn't answering at $POCKET_URL. Start it in the Terminal app first.")
        val fp = pocketLab(ctx).pair(POCKET_URL, code, phoneName)
        prefs(ctx).edit().putBoolean(KEY_PAIRED, true).putBoolean(KEY_ON, true).apply()
        return fp
    }

    fun forget(ctx: Context) = prefs(ctx).edit().putBoolean(KEY_PAIRED, false).putBoolean(KEY_ON, false).apply()

    /**
     * Runs [block] against the PC. Only when the PC's port doesn't answer at all (and Pocket Orion is on, paired
     * and up) does the same request go to Pocket Orion. A PC that answers slowly or with an error is never
     * second-guessed: that answer is shown as it is.
     */
    private fun call(ctx: Context, lab: Lab, block: (Lab) -> JSONObject): JSONObject {
        if (enabled(ctx) && paired(ctx) && !reachable(lab.labUrl) && pocketUp()) {
            val out = block(pocketLab(ctx)).put("pocket", true)
            usingPocket = true
            return out
        }
        try {
            val out = block(lab)
            usingPocket = false
            return out
        } catch (e: java.io.IOException) {   // a refused connection while sending the body escapes Lab's own catch
            throw LabError("Can't reach your PC at ${lab.labUrl}. On home Wi-Fi? (Away from home: Tailscale.)")
        }
    }

    fun chat(ctx: Context, lab: Lab, text: String, speak: Boolean): JSONObject = call(ctx, lab) { it.chat(text, speak) }

    fun voice(ctx: Context, lab: Lab, audio: ByteArray): JSONObject = call(ctx, lab) { it.voice(audio) }

    fun scout(ctx: Context, lab: Lab, text: String): JSONObject = call(ctx, lab) { it.scout(text) }

    /**
     * A reply drafted in your style (never sent by Orion: you tap Send). Returns (draft, fromPocket).
     * The PC path is SystemApi.draft; the pocket path is the same signed POST /phone/draft.
     */
    fun draft(ctx: Context, app: String, sender: String, text: String, thread: String?): Pair<String, Boolean> {
        val lab = Lab(ctx.applicationContext)
        val body = JSONObject().put("app", app).put("sender", sender).put("text", text.take(4000))
        if (!thread.isNullOrEmpty()) body.put("thread", thread)
        val r = call(ctx, lab) {
            if (it.urlOverride == null) JSONObject().put("draft", SystemApi(ctx).draft(app, sender, text, thread))
            else it.readJson("POST", "/phone/draft", body.toString().toByteArray(), timeoutMs = 90000)
        }
        return Pair(r.optString("draft"), r.optBoolean("pocket"))
    }

    /** The reply as the screen shows it: with [NOTICE] on top when Pocket Orion answered. */
    fun shown(r: JSONObject, key: String = "reply"): String {
        val text = r.optString(key)
        return if (r.optBoolean("pocket")) "$NOTICE\n\n$text" else text
    }
}
