#!/usr/bin/env python3
"""Orion boardroom: the sign-off station that runs on VM 2 (orion-boardroom).

It holds the only key that can approve Orion's high-stakes decisions. The key is generated here, encrypted
with your passphrase, and never leaves this machine. Every approval asks for the passphrase again.

    orion-boardroom init            make the signing key (once)
    orion-boardroom pair            pair with the lab (type the code the lab shows)
    orion-boardroom                 the sign-off console (default)
    orion-boardroom fingerprint     show this key's fingerprint

Needs only Python 3 and python3-cryptography. Talks to the lab over Tailscale at http://orion-lab:9121
(override with ORION_LAB_URL).
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HOME = Path(os.environ.get("ORION_BOARDROOM_HOME", Path.home() / ".orion-boardroom"))
KEY_FILE = HOME / "boardroom.key"
PUB_FILE = HOME / "boardroom.pub"
LAB_URL = os.environ.get("ORION_LAB_URL", "http://orion-lab:9121").rstrip("/")
SIG_DOMAIN = b"ORION-BOARDROOM-v1\n"

C = {"dim": "\033[2m", "b": "\033[1m", "cyan": "\033[96m", "green": "\033[92m", "red": "\033[91m",
     "amber": "\033[93m", "violet": "\033[95m", "x": "\033[0m"}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}
DOMAIN_COLOR = {"money": "amber", "code": "cyan", "infra": "violet", "community": "green", "autonomy": "violet"}


def fingerprint(pub: bytes) -> str:
    h = hashlib.sha256(pub).hexdigest()[:20].upper()
    return "-".join(h[i:i + 4] for i in range(0, 20, 4))


def packet_hash(packet: dict[str, Any]) -> str:
    """Same canonical hash as the lab. The console signs the hash of the packet it *shows you*."""
    canon = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def norm_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def fmt_usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


# ----------------------------------------------------------------------------- key handling
def init_key(passphrase: Optional[str] = None) -> str:
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(HOME, 0o700)
    if KEY_FILE.exists():
        raise SystemExit(f"A key already exists at {KEY_FILE}. Delete it yourself if you really mean to replace it.")
    if passphrase is None:
        while True:
            passphrase = getpass.getpass("Choose a passphrase for the boardroom key (12+ characters): ")
            if len(passphrase) < 12:
                print("Too short.")
                continue
            if getpass.getpass("Type it again: ") != passphrase:
                print("They don't match.")
                continue
            break
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(passphrase.encode()))
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    PUB_FILE.write_text(base64.b64encode(pub).decode() + "\n")
    return fingerprint(pub)


def public_key() -> bytes:
    if not PUB_FILE.exists():
        raise SystemExit("No key yet. Run: orion-boardroom init")
    return base64.b64decode(PUB_FILE.read_text().strip())


def load_key(passphrase: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=passphrase.encode())
    assert isinstance(key, Ed25519PrivateKey)
    return key


# ----------------------------------------------------------------------------- HTTP
class Lab:
    def __init__(self, url: str = LAB_URL) -> None:
        self.url = url
        self._last_ts = 0

    def _ts(self) -> str:
        t = max(int(time.time()), self._last_ts + 1)  # strictly increasing, as the lab requires
        self._last_ts = t
        return str(t)

    def request(self, method: str, path: str, key: Optional[Ed25519PrivateKey] = None,
                body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else b""
        req = urllib.request.Request(self.url + path, data=data if body is not None else None, method=method)
        req.add_header("Content-Type", "application/json")
        if key is not None:
            ts = self._ts()
            msg = SIG_DOMAIN + f"{method}\n{path}\n{ts}\n{hashlib.sha256(data).hexdigest()}".encode()
            req.add_header("X-Orion-Ts", ts)
            req.add_header("X-Orion-Sig", base64.b64encode(key.sign(msg)).decode())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", str(e))
            except Exception:
                err = str(e)
            raise RuntimeError(err) from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"can't reach the lab at {self.url} ({e.reason}). Is Tailscale up on both VMs?") from None


# ----------------------------------------------------------------------------- commands
def cmd_pair(lab: Lab, code: Optional[str] = None) -> str:
    pub = public_key()
    mine = fingerprint(pub)
    print(f"{C['b']}Pair with the lab{C['x']}")
    print("On orion-lab, open a terminal (Ctrl+Alt+T) and run:  orion-ctl pair-boardroom")
    if code is None:
        code = input("Pairing code shown on the lab: ").strip()
    proof = hmac.new(norm_code(code).encode(), pub, hashlib.sha256).hexdigest()
    lab.request("POST", "/boardroom/pair", body={"pubkey": base64.b64encode(pub).decode(), "proof": proof})
    print(f"\nThis boardroom key's fingerprint is:\n\n    {C['b']}{mine}{C['x']}\n")
    print("The lab now shows the fingerprint it received and asks you to confirm it.")
    print(f"{C['amber']}Confirm on the lab ONLY if it shows exactly {mine}.{C['x']} Anything else means")
    print("something on the network swapped the key: answer no there and investigate.")
    input("\nPress Enter once you've confirmed on the lab… ")
    return mine


def show(d: dict[str, Any], i: int, n: int) -> None:
    p = d["packet"]
    col = C[DOMAIN_COLOR.get(p["domain"], "cyan")]
    print(f"\n{C['dim']}── decision {i}/{n} ─────────────────────────────────────────────{C['x']}")
    print(f"{col}{C['b']}{p['label'].upper()}{C['x']}  {C['dim']}{p['id']} · risk {p['risk']} · from {p['proposer']}{C['x']}")
    print(f"{C['b']}{p['summary']}{C['x']}")
    det = p["details"]
    for k, v in det.items():
        if k == "amount_cents":
            v = f"{fmt_usd(int(v))}  {C['amber']}(paper money, simulated){C['x']}"
        print(f"  {k:<26} {textwrap.shorten(str(v), 70)}")
    if p.get("recommendation"):
        print(f"  {C['cyan']}Orion recommends: {p['recommendation']} (it has earned level {p['autonomy_level']}){C['x']}")


def ledger_line(ledger: dict[str, Any]) -> str:
    parts = [f"{b['label']} {fmt_usd(b['balance_cents'])}" for b in ledger["buckets"]]
    ok = f"{C['green']}chain ok{C['x']}" if ledger.get("chain_ok") else f"{C['red']}CHAIN BROKEN{C['x']}"
    return f"Paper treasury: {' · '.join(parts)}  ({ok})"


def cmd_console(lab: Lab) -> None:
    pub = public_key()
    print(f"{C['b']}Orion boardroom{C['x']}  key {fingerprint(pub)}  lab {lab.url}")
    print(f"{C['dim']}Enter = refresh · a = approve · e = edit · r = reject · s = skip · q = quit{C['x']}")
    # Pending lists need a signature too, so unlock once to read; each verdict asks again.
    while True:
        try:
            pw = getpass.getpass("\nPassphrase (to read the queue): ")
            reader = load_key(pw)
            break
        except (ValueError, TypeError):
            print(f"{C['red']}Wrong passphrase.{C['x']}")
    while True:
        try:
            data = lab.request("GET", "/boardroom/pending", key=reader)
        except RuntimeError as exc:
            print(f"{C['red']}{exc}{C['x']}")
            if input("Retry? [Enter / q] ").strip().lower() == "q":
                return
            continue
        print("\n" + ledger_line(data["ledger"]))
        for a in data["autonomy"]:
            nxt = f", next level at {a['next_level_at']} in a row" if a.get("next_level_at") else ""
            print(f"  {C['dim']}{a['label']:<28} L{a['level']} {a['level_name']:<20} streak {a['streak']}{nxt}{C['x']}")
        pending = data["pending"]
        if not pending:
            ans = input(f"\n{C['green']}Nothing waiting.{C['x']} [Enter to refresh / q] ").strip().lower()
            if ans == "q":
                return
            continue
        for i, d in enumerate(pending, 1):
            if packet_hash(d["packet"]) != d.get("packet_hash"):
                print(f"{C['red']}Decision {d.get('id')} doesn't match its own fingerprint (packet hash). "
                      f"Not signing it. Check the lab.{C['x']}")
                continue
            show(d, i, len(pending))
            ans = input("[a]pprove  [e]dit  [r]eject  [s]kip  [q]uit > ").strip().lower()
            if ans == "q":
                return
            if ans not in ("a", "e", "r"):
                continue
            verdict = {"a": "approve", "e": "edit", "r": "reject"}[ans]
            edits: dict[str, Any] = {}
            if verdict == "edit":
                if "amount_cents" not in d["packet"]["details"]:
                    print("Only payout amounts can be edited here; approve or reject instead.")
                    continue
                raw = input("New amount in dollars (lower only): $").strip()
                try:
                    edits["amount_cents"] = int(round(float(raw) * 100))
                except ValueError:
                    print("Not a number.")
                    continue
            note = input("Note (optional): ").strip()
            try:
                signer = load_key(getpass.getpass(f"Passphrase to sign “{verdict}”: "))
            except (ValueError, TypeError):
                print(f"{C['red']}Wrong passphrase. Nothing was signed.{C['x']}")
                continue
            try:
                res = lab.request("POST", "/boardroom/verdict", key=signer, body={
                    "id": d["id"], "verdict": verdict, "packet_hash": packet_hash(d["packet"]), "note": note,
                    "edits": edits})
            except RuntimeError as exc:
                print(f"{C['red']}Refused by the lab: {exc}{C['x']}")
                continue
            dd = res["decision"]
            colour = C["green"] if dd["status"] in ("approved", "edited") else C["red"]
            print(f"{colour}→ {dd['status']}{C['x']}  {C['dim']}{json.dumps(dd.get('executed') or {})}{C['x']}")


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "console"
    lab = Lab()
    if cmd == "init":
        print(f"Key created. Fingerprint: {C['b']}{init_key()}{C['x']}")
        print(f"Back it up: copy {KEY_FILE} to an encrypted USB stick and keep it offline.")
    elif cmd == "fingerprint":
        print(fingerprint(public_key()))
    elif cmd == "pair":
        cmd_pair(lab)
    elif cmd == "console":
        if not KEY_FILE.exists():
            print("First run: let's make the boardroom signing key.")
            print(f"Key created. Fingerprint: {C['b']}{init_key()}{C['x']}")
            cmd_pair(lab)
        cmd_console(lab)
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except (KeyboardInterrupt, EOFError):
        print()
