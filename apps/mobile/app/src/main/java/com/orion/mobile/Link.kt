package com.orion.mobile

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NoRouteToHostException
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.util.UUID
import kotlin.random.Random

/*
 * A steadier phone <-> PC link (the PC side is apps/daemon/phone_link.py). No libraries.
 *
 *  - Every request: a connect timeout, a read timeout, and the PC's clock (X-Orion-Time) noted on every answer, so
 *    timestamps are corrected when this phone's clock is a few minutes off ([nextTs]).
 *  - Reads retry a few times with growing, random pauses ([retrying]); a request that may already have reached the
 *    PC is only retried when that is safe (GETs, or approvals carrying an idem key: [resend]).
 *  - Approvals and other fingerprint-signed requests carry an "idem" key inside the signed body. If the answer is
 *    lost, the exact same bytes are sent again and the PC hands back its stored answer: never applied twice, never
 *    lost, no second fingerprint.
 *  - One shared home fetch for every screen ([sharedHome]): screens and the wallpaper ask at once, one request goes.
 *    After a failure the next try waits (2 s, 4 s ... 60 s, with jitter) unless the network changes.
 *  - Network changes (Wi-Fi <-> mobile data, new Wi-Fi, new address) are watched ([watch]). Each one finds the PC
 *    again: the saved home address first when on Wi-Fi, then the current one, then the PC's Tailscale address, then
 *    a search on the Wi-Fi. Switching to a new address only happens after the PC proves it holds the key pinned at
 *    pairing (PcLink.hello with mustProve).
 *  - One plain status line ([line]) and "Fix the connection" ([LinkDoctor]).
 */

/** An I/O failure. [sent] = the request may have reached the PC (so only safe requests are retried). */
class LinkIOError(message: String, val sent: Boolean) : LabError(message)

enum class LinkState { CONNECTED, RECONNECTING, POCKET, NOT_SAME_WIFI, OFFLINE, UNREACHABLE, NOT_PAIRED, CLOCK }

object Link {
    private const val PREFS = "orion"
    const val KEY_LAN = "lan_url_saved"      // the PC's home-network address (from its signed hello)
    const val KEY_TAIL = "tail_url"          // the PC's Tailscale address, when it has one
    private const val KEY_OFFSET = "clock_offset_ms"
    private const val KEY_LAST_TS = "last_ts"
    const val CONNECT_TIMEOUT_MS = 5000
    private const val MAX_TRIES = 3
    private const val SKEW_LIMIT_MS = 240_000L   // the PC allows 300 s; stay inside it

    private val tsLock = Any()
    @Volatile var offsetMs: Long = 0L; private set
    @Volatile private var offsetLoaded = false

    @Volatile var lastOkAt: Long = 0L; private set          // elapsedRealtime of the last answer from the PC
    @Volatile var rttMs: Long = -1L; private set            // smoothed round trip
    @Volatile var fails: Int = 0; private set               // failures in a row
    @Volatile var reconnects: Int = 0; private set
    @Volatile var clockOff: Boolean = false; private set
    @Volatile var resolving: Boolean = false; private set

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // ------------------------------------------------------------------ clocks
    /** Milliseconds, strictly increasing across every Lab object in this app, corrected by the PC's clock. */
    fun nextTs(p: android.content.SharedPreferences): String = synchronized(tsLock) {
        if (!offsetLoaded) { offsetMs = p.getLong(KEY_OFFSET, 0L); offsetLoaded = true }
        val t = System.currentTimeMillis() + offsetMs
        var last = p.getLong(KEY_LAST_TS, 0L)
        if (last + 1 > t + SKEW_LIMIT_MS) last = t - 1    // this phone's clock was far ahead before: start again
        val ts = maxOf(t, last + 1)
        p.edit().putLong(KEY_LAST_TS, ts).commit()
        ts.toString()
    }

    /** The PC's clock from an answer; the midpoint of the round trip stands for "now" on the PC. */
    fun noteServerTime(p: android.content.SharedPreferences, header: String?, sentAt: Long, gotAt: Long) {
        val server = header?.toLongOrNull() ?: return
        val off = server - (sentAt + gotAt) / 2
        synchronized(tsLock) {
            if (kotlin.math.abs(off - offsetMs) > 1500) {   // ignore jitter; correct real drift
                offsetMs = off
                p.edit().putLong(KEY_OFFSET, off).apply()
            }
            offsetLoaded = true
        }
        clockOff = kotlin.math.abs(off) > 5 * 60_000L
    }

    // ------------------------------------------------------------------ one exchange
    /** One HTTP request with timeouts. Throws [LinkIOError]; any HTTP answer (even an error) is returned. */
    fun exchange(p: android.content.SharedPreferences, base: String, method: String, path: String, body: ByteArray?,
                 headers: Map<String, String>, contentType: String, timeoutMs: Int): Lab.Response {
        if (base.isEmpty()) throw LabError("No lab address yet. Pair this phone first.")
        val sentAt = System.currentTimeMillis()
        val conn = try {
            (URL(base + path).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = minOf(CONNECT_TIMEOUT_MS, timeoutMs)
                readTimeout = timeoutMs
                useCaches = false
                setRequestProperty("Content-Type", contentType)
                headers.forEach { (k, v) -> setRequestProperty(k, v) }
                if (body != null) doOutput = true   // must be set before connect()
            }
        } catch (e: Exception) { throw LinkIOError("That PC address doesn't look right: $base", sent = false) }
        try {
            try { conn.connect() } catch (e: java.io.IOException) { fail(); throw unreachable(base, sent = false) }
            if (body != null) {
                try { conn.outputStream.use { it.write(body) } } catch (e: java.io.IOException) { fail(); throw unreachable(base, sent = false) }
            }
            val code = try { conn.responseCode } catch (e: java.io.IOException) { fail(); throw unreachable(base, sent = true) }
            val bytes = try {
                (if (code in 200..299) conn.inputStream else conn.errorStream)?.use { it.readBytes() } ?: ByteArray(0)
            } catch (e: java.io.IOException) { fail(); throw unreachable(base, sent = true) }
            val gotAt = System.currentTimeMillis()
            noteServerTime(p, conn.getHeaderField("X-Orion-Time"), sentAt, gotAt)
            ok(gotAt - sentAt)
            return Lab.Response(code, bytes)
        } finally {
            conn.disconnect()
        }
    }

    private fun unreachable(base: String, sent: Boolean) =
        LinkIOError("Can't reach the lab at $base. On home Wi-Fi? (Away from home: Tailscale.)", sent)

    private fun ok(rtt: Long) {
        if (fails > 0) reconnects++
        fails = 0
        lastOkAt = SystemClock.elapsedRealtime()
        rttMs = if (rttMs < 0) rtt else (rttMs * 3 + rtt) / 4
    }

    private fun fail() { fails++ }

    // ------------------------------------------------------------------ retries
    /** Pause before try [attempt] (0-based): full jitter up to 400 ms * 2^attempt, at most 8 s. */
    fun backoffMs(attempt: Int, baseMs: Long = 400L, capMs: Long = 8000L): Long =
        Random.nextLong(0, minOf(capMs, baseMs shl minOf(attempt, 10)) + 1)

    private fun pause(ms: Long) { try { Thread.sleep(ms) } catch (_: InterruptedException) { Thread.currentThread().interrupt() } }

    private fun retryAfter(r: Lab.Response): Boolean = r.code == 429 || r.code == 502 || r.code == 503

    private fun clockRefusal(r: Lab.Response): Boolean =
        r.code == 401 && String(r.body, Charsets.UTF_8).contains("clock")

    /**
     * A read-key call. [attempt] signs afresh each time (new timestamp). GETs retry on any I/O failure; other
     * methods only when the request never left the phone. A clock refusal is retried once (the answer carried the
     * PC's time, so the new timestamp is corrected).
     */
    fun retrying(method: String, attempt: () -> Lab.Response): Lab.Response {
        var skewRetry = true
        var last: LabError? = null
        var i = 0
        while (i < MAX_TRIES) {
            try {
                val r = attempt()
                if (clockRefusal(r) && skewRetry) { skewRetry = false; continue }
                if (retryAfter(r) && i < MAX_TRIES - 1) { pause(backoffMs(i)); i++; continue }
                return r
            } catch (e: LinkIOError) {
                last = e
                if (e.sent && method != "GET") throw e
                if (Thread.currentThread().isInterrupted) throw e
                if (i < MAX_TRIES - 1) pause(backoffMs(i))
                i++
            }
        }
        throw last ?: LabError("Can't reach your PC.")
    }

    /**
     * A fingerprint-signed request: signed once, so the very same bytes are sent again after a failure. The PC
     * answers a resend with its stored answer (idem key), so this is safe even when the first one got through.
     */
    fun resend(send: () -> Lab.Response): Lab.Response {
        var last: LabError? = null
        for (i in 0 until MAX_TRIES + 1) {
            try {
                val r = send()
                if (retryAfter(r) && i < MAX_TRIES) { pause(backoffMs(i)); continue }
                return r
            } catch (e: LinkIOError) {
                last = e
                if (i < MAX_TRIES) pause(backoffMs(i, 600L))
            }
        }
        throw last ?: LabError("Can't reach your PC.")
    }

    /** The body with an "idem" key added (kept if the caller set one) and that key, for X-Orion-Idem. */
    fun withIdem(body: ByteArray): Pair<ByteArray, String> {
        val j = try { JSONObject(String(body, Charsets.UTF_8)) } catch (e: Exception) { return Pair(body, "") }
        val key = j.optString("idem").ifEmpty { UUID.randomUUID().toString().replace("-", "") }
        j.put("idem", key)
        return Pair(j.toString().toByteArray(Charsets.UTF_8), key)
    }

    // ------------------------------------------------------------------ one shared home fetch
    private val homeLock = Object()
    private var homeVal: HomeData? = null
    private var homeAt = 0L
    private var homeBusy = false
    private var nextTryAt = 0L
    private var downTries = 0

    /** Every screen's GET /phone/home goes through here: at most one in flight, fresh answers shared. */
    fun sharedHome(maxAgeMs: Long, fetch: () -> HomeData): HomeData {
        synchronized(homeLock) {
            val now = SystemClock.elapsedRealtime()
            val cached = homeVal
            if (cached != null && now - homeAt < maxAgeMs) return cached
            if (cached != null && !cached.reachable && now < nextTryAt) return cached   // backing off
            if (homeBusy) {
                val until = now + 20_000
                while (homeBusy && SystemClock.elapsedRealtime() < until) homeLock.wait(1000)
                homeVal?.let { return it }
            }
            homeBusy = true
        }
        var h: HomeData? = null
        try {
            h = fetch()
            return h
        } finally {
            synchronized(homeLock) {
                val now = SystemClock.elapsedRealtime()
                if (h != null) { homeVal = h; homeAt = now }
                if (h?.reachable == true) { downTries = 0; nextTryAt = 0 }
                else { nextTryAt = now + backoffMs(downTries, 2000L, 60_000L).coerceAtLeast(1000L); downTries++ }
                homeBusy = false
                homeLock.notifyAll()
            }
        }
    }

    /** The last home answer any screen got (null before the first). */
    fun lastHome(): HomeData? = synchronized(homeLock) { homeVal }

    private fun forgetBackoff() = synchronized(homeLock) { nextTryAt = 0; downTries = 0; homeAt = 0 }

    // ------------------------------------------------------------------ network changes
    @Volatile private var watching = false
    private val wakers = java.util.concurrent.CopyOnWriteArrayList<() -> Unit>()
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var pending: Runnable? = null

    /** Called on a network change and when the PC answers again: a long-poll that is sleeping wakes up. */
    fun onWake(f: () -> Unit) { wakers += f }
    fun offWake(f: () -> Unit) { wakers -= f }

    /** Start watching the phone's network (once per app process). */
    fun watch(ctx: Context) {
        if (watching) return
        val app = ctx.applicationContext
        val cm = app.getSystemService(ConnectivityManager::class.java) ?: return
        try {
            cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = changed(app)
                override fun onLost(network: Network) = changed(app)
                override fun onLinkPropertiesChanged(network: Network, lp: LinkProperties) = changed(app)
                override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                    // fires often (signal strength): only VPN on/off matters here (Tailscale)
                    val vpn = caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
                    if (vpn != lastVpn) { lastVpn = vpn; changed(app) }
                }
            })
            watching = true
        } catch (e: Exception) { }
    }

    @Volatile private var lastVpn: Boolean? = null

    /** Callbacks come in bursts: wait a moment, then find the PC again once. */
    private fun changed(app: Context) {
        forgetBackoff()
        try { prefs(app).edit().putLong("heal_at", 0L).apply() } catch (_: Exception) { }
        pending?.let { main.removeCallbacks(it) }
        val r = Runnable { Thread { resolve(app); wakers.forEach { try { it() } catch (_: Exception) { } } }.start() }
        pending = r
        main.postDelayed(r, 1500)
    }

    /** Addresses to try, best first, for the network the phone is on now. */
    fun candidates(ctx: Context, current: String): List<String> {
        val p = prefs(ctx)
        val lan = p.getString(KEY_LAN, "") ?: ""
        val tail = p.getString(KEY_TAIL, "") ?: ""
        val net = NetWords.transport(ctx)
        val out = when (net) {
            "none" -> emptyList()
            "cellular" -> listOf(tail, if (NetWords.homeOnly(current)) "" else current)
            else -> listOf(lan, current, tail)   // Wi-Fi, ethernet, VPN: home first (fastest), then the tailnet
        }
        return out.filter { it.isNotEmpty() }.distinct()
    }

    private val resolveLock = Any()

    /**
     * Find the PC again (background thread). Uses the current address if it answers; otherwise switches to another
     * known address or a PC found on the Wi-Fi, but only one that proves it holds the pinned key. True when connected.
     */
    fun resolve(ctx: Context): Boolean {
        val app = ctx.applicationContext
        val lab = Lab(app)
        if (!lab.paired || lab.urlOverride != null) return false
        synchronized(resolveLock) {
            resolving = true
            try {
                val p = prefs(app)
                val current = lab.labUrl
                val pinned = PcKey.pinned(p)
                for (u in candidates(app, current)) {
                    if (!PocketFallback.reachable(u, 1500)) continue
                    if (u == current) {
                        if (try { PcLink.hello(app, lab); true } catch (e: Exception) { false }) return true
                        continue
                    }
                    if (!pinned) continue   // never move to another address without the PC's key to check
                    lab.labUrl = u
                    try { PcLink.hello(app, lab, mustProve = true); return true } catch (e: Exception) { lab.labUrl = current }
                }
                if (NetWords.transport(app) != "cellular" && PcFinder.heal(app, lab, force = true)) return true
                return false
            } finally { resolving = false }
        }
    }

    // ------------------------------------------------------------------ the status line
    fun state(ctx: Context, h: HomeData? = lastHome()): LinkState {
        val lab = Lab(ctx)
        if (!lab.paired) return LinkState.NOT_PAIRED
        val fresh = SystemClock.elapsedRealtime() - lastOkAt < 30_000 && fails == 0
        if (h?.reachable == true || (h == null && fresh)) return LinkState.CONNECTED
        if (clockOff && fails == 0) return LinkState.CLOCK
        val net = NetWords.transport(ctx)
        if (net == "none") return LinkState.OFFLINE
        val tail = prefs(ctx).getString(KEY_TAIL, "") ?: ""
        if (net == "cellular" && NetWords.homeOnly(lab.labUrl) && tail.isEmpty()) return LinkState.NOT_SAME_WIFI
        if (h?.pocket == true || PocketFallback.usingPocket) return LinkState.POCKET
        if (resolving || fails in 1..3 || downTries <= 2) return LinkState.RECONNECTING
        return LinkState.UNREACHABLE
    }

    /** One plain line: "Connected", "Reconnecting…", "PC asleep — using Orion on this phone", ... */
    fun line(ctx: Context, h: HomeData? = lastHome()): String = when (state(ctx, h)) {
        LinkState.CONNECTED -> "Connected" + (PcLink.pcName(ctx).takeIf { it.isNotEmpty() }?.let { " to $it" } ?: "")
        LinkState.RECONNECTING -> "Reconnecting…"
        LinkState.POCKET -> "PC asleep — using Orion on this phone"
        LinkState.NOT_SAME_WIFI -> "Not on the same Wi-Fi"
        LinkState.OFFLINE -> "Phone offline — turn on Wi-Fi"
        LinkState.CLOCK -> "Phone clock is off — check date and time"
        LinkState.UNREACHABLE -> "Can't reach your PC — tap to fix"
        LinkState.NOT_PAIRED -> "Not paired with a PC"
    }

    /** "good", "fair" or "poor" (for the hello the PC shows in its Phone panel). */
    fun quality(): String = when {
        fails >= 3 || rttMs > 800 -> "poor"
        fails >= 1 || rttMs > 250 -> "fair"
        else -> "good"
    }

    /** What the phone tells the PC about the link in its hello. */
    fun helloFacts(ctx: Context, body: JSONObject, url: String) {
        if (rttMs >= 0) body.put("rtt_ms", rttMs)
        body.put("fails", fails).put("reconnects", reconnects).put("pinned", PcKey.pinned(prefs(ctx)))
        body.put("via", when { NetWords.tailscale(url) -> "tailnet"; NetWords.homeOnly(url) -> "lan"; else -> "other" })
    }

    /** After a hello: remember the PC's home and Tailscale addresses it reported (only from a proven PC). */
    fun saveAddresses(ctx: Context, r: JSONObject, proven: Boolean, url: String) {
        val p = prefs(ctx)
        val e = p.edit()
        if (NetWords.homeOnly(url)) e.putString(KEY_LAN, url)
        val a = r.optJSONObject("addresses")
        if (a != null && proven) {
            a.optString("tailnet").takeIf { it.startsWith("http://") && NetWords.tailscale(it) }?.let { e.putString(KEY_TAIL, it) }
            a.optString("lan").takeIf { it.startsWith("http://") && NetWords.homeOnly(it) }?.let { e.putString(KEY_LAN, it) }
        }
        e.apply()
    }

    /** The phone's own IPv4 address on the current network, or null. */
    fun myIp(ctx: Context): String? = try {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        cm.getLinkProperties(cm.activeNetwork)?.linkAddresses?.map { it.address }
            ?.firstOrNull { it is Inet4Address && !it.isLoopbackAddress }?.hostAddress
    } catch (e: Exception) { null }

    fun sameSubnet(a: String?, b: String?): Boolean? {
        if (a == null || b == null) return null
        val x = a.split("."); val y = b.split(".")
        if (x.size != 4 || y.size != 4) return null
        return x.take(3) == y.take(3)
    }
}

/**
 * "Fix the connection": runs the checks in order and says what to do, in plain words. Network calls run on a
 * background thread; the answer shows in a dialog.
 */
object LinkDoctor {
    data class Step(val name: String, val ok: Boolean?, val words: String)

    fun run(ctx: Context): Pair<String, List<Step>> {
        val app = ctx.applicationContext
        val lab = Lab(app)
        val steps = mutableListOf<Step>()
        fun done(say: String) = Pair(say, steps.toList())
        if (!lab.paired) return done("This phone isn't paired yet. Open Menu > Pair with my PC.")
        val net = NetWords.transport(app)
        if (net == "none") { steps += Step("Network", false, "The phone is offline."); return done("Turn on Wi-Fi on this phone.") }
        steps += Step("Network", true, when (net) { "wifi" -> "On Wi-Fi."; "cellular" -> "On mobile data."; "vpn" -> "VPN on (Tailscale?)."; else -> "Connected." })
        val url = lab.labUrl
        val host = NetWords.host(url)
        if (NetWords.homeOnly(url)) {
            val same = Link.sameSubnet(Link.myIp(app), host)
            if (net == "cellular" || same == false) {
                steps += Step("Same Wi-Fi", false, "Your PC is at $host; this phone is ${if (net == "cellular") "on mobile data" else "on another network (${Link.myIp(app)})"}.")
                if (Link.resolve(app)) { steps += Step("Found your PC", true, "Reached it at ${lab.labUrl}."); return done("Fixed: connected again.") }
                return done("Join this phone to the same Wi-Fi as your PC (not a guest network).")
            }
            if (same == true) steps += Step("Same Wi-Fi", true, "The phone and the PC are on the same network.")
        }
        // the port: refused = Orion isn't listening; silence = something (usually the firewall) blocks it
        val port = try { URL(url).port.takeIf { it > 0 } ?: 9121 } catch (e: Exception) { 9121 }
        val portWords = try {
            Socket().use { it.connect(InetSocketAddress(host, port), 2500) }; null
        } catch (e: ConnectException) { "Your PC answered but Orion's phone port is closed. Is Orion running on the PC?" }
        catch (e: SocketTimeoutException) { "Something blocks port $port on your PC, usually Windows Firewall. On the PC, open Orion's Phone panel and press \"Let my phone in\"." }
        catch (e: NoRouteToHostException) { "No route to your PC. Is it on and awake?" }
        catch (e: UnknownHostException) { "That PC address doesn't work any more." }
        catch (e: Exception) { "Can't reach your PC at $host. Is it on and awake?" }
        if (portWords != null) {
            steps += Step("PC port", false, portWords)
            if (Link.resolve(app)) { steps += Step("Found your PC", true, "Your PC moved; reached it at ${lab.labUrl}."); return done("Fixed: connected again.") }
            val pocket = if (PocketFallback.enabled(app) && PocketFallback.paired(app)) " Meanwhile Orion on this phone answers chat and voice." else ""
            return done(portWords + pocket)
        }
        steps += Step("PC port", true, "Port $port on your PC answers.")
        // the PC's clock (unsigned hello) and its key (signed hello)
        val p = app.getSharedPreferences("orion", Context.MODE_PRIVATE)
        try { Link.exchange(p, url, "GET", "/boardroom/hello", null, emptyMap(), "application/json", 4000) } catch (_: Exception) { }
        val offMin = kotlin.math.abs(Link.offsetMs) / 60_000
        steps += Step("Clocks", offMin < 5, if (offMin < 1) "The phone's clock matches the PC's." else "The phone's clock is $offMin min off; Orion corrects for it.")
        val pinnedBefore = PcKey.pinned(p)
        try {
            PcLink.hello(app, lab, mustProve = pinnedBefore)
            steps += Step("PC key", true, if (pinnedBefore) "Your PC proved it holds the key saved at pairing." else "Saved your PC's key now.")
        } catch (e: Exception) {
            val m = e.message ?: ""
            steps += Step("PC key", false, m)
            return done(when {
                m.contains("unknown or unpaired") -> "Your PC doesn't know this phone any more. Pair again (Menu > Pair with my PC)."
                m.contains("clock") -> "Turn on automatic date and time on this phone."
                m.contains("isn't your PC") -> "Something else answered at your PC's address. Pair again on your home Wi-Fi."
                else -> m
            })
        }
        // the PC's own checks (firewall, its address, the network it saw this phone on)
        try {
            val r = lab.readJson("POST", "/phone/link-check", "{}".toByteArray(), timeoutMs = 15000)
            val a = r.optJSONArray("checks")
            if (a != null) for (i in 0 until a.length()) {
                val c = a.getJSONObject(i)
                val ok = if (c.isNull("ok")) null else c.optBoolean("ok")
                if (ok == false) steps += Step("PC: " + c.optString("name"), false, c.optString("words") +
                    (c.optString("fix").takeIf { it.isNotEmpty() }?.let { " $it" } ?: ""))
            }
            if (!r.optBoolean("ok", true)) return done(r.optString("say"))
        } catch (e: Exception) { /* an older PC without /phone/link-check */ }
        return done("Everything checks out. Connected.")
    }

    fun show(a: Activity) {
        val dlg = AlertDialog.Builder(a).setTitle("Fix the connection").setMessage("Checking…")
            .setPositiveButton("Close", null).setNeutralButton("Check again", null).create()
        fun go() {
            dlg.setMessage("Checking…")
            Thread {
                val (say, steps) = try { run(a) } catch (e: Exception) { Pair(e.message ?: "Check failed.", emptyList()) }
                val text = say + "\n\n" + steps.joinToString("\n") {
                    (when (it.ok) { true -> "OK  "; false -> "Problem  "; null -> "" }) + it.name + ": " + it.words
                }
                a.runOnUiThread { if (!a.isFinishing) dlg.setMessage(text) }
            }.start()
        }
        dlg.setOnShowListener { dlg.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener { go() } }
        dlg.show()
        go()
    }
}
