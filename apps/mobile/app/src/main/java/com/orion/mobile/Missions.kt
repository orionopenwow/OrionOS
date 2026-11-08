package com.orion.mobile

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG
import android.hardware.biometrics.BiometricPrompt
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.security.Signature

/*
 * Send work to the PC: Mission Control from the phone.
 *
 *   GET  /phone/missions               read key   the board (state, cost, budget)
 *   POST /phone/mission                read key   queue a new mission (the PC never merges anything from this)
 *   POST /phone/mission/{id}/{op}      pause / resume / stop: read key
 *                                      approve_review / reject: fingerprint key only (BiometricPrompt below)
 *
 * Money rule (PC side, phone_missions.py): with the read key one mission can have up to $5 and all open read-key
 * missions together up to $5. Over that the PC says it "needs your fingerprint"; the phone then asks for the
 * fingerprint and sends the same request signed with the approval key. Stopping work that waits for review is
 * refused (that's Reject, with the fingerprint), so the board shows Reject instead of Stop there.
 *
 * Missions always go to the PC, never to Pocket Orion. Opened from the home screen (long-press the sky, or tap a
 * mission robot), from the share sheet ("Send work to PC") and from Phone setup. Saying "Orion, have the PC
 * build ..." does the same through the PC's chat and voice routes (apps/daemon/phone_missions.py).
 */

/** One mission on the PC's board, as GET /phone/missions sends it. */
data class Mission(
    val id: String,
    val title: String,
    val repo: String,
    val agent: String,
    val state: String,
    val costUsd: Double?,
    val budgetUsd: Double?,
    val branch: String,
    val summary: String,
) {
    private val s get() = state.lowercase()
    val finished get() = s in listOf("done", "merged", "failed", "stopped", "rejected", "cancelled", "canceled")
    val needsReview get() = "review" in s
    val paused get() = s == "paused"

    /** "Spent $1.20 of $5.00", or "" when the PC sent no cost. */
    fun spent(): String {
        val c = costUsd ?: return if (budgetUsd != null) "Budget ${usd(budgetUsd)}" else ""
        return "Spent ${usd(c)}" + (budgetUsd?.let { " of ${usd(it)}" } ?: "")
    }

    companion object {
        fun usd(v: Double) = String.format("$%,.2f", v)

        fun from(o: JSONObject) = Mission(
            id = o.optString("id"), title = o.optString("title"), repo = o.optString("repo"),
            agent = o.optString("agent"), state = o.optString("state"),
            costUsd = num(o, "cost_usd"), budgetUsd = num(o, "budget_usd"),
            branch = o.optString("branch"), summary = o.optString("summary"),
        )

        private fun num(o: JSONObject, k: String): Double? =
            if (!o.has(k) || o.isNull(k)) null else o.optDouble(k).takeIf { !it.isNaN() }
    }
}

/** The PC wants this request signed with the fingerprint key (the money rule above). */
class NeedsFingerprint(message: String) : Exception(message)

/** The network calls (call from a background thread). */
class MissionsApi(context: Context) {
    val lab = Lab(context.applicationContext)

    class Board(val available: Boolean, val missions: List<Mission>, val message: String)

    fun board(): Board {
        val j = older { lab.readJson("GET", "/phone/missions") }
        val a = j.optJSONArray("missions") ?: JSONArray()
        val list = (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(Mission::from) }.filter { it.id.isNotEmpty() }
        return Board(j.optBoolean("available", true), list, j.optString("message"))
    }

    /** Queues a mission on the PC. Returns the PC's own words. */
    fun create(title: String, repo: String?, budgetUsd: Double?): String {
        val b = JSONObject().put("title", title)
        if (!repo.isNullOrBlank()) b.put("repo", repo.trim())
        if (budgetUsd != null) b.put("budget_usd", budgetUsd)
        val r = older { money { lab.readJson("POST", "/phone/mission", b.toString().toByteArray()) } }
        return r.optString("message").ifEmpty { "Sent to your PC." }
    }

    /** A budget over $5 (or over the $5 open total) is spending money: signed with the fingerprint key ([unlocked]). */
    fun createSigned(title: String, repo: String?, budgetUsd: Double?, unlocked: Signature): String {
        val b = JSONObject().put("title", title)
        if (budgetUsd != null) b.put("budget_usd", budgetUsd)
        if (!repo.isNullOrBlank()) b.put("repo", repo.trim())
        val r = older { lab.signedJson("POST", "/phone/mission", b.toString().toByteArray(), unlocked) }
        return r.optString("message").ifEmpty { "Sent to your PC." }
    }

    /** pause / resume / stop: the read key is enough (they only make the PC do less, or carry on). */
    fun act(id: String, op: String): String {
        val r = older { money { lab.readJson("POST", "/phone/mission/${enc(id)}/$op", "{}".toByteArray()) } }
        return r.optString("message").ifEmpty { "Done." }
    }

    /** The PC's "needs your fingerprint" answer becomes [NeedsFingerprint], so the screen can ask for it. */
    private fun money(block: () -> JSONObject): JSONObject = try {
        block()
    } catch (e: LabError) {
        if ((e.message ?: "").contains("needs your fingerprint")) throw NeedsFingerprint(e.message ?: "")
        throw e
    }

    /** approve_review / reject: [unlocked] is the Signature BiometricPrompt returned for this one decision. */
    fun decide(id: String, op: String, unlocked: Signature): String {
        val r = older { lab.signedJson("POST", "/phone/mission/${enc(id)}/$op", "{}".toByteArray(), unlocked) }
        return r.optString("message").ifEmpty { "Done." }
    }

    /** The id goes into the signed path as it is, so only plain ids are allowed (the PC makes them). */
    private fun enc(id: String): String {
        if (!id.matches(Regex("[A-Za-z0-9._:-]{1,120}"))) throw LabError("That mission has an id the phone can't use.")
        return id
    }

    private fun older(block: () -> JSONObject): JSONObject = try {
        block()
    } catch (e: LabError) {
        if ((e.message ?: "").contains("404")) throw LabError(MissionsActivity.OLDER_PC)
        throw e
    } catch (e: java.io.IOException) {
        throw LabError("Can't reach your PC. Missions run on the PC, so it needs to be on.")
    }
}

/** The missions board in Orion's glass style, plus "Send work to the PC". */
class MissionsActivity : Activity() {
    private lateinit var api: MissionsApi
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var list: LinearLayout
    private lateinit var boardNote: TextView
    private var loading = false

    private val autoRefresh = object : Runnable {
        override fun run() { load(); ui.postDelayed(this, 15_000) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        api = MissionsApi(this)
        render(sharedText(intent))
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        render(sharedText(intent))
    }

    override fun onResume() {
        super.onResume()
        ui.removeCallbacks(autoRefresh)
        ui.post(autoRefresh)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(autoRefresh)
    }

    /** Text shared from another app, or a title handed over by the home screen. */
    private fun sharedText(i: Intent?): String {
        if (i == null) return ""
        val t = if (i.action == Intent.ACTION_SEND) i.getStringExtra(Intent.EXTRA_TEXT) else i.getStringExtra(EXTRA_TITLE)
        return (t ?: "").trim().take(300)
    }

    private fun render(prefill: String) {
        val col = SysUi.page(this, "Missions", "Work your PC does for you. Approving or rejecting a review needs your fingerprint.")
        if (!api.lab.paired) {
            col.addView(SysUi.card(this).apply {
                addView(SysUi.text(this@MissionsActivity, "Pair this phone with your PC first. Open Orion to pair."))
                addView(SysUi.button(this@MissionsActivity, "Open Orion", primary = true) {
                    startActivity(Intent(this@MissionsActivity, MainActivity::class.java)); finish()
                })
            })
            return
        }

        // ------------------------------------------------------------ send work to the PC
        col.addView(SysUi.section(this, "Send work to the PC"))
        val form = SysUi.card(this)
        val title = SysUi.input(this, "What should the PC do? e.g. Build a landing page for the shop", prefill, multiLine = true)
        val repo = SysUi.input(this, "Project or repo (optional)", "")
        val budget = SysUi.input(this, "Budget in dollars (optional)", "").apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }
        val status = SysUi.note(this, "It's queued on your PC's Mission Control. Nothing is merged from here.")
        form.addView(title); form.addView(repo); form.addView(budget)
        form.addView(SysUi.button(this, "Send to PC", primary = true) {
            val t = title.text.toString().trim()
            if (t.isEmpty()) { status.setTextColor(SysUi.amber); status.text = "Say what the PC should do."; return@button }
            val b = budget.text.toString().trim().replace(",", ".").replace("$", "")
            val money = if (b.isEmpty()) null else b.toDoubleOrNull()
            if (b.isNotEmpty() && (money == null || money <= 0)) {
                status.setTextColor(SysUi.amber); status.text = "The budget must be a number of dollars, like 5."; return@button
            }
            status.setTextColor(SysUi.quiet); status.text = "Sending to your PC..."
            if (money != null && money > 5.0) {   // spending more than the everyday amount: fingerprint (PC rule)
                val sayStatus: (String, Int) -> Unit = { text, color -> ui.post { status.setTextColor(color); status.text = text } }
                withFingerprint("Allow a bigger budget", t, "Lets this mission spend up to $${"%.2f".format(money)}.", sayStatus) { sig ->
                    try {
                        val words = api.createSigned(t, repo.text.toString(), money, sig)
                        ui.post { status.setTextColor(SysUi.green); status.text = words; title.setText(""); load() }
                    } catch (e: Exception) {
                        ui.post { status.setTextColor(SysUi.red); status.text = e.message ?: e.toString() }
                    }
                }
                return@button
            }
            Thread {
                try {
                    val words = api.create(t, repo.text.toString(), money)
                    ui.post { status.setTextColor(SysUi.green); status.text = words; title.setText(""); load() }
                } catch (e: NeedsFingerprint) {   // over the $5 open total: same request, fingerprint key
                    ui.post {
                        status.setTextColor(SysUi.amber); status.text = e.message
                        val sayStatus: (String, Int) -> Unit = { text, color -> ui.post { status.setTextColor(color); status.text = text } }
                        withFingerprint("Allow more spending", t, "Your phone already has $5 of work open on the PC.", sayStatus) { sig ->
                            try {
                                val words = api.createSigned(t, repo.text.toString(), money, sig)
                                ui.post { status.setTextColor(SysUi.green); status.text = words; title.setText(""); load() }
                            } catch (x: Exception) {
                                ui.post { status.setTextColor(SysUi.red); status.text = x.message ?: x.toString() }
                            }
                        }
                    }
                } catch (e: Exception) {
                    ui.post { status.setTextColor(SysUi.red); status.text = e.message ?: e.toString() }
                }
            }.start()
        })
        form.addView(status)
        col.addView(form)

        // ------------------------------------------------------------ the board
        val head = SysUi.row(this)
        head.addView(SysUi.weighted(SysUi.section(this, "On the board")))
        head.addView(SysUi.pill(this, "Refresh") { load() })
        col.addView(head)
        boardNote = SysUi.note(this, "Asking your PC...")
        col.addView(boardNote)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(list)
        col.addView(SysUi.note(this, "Tip: say \"Orion, have the PC build ...\" to send work by voice."))
        load()
    }

    private fun load() {
        if (loading || !::list.isInitialized || !api.lab.paired) return
        loading = true
        Thread {
            val res = try { Result.success(api.board()) } catch (e: Exception) { Result.failure(e) }
            ui.post {
                loading = false
                res.onSuccess { show(it) }.onFailure { e ->
                    boardNote.setTextColor(SysUi.amber)
                    boardNote.text = e.message ?: e.toString()
                }
            }
        }.start()
    }

    private fun show(b: MissionsApi.Board) {
        list.removeAllViews()
        boardNote.setTextColor(SysUi.quiet)
        when {
            !b.available -> { boardNote.setTextColor(SysUi.amber); boardNote.text = b.message.ifEmpty { NOT_ON_PC } }
            b.missions.isEmpty() -> boardNote.text = b.message.ifEmpty { "No missions yet. Send one above." }
            else -> {
                val total = b.missions.mapNotNull { it.costUsd }.sum()
                boardNote.text = "${b.missions.size} on the board · ${Mission.usd(total)} spent"
            }
        }
        val focus = intent?.getStringExtra(EXTRA_FOCUS)
        val ordered = b.missions.sortedBy { if (it.id == focus) 0 else if (it.needsReview) 1 else if (it.finished) 3 else 2 }
        for (m in ordered) list.addView(card(m))
    }

    private fun card(m: Mission): View {
        val c = SysUi.card(this)
        if (Glass.blurAvailable) c.background = Glass.panel(this, 18)
        c.addView(SysUi.title(this, m.title.ifEmpty { "Mission" }).apply { typeface = Glass.bodyBold(this@MissionsActivity) })
        val stateColor = when {
            m.needsReview -> SysUi.amber
            m.paused -> SysUi.amber
            m.finished -> SysUi.quiet
            m.state.lowercase() in listOf("failed", "error") -> SysUi.red
            else -> SysUi.green
        }
        val line = listOf(words(m.state), m.agent, m.repo).filter { it.isNotEmpty() }.joinToString(" · ")
        c.addView(SysUi.text(this, line, 14f, stateColor))
        m.spent().takeIf { it.isNotEmpty() }?.let {
            c.addView(SysUi.text(this, it, 13f, SysUi.ink).apply { typeface = Glass.mono(this@MissionsActivity) })
        }
        if (m.summary.isNotEmpty()) c.addView(SysUi.note(this, m.summary))
        if (m.branch.isNotEmpty()) c.addView(SysUi.text(this, m.branch, 12f, SysUi.quiet).apply { typeface = Typeface.MONOSPACE })
        val result = SysUi.note(this, "").apply { visibility = View.GONE }
        val row = SysUi.row(this)
        fun say(text: String, color: Int) { result.visibility = View.VISIBLE; result.setTextColor(color); result.text = text }
        fun run(op: String) {
            say("Asking your PC...", SysUi.quiet)
            Thread {
                try { val w = api.act(m.id, op); ui.post { say(w, SysUi.green); load() } }
                catch (e: NeedsFingerprint) {   // a resume over the $5 open total: ask for the fingerprint
                    ui.post {
                        say(e.message ?: "", SysUi.amber)
                        withFingerprint("Allow more spending", m.title, "Puts this mission back in the queue.", ::say) { sig ->
                            val w = api.decide(m.id, op, sig); ui.post { say(w, SysUi.green); load() }
                        }
                    }
                }
                catch (e: Exception) { ui.post { say(e.message ?: e.toString(), SysUi.red) } }
            }.start()
        }
        if (!m.finished && !m.needsReview) {   // waiting for review: Reject (fingerprint), never Stop
            if (m.paused) row.addView(SysUi.pill(this, "Resume", SysUi.cyan) { run("resume") })
            else row.addView(SysUi.pill(this, "Pause", SysUi.amber) { run("pause") })
            var armed = false
            lateinit var stop: Button
            stop = SysUi.pill(this, "Stop", SysUi.red) {
                if (!armed) { armed = true; stop.text = "Tap again to stop" } else { armed = false; stop.text = "Stop"; run("stop") }
            }
            row.addView(stop)
        }
        if (m.needsReview) {
            row.addView(SysUi.pill(this, "Approve review", SysUi.green) {
                withFingerprint("Approve the review", m.title, "Approves this mission's review on your PC.", ::say) { sig ->
                    val w = api.decide(m.id, "approve_review", sig); ui.post { say(w, SysUi.green); load() }
                }
            })
            row.addView(SysUi.pill(this, "Reject", SysUi.red) {
                withFingerprint("Reject the work", m.title, "Sends this mission's work back. Nothing is merged.", ::say) { sig ->
                    val w = api.decide(m.id, "reject", sig); ui.post { say(w, SysUi.quiet); load() }
                }
            })
        }
        if (row.childCount > 0) c.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(row) })
        c.addView(result)
        return c
    }

    private fun words(state: String) = state.replace('_', ' ').replaceFirstChar { it.uppercase() }

    /** BiometricPrompt unlocks the fingerprint key for exactly one signature; [action] runs on a background thread. */
    private fun withFingerprint(title: String, subtitle: String, description: String, say: (String, Int) -> Unit,
                                action: (Signature) -> Unit) {
        val sig = try {
            Keys.signatureForApproval()
        } catch (e: KeyPermanentlyInvalidatedException) {
            say("A new fingerprint was added, so the approval key was wiped. Open Orion and pair this phone again.", SysUi.red)
            return
        } catch (e: Exception) {
            say(e.message ?: e.toString(), SysUi.red); return
        }
        val prompt = BiometricPrompt.Builder(this)
            .setTitle(title).setSubtitle(subtitle.take(80)).setDescription(description)
            .setAllowedAuthenticators(BIOMETRIC_STRONG)
            .setNegativeButton("Cancel", mainExecutor) { _, _ -> }
            .build()
        prompt.authenticate(BiometricPrompt.CryptoObject(sig), CancellationSignal(), mainExecutor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    val unlocked = result.cryptoObject?.signature ?: return
                    say("Signing...", SysUi.quiet)
                    Thread {
                        try { action(unlocked) } catch (e: Exception) { ui.post { say(e.message ?: e.toString(), SysUi.red) } }
                    }.start()
                }

                override fun onAuthenticationError(code: Int, msg: CharSequence) { say(msg.toString(), SysUi.quiet) }
            })
    }

    companion object {
        /** A title to put in the "Send work to the PC" box. */
        const val EXTRA_TITLE = "mission_title"
        /** A mission to show first (a tapped robot). */
        const val EXTRA_FOCUS = "mission_id"
        const val NOT_ON_PC = "Mission Control isn't on this PC yet. Update Orion on the PC."
        const val OLDER_PC = "Your PC's Orion is older and doesn't have Mission Control for the phone yet. Update Orion on the PC."

        fun open(c: Context, focus: String? = null) {
            c.startActivity(Intent(c, MissionsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).apply {
                if (focus != null) putExtra(EXTRA_FOCUS, focus)
            })
        }
    }
}
