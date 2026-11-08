"""This PC's name and key, as the phone sees them.

Name: phones and the Wi-Fi announcement (mDNS) show a neutral "Orion PC", never the computer's own hostname
(hostnames often hold the owner's name). You can pick another name (POST /phones/pc-name from the HUD's Phone panel,
or ORION_PC_NAME); it's kept in pc_identity.json in the data folder.

Key: an ECDSA P-256 key made once and kept in the boardroom folder (owner-only, next to the phone registry). The
phone pins its public half when it pairs (the pairing answer carries it with a proof made from the pairing code, so
only the PC that showed the code could have sent it). When the PC's address changes and the phone finds a PC on the
Wi-Fi with the same id, the phone sends a fresh random nonce in /phone/hello and only switches if the answer is
signed by the pinned key. Ids are announced in the clear, so the id alone proves nothing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.pc_identity")

DEFAULT_NAME = "Orion PC"
NAME_MAX = 40
KEY_FILE = "pc_identity_key.pem"
NAME_FILE = "pc_identity.json"
PAIR_PROOF_LABEL = b"orion-pc-key\n"
HELLO_LABEL = b"orion-pc-hello\n"
NONCE_RE = re.compile(r"[A-Za-z0-9_\-+/=]{16,128}")

_lock = threading.Lock()


# ------------------------------------------------------------------ name
def _data_dir(data_dir: Optional[Path]) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    from config import settings
    return Path(settings.data_dir)


def clean_name(name: Any) -> str:
    """Letters, digits, spaces and a little punctuation; at most NAME_MAX characters ("" if nothing is left)."""
    s = "".join(ch for ch in str(name or "") if ch.isalnum() or ch in " .-_'")
    return " ".join(s.split())[:NAME_MAX]


def pc_name(data_dir: Optional[Path] = None) -> str:
    """The name you gave this PC, else ORION_PC_NAME, else "Orion PC". Never the hostname."""
    try:
        p = _data_dir(data_dir) / NAME_FILE
        if p.exists():
            n = clean_name(json.loads(p.read_text(encoding="utf-8")).get("name"))
            if n:
                return n
    except Exception:
        pass
    return clean_name(os.environ.get("ORION_PC_NAME", "")) or DEFAULT_NAME


def set_pc_name(name: Any, data_dir: Optional[Path] = None) -> str:
    """Saves the name ("" goes back to "Orion PC"). Returns the name now in use."""
    d = _data_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / NAME_FILE).write_text(json.dumps({"name": clean_name(name)}), encoding="utf-8")
    return pc_name(d)


# ------------------------------------------------------------------ key
class PcKey:
    def __init__(self, folder: Path) -> None:
        self.path = Path(folder) / KEY_FILE
        self._key: Any = None

    def _load(self) -> Any:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        with _lock:
            if self._key is not None:
                return self._key
            if self.path.exists():
                self._key = serialization.load_pem_private_key(self.path.read_bytes(), password=None)
                return self._key
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            old = os.umask(0o077) if hasattr(os, "umask") else None
            try:
                tmp = self.path.with_suffix(".tmp")
                fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(pem)
                os.replace(tmp, self.path)
            finally:
                if old is not None:
                    os.umask(old)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._key = key
            return key

    def public_der(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._load().public_key().public_bytes(serialization.Encoding.DER,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo)

    def public_b64(self) -> str:
        return base64.b64encode(self.public_der()).decode()

    def sign(self, message: bytes) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        return base64.b64encode(self._load().sign(message, ec.ECDSA(hashes.SHA256()))).decode()


def for_board(board: Any) -> PcKey:
    """The key lives in the boardroom folder (owner-only), next to the phone registry."""
    k = getattr(board, "_pc_key", None)
    if k is None:
        k = PcKey(Path(board.data_dir))
        try:
            board._pc_key = k
        except Exception:
            pass
    return k


def _norm_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def pair_proof(code: str, pc_der: bytes) -> str:
    """HMAC(code, label + PC key): the phone checks it with the code it typed (or scanned)."""
    return hmac.new(_norm_code(code).encode(), PAIR_PROOF_LABEL + pc_der, hashlib.sha256).hexdigest()


def pair_extras(board: Any, code: str) -> dict[str, str]:
    k = for_board(board)
    der = k.public_der()
    return {"pc_key": base64.b64encode(der).decode(), "pc_key_proof": pair_proof(code, der)}


def hello_message(pc_id: str, nonce: str) -> bytes:
    return HELLO_LABEL + pc_id.encode() + b"\n" + nonce.encode()


def hello_extras(board: Any, pc_id: str, nonce: Any) -> dict[str, str]:
    """{"pc_key","pc_sig"} for a phone-chosen nonce, or {} when the nonce is missing or odd."""
    n = str(nonce or "")
    if not NONCE_RE.fullmatch(n):
        return {}
    k = for_board(board)
    return {"pc_key": k.public_b64(), "pc_sig": k.sign(hello_message(pc_id, n))}
