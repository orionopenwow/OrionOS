package com.orion.mobile

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Phone-system calls to the lab (Android Stage 1: home, draft, inbox mirror, hot mic).
 * Same signing as Lab.kt: the read key (Keys.signWithRead) over
 * "ORION-BOARDROOM-v1\n" + METHOD + "\n" + PATH + "\n" + TS + "\n" + sha256hex(body),
 * with the same strictly increasing timestamp stored in Lab's preferences ("last_ts").
 * Kept in its own file so Lab.kt stays untouched; anything that needs the fingerprint still goes through Lab.
 */
class SystemApi(private val context: Context) {
    private val lab = Lab(context)
    private val prefs = context.getSharedPreferences("orion", Context.MODE_PRIVATE)

    /** GET /phone/home -> {"core", "approvals", "agents", "health", "frozen", "pc"}. */
    fun home(): JSONObject = call("GET", "/phone/home")

    /** POST /phone/draft: a reply drafted in your style. The PC never sends it; you do, with a tap. */
    fun draft(app: String, sender: String, text: String, thread: String?): String {
        val b = JSONObject().put("app", app).put("sender", sender).put("text", text.take(4000))
        if (!thread.isNullOrEmpty()) b.put("thread", thread)
        return call("POST", "/phone/draft", b.toString().toByteArray(), timeoutMs = 90000).optString("draft")
    }

    /** POST /phone/inbox: only used when "Mirror inbox to my PC" is on. */
    fun mirrorInbox(items: JSONArray): JSONObject =
        call("POST", "/phone/inbox", JSONObject().put("items", items).toString().toByteArray())

    /** POST /phone/hotmic {"state":"on|off"}: turns the PC's hot mic on or off. Returns the mic status. */
    fun hotMic(on: Boolean): JSONObject =
        call("POST", "/phone/hotmic", JSONObject().put("state", if (on) "on" else "off").toString().toByteArray(),
            timeoutMs = 30000)

    /** GET /phone/hotmic: {"listening": bool, ...} (may be missing on older PCs). */
    fun hotMicState(): JSONObject = call("GET", "/phone/hotmic")

    /** Is Orion frozen? Uses /phone/health, which every paired PC has. */
    fun frozen(): Boolean = lab.health().optBoolean("frozen")

    // ------------------------------------------------------------------ plumbing (mirrors Lab.readCall)
    private fun call(method: String, path: String, body: ByteArray? = null, timeoutMs: Int = 15000): JSONObject {
        val url = lab.labUrl
        if (url.isEmpty() || !lab.paired) throw LabError("Pair this phone with your PC first.")
        val bytes = body ?: ByteArray(0)
        val ts = nextTs()
        val msg = "ORION-BOARDROOM-v1\n$method\n$path\n$ts\n${Lab.sha256hex(bytes)}".toByteArray(Charsets.UTF_8)
        val sig = Keys.signWithRead(msg)
        val conn = (URL(url + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 8000
            readTimeout = timeoutMs
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("X-Orion-Device", lab.deviceId)
            setRequestProperty("X-Orion-Ts", ts)
            setRequestProperty("X-Orion-Sig", Lab.b64(sig))
            if (body != null) {
                doOutput = true
                outputStream.use { it.write(body) }
            }
        }
        try {
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() } ?: ""
            val j = try { JSONObject(text) } catch (e: Exception) { JSONObject() }
            if (code == 404) throw MissingRoute(path)
            if (code !in 200..299) throw LabError(j.optString("error").ifEmpty { "Your PC answered $code" })
            return j
        } catch (e: java.io.IOException) {
            throw LabError("Can't reach your PC at $url. On home Wi-Fi? (Away from home: Tailscale.)")
        } finally {
            conn.disconnect()
        }
    }

    private fun nextTs(): String = synchronized(LOCK) {
        val t = maxOf(System.currentTimeMillis(), prefs.getLong("last_ts", 0L) + 1)
        prefs.edit().putLong("last_ts", t).commit()
        t.toString()
    }

    /** The PC runs an older Orion without this route. */
    class MissingRoute(path: String) : Exception("Your PC's Orion is older and doesn't know $path yet. Update Orion on the PC.")

    companion object {
        private val LOCK = Any()
    }
}
