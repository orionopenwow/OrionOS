package com.orion.mobile

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Drawable
import android.net.Uri
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.GridView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.TextView
import android.widget.Toast

/** One launchable app. The icon is loaded lazily (the first time it's shown). */
class AppEntry(val label: String, val component: ComponentName) {
    var icon: Drawable? = null
    val key: String get() = component.flattenToString()
}

/**
 * The installed apps that have a launcher icon (needs the MAIN/LAUNCHER <queries> entry in the manifest on
 * Android 11+), plus the taskbar pins for desktop mode.
 */
object Apps {
    private const val PREFS = "orion_launcher"

    fun load(context: Context): List<AppEntry> {
        val pm = context.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return pm.queryIntentActivities(intent, 0)
            .map { AppEntry(it.loadLabel(pm).toString(), ComponentName(it.activityInfo.packageName, it.activityInfo.name)) }
            .sortedBy { it.label.lowercase() }
    }

    fun icon(context: Context, e: AppEntry): Drawable? {
        e.icon?.let { return it }
        return try { context.packageManager.getActivityIcon(e.component).also { e.icon = it } } catch (x: Exception) { null }
    }

    fun launch(activity: Activity, e: AppEntry) {
        try {
            activity.startActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                .setComponent(e.component).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED))
        } catch (x: Exception) {
            Toast.makeText(activity, activity.getString(R.string.launcher_cant_open), Toast.LENGTH_SHORT).show()
        }
    }

    fun appInfo(activity: Activity, e: AppEntry) {
        activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(Uri.parse("package:" + e.component.packageName)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun pinned(context: Context): List<String> =
        (context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("pinned", "") ?: "")
            .split('\n').filter { it.isNotBlank() }

    fun togglePin(context: Context, e: AppEntry): Boolean {
        val now = pinned(context).toMutableList()
        val on = if (now.remove(e.key)) false else { now.add(e.key); true }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("pinned", now.joinToString("\n")).apply()
        return on
    }
}

/**
 * The frosted-glass app list that swipe-up opens: a search box over a grid of apps. Tap opens an app; long-press
 * shows App info and Pin to taskbar.
 */
class AppDrawer(private val activity: Activity, private val onPinsChanged: () -> Unit) {
    private val ctx: Context = activity
    private var all: List<AppEntry> = emptyList()
    private var shown: List<AppEntry> = emptyList()
    private val grid = GridView(ctx)
    val search = EditText(ctx)

    private val adapter = object : BaseAdapter() {
        override fun getCount() = shown.size
        override fun getItem(p: Int) = shown[p]
        override fun getItemId(p: Int) = p.toLong()
        override fun getView(p: Int, convert: View?, parent: ViewGroup?): View {
            val cell = (convert as? LinearLayout) ?: LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
                setPadding(0, Glass.dp(ctx, 8), 0, Glass.dp(ctx, 8))
                addView(ImageView(ctx), LinearLayout.LayoutParams(Glass.dp(ctx, 48), Glass.dp(ctx, 48)))
                addView(Glass.text(ctx, "", 12f).apply {
                    gravity = Gravity.CENTER; maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END
                    setPadding(Glass.dp(ctx, 2), Glass.dp(ctx, 6), Glass.dp(ctx, 2), 0)
                }, LinearLayout.LayoutParams(-1, -2))
            }
            val e = shown[p]
            (cell.getChildAt(0) as ImageView).setImageDrawable(Apps.icon(ctx, e))
            (cell.getChildAt(1) as TextView).text = e.label
            return cell
        }
    }

    /** Builds the panel's content (the caller puts it inside a glass sheet). */
    fun build(): View {
        val col = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        search.apply {
            hint = ctx.getString(R.string.launcher_search_apps)
            setTextColor(Glass.INK); setHintTextColor(Glass.QUIET); typeface = Glass.body(ctx)
            isSingleLine = true
            background = Glass.panel(ctx, 999, android.graphics.Color.parseColor("#26FFFFFF"))
            setPadding(Glass.dp(ctx, 16), Glass.dp(ctx, 10), Glass.dp(ctx, 16), Glass.dp(ctx, 10))
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun afterTextChanged(s: Editable?) = filter(s?.toString() ?: "")
            })
        }
        col.addView(search, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = Glass.dp(ctx, 10) })
        grid.apply {
            numColumns = GridView.AUTO_FIT
            columnWidth = Glass.dp(ctx, 82)
            stretchMode = GridView.STRETCH_COLUMN_WIDTH
            verticalSpacing = Glass.dp(ctx, 4)
            selector = android.graphics.drawable.ColorDrawable(android.graphics.Color.TRANSPARENT)
            adapter = this@AppDrawer.adapter
            setOnItemClickListener { _, _, p, _ -> Apps.launch(activity, shown[p]) }
            setOnItemLongClickListener { _, v, p, _ -> menu(v, shown[p]); true }
        }
        col.addView(grid, LinearLayout.LayoutParams(-1, 0, 1f))
        refresh()
        return col
    }

    fun refresh() {
        Thread {
            val list = try { Apps.load(ctx) } catch (e: Exception) { emptyList() }
            activity.runOnUiThread { all = list; filter(search.text?.toString() ?: "") }
        }.start()
    }

    /** True when the list is scrolled to the very top (so a downward swipe should close the drawer). */
    fun atTop(): Boolean = grid.childCount == 0 || (grid.firstVisiblePosition == 0 && grid.getChildAt(0).top >= grid.paddingTop)

    private fun filter(q: String) {
        val s = q.trim().lowercase()
        shown = if (s.isEmpty()) all else all.filter { it.label.lowercase().contains(s) }
        adapter.notifyDataSetChanged()
    }

    private fun menu(anchor: View, e: AppEntry) {
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            background = Glass.panel(ctx, 16, android.graphics.Color.parseColor("#F00B0F1E"))
            setPadding(Glass.dp(ctx, 8), Glass.dp(ctx, 8), Glass.dp(ctx, 8), Glass.dp(ctx, 8))
        }
        val pop = PopupWindow(box, ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, true)
        val pinnedNow = Apps.pinned(ctx).contains(e.key)
        box.addView(Glass.pill(ctx, ctx.getString(R.string.launcher_app_info)) { pop.dismiss(); Apps.appInfo(activity, e) })
        box.addView(Glass.pill(ctx, ctx.getString(if (pinnedNow) R.string.launcher_unpin else R.string.launcher_pin)) {
            pop.dismiss(); Apps.togglePin(ctx, e); onPinsChanged()
        })
        pop.elevation = Glass.dp(ctx, 8).toFloat()
        pop.showAsDropDown(anchor, 0, -anchor.height / 2)
    }
}
