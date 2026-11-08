package com.orion.mobile

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import java.net.Inet6Address
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/*
 * "Pair with my PC" (phone side of the HUD's Phone panel, apps/daemon/phone_pairing_ui.py):
 *
 *  1. Find PCs on this Wi-Fi with Android's built-in NsdManager (the PC announces "_orion._tcp"), or arrive here
 *     from the QR code: the camera opens orion://pair?u=...&c=... and Orion pairs straight away.
 *  2. Tap your PC and type the code it shows.
 *  3. Check the five words match on both screens, and press Confirm on the PC. This screen notices by itself.
 *
 * Also here: finding the same PC again when its address changes (PcFinder.heal), the plain-words reason a PC
 * can't be reached (NetWords), and the hello the phone sends so the PC knows it's alive (PcLink).
 * No third-party libraries.
 */

/** The five words both screens show for a fingerprint. Same list, same order as phone_pairing_ui.WORDS. */
object FpWords {
    val WORDS = (
        "acorn album alpha amber anchor apple apron arrow aspen atlas attic autumn badge bagel baker bamboo " +
        "banjo barn basil basket beach beacon beaver berry bicycle bison blanket blossom boat bottle branch " +
        "bread brick bridge brook brush bubble bucket buffalo bundle butter button cabin cactus camel camera " +
        "candle canoe canyon carpet carrot castle cedar cello chalk cherry chess circle citrus clock cloud " +
        "clover cobalt cocoa coffee comet compass copper coral cotton cradle crane crayon cricket crystal " +
        "cupcake curtain daisy delta desert diamond dolphin domino donkey dragon drum eagle easel echo elbow " +
        "ember engine falcon feather fern fiddle field finch flute forest fossil fountain fox galaxy garden " +
        "garlic gecko ginger giraffe glacier globe goose granite grape gravel guitar hammer harbor harvest " +
        "hazel helmet heron honey horizon husky igloo indigo island ivory jacket jaguar jasmine jelly jigsaw " +
        "jungle kayak kettle kitten koala ladder lagoon lantern lemon lily linen lizard lobster locket lotus " +
        "magnet mango maple marble meadow melon meteor mint mirror mitten monkey moose mosaic muffin museum " +
        "nectar nickel noodle nutmeg oasis ocean olive onion orange orbit orchid otter owl paddle panda " +
        "paper parrot peach peanut pebble pelican pencil pepper piano pigeon pillow pine planet plum pocket " +
        "pony poppy potato pretzel prism puffin pumpkin puzzle quartz quill rabbit radar radish raven reef " +
        "ribbon river robin rocket rose ruby saddle salmon sandal satin shell silver sketch sled snail " +
        "sparrow spoon spruce stamp star stone summer sunset swan teapot thunder tiger toast tomato topaz " +
        "tulip tundra turtle umbrella valley velvet violet volcano waffle walnut whale willow window winter " +
        "yogurt zebra zipper"
    ).split(" ")

    fun of(fp: String): String {
        val hx = fp.filter { it.isLetterOrDigit() }
        if (hx.length < 10) return ""
        return (0 until 5).joinToString(" ") { WORDS[hx.substring(it * 2, it * 2 + 2).toInt(16)] }
    }
}

/** Why the PC can't be reached, in plain words. */
object NetWords {
    const val NOT_SAME_WIFI = "Your phone isn't on the same Wi-Fi as your PC."

    /** wifi, cellular, ethernet, vpn, other or none (vpn = Tailscale or another VPN is on). */
    fun transport(ctx: Context): String = try {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        when {
            caps == null -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            else -> "other"
        }
    } catch (e: Exception) { "other" }

    fun host(url: String): String = url.substringAfter("//").substringBefore("/").let {
        if (it.startsWith("[")) it.substringAfter("[").substringBefore("]") else it.substringBefore(":")
    }

    /** An address only reachable inside a home network (10.x, 172.16-31.x, 192.168.x). */
    fun homeOnly(url: String): Boolean {
        val p = host(url).split(".").mapNotNull { it.toIntOrNull() }
        if (p.size != 4) return false
        return p[0] == 10 || (p[0] == 172 && p[1] in 16..31) || (p[0] == 192 && p[1] == 168)
    }

    fun tailscale(url: String): Boolean {
        val p = host(url).split(".").mapNotNull { it.toIntOrNull() }
        return p.size == 4 && p[0] == 100 && p[1] in 64..127
    }

    fun why(ctx: Context, url: String): String {
        val t = transport(ctx)
        return when {
            url.isEmpty() -> "This phone isn't paired with a PC yet."
            t == "none" -> "Your phone is offline. Turn on Wi-Fi."
            homeOnly(url) && t == "cellular" -> "$NOT_SAME_WIFI Turn on Wi-Fi and join your home network."
            tailscale(url) && t != "vpn" -> "Your PC is set up through Tailscale. Turn Tailscale on, on this phone."
            else -> "Can't reach your PC. Is it on and awake, and on the same Wi-Fi as this phone?"
        }
    }
}

/** One PC found on the Wi-Fi. */
data class FoundPc(val name: String, val host: String, val port: Int, val id: String) {
    val url: String get() = "http://" + (if (host.contains(":")) "[$host]" else host) + ":" + port
}

/**
 * Looks for "_orion._tcp" on the Wi-Fi. Android resolves one service at a time, so resolves are queued.
 * [onChange] runs on the main thread with every PC found so far.
 */
class PcFinder(context: Context, private val onChange: (List<FoundPc>) -> Unit) {
    private val nsd = context.applicationContext.getSystemService(NsdManager::class.java)
    private val ui = Handler(Looper.getMainLooper())
    private val found = LinkedHashMap<String, FoundPc>()
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false
    private var listener: NsdManager.DiscoveryListener? = null

    fun start() {
        if (listener != null || nsd == null) return
        val l = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(serviceType: String?, errorCode: Int) { listener = null }
            override fun onStopDiscoveryFailed(serviceType: String?, errorCode: Int) {}
            override fun onDiscoveryStarted(serviceType: String?) {}
            override fun onDiscoveryStopped(serviceType: String?) {}
            override fun onServiceFound(info: NsdServiceInfo) {
                synchronized(queue) { queue.addLast(info) }
                next()
            }
            override fun onServiceLost(info: NsdServiceInfo) {
                synchronized(found) { found.remove(info.serviceName) }
                publish()
            }
        }
        listener = l
        try { nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, l) } catch (e: Exception) { listener = null }
    }

    fun stop() {
        val l = listener ?: return
        listener = null
        try { nsd?.stopServiceDiscovery(l) } catch (_: Exception) { }
    }

    fun all(): List<FoundPc> = synchronized(found) { found.values.toList() }

    private fun next() {
        val info: NsdServiceInfo
        synchronized(queue) {
            if (resolving || queue.isEmpty()) return
            resolving = true
            info = queue.removeFirst()
        }
        try {
            nsd?.resolveService(info, object : NsdManager.ResolveListener {
                override fun onResolveFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) { done() }
                override fun onServiceResolved(s: NsdServiceInfo) {
                    val host = s.host
                    if (host != null && !(host is Inet6Address && host.isLinkLocalAddress)) {
                        val attrs = s.attributes ?: emptyMap()
                        fun attr(k: String) = attrs[k]?.toString(Charsets.UTF_8) ?: ""
                        val pc = FoundPc(attr("name").ifEmpty { s.serviceName }, host.hostAddress ?: "", s.port, attr("id"))
                        if (pc.host.isNotEmpty()) synchronized(found) { found[s.serviceName] = pc }
                        publish()
                    }
                    done()
                }
            })
        } catch (e: Exception) { done() }
    }

    private fun done() {
        synchronized(queue) { resolving = false }
        next()
    }

    private fun publish() {
        val list = all()
        ui.post { onChange(list) }
    }

    companion object {
        const val SERVICE = "_orion._tcp"
        private const val HEAL_EVERY_MS = 60_000L

        /** Waits up to [timeoutMs] for PCs (call from a background thread). With [wantId], stops at that PC. */
        fun findBlocking(ctx: Context, wantId: String?, timeoutMs: Long): List<FoundPc> {
            val latch = CountDownLatch(1)
            var finder: PcFinder? = null
            Handler(Looper.getMainLooper()).post {
                finder = PcFinder(ctx) { list -> if (wantId != null && list.any { it.id == wantId }) latch.countDown() }
                finder?.start()
            }
            latch.await(timeoutMs, TimeUnit.MILLISECONDS)
            val out = finder?.all() ?: emptyList()
            Handler(Looper.getMainLooper()).post { finder?.stop() }
            return out
        }

        /**
         * The PC's address changed (new Wi-Fi lease, other network)? Look for the same PC (by its id) on the Wi-Fi,
         * save its new address and check the pairing still works. At most once a minute. Background thread only.
         */
        fun heal(ctx: Context, lab: Lab, force: Boolean = false): Boolean {  // link: force = a network change
            val prefs = ctx.getSharedPreferences("orion", Context.MODE_PRIVATE)
            val id = prefs.getString(PcLink.KEY_PC_ID, "") ?: ""
            val now = System.currentTimeMillis()
            if (id.isEmpty() || !lab.paired || lab.urlOverride != null) return false
            if (!force && now - prefs.getLong("heal_at", 0L) < HEAL_EVERY_MS) return false  // link
            prefs.edit().putLong("heal_at", now).apply()
            if (NetWords.transport(ctx) == "cellular") return false
            // The id is announced in the clear, so anyone on the Wi-Fi can copy it: only switch to a PC that proves
            // it holds the key pinned at pairing (it signs a fresh random nonce in its hello).
            if (!PcKey.pinned(prefs)) return false
            val pc = findBlocking(ctx, id, 5000).firstOrNull { it.id == id } ?: return false
            if (pc.url == lab.labUrl) return false
            val old = lab.labUrl
            lab.labUrl = pc.url
            return try { PcLink.hello(ctx, lab, mustProve = true); true } catch (e: Exception) { lab.labUrl = old; false }
        }
    }
}

/**
 * The PC's own key (ECDSA P-256), pinned when the phone pairs. The pairing answer carries it with an HMAC made from
 * the pairing code, so only the PC that showed the code could have sent it. java.security only, no libraries.
 */
object PcKey {
    const val KEY_PC_KEY = "pc_key"
    private const val PAIR_LABEL = "orion-pc-key\n"
    private const val HELLO_LABEL = "orion-pc-hello\n"

    fun pinned(prefs: android.content.SharedPreferences): Boolean = !prefs.getString(KEY_PC_KEY, "").isNullOrEmpty()

    /** After /phone/pair: check the proof with the typed code and pin the key. A wrong proof stops the pairing. */
    fun fromPairing(prefs: android.content.SharedPreferences, normCode: String, res: JSONObject) {
        val keyB64 = res.optString("pc_key")
        if (keyB64.isEmpty()) { prefs.edit().remove(KEY_PC_KEY).apply(); return }   // an older PC: nothing to pin
        val der = try { android.util.Base64.decode(keyB64, android.util.Base64.NO_WRAP) } catch (e: Exception) {
            throw LabError("The PC sent a broken key. Don't confirm it on the PC.")
        }
        val mac = javax.crypto.Mac.getInstance("HmacSHA256")
        mac.init(javax.crypto.spec.SecretKeySpec(normCode.toByteArray(), "HmacSHA256"))
        val want = mac.doFinal(PAIR_LABEL.toByteArray() + der).joinToString("") { "%02x".format(it) }
        if (!java.security.MessageDigest.isEqual(want.toByteArray(), res.optString("pc_key_proof").lowercase().toByteArray()))
            throw LabError("The PC's key doesn't match the code. Don't confirm it on the PC.")
        prefs.edit().putString(KEY_PC_KEY, keyB64).apply()
    }

    fun nonce(): String {
        val b = ByteArray(24)
        java.security.SecureRandom().nextBytes(b)
        return android.util.Base64.encodeToString(b, android.util.Base64.NO_WRAP or android.util.Base64.URL_SAFE)
    }

    /** True when [sigB64] is the pinned PC key's signature over this hello. With no pinned key yet, pins [keyB64]
     *  once its own signature checks out (a phone paired before PCs had keys). */
    fun check(prefs: android.content.SharedPreferences, pcId: String, nonce: String, keyB64: String, sigB64: String): Boolean {
        if (pcId.isEmpty() || sigB64.isEmpty()) return false
        val pinned = prefs.getString(KEY_PC_KEY, "") ?: ""
        val use = pinned.ifEmpty { keyB64 }
        if (use.isEmpty()) return false
        val ok = verify(use, (HELLO_LABEL + pcId + "\n" + nonce).toByteArray(), sigB64)
        if (ok && pinned.isEmpty()) prefs.edit().putString(KEY_PC_KEY, use).apply()
        return ok
    }

    private fun verify(keyB64: String, msg: ByteArray, sigB64: String): Boolean = try {
        val der = android.util.Base64.decode(keyB64, android.util.Base64.NO_WRAP)
        val pub = java.security.KeyFactory.getInstance("EC").generatePublic(java.security.spec.X509EncodedKeySpec(der))
        val v = java.security.Signature.getInstance("SHA256withECDSA")
        v.initVerify(pub)
        v.update(msg)
        v.verify(android.util.Base64.decode(sigB64, android.util.Base64.NO_WRAP))
    } catch (e: Exception) { false }
}

/** The phone's hello to its PC: battery, network, app version. Saves the PC's id so it can be found again. */
object PcLink {
    const val KEY_PC_ID = "pc_id"
    const val KEY_PC_NAME = "pc_name"

    /**
     * [mustProve]: the answer must be signed by the PC key pinned at pairing (used before switching to a new
     * address). Otherwise an unsigned answer is accepted (an older PC), and a PC with no pinned key yet gets its
     * key pinned when its signature checks out.
     */
    fun hello(ctx: Context, lab: Lab, mustProve: Boolean = false): JSONObject {
        val prefs = ctx.getSharedPreferences("orion", Context.MODE_PRIVATE)
        val nonce = PcKey.nonce()
        val body = JSONObject().put("network", NetWords.transport(ctx)).put("nonce", nonce)
        try {
            val bm = ctx.getSystemService(BatteryManager::class.java)
            val pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            if (pct in 0..100) body.put("battery", pct)
            body.put("charging", bm.isCharging)
        } catch (_: Exception) { }
        try { body.put("app", ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "") } catch (_: Exception) { }
        Link.helloFacts(ctx, body, lab.labUrl)  // link: round trip, retries, key pinned (the PC's Phone panel shows it)
        val r = lab.readJson("POST", "/phone/hello", body.toString().toByteArray(), timeoutMs = 8000)
        val pc = r.optJSONObject("pc")
        val proven = PcKey.check(prefs, pc?.optString("id") ?: "", nonce, r.optString("pc_key"), r.optString("pc_sig"))
        if (mustProve && !proven) throw LabError("That isn't your PC: it couldn't prove it holds your PC's key.")
        Link.saveAddresses(ctx, r, proven, lab.labUrl)  // link: its home and Tailscale addresses, for later
        if (pc != null && pc.optString("id").isNotEmpty()) {
            prefs.edit()
                .putString(KEY_PC_ID, pc.optString("id")).putString(KEY_PC_NAME, pc.optString("name")).apply()
        }
        return r
    }

    fun pcName(ctx: Context): String =
        ctx.getSharedPreferences("orion", Context.MODE_PRIVATE).getString(KEY_PC_NAME, "") ?: ""
}

/**
 * The pairing screen. Opened from the menu (Settings > Pair with my PC), when the phone isn't paired yet, or by
 * the orion://pair link from the PC's QR code (MainActivity forwards it here with [EXTRA_URL] and [EXTRA_CODE]).
 */
class PairPcActivity : Activity() {
    private lateinit var lab: Lab
    private val ui = Handler(Looper.getMainLooper())
    private var finder: PcFinder? = null
    private var waiting = false
    private var oldUrl = ""
    private var oldPaired = false
    private var confirmed = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lab = Lab(this)
        oldUrl = lab.labUrl; oldPaired = lab.paired
        val data = intent.data
        val u = intent.getStringExtra(EXTRA_URL) ?: data?.getQueryParameter("u") ?: ""
        val c = intent.getStringExtra(EXTRA_CODE) ?: data?.getQueryParameter("c") ?: ""
        if (u.isNotEmpty() && c.isNotEmpty()) pair(u, c, fromLink = true) else choose()
    }

    override fun onDestroy() {
        finder?.stop(); waiting = false
        if (!confirmed && oldPaired && lab.labUrl != oldUrl) lab.labUrl = oldUrl   // cancelled: keep the old PC
        super.onDestroy()
    }

    // ---------------------------------------------------------------- 1 + 2: pick your PC, type the code
    private fun choose(prefill: String = "") {
        finder?.stop()
        val col = SysUi.page(this, "Pair with my PC", "On your PC, open Orion's menu and choose Phone. It shows a code.")
        val net = NetWords.transport(this)
        if (net == "cellular" || net == "none") col.addView(warn(
            if (net == "none") "Your phone is offline. Turn on Wi-Fi." else "${NetWords.NOT_SAME_WIFI} Turn on Wi-Fi and join the same network as your PC."))

        col.addView(SysUi.section(this, "1. Your PC"))
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val searching = SysUi.note(this, "Looking for PCs on this Wi-Fi…")
        col.addView(list); col.addView(searching)
        var picked = prefill
        val address = SysUi.input(this, "PC address, e.g. http://192.168.1.20:9121", prefill).apply { visibility = android.view.View.GONE }
        val showAddress = SysUi.pill(this, "Type the address instead") { address.visibility = android.view.View.VISIBLE }
        col.addView(showAddress); col.addView(address)

        col.addView(SysUi.section(this, "2. The code on your PC"))
        val code = SysUi.input(this, "Code, e.g. ABCD-EFGH-…", "").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        }
        col.addView(code)
        val status = SysUi.note(this, "")
        col.addView(SysUi.button(this, "Pair", primary = true) {
            val url = if (address.visibility == android.view.View.VISIBLE && address.text.isNotBlank()) normal(address.text.toString()) else picked
            val c = code.text.toString().trim()
            when {
                url.isEmpty() -> { status.setTextColor(SysUi.amber); status.text = "Tap your PC above first (or type its address)." }
                c.length < 6 -> { status.setTextColor(SysUi.amber); status.text = "Type the code your PC shows." }
                else -> pair(url, c, fromLink = false)
            }
        })
        col.addView(status)
        col.addView(SysUi.note(this, "Quicker: point your phone's camera at the QR code on your PC and tap the link."))

        fun render(pcs: List<FoundPc>) {
            list.removeAllViews()
            for (pc in pcs) {
                val on = pc.url == picked
                list.addView(SysUi.button(this, (if (on) "✓  " else "") + pc.name + "   " + pc.host, primary = on) {
                    picked = pc.url; address.visibility = android.view.View.GONE; render(finder?.all() ?: pcs); code.requestFocus()
                })
            }
            if (pcs.size == 1 && picked.isEmpty()) { picked = pcs[0].url; render(pcs); return }
            searching.text = if (pcs.isEmpty()) "Looking for PCs on this Wi-Fi…" else "Tap your PC."
        }
        finder = PcFinder(this) { render(it) }.also { it.start() }
        ui.postDelayed({
            if (!isFinishing && (finder?.all() ?: emptyList()).isEmpty()) {
                searching.text = "No PC found yet. Check your phone is on the same Wi-Fi as your PC, and that Orion's " +
                    "Phone panel is open on the PC. Or type the address instead."
                searching.setTextColor(SysUi.amber)
            }
        }, 8000)
    }

    private fun normal(s: String): String {
        var u = s.trim().trimEnd('/')
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        if (u.substringAfter("//").count { it == ':' } == 0) u += ":9121"
        return u
    }

    // ---------------------------------------------------------------- send the keys
    private fun pair(url: String, code: String, fromLink: Boolean) {
        finder?.stop()
        val col = SysUi.page(this, "Pairing…", "Talking to your PC.")
        val status = SysUi.note(this, "")
        col.addView(status)
        Thread {
            try {
                val fp = lab.pair(url, code, Build.MODEL ?: "Android phone")
                ui.post { confirm(fp) }
            } catch (e: Exception) {
                val words = plain(e, url)
                ui.post {
                    val c2 = SysUi.page(this, "Pair with my PC", "")
                    c2.addView(warn(words))
                    c2.addView(SysUi.button(this, "Try again", primary = true) { choose(prefill = url) })
                }
            }
        }.start()
    }

    private fun plain(e: Exception, url: String): String {
        val m = e.message ?: e.toString()
        return when {
            m.contains("wrong pairing code") -> "That code doesn't match. Check the code on your PC and try again."
            m.contains("expired") || m.contains("not open") -> "Pairing isn't open on your PC. On the PC, open Orion's menu > Phone first."
            m.contains("too many wrong") -> "Too many wrong codes, so the PC closed pairing. Press Pair my phone on the PC again."
            m.contains("Can't reach") -> NetWords.why(this, url)
            e is IllegalStateException -> m   // "Set up a fingerprint (or face unlock) on this phone first..."
            else -> m
        }
    }

    // ---------------------------------------------------------------- 3: compare the words, confirm on the PC
    private fun confirm(fp: String) {
        val col = SysUi.page(this, "Do these words match your PC?", "Your PC shows five words too. They must be the same.")
        col.addView(TextView(this).apply {
            text = FpWords.of(fp).replace(" ", "\n"); setTextColor(SysUi.cyan); typeface = Typeface.DEFAULT_BOLD
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 30f); gravity = Gravity.CENTER; setPadding(0, dp(18), 0, dp(10))
            setLineSpacing(0f, 1.15f)
        })
        col.addView(TextView(this).apply {
            text = fp; setTextColor(SysUi.quiet); typeface = Typeface.MONOSPACE; gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f); setPadding(0, 0, 0, dp(16))
        })
        val status = SysUi.text(this, "If they match, press Confirm on your PC. This screen moves on by itself.", 15f, SysUi.ink)
        col.addView(status)
        col.addView(SysUi.note(this, "If they don't match, press Cancel on your PC: something on the network swapped the key."))
        col.addView(SysUi.button(this, "Cancel", primary = false) { waiting = false; finish() })
        waiting = true
        val started = System.currentTimeMillis()
        Thread {
            while (waiting && System.currentTimeMillis() - started < 10 * 60_000L) {
                try {
                    PcLink.hello(this, lab)
                    ui.post { done() }
                    return@Thread
                } catch (e: Exception) {
                    val m = e.message ?: ""
                    if (!m.contains("unknown or unpaired") && !m.contains("Can't reach")) ui.post { status.text = m }
                }
                try { Thread.sleep(2000) } catch (_: InterruptedException) { return@Thread }
            }
            if (waiting) ui.post { status.setTextColor(SysUi.amber); status.text = "Your PC didn't confirm in 10 minutes. Start again on the PC." }
        }.start()
    }

    private fun done() {
        if (!waiting) return
        waiting = false; confirmed = true
        lab.paired = true
        PollJob.schedule(this)
        if (LiveService.enabled(this)) LiveService.start(this)
        val name = PcLink.pcName(this).ifEmpty { "your PC" }
        val col = SysUi.page(this, "Paired", "This phone and $name are connected.")
        col.addView(SysUi.button(this, "Test the connection", primary = true) {
            startActivity(Intent(this, PhoneTestActivity::class.java)); finish()
        })
        col.addView(SysUi.button(this, "Done", primary = false) {
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); finish()
        })
    }

    private fun warn(s: String) = SysUi.card(this).apply {
        addView(SysUi.text(this@PairPcActivity, s, 15f, SysUi.amber))
    }

    private fun dp(v: Int) = SysUi.dp(this, v)

    companion object {
        const val EXTRA_URL = "pair_url"
        const val EXTRA_CODE = "pair_code"

        fun open(c: Context, url: String = "", code: String = "") {
            c.startActivity(Intent(c, PairPcActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).apply {
                if (url.isNotEmpty()) putExtra(EXTRA_URL, url)
                if (code.isNotEmpty()) putExtra(EXTRA_CODE, code)
            })
        }
    }
}
