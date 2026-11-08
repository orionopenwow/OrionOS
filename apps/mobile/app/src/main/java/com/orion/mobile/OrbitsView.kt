package com.orion.mobile

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.os.SystemClock
import android.view.View
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.sin

/**
 * Drawn over the black hole: approval stars orbit just outside the disk, agent robots orbit further out with a
 * one-line status bubble. Taps are handled by HomeActivity through [hit]. Positions use the shader's units
 * (0.5 = half the screen's shorter side), so stars sit where the desktop puts them.
 */
class OrbitsView(context: Context) : View(context) {
    var approvals: List<HomeApproval> = emptyList()
        set(v) { field = v; invalidate() }
    var agents: List<HomeAgent> = emptyList()
        set(v) { field = v; invalidate() }
    /** False = calm motion: nothing travels, stars just sit where they are. */
    var moving = true
        set(v) { field = v; invalidate() }
    /** Where the core sits (desktop mode can move it); defaults to the middle. */
    var centerX = -1f
    var centerY = -1f

    private val density = resources.displayMetrics.density
    private val start = SystemClock.elapsedRealtime()
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG)
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 1.2f * density; color = Color.WHITE }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = density; color = Color.parseColor("#3DFFFFFF")
    }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Glass.INK; textSize = 12f * density; typeface = Glass.body(context)
    }

    sealed class Hit {
        data class Star(val approval: HomeApproval) : Hit()
        data class Robot(val agent: HomeAgent) : Hit()
        object Core : Hit()
    }

    private fun cx() = if (centerX >= 0) centerX else width / 2f
    private fun cy() = if (centerY >= 0) centerY else height / 2f
    private fun unit() = min(width, height).toFloat()
    private fun phase(): Double = if (moving) (SystemClock.elapsedRealtime() - start) / 1000.0 * ORBIT_SPEED else 0.0

    private fun starPos(i: Int, n: Int): Pair<Float, Float> {
        val a = PI * 0.25 + i * 2 * PI / maxOf(n, 1) + phase()
        val r = ORBIT_R * unit()
        return Pair(cx() + (cos(a) * r).toFloat(), cy() - (sin(a) * r).toFloat())
    }

    /** Robots ride a wider, taller ellipse so they use the phone's height and never sit on the disk. */
    private fun robotPos(i: Int, n: Int): Pair<Float, Float> {
        val a = PI * 0.75 + i * 2 * PI / maxOf(n, 1) - phase() * 0.7
        val rx = min(0.46f * unit(), width / 2f - 40 * density)
        val ry = min(0.75f * unit(), height / 2f - 120 * density).coerceAtLeast(rx)
        return Pair(cx() + (cos(a) * rx).toFloat(), cy() - (sin(a) * ry).toFloat())
    }

    fun hit(x: Float, y: Float): Hit? {
        val reach = 28 * density
        agents.take(MAX_ROBOTS).forEachIndexed { i, ag ->
            val (px, py) = robotPos(i, min(agents.size, MAX_ROBOTS))
            if (hypot(x - px, y - py) < reach) return Hit.Robot(ag)
        }
        approvals.take(MAX_STARS).forEachIndexed { i, ap ->
            val (px, py) = starPos(i, min(approvals.size, MAX_STARS))
            if (hypot(x - px, y - py) < reach) return Hit.Star(ap)
        }
        if (hypot(x - cx(), y - cy()) < CORE_R * unit()) return Hit.Core
        return null
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val t = (SystemClock.elapsedRealtime() - start) / 1000.0
        val stars = approvals.take(MAX_STARS)
        stars.forEachIndexed { i, ap ->
            val (x, y) = starPos(i, stars.size)
            val tw = if (moving) (0.88 + 0.12 * sin(t * 1.7 + i * 3)).toFloat() else 1f
            drawStar(canvas, x, y, if (ap.risk == "high") Glass.EMBER else Glass.PHOTON, tw)
        }
        val bots = agents.take(MAX_ROBOTS)
        bots.forEachIndexed { i, ag ->
            val (x, y) = robotPos(i, bots.size)
            drawRobot(canvas, x, y, ag)
        }
        if (moving && (stars.isNotEmpty() || bots.isNotEmpty())) postInvalidateDelayed(BlackHoleView.FRAME_MS)
    }

    private fun drawStar(c: Canvas, x: Float, y: Float, tint: Int, k: Float) {
        val r = 14 * density
        glow.shader = RadialGradient(x, y, r, intArrayOf(Color.argb((200 * k).toInt(), 255, 255, 255),
            (tint and 0x00FFFFFF) or (((120 * k).toInt()) shl 24), Color.TRANSPARENT), floatArrayOf(0f, 0.25f, 1f), Shader.TileMode.CLAMP)
        c.drawCircle(x, y, r, glow)
        line.alpha = (170 * k).toInt()
        val g = 9 * density * k
        c.drawLine(x - g, y, x + g, y, line)
        c.drawLine(x, y - g, x, y + g, line)
    }

    private fun drawRobot(c: Canvas, x: Float, y: Float, ag: HomeAgent) {
        val s = 11 * density
        val tint = when (ag.state) {
            "paused", "review", "needs_review", "waiting_review" -> Glass.EMBER
            "error", "failed", "stuck" -> Glass.ROSE
            else -> Glass.AURORA
        }
        // head
        fill.color = Color.parseColor("#CC0B0F1E")
        val head = RectF(x - s, y - s * 0.8f, x + s, y + s * 0.8f)
        c.drawRoundRect(head, s * 0.45f, s * 0.45f, fill)
        edge.color = tint
        c.drawRoundRect(head, s * 0.45f, s * 0.45f, edge)
        // eyes + antenna
        fill.color = tint
        c.drawCircle(x - s * 0.4f, y - s * 0.05f, s * 0.17f, fill)
        c.drawCircle(x + s * 0.4f, y - s * 0.05f, s * 0.17f, fill)
        c.drawLine(x, y - s * 0.8f, x, y - s * 1.25f, edge)
        c.drawCircle(x, y - s * 1.35f, s * 0.14f, fill)
        edge.color = Color.parseColor("#3DFFFFFF")
        // one-line status bubble
        val cost = ag.cost()   // what this robot has spent so far (and its budget), when the PC says
        val limit = if (cost.isEmpty()) 30 else 22
        val words = (ag.status.ifEmpty { ag.state.ifEmpty { "working" } }).let { if (it.length > limit) it.take(limit - 1) + "…" else it } +
            (if (cost.isEmpty()) "" else " · $cost")
        val tw = label.measureText(words)
        val pad = 8 * density
        var bx = x - tw / 2 - pad
        bx = bx.coerceIn(4 * density, width - tw - 2 * pad - 4 * density)
        val by = y + s * 1.3f
        val bubble = RectF(bx, by, bx + tw + 2 * pad, by + label.textSize + pad * 1.2f)
        fill.color = Color.parseColor("#B30B0F1E")
        c.drawRoundRect(bubble, 999f, 999f, fill)
        c.drawRoundRect(bubble, 999f, 999f, edge)
        c.drawText(words, bx + pad, by + label.textSize + pad * 0.35f, label)
    }

    companion object {
        const val ORBIT_R = 0.43f        // same as the desktop's reactions.ts
        const val CORE_R = 0.16f         // long-press area around the hole
        private const val ORBIT_SPEED = 0.045  // rad/s: one lap in about two and a half minutes
        const val MAX_STARS = 7
        const val MAX_ROBOTS = 5
    }
}
