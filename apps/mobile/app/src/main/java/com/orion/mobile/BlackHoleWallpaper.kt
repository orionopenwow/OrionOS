package com.orion.mobile

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLExt
import android.opengl.EGLSurface
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.service.wallpaper.WallpaperService
import android.view.SurfaceHolder

/**
 * The black hole as a live wallpaper (home and lock screen). Same renderer class as the home screen, on its own
 * EGL14 context and render thread. 30 fps cap, nothing at all while hidden, one still frame on battery saver or
 * with "Remove animations". Polls the PC every 30 s while visible, so it reacts like the home screen does.
 */
class BlackHoleWallpaper : WallpaperService() {
    override fun onCreateEngine(): Engine = OrionEngine()

    inner class OrionEngine : Engine() {
        private val look = BlackHoleLook()
        private val renderer = BlackHoleRenderer(this@BlackHoleWallpaper, look)
        private val thread = HandlerThread("orion-wallpaper").apply { start() }
        private val gl = Handler(thread.looper)
        private var display: EGLDisplay = EGL14.EGL_NO_DISPLAY
        private var eglContext: EGLContext = EGL14.EGL_NO_CONTEXT
        private var config: EGLConfig? = null
        private var surface: EGLSurface = EGL14.EGL_NO_SURFACE
        private var ready = false
        private var visibleNow = false
        private var useGl = true
        private var lastPoll = 0L
        private var lastMessages = -1
        @Volatile private var polling = false

        private val frame = object : Runnable {
            override fun run() {
                if (!visibleNow || !ready) return
                val now = SystemClock.elapsedRealtime() / 1000.0
                renderer.draw(now)
                EGL14.eglSwapBuffers(display, surface)
                maybePoll()
                if (look.quality > 0 || look.busy(now)) gl.postDelayed(this, BlackHoleView.FRAME_MS)
            }
        }

        override fun onCreate(surfaceHolder: SurfaceHolder) {
            super.onCreate(surfaceHolder)
            val dm = resources.displayMetrics
            surfaceHolder.setFixedSize(dm.widthPixels / 2, dm.heightPixels / 2)
            useGl = BlackHoleRenderer.supportsEs3(this@BlackHoleWallpaper)
            setOffsetNotificationsEnabled(false)
        }

        override fun onSurfaceCreated(holder: SurfaceHolder) {
            super.onSurfaceCreated(holder)
            if (!useGl) return
            gl.post {
                if (!setupEgl(holder)) { useGl = false; return@post }
                renderer.release()  // a new surface on the same context: rebuild cleanly
                ready = renderer.init()
                if (!ready) useGl = false
            }
        }

        override fun onSurfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            super.onSurfaceChanged(holder, format, width, height)
            if (useGl) gl.post { if (ready) { renderer.resize(width, height); kick() } } else drawStill(holder)
        }

        override fun onVisibilityChanged(visible: Boolean) {
            visibleNow = visible
            look.quality = if (BlackHoleRenderer.motionAllowed(this@BlackHoleWallpaper)) 1 else 0
            look.calm = look.quality == 0
            if (!useGl) { if (visible) drawStill(surfaceHolder); return }
            gl.removeCallbacks(frame)
            if (visible) kick()
        }

        override fun onSurfaceDestroyed(holder: SurfaceHolder) {
            visibleNow = false
            gl.removeCallbacks(frame)
            gl.post { teardownSurface() }
            super.onSurfaceDestroyed(holder)
        }

        override fun onDestroy() {
            gl.removeCallbacks(frame)
            gl.post {
                renderer.release()
                teardownSurface()
                if (display != EGL14.EGL_NO_DISPLAY) {
                    EGL14.eglDestroyContext(display, eglContext)
                    EGL14.eglTerminate(display)
                }
                display = EGL14.EGL_NO_DISPLAY
                thread.quitSafely()
            }
            super.onDestroy()
        }

        private fun kick() {
            gl.removeCallbacks(frame)
            gl.post(frame)
        }

        // ------------------------------------------------------------ the PC's mood, every 30 s while visible
        private fun maybePoll() {
            val t = SystemClock.elapsedRealtime()
            if (polling || t - lastPoll < 30_000) return
            lastPoll = t
            polling = true
            Thread {
                try {
                    val home = PairApi(this@BlackHoleWallpaper).home()
                    look.setCore(home.core, home.healthLevel)
                    val m = home.core.messages
                    if (lastMessages in 0 until m) look.comet(SystemClock.elapsedRealtime() / 1000.0, m - lastMessages)
                    lastMessages = m
                } finally { polling = false }
            }.start()
        }

        // ------------------------------------------------------------ EGL14 (ES 3 context on the wallpaper surface)
        private fun setupEgl(holder: SurfaceHolder): Boolean {
            if (display == EGL14.EGL_NO_DISPLAY) {
                display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
                val v = IntArray(2)
                if (!EGL14.eglInitialize(display, v, 0, v, 1)) return false
                val attrs = intArrayOf(
                    EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
                    EGL14.EGL_RENDERABLE_TYPE, EGLExt.EGL_OPENGL_ES3_BIT_KHR,
                    EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT,
                    EGL14.EGL_NONE,
                )
                val configs = arrayOfNulls<EGLConfig>(1)
                val n = IntArray(1)
                if (!EGL14.eglChooseConfig(display, attrs, 0, configs, 0, 1, n, 0) || n[0] == 0) return false
                config = configs[0]
                eglContext = EGL14.eglCreateContext(display, config, EGL14.EGL_NO_CONTEXT,
                    intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 3, EGL14.EGL_NONE), 0)
                if (eglContext == EGL14.EGL_NO_CONTEXT) return false
            }
            teardownSurface()
            surface = EGL14.eglCreateWindowSurface(display, config, holder.surface, intArrayOf(EGL14.EGL_NONE), 0)
            if (surface == EGL14.EGL_NO_SURFACE) return false
            return EGL14.eglMakeCurrent(display, surface, surface, eglContext)
        }

        private fun teardownSurface() {
            if (surface != EGL14.EGL_NO_SURFACE) {
                EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
                EGL14.eglDestroySurface(display, surface)
                surface = EGL14.EGL_NO_SURFACE
                ready = false
            }
        }

        /** No ES 3 on this phone: the painting, centred on black. */
        private fun drawStill(holder: SurfaceHolder) {
            val canvas = try { holder.lockCanvas() } catch (e: Exception) { null } ?: return
            try {
                canvas.drawColor(Color.parseColor("#05070E"))
                val bmp = BitmapFactory.decodeResource(resources, R.drawable.black_hole_core)
                if (bmp != null) {
                    val side = (minOf(canvas.width, canvas.height) * 0.8f).toInt()
                    val l = (canvas.width - side) / 2; val t = (canvas.height - side) / 2
                    canvas.drawBitmap(bmp, null, Rect(l, t, l + side, t + side), Paint(Paint.FILTER_BITMAP_FLAG))
                    bmp.recycle()
                }
            } finally { holder.unlockCanvasAndPost(canvas) }
        }
    }

    companion object {
        /** Opens Android's own wallpaper preview with Orion's black hole selected ("Set wallpaper" happens there). */
        fun pickerIntent(context: Context): Intent =
            Intent(android.app.WallpaperManager.ACTION_CHANGE_LIVE_WALLPAPER).putExtra(
                android.app.WallpaperManager.EXTRA_LIVE_WALLPAPER_COMPONENT,
                ComponentName(context, BlackHoleWallpaper::class.java),
            )
    }
}
