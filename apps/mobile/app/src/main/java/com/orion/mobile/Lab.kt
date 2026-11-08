package com.orion.mobile

import android.content.Context
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.Signature
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

open class LabError(message: String) : Exception(message)  // link: open for LinkIOError

/** One decision waiting on the boardroom, parsed from the exact text whose hash the phone will sign. */
data class Decision(
    val id: String,
    val packetHash: String,
    val label: String,
    val domain: String,
    val summary: String,
    val proposer: String,
    val risk: String,
    val amountCents: Long?,
    val recommendation: String?,
    val simulated: Boolean,
)

/**
 * Talks to the Orion lab's phone endpoint (port 9121, over your home Wi-Fi or Tailscale).
 * Every request is signed: "ORION-BOARDROOM-v1\n" + METHOD + "\n" + PATH + "\n" + TS + "\n" + sha256hex(body),
 * the same canonical message as apps/daemon/boardroom.py signing_message().
 */
class Lab(context: Context) {
    private val prefs = context.getSharedPreferences("orion", Context.MODE_PRIVATE)

    /** Pocket Orion (PocketFallback.kt): another address for this one Lab object; the saved PC address stays. */
    var urlOverride: String? = null   // pocket
    var labUrl: String
        get() = urlOverride ?: prefs.getString("lab_url", "") ?: ""   // pocket: urlOverride first
        set(v) { val u = v.trim().trimEnd('/'); if (urlOverride != null) urlOverride = u else prefs.edit().putString("lab_url", u).apply() }  // pocket

    var paired: Boolean
        get() = prefs.getBoolean("paired", false)
        set(v) = prefs.edit().putBoolean("paired", v).apply()

    val deviceId: String
        get() = prefs.getString("device_id", null) ?: ("phone-" + UUID.randomUUID().toString().replace("-", "").take(16))
            .also { prefs.edit().putString("device_id", it).apply() }

    /** Milliseconds, strictly increasing: the lab refuses any timestamp it has already seen. */
    private fun nextTs(): String = Link.nextTs(prefs)  // link: one lock for every Lab object, PC clock correction

    // ------------------------------------------------------------------ pairing
    fun pair(url: String, code: String, phoneName: String): String {
        labUrl = url
        Keys.ensure()
        val readDer = Keys.publicDer(Keys.READ)
        val signDer = Keys.publicDer(Keys.SIGN)
        val norm = code.uppercase().filter { it.isLetterOrDigit() }
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(norm.toByteArray(), "HmacSHA256")) }
        val proof = hex(mac.doFinal(readDer + signDer))
        val body = JSONObject()
            .put("device_id", deviceId)
            .put("name", phoneName)
            .put("read_pub", b64(readDer))
            .put("sign_pub", b64(signDer))
            .put("proof", proof)
        val res = http("POST", "/phone/pair", body.toString().toByteArray(), headers = emptyMap())
        val labFp = res.optString("fingerprint")
        val mine = Keys.fingerprint(signDer)
        if (labFp != mine) throw LabError("The lab registered a different key ($labFp). Don't confirm it on the lab.")
        PcKey.fromPairing(prefs, norm, res)  // [review-fix 7] pin the PC's own key (checked with the code)
        return mine
    }

    fun myFingerprint(): String = Keys.fingerprint(Keys.publicDer(Keys.SIGN))

    // ------------------------------------------------------------------ reading (read key, no fingerprint)
    data class Snapshot(val decisions: List<Decision>, val treasury: String, val rejected: Int)

    fun pending(): Snapshot {
        val ts = nextTs()
        val sig = Keys.signWithRead(message("GET", "/phone/pending", ts, ByteArray(0)))
        val res = http("GET", "/phone/pending", null, signedHeaders(ts, sig))
        val out = mutableListOf<Decision>()
        var rejected = 0
        val arr = res.optJSONArray("pending") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val item = arr.getJSONObject(i)
            val canon = item.optString("packet_canon")
            val hash = item.optString("packet_hash")
            // Only show (and later sign) a decision whose text hashes to its own fingerprint.
            if (canon.isEmpty() || sha256hex(canon.toByteArray(Charsets.UTF_8)) != hash) { rejected++; continue }
            val p = JSONObject(canon)
            val d = p.optJSONObject("details") ?: JSONObject()
            out += Decision(
                id = p.optString("id"),
                packetHash = hash,
                label = p.optString("label"),
                domain = p.optString("domain"),
                summary = p.optString("summary"),
                proposer = p.optString("proposer"),
                risk = p.optString("risk"),
                amountCents = if (d.has("amount_cents")) d.optLong("amount_cents") else null,
                recommendation = p.optString("recommendation").takeIf { it.isNotEmpty() && it != "null" },
                simulated = p.optBoolean("simulated", false),
            )
        }
        val t = res.optJSONArray("treasury") ?: JSONArray()
        val parts = (0 until t.length()).map {
            val b = t.getJSONObject(it)
            "${b.optString("label")} ${usd(b.optLong("balance_cents"))}"
        }
        return Snapshot(out, parts.joinToString(" · "), rejected)
    }

    // ------------------------------------------------------------------ deciding (sign key, after fingerprint)
    /** [unlocked] is the Signature returned by BiometricPrompt for this one approval. */
    fun verdict(decision: Decision, verdict: String, unlocked: Signature): JSONObject {
        val body = JSONObject()
            .put("id", decision.id)
            .put("verdict", verdict)
            .put("packet_hash", decision.packetHash)
            .put("note", "from Orion Mobile")
            .toString().toByteArray()
        return json(signedSend("POST", "/phone/verdict", body, unlocked, 15000)).getJSONObject("decision")  // link
    }

    // ------------------------------------------------------------------ v0.2: alerts, chat, voice, step cards
    /** Read-key request with a JSON (or raw) body. */
    private fun readCall(method: String, path: String, body: ByteArray? = null, query: String = "",
                         contentType: String = "application/json", timeoutMs: Int = 15000): Response =
        Link.retrying(method) {  // link: bounded retries, signed afresh each try
            val ts = nextTs()
            val sig = Keys.signWithRead(message(method, path, ts, body ?: ByteArray(0)))
            raw(method, path + query, body, signedHeaders(ts, sig), contentType, timeoutMs)
        }

    /** link: a fingerprint-signed request with an idem key; resent byte for byte if the answer is lost. */
    private fun signedSend(method: String, path: String, body: ByteArray, unlocked: Signature, timeoutMs: Int): Response {
        val (b, idem) = Link.withIdem(body)
        val ts = nextTs()
        unlocked.update(message(method, path, ts, b))
        val h = signedHeaders(ts, unlocked.sign()) + (if (idem.isNotEmpty()) mapOf("X-Orion-Idem" to idem) else emptyMap())
        return Link.resend { raw(method, path, b, h, "application/json", timeoutMs) }
    }

    // launcher (PairApi.kt): one public read-key call so new routes live in their own file
    fun readJson(method: String, path: String, body: ByteArray? = null, timeoutMs: Int = 15000): JSONObject =
        json(readCall(method, path, body, timeoutMs = timeoutMs))

    private fun json(r: Response): JSONObject {
        val text = String(r.body, Charsets.UTF_8)
        val j = try { JSONObject(text) } catch (e: Exception) { JSONObject() }
        if (r.code !in 200..299) throw LabError(j.optString("error").ifEmpty { "Lab answered ${r.code}" })
        return j
    }

    /** New alerts after [since]; waits up to [waitS] seconds for one (long-poll). */
    fun feed(since: Long, waitS: Int = 0): JSONObject =
        json(readCall("GET", "/phone/feed", query = "?since=$since&wait=$waitS", timeoutMs = (waitS + 15) * 1000))

    fun feedback(events: JSONArray): JSONObject =
        json(readCall("POST", "/phone/feedback", JSONObject().put("events", events).toString().toByteArray()))

    fun alertSettings(type: String, level: String?, locked: Boolean?): JSONObject {
        val b = JSONObject().put("type", type)
        if (level != null) b.put("level", level)
        if (locked != null) b.put("locked", locked)
        return json(readCall("POST", "/phone/alert-settings", b.toString().toByteArray()))
    }

    fun chat(text: String, speak: Boolean): JSONObject =
        json(readCall("POST", "/phone/chat", JSONObject().put("text", text).put("speak", speak).toString().toByteArray(),
            timeoutMs = 90000))

    fun voice(audio: ByteArray): JSONObject =
        json(readCall("POST", "/phone/voice", audio, contentType = "audio/mp4", timeoutMs = 120000))

    /** Video Scout: a shared TikTok / YouTube / Reels link. Read key: scouting only reads, it never posts. */
    fun scout(text: String): JSONObject =
        json(readCall("POST", "/phone/scout", JSONObject().put("text", text).toString().toByteArray()))

    fun quick(): List<String> {
        val a = json(readCall("GET", "/phone/quick")).optJSONArray("commands") ?: JSONArray()
        return (0 until a.length()).map { a.getString(it) }
    }

    fun cards(): JSONArray = json(readCall("GET", "/phone/cards")).optJSONArray("cards") ?: JSONArray()

    fun cardImage(id: String): ByteArray? {
        val r = readCall("GET", "/phone/card-image/$id")
        return if (r.code == 200) r.body else null
    }

    fun cardDone(id: String): JSONObject =
        json(readCall("POST", "/phone/card/done", JSONObject().put("id", id).toString().toByteArray()))

    /** "Orion does it": signed with the fingerprint key, like an approval. */
    fun cardDo(id: String, unlocked: Signature): JSONObject {
        val body = JSONObject().put("id", id).toString().toByteArray()
        return json(signedSend("POST", "/phone/card/do", body, unlocked, 15000))  // link
    }

    /** Missions.kt: one public fingerprint-key call (approve or reject a mission's review). */
    fun signedJson(method: String, path: String, body: ByteArray, unlocked: Signature, timeoutMs: Int = 15000): JSONObject {  // missions
        return json(signedSend(method, path, body, unlocked, timeoutMs))  // missions, link
    }  // missions

    /** The lab's health monitor in one line, plus whether the kill switch is on (read key). */
    fun health(): JSONObject = json(readCall("GET", "/phone/health")).optJSONObject("health") ?: JSONObject()

    /** Kill switch: freeze or unfreeze Orion. Both are signed with the fingerprint key; unfreezing *is* the approval. */
    fun setFrozen(freeze: Boolean, unlocked: Signature): JSONObject {
        val path = if (freeze) "/phone/freeze" else "/phone/unfreeze"
        val body = JSONObject().put("why", "from the phone").toString().toByteArray()
        return json(signedSend("POST", path, body, unlocked, 30000))  // link
            .optJSONObject("freeze") ?: JSONObject()
    }

    /** Turn the PC's hot mic on: signed with the fingerprint key (turning it off only needs the read key). */
    fun hotMicOn(unlocked: Signature): JSONObject {
        val path = "/phone/hotmic"
        val body = JSONObject().put("state", "on").toString().toByteArray()
        return json(signedSend("POST", path, body, unlocked, 15000))  // link
    }

    // ------------------------------------------------------------------ plumbing
    class Response(val code: Int, val body: ByteArray)

    private fun raw(method: String, path: String, body: ByteArray?, headers: Map<String, String>,
                    contentType: String, timeoutMs: Int): Response =
        Link.exchange(prefs, labUrl, method, path, body, headers, contentType, timeoutMs)  // link: timeouts, PC clock, status

    private fun message(method: String, path: String, ts: String, body: ByteArray): ByteArray =
        "ORION-BOARDROOM-v1\n$method\n$path\n$ts\n${sha256hex(body)}".toByteArray(Charsets.UTF_8)

    private fun signedHeaders(ts: String, sig: ByteArray) = mapOf(
        "X-Orion-Device" to deviceId,
        "X-Orion-Ts" to ts,
        "X-Orion-Sig" to b64(sig),
    )

    private fun http(method: String, path: String, body: ByteArray?, headers: Map<String, String>): JSONObject =
        json(raw(method, path, body, headers, "application/json", 15000))  // link

    companion object {
        fun sha256hex(b: ByteArray): String = hex(MessageDigest.getInstance("SHA-256").digest(b))
        fun hex(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }
        fun b64(b: ByteArray): String = Base64.encodeToString(b, Base64.NO_WRAP)
        fun usd(cents: Long): String = "$" + String.format("%,.2f", cents / 100.0)
    }
}
