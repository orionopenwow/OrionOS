package com.orion.mobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Orion's glass look on the phone, matching the desktop: translucent dark panels with a thin light edge, the three
 * belt dots (rose, ember, aurora), Unbounded for the clock and headings, Figtree for reading, Martian Mono for
 * numbers. Real blur (RenderEffect) only on Android 12+; older phones get a deeper translucent tint instead.
 */
object Glass {
    val VOID = Color.parseColor("#05070E")
    val INK = Color.parseColor("#D8DEFF")
    val QUIET = Color.parseColor("#9AA0C8")
    val ROSE = Color.parseColor("#FF5C8A")
    val EMBER = Color.parseColor("#FFC66B")
    val AURORA = Color.parseColor("#5CFFB0")
    val PHOTON = Color.parseColor("#00E5FF")
    val VIOLET = Color.parseColor("#7C5CFF")
    private val EDGE = Color.parseColor("#3DFFFFFF")
    /** Panel fill: lighter when there's a real blur behind it, darker when there isn't. */
    val FILL get() = if (blurAvailable) Color.parseColor("#8C0B0F1E") else Color.parseColor("#D90B0F1E")

    val blurAvailable: Boolean get() = Build.VERSION.SDK_INT >= 31

    private var unbounded: Typeface? = null
    private var figtree: Typeface? = null
    private var figtreeBold: Typeface? = null
    private var mono: Typeface? = null

    fun heading(c: Context): Typeface = unbounded ?: load(c, R.font.unbounded_semibold, Typeface.DEFAULT_BOLD).also { unbounded = it }
    fun body(c: Context): Typeface = figtree ?: load(c, R.font.figtree_regular, Typeface.DEFAULT).also { figtree = it }
    fun bodyBold(c: Context): Typeface = figtreeBold ?: load(c, R.font.figtree_semibold, Typeface.DEFAULT_BOLD).also { figtreeBold = it }
    fun mono(c: Context): Typeface = mono ?: load(c, R.font.martianmono_regular, Typeface.MONOSPACE).also { mono = it }

    private fun load(c: Context, id: Int, fallback: Typeface): Typeface =
        try { c.resources.getFont(id) } catch (e: Exception) { fallback }

    fun dp(c: Context, v: Int): Int = (v * c.resources.displayMetrics.density).toInt()

    /** A glass panel background. */
    fun panel(c: Context, radiusDp: Int = 22, fill: Int = FILL): GradientDrawable = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(c, radiusDp).toFloat()
        setStroke(maxOf(1, dp(c, 1) / 2 + 1), EDGE)
    }

    /** The three belt dots, as on the desktop's glass windows. */
    fun beltDots(c: Context): LinearLayout = LinearLayout(c).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        for (col in intArrayOf(ROSE, EMBER, AURORA)) {
            addView(View(c).apply {
                background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(col) }
            }, LinearLayout.LayoutParams(dp(c, 8), dp(c, 8)).apply { marginEnd = dp(c, 6) })
        }
    }

    fun text(c: Context, s: CharSequence, sizeSp: Float = 15f, color: Int = INK, face: Typeface? = null) = TextView(c).apply {
        text = s
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
        typeface = face ?: body(c)
    }

    fun pill(c: Context, label: String, accent: Int = INK, onClick: () -> Unit) = Button(c).apply {
        text = label; isAllCaps = false; setTextColor(accent)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
        typeface = bodyBold(c)
        background = panel(c, 999, Color.parseColor("#33FFFFFF"))
        stateListAnimator = null; minHeight = 0; minimumHeight = 0; minWidth = 0; minimumWidth = 0
        setPadding(dp(c, 14), dp(c, 8), dp(c, 14), dp(c, 8))
        setOnClickListener { onClick() }
    }

    /**
     * Turns a snapshot of the black hole into a frosted backdrop. Android 12+: real blur via RenderEffect (looked
     * up by reflection so this still compiles against older SDKs). Older: no blur, the panel's tint does the work.
     */
    fun frost(view: View, radiusPx: Float = 60f): Boolean {
        if (Build.VERSION.SDK_INT < 31) return false
        return try {
            val re = Class.forName("android.graphics.RenderEffect")
            val effect = re.getMethod("createBlurEffect", Float::class.javaPrimitiveType, Float::class.javaPrimitiveType,
                Shader.TileMode::class.java).invoke(null, radiusPx, radiusPx, Shader.TileMode.CLAMP)
            View::class.java.getMethod("setRenderEffect", re).invoke(view, effect)
            true
        } catch (e: Throwable) {
            false
        }
    }

    /** Cheap pre-blur for the snapshot: shrink hard, let the ImageView scale it back up smoothly. */
    fun shrink(b: Bitmap): Bitmap {
        val w = maxOf(8, b.width / 6); val h = maxOf(8, b.height / 6)
        return Bitmap.createScaledBitmap(b, w, h, true)
    }
}
