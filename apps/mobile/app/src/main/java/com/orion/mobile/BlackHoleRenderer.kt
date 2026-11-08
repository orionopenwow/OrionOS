package com.orion.mobile

import android.animation.ValueAnimator
import android.app.ActivityManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.opengl.GLES30
import android.opengl.GLUtils
import android.os.PowerManager
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

/**
 * Orion's living black hole on the phone's GPU (OpenGL ES 3.0).
 *
 * It runs the very same fragment shader as the desktop (os/theme/blackhole/blackhole.frag, GLSL ES 3.00, bundled
 * as assets/blackhole.frag) over one full-screen triangle, with the same painted disk (black-hole-core.png).
 * The class is GL-thread only and has no idea who owns the surface: HomeActivity's GLSurfaceView and the live
 * wallpaper's own EGL14 context both drive it through init / resize / draw.
 */
class BlackHoleRenderer(private val context: Context, val look: BlackHoleLook) {
    private var program = 0
    private var texture = 0
    private var width = 1
    private var height = 1
    private val loc = HashMap<String, Int>()
    private val bodies = FloatArray(BODY_SLOTS * 4)
    @Volatile var failed = false
        private set

    /** Asked from the UI thread; filled on the next draw (the glass panels use it as their frosted backdrop). */
    @Volatile private var snapshotWanted: ((Bitmap?) -> Unit)? = null

    /** Call on the GL thread with a current ES 3 context. Returns false if the shader can't run here. */
    fun init(): Boolean {
        failed = false
        loc.clear()
        return try {
            val frag = context.assets.open("blackhole.frag").bufferedReader().use { it.readText() }
            program = link(compile(GLES30.GL_VERTEX_SHADER, VERTEX), compile(GLES30.GL_FRAGMENT_SHADER, frag))
            texture = loadDisk()
            true
        } catch (e: Exception) {
            android.util.Log.w("Orion", "Black hole shader unavailable: ${e.message}")
            failed = true
            false
        }
    }

    fun resize(w: Int, h: Int) {
        width = max(1, w); height = max(1, h)
        GLES30.glViewport(0, 0, width, height)
    }

    fun requestSnapshot(cb: (Bitmap?) -> Unit) { snapshotWanted = cb }

    /** Draws one frame. [nowS] is a monotonic clock in seconds. */
    fun draw(nowS: Double) {
        if (failed || program == 0) {
            GLES30.glClearColor(0.02f, 0.027f, 0.055f, 1f)
            GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT)
            return
        }
        val u = look.step(nowS)
        GLES30.glUseProgram(program)
        GLES30.glActiveTexture(GLES30.GL_TEXTURE0)
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texture)
        GLES30.glUniform1i(l("u_disk"), 0)
        GLES30.glUniform2f(l("u_resolution"), width.toFloat(), height.toFloat())
        GLES30.glUniform1f(l("u_time"), u.time)
        GLES30.glUniform1f(l("u_flow"), u.flow)
        GLES30.glUniform1f(l("u_spin"), u.spin)
        GLES30.glUniform1f(l("u_temp"), u.temp)
        GLES30.glUniform1f(l("u_ring"), u.ring)
        GLES30.glUniform1f(l("u_glow"), u.glow)
        GLES30.glUniform1f(l("u_turbulence"), u.turbulence)
        GLES30.glUniform1f(l("u_size"), u.size)
        GLES30.glUniform1f(l("u_tilt"), 0f)
        GLES30.glUniform1f(l("u_boot"), -1f)
        GLES30.glUniform1f(l("u_voice"), u.voice)
        GLES30.glUniform1f(l("u_ripple"), u.ripple)
        GLES30.glUniform1f(l("u_quality"), u.quality.toFloat())
        GLES30.glUniform2f(l("u_parallax"), u.parallaxX, u.parallaxY)
        GLES30.glUniform1f(l("u_hue"), u.hue)
        GLES30.glUniform1f(l("u_transparent"), 0f)
        GLES30.glUniform1f(l("u_flare"), u.flare)
        GLES30.glUniform1f(l("u_ring2"), 0f)
        look.fillBodies(bodies, nowS)
        GLES30.glUniform4fv(l("u_bodies[0]"), BODY_SLOTS, bodies, 0)
        GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3)
        snapshotWanted?.let { cb -> snapshotWanted = null; cb(readPixels()) }
    }

    fun release() {
        if (program != 0) GLES30.glDeleteProgram(program)
        if (texture != 0) GLES30.glDeleteTextures(1, intArrayOf(texture), 0)
        program = 0; texture = 0
    }

    // ------------------------------------------------------------------ plumbing
    private fun l(name: String): Int = loc.getOrPut(name) { GLES30.glGetUniformLocation(program, name) }

    private fun compile(type: Int, src: String): Int {
        val s = GLES30.glCreateShader(type)
        GLES30.glShaderSource(s, src)
        GLES30.glCompileShader(s)
        val ok = IntArray(1)
        GLES30.glGetShaderiv(s, GLES30.GL_COMPILE_STATUS, ok, 0)
        if (ok[0] == 0) {
            val log = GLES30.glGetShaderInfoLog(s)
            GLES30.glDeleteShader(s)
            throw IllegalStateException("compile: $log")
        }
        return s
    }

    private fun link(vs: Int, fs: Int): Int {
        val p = GLES30.glCreateProgram()
        GLES30.glAttachShader(p, vs)
        GLES30.glAttachShader(p, fs)
        GLES30.glLinkProgram(p)
        GLES30.glDeleteShader(vs)
        GLES30.glDeleteShader(fs)
        val ok = IntArray(1)
        GLES30.glGetProgramiv(p, GLES30.GL_LINK_STATUS, ok, 0)
        if (ok[0] == 0) {
            val log = GLES30.glGetProgramInfoLog(p)
            GLES30.glDeleteProgram(p)
            throw IllegalStateException("link: $log")
        }
        return p
    }

    /**
     * The painting, as the shader contract asks: flipped so row 0 is the bottom (UNPACK_FLIP_Y), premultiplied
     * (Android bitmaps already are, and GLUtils uploads them that way), mipmapped, clamped.
     */
    private fun loadDisk(): Int {
        val opts = BitmapFactory.Options().apply { inScaled = false; inPremultiplied = true }
        val src = BitmapFactory.decodeResource(context.resources, R.drawable.black_hole_core, opts)
            ?: throw IllegalStateException("disk painting missing")
        val flipped = Bitmap.createBitmap(src, 0, 0, src.width, src.height, Matrix().apply { preScale(1f, -1f) }, false)
        if (flipped !== src) src.recycle()
        val ids = IntArray(1)
        GLES30.glGenTextures(1, ids, 0)
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, ids[0])
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_LINEAR_MIPMAP_LINEAR)
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MAG_FILTER, GLES30.GL_LINEAR)
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_WRAP_S, GLES30.GL_CLAMP_TO_EDGE)
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_WRAP_T, GLES30.GL_CLAMP_TO_EDGE)
        GLUtils.texImage2D(GLES30.GL_TEXTURE_2D, 0, flipped, 0)
        GLES30.glGenerateMipmap(GLES30.GL_TEXTURE_2D)
        flipped.recycle()
        return ids[0]
    }

    /** The frame just drawn, as a Bitmap the right way up (small: the surface is rendered below screen size). */
    private fun readPixels(): Bitmap? = try {
        val buf = ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder())
        GLES30.glReadPixels(0, 0, width, height, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, buf)
        buf.rewind()
        val bmp = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        bmp.copyPixelsFromBuffer(buf)
        val up = Bitmap.createBitmap(bmp, 0, 0, width, height, Matrix().apply { preScale(1f, -1f) }, false)
        if (up !== bmp) bmp.recycle()
        up
    } catch (e: Throwable) {
        null
    }

    companion object {
        const val BODY_SLOTS = 10

        /** Full-screen triangle from gl_VertexID; no buffers, no varyings (the shader only uses gl_FragCoord). */
        private const val VERTEX = "#version 300 es\n" +
            "void main() {\n" +
            "  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));\n" +
            "  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n" +
            "}\n"

        /** True when the phone has OpenGL ES 3.0 (otherwise the home screen shows the painting as a still). */
        fun supportsEs3(context: Context): Boolean {
            val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            return am.deviceConfigurationInfo.reqGlEsVersion >= 0x30000
        }

        /** Motion is off when battery saver is on or the system "Remove animations" setting is set. */
        fun motionAllowed(context: Context): Boolean {
            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            if (pm.isPowerSaveMode) return false
            return ValueAnimator.areAnimatorsEnabled()
        }
    }
}

/** The values the shader gets on one frame. */
class Uniforms {
    var time = 7f; var flow = 7f
    var spin = 1f; var temp = 0f; var ring = 1f; var glow = 1f; var turbulence = 0.25f; var size = 1f
    var voice = 0f; var ripple = 0f; var hue = 0f; var flare = 0f
    var parallaxX = 0f; var parallaxY = 0f
    var quality = 1
}

/**
 * The black hole's mood: the PC's core signals (from GET /phone/home) plus the phone's own (the mic level while
 * you hold the core). Every value eases, gently, like the desktop's applySignals (calm liveliness).
 * Shared between the UI thread (which sets targets) and the GL thread (which steps it), so it is synchronized.
 */
class BlackHoleLook {
    /** 0 Still, 1 Battery, 2 Balanced, 3 Ultra (os/theme/blackhole/README.md). */
    @Volatile var quality = 1
    /** Calm motion: comets fade in place instead of travelling; stars don't orbit. */
    @Volatile var calm = false

    private val target = FloatArray(N)
    private val level = FloatArray(N)
    private var flare = 0f
    private var last = -1.0
    private val u = Uniforms()
    private val comets = ArrayList<DoubleArray>()   // [born, angle]
    private var seq = 0
    private var micLevel = 0f
    private var tiltX = 0f
    private var tiltY = 0f

    @Synchronized fun setCore(c: CoreSignals, healthLevel: Int) {
        target[VOICE] = c.voice
        target[RIPPLE] = c.speaking
        target[THINKING] = c.thinking
        target[HEAT] = c.heat
        target[QUIET] = c.quiet
        target[HEALTH] = (healthLevel.coerceIn(0, 3)) / 3f
    }

    /** The phone's own mic while you hold the core (0..1). Drives the ring like the desktop's hot mic. */
    @Synchronized fun setMic(v: Float) { micLevel = v.coerceIn(0f, 1f) }

    /** Gentle parallax for the stars (from touch position), -1..1. */
    @Synchronized fun setParallax(x: Float, y: Float) { tiltX = x.coerceIn(-1f, 1f); tiltY = y.coerceIn(-1f, 1f) }

    /** A new message: a small ember comet falls into the hole. */
    @Synchronized fun comet(now: Double, count: Int = 1) {
        repeat(min(count, 3)) {
            seq++
            val x = sin(seq * 12.9898 + 78.233) * 43758.5453
            comets.add(doubleArrayOf(now + it * 0.7, (x - kotlin.math.floor(x)) * 2 * PI))
        }
        while (comets.size > 3) comets.removeAt(0)
    }

    /** Something fell in (an approval was decided, a file was fed): the disk flares from the inside out. */
    @Synchronized fun flare(strength: Float = 1f) { flare = max(flare, strength.coerceIn(0f, 1f) * (if (calm) 0.5f else 1f)) }

    /** True while a comet or flare is playing, so a Still home screen knows to draw a few frames. */
    @Synchronized fun busy(now: Double): Boolean = flare > 0.01f || comets.any { now - it[0] < COMET_DUR }

    @Synchronized fun step(now: Double): Uniforms {
        val dt = if (last < 0) 0.0 else (now - last).coerceIn(0.0, 0.25)
        last = now
        target[MIC] = micLevel
        for (i in 0 until N) {
            val k = 1f - exp(-(dt / TAU[i])).toFloat()
            level[i] += (target[i] - level[i]) * k
        }
        flare *= exp(-dt / 0.9).toFloat()
        val thinking = level[THINKING]; val heat = level[HEAT]; val quiet = level[QUIET]; val health = level[HEALTH]
        u.spin = 1f * (1 + 0.5f * thinking) * (1 - 0.35f * quiet)
        u.temp = (0.2f * thinking + 0.45f * heat + 0.25f * health).coerceIn(-1f, 1f)
        u.ring = (1 + 0.2f * thinking) * (1 - 0.2f * quiet)
        u.glow = 1f * (1 - 0.3f * quiet)
        u.turbulence = 0.25f + 0.2f * health
        u.size = 1f
        u.voice = max(level[VOICE], level[MIC])
        u.ripple = level[RIPPLE]
        u.flare = flare
        u.hue = if (level[MIC] > 0.02f) -0.35f * min(1f, level[MIC] * 3f) else 0f
        u.time += dt.toFloat()
        u.flow += (dt * u.spin).toFloat()
        u.parallaxX += (tiltX - u.parallaxX) * 0.05f
        u.parallaxY += (tiltY - u.parallaxY) * 0.05f
        u.quality = quality
        comets.removeAll { now - it[0] > COMET_DUR }
        return u
    }

    /** Comets as u_bodies floats (x, y, brightness, colour + tail), same path as the desktop's reactions.ts. */
    @Synchronized fun fillBodies(out: FloatArray, now: Double) {
        java.util.Arrays.fill(out, 0f)
        var i = 0
        for (c in comets) {
            if (i >= BlackHoleRenderer.BODY_SLOTS) break
            val t = ((now - c[0]) / COMET_DUR).toFloat()
            if (t < 0f || t >= 1f) continue
            val angle: Double; val r: Float; val tail: Float; val fade: Float
            if (calm) {
                angle = c[1]; r = 0.3f; tail = 0f
                fade = smooth(0f, 0.2f, t) * (1 - smooth(0.65f, 1f, t))
            } else {
                val fall = t.toDouble().pow(1.6).toFloat()
                r = 0.47f + (0.088f - 0.47f) * fall
                angle = c[1] + 0.9 * t + 1.8 * t * t
                tail = 0.55f
                fade = smooth(0f, 0.12f, t) * (1 - smooth(0.85f, 1f, t))
            }
            out[i * 4] = (cos(angle) * r).toFloat()
            out[i * 4 + 1] = (sin(angle) * r).toFloat()
            out[i * 4 + 2] = fade
            out[i * 4 + 3] = 1f + tail   // 1 = ember
            i++
        }
    }

    private fun smooth(e0: Float, e1: Float, x: Float): Float {
        val t = ((x - e0) / (e1 - e0)).coerceIn(0f, 1f)
        return t * t * (3 - 2 * t)
    }

    private companion object {
        const val VOICE = 0; const val RIPPLE = 1; const val THINKING = 2; const val HEAT = 3
        const val QUIET = 4; const val HEALTH = 5; const val MIC = 6; const val N = 7
        val TAU = doubleArrayOf(0.25, 0.4, 2.5, 2.5, 6.0, 3.0, 0.12)
        const val COMET_DUR = 6.5
    }
}
