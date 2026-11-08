package com.orion.mobile

import android.app.Activity
import android.os.Build
import android.text.InputType
import android.view.View
import android.widget.LinearLayout

/**
 * Phone setup's Pocket Orion card: is it running, pair with it, turn the fallback on or off, forget it.
 * Setup itself happens in Android's Terminal app (os/phone/orion-pocket-setup.sh); see docs/ORION_PHONE_GUIDE.md.
 */
object PocketCard {
    fun build(a: Activity): View {
        val c = SysUi.card(a)
        val head = SysUi.row(a)
        head.addView(SysUi.weighted(SysUi.title(a, "Pocket Orion")))
        val state = SysUi.text(a, "Checking...", 14f, SysUi.quiet)
        head.addView(state)
        c.addView(head)
        c.addView(SysUi.note(a, "A small Orion inside this phone's Linux terminal (Android 16 and newer). When your PC is " +
            "off, chat, voice and Scout use it instead, and Orion says \"${PocketFallback.NOTICE}\". " +
            "Approvals, agents and missions always wait for your PC."))
        val result = SysUi.note(a, "").apply { visibility = View.GONE }
        val body = LinearLayout(a).apply { orientation = LinearLayout.VERTICAL }
        c.addView(body)
        c.addView(result)

        fun say(t: String, color: Int) { result.visibility = View.VISIBLE; result.setTextColor(color); result.text = t }

        fun fill(up: Boolean) {
            body.removeAllViews()
            val paired = PocketFallback.paired(a)
            val on = PocketFallback.enabled(a)
            state.text = when {
                !up -> "Not running"
                !paired -> "Running, not paired"
                on -> "On"
                else -> "Off"
            }
            state.setTextColor(if (up && paired && on) SysUi.green else SysUi.quiet)
            if (paired) {
                body.addView(SysUi.button(a, if (on) "Turn off" else "Turn on", primary = !on) {
                    PocketFallback.setEnabled(a, !on); fill(up)
                })
                body.addView(SysUi.button(a, "Forget Pocket Orion", primary = false) {
                    PocketFallback.forget(a); fill(up); say("Forgotten. Chat goes only to your PC now.", SysUi.quiet)
                })
                return
            }
            if (!up) {
                body.addView(SysUi.text(a, "1. Open the Terminal app and run orion-pocket-setup.sh (from the Orion folder).\n" +
                    "2. Allow it to open port 9121 when Android asks.\n3. Come back here and pair.", 14f))
                body.addView(SysUi.button(a, "Check again", primary = false) { check(a, ::fill) })
                return
            }
            val code = SysUi.input(a, "Code from: orion-pocket pair", "").apply {
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
            }
            body.addView(code)
            body.addView(SysUi.button(a, "Pair with Pocket Orion", primary = true) {
                val cd = code.text.toString().trim()
                if (cd.isEmpty()) { say("Type the code orion-pocket pair printed.", SysUi.amber); return@button }
                say("Pairing...", SysUi.quiet)
                Thread {
                    try {
                        val fp = PocketFallback.pair(a, cd, Build.MODEL ?: "Android phone")
                        a.runOnUiThread {
                            fill(true)
                            say("Now run this in the Terminal app, after checking it matches:\norion-pocket confirm $fp", SysUi.green)
                        }
                    } catch (e: Exception) {
                        a.runOnUiThread { say(e.message ?: e.toString(), SysUi.red) }
                    }
                }.start()
            })
        }

        check(a, ::fill)
        return c
    }

    private fun check(a: Activity, then: (Boolean) -> Unit) {
        Thread {
            val up = PocketFallback.pocketUp()
            a.runOnUiThread { then(up) }
        }.start()
    }
}
