package com.orion.mobile

import android.content.Context
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.speech.tts.TextToSpeech
import android.util.Base64
import org.json.JSONObject
import java.io.File

/**
 * Hold-to-talk as a small shared helper (same recording settings and reply playback as the chat screen):
 * start() while the finger is down, stop() when it lifts; the clip goes to POST /phone/voice. The mic is only
 * open while you hold, and level() lets the screen show it (the black hole's ring follows your voice).
 * Voice never approves anything: approvals need the fingerprint in the chat screen.
 */
class Talk(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var file: File? = null
    private var started = 0L
    private var tts: TextToSpeech? = null

    val recording: Boolean get() = recorder != null

    @Suppress("DEPRECATION")
    fun start(): Boolean {
        if (recorder != null) return true
        val f = File(context.cacheDir, "orion-home-voice.m4a")
        return try {
            recorder = MediaRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioSamplingRate(16000); setAudioChannels(1); setAudioEncodingBitRate(32000)
                setOutputFile(f.absolutePath)
                prepare(); start()
            }
            file = f
            started = System.currentTimeMillis()
            true
        } catch (e: Exception) {
            recorder?.release(); recorder = null
            false
        }
    }

    /** 0..1, how loud the mic is right now (for the ring). */
    fun level(): Float = try {
        ((recorder?.maxAmplitude ?: 0) / 12000f).coerceIn(0f, 1f)
    } catch (e: Exception) { 0f }

    /** Stops recording. Returns the clip, or null if it was too short to be speech (the file is gone either way). */
    fun stop(): ByteArray? {
        val r = recorder ?: return null
        recorder = null
        val tooShort = System.currentTimeMillis() - started < 500
        try { r.stop() } catch (_: Exception) { }
        r.release()
        val f = file ?: return null
        file = null
        return try { if (tooShort) null else f.readBytes() } finally { f.delete() }
    }

    /** Orion's own voice when the PC sends audio; otherwise the phone's built-in voice reads the reply. */
    fun play(r: JSONObject) {
        val b64 = r.optString("audio_b64")
        if (b64.isNotEmpty()) {
            val f = File(context.cacheDir, "orion-home-reply.wav")
            f.writeBytes(Base64.decode(b64, Base64.DEFAULT))
            try {
                MediaPlayer().apply {
                    setDataSource(f.absolutePath); prepare(); start()
                    setOnCompletionListener { it.release(); f.delete() }
                }
            } catch (e: Exception) { f.delete() }
        } else {
            val text = r.optString("reply")
            if (text.isEmpty()) return
            val t = tts
            if (t == null) tts = TextToSpeech(context) { if (it == TextToSpeech.SUCCESS) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "r") }
            else t.speak(text, TextToSpeech.QUEUE_FLUSH, null, "r")
        }
    }

    fun release() {
        stop()
        tts?.shutdown(); tts = null
    }
}
