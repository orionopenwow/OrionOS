package com.orion.mobile

import android.app.Activity
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG
import android.hardware.biometrics.BiometricPrompt
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.speech.tts.TextToSpeech
import android.text.InputType
import android.util.Base64
import android.util.TypedValue
import android.view.GestureDetector
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.io.File
import java.security.Signature

/**
 * Orion Mobile v0.2. The whole screen is a conversation with Orion: no title bar, no system bars (swipe from an
 * edge to peek at them). Approvals, next-step cards and settings slide in over the chat as full-screen panes
 * instead of opening windows; swipe down on a pane's title, or press Back, to return to the chat.
 * The UI is built in code with plain Android views (no libraries), in Orion's colours.
 */
class MainActivity : Activity() {
    private lateinit var lab: Lab
    private lateinit var stage: FrameLayout       // chat underneath, panes on top
    private val ui = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var pane: View? = null
    private var refreshing = false

    // ---------------------------------------------------------------- colours
    private val void = Color.parseColor("#05070E")
    private val panel = Color.parseColor("#0B0F1E")
    private val edge = Color.parseColor("#262D50")
    private val violet = Color.parseColor("#7C5CFF")
    private val cyan = Color.parseColor("#00E5FF")
    private val ink = Color.parseColor("#D8DEFF")
    private val quiet = Color.parseColor("#8888AA")
    private val red = Color.parseColor("#FF5C8A")
    private val amber = Color.parseColor("#FFB300")

    private val autoRefresh = object : Runnable {
        override fun run() {
            if (lab.paired) refreshCounts()
            ui.postDelayed(this, 10_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lab = Lab(this)
        Link.watch(this)  // link
        stage = FrameLayout(this).apply { setBackgroundColor(void) }
        setContentView(stage)
        immersive()
        Alerts.ensureChannels(this)
        PollJob.ensureChannel(this)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != 0) {
            requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), 7)
        }
        route(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        route(intent)
    }

    override fun onResume() {
        super.onResume()
        immersive()
        ui.removeCallbacks(autoRefresh)
        ui.post(autoRefresh)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(autoRefresh)
    }

    override fun onDestroy() {
        tts?.shutdown()
        super.onDestroy()
    }

    @Deprecated("Back closes a pane first")
    override fun onBackPressed() {
        if (pane != null) closePane() else super.onBackPressed()
    }

    /** Full screen: hide the status and navigation bars; a swipe from the edge shows them for a moment. */
    private fun immersive() {
        window.setDecorFitsSystemWindows(false)
        window.insetsController?.let {
            it.hide(WindowInsets.Type.systemBars())
            it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun route(intent: Intent?) {
        val data = intent?.data
        val alertId = intent?.getStringExtra(Alerts.EXTRA_ALERT)
        if (alertId != null) Alerts.record(this, alertId, "opened")
        when {
            data != null && data.scheme == "orion" && data.host == "pair" ->
                PairPcActivity.open(this, data.getQueryParameter("u") ?: "", data.getQueryParameter("c") ?: "")  // [phone2]
            !lab.paired -> { showPair(lab.labUrl, ""); PairPcActivity.open(this) }  // [phone2] find the PC on the Wi-Fi
            else -> {
                showChat()
                if (LiveService.enabled(this)) LiveService.start(this)
                when {
                    intent?.action == Intent.ACTION_SEND -> shareToScout(intent)
                    intent?.getStringExtra(Alerts.EXTRA_CARD) != null -> showSteps()
                    intent?.getStringExtra(Alerts.EXTRA_KIND) == "approval" -> showApprovals()
                    intent?.getStringExtra(OrionMenu.EXTRA_PANE) == "settings" -> showSettings()  // [phone2]
                }
            }
        }
    }

    // ================================================================= pairing (full screen)
    private fun page(): LinearLayout {
        stage.removeAllViews(); pane = null
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(36), dp(20), dp(20)) }
        stage.addView(ScrollView(this).apply { isFillViewport = true; addView(col) })
        return col
    }

    private fun showPair(url: String, code: String) {
        val root = page()
        header(root, "Pair with your lab", "On orion-lab: Ctrl+Alt+S → Pair my phone. Scan the QR with this phone's camera, or type it below.")
        val urlBox = input("Lab address, e.g. http://192.168.1.20:9121", url)
        val codeBox = input("Pairing code", code).apply { inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS }
        val status = note("")
        root.addView(urlBox); root.addView(codeBox)
        root.addView(button("Pair this phone", primary = true) {
            status.setTextColor(quiet); status.text = "Pairing…"
            val u = urlBox.text.toString().trim(); val c = codeBox.text.toString().trim()
            Thread {
                try {
                    val fp = lab.pair(u, c, Build.MODEL ?: "Android phone")
                    ui.post { showConfirm(fp) }
                } catch (e: Exception) {
                    ui.post { status.setTextColor(red); status.text = e.message ?: e.toString() }
                }
            }.start()
        })
        root.addView(status)
        root.addView(note("At home: this phone and your PC on the same Wi-Fi (run Open Phone Access.cmd on the PC once). Away: Tailscale on both."))
    }

    private fun showConfirm(fp: String) {
        val root = page()
        header(root, "Check the fingerprint", "The lab is now showing a fingerprint and asking you to confirm it. It must match this one exactly:")
        root.addView(TextView(this).apply {
            text = fp; setTextColor(cyan); typeface = Typeface.MONOSPACE
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f); gravity = Gravity.CENTER; setPadding(0, dp(24), 0, dp(24))
        })
        root.addView(note("If it matches, type yes on the lab. If it doesn't, type no: something on the network swapped the key."))
        root.addView(button("I typed yes on the lab", primary = true) {
            lab.paired = true
            PollJob.schedule(this)
            if (LiveService.enabled(this)) LiveService.start(this)
            showChat()
        })
        root.addView(button("Start over", primary = false) { showPair(lab.labUrl, "") })
    }

    // ================================================================= the chat (home)
    private lateinit var transcript: LinearLayout
    private lateinit var transcriptScroll: ScrollView
    private lateinit var approvalsPill: Button
    private lateinit var stepsPill: Button
    private lateinit var inputBox: EditText
    private lateinit var micButton: Button
    private lateinit var healthLine: TextView
    private lateinit var freezePill: Button
    private var frozen = false
    private var speakReplies = true

    private fun showChat() {
        stage.removeAllViews(); pane = null
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), dp(18), dp(14), dp(12)) }
        stage.addView(col, FrameLayout.LayoutParams(-1, -1))

        // top: name + pills
        val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        top.addView(TextView(this).apply {
            text = "Orion"; setTextColor(Color.WHITE); typeface = Typeface.DEFAULT_BOLD; setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f)
        }, LinearLayout.LayoutParams(0, -2, 1f))
        approvalsPill = pill("Approvals") { showApprovals() }
        stepsPill = pill("Steps") { showSteps() }
        top.addView(approvalsPill); top.addView(stepsPill); top.addView(pill("⚙") { showSettings() }); top.addView(pill("Menu") { OrionMenu.openMenu(this) })  // [phone2]
        col.addView(top)

        // health line + kill switch (both from /phone/health; freeze and unfreeze need your fingerprint)
        val healthRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        healthLine = TextView(this).apply {
            text = "Checking the lab's health…"; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            maxLines = 2; setPadding(dp(4), 0, dp(6), 0)
            setOnClickListener { refreshCounts() }
        }
        healthRow.addView(healthLine, LinearLayout.LayoutParams(0, -2, 1f))
        freezePill = pill("Freeze") { toggleFreeze() }
        healthRow.addView(freezePill)
        col.addView(healthRow)

        // middle: the conversation
        transcript = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, dp(8), 0, dp(8)) }
        transcriptScroll = ScrollView(this).apply { addView(transcript); isFillViewport = true }
        col.addView(transcriptScroll, LinearLayout.LayoutParams(-1, 0, 1f))
        if (transcript.childCount == 0) bubble("Hi. Type or hold the mic and talk. Try \"status\" or \"start the server\".", mine = false)

        // quick commands row
        val quickRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        col.addView(android.widget.HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(quickRow) })
        Thread {
            val names = try { lab.quick() } catch (e: Exception) { emptyList() }
            ui.post { names.forEach { n -> quickRow.addView(pill(n) { send(n) }) } }
        }.start()

        // bottom: input + send + hold-to-talk mic
        val bottom = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; setPadding(0, dp(8), 0, 0) }
        inputBox = input("Message Orion", "").apply {
            layoutParams = LinearLayout.LayoutParams(0, -2, 1f).apply { setMargins(0, 0, dp(8), 0) }
            imeOptions = EditorInfo.IME_ACTION_SEND
            setOnEditorActionListener { _, _, _ -> sendTyped(); true }
        }
        bottom.addView(inputBox)
        bottom.addView(round("➤", violet) { sendTyped() })
        micButton = round("🎤", Color.parseColor("#00B8D9")) { }
        micButton.setOnTouchListener { v, e -> holdToTalk(v, e) }
        bottom.addView(micButton)
        col.addView(bottom)
        refreshCounts()
    }

    private fun bubble(text: String, mine: Boolean) {
        if (!::transcript.isInitialized) return
        val tv = TextView(this).apply {
            this.text = text
            setTextColor(if (mine) Color.WHITE else ink)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            background = rounded(if (mine) Color.parseColor("#2A2066") else panel, if (mine) violet else edge, 18)
        }
        val row = LinearLayout(this).apply {
            gravity = if (mine) Gravity.END else Gravity.START
            setPadding(if (mine) dp(48) else 0, dp(4), if (mine) 0 else dp(48), dp(4))
            addView(tv)
        }
        transcript.addView(row)
        transcriptScroll.post { transcriptScroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun sendTyped() {
        val t = inputBox.text.toString().trim()
        if (t.isEmpty()) return
        inputBox.setText("")
        send(t)
    }

    private fun send(text: String) {
        bubble(text, mine = true)
        bubble("…", mine = false)
        Thread {
            try {
                val r = PocketFallback.chat(this, lab, text, speakReplies)  // pocket: PC off -> Orion on this phone
                ui.post { replaceLast(PocketFallback.shown(r)); play(r) }  // pocket
            } catch (e: Exception) {
                ui.post { replaceLast(e.message ?: e.toString()) }
            }
        }.start()
    }

    /** A link shared from TikTok / YouTube / Instagram: hand it to Video Scout on the lab. */
    private fun shareToScout(intent: Intent) {
        val shared = (intent.getStringExtra(Intent.EXTRA_TEXT) ?: "").trim()
        intent.action = Intent.ACTION_MAIN            // don't scout it again when the activity is recreated
        if (shared.isEmpty()) return
        bubble("Scout: " + shared.take(300), mine = true)
        bubble("…", mine = false)
        Thread {
            try {
                val r = PocketFallback.scout(this, lab, shared)  // pocket
                ui.post { replaceLast(PocketFallback.shown(r).ifEmpty { "Scouting it." }) }  // pocket
            } catch (e: Exception) {
                ui.post { replaceLast(e.message ?: e.toString()) }
            }
        }.start()
    }

    private fun replaceLast(text: String) {
        if (transcript.childCount == 0) return
        transcript.removeViewAt(transcript.childCount - 1)
        bubble(text, mine = false)
    }

    // ---------------------------------------------------------------- voice: hold the mic, talk, let go
    private var recorder: MediaRecorder? = null
    private var recFile: File? = null
    private var recStart = 0L

    private fun holdToTalk(v: View, e: MotionEvent): Boolean {
        when (e.action) {
            MotionEvent.ACTION_DOWN -> {
                if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != 0) {
                    requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 8); return true
                }
                startRecording(); v.alpha = 0.6f
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { v.alpha = 1f; stopRecordingAndSend() }
        }
        return true
    }

    @Suppress("DEPRECATION")
    private fun startRecording() {
        val f = File(cacheDir, "orion-voice.m4a")
        recFile = f
        recorder = MediaRecorder().apply {
            setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioSamplingRate(16000); setAudioChannels(1); setAudioEncodingBitRate(32000)
            setOutputFile(f.absolutePath)
            prepare(); start()
        }
        recStart = System.currentTimeMillis()
    }

    private fun stopRecordingAndSend() {
        val r = recorder ?: return
        recorder = null
        val tooShort = System.currentTimeMillis() - recStart < 500
        try { r.stop() } catch (_: Exception) { }
        r.release()
        val f = recFile ?: return
        if (tooShort) { f.delete(); Toast.makeText(this, "Hold the mic while you talk.", Toast.LENGTH_SHORT).show(); return }
        bubble("🎤 …", mine = true)
        bubble("…", mine = false)
        Thread {
            try {
                val out = PocketFallback.voice(this, lab, f.readBytes())  // pocket
                ui.post {
                    transcript.removeViewAt(transcript.childCount - 1)
                    transcript.removeViewAt(transcript.childCount - 1)
                    bubble(out.optString("heard").ifEmpty { "🎤 (nothing heard)" }, mine = true)
                    bubble(PocketFallback.shown(out), mine = false)  // pocket
                    play(out)
                }
            } catch (e: Exception) {
                ui.post { replaceLast(e.message ?: e.toString()) }
            } finally {
                f.delete()
            }
        }.start()
    }

    /** Orion's own voice when the lab sends audio; otherwise the phone's built-in voice reads the reply. */
    private fun play(r: JSONObject) {
        if (!speakReplies) return
        val b64 = r.optString("audio_b64")
        if (b64.isNotEmpty()) {
            val f = File(cacheDir, "orion-reply.wav")
            f.writeBytes(Base64.decode(b64, Base64.DEFAULT))
            MediaPlayer().apply {
                setDataSource(f.absolutePath); prepare(); start()
                setOnCompletionListener { it.release(); f.delete() }
            }
        } else {
            val text = r.optString("reply")
            if (tts == null) tts = TextToSpeech(this) { if (it == TextToSpeech.SUCCESS) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "r") }
            else tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "r")
        }
    }

    // ================================================================= panes that slide over the chat
    private fun openPane(title: String, sub: String): LinearLayout {
        closePane(animate = false)
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(24), dp(18), dp(18)) }
        val handle = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        handle.addView(TextView(this).apply {
            text = "⌄  swipe down to go back"; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            gravity = Gravity.CENTER; setPadding(0, 0, 0, dp(8))
        })
        handle.addView(TextView(this).apply {
            text = title; setTextColor(Color.WHITE); typeface = Typeface.DEFAULT_BOLD; setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f)
        })
        if (sub.isNotEmpty()) handle.addView(TextView(this).apply {
            text = sub; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f); setPadding(0, dp(4), 0, dp(12))
        })
        val swipe = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent) = true
            override fun onFling(e1: MotionEvent?, e2: MotionEvent, vx: Float, vy: Float): Boolean {
                if (vy > 800 && (e2.y - (e1?.y ?: e2.y)) > dp(60)) { closePane(); return true }
                return false
            }
        })
        handle.setOnTouchListener { _, ev -> swipe.onTouchEvent(ev) }
        handle.setOnClickListener { }
        col.addView(handle)
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(ScrollView(this).apply { addView(body) }, LinearLayout.LayoutParams(-1, 0, 1f))
        val sheet = FrameLayout(this).apply { setBackgroundColor(void); isClickable = true; addView(col) }
        stage.addView(sheet, FrameLayout.LayoutParams(-1, -1))
        sheet.translationY = resources.displayMetrics.heightPixels.toFloat()
        sheet.animate().translationY(0f).setDuration(220).start()
        pane = sheet
        return body
    }

    private fun closePane(animate: Boolean = true) {
        val p = pane ?: return
        pane = null
        if (animate) p.animate().translationY(resources.displayMetrics.heightPixels.toFloat()).setDuration(200)
            .withEndAction { stage.removeView(p) }.start()
        else stage.removeView(p)
        refreshCounts()
    }

    private fun refreshCounts() {
        if (refreshing || !lab.paired || !::approvalsPill.isInitialized) return
        refreshing = true
        Thread {
            try {
                val snap = PollJob.check(this)
                val cards = try { lab.cards().length() } catch (e: Exception) { 0 }
                val health = try { lab.health() } catch (e: Exception) { null }
                ui.post {
                    if (health != null) showHealth(health)
                    approvalsPill.text = if (snap.decisions.isEmpty()) "Approvals" else "Approvals ${snap.decisions.size}"
                    approvalsPill.setTextColor(if (snap.decisions.isEmpty()) ink else amber)
                    stepsPill.text = if (cards == 0) "Steps" else "Steps $cards"
                    stepsPill.setTextColor(if (cards == 0) ink else cyan)
                }
            } catch (_: Exception) {
            } finally { refreshing = false }
        }.start()
    }

    // ---------------------------------------------------------------- health line and kill switch
    private fun showHealth(h: JSONObject) {
        if (!::healthLine.isInitialized) return
        frozen = h.optBoolean("frozen")
        val status = h.optString("status", "ok")
        val words = h.optString("words").ifEmpty { "Health not checked yet." }
        healthLine.text = if (frozen) "Frozen: no self-changes, updates or agents. $words" else words
        healthLine.setTextColor(when { frozen -> cyan; status == "fail" -> red; status == "warn" -> amber; else -> quiet })
        freezePill.text = if (frozen) "Unfreeze" else "Freeze"
        freezePill.setTextColor(if (frozen) cyan else ink)
    }

    private fun toggleFreeze() {
        val freeze = !frozen
        val title = if (freeze) "Freeze Orion" else "Unfreeze Orion"
        val desc = if (freeze) "Stops all self-changes, updates and agents until you unfreeze it here."
            else "Lets updates, self-changes and agents run again."
        withFingerprint(title, "Kill switch", desc, healthLine) { sig ->
            val st = lab.setFrozen(freeze, sig)
            ui.post {
                Toast.makeText(this, if (st.optBoolean("frozen")) "Orion is frozen." else "Orion is running again.",
                    Toast.LENGTH_SHORT).show()
                refreshCounts()
            }
        }
    }

    // ---------------------------------------------------------------- approvals
    private fun showApprovals() {
        val body = openPane("Approvals", "Each one needs your fingerprint.")
        val status = note("Checking the lab…")
        body.addView(status)
        Thread {
            try {
                val snap = lab.pending()
                ui.post {
                    status.text = when {
                        snap.rejected > 0 -> "${snap.rejected} hidden: their text didn't match their fingerprint. Check the lab."
                        snap.decisions.isEmpty() -> "Nothing waiting."
                        else -> if (snap.treasury.isNotEmpty()) "Paper treasury: ${snap.treasury}" else ""
                    }
                    if (snap.rejected > 0) status.setTextColor(red)
                    snap.decisions.forEach { body.addView(decisionCard(it, status)) }
                }
            } catch (e: Exception) { ui.post { status.setTextColor(red); status.text = e.message } }
        }.start()
    }

    private fun decisionCard(d: Decision, status: TextView): View {
        val c = card()
        val tag = when (d.domain) { "money" -> amber; "code" -> cyan; else -> violet }
        c.addView(TextView(this).apply {
            text = d.label.uppercase() + "  ·  risk " + d.risk
            setTextColor(tag); setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f); typeface = Typeface.DEFAULT_BOLD
        })
        c.addView(TextView(this).apply { text = d.summary; setTextColor(ink); setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f); setPadding(0, dp(6), 0, dp(4)) })
        val extra = buildList {
            d.amountCents?.let { add("Amount " + Lab.usd(it) + if (d.simulated) " (paper money)" else "") }
            add("From " + d.proposer)
            d.recommendation?.let { add("Orion recommends: $it") }
        }
        c.addView(TextView(this).apply { text = extra.joinToString("\n"); setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f) })
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(12), 0, 0) }
        row.addView(button("Approve", primary = true) { decide(d, "approve", status) }, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(6) })
        row.addView(button("Reject", primary = false) { decide(d, "reject", status) }, LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = dp(6) })
        c.addView(row)
        return c
    }

    private fun decide(d: Decision, verdict: String, status: TextView) {
        val word = if (verdict == "approve") "Approve" else "Reject"
        withFingerprint("$word: ${d.label}", d.summary,
            d.amountCents?.let { "Amount " + Lab.usd(it) + if (d.simulated) " (paper money)" else "" } ?: "", status) { sig ->
            val out = lab.verdict(d, verdict, sig)
            ui.post { Toast.makeText(this, "${d.label}: ${out.optString("status")}", Toast.LENGTH_SHORT).show(); showApprovals() }
        }
    }

    // ---------------------------------------------------------------- next-step cards
    private fun showSteps() {
        val body = openPane("Next steps", "What Orion needs on your PC. Pictures disappear after 24 hours.")
        val status = note("Checking the lab…")
        body.addView(status)
        Thread {
            try {
                val arr = lab.cards()
                ui.post {
                    status.text = if (arr.length() == 0) "Nothing to do right now." else ""
                    for (i in 0 until arr.length()) body.addView(stepCard(arr.getJSONObject(i), status))
                }
            } catch (e: Exception) { ui.post { status.setTextColor(red); status.text = e.message } }
        }.start()
    }

    private fun stepCard(m: JSONObject, status: TextView): View {
        val c = card()
        val id = m.optString("id")
        c.addView(TextView(this).apply { text = m.optString("text"); setTextColor(ink); setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f) })
        if (m.optBoolean("image")) {
            val img = ImageView(this).apply { adjustViewBounds = true; setPadding(0, dp(10), 0, dp(4)) }
            c.addView(img)
            Thread {
                val bytes = try { lab.cardImage(id) } catch (e: Exception) { null }
                if (bytes != null) {
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    ui.post { img.setImageBitmap(bmp) }
                }
            }.start()
        } else if (!m.isNull("withheld")) {
            c.addView(note(m.optString("withheld")))
        }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(10), 0, 0) }
        if (!m.isNull("target")) {
            row.addView(button("Orion does it", primary = true) {
                withFingerprint("Let Orion click this", m.optString("text"), "Orion will click it on your PC.", status) { sig ->
                    lab.cardDo(id, sig)
                    ui.post { Toast.makeText(this, "Done.", Toast.LENGTH_SHORT).show(); showSteps() }
                }
            }, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(6) })
        }
        row.addView(button("I did it", primary = m.isNull("target")) {
            Thread {
                try { lab.cardDone(id); ui.post { showSteps() } }
                catch (e: Exception) { ui.post { status.setTextColor(red); status.text = e.message } }
            }.start()
        }, LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = dp(6) })
        c.addView(row)
        return c
    }

    // ---------------------------------------------------------------- settings: alerts, voice, pairing
    private fun showSettings() {
        val body = openPane("Alerts & settings", "Orion learns how loudly to tell you about each kind of thing. Lock one to keep it as is.")
        val levels = listOf("urgent", "normal", "digest")
        val pol = Alerts.policy(this)
        for (i in 0 until pol.length()) {
            val p = pol.getJSONObject(i)
            val type = p.optString("type")
            val c = card()
            c.addView(TextView(this).apply {
                text = p.optString("label") + (if (p.optBoolean("locked")) "  🔒" else "")
                setTextColor(Color.WHITE); typeface = Typeface.DEFAULT_BOLD; setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            })
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(8), 0, 0) }
            val floor = levels.indexOf(p.optString("floor", "digest")).let { if (it < 0) 2 else it }
            levels.forEachIndexed { idx, lv ->
                if (idx > floor) return@forEachIndexed
                row.addView(pill(lv.replaceFirstChar { it.uppercase() } + if (p.optString("level") == lv) " ✓" else "") {
                    setAlert(type, lv, true)
                })
            }
            row.addView(pill(if (p.optBoolean("locked")) "Unlock" else "Lock") { setAlert(type, null, !p.optBoolean("locked")) })
            c.addView(row)
            body.addView(c)
        }
        if (pol.length() == 0) body.addView(note("Alert levels appear after the first check with the lab."))
        body.addView(button(if (LiveService.enabled(this)) "Instant alerts: on" else "Instant alerts: off (every 15 min)", primary = false) {
            LiveService.setEnabled(this, !LiveService.enabled(this)); showSettings()
        })
        body.addView(button(if (speakReplies) "Spoken replies: on" else "Spoken replies: off", primary = false) {
            speakReplies = !speakReplies; showSettings()
        })
        // phone system (Android Stage 1): checklist, inbox, call screening, PC status — PhoneSetupActivity.kt
        body.addView(button("Phone setup and inbox", primary = false) { startActivity(Intent(this, PhoneSetupActivity::class.java)) })
        body.addView(note("Paired as ${lab.myFingerprint()} · ${lab.labUrl}"))
        body.addView(button("Unpair this phone", primary = false) {
            lab.paired = false; Keys.deleteAll(); LiveService.setEnabled(this, false); showPair(lab.labUrl, "")
        })
    }

    private fun setAlert(type: String, level: String?, locked: Boolean?) {
        Thread {
            try {
                val out = lab.alertSettings(type, level, locked)
                getSharedPreferences("orion", MODE_PRIVATE).edit().putString("policy", out.optJSONArray("policy")?.toString() ?: "[]").apply()
                ui.post { showSettings() }
            } catch (e: Exception) { ui.post { Toast.makeText(this, e.message, Toast.LENGTH_LONG).show() } }
        }.start()
    }

    // ================================================================= fingerprint → signature
    private fun withFingerprint(title: String, subtitle: String, description: String, status: TextView,
                                action: (Signature) -> Unit) {
        val sig = try {
            Keys.signatureForApproval()
        } catch (e: KeyPermanentlyInvalidatedException) {
            Toast.makeText(this, "A new fingerprint was added, so the approval key was wiped. Pair this phone again.", Toast.LENGTH_LONG).show()
            lab.paired = false; Keys.deleteAll(); showPair(lab.labUrl, ""); return
        } catch (e: Exception) {
            status.setTextColor(red); status.text = e.message ?: e.toString(); return
        }
        val prompt = BiometricPrompt.Builder(this)
            .setTitle(title).setSubtitle(subtitle).setDescription(description)
            .setAllowedAuthenticators(BIOMETRIC_STRONG)
            .setNegativeButton("Cancel", mainExecutor) { _, _ -> }
            .build()
        prompt.authenticate(BiometricPrompt.CryptoObject(sig), CancellationSignal(), mainExecutor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    val unlocked = result.cryptoObject?.signature ?: return
                    status.setTextColor(quiet); status.text = "Signing…"
                    Thread {
                        try { action(unlocked) } catch (e: Exception) { ui.post { status.setTextColor(red); status.text = e.message ?: e.toString() } }
                    }.start()
                }

                override fun onAuthenticationError(code: Int, msg: CharSequence) {
                    status.setTextColor(quiet); status.text = msg
                }
            })
    }

    // ================================================================= view helpers
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun rounded(fill: Int, stroke: Int, radiusDp: Int) = GradientDrawable().apply {
        setColor(fill); cornerRadius = dp(radiusDp).toFloat(); setStroke(dp(1), stroke)
    }

    private fun card() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(14), dp(16), dp(14))
        background = rounded(panel, edge, 18)
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(6), 0, dp(10)) }
    }

    private fun header(root: LinearLayout, title: String, sub: String) {
        root.addView(TextView(this).apply { text = title; setTextColor(Color.WHITE); typeface = Typeface.DEFAULT_BOLD; setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f) })
        root.addView(TextView(this).apply { text = sub; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f); setPadding(0, dp(6), 0, dp(18)) })
    }

    private fun note(s: String) = TextView(this).apply {
        text = s; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f); setPadding(0, dp(8), 0, dp(8))
    }

    private fun input(hint: String, value: String) = EditText(this).apply {
        this.hint = hint; setText(value); setTextColor(ink); setHintTextColor(quiet)
        background = rounded(Color.parseColor("#070A14"), edge, 14)
        setPadding(dp(14), dp(12), dp(14), dp(12))
        isSingleLine = true
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, 0, 0, dp(10)) }
    }

    private fun button(label: String, primary: Boolean, onClick: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        background = if (primary) GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(violet, Color.parseColor("#00B8D9")))
            .apply { cornerRadius = dp(999).toFloat() } else rounded(Color.parseColor("#151A30"), edge, 999)
        stateListAnimator = null
        setPadding(dp(16), dp(10), dp(16), dp(10))
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(6), 0, dp(6)) }
        setOnClickListener { onClick() }
    }

    private fun pill(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; setTextColor(ink); setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        background = rounded(Color.parseColor("#151A30"), edge, 999)
        stateListAnimator = null; minHeight = 0; minimumHeight = 0; minWidth = 0; minimumWidth = 0
        setPadding(dp(12), dp(6), dp(12), dp(6))
        layoutParams = LinearLayout.LayoutParams(-2, -2).apply { setMargins(dp(3), dp(4), dp(3), dp(4)) }
        setOnClickListener { onClick() }
    }

    private fun round(label: String, fill: Int, onClick: () -> Unit) = Button(this).apply {
        text = label; setTextColor(Color.WHITE); setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(fill) }
        stateListAnimator = null
        layoutParams = LinearLayout.LayoutParams(dp(52), dp(52)).apply { setMargins(dp(4), 0, 0, 0) }
        setOnClickListener { onClick() }
    }
}
