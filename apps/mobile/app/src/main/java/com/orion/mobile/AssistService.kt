package com.orion.mobile

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.util.Base64
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import java.io.File

/*
 * Orion as the phone's digital assistant (Settings > Apps > Default apps > Digital assistant app).
 * Long-press power (or the assist gesture / button) shows our session, which opens AssistActivity:
 * a glass sheet that starts listening at once, uploads the clip to the PC's /phone/voice (the same call
 * and recording settings as hold-to-talk in MainActivity) and shows and speaks Orion's reply.
 * Nothing said here can approve anything: approvals only happen with the fingerprint in the Approvals pane.
 */

/** The assistant service itself. Android keeps it bound while Orion is the chosen assistant. */
class AssistService : VoiceInteractionService()

/** Hands out one session per long-press. */
class AssistSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = AssistSession(this)
}

/** The session has no window of its own: it opens the listening sheet and steps aside. */
class AssistSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        val i = Intent(context, AssistActivity::class.java).putExtra(AssistActivity.EXTRA_FROM, "assistant")
        try {
            startAssistantActivity(i)
        } catch (e: Exception) {
            context.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
        hide()
    }
}

/**
 * Android requires an assistant to name a speech recognizer. Orion's speech-to-text runs on your PC, not as a
 * phone-wide recognizer, so this one politely declines; other apps keep using the phone's normal recognizer.
 */
class AssistRecognitionService : RecognitionService() {
    override fun onStartListening(recognizerIntent: Intent?, listener: Callback?) {
        try { listener?.error(SpeechRecognizer.ERROR_CLIENT) } catch (_: Exception) { }
    }
    override fun onCancel(listener: Callback?) {}
    override fun onStopListening(listener: Callback?) {}
}

/**
 * The listening sheet. Opened by the assistant session, by ACTION_ASSIST / ACTION_VOICE_COMMAND (headset button,
 * "open assistant" shortcuts), or by anything else that wants Orion listening.
 * Records only while the sheet says "Listening", at most 30 seconds; tap Done to send, Cancel to throw it away.
 */
class AssistActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var lab: Lab
    private lateinit var status: TextView
    private lateinit var heard: TextView
    private lateinit var reply: TextView
    private lateinit var dot: View
    private lateinit var actions: LinearLayout
    private var recorder: MediaRecorder? = null
    private var recFile: File? = null
    private var recStart = 0L
    private var tts: TextToSpeech? = null
    private var player: MediaPlayer? = null
    private val autoStop = Runnable { stopAndSend() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lab = Lab(this)
        build()
        if (!lab.paired) {
            status.text = "Pair this phone with your PC first: open Orion and follow the steps."
            showActions(listening = false)
            return
        }
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != 0) {
            status.text = "Orion needs the microphone to hear you. It only listens while this sheet says Listening."
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), REQ_MIC)
        } else startListening()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode == REQ_MIC) {
            if (grantResults.isNotEmpty() && grantResults[0] == 0) startListening()
            else { status.text = "No microphone permission, so Orion can't hear you."; showActions(false) }
        }
    }

    override fun onPause() {
        super.onPause()
        if (recorder != null) cancelRecording()   // never keep recording once the sheet is gone
    }

    override fun onDestroy() {
        ui.removeCallbacks(autoStop)
        cancelRecording()
        tts?.shutdown()
        player?.release()
        super.onDestroy()
    }

    // ---------------------------------------------------------------- layout: a glass sheet at the bottom
    private fun build() {
        val c = this
        val root = FrameLayout(c).apply {
            setBackgroundColor(Color.parseColor("#99000000"))
            setOnClickListener { if (recorder == null) finish() }
        }
        val sheet = LinearLayout(c).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(SysUi.dp(c, 20), SysUi.dp(c, 18), SysUi.dp(c, 20), SysUi.dp(c, 22))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#E60B0F1E"))
                cornerRadii = FloatArray(8) { i -> if (i < 4) SysUi.dp(c, 26).toFloat() else 0f }
                setStroke(SysUi.dp(c, 1), SysUi.edge)
            }
            isClickable = true
        }
        val top = SysUi.row(c)
        dot = View(c).apply {
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(SysUi.cyan) }
            layoutParams = LinearLayout.LayoutParams(SysUi.dp(c, 12), SysUi.dp(c, 12)).apply { marginEnd = SysUi.dp(c, 10) }
            visibility = View.INVISIBLE
        }
        top.addView(dot)
        status = TextView(c).apply {
            text = "Orion"; setTextColor(Color.WHITE); typeface = Typeface.DEFAULT_BOLD
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
        }
        top.addView(status, LinearLayout.LayoutParams(0, -2, 1f))
        sheet.addView(top)
        heard = SysUi.text(c, "", 15f, Color.WHITE).apply { visibility = View.GONE; setPadding(0, SysUi.dp(c, 12), 0, 0) }
        reply = SysUi.text(c, "", 16f, SysUi.ink).apply { visibility = View.GONE; setPadding(0, SysUi.dp(c, 8), 0, 0) }
        sheet.addView(heard); sheet.addView(reply)
        actions = SysUi.row(c).apply { setPadding(0, SysUi.dp(c, 14), 0, 0) }
        sheet.addView(actions)
        root.addView(sheet, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
        setContentView(root)
    }

    private fun showActions(listening: Boolean) {
        actions.removeAllViews()
        if (listening) {
            actions.addView(SysUi.weighted(SysUi.button(this, "Done", primary = true) { stopAndSend() }))
            actions.addView(SysUi.weighted(SysUi.button(this, "Cancel", primary = false) { cancelRecording(); finish() }))
        } else {
            if (lab.paired) actions.addView(SysUi.weighted(SysUi.button(this, "Talk again", primary = true) { startListening() }))
            actions.addView(SysUi.weighted(SysUi.button(this, "Open Orion", primary = false) {
                startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); finish()
            }))
        }
        for (i in 0 until actions.childCount) {
            (actions.getChildAt(i).layoutParams as LinearLayout.LayoutParams).setMargins(SysUi.dp(this, 4), 0, SysUi.dp(this, 4), 0)
        }
    }

    // ---------------------------------------------------------------- recording (same settings as hold-to-talk)
    @Suppress("DEPRECATION")
    private fun startListening() {
        player?.stop(); tts?.stop()
        val f = File(cacheDir, "orion-assist.m4a")
        recFile = f
        try {
            recorder = MediaRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioSamplingRate(16000); setAudioChannels(1); setAudioEncodingBitRate(32000)
                setOutputFile(f.absolutePath)
                prepare(); start()
            }
        } catch (e: Exception) {
            recorder = null
            status.text = "The microphone is busy. Try again in a moment."
            showActions(false); return
        }
        recStart = System.currentTimeMillis()
        status.text = "Listening"
        dot.visibility = View.VISIBLE
        dot.animate().alpha(0.3f).setDuration(600).withEndAction { pulse() }.start()
        showActions(listening = true)
        ui.removeCallbacks(autoStop)
        ui.postDelayed(autoStop, 30_000)
    }

    private fun pulse() {
        if (recorder == null) { dot.alpha = 1f; return }
        dot.animate().alpha(if (dot.alpha < 0.5f) 1f else 0.3f).setDuration(600).withEndAction { pulse() }.start()
    }

    private fun cancelRecording() {
        ui.removeCallbacks(autoStop)
        val r = recorder ?: return
        recorder = null
        try { r.stop() } catch (_: Exception) { }
        r.release()
        recFile?.delete()
        if (::dot.isInitialized) dot.visibility = View.INVISIBLE
    }

    private fun stopAndSend() {
        ui.removeCallbacks(autoStop)
        val r = recorder ?: return
        recorder = null
        dot.visibility = View.INVISIBLE
        val tooShort = System.currentTimeMillis() - recStart < 600
        try { r.stop() } catch (_: Exception) { }
        r.release()
        val f = recFile ?: return
        if (tooShort) { f.delete(); status.text = "I didn't catch that."; showActions(false); return }
        status.text = "Thinking"
        actions.removeAllViews()
        Thread {
            try {
                val out = PocketFallback.voice(this, lab, f.readBytes())  // PC off: Orion on this phone
                ui.post { showReply(out) }
            } catch (e: Exception) {
                ui.post { status.text = "Orion"; reply.visibility = View.VISIBLE; reply.setTextColor(SysUi.red)
                    reply.text = e.message ?: e.toString(); showActions(false) }
            } finally { f.delete() }
        }.start()
    }

    private fun showReply(out: JSONObject) {
        status.text = "Orion"
        heard.visibility = View.VISIBLE
        heard.text = out.optString("heard").ifEmpty { "(nothing heard)" }
        reply.visibility = View.VISIBLE
        reply.setTextColor(SysUi.ink)
        reply.text = PocketFallback.shown(out)
        showActions(false)
        speak(out)
    }

    /** Orion's own voice when the PC sends audio; otherwise the phone's built-in voice reads the reply. */
    private fun speak(r: JSONObject) {
        val b64 = r.optString("audio_b64")
        if (b64.isNotEmpty()) {
            val f = File(cacheDir, "orion-assist-reply.wav")
            f.writeBytes(Base64.decode(b64, Base64.DEFAULT))
            player?.release()
            player = MediaPlayer().apply {
                setDataSource(f.absolutePath); prepare(); start()
                setOnCompletionListener { f.delete() }
            }
        } else {
            val text = r.optString("reply")
            if (text.isEmpty()) return
            if (tts == null) tts = TextToSpeech(this) { if (it == TextToSpeech.SUCCESS) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "a") }
            else tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "a")
        }
    }

    companion object {
        const val EXTRA_FROM = "orion_assist_from"
        private const val REQ_MIC = 31
    }
}
