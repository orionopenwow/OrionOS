"""Orion Mobile: paired phones as signers (ARCHITECTURE.md §8).

Each phone holds two ECDSA P-256 keys in Android's hardware keystore:

  read key   no fingerprint needed; signs requests that only *read* (the queue, notifications)
  sign key   usable only right after a fingerprint; the only key whose signature can approve or reject

Pairing works like the boardroom's: `orion-ctl pair-phone` opens a one-time code (shown as a QR that the
phone's camera opens in the app), the phone registers both public keys with an HMAC proof of the code, and
the key stays *pending* until you compare fingerprints and confirm on the lab. Requests carry
X-Orion-Device / X-Orion-Ts / X-Orion-Sig over the same canonical message as the boardroom
(boardroom.signing_message), with strictly increasing timestamps per phone.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Optional

log = logging.getLogger("orion.phones")

PAIRING_TTL_S = 600
# [phonelink] A phone's clock may be up to 5 minutes off; every answer carries X-Orion-Time so the phone corrects
# itself (phone_link.py). Requests may also arrive out of order (several screens, retries) within REORDER_MS: each
# one is remembered by its signed message, so a copy is still refused (replay) but an honest late arrival is not.
MAX_CLOCK_SKEW_S = 300
REORDER_MS = 60_000
SEEN_MAX = 512
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class ClockSkewError(PermissionError):
    """The phone's timestamp is too far from the PC's clock. skew_s > 0: the phone is ahead."""

    def __init__(self, skew_s: float) -> None:
        self.skew_s = skew_s
        super().__init__(skew_words(skew_s))


class ReplayError(PermissionError):
    """A correctly signed request the PC has already seen (or older than the reorder window)."""


def skew_words(skew_s: float) -> str:
    mins = max(1, round(abs(skew_s) / 60))
    way = "ahead of" if skew_s > 0 else "behind"
    return (f"stale request: your phone's clock is {mins} minute{'s' if mins != 1 else ''} {way} your PC's clock. "
            "Turn on automatic date and time on the phone (or fix the PC's clock).")


def _norm(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def fingerprint(spki_der: bytes) -> str:
    h = hashlib.sha256(spki_der).hexdigest()[:20].upper()
    return "-".join(h[i:i + 4] for i in range(0, 20, 4))


def pairing_proof(code: str, read_der: bytes, sign_der: bytes) -> str:
    return hmac.new(_norm(code).encode(), read_der + sign_der, hashlib.sha256).hexdigest()


def _load_p256(spki_der: bytes):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    key = load_der_public_key(spki_der)
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("phone keys must be EC P-256")
    return key


def verify_p256(spki_der: bytes, message: bytes, sig_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        _load_p256(spki_der).verify(base64.b64decode(sig_b64, validate=True), message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class PhoneRegistry:
    """Phones live in the boardroom store's key/value table (owner-only file, see boardroom.Store)."""

    def __init__(self, store: Any) -> None:
        self.s = store

    # ------------------------------------------------------------------ storage
    def _phones(self) -> dict[str, dict[str, Any]]:
        return json.loads(self.s.get("phones", "{}") or "{}")

    def _save(self, phones: dict[str, dict[str, Any]]) -> None:
        self.s.put("phones", json.dumps(phones))

    def list(self) -> list[dict[str, Any]]:
        return [{"device_id": k, "name": v["name"], "fingerprint": v["fp"], "added": v["added"]}
                for k, v in self._phones().items()]

    # ------------------------------------------------------------------ pairing
    def new_code(self) -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(28))
        code = "-".join(raw[i:i + 4] for i in range(0, 28, 4))
        self.s.put("phone_pairing", json.dumps({"code": code, "expires": time.time() + PAIRING_TTL_S, "fails": 0}))
        self.s.put("phone_pending", None)
        return code

    def pair(self, device_id: str, name: str, read_b64: str, sign_b64: str, proof_hex: str) -> str:
        raw = self.s.get("phone_pairing")
        if not raw:
            raise PermissionError("phone pairing is not open (run orion-ctl pair-phone on the lab)")
        pairing = json.loads(raw)
        if time.time() > pairing["expires"]:
            self.s.put("phone_pairing", None)
            raise PermissionError("pairing code expired")
        device_id = "".join(ch for ch in device_id if ch.isalnum() or ch in "-_")[:64]
        if len(device_id) < 8:
            raise ValueError("bad device id")
        try:
            read_der = base64.b64decode(read_b64, validate=True)
            sign_der = base64.b64decode(sign_b64, validate=True)
            _load_p256(read_der)
            _load_p256(sign_der)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"bad phone key: {exc}")
        if read_der == sign_der:
            raise ValueError("read and sign keys must differ")
        if not hmac.compare_digest(pairing_proof(pairing["code"], read_der, sign_der), proof_hex or ""):
            pairing["fails"] = int(pairing.get("fails", 0)) + 1
            if pairing["fails"] >= 5:
                self.s.put("phone_pairing", None)
                raise PermissionError("too many wrong codes; pairing closed. Make a new code on the lab.")
            self.s.put("phone_pairing", json.dumps(pairing))
            raise PermissionError("wrong pairing code")
        fp = fingerprint(sign_der)
        self.s.put("phone_pending", json.dumps({
            "device_id": device_id, "name": (name or "phone")[:40], "read": read_b64, "sign": sign_b64, "fp": fp}))
        log.warning("Phone %s (%s) is waiting for confirmation on the lab", name, fp)
        return fp

    def pending_fingerprint(self) -> Optional[str]:
        raw = self.s.get("phone_pending")
        return json.loads(raw)["fp"] if raw else None

    def confirm(self, fp: str) -> dict[str, Any]:
        raw = self.s.get("phone_pending")
        if not raw or not self.s.get("phone_pairing"):
            raise PermissionError("no phone is waiting for confirmation")
        p = json.loads(raw)
        if not hmac.compare_digest(_norm(p["fp"]), _norm(fp)):
            raise PermissionError("fingerprint doesn't match the waiting phone")
        phones = self._phones()
        old = phones.get(p["device_id"], {})
        phones[p["device_id"]] = {"name": p["name"], "read": p["read"], "sign": p["sign"], "fp": p["fp"],
                                  "added": time.time(), "last_ts": int(old.get("last_ts", 0))}  # never lowered
        self._save(phones)
        self.s.put("phone_pending", None)
        self.s.put("phone_pairing", None)
        return {"device_id": p["device_id"], "name": p["name"], "fingerprint": p["fp"]}

    def remove(self, device_id: str) -> int:
        phones = self._phones()
        if device_id == "all":
            n = len(phones)
            phones = {}
        else:
            n = 1 if phones.pop(device_id, None) else 0
        self._save(phones)
        return n

    # ------------------------------------------------------------------ request checks
    def check(self, device_id: Optional[str], method: str, path: str, ts: Optional[str], sig: Optional[str],
              body: bytes, need: str) -> dict[str, Any]:
        """Verify a phone request. need = "read" (either key) or "sign" (sign key only)."""
        from boardroom import signing_message

        with self.s.lock:
            phones = self._phones()
            phone = phones.get(device_id or "")
            if phone is None:
                raise PermissionError("unknown or unpaired phone")
            if not ts or not sig:
                raise PermissionError("unsigned request")
            try:
                t = int(ts)  # milliseconds: a phone can make several requests in one second
            except ValueError:
                raise PermissionError("bad timestamp")
            skew = t / 1000 - time.time()
            if abs(skew) > MAX_CLOCK_SKEW_S:
                raise ClockSkewError(skew)
            msg = signing_message(method, path, ts, body)
            sign_der = base64.b64decode(phone["sign"])
            ok = verify_p256(sign_der, msg, sig)
            if not ok and need == "read":
                ok = verify_p256(base64.b64decode(phone["read"]), msg, sig)
            if not ok:
                raise PermissionError("bad signature" if need == "read" else
                                      "approvals need the fingerprint-protected key")
            # [phonelink] replay guard with a reorder window. Keyed on the signed message (not the signature:
            # ECDSA signatures can be re-encoded), so the same request can never be used twice.
            last = int(phone.get("last_ts", 0))
            if "seen" not in phone:          # a phone record from before the window: strict from where it was
                phone["floor"], phone["seen"] = last, []
            floor = int(phone.get("floor", 0))
            digest = hashlib.sha256(msg).hexdigest()[:20]
            seen = [e for e in phone["seen"] if int(e[0]) > floor]
            if t <= floor or any(e[1] == digest for e in seen):
                raise ReplayError("replayed request")
            seen.append([t, digest])
            last = max(last, t)
            floor = max(floor, last - REORDER_MS)
            seen = sorted((e for e in seen if int(e[0]) > floor), key=lambda e: int(e[0]))
            if len(seen) > SEEN_MAX:
                floor = int(seen[-SEEN_MAX - 1][0])
                seen = seen[-SEEN_MAX:]
            phone["last_ts"], phone["floor"], phone["seen"] = last, floor, seen
            self._save(phones)
            return {"device_id": device_id, "name": phone["name"], "fingerprint": phone["fp"]}
