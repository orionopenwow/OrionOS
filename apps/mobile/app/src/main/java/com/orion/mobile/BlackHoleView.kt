package com.orion.mobile

import android.content.Context
import android.graphics.Bitmap
import android.opengl.GLSurfaceView
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/**
 * The home screen's black hole: a GLSurfaceView (ES 3) that draws at most 30 frames a second, renders below
 * screen size (the GPU stays cool; the picture is soft by nature), stops when it isn't visible, and drops to one
 * still frame when [moving] is false (battery saver, "Remove animations", or a glass panel covering it).
 */
class BlackHoleView(context: Context, val look: BlackHoleLook, private val onFailed: () -> Unit) : GLSurfaceView(context) {
    private val renderer = BlackHoleRenderer(context, look)
    private val ui = Handler(Looper.getMainLooper())
    private var running = false
    /** Fraction of the screen's pixels the shader draws (0.5 = a quarter of the pixels). */
    var renderScale = 0.5f
        set(v) { field = v; if (width > 0) applyScale(width, height) }

    /** False = one still frame (redrawn only when the mood changes). */
    var moving = true
        set(v) { field = v; kick() }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            val now = SystemClock.elapsedRealtime() / 1000.0
            if (moving || look.busy(now)) {
                requestRender()
                ui.postDelayed(this, FRAME_MS)
            }
        }
    }

    init {
        setEGLContextClientVersion(3)
        setEGLConfigChooser(8, 8, 8, 8, 0, 0)
        preserveEGLContextOnPause = true
        setRenderer(object : Renderer {
            override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
                if (!renderer.init()) ui.post { onFailed() }
            }
            override fun onSurfaceChanged(gl: GL10?, w: Int, h: Int) = renderer.resize(w, h)
            override fun onDrawFrame(gl: GL10?) = renderer.draw(SystemClock.elapsedRealtime() / 1000.0)
        })
        renderMode = RENDERMODE_WHEN_DIRTY
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        applyScale(w, h)
    }

    private fun applyScale(w: Int, h: Int) {
        if (w <= 0 || h <= 0) return
        holder.setFixedSize(maxOf(1, (w * renderScale).toInt()), maxOf(1, (h * renderScale).toInt()))
    }

    /** Start (or restart) the frame loop; one frame is always drawn so a still screen is never empty. */
    fun kick() {
        if (!running) return
        ui.removeCallbacks(tick)
        requestRender()
        ui.postDelayed(tick, FRAME_MS)
    }

    fun start() {
        running = true
        onResume()
        kick()
    }

    fun stop() {
        running = false
        ui.removeCallbacks(tick)
        onPause()
    }

    /** The current frame as a Bitmap, on the UI thread (null if it couldn't be read). */
    fun snapshot(cb: (Bitmap?) -> Unit) {
        renderer.requestSnapshot { bmp -> ui.post { cb(bmp) } }
        requestRender()
    }

    companion object {
        const val FRAME_MS = 33L  // 30 fps cap
    }
}
