package com.orion.mobile

import android.app.Activity
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.util.TypedValue
import android.widget.LinearLayout
import org.json.JSONArray
import org.json.JSONObject

/*
 * Test (the menu's fifth item): one tap checks the whole PC <-> phone connection.
 *
 * The phone checks itself first (fingerprint key, notification access, assistant, PC reachable, Pocket Orion,
 * battery optimisation), then reads the PC's checklist: GET /phone/tests (read key) ->
 *   {"groups":[{"id","title","items":[{"id","title","where","kind","status","detail","next"}]}],"next": item|null,"summary":{...}}
 * Results of phone-side items go back with POST /phone/tests/{id}/result {"status","note"?,"facts"?}. Then one
 * "Next" card with one big "Do it for me" button. A PC without /phone/tests still gets the phone's own checks,
 * plus "Update Orion on your PC".
 *
 * "Do it for me" opens the exact Android page that fixes a phone check (notification access for Orion, the
 * assistant and voice input page, the battery "allow" prompt). Coming back (onResume) checks again by itself and
 * posts the results straight away, so the Next card moves on without a tap. For PC items that need this phone
 * (approve the test card, freeze, send a mission, draft a reply) it opens the right screen here and then watches
 * the PC's checklist every few seconds until the PC sees it happen. Approving and unfreezing still need your
 * fingerprint in the normal screens: this page never approves anything.
 */

/** One phone-side check. [fix] opens the page that fixes it (null when there is nothing to open). */
data class PhoneCheck(val id: String, val title: String, val status: String, val detail: String,
                      val fixLabel: String = "", val fix: ((Activity) -> Unit)? = null, val optional: Boolean = false) {
    val ok get() = status == "pass"
}

object PhoneChecks {
    /** The PC checklist item each phone check fills in (exact ids from the PC's test_center.py). */
    private val MATCH = mapOf(
        "key" to listOf("phone_fingerprint_key"),
        "notifications" to listOf("phone_notifications"),
        "assistant" to listOf("phone_assistant"),
        "pc" to listOf("phone_reaches"),
        "battery" to listOf("phone_battery"),
        // "pocket" is checked on the phone only: being paired with Pocket Orion isn't the PC's
        // "Pocket Orion answers when the PC is off" test, which you do by hand.
    )

    /** Runs every phone check (background thread: one of them talks to the PC). */
    fun run(ctx: Context, lab: Lab): List<PhoneCheck> {
        val out = mutableListOf<PhoneCheck>()
        val keyOk = try { Keys.exists() } catch (e: Exception) { false }
        out += PhoneCheck("key", "Fingerprint key", if (keyOk) "pass" else "fail",
            if (keyOk) "Approvals on this phone need your fingerprint." else "No approval key yet. Pair with your PC to make one.",
            "Pair with my PC", { a -> PairPcActivity.open(a) })

        val notif = try {
            ctx.getSystemService(NotificationManager::class.java).isNotificationListenerAccessGranted(InboxListener.component(ctx))
        } catch (e: Exception) { false }
        out += PhoneCheck("notifications", "Notification access", if (notif) "pass" else "fail",
            if (notif) "Your inbox fills from your messaging apps." else "Needed for the one inbox. Find Orion on the next page and turn it on.",
            "Do it for me: open notification access", { a ->
                try {
                    a.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                        .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, InboxListener.component(a).flattenToString()))
                } catch (e: Exception) { open(a, "android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS") }
            })

        val assistant = try {
            val rm = ctx.getSystemService(RoleManager::class.java)
            rm.isRoleAvailable(RoleManager.ROLE_ASSISTANT) && rm.isRoleHeld(RoleManager.ROLE_ASSISTANT)
        } catch (e: Exception) { false }
        out += PhoneCheck("assistant", "Assistant (hold power to talk)", if (assistant) "pass" else "fail",
            if (assistant) "Long-press power talks to Orion." else "Pick Orion under Default apps > Digital assistant app.",
            "Do it for me: open the assistant page", { a -> open(a, Settings.ACTION_VOICE_INPUT_SETTINGS, Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS) })

        out += pcCheck(ctx, lab)

        val pocketOn = PocketFallback.enabled(ctx) && PocketFallback.paired(ctx)
        val pocketUp = pocketOn && PocketFallback.pocketUp()
        out += PhoneCheck("pocket", "Pocket Orion (when the PC is asleep)",
            when { pocketUp -> "pass"; pocketOn -> "fail"; else -> "skip" },
            when {
                pocketUp -> "Orion on this phone answers when your PC is off."
                pocketOn -> "Set up but not answering. Start it in the Terminal app."
                else -> "Optional. Lets chat and voice work while your PC sleeps."
            }, "Phone setup", { a -> a.startActivity(Intent(a, PhoneSetupActivity::class.java)) }, optional = true)

        val battOk = try { ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName) } catch (e: Exception) { false }
        out += PhoneCheck("battery", "Battery saver lets Orion run", if (battOk) "pass" else "fail",
            if (battOk) "Alerts arrive on time." else "Android may hold back alerts. On the next page, set Orion to Unrestricted (or Don't optimise).",
            "Do it for me: let Orion run", { a ->
                try {   // the direct "Allow" prompt for Orion only; the list of apps if this phone doesn't offer it
                    a.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${a.packageName}")))
                } catch (e: Exception) { open(a, Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS) }
            })
        return out
    }

    private fun pcCheck(ctx: Context, lab: Lab): PhoneCheck {
        val fix: (Activity) -> Unit = { a -> PairPcActivity.open(a) }
        if (!lab.paired) return PhoneCheck("pc", "PC reachable", "fail", "This phone isn't paired with a PC yet.", "Pair with my PC", fix)
        return try {
            PcLink.hello(ctx, lab)
            PhoneCheck("pc", "PC reachable", "pass", "Connected to ${PcLink.pcName(ctx).ifEmpty { "your PC" }}.")
        } catch (e: Exception) {
            val m = e.message ?: ""
            when {
                m.contains("unknown or unpaired") ->
                    PhoneCheck("pc", "PC reachable", "fail", "Your PC doesn't know this phone any more. Pair again.", "Pair with my PC", fix)
                PcFinder.heal(ctx, lab) ->   // the PC moved to a new address: found it again on the Wi-Fi
                    PhoneCheck("pc", "PC reachable", "pass", "Your PC's address changed; found it again at ${lab.labUrl}.")
                m.contains("404") || m.contains("Not Found") ->
                    PhoneCheck("pc", "PC reachable", "pass", "Connected. (Update Orion on your PC for the full test.)")
                else -> {
                    val pocket = if (PocketFallback.enabled(ctx) && PocketFallback.paired(ctx)) " Pocket Orion on this phone answers meanwhile." else ""
                    PhoneCheck("pc", "PC reachable", "fail", NetWords.why(ctx, lab.labUrl) + pocket, "Fix the connection",
                        { a -> LinkDoctor.show(a) })  // link
                }
            }
        }
    }

    /** PC checklist items done on this phone: the screen that does them (the PC watches for the result). */
    val PC_ON_PHONE: Map<String, Pair<String, (Activity) -> Unit>> = mapOf(
        "approve_card" to ("Do it for me: open the card" to { a: Activity ->
            a.startActivity(Intent(a, MainActivity::class.java).putExtra(Alerts.EXTRA_KIND, "approval")) }),
        "freeze_phone" to ("Do it for me: open Freeze" to { a: Activity -> a.startActivity(Intent(a, MainActivity::class.java)) }),
        "unfreeze_phone" to ("Do it for me: open Unfreeze" to { a: Activity -> a.startActivity(Intent(a, MainActivity::class.java)) }),
        "send_work" to ("Do it for me: open Missions" to { a: Activity -> MissionsActivity.open(a) }),
        "phone_draft" to ("Do it for me: open the inbox" to { a: Activity -> a.startActivity(Intent(a, InboxActivity::class.java)) }),
    )

    /** Which phone check (if any) a PC checklist item is about. */
    fun matchFor(item: JSONObject): String? {
        val where = item.optString("where").lowercase()
        if (where.isNotEmpty() && where != "phone" && where != "both") return null
        val id = item.optString("id").lowercase()
        return MATCH.entries.firstOrNull { (_, ids) -> id in ids }?.key
    }

    fun open(a: Activity, vararg actions: String) {
        for (act in actions) {
            try { a.startActivity(Intent(act)); return } catch (_: Exception) { }
        }
        try { a.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${a.packageName}"))) } catch (_: Exception) { }
    }
}

class PhoneTestActivity : Activity() {
    private lateinit var lab: Lab
    private val ui = Handler(Looper.getMainLooper())
    private var busy = false
    private var resumed = false
    private var lastChecks: List<PhoneCheck> = emptyList()
    private var lastPc: JSONObject? = null
    private var lastPcNote = ""
    private var seen: Map<String, String> = emptyMap()   // item id -> status at the last look (for "That worked")
    private var flash = ""

    /** While the Next card waits for the PC to see something, look at the PC's checklist every few seconds. */
    private val watch = object : Runnable {
        override fun run() {
            if (!resumed || busy || !lab.paired) return
            Thread {
                val pc = try { lab.readJson("GET", "/phone/tests") } catch (_: Exception) { null }
                ui.post {
                    if (pc != null && resumed && !busy) show(lastChecks, pc, lastPcNote)
                    else if (resumed) ui.postDelayed(this, WATCH_MS)
                }
            }.start()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lab = Lab(this)
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        runAll()   // back from a settings page: check again, post the results, move the Next card on
    }

    override fun onPause() {
        super.onPause()
        resumed = false
        ui.removeCallbacks(watch)
    }

    private fun runAll() {
        if (busy) return
        busy = true
        ui.removeCallbacks(watch)
        val col = SysUi.page(this, "Test", "Checking this phone and your PC…")
        col.addView(SysUi.note(this, "This takes a few seconds."))
        Thread {
            val checks = PhoneChecks.run(this, lab)
            var pc: JSONObject? = null
            var pcNote = ""
            val pcReached = checks.firstOrNull { it.id == "pc" }?.ok == true
            if (pcReached) {
                try {
                    pc = lab.readJson("GET", "/phone/tests")
                    if (report(pc, checks)) pc = lab.readJson("GET", "/phone/tests")   // "next" reflects what was just sent
                } catch (e: Exception) {
                    val m = e.message ?: ""
                    pcNote = if (m.contains("404") || m.contains("Not Found")) "Update Orion on your PC to see the PC's half of the test."
                    else "The PC's checklist didn't load: $m"
                }
            }
            ui.post { busy = false; show(checks, pc, pcNote) }
        }.start()
    }

    /** POST /phone/tests/{id}/result for every PC item one of the phone's checks answers, when it changed.
     *  True when anything was sent. */
    private fun report(pc: JSONObject, checks: List<PhoneCheck>): Boolean {
        val byId = checks.associateBy { it.id }
        val facts = JSONObject()
        checks.forEach { facts.put(it.id, it.status) }
        facts.put("network", NetWords.transport(this))
        var sent = false
        for (item in items(pc)) {
            val id = item.optString("id")
            val pcStatus = item.optString("status").lowercase()
            val c = PhoneChecks.matchFor(item)?.let { byId[it] }
            val body = when {
                c != null && c.status != pcStatus && c.status in setOf("pass", "fail", "skip") ->
                    JSONObject().put("status", c.status).put("note", c.detail).put("facts", facts)
                // Pocket Orion isn't set up on this phone: its PC test doesn't apply
                id == "pocket_orion" && pcStatus == "todo" && byId["pocket"]?.status == "skip" ->
                    JSONObject().put("status", "skip").put("note", "Pocket Orion isn't set up on this phone.")
                else -> null
            } ?: continue
            try { lab.readJson("POST", "/phone/tests/${Uri.encode(id)}/result", body.toString().toByteArray()); sent = true }
            catch (_: Exception) { }
        }
        return sent
    }

    private fun items(pc: JSONObject): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        val groups = pc.optJSONArray("groups") ?: JSONArray()
        for (g in 0 until groups.length()) {
            val its = groups.optJSONObject(g)?.optJSONArray("items") ?: continue
            for (i in 0 until its.length()) its.optJSONObject(i)?.let { out += it }
        }
        return out
    }

    private fun show(checks: List<PhoneCheck>, pc: JSONObject?, pcNote: String) {
        lastChecks = checks; lastPc = pc; lastPcNote = pcNote
        // what just started working since the last look (not on the first look)
        val now = mutableMapOf<String, String>()
        val titles = mutableMapOf<String, String>()
        checks.forEach { now["phone:" + it.id] = it.status; titles["phone:" + it.id] = it.title }
        if (pc != null) items(pc).forEach { now[it.optString("id")] = it.optString("status"); titles[it.optString("id")] = it.optString("title") }
        if (seen.isNotEmpty()) {
            val passed = now.filter { (k, v) -> v == "pass" && seen[k] != null && seen[k] != "pass" }.keys
            if (passed.isNotEmpty()) flash = if (passed.size == 1) "That worked: ${titles[passed.first()]}" else "${passed.size} more things work."
        }
        seen = now

        val bad = checks.count { !it.ok && !it.optional }
        val summary = pc?.optJSONObject("summary")
        val sub = when {
            summary?.optString("words")?.isNotEmpty() == true -> summary.optString("words")
            bad == 0 -> "Everything on this phone is ready."
            bad == 1 -> "One thing to fix on this phone."
            else -> "$bad things to fix on this phone."
        }
        val col = SysUi.page(this, "Test", sub)
        if (flash.isNotEmpty()) {
            col.addView(SysUi.card(this).apply { addView(SysUi.text(this@PhoneTestActivity, flash, 15f, SysUi.green)) })
            val shown = flash
            ui.postDelayed({ if (flash == shown) flash = "" }, 4000)
        }
        val watching = nextCard(col, checks, pc)
        if (pcNote.isNotEmpty()) col.addView(SysUi.card(this).apply { addView(SysUi.text(this@PhoneTestActivity, pcNote, 14f, SysUi.amber)) })

        col.addView(SysUi.section(this, "This phone"))
        for (c in checks) col.addView(line(c.title, c.status, c.detail))
        if (pc != null) {
            val groups = pc.optJSONArray("groups") ?: JSONArray()
            for (g in 0 until groups.length()) {
                val grp = groups.optJSONObject(g) ?: continue
                col.addView(SysUi.section(this, grp.optString("title")))
                val its = grp.optJSONArray("items") ?: JSONArray()
                for (i in 0 until its.length()) {
                    val it = its.optJSONObject(i) ?: continue
                    col.addView(line(it.optString("title"), it.optString("status"), it.optString("detail")))
                }
            }
        }
        col.addView(SysUi.button(this, "Run the test again", primary = false) { runAll() })
        ui.removeCallbacks(watch)
        if (watching && resumed) ui.postDelayed(watch, WATCH_MS)
    }

    /** One card, one big button: the single next thing to do. True when it waits for the PC to see something. */
    private fun nextCard(col: LinearLayout, checks: List<PhoneCheck>, pc: JSONObject?): Boolean {
        val card = SysUi.card(this)
        card.addView(SysUi.section(this, "Next"))
        val pcNext = pc?.optJSONObject("next")
        val local = checks.firstOrNull { !it.ok && !it.optional }
        val pcReachFail = checks.firstOrNull { it.id == "pc" && !it.ok }
        var watching = false
        when {
            pcReachFail != null -> nextLocal(card, pcReachFail)
            local != null -> nextLocal(card, local)          // this phone's own fixes first: one tap each
            pcNext != null -> {
                val id = pcNext.optString("id")
                val where = when (pcNext.optString("where").lowercase()) { "pc" -> "On your PC"; "phone" -> "On this phone"; else -> "On this phone and your PC" }
                card.addView(big(pcNext.optString("title")))
                card.addView(SysUi.note(this, where))
                val detail = pcNext.optString("next").ifEmpty { pcNext.optString("detail") }
                if (detail.isNotEmpty()) card.addView(SysUi.text(this, detail))
                val onPhone = PhoneChecks.PC_ON_PHONE[id]
                val manual = pcNext.optString("kind").lowercase() == "manual"
                val pcSide = pcNext.optString("where").lowercase() == "pc"
                when {
                    onPhone != null -> {
                        card.addView(SysUi.button(this, onPhone.first, primary = true) { onPhone.second.invoke(this) })
                        card.addView(SysUi.note(this, "Orion's PC sees it happen and this card moves on by itself."))
                        watching = true
                    }
                    pcSide -> {
                        card.addView(SysUi.note(this, "Do this one on your PC's Test Center. This card moves on by itself."))
                        watching = true
                    }
                    manual -> {
                        card.addView(SysUi.button(this, "It worked", primary = true) { manual(pcNext, "pass") })
                        card.addView(SysUi.pill(this, "It didn't work", SysUi.quiet) { manual(pcNext, "fail") })
                    }
                    else -> { card.addView(SysUi.button(this, "Check again", primary = true) { runAll() }); watching = true }
                }
                if (manual && onPhone != null) {
                    val row = SysUi.row(this)
                    row.addView(SysUi.pill(this, "It didn't work", SysUi.quiet) { manual(pcNext, "fail") })
                    row.addView(SysUi.pill(this, "Skip", SysUi.quiet) { manual(pcNext, "skip") })
                    card.addView(row)
                } else if (!pcSide) {
                    card.addView(SysUi.pill(this, "Skip", SysUi.quiet) { manual(pcNext, "skip") })
                }
            }
            else -> {
                card.addView(big("All done"))
                card.addView(SysUi.text(this, if (pc?.optBoolean("all_done") == true) "Everything worked or was skipped. Your phone and your PC are ready."
                                              else "Your phone and your PC are connected."))
                card.addView(SysUi.button(this, "Back", primary = true) { finish() })
            }
        }
        col.addView(card)
        return watching
    }

    private fun nextLocal(card: LinearLayout, c: PhoneCheck) {
        card.addView(big(c.title))
        card.addView(SysUi.note(this, "On this phone"))
        card.addView(SysUi.text(this, c.detail))
        val f = c.fix
        if (f != null) {
            card.addView(SysUi.button(this, c.fixLabel.ifEmpty { "Do it for me" }, primary = true) { f(this) })
            card.addView(SysUi.note(this, "When you come back, this phone checks again by itself."))
        } else card.addView(SysUi.button(this, "Check again", primary = true) { runAll() })
    }

    private fun manual(item: JSONObject, status: String) {
        Thread {
            try {
                lab.readJson("POST", "/phone/tests/${Uri.encode(item.optString("id"))}/result",
                    JSONObject().put("status", status).toString().toByteArray())
            } catch (_: Exception) { }
            ui.post { runAll() }
        }.start()
    }

    private fun big(s: String) = SysUi.title(this, s).apply { setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f); typeface = Typeface.DEFAULT_BOLD }

    private fun line(title: String, status: String, detail: String) = SysUi.card(this).apply {
        val head = SysUi.row(this@PhoneTestActivity)
        head.addView(SysUi.weighted(SysUi.title(this@PhoneTestActivity, title)))
        val (word, color) = when (status.lowercase()) {
            "pass", "ok", "done" -> "OK" to SysUi.green
            "fail", "failed", "error" -> "Fix" to SysUi.red
            "skip", "skipped", "optional" -> "Optional" to SysUi.quiet
            "warn" -> "Check" to SysUi.amber
            "running" -> "Checking" to SysUi.amber
            else -> "Waiting" to SysUi.quiet
        }
        head.addView(SysUi.text(this@PhoneTestActivity, word, 14f, color))
        addView(head)
        if (detail.isNotEmpty()) addView(SysUi.note(this@PhoneTestActivity, detail))
    }

    companion object { const val WATCH_MS = 3000L }
}
