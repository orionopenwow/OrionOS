package com.orion.mobile

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** One approval waiting on the PC, as the home screen shows it (approving still happens in MainActivity). */
data class HomeApproval(val id: String, val title: String, val risk: String, val kind: String)

/**
 * One coding agent running on the PC, or one Mission Control mission ([mission] = true). Robots on the home screen
 * show [costUsd] (and the budget) when the PC sends it.
 */
data class HomeAgent(val id: String, val name: String, val tool: String, val status: String, val state: String,
                     val costUsd: Double? = null, val budgetUsd: Double? = null, val mission: Boolean = false) {
    /** "$1.23" or "$1.23 of $5" or "" when the PC sent no cost. */
    fun cost(): String {
        val c = costUsd ?: return ""
        val b = budgetUsd
        return if (b != null && b > 0) "${usd(c)} of ${usd(b)}" else usd(c)
    }

    private fun usd(v: Double) = if (v < 10) String.format("$%.2f", v) else String.format("$%.0f", v)
}

/** The core's live signals, all 0..1 except the counts. */
data class CoreSignals(
    val voice: Float = 0f,
    val speaking: Float = 0f,
    val thinking: Float = 0f,
    val heat: Float = 0f,
    val quiet: Float = 0f,
    val messages: Int = 0,
    val approvals: Int = 0,
    val health: Int = 0,
)

/** Everything the home screen needs in one poll of GET /phone/home. */
data class HomeData(
    val reachable: Boolean,
    val paired: Boolean,
    val core: CoreSignals = CoreSignals(),
    val approvals: List<HomeApproval> = emptyList(),
    val agents: List<HomeAgent> = emptyList(),
    val healthLevel: Int = 0,
    val healthText: String = "",
    val frozen: Boolean = false,
    val pcName: String = "",
    val error: String = "",
    /** The PC is off but Pocket Orion on this phone is on, paired and answering. */
    val pocket: Boolean = false,
)

/**
 * The launcher's network calls. Every request is signed by Lab with the read key; nothing here can approve
 * anything (approvals need the fingerprint key, which only MainActivity uses).
 */
class PairApi(context: Context) {
    private val app = context.applicationContext
    val lab = Lab(app)

    /** GET /phone/home. Never throws: an unreachable or unpaired PC comes back as reachable = false.
     *  [link] Every screen shares one fetch (Link.sharedHome): at most one request in flight, answers reused for
     *  [maxAgeMs], and growing pauses while the PC can't be reached. */
    fun home(maxAgeMs: Long = 4000L): HomeData {
        if (!lab.paired) return HomeData(reachable = false, paired = false)
        return Link.sharedHome(maxAgeMs) { fetchHome() }
    }

    private fun fetchHome(): HomeData = try {
        parseHome(lab.readJson("GET", "/phone/home"))
    } catch (e: LabError) {
        if ((e.message ?: "").contains("404")) older()
        else if ((e.message ?: "").contains("Can't reach") && Link.resolve(app)) {  // [link] PC moved / other network
            try { parseHome(lab.readJson("GET", "/phone/home")) } catch (e2: Exception) { offline(NetWords.why(app, lab.labUrl)) }
        }
        else offline(NetWords.why(app, lab.labUrl))  // [phone2] plain reason
    } catch (e: Exception) {
        offline(e.message ?: e.toString())
    }

    private fun offline(why: String): HomeData =
        HomeData(false, true, error = why, pocket = PocketFallback.enabled(app) && PocketFallback.ready(app))

    /** A PC that doesn't have /phone/home yet: build what we can from the older routes. */
    private fun older(): HomeData = try {
        val snap = lab.pending()
        val h = try { lab.health() } catch (e: Exception) { JSONObject() }
        val level = when (h.optString("status")) { "fail" -> 3; "warn" -> 2; else -> 0 }
        HomeData(
            reachable = true, paired = true,
            core = CoreSignals(approvals = snap.decisions.size, health = level),
            approvals = snap.decisions.map { HomeApproval(it.id, it.summary.ifEmpty { it.label }, it.risk, it.domain) },
            healthLevel = level, healthText = h.optString("words"), frozen = h.optBoolean("frozen"),
        )
    } catch (e: Exception) {
        HomeData(false, true, error = e.message ?: e.toString())
    }

    /**
     * POST /phone/agent {"id","action"}: action is "open" (show it on the PC), "pause" or "stop".
     * Returns the PC's own words. The PC refuses to stop an agent it didn't start; that refusal comes back as
     * a LabError whose message is shown to the user as is.
     */
    fun agentAction(id: String, action: String): String {
        val body = JSONObject().put("id", id).put("action", action).toString().toByteArray()
        return try {
            val r = lab.readJson("POST", "/phone/agent", body)
            r.optString("message").ifEmpty { r.optString("status").ifEmpty { "Done." } }
        } catch (e: LabError) {
            val m = e.message ?: ""
            if (m.contains("404")) throw LabError("Your PC doesn't know this yet. Update Orion on the PC.")
            throw e
        }
    }

    companion object {
        fun parseHome(j: JSONObject): HomeData {
            val c = j.optJSONObject("core") ?: JSONObject()
            val h = j.optJSONObject("health") ?: JSONObject()
            val pc = j.optJSONObject("pc") ?: JSONObject()
            val approvals = list(j.optJSONArray("approvals")) {
                HomeApproval(it.optString("id"), it.optString("title"), it.optString("risk"), it.optString("kind"))
            }
            val agents = list(j.optJSONArray("agents")) {
                HomeAgent(it.optString("id"), it.optString("name"), it.optString("tool"),
                    it.optString("status"), it.optString("state"), money(it, "cost_usd"))
            } + list(j.optJSONArray("missions")) {   // Mission Control (phone_missions.py): robots with their cost
                HomeAgent(it.optString("id"), it.optString("title"), it.optString("agent"),
                    it.optString("summary").ifEmpty { it.optString("state") }, it.optString("state"),
                    money(it, "cost_usd"), money(it, "budget_usd"), mission = true)
            }
            val core = CoreSignals(
                voice = level(c, "voice"),
                speaking = level(c, "speaking"),
                thinking = level(c, "thinking"),
                heat = level(c, "heat"),
                quiet = level(c, "quiet"),
                messages = c.optInt("messages", 0),
                approvals = c.optInt("approvals", approvals.size),
                health = c.optInt("health", h.optInt("level", 0)),
            )
            return HomeData(
                reachable = pc.optBoolean("online", true), paired = true, core = core,
                approvals = approvals, agents = agents,
                healthLevel = h.optInt("level", 0), healthText = h.optString("text"),
                frozen = j.optBoolean("frozen", false), pcName = pc.optString("name"),
            )
        }

        private fun money(o: JSONObject, key: String): Double? =
            if (o.isNull(key) || !o.has(key)) null else o.optDouble(key).takeIf { !it.isNaN() }

        /** A signal may arrive as a number or a boolean. */
        private fun level(o: JSONObject, key: String): Float {
            val v = o.opt(key) ?: return 0f
            val f = when (v) {
                is Boolean -> if (v) 1f else 0f
                is Number -> v.toFloat()
                else -> 0f
            }
            return if (f.isNaN()) 0f else f.coerceIn(0f, 1f)
        }

        private fun <T> list(a: JSONArray?, f: (JSONObject) -> T): List<T> {
            if (a == null) return emptyList()
            return (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(f) }
        }
    }
}
