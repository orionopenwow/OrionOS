"""The protected-core list (protected_core.py + protected_core.json), Phase 8 §7 item 6."""
import json
from pathlib import Path

import pytest

import protected_core as pc

REPO = Path(__file__).resolve().parents[3]


def test_code_and_data_file_say_the_same_thing():
    data = json.loads(pc.DATA_FILE.read_text(encoding="utf-8"))
    from_data = {r["path"]: (sorted(r["symbols"]) if r.get("symbols") else None) for r in data["rules"]}
    from_code = {p: (sorted(s) if s else None) for p, _a, _w, s in pc.RULES}
    assert from_data == from_code
    assert [t["pattern"] for t in data["tamper"]] == [p for p, _ in pc.TAMPER]


def test_every_area_of_the_plan_is_covered():
    areas = {r["area"] for r in pc.rules()}
    assert {"approvals", "firewall", "privacy", "self-change gate"} <= areas
    for must in ("apps/daemon/approval_queue.py", "apps/daemon/boardroom.py", "apps/daemon/phones.py",
                 "apps/mobile/app/src/main/java/com/orion/mobile/Keys.kt", "os/common/harden.sh",
                 "apps/daemon/injection_firewall.py", "apps/daemon/capture_privacy.py", "apps/daemon/skills_recorder.py",
                 "apps/extension/recorder.js", "apps/daemon/api_token.py", "apps/daemon/self_evolve.py",
                 "os/lab/bin/orion-self-gate", "os/safety/orion-self-gate", "apps/daemon/self_levels.py"):
        assert pc.is_protected_path(must), must


def test_deleting_the_data_file_never_weakens(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "DATA_FILE", tmp_path / "missing.json")
    for path, _a, _w, _s in pc.RULES:
        assert pc.is_protected_path(path.replace("*", "x"))
    (tmp_path / "empty.json").write_text('{"rules": [], "tamper": []}', encoding="utf-8")
    monkeypatch.setattr(pc, "DATA_FILE", tmp_path / "empty.json")
    assert pc.is_protected_path("apps/daemon/approval_queue.py")


def test_data_file_can_add_but_not_remove(monkeypatch, tmp_path):
    f = tmp_path / "pc.json"
    f.write_text(json.dumps({"rules": [{"path": "apps/daemon/extra.py", "why": "x"},
                                       {"path": "apps/daemon/boardroom.py", "symbols": ["fmt_usd"]}]}),
                 encoding="utf-8")
    monkeypatch.setattr(pc, "DATA_FILE", f)
    assert pc.is_protected_path("apps/daemon/extra.py")
    board = pc.rule_for("apps/daemon/boardroom.py")
    assert "fmt_usd" in board["symbols"] and "verify_signature" in board["symbols"]


def test_paths_ignore_case_and_backslashes():
    assert pc.is_protected_path("Apps\\Daemon\\Approval_Queue.py")
    assert pc.is_protected_path("os/common/killswitch.nft.tmpl")
    assert not pc.is_protected_path("apps/daemon/xp.py")


@pytest.mark.parametrize("path,_a,_w,symbols", [r for r in pc.RULES if r[3]])
def test_symbol_lists_match_the_real_files(path, _a, _w, symbols):
    """If a protected function is renamed, this fails (and check() treats the whole file as protected meanwhile)."""
    ranges = pc._ranges((REPO / path).read_text(encoding="utf-8"))
    assert ranges is not None
    missing = [s for s in symbols if s not in ranges]
    assert not missing, f"{path}: {missing}"


BOARD = '''import base64
from x import Ed25519PublicKey


def fmt_usd(c):
    return c


def verify_signature(pub, msg, sig):
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except Exception:
        return False


class Boardroom:
    def state(self):
        return 1

    def verdict(self, d):
        return d
'''


def _check(before, after, path="apps/daemon/boardroom.py", action="modify"):
    return pc.check([{"path": path, "action": action, "before": before, "after": after}])


def test_symbols_inside_a_file(monkeypatch, tmp_path):
    f = tmp_path / "pc.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pc, "DATA_FILE", f)
    monkeypatch.setattr(pc, "RULES", [("apps/daemon/boardroom.py", "approvals", "signer",
                                        ["verify_signature", "Boardroom.verdict"])])
    assert _check(BOARD, BOARD.replace("return c", "return c * 2")) == []                 # unprotected part: fine
    assert _check(BOARD, BOARD.replace("return 1", "return 2")) == []                      # another method: fine
    hit = _check(BOARD, BOARD.replace("        return True", "        return True  # x"))
    assert hit and hit[0]["parts"] == ["verify_signature"]
    assert _check(BOARD, BOARD.replace("        return False", "        return True"))  # the real attack
    assert _check(BOARD, BOARD.replace("return d", "return {}"))[0]["parts"] == ["Boardroom.verdict"]
    assert _check(BOARD, BOARD + "\nverify_signature = lambda *a: True\n")                # re-bound later
    assert _check(BOARD, BOARD + "\nBoardroom.verdict = lambda s, d: d\n")
    assert _check(BOARD, BOARD + "\nsetattr(Boardroom, 'verdict', None)\n")
    assert _check(BOARD, BOARD.replace("from x import", "from fake import"))[0]["parts"] == ["<imports>"]
    inserted = BOARD.replace("    try:\n", "    return True\n    try:\n")
    assert _check(BOARD, inserted)                                                          # line inserted inside
    assert "couldn't tell" in _check(BOARD, BOARD + "\ndef broken(:\n")[0]["why"]           # can't parse: whole file
    renamed = BOARD.replace("def verify_signature", "def verify_sig")
    assert _check(renamed, renamed.replace("return c", "return 3"))                         # list out of date
    assert _check(BOARD, "", action="delete")


def test_tamper_from_other_files():
    hit = pc.check([{"path": "apps/daemon/xp.py", "action": "modify", "before": "x = 1\n",
                     "after": "x = 1\nimport boardroom\nboardroom.verify_signature = lambda *a: True\n"}])
    assert hit and hit[0]["area"] == "tamper"
    assert pc.check([{"path": "apps/daemon/xp.py", "action": "create", "before": "",
                      "after": "open('/home/u/.orion-signing/key')\n"}])
    assert pc.check([{"path": "apps/daemon/xp.py", "action": "modify", "before": "",
                      "after": "if boardroom.verify_signature == x:\n    pass\n"}]) == []      # comparing is fine
    assert pc.check([{"path": "apps/daemon/xp.py", "action": "modify", "before": "",
                      "after": "cfg.security.level = 2\n"}]) == []                           # not the module
    # tests only run inside the sandbox copy: they may monkeypatch the core
    assert pc.check([{"path": "apps/daemon/tests/test_x.py", "action": "create", "before": "",
                      "after": "boardroom.verify_signature = None\n"}]) == []


def test_describe_is_plain():
    words = pc.describe(pc.check([{"path": "apps/daemon/api_token.py", "action": "modify", "before": "a", "after": "b"}]))
    assert "protected core" in words and "fingerprint" in words
