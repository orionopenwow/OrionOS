"""One-tap phone pairing from the HUD, on Windows and OrionOS (the HUD's Phone panel, PhonePanel.tsx).

Until now pairing was `orion-ctl pair-phone` in an OrionOS terminal. This adds the same steps as buttons. The
trust rules don't change (phones.py): a one-time code, the phone proves it knows the code, and nothing is trusted
until you compare the fingerprint on both screens and press Confirm.

Local routes (127.0.0.1:9120, need the daemon's per-start key like every HUD route; guests can't use them):

  POST /phones/pair/open        open pairing: {"code","url","qr_svg","expires","address","lan":{...},"pc":{...}}
  GET  /phones/pair/status      {"open","expires","pending":[{"device_id","name","fingerprint","words"}],"phones":[...]}
  POST /phones/{id}/confirm     {"fingerprint"?}: trust the waiting phone (the words must match on both screens)
  POST /phones/pair/close       cancel pairing (forgets a waiting phone)
  GET  /phones                  paired phones: name, last seen, online, battery if the phone told us
  POST /phones/{id}/unpair      forget a phone
  POST /phones/test-card        a test alert on your phone(s): "If you can see this, your PC reaches your phone."
  POST /phones/open-access      Windows: runs Open Phone Access (asks Windows for admin permission once)

Phone route (port 9121, signed with the phone's read key):

  POST /phone/hello   {"battery"?,"charging"?,"network"?,"app"?} -> {"ok":true,"pc":{"id","name"}}
                      The phone says hello when it opens and from its Test screen. The PC's id lets the phone find
                      the same PC again after its address changes (it looks for _orion._tcp on the Wi-Fi).

Finding the PC without typing: while pairing is open or a phone is paired, the PC announces itself on the local
network as an "_orion._tcp" service (mDNS / DNS-SD) when the optional `zeroconf` package is installed. The phone
finds it with Android's built-in NsdManager. Without zeroconf, the QR code and the typed address still work.

Windows: the phone port (9121) is off unless something turns it on. Opening pairing turns it on (and remembers
that, so it comes back after a restart). Windows Firewall blocks it from other devices until the Open Phone Access
step adds a rule for your home network only; the panel says so in plain words and offers that one button.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import qr_svg

log = logging.getLogger("orion.phone_pairing_ui")

ONLINE_S = 120                 # seen in the last 2 minutes = online
HELLO_BODY_MAX = 4096
SERVICE_TYPE = "_orion._tcp.local."
FIREWALL_RULE = "OrionOS phone access (port 9121)"   # same name Open-PhoneAccess.ps1 uses
LAN_CACHE_S = 30.0
REPO = Path(__file__).resolve().parents[2]
OPEN_ACCESS_PS1 = REPO / "os" / "windows" / "Open-PhoneAccess.ps1"
PORT_HELPER = "/opt/orion/privacy/orion-phone-port"
FREEZE_HELPER = "/opt/orion/safety/orion-freeze-helper"

# Fingerprint words: the first five bytes of the fingerprint, one word each. The same list is in
# apps/mobile/.../PairPc.kt (FpWords), so both screens show the same five words. 256 words, sorted.
WORDS = (
    "acorn album alpha amber anchor apple apron arrow aspen atlas attic autumn badge bagel baker bamboo banjo barn "
    "basil basket beach beacon beaver berry bicycle bison blanket blossom boat bottle branch bread brick bridge brook "
    "brush bubble bucket buffalo bundle butter button cabin cactus camel camera candle canoe canyon carpet carrot "
    "castle cedar cello chalk cherry chess circle citrus clock cloud clover cobalt cocoa coffee comet compass copper "
    "coral cotton cradle crane crayon cricket crystal cupcake curtain daisy delta desert diamond dolphin domino donkey "
    "dragon drum eagle easel echo elbow ember engine falcon feather fern fiddle field finch flute forest fossil "
    "fountain fox galaxy garden garlic gecko ginger giraffe glacier globe goose granite grape gravel guitar hammer "
    "harbor harvest hazel helmet heron honey horizon husky igloo indigo island ivory jacket jaguar jasmine jelly "
    "jigsaw jungle kayak kettle kitten koala ladder lagoon lantern lemon lily linen lizard lobster locket lotus magnet "
    "mango maple marble meadow melon meteor mint mirror mitten monkey moose mosaic muffin museum nectar nickel noodle "
    "nutmeg oasis ocean olive onion orange orbit orchid otter owl paddle panda paper parrot peach peanut pebble "
    "pelican pencil pepper piano pigeon pillow pine planet plum pocket pony poppy potato pretzel prism puffin pumpkin "
    "puzzle quartz quill rabbit radar radish raven reef ribbon river robin rocket rose ruby saddle salmon sandal satin "
    "shell silver sketch sled snail sparrow spoon spruce stamp star stone summer sunset swan teapot thunder tiger "
    "toast tomato topaz tulip tundra turtle umbrella valley velvet violet volcano waffle walnut whale willow window "
    "winter yogurt zebra zipper"
).split()
assert len(WORDS) == 256 and len(set(WORDS)) == 256


def fingerprint_words(fp: str) -> str:
    """'3F2A-91C0-...' -> 'cherry ... ...' (five words). Empty for a malformed fingerprint."""
    hx = "".join(ch for ch in (fp or "") if ch.isalnum())
    try:
        return " ".join(WORDS[int(hx[i:i + 2], 16)] for i in range(0, 10, 2))
    except (ValueError, IndexError):
        return ""


# ===================================================================================== network helpers
def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private and not a.is_loopback and not a.is_link_local


def _is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def default_route_ip() -> Optional[str]:
    """This computer's address on the network it reaches the internet through. A UDP "connect" sends nothing."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 9))
        ip = s.getsockname()[0]
        return ip if ip and not ip.startswith("127.") and ip != "0.0.0.0" else None
    except OSError:
        return None
    finally:
        s.close()


def _cmd(run: Callable[..., Any], args: list[str], timeout: float = 6.0) -> str:
    try:
        r = run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _rc(run: Callable[..., Any], args: list[str], timeout: float = 6.0) -> int:
    try:
        return int(run(args, capture_output=True, text=True, timeout=timeout).returncode)
    except Exception:
        return -1


# ===================================================================================== the pairing helper
class PhonePairingUI:
    def __init__(self, daemon: Any, *, platform: str = sys.platform, run: Callable[..., Any] = subprocess.run,
                 popen: Callable[..., Any] = subprocess.Popen, zeroconf_mod: Any = "auto",
                 route_ip: Callable[[], Optional[str]] = default_route_ip, clock: Callable[[], float] = time.time,
                 port: Optional[int] = None) -> None:
        self.d = daemon
        self.board = daemon.board
        self.platform = platform
        self.run = run
        self.popen = popen
        self.route_ip = route_ip
        self.clock = clock
        if port is None:
            try:
                from config import settings
                port = int(settings.boardroom_port)
            except Exception:
                port = 9121
        self.port = port
        if zeroconf_mod == "auto":
            try:
                import zeroconf as zeroconf_mod  # optional: pip install zeroconf
            except Exception:
                zeroconf_mod = None
        self.zc_mod = zeroconf_mod
        self._zc: Any = None
        self._zc_info: Any = None
        self._lan_cache: tuple[float, dict[str, Any]] | None = None
        self.pin_state = ""          # OrionOS: root's copy of the phone's approval key ("", asked, done, skipped)
        self.data_dir: Optional[Path] = None   # where the PC's name is kept (None = settings.data_dir)

    # ------------------------------------------------------------------ small facts
    @property
    def store(self) -> Any:
        return self.board.store

    def pc_id(self) -> str:
        """A random id for this PC, made once. The phone uses it to find this same PC again on the Wi-Fi."""
        v = self.store.get("pc_id")
        if not v:
            v = secrets.token_hex(6)
            self.store.put("pc_id", v)
        return v

    def pc_name(self) -> str:
        """[review-fix 8] "Orion PC" unless you named it (POST /phones/pc-name): never the hostname."""
        import pc_identity
        return pc_identity.pc_name(getattr(self, "data_dir", None))

    def _facts(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.store.get("phone_facts", "{}") or "{}")
        except ValueError:
            return {}

    def address(self) -> dict[str, Any]:
        """The best address for the phone: {"url","host","how","words"}."""
        override = os.environ.get("ORION_PHONE_URL", "").strip().rstrip("/")
        if override:
            return {"url": override, "host": override.split("//")[-1].split(":")[0], "how": "set",
                    "words": "Using the address set in ORION_PHONE_URL."}
        if self.platform.startswith("linux"):
            # OrionOS lab inside VirtualBox on Windows: Open Phone Access.cmd stored the PC's Wi-Fi address.
            out = _cmd(self.run, ["VBoxControl", "--nologo", "guestproperty", "get", "/Orion/PhoneURL"], 4)
            if out.startswith("Value:"):
                url = out.split(":", 1)[1].strip().rstrip("/")
                if url:
                    return {"url": url, "host": url.split("//")[-1].split(":")[0], "how": "vbox",
                            "words": "Your phone reaches this lab through your Windows PC."}
        ip = self.route_ip()
        if ip and not _is_tailscale(ip):
            return {"url": f"http://{ip}:{self.port}", "host": ip, "how": "lan",
                    "words": "Your phone must be on the same Wi-Fi as this PC."}
        ts = _cmd(self.run, ["tailscale", "ip", "-4"], 4).splitlines()
        if ts and ts[0].strip():
            t = ts[0].strip()
            return {"url": f"http://{t}:{self.port}", "host": t, "how": "tailscale",
                    "words": "Tailscale must be on, on your phone."}
        if ip:
            return {"url": f"http://{ip}:{self.port}", "host": ip, "how": "tailscale",
                    "words": "Tailscale must be on, on your phone."}
        return {"url": "", "host": "", "how": "none",
                "words": "This PC isn't on a network. Connect it to your Wi-Fi first."}

    # ------------------------------------------------------------------ the phone port
    def port_running(self) -> bool:
        srv = getattr(self.board, "_server", None)
        if srv is not None and getattr(srv, "started", True) is not False:   # [phonelink] listening, not just made
            return True
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                return True
        except OSError:
            return False

    async def ensure_port(self) -> bool:
        """Turn the phone port on if it's off (Windows, or a Linux PC without the lab settings). Remembered."""
        if await asyncio.to_thread(self.port_running):
            return True
        try:
            await self.board.start(simulator_every_s=0.0, bind="0.0.0.0", port=self.port)
            self.store.put("phone_port_on", "1")
            return True
        except Exception as exc:
            log.warning("Couldn't start the phone port: %s", exc)
            return False

    def _helper(self, args: list[str], wait: bool = False) -> None:
        """OrionOS root helpers through pkexec (polkit rules decide; the password prompt is polkit's own)."""
        if not self.platform.startswith("linux") or not os.path.exists(args[0]):
            return
        cmd = ["pkexec", *args]
        if wait:
            _cmd(self.run, cmd, 120)
        else:
            threading.Thread(target=_cmd, args=(self.run, cmd, 120), daemon=True).start()

    # ------------------------------------------------------------------ can the phone reach us?
    def lan(self, fresh: bool = False) -> dict[str, Any]:
        """{"ok": True/False/None, "words", "fix": "open_access"|"vbox_open_access"|None, "fix_words"}"""
        now = self.clock()
        if not fresh and self._lan_cache and now - self._lan_cache[0] < LAN_CACHE_S:
            return self._lan_cache[1]
        res = self._lan_check()
        self._lan_cache = (now, res)
        return res

    def _lan_check(self) -> dict[str, Any]:
        if not self.port_running():
            return {"ok": False, "words": "The phone port on this PC is off.", "fix": None,
                    "fix_words": "Press Pair my phone again."}
        addr = self.address()
        if addr["how"] == "none":
            return {"ok": False, "words": addr["words"], "fix": None, "fix_words": ""}
        if self.platform == "win32":
            ps = ("$r = [bool](Get-NetFirewallRule -DisplayName '%s' -ErrorAction SilentlyContinue); "
                  "$c = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
                  "Where-Object { $_.IPv4Connectivity -ne 'Disconnected' } | "
                  "ForEach-Object { [string]$_.NetworkCategory }); "
                  "@{rule=$r; cats=$c} | ConvertTo-Json -Compress") % FIREWALL_RULE
            out = _cmd(self.run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 10)
            try:
                j = json.loads(out) if out else {}
            except ValueError:
                j = {}
            cats = j.get("cats") or []
            cats = [cats] if isinstance(cats, str) else cats
            if not j.get("rule"):
                return {"ok": False, "fix": "open_access",
                        "words": "Windows Firewall stops your phone reaching this PC.",
                        "fix_words": "Press \"Let my phone in\". Windows asks for admin permission once, and only "
                                     "your home Wi-Fi is let in, nothing else."}
            if cats and "Private" not in cats and "DomainAuthenticated" not in cats:
                return {"ok": False, "fix": "open_access",
                        "words": "Windows thinks this Wi-Fi is a public network, so it blocks phones.",
                        "fix_words": "If this is your home Wi-Fi, press \"Let my phone in\" and say yes to marking "
                                     "it as your home network."}
            return {"ok": True, "words": "Your phone can reach this PC on your home Wi-Fi.", "fix": None,
                    "fix_words": ""}
        if self.platform.startswith("linux") and addr["how"] == "lan":
            virt = _cmd(self.run, ["systemd-detect-virt"], 3)
            if virt and virt != "none":
                return {"ok": False, "fix": "vbox_open_access",
                        "words": "This lab runs inside your Windows PC, so your phone can't see it yet.",
                        "fix_words": "On Windows, double-click Open Phone Access.cmd, then press Pair my phone "
                                     "again here."}
        return {"ok": True, "words": addr["words"], "fix": None, "fix_words": ""}

    # ------------------------------------------------------------------ mDNS / DNS-SD
    def advertise(self) -> bool:
        """Announce _orion._tcp while pairing is open or a phone is paired. False when zeroconf isn't installed."""
        if self.zc_mod is None:
            return False
        want = bool(self.store.get("phone_pairing")) or bool(self.board.phones.list())
        addr = self.address()
        host = addr.get("host") or ""
        if not want or not _is_private(host) or not self.port_running():
            self.stop_advertising()
            return False
        try:
            packed = socket.inet_aton(host)
        except OSError:
            return False
        if self._zc_info is not None and getattr(self._zc_info, "addresses", [packed]) == [packed]:
            return True
        self.stop_advertising()
        try:
            name = f"{self.pc_name()} Orion {self.pc_id()[:4]}.{SERVICE_TYPE}"
            info = self.zc_mod.ServiceInfo(SERVICE_TYPE, name, addresses=[packed], port=self.port,
                                           properties={"id": self.pc_id(), "name": self.pc_name(), "v": "1"},
                                           server=f"orion-{self.pc_id()}.local.")
            zc = self.zc_mod.Zeroconf()
            zc.register_service(info)
            self._zc, self._zc_info = zc, info
            return True
        except Exception as exc:
            log.info("mDNS announce skipped: %s", exc)
            self._zc = self._zc_info = None
            return False

    def stop_advertising(self) -> None:
        zc, info = self._zc, self._zc_info
        self._zc = self._zc_info = None
        if zc is not None:
            try:
                if info is not None:
                    zc.unregister_service(info)
                zc.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ actions
    async def open(self) -> dict[str, Any]:
        # [review-fix 5] address(), advertise() and lan() run commands (PowerShell, tailscale, VBoxControl) and
        # mDNS registration: never on the event loop.
        await self.ensure_port()
        addr = await asyncio.to_thread(self.address)
        code = self.board.phones.new_code()
        raw = json.loads(self.store.get("phone_pairing") or "{}")
        url = f"orion://pair?u={addr['url']}&c={code}" if addr["url"] else ""
        if addr["how"] in ("lan", "vbox"):
            self._helper([PORT_HELPER, "open", "600"])   # OrionOS: port open on this Wi-Fi for 10 minutes
        await asyncio.to_thread(self.advertise)
        lan = await asyncio.to_thread(self.lan, True)
        return {"code": code, "url": url, "qr_svg": qr_svg.svg(url) if url else "", "expires": raw.get("expires"),
                "address": addr["url"], "how": addr["words"], "lan": lan, "zeroconf": self._zc is not None,
                "pc": {"id": self.pc_id(), "name": self.pc_name()}}

    def pending(self) -> list[dict[str, Any]]:
        raw = self.store.get("phone_pending")
        if not raw:
            return []
        p = json.loads(raw)
        return [{"device_id": p["device_id"], "name": p["name"], "fingerprint": p["fp"],
                 "words": fingerprint_words(p["fp"])}]

    def status(self) -> dict[str, Any]:
        raw = self.store.get("phone_pairing")
        exp = json.loads(raw).get("expires") if raw else None
        is_open = bool(exp and exp > self.clock())
        return {"open": is_open, "expires": exp if is_open else None, "pending": self.pending() if is_open else [],
                "phones": self.phones(), "pin": self.pin_state}

    def confirm(self, device_id: str, fp: str = "") -> dict[str, Any]:
        pend = self.pending()
        if not pend or pend[0]["device_id"] != device_id:
            raise PermissionError("No phone with that name is waiting. Press Pair my phone again.")
        if fp and "".join(fp.split("-")).upper() != "".join(pend[0]["fingerprint"].split("-")).upper():
            raise PermissionError("That fingerprint doesn't match the waiting phone.")
        out = self.board.phones.confirm(pend[0]["fingerprint"])
        self.pin_state = ""
        # OrionOS [H9]: root keeps its own copy of the approval key so unfreezing can check your fingerprint
        # itself. polkit asks for your password; saying no just means unfreezing asks for it on the PC instead.
        if self.platform.startswith("linux") and os.path.exists(FREEZE_HELPER):
            sign = json.loads(self.store.get("phones", "{}") or "{}").get(out["device_id"], {}).get("sign", "")
            if sign:
                self.pin_state = "asked"

                def pin() -> None:
                    rc = _rc(self.run, ["pkexec", FREEZE_HELPER, "pin-phone", out["device_id"], sign], 180)
                    self.pin_state = "done" if rc == 0 else "skipped"
                threading.Thread(target=pin, daemon=True).start()
        self._helper([PORT_HELPER, "sync"])
        self.advertise()
        return {**out, "words": fingerprint_words(out["fingerprint"])}

    def close(self) -> dict[str, Any]:
        self.store.put("phone_pending", None)
        self.store.put("phone_pairing", None)
        self._helper([PORT_HELPER, "sync"])
        self.advertise()
        return self.status()

    def phones(self) -> list[dict[str, Any]]:
        raw = json.loads(self.store.get("phones", "{}") or "{}")
        facts = self._facts()
        now = self.clock()
        out = []
        for did, p in raw.items():
            seen = int(p.get("last_ts", 0)) / 1000.0
            f = facts.get(did, {})
            seen = max(seen, float(f.get("ts", 0) or 0))
            out.append({"device_id": did, "name": p.get("name", "phone"), "fingerprint": p.get("fp", ""),
                        "words": fingerprint_words(p.get("fp", "")), "added": p.get("added"),
                        "last_seen": seen or None, "online": bool(seen and now - seen < ONLINE_S),
                        "battery": f.get("battery"), "charging": f.get("charging"), "network": f.get("network"),
                        "app": f.get("app"),
                        "link": __import__("phone_link").link_info(did, seen, f, now)})   # [phonelink]
        out.sort(key=lambda x: -(x["last_seen"] or 0))
        return out

    def unpair(self, device_id: str) -> dict[str, Any]:
        n = self.board.phones.remove(device_id)
        facts = self._facts()
        facts.pop(device_id, None)
        self.store.put("phone_facts", json.dumps(facts))
        if n and self.platform.startswith("linux") and os.path.exists(FREEZE_HELPER):
            self._helper([FREEZE_HELPER, "unpin-phone", device_id])
        self.advertise()
        return {"removed": n, "phones": self.phones()}

    def test_card(self) -> dict[str, Any]:
        if not self.board.phones.list():
            return {"ok": False, "words": "No phone is paired yet."}
        alerts = getattr(self.d, "alerts", None)
        if alerts is None:
            return {"ok": False, "words": "Phone alerts aren't running on this PC."}
        alerts.post("system", "Test from your PC", "If you can see this, your PC can reach your phone.",
                    level="normal")
        return {"ok": True, "words": "Sent. It shows on your phone within a few seconds if the phone is on the "
                                     "same Wi-Fi (or Tailscale), and within 15 minutes otherwise."}

    def open_access(self) -> dict[str, Any]:
        """Windows: run Open Phone Access in its own window (it explains each step and asks for admin once)."""
        if self.platform != "win32":
            return {"ok": False, "words": "This step is for Windows. On OrionOS, pairing opens the port itself."}
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            if OPEN_ACCESS_PS1.exists():
                self.popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(OPEN_ACCESS_PS1), "-ThisPC"], creationflags=flags)
            else:   # a packaged install without the script: just the firewall rule, home networks only
                rule = (f"New-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -Direction Inbound -Action Allow "
                        f"-Protocol TCP -LocalPort {self.port} -Profile Private -RemoteAddress LocalSubnet")
                enc = base64.b64encode(rule.encode("utf-16-le")).decode()
                self.popen(["powershell.exe", "-NoProfile", "-Command",
                            f"Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-EncodedCommand','{enc}'"],
                           creationflags=flags)
        except Exception as exc:
            return {"ok": False, "words": f"Couldn't start it: {exc}"}
        self._lan_cache = None
        return {"ok": True, "words": "A window opened. Say yes when Windows asks for permission, then come back "
                                     "here: the panel checks again by itself."}

    # ------------------------------------------------------------------ the phone's hello
    def hello(self, who: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        facts = self._facts()
        f: dict[str, Any] = {"ts": self.clock()}
        b = data.get("battery")
        if isinstance(b, (int, float)) and not isinstance(b, bool) and 0 <= b <= 100:
            f["battery"] = int(b)
        if isinstance(data.get("charging"), bool):
            f["charging"] = data["charging"]
        if str(data.get("network", "")) in ("wifi", "cellular", "ethernet", "vpn", "other"):
            f["network"] = str(data["network"])
        app = str(data.get("app", ""))[:20]
        if app:
            f["app"] = "".join(ch for ch in app if ch.isalnum() or ch in ".-_ ")
        import phone_link   # [phonelink] the phone's own view of the link (round trip, retries, key pinned)
        f.update(phone_link.hello_facts(data))   # [phonelink]
        facts[who["device_id"]] = f
        self.store.put("phone_facts", json.dumps(facts))
        out = {"ok": True, "pc": {"id": self.pc_id(), "name": self.pc_name()}, "phone": who.get("name"),
               "addresses": phone_link.addresses(self), "server_time": phone_link.now_ms()}   # [phonelink]
        # [review-fix 7] proof this is the paired PC: its pinned key signs the phone's fresh nonce
        try:
            import pc_identity
            out.update(pc_identity.hello_extras(self.board, self.pc_id(), data.get("nonce")))
        except Exception as exc:
            log.warning("hello signature skipped: %s", exc)
        return out


# ===================================================================================== wiring
_ui: Optional[PhonePairingUI] = None


def attach(daemon: Any, **kw: Any) -> PhonePairingUI:
    """Called from OrionDaemon.__init__: the helper plus POST /phone/hello on the phone port."""
    global _ui
    ui = PhonePairingUI(daemon, **kw)
    _ui = ui

    def hook(app, phone_auth, json_body) -> None:
        @app.post("/phone/hello")
        async def hello(request: Request):
            who, err = await phone_auth(request, "read", HELLO_BODY_MAX)
            if err is not None:
                return err
            data = await json_body(request)
            return await asyncio.to_thread(ui.hello, who, data or {})

    daemon.board.remote_hooks.append(hook)
    try:   # [phonelink] POST /phone/link-check (the phone's "Fix the connection")
        import phone_link
        daemon.board.remote_hooks.append(phone_link.phone_hook(ui))
    except Exception as exc:   # [phonelink]
        log.warning("Phone link checks not added: %s", exc)
    return ui


async def start(daemon: Any) -> None:
    """At daemon start: bring back the phone port if pairing turned it on before, then announce the PC."""
    ui = getattr(daemon, "phone_link", None) or _ui
    if ui is None:
        return
    if ui.store.get("phone_port_on") == "1" and not await asyncio.to_thread(ui.port_running):
        await ui.ensure_port()
    await asyncio.to_thread(ui.advertise)
    try:   # [phonelink] re-announce on address changes, firewall status at start
        import phone_link
        ui._watch = phone_link.LinkWatch(ui)
        ui._watch.start()
    except Exception as exc:   # [phonelink]
        log.warning("Phone link watch not started: %s", exc)


def stop(daemon: Any) -> None:
    ui = getattr(daemon, "phone_link", None) or _ui
    if ui is not None:
        if getattr(ui, "_watch", None) is not None:   # [phonelink]
            ui._watch.stop()
        ui.stop_advertising()


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    r = APIRouter()

    def ui() -> PhonePairingUI:
        u = getattr(get_daemon(), "phone_link", None) or _ui
        if u is None:
            raise RuntimeError("phone pairing isn't running")
        return u

    @r.post("/phones/pair/open")
    async def pair_open():
        return await ui().open()

    @r.get("/phones/pair/status")
    async def pair_status():
        u = ui()
        st = u.status()
        st["lan"] = await asyncio.to_thread(u.lan) if st["open"] else None   # [review-fix 5]
        return st

    @r.post("/phones/pair/close")
    async def pair_close():
        return await asyncio.to_thread(ui().close)

    @r.get("/phones")
    async def phones():
        u = ui()
        addr = await asyncio.to_thread(u.address)   # [review-fix 5]
        return {"phones": u.phones(), "pc": {"id": u.pc_id(), "name": u.pc_name()},
                "address": addr["url"], "zeroconf": u.zc_mod is not None}

    @r.post("/phones/pc-name")
    async def pc_name(request: Request):
        """[review-fix 8] The name phones and the Wi-Fi announcement show ("" = "Orion PC")."""
        import pc_identity
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = (body or {}).get("name", "") if isinstance(body, dict) else ""
        u = ui()
        now = await asyncio.to_thread(pc_identity.set_pc_name, name, u.data_dir)
        u.stop_advertising()
        await asyncio.to_thread(u.advertise)   # announce under the new name
        return {"name": now}

    @r.post("/phones/test-card")
    async def test_card():
        return ui().test_card()

    @r.post("/phones/open-access")
    async def open_access():
        return ui().open_access()

    @r.get("/phones/link")   # [phonelink] link status for the Phone panel
    async def link_status():
        import phone_link
        u = ui()
        return {"phones": [{"device_id": p["device_id"], **p["link"]} for p in u.phones()], **phone_link.status()}

    @r.get("/phones/link/check")   # [phonelink] "Fix the connection" on the PC
    async def link_check(device_id: str = ""):
        import phone_link
        u = ui()
        did = device_id or next((p["device_id"] for p in u.phones()), "")
        return await asyncio.to_thread(phone_link.diagnose, u, did or None)

    @r.get("/phones/lan")
    async def lan():
        return await asyncio.to_thread(ui().lan, True)

    @r.post("/phones/{device_id}/confirm")
    async def confirm(device_id: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        fp = str((body or {}).get("fingerprint", "")) if isinstance(body, dict) else ""
        try:
            return await asyncio.to_thread(ui().confirm, device_id, fp)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @r.post("/phones/{device_id}/unpair")
    async def unpair(device_id: str):
        if device_id == "all" or not device_id:
            return JSONResponse({"error": "unpair one phone at a time"}, status_code=400)
        return await asyncio.to_thread(ui().unpair, device_id)

    return r
