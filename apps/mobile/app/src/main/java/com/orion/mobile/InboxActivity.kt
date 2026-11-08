package com.orion.mobile

import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast

/**
 * One inbox: your messages from the apps you picked, grouped by person or chat, newest first.
 * Open a conversation, tap "Draft reply" (your PC drafts it in your style), edit it, and tap Send.
 * Orion never sends on its own: Send goes through the messaging app's own reply button, only when you tap it.
 */
class InboxActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private var open: String? = null                       // the conversation that's expanded
    private val drafts = HashMap<String, String>()         // unsent text per conversation, kept while the screen lives
    private var settings = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        open = savedInstanceState?.getString("open") ?: intent.getStringExtra(EXTRA_THREAD)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("open", open)
    }

    override fun onResume() {
        super.onResume()
        InboxStore.onChange = { if (!settings && !typing()) render() }
        render()
    }

    override fun onPause() {
        InboxStore.onChange = null
        super.onPause()
    }

    @Deprecated("Back leaves settings first")
    override fun onBackPressed() {
        if (settings) { settings = false; render() } else super.onBackPressed()
    }

    private var draftBox: EditText? = null
    private fun typing() = draftBox?.hasFocus() == true

    private fun accessOn(): Boolean =
        getSystemService(NotificationManager::class.java).isNotificationListenerAccessGranted(InboxListener.component(this))

    private fun render() { if (settings) renderSettings() else renderList() }

    // ================================================================= the list
    private fun renderList() {
        draftBox = null
        val col = SysUi.page(this, "Inbox", "Kept on this phone only, for 7 days. Replies go out only when you tap Send.")
        val top = SysUi.row(this)
        top.addView(SysUi.pill(this, "Inbox settings") { settings = true; render() })
        top.addView(SysUi.pill(this, "Phone setup") { startActivity(Intent(this, PhoneSetupActivity::class.java)) })
        col.addView(top)

        if (!accessOn()) {
            val c = SysUi.card(this)
            c.addView(SysUi.title(this, "Turn on notification access", SysUi.amber))
            c.addView(SysUi.text(this, "Orion reads your message notifications to fill this inbox. On the next screen, " +
                "find Orion and turn it on. Messages stay on this phone."))
            c.addView(SysUi.button(this, "Open notification access", primary = true) { openAccess() })
            col.addView(c)
        }

        val items = InboxStore.all(this)
        if (items.isEmpty()) { col.addView(SysUi.note(this, "No messages yet. New ones appear here as they arrive.")); return }
        val threads = items.groupBy { it.thread }.entries.sortedByDescending { e -> e.value.maxOf { it.ts } }
        for ((thread, msgs) in threads) col.addView(threadCard(thread, msgs.sortedBy { it.ts }))
    }

    private fun threadCard(thread: String, msgs: List<InboxItem>): View {
        val c = SysUi.card(this)
        val last = msgs.last()
        val head = SysUi.row(this)
        head.addView(SysUi.weighted(SysUi.title(this, last.title.ifEmpty { last.sender })))
        head.addView(SysUi.note(this, "${last.app} · ${SysUi.ago(last.ts)}"))
        c.addView(head)
        val expanded = open == thread
        c.setOnClickListener { open = if (expanded) null else thread; render() }
        if (!expanded) {
            c.addView(SysUi.text(this, (if (last.mine) "You: " else if (last.sender != last.title) "${last.sender}: " else "") +
                last.text.take(140), 14f, SysUi.ink).apply { maxLines = 2 })
            return c
        }
        msgs.takeLast(12).forEach { m ->
            c.addView(SysUi.text(this, (if (m.mine) "You" else m.sender) + "  ·  " + SysUi.ago(m.ts), 12f, SysUi.quiet))
            c.addView(SysUi.text(this, m.text, 15f, if (m.mine) SysUi.cyan else SysUi.ink))
        }

        val status = SysUi.note(this, "")
        val box = SysUi.input(this, "Your reply", drafts[thread] ?: "", multiLine = true)
        box.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, d: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, d: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) { drafts[thread] = s?.toString() ?: "" }
        })
        draftBox = box
        c.addView(box)
        val row = SysUi.row(this)
        row.addView(SysUi.weighted(SysUi.button(this, "Draft reply", primary = false) { draft(thread, msgs, box, status) }))
        val canSend = InboxListener.canReply(thread)
        row.addView(SysUi.weighted(SysUi.button(this, "Send", primary = canSend) { send(thread, last, box, status) }.apply {
            alpha = if (canSend) 1f else 0.5f
        }))
        for (i in 0 until row.childCount) (row.getChildAt(i).layoutParams as LinearLayout.LayoutParams).setMargins(SysUi.dp(this, 3), 0, SysUi.dp(this, 3), 0)
        c.addView(row)
        if (!canSend) c.addView(SysUi.note(this, "Send works while this message's notification is still on your phone. " +
            "It's gone now, so copy the text and reply in ${last.app}."))
        c.addView(status)
        val more = SysUi.row(this)
        more.addView(SysUi.pill(this, "Open ${last.app}") { if (!InboxListener.open(this, thread, last.pkg)) toast("Can't open ${last.app}.") })
        more.addView(SysUi.pill(this, "Clear this chat") { InboxStore.clearThread(this, thread); open = null; render() })
        c.addView(more)
        return c
    }

    private fun draft(thread: String, msgs: List<InboxItem>, box: EditText, status: android.widget.TextView) {
        val incoming = msgs.filter { !it.mine }.takeLast(5)
        if (incoming.isEmpty()) { status.text = "Nothing to answer yet."; return }
        val last = incoming.last()
        status.setTextColor(SysUi.quiet); status.text = "Drafting on your PC..."
        val text = incoming.joinToString("\n") { if (it.sender != last.title) "${it.sender}: ${it.text}" else it.text }
        Thread {
            try {
                val (d, pocket) = PocketFallback.draft(this, last.app, last.sender, text, thread)   // PC off: Orion on this phone
                ui.post {
                    drafts[thread] = d
                    box.setText(d); box.setSelection(box.text.length)
                    status.text = (if (pocket) PocketFallback.NOTICE + ". " else "") +
                        if (d.isEmpty()) "No draft came back." else "Draft ready. Edit it, then tap Send."
                }
            } catch (e: Exception) {
                ui.post { status.setTextColor(SysUi.red); status.text = e.message ?: e.toString() }
            }
        }.start()
    }

    /** The only place a message is ever sent: your tap on Send. */
    private fun send(thread: String, last: InboxItem, box: EditText, status: android.widget.TextView) {
        val text = box.text.toString().trim()
        if (text.isEmpty()) { status.text = "Write a reply first."; return }
        if (!InboxListener.canReply(thread)) { status.setTextColor(SysUi.amber); status.text = "Reply in ${last.app} instead."; return }
        if (InboxListener.sendReply(this, thread, text)) {
            InboxStore.add(this, listOf(InboxItem(InboxStore.id(thread, "You", text, System.currentTimeMillis().toString()),
                last.pkg, last.app, "You", text, System.currentTimeMillis(), thread, last.title, true)))
            drafts.remove(thread)
            toast("Sent through ${last.app}.")
            box.setText(""); box.clearFocus(); render()
        } else {
            status.setTextColor(SysUi.red); status.text = "${last.app} didn't take the reply. Open it and reply there."
        }
    }

    // ================================================================= settings
    private fun renderSettings() {
        draftBox = null
        val col = SysUi.page(this, "Inbox settings", "Pick the apps whose messages Orion keeps. Back returns to the inbox.")
        col.addView(SysUi.section(this, "Apps"))
        val on = InboxStore.enabledApps(this)
        for ((key, name) in InboxStore.KNOWN) {
            val r = SysUi.row(this)
            r.addView(SysUi.weighted(SysUi.text(this, name)))
            val enabled = key in on
            r.addView(SysUi.pill(this, if (enabled) "On" else "Off", if (enabled) SysUi.cyan else SysUi.quiet) {
                InboxStore.setAppEnabled(this, key, !enabled); render()
            })
            col.addView(r)
        }
        col.addView(SysUi.note(this, "Other apps: type their package names, separated by commas (for example com.slack)."))
        val extra = SysUi.input(this, "com.example.chat", InboxStore.extraApps(this).joinToString(", "))
        col.addView(extra)
        col.addView(SysUi.button(this, "Save other apps", primary = false) { InboxStore.setExtraApps(this, extra.text.toString()); toast("Saved.") })

        col.addView(SysUi.section(this, "Your PC"))
        val mirror = InboxStore.mirror(this)
        col.addView(SysUi.button(this, if (mirror) "Mirror inbox to my PC: on" else "Mirror inbox to my PC: off", primary = false) {
            InboxStore.setMirror(this, !mirror); render()
        })
        col.addView(SysUi.note(this, "Off by default. When on, each new message is also copied to your own PC so Orion " +
            "there can see it. Nothing is ever sent to anyone."))

        col.addView(SysUi.section(this, "Stored messages"))
        col.addView(SysUi.note(this, "${InboxStore.all(this).size} messages kept on this phone. Older than 7 days are removed automatically."))
        col.addView(SysUi.button(this, "Clear the whole inbox", primary = false) { InboxStore.clear(this); toast("Inbox cleared."); render() })
        col.addView(SysUi.button(this, "Notification access", primary = false) { openAccess() })
    }

    private fun openAccess() {
        try {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, InboxListener.component(this).flattenToString()))
        } catch (e: Exception) {
            startActivity(Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"))
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    companion object { const val EXTRA_THREAD = "orion_inbox_thread" }
}
