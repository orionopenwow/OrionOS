"""Update channels with a test run first (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 3).

Channels: Stable, Beta and "My builds", one word in /etc/orion/channel (stable | beta | mine). orion-slot and
orion-update read it; they keep their signed-release checks. Changing it needs the orion-admin password
(Settings > Updates, or `sudo orion-self-helper channel beta`).

Before a new system version is offered:
  1. Orion asks the channel what's newest (`orion-slot latest --json` on the USB stick, `orion-update check
     --json` on an installed system).
  2. The self-test (os/lab/bin/orion-selftest) runs. When this computer can run a VM (QEMU with KVM, enough free
     memory and disk) and "test new versions in a VM first" is on, the new version's image is fetched and checked
     against the release key (`orion-slot fetch-image`), booted in QEMU with the existing boot test
     (os/image/test-boot.sh), Orion waits until the Orion screen is up, and a screenshot is checked. Otherwise only
     the parts that don't need a VM run (on this computer), and Orion says plainly that the VM run was skipped.
  3. Only a passing version is offered: a card (new system versions are always "ask"). Approving opens a terminal
     that installs it into the spare system copy (`orion-slot update --expect <version>`, automatic rollback if it
     doesn't start) and asks for the orion-admin password. The black hole's second ring (update_ready) shows
     while one waits.

HTTP (local):  GET /self/channel   POST /self/updates/check (runs steps 1-3 now)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Optional

import self_cards
import self_host
import self_levels

log = logging.getLogger("orion.update_channels")

CHANNELS = {"stable": "Stable", "beta": "Beta", "mine": "My builds"}
SELFTEST_TIMEOUT_S = 3 * 3600


def channel_path() -> Path:
    return Path(os.environ.get("ORION_CHANNEL_FILE", "/etc/orion/channel"))


def read_channel() -> str:
    try:
        c = channel_path().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "stable"
    return c if c in CHANNELS else "stable"


def channel_command(channel: str) -> str:
    """What Settings runs in a terminal to switch (asks for the orion-admin password)."""
    if channel not in CHANNELS:
        raise ValueError("The channel is stable, beta or mine.")
    return f"su -P - orion-admin -c 'sudo /opt/orion/safety/orion-self-helper channel {channel}'"


def _tool(h: Any, name: str) -> str:
    for p in (f"/usr/local/bin/{name}", f"/opt/orion/bin/{name}"):
        if h.exists(p):
            return p
    return h.which(name) or name


def _json_tail(out: str) -> Optional[dict[str, Any]]:
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                return d if isinstance(d, dict) else None
            except ValueError:
                continue
    return None


def on_slot(h: Any) -> bool:
    try:
        return "orion.slot" in Path(os.environ.get("ORION_CMDLINE", "/proc/cmdline")).read_text()
    except OSError:
        return False


async def latest(h: Any) -> dict[str, Any]:
    """{"current", "latest", "channel", "slot", "newer"}. {"error"} when the channel can't be reached."""
    slot = on_slot(h)
    argv = [_tool(h, "orion-slot"), "latest", "--json"] if slot else [_tool(h, "orion-update"), "check", "--json"]
    rc, out = await asyncio.to_thread(h.run, argv, 120)
    d = _json_tail(out)
    if d is None or rc not in (0, 1):
        return {"error": "Couldn't reach the update channel.", "slot": slot, "channel": read_channel()}
    cur, new = str(d.get("current") or ""), str(d.get("latest") or "")
    return {"current": cur, "latest": new, "channel": d.get("channel") or read_channel(), "slot": slot,
            "newer": bool(new) and new != cur}


async def can_vm(h: Any) -> dict[str, Any]:
    rc, out = await asyncio.to_thread(h.run, [_tool(h, "orion-selftest"), "can-vm", "--json"], 60)
    return _json_tail(out) or {"ok": False, "why": "the self-test isn't installed"}


async def selftest(h: Any, version: str, slot: bool, vm_wanted: bool) -> dict[str, Any]:
    """Run the self-test for `version`: in a VM when possible, otherwise the parts that don't need one."""
    vm_why = ""
    image = ""
    if not vm_wanted:
        vm_why = "you turned off testing new versions in a VM"
    elif not slot:
        vm_why = "this computer updates from source, so there's no system image to boot"
    else:
        cap = await can_vm(h)
        if not cap.get("ok"):
            vm_why = str(cap.get("why") or "this computer can't run a VM")
        else:
            from config import settings
            work = Path(settings.data_dir) / "selftest"
            rc, out = await asyncio.to_thread(h.run, [_tool(h, "orion-slot"), "fetch-image", str(work),
                                                      "--expect", version], 4 * 3600)
            got = _json_tail(out) or {}
            if rc != 0 or not got.get("image"):
                vm_why = "the new version's image couldn't be downloaded or checked (" + \
                         str(got.get("message") or out.strip()[-120:] or "no answer") + ")"
            else:
                image = str(got["image"])
    argv = [_tool(h, "orion-selftest"), "run", "--json"]
    argv += ["--vm-image", image] if image else ["--no-vm", vm_why]
    rc, out = await asyncio.to_thread(h.run, argv, SELFTEST_TIMEOUT_S)
    res = _json_tail(out) or {"ok": False, "parts": [], "message": "The self-test gave no answer."}
    res["vm_ran"] = bool(image) and bool((res.get("vm") or {}).get("ran"))
    res["vm_skipped"] = "" if image else vm_why
    return res


def _signal(on: bool) -> None:
    try:
        import core_signals
        core_signals.set_external_signal("update_ready", 1.0 if on else 0.0)
    except Exception:
        pass


def _words(res: dict[str, Any]) -> str:
    lines = []
    for p in res.get("parts") or []:
        mark = "passed" if p.get("ok") else ("skipped" if p.get("skipped") else "FAILED")
        lines.append(f"- {p.get('name')}: {mark}" + (f" ({p.get('message')})" if p.get("message") else ""))
    if res.get("vm_skipped"):
        lines.append(f"The run in a VM was skipped: {res['vm_skipped']}. Only this computer's own checks ran; the "
                     "new version itself wasn't tried before this offer.")
    return "\n".join(lines)


async def check_and_offer(daemon: Any, h: Any = None) -> dict[str, Any]:
    import freeze
    import self_updates
    if freeze.is_frozen():
        return {"skipped": "frozen"}
    h = h or self_host.host()
    info = await latest(h)
    if info.get("error"):
        return info
    st = self_updates.load_state()
    if not info["newer"]:
        _signal(False)
        self_updates.update_state(lambda s: s.pop("offer", None))
        return {**info, "offered": False}
    offer = st.get("offer") or {}
    if offer.get("version") == info["latest"] and offer.get("passed") is not None:
        _signal(bool(offer.get("passed")))
        if offer.get("passed") and not offer.get("carded"):
            await _offer_card(daemon, info, offer)
        return {**info, "offered": bool(offer.get("passed")), "already_tested": True}
    res = await selftest(h, info["latest"], info["slot"], bool(st.get("vm_test", True)))
    passed = bool(res.get("ok"))
    offer = {"version": info["latest"], "current": info["current"], "channel": info["channel"], "passed": passed,
             "vm_ran": res.get("vm_ran"), "vm_skipped": res.get("vm_skipped"), "words": _words(res),
             "at": h.now()}
    self_updates.update_state(lambda s: s.__setitem__("offer", offer))
    try:
        import changes
        changes.record_change(f"Tested OrionOS {info['latest']} ({CHANNELS.get(info['channel'], info['channel'])})"
                              + (": passed" if passed else ": held back"),
                              why="a new system version is out", kind="system_test", source="update channels",
                              approved_by="automatic (only tested)",
                              tests=[{"name": p.get("name"), "passed": bool(p.get("ok")), "skipped": bool(p.get("skipped"))}
                                     for p in res.get("parts") or []],
                              result="passed" if passed else "failed", details={"vm_skipped": res.get("vm_skipped")})
    except Exception:
        pass
    _signal(passed)
    if not passed:
        try:
            import alerts
            alerts.emit("system", f"OrionOS {info['latest']} held back",
                        "It didn't pass Orion's self-test, so it isn't offered. Orion tries again with the next one.",
                        ref="system-update")
        except Exception:
            pass
        return {**info, "offered": False, "selftest": res}
    await _offer_card(daemon, info, offer)
    return {**info, "offered": True, "selftest": res}


async def _offer_card(daemon: Any, info: dict[str, Any], offer: dict[str, Any]) -> None:
    if self_levels.decide("system_version") != "ask":     # can't happen (max level 1), but stay safe
        log.warning("system_version isn't 'ask'; offering a card anyway")
    v = offer["version"]
    body = (f"OrionOS {v} is out on the {CHANNELS.get(offer['channel'], offer['channel'])} channel (you have "
            f"{offer.get('current') or 'an older one'}). It passed Orion's self-test:\n{offer.get('words', '')}\n\n"
            "Approve to install it into the spare system copy (a terminal asks for the orion-admin password). If "
            "it doesn't start, the computer comes back to this version by itself.")
    card = await self_cards.propose(daemon, "system_version", f"Install OrionOS {v}?", body, "system_install",
                                    {"version": v, "slot": bool(info.get("slot"))}, key=f"system:{v}",
                                    proposer="Updates")
    if card is not None:     # one card per version: a "no" isn't asked again every day
        import self_updates
        self_updates.update_state(lambda s: (s.get("offer") or {}).__setitem__("carded", True))


async def _action_install(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    v = str(args.get("version") or "")
    if not v or any(c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-+_" for c in v):
        return {"ok": False, "message": "That version name doesn't look right."}
    # both check the release signature and install exactly the version Orion tested (orion-update: review B3)
    inner = (f"sudo orion-slot update --expect {shlex.quote(v)}" if args.get("slot")
             else f"sudo orion-update --expect {shlex.quote(v)}")
    cmd = f"su -P - orion-admin -c {shlex.quote(inner)}"
    opened = await asyncio.to_thread(self_host.host().open_terminal, cmd)
    try:
        import changes
        changes.record_change(f"Started installing OrionOS {v}", why="you approved the tested version",
                              kind="system_version", source="update channels", approved_by="you (card)",
                              result="terminal opened" if opened else "waiting for you to run it")
    except Exception:
        pass
    return {"ok": opened, "message": "Installing in a terminal (asks for the orion-admin password). Restart when it "
            "says it's ready." if opened else f"Couldn't open a terminal. Run: {cmd}"}


self_cards.register("system_install", _action_install)


def status() -> dict[str, Any]:
    import self_updates
    st = self_updates.load_state()
    ch = read_channel()
    return {"channel": ch, "channel_name": CHANNELS[ch], "channels": CHANNELS, "vm_test": bool(st.get("vm_test")),
            "offer": st.get("offer"), "commands": {c: channel_command(c) for c in CHANNELS}}
