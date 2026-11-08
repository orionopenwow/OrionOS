package com.orion.mobile

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * Small view helpers for the phone-system screens (assistant sheet, inbox, phone setup, send to PC),
 * in Orion's glass colours. Plain Android views, no libraries; same palette as MainActivity.
 */
object SysUi {
    val void = Color.parseColor("#05070E")
    val panel = Color.parseColor("#B30B0F1E")      // frosted glass: the panel colour, partly see-through
    val edge = Color.parseColor("#262D50")
    val violet = Color.parseColor("#7C5CFF")
    val cyan = Color.parseColor("#00E5FF")
    val ink = Color.parseColor("#D8DEFF")
    val quiet = Color.parseColor("#8888AA")
    val red = Color.parseColor("#FF5C8A")
    val amber = Color.parseColor("#FFB300")
    val green = Color.parseColor("#4ADE80")

    fun dp(c: Context, v: Int) = (v * c.resources.displayMetrics.density).toInt()

    fun rounded(c: Context, fill: Int, stroke: Int, radiusDp: Int) = GradientDrawable().apply {
        setColor(fill); cornerRadius = dp(c, radiusDp).toFloat(); setStroke(dp(c, 1), stroke)
    }

    /** A full-screen scrolling page with a title and a short line under it. Returns the column to fill. */
    fun page(a: Activity, title: String, sub: String): LinearLayout {
        val col = LinearLayout(a).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(a, 18), dp(a, 36), dp(a, 18), dp(a, 24))
        }
        col.addView(TextView(a).apply {
            text = title; setTextColor(Color.WHITE); typeface = Typeface.DEFAULT_BOLD
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
        })
        if (sub.isNotEmpty()) col.addView(TextView(a).apply {
            text = sub; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setPadding(0, dp(a, 6), 0, dp(a, 14))
        })
        val root = FrameLayout(a).apply { setBackgroundColor(void) }
        root.addView(ScrollView(a).apply { isFillViewport = true; addView(col) })
        a.setContentView(root)
        return col
    }

    fun card(c: Context) = LinearLayout(c).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(c, 16), dp(c, 14), dp(c, 16), dp(c, 14))
        background = rounded(c, panel, edge, 18)
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(c, 6), 0, dp(c, 8)) }
    }

    fun title(c: Context, s: String, color: Int = Color.WHITE) = TextView(c).apply {
        text = s; setTextColor(color); typeface = Typeface.DEFAULT_BOLD; setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
    }

    fun text(c: Context, s: String, sizeSp: Float = 15f, color: Int = ink) = TextView(c).apply {
        text = s; setTextColor(color); setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp); setPadding(0, dp(c, 3), 0, dp(c, 3))
    }

    fun note(c: Context, s: String) = TextView(c).apply {
        text = s; setTextColor(quiet); setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f); setPadding(0, dp(c, 6), 0, dp(c, 6))
    }

    fun section(c: Context, s: String) = TextView(c).apply {
        text = s.uppercase(); setTextColor(quiet); typeface = Typeface.DEFAULT_BOLD
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f); letterSpacing = 0.08f
        setPadding(dp(c, 2), dp(c, 16), 0, dp(c, 4))
    }

    fun input(c: Context, hint: String, value: String, multiLine: Boolean = false) = EditText(c).apply {
        this.hint = hint; setText(value); setTextColor(ink); setHintTextColor(quiet)
        background = rounded(c, Color.parseColor("#070A14"), edge, 14)
        setPadding(dp(c, 14), dp(c, 12), dp(c, 14), dp(c, 12))
        if (multiLine) {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 3; maxLines = 8; gravity = Gravity.TOP or Gravity.START
        } else isSingleLine = true
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(c, 4), 0, dp(c, 8)) }
    }

    fun button(c: Context, label: String, primary: Boolean, onClick: () -> Unit) = Button(c).apply {
        text = label; isAllCaps = false; setTextColor(Color.WHITE); setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        background = if (primary) GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
            intArrayOf(violet, Color.parseColor("#00B8D9"))).apply { cornerRadius = dp(c, 999).toFloat() }
        else rounded(c, Color.parseColor("#151A30"), edge, 999)
        stateListAnimator = null
        setPadding(dp(c, 16), dp(c, 10), dp(c, 16), dp(c, 10))
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(c, 5), 0, dp(c, 5)) }
        setOnClickListener { onClick() }
    }

    fun pill(c: Context, label: String, color: Int = ink, onClick: () -> Unit) = Button(c).apply {
        text = label; isAllCaps = false; setTextColor(color); setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        background = rounded(c, Color.parseColor("#151A30"), edge, 999)
        stateListAnimator = null; minHeight = 0; minimumHeight = 0; minWidth = 0; minimumWidth = 0
        setPadding(dp(c, 12), dp(c, 6), dp(c, 12), dp(c, 6))
        layoutParams = LinearLayout.LayoutParams(-2, -2).apply { setMargins(dp(c, 3), dp(c, 4), dp(c, 3), dp(c, 4)) }
        setOnClickListener { onClick() }
    }

    fun row(c: Context) = LinearLayout(c).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }

    fun weighted(v: View, weight: Float = 1f): View {
        v.layoutParams = LinearLayout.LayoutParams(0, -2, weight)
        return v
    }

    /** "5 min ago", "yesterday 14:02" ... */
    fun ago(ts: Long): String {
        val d = (System.currentTimeMillis() - ts) / 1000
        return when {
            d < 60 -> "just now"
            d < 3600 -> "${d / 60} min ago"
            d < 86400 -> "${d / 3600} h ago"
            else -> "${d / 86400} d ago"
        }
    }
}
