package com.orion.mobile

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle

/*
 * The one menu (at most six items): Talk, Approvals, Projects, Inbox, Test, Settings.
 * The home screen stays the black hole; the menu opens from the Menu chip or a long-press on empty sky
 * (HomeActivity), and from the Menu pill in the chat screen (MainActivity, through MenuActivity).
 */
object OrionMenu {
    const val EXTRA_PANE = "orion_pane"          // MainActivity: "settings" opens the alerts and voice pane

    data class Item(val label: String, val sub: String, val go: (Activity) -> Unit)

    fun items(): List<Item> = listOf(
        Item("Talk", "Chat or hold the mic") { a -> chat(a) },
        Item("Approvals", "Things waiting for your fingerprint") { a ->
            a.startActivity(Intent(a, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra(Alerts.EXTRA_KIND, "approval"))
        },
        Item("Projects", "Send work to your PC and follow it") { a -> MissionsActivity.open(a) },
        Item("Inbox", "Messages from your apps, kept on this phone") { a -> a.startActivity(Intent(a, InboxActivity::class.java)) },
        Item("Test", "Check your phone and PC are connected") { a -> a.startActivity(Intent(a, PhoneTestActivity::class.java)) },
        Item("Settings", "Pair with my PC, phone setup, alerts") { a -> openMenu(a, settings = true) },
    )

    fun settingsItems(): List<Item> = listOf(
        Item("Pair with my PC", "Find your PC on this Wi-Fi and type its code") { a -> PairPcActivity.open(a) },
        Item("Phone setup", "Home screen, assistant, inbox, wallpaper, Pocket Orion") { a ->
            a.startActivity(Intent(a, PhoneSetupActivity::class.java))
        },
        Item("Alerts and voice", "How loudly Orion tells you things") { a ->
            a.startActivity(Intent(a, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra(EXTRA_PANE, "settings"))
        },
    )

    fun chat(a: Activity) = a.startActivity(Intent(a, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

    fun openMenu(c: Context, settings: Boolean = false) {
        c.startActivity(Intent(c, MenuActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(MenuActivity.EXTRA_SETTINGS, settings))
    }
}

/** The menu as its own screen (used from the chat screen, and for Settings). */
class MenuActivity : Activity() {
    override fun onResume() {
        super.onResume()
        val settings = intent.getBooleanExtra(EXTRA_SETTINGS, false)
        val col = SysUi.page(this, if (settings) "Settings" else "Orion", "")
        val paired = Lab(this).paired
        if (!paired && !settings) col.addView(SysUi.button(this, "Pair with my PC", primary = true) { PairPcActivity.open(this) })
        for (item in if (settings) OrionMenu.settingsItems() else OrionMenu.items()) {
            val card = SysUi.card(this)
            card.addView(SysUi.title(this, item.label))
            card.addView(SysUi.note(this, item.sub))
            card.setOnClickListener { item.go(this); if (item.label != "Settings") finish() }
            col.addView(card)
        }
    }

    companion object { const val EXTRA_SETTINGS = "settings" }
}
