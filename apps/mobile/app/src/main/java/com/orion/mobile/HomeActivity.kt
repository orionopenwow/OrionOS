package com.orion.mobile

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.view.Display
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowInsets
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextClock
import android.widget.TextView
import android.widget.Toast
import kotlin.math.abs

/**
 * Orion Home (Android Stage 1, the look): the phone's home screen is the living black hole.
 *
 *  - Swipe up: the frosted-glass app list.        - Swipe down: approvals.
 *  - Two-finger swipe: agents.                    - Hold the core: talk to Orion (mic only while you hold).
 *  - Tap an approval star: opens it in the chat screen, where approving needs your fingerprint.
 *  - Tap a robot: what that agent is doing, with Open on PC / Pause / Stop.
 *  - Long-press empty sky: Set as wallpaper, Change Home app, Open Orion chat.
 *  - On a wide screen (external monitor, tablet): the Orion glass desktop with chat, approvals, agents and a taskbar.
 *
 * Nothing here can approve anything: approvals only happen in MainActivity with the fingerprint key.
 */
open class HomeActivity : Activity() {
    private lateinit var api: PairApi
    private lateinit var root: FrameLayout
    private lateinit var orbits: OrbitsView
    private lateinit var statusLine: TextView
    private lateinit var freezeChip: TextView
    private lateinit var listening: TextView
    private lateinit var replyCard: TextView
    private lateinit var drawer: AppDrawer
    private var bh: BlackHoleView? = null
    private val look = BlackHoleLook()
    private val talk by lazy { Talk(this) }
    private val ui = Handler(Looper.getMainLooper())

    private var home: HomeData = HomeData(reachable = false, paired = false)
    private var lastMessages = -1
    private var lastApprovals = -1
    private var polling = false
    private var sheet: View? = null
    private var wide = false
    private var desktop: DesktopPanels? = null

    // ------------------------------------------------------------------ lifecycle
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        api = PairApi(this)
        Link.watch(this)  // link: network changes find the PC again
        drawer = AppDrawer(this) { desktop?.refreshTaskbar() }
        wide = isWide()
        window.setDecorFitsSystemWindows(false)
        root = FrameLayout(this).apply { setBackgroundColor(Glass.VOID) }
        setContentView(root)
        buildSky()
        buildGlance()
        buildVoiceBits()
        if (wide) desktop = DesktopPanels(this).also { it.attach() }
        else root.addView(Glass.text(this, getString(R.string.launcher_swipe_hint), 12f, Glass.QUIET).apply {
            gravity = Gravity.CENTER; tag = "hint"
        }, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM).apply { bottomMargin = dp(28) })
        root.setOnApplyWindowInsetsListener { _, insets -> applyInsets(insets); insets }
        registerReceiver(motionWatch, IntentFilter(PowerManager.ACTION_POWER_SAVE_MODE_CHANGED))
    }

    override fun onResume() {
        super.onResume()
        updateMotion()
        bh?.start()
        ui.removeCallbacks(poll)
        ui.post(poll)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(poll)
        if (talk.recording) stopTalking(send = false)
        bh?.stop()
    }

    override fun onDestroy() {
        try { unregisterReceiver(motionWatch) } catch (_: Exception) { }
        talk.release()
        super.onDestroy()
    }

    /** Pressing Home while already home: close whatever is open. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        closeSheet()
    }

    @Deprecated("Back closes a panel; the home screen itself has nowhere to go back to")
    override fun onBackPressed() {
        if (sheet != null) closeSheet()
    }

    protected open fun isWide(): Boolean {
        val external = try { display?.displayId?.let { it != Display.DEFAULT_DISPLAY } ?: false } catch (e: Exception) { false }
        return external || resources.configuration.screenWidthDp >= 840
    }

    private fun applyInsets(insets: WindowInsets) {
        val bars = insets.getInsets(WindowInsets.Type.systemBars())
        root.findViewWithTag<View>("glance")?.setPadding(dp(20), bars.top + dp(12), dp(20), dp(8))
        root.findViewWithTag<View>("hint")?.let {
            (it.layoutParams as FrameLayout.LayoutParams).bottomMargin = bars.bottom + dp(18); it.requestLayout()
        }
        desktop?.insets(bars.top, bars.bottom)
    }

    // ------------------------------------------------------------------ the black hole + orbits
    private fun buildSky() {
        if (BlackHoleRenderer.supportsEs3(this)) {
            val v = BlackHoleView(this, look) { showStill() }
            v.renderScale = 0.5f
            root.addView(v, FrameLayout.LayoutParams(-1, -1))
            bh = v
        } else showStill()
        orbits = OrbitsView(this)
        orbits.setOnTouchListener { _, e -> onSkyTouch(e) }
        root.addView(orbits, FrameLayout.LayoutParams(-1, -1))
    }

    /** No OpenGL ES 3 (or the shader failed): the painting as a still, centred on black. */
    private fun showStill() {
        bh?.let { it.stop(); root.removeView(it) }
        bh = null
        val img = ImageView(this).apply {
            setImageResource(R.drawable.black_hole_core)
            scaleType = ImageView.ScaleType.FIT_CENTER
            setBackgroundColor(Glass.VOID)
            val pad = (resources.displayMetrics.widthPixels * 0.08f).toInt()
            setPadding(pad, pad, pad, pad)
        }
        root.addView(img, 0, FrameLayout.LayoutParams(-1, -1))
    }

    private val motionWatch = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) = updateMotion()
    }

    /** Battery saver or "Remove animations": a still frame and calm orbits. Wide screens get the Balanced tier. */
    private fun updateMotion() {
        val motion = BlackHoleRenderer.motionAllowed(this)
        look.quality = if (!motion) 0 else if (wide) 2 else 1
        look.calm = !motion
        orbits.moving = motion
        bh?.moving = motion && sheet == null
    }

    // ------------------------------------------------------------------ glance strip: time, PC, Freeze
    private fun buildGlance() {
        val strip = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; tag = "glance" }
        strip.addView(TextClock(this).apply {
            format12Hour = "h:mm"; format24Hour = "H:mm"
            typeface = Glass.heading(this@HomeActivity)
            setTextColor(Color.WHITE); textSize = if (wide) 34f else 44f
        })
        strip.addView(TextClock(this).apply {
            format12Hour = "EEEE d MMMM"; format24Hour = "EEEE d MMMM"
            typeface = Glass.body(this@HomeActivity); setTextColor(Glass.QUIET); textSize = 14f
        })
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        statusLine = Glass.text(this, getString(R.string.launcher_asking_pc), 14f, Glass.INK).apply {
            maxLines = 1; ellipsize = TextUtils.TruncateAt.END
            setOnClickListener {  // link: not connected -> "Fix the connection"
                if (home.paired && !home.reachable) LinkDoctor.show(this@HomeActivity) else openChat()
            }
        }
        row.addView(statusLine, LinearLayout.LayoutParams(0, -2, 1f))
        freezeChip = Glass.text(this, getString(R.string.launcher_running), 13f, Glass.AURORA, Glass.bodyBold(this)).apply {
            background = Glass.panel(this@HomeActivity, 999)
            setPadding(dp(12), dp(6), dp(12), dp(6))
            setOnClickListener { openChat() }   // freezing and unfreezing need the fingerprint, in the chat screen
        }
        row.addView(freezeChip)
        row.addView(Glass.text(this, "Menu", 13f, Glass.INK, Glass.bodyBold(this)).apply {  // [phone2] the one menu
            background = Glass.panel(this@HomeActivity, 999); setPadding(dp(12), dp(6), dp(12), dp(6))
            setOnClickListener { skyMenu() }
        }, LinearLayout.LayoutParams(-2, -2).apply { marginStart = dp(8) })
        strip.addView(row, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6) })
        root.addView(strip, FrameLayout.LayoutParams(if (wide) dp(420) else -1, -2, Gravity.TOP or Gravity.START))
    }

    private fun showGlance(h: HomeData) {
        statusLine.text = when {
            !h.paired -> getString(R.string.launcher_not_paired)
            !h.reachable -> Link.line(this, h)  // link: Reconnecting… / PC asleep — using Orion on this phone / Not on the same Wi-Fi
            h.approvals.isEmpty() && h.core.approvals == 0 -> getString(R.string.launcher_pc_online_clear)
            else -> getString(R.string.launcher_pc_online_waiting, maxOf(h.approvals.size, h.core.approvals))
        }
        statusLine.setTextColor(when {
            !h.reachable -> Glass.QUIET
            h.healthLevel >= 3 -> Glass.ROSE
            h.healthLevel == 2 -> Glass.EMBER
            else -> Glass.INK
        })
        freezeChip.text = getString(if (h.frozen) R.string.launcher_frozen else R.string.launcher_running)
        freezeChip.setTextColor(if (h.frozen) Glass.PHOTON else Glass.AURORA)
        freezeChip.visibility = if (h.reachable) View.VISIBLE else View.GONE
    }

    // ------------------------------------------------------------------ polling GET /phone/home every 10 s
    private val poll = object : Runnable {
        override fun run() {
            refresh()
            ui.postDelayed(this, 10_000)
        }
    }

    private fun refresh() {
        if (polling) return
        polling = true
        Thread {
            val h = api.home()
            ui.post {
                polling = false
                apply(h)
            }
        }.start()
    }

    private fun apply(h: HomeData) {
        home = h
        val now = SystemClock.elapsedRealtime() / 1000.0
        look.setCore(h.core, h.healthLevel)
        val m = h.core.messages
        if (lastMessages in 0 until m) look.comet(now, m - lastMessages)
        lastMessages = m
        val a = h.approvals.size
        if (lastApprovals > a) look.flare(0.8f)   // one was decided: it fell into the hole
        lastApprovals = a
        orbits.approvals = h.approvals
        orbits.agents = h.agents
        showGlance(h)
        desktop?.show(h)
        bh?.kick()
    }

    // ------------------------------------------------------------------ gestures on the sky
    private var downX = 0f
    private var downY = 0f
    private var twoFinger = false
    private var twoStartY = 0f
    private var twoStartX = 0f
    private var gestureDone = false
    private var longPressed = false
    private val slop by lazy { ViewConfiguration.get(this).scaledTouchSlop }
    private val longPress = Runnable { onLongPress() }

    private fun onSkyTouch(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = e.x; downY = e.y; twoFinger = false; gestureDone = false; longPressed = false
                ui.postDelayed(longPress, ViewConfiguration.getLongPressTimeout().toLong())
                look.setParallax((e.x / root.width - 0.5f) * 2f, (0.5f - e.y / root.height) * 2f)
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                ui.removeCallbacks(longPress)
                if (e.pointerCount == 2) {
                    twoFinger = true
                    twoStartX = (e.getX(0) + e.getX(1)) / 2; twoStartY = (e.getY(0) + e.getY(1)) / 2
                }
            }
            MotionEvent.ACTION_MOVE -> {
                if (talk.recording) return true
                if (abs(e.x - downX) > slop || abs(e.y - downY) > slop) ui.removeCallbacks(longPress)
                if (twoFinger && e.pointerCount >= 2 && !gestureDone) {
                    val mx = (e.getX(0) + e.getX(1)) / 2; val my = (e.getY(0) + e.getY(1)) / 2
                    if (abs(mx - twoStartX) > dp(70) || abs(my - twoStartY) > dp(70)) { gestureDone = true; showAgents() }
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                ui.removeCallbacks(longPress)
                look.setParallax(0f, 0f)
                if (talk.recording) { stopTalking(send = e.actionMasked == MotionEvent.ACTION_UP); return true }
                if (gestureDone || twoFinger || longPressed || e.actionMasked == MotionEvent.ACTION_CANCEL) return true
                val dy = e.y - downY; val dx = e.x - downX
                when {
                    dy < -dp(80) && abs(dy) > abs(dx) -> showDrawer()
                    dy > dp(80) && abs(dy) > abs(dx) -> showApprovals()
                    abs(dx) < slop * 2 && abs(dy) < slop * 2 -> onTap(e.x, e.y)
                }
            }
        }
        return true
    }

    private fun onTap(x: Float, y: Float) {
        when (val hit = orbits.hit(x, y)) {
            is OrbitsView.Hit.Star -> openApproval(hit.approval)
            is OrbitsView.Hit.Robot -> if (hit.agent.mission) MissionsActivity.open(this, hit.agent.id) else agentCard(hit.agent)
            else -> {}
        }
    }

    private fun onLongPress() {
        longPressed = true
        if (orbits.hit(downX, downY) is OrbitsView.Hit.Core) startTalking() else skyMenu()
    }

    // ------------------------------------------------------------------ hold the core to talk
    private fun buildVoiceBits() {
        listening = Glass.text(this, getString(R.string.launcher_listening), 14f, Color.WHITE, Glass.bodyBold(this)).apply {
            background = Glass.panel(this@HomeActivity, 999, Color.parseColor("#CC3A0F22"))
            setPadding(dp(16), dp(8), dp(16), dp(8))
            setCompoundDrawablesRelativeWithIntrinsicBounds(dot(Glass.ROSE), null, null, null)
            compoundDrawablePadding = dp(8)
            visibility = View.GONE
        }
        root.addView(listening, FrameLayout.LayoutParams(-2, -2, Gravity.CENTER_HORIZONTAL or Gravity.BOTTOM).apply { bottomMargin = dp(120) })
        replyCard = Glass.text(this, "", 15f).apply {
            background = Glass.panel(this@HomeActivity)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            maxLines = 6; ellipsize = TextUtils.TruncateAt.END
            visibility = View.GONE
            setOnClickListener { visibility = View.GONE; openChat() }
        }
        root.addView(replyCard, FrameLayout.LayoutParams(if (wide) dp(520) else -1, -2, Gravity.CENTER_HORIZONTAL or Gravity.BOTTOM).apply {
            setMargins(dp(16), 0, dp(16), dp(64))
        })
    }

    private fun dot(color: Int) = android.graphics.drawable.GradientDrawable().apply {
        shape = android.graphics.drawable.GradientDrawable.OVAL; setColor(color); setSize(dp(10), dp(10))
    }

    private val micMeter = object : Runnable {
        override fun run() {
            if (!talk.recording) return
            look.setMic(talk.level())
            if (bh?.moving != true) bh?.kick()
            ui.postDelayed(this, 50)
        }
    }

    private fun startTalking() {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != 0) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 8)
            toast(getString(R.string.launcher_mic_needed)); return
        }
        if (!api.lab.paired) { openChat(); return }
        if (!talk.start()) { toast(getString(R.string.launcher_mic_failed)); return }
        orbits.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
        listening.visibility = View.VISIBLE
        replyCard.visibility = View.GONE
        ui.post(micMeter)
    }

    private fun stopTalking(send: Boolean) {
        ui.removeCallbacks(micMeter)
        look.setMic(0f)
        listening.visibility = View.GONE
        val clip = talk.stop()
        if (!send) return
        if (clip == null) { toast(getString(R.string.launcher_too_short)); return }
        replyCard.text = getString(R.string.launcher_thinking)
        replyCard.visibility = View.VISIBLE
        Thread {
            try {
                val out = PocketFallback.voice(this, api.lab, clip)   // PC off: Orion on this phone
                ui.post {
                    val heard = out.optString("heard")
                    replyCard.text = (if (heard.isNotEmpty()) "“$heard”\n\n" else "") + PocketFallback.shown(out)
                    talk.play(out)
                    refresh()
                }
            } catch (e: Exception) {
                ui.post { replyCard.text = e.message ?: e.toString() }
            }
        }.start()
    }

    // ------------------------------------------------------------------ glass sheets
    /**
     * Opens a glass panel over the black hole. On Android 12+ the panel sits on a blurred snapshot of the frame
     * (and the GL view stops drawing underneath it); older phones get a deeper tint instead of blur.
     */
    private fun openSheet(content: View, fromTop: Boolean, title: String?, sub: String?): FrameLayout {
        closeSheet(animate = false)
        val layer = FrameLayout(this).apply { isClickable = true }
        val backdrop = ImageView(this).apply { scaleType = ImageView.ScaleType.FIT_XY; setBackgroundColor(Color.parseColor("#66000000")) }
        layer.addView(backdrop, FrameLayout.LayoutParams(-1, -1))
        if (Glass.blurAvailable) {
            bh?.snapshot { bmp: Bitmap? ->
                if (bmp != null && sheet === layer) {
                    backdrop.setImageBitmap(Glass.shrink(bmp))
                    Glass.frost(backdrop)
                    backdrop.setColorFilter(Color.parseColor("#55000000"))
                }
            }
        }
        backdrop.setOnClickListener { closeSheet() }
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = Glass.panel(this@HomeActivity, 28)
            setPadding(dp(18), dp(16), dp(18), dp(18))
            isClickable = true
        }
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(Glass.beltDots(this))
        if (title != null) head.addView(Glass.text(this, title, 18f, Color.WHITE, Glass.heading(this)).apply {
            setPadding(dp(8), 0, 0, 0)
        }, LinearLayout.LayoutParams(0, -2, 1f))
        else head.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f))
        head.addView(Glass.pill(this, getString(R.string.launcher_close), Glass.QUIET) { closeSheet() })
        panel.addView(head)
        if (sub != null) panel.addView(Glass.text(this, sub, 13f, Glass.QUIET).apply { setPadding(0, dp(6), 0, dp(10)) })
        panel.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        // a fling on the header closes the panel (down for the app list, up for the top panels)
        head.setOnTouchListener(object : View.OnTouchListener {
            var y0 = 0f
            override fun onTouch(v: View, ev: MotionEvent): Boolean {
                when (ev.actionMasked) {
                    MotionEvent.ACTION_DOWN -> y0 = ev.rawY
                    MotionEvent.ACTION_UP -> {
                        val dy = ev.rawY - y0
                        if ((!fromTop && dy > dp(60)) || (fromTop && dy < -dp(60))) closeSheet()
                    }
                }
                return true
            }
        })
        val maxW = if (wide) dp(720) else -1
        val lp = FrameLayout.LayoutParams(maxW, -1, Gravity.CENTER_HORIZONTAL).apply {
            setMargins(dp(10), if (fromTop) dp(36) else dp(90), dp(10), if (fromTop) dp(120) else dp(10))
        }
        layer.addView(panel, lp)
        root.addView(layer, FrameLayout.LayoutParams(-1, -1))
        val h = resources.displayMetrics.heightPixels.toFloat()
        panel.translationY = if (fromTop) -h else h
        panel.animate().translationY(0f).setDuration(if (look.calm) 0 else 240).start()
        sheet = layer
        bh?.moving = false
        return layer
    }

    private fun closeSheet(animate: Boolean = true) {
        val s = sheet ?: return
        sheet = null
        if (animate && !look.calm) s.animate().alpha(0f).setDuration(160).withEndAction { root.removeView(s) }.start()
        else root.removeView(s)
        updateMotion()
        bh?.kick()
    }

    private fun showDrawer() {
        val content = drawer.build()
        drawer.refresh()
        openSheet(content, fromTop = false, title = null, sub = null)
    }

    private fun showApprovals() {
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fillApprovals(list, home.approvals)
        openSheet(ScrollView(this).apply { addView(list) }, fromTop = true,
            title = getString(R.string.launcher_approvals), sub = getString(R.string.launcher_approvals_sub))
    }

    fun fillApprovals(list: LinearLayout, items: List<HomeApproval>) {
        list.removeAllViews()
        if (items.isEmpty()) { list.addView(Glass.text(this, getString(R.string.launcher_nothing_waiting), 14f, Glass.QUIET)); return }
        for (a in items) list.addView(row(a.title.ifEmpty { a.kind }, listOf(a.kind, if (a.risk.isNotEmpty()) "risk ${a.risk}" else "")
            .filter { it.isNotEmpty() }.joinToString(", "), if (a.risk == "high") Glass.EMBER else Glass.PHOTON) { openApproval(a) })
    }

    private fun showAgents() {
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fillAgents(list, home.agents)
        list.addView(Glass.pill(this, getString(R.string.launcher_missions), Glass.PHOTON) { closeSheet(false); MissionsActivity.open(this) })  // Missions.kt
        openSheet(ScrollView(this).apply { addView(list) }, fromTop = true,
            title = getString(R.string.launcher_agents), sub = getString(R.string.launcher_agents_sub))
    }

    fun fillAgents(list: LinearLayout, items: List<HomeAgent>) {
        list.removeAllViews()
        if (items.isEmpty()) { list.addView(Glass.text(this, getString(R.string.launcher_no_agents), 14f, Glass.QUIET)); return }
        for (a in items) list.addView(row(a.name.ifEmpty { a.tool }, listOf(a.status.ifEmpty { a.state }, a.cost())
            .filter { it.isNotEmpty() }.joinToString(" · "),
            if (a.state == "paused") Glass.EMBER else Glass.AURORA) { if (a.mission) MissionsActivity.open(this, a.id) else agentCard(a) })
    }

    private fun row(title: String, sub: String, accent: Int, onClick: () -> Unit): View {
        val r = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            background = Glass.panel(this@HomeActivity, 16, Color.parseColor("#1FFFFFFF"))
            setPadding(dp(14), dp(12), dp(14), dp(12))
            setOnClickListener { onClick() }
        }
        r.addView(View(this).apply { background = dot(accent) }, LinearLayout.LayoutParams(dp(10), dp(10)).apply { marginEnd = dp(12) })
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(Glass.text(this, title, 15f, Color.WHITE, Glass.bodyBold(this)).apply { maxLines = 2; ellipsize = TextUtils.TruncateAt.END })
        if (sub.isNotEmpty()) col.addView(Glass.text(this, sub, 13f, Glass.QUIET).apply { maxLines = 1; ellipsize = TextUtils.TruncateAt.END })
        r.addView(col, LinearLayout.LayoutParams(0, -2, 1f))
        r.layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) }
        return r
    }

    /** What one agent is doing, with Open on PC / Pause / Stop. The PC refuses to stop agents it didn't start. */
    fun agentCard(a: HomeAgent) {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(Glass.text(this, a.tool.ifEmpty { "agent" }, 12f, Glass.QUIET, Glass.mono(this)))
        col.addView(Glass.text(this, a.status.ifEmpty { a.state.ifEmpty { "Working." } }, 16f).apply { setPadding(0, dp(6), 0, dp(12)) })
        if (a.cost().isNotEmpty()) col.addView(Glass.text(this, a.cost(), 13f, Glass.INK, Glass.mono(this)).apply { setPadding(0, 0, 0, dp(10)) })
        val result = Glass.text(this, "", 13f, Glass.QUIET).apply { setPadding(0, dp(10), 0, 0) }
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        var armed = false
        fun act(action: String) {
            result.setTextColor(Glass.QUIET); result.text = getString(R.string.launcher_asking_pc)
            Thread {
                try {
                    val words = api.agentAction(a.id, action)
                    ui.post { result.setTextColor(Glass.AURORA); result.text = words; refresh() }
                } catch (e: Exception) {
                    // e.g. "Orion didn't start this agent, so it can't stop it." shown exactly as the PC says it
                    ui.post { result.setTextColor(Glass.ROSE); result.text = e.message ?: e.toString() }
                }
            }.start()
        }
        buttons.addView(Glass.pill(this, getString(R.string.launcher_open_on_pc), Glass.PHOTON) { act("open") },
            LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(8) })
        buttons.addView(Glass.pill(this, getString(R.string.launcher_pause), Glass.EMBER) { act("pause") },
            LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(8) })
        lateinit var stop: android.widget.Button
        stop = Glass.pill(this, getString(R.string.launcher_stop), Glass.ROSE) {
            if (!armed) { armed = true; stop.text = getString(R.string.launcher_stop_confirm) } else { armed = false; stop.text = getString(R.string.launcher_stop); act("stop") }
        }
        buttons.addView(stop)
        col.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(buttons) })
        col.addView(result)
        openSheet(ScrollView(this).apply { addView(col) }, fromTop = true, title = a.name.ifEmpty { a.tool }, sub = null)
    }

    /** Long-press on empty sky (or the Menu chip): the one menu. Wallpaper and Home app live in Settings > Phone setup. */
    private fun skyMenu() {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        for (m in OrionMenu.items()) col.addView(row(m.label, m.sub, Glass.VIOLET) { closeSheet(false); m.go(this) })  // [phone2] the one menu (OrionMenu.kt)
        openSheet(ScrollView(this).apply { addView(col) }, fromTop = false, title = "Orion", sub = null)
    }

    private fun setWallpaper() {
        try { startActivity(BlackHoleWallpaper.pickerIntent(this)) }
        catch (e: Exception) { toast(getString(R.string.launcher_wallpaper_missing)) }
    }

    // ------------------------------------------------------------------ hand-offs to the chat screen
    fun openChat() {
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /** Opens the approvals pane in the chat screen. Approving there needs the fingerprint. */
    fun openApproval(a: HomeApproval) {
        startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(Alerts.EXTRA_KIND, "approval")
            .putExtra(EXTRA_APPROVAL_ID, a.id))
    }

    fun showDrawerFromTaskbar() = showDrawer()
    fun chatApi(): PairApi = api

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
    fun dp(v: Int) = Glass.dp(this, v)

    companion object {
        /** Which approval the star was (MainActivity opens the approvals pane; this lets it scroll to the one). */
        const val EXTRA_APPROVAL_ID = "approval_id"
    }
}

/**
 * Desktop mode (external monitor or a big screen): the Orion glass desktop around the core. Chat on the left,
 * approvals and agents on the right, a taskbar of pinned apps bottom-left.
 */
class DesktopPanels(private val home: HomeActivity) {
    private val root get() = home.findViewById<FrameLayout>(android.R.id.content).getChildAt(0) as FrameLayout
    private lateinit var transcript: LinearLayout
    private lateinit var approvals: LinearLayout
    private lateinit var agents: LinearLayout
    private lateinit var taskbar: LinearLayout
    private lateinit var left: View
    private lateinit var right: View
    private lateinit var bar: View

    fun attach() {
        val c = home
        // left: chat
        val chat = panel(home.getString(R.string.launcher_chat))
        transcript = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        chat.addView(ScrollView(c).apply { addView(transcript) }, LinearLayout.LayoutParams(-1, 0, 1f))
        val input = EditText(c).apply {
            hint = c.getString(R.string.launcher_message_hint); setTextColor(Glass.INK); setHintTextColor(Glass.QUIET)
            typeface = Glass.body(c); isSingleLine = true; imeOptions = EditorInfo.IME_ACTION_SEND
            background = Glass.panel(c, 999, Color.parseColor("#26FFFFFF"))
            setPadding(home.dp(14), home.dp(10), home.dp(14), home.dp(10))
        }
        input.setOnEditorActionListener { _, _, _ -> send(input); true }
        chat.addView(input, LinearLayout.LayoutParams(-1, -2).apply { topMargin = home.dp(8) })
        left = chat
        root.addView(chat, FrameLayout.LayoutParams(home.dp(340), -1, Gravity.START).apply {
            setMargins(home.dp(20), home.dp(150), 0, home.dp(96))
        })
        // right: approvals over agents
        val col = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        val ap = panel(home.getString(R.string.launcher_approvals))
        approvals = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        ap.addView(ScrollView(c).apply { addView(approvals) }, LinearLayout.LayoutParams(-1, 0, 1f))
        val ag = panel(home.getString(R.string.launcher_agents))
        agents = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        ag.addView(ScrollView(c).apply { addView(agents) }, LinearLayout.LayoutParams(-1, 0, 1f))
        col.addView(ap, LinearLayout.LayoutParams(-1, 0, 1f).apply { bottomMargin = home.dp(14) })
        col.addView(ag, LinearLayout.LayoutParams(-1, 0, 1f))
        right = col
        root.addView(col, FrameLayout.LayoutParams(home.dp(340), -1, Gravity.END).apply {
            setMargins(0, home.dp(40), home.dp(20), home.dp(96))
        })
        // bottom-left: taskbar
        val tb = LinearLayout(c).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            background = Glass.panel(c, 20)
            setPadding(home.dp(10), home.dp(8), home.dp(10), home.dp(8))
        }
        tb.addView(Glass.beltDots(c), LinearLayout.LayoutParams(-2, -2).apply { marginEnd = home.dp(6) })
        tb.addView(Glass.pill(c, c.getString(R.string.launcher_apps)) { home.showDrawerFromTaskbar() })
        taskbar = LinearLayout(c).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        tb.addView(taskbar)
        bar = tb
        root.addView(tb, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.START).apply {
            setMargins(home.dp(20), 0, 0, home.dp(20))
        })
        refreshTaskbar()
        line(c.getString(R.string.launcher_swipe_hint), mine = false)
    }

    fun insets(top: Int, bottom: Int) {
        (bar.layoutParams as FrameLayout.LayoutParams).bottomMargin = bottom + home.dp(16); bar.requestLayout()
        (right.layoutParams as FrameLayout.LayoutParams).topMargin = top + home.dp(16); right.requestLayout()
        (left.layoutParams as FrameLayout.LayoutParams).bottomMargin = bottom + home.dp(90); left.requestLayout()
    }

    private fun panel(title: String): LinearLayout = LinearLayout(home).apply {
        orientation = LinearLayout.VERTICAL
        background = Glass.panel(home, 24)
        setPadding(home.dp(16), home.dp(14), home.dp(16), home.dp(14))
        val head = LinearLayout(home).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(Glass.beltDots(home))
        head.addView(Glass.text(home, title, 15f, Color.WHITE, Glass.heading(home)).apply { setPadding(home.dp(6), 0, 0, 0) })
        addView(head, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = home.dp(10) })
    }

    fun show(h: HomeData) {
        home.fillApprovals(approvals, h.approvals)
        home.fillAgents(agents, h.agents)
    }

    fun refreshTaskbar() {
        if (!::taskbar.isInitialized) return
        taskbar.removeAllViews()
        val pins = Apps.pinned(home)
        if (pins.isEmpty()) {
            taskbar.addView(Glass.text(home, home.getString(R.string.launcher_taskbar_empty), 12f, Glass.QUIET).apply {
                setPadding(home.dp(8), 0, home.dp(4), 0)
            })
            return
        }
        Thread {
            val all = try { Apps.load(home) } catch (e: Exception) { emptyList() }
            val chosen = pins.mapNotNull { k -> all.firstOrNull { it.key == k } }
            chosen.forEach { Apps.icon(home, it) }
            home.runOnUiThread {
                taskbar.removeAllViews()
                for (e in chosen) taskbar.addView(ImageView(home).apply {
                    setImageDrawable(e.icon); contentDescription = e.label
                    setPadding(home.dp(6), 0, home.dp(6), 0)
                    setOnClickListener { Apps.launch(home, e) }
                    setOnLongClickListener { Apps.togglePin(home, e); refreshTaskbar(); true }
                }, LinearLayout.LayoutParams(home.dp(48), home.dp(36)))
            }
        }.start()
    }

    private fun line(text: String, mine: Boolean) {
        transcript.addView(Glass.text(home, text, 14f, if (mine) Color.WHITE else Glass.INK).apply {
            background = Glass.panel(home, 14, Color.parseColor(if (mine) "#402A2066" else "#1FFFFFFF"))
            setPadding(home.dp(12), home.dp(8), home.dp(12), home.dp(8))
        }, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = home.dp(6) })
    }

    private fun send(input: EditText) {
        val t = input.text.toString().trim()
        if (t.isEmpty()) return
        input.setText("")
        line(t, mine = true)
        Thread {
            val reply = try { PocketFallback.shown(PocketFallback.chat(home, home.chatApi().lab, t, false)) } catch (e: Exception) { e.message ?: e.toString() }
            home.runOnUiThread { line(reply, mine = false) }
        }.start()
    }
}

/** The same home screen on an external monitor (Android's SECONDARY_HOME); it always lays out as the desktop. */
class DesktopHomeActivity : HomeActivity() {
    override fun isWide() = true
}
