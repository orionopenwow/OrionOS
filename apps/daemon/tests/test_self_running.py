"""Self-running (complete plan step 4): permission levels, self-repair, update channels with a test run, keeping
everything current, the weekly review. Nothing real is touched: a fake Host stands in for systemctl, the root
helper, ollama, fwupdmgr, orion-slot / orion-selftest (the QEMU runner) and terminals."""
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import approval_queue
import boardroom as br
import changes
import freeze
import self_cards
import self_host
import self_levels
import self_repair
import self_running
import self_updates
import update_channels
import weekly_review


def run(coro):
    return asyncio.run(coro)


class FakeBus:
    def __init__(self):
        self.events, self.logs = [], []
        self.clients = {}

    async def publish(self, e, **k):
        self.events.append(e)

    async def log(self, m, level="info"):
        self.logs.append((level, m))


class FakeMonitor:
    """The health monitor: `last` plus a queue of snapshots check_now() hands out."""
    def __init__(self, checks):
        self.last = {"checks": checks}
        self.next = []

    async def check_now(self):
        if self.next:
            self.last = {"checks": self.next.pop(0)}
        return self.last


def chk(cid, status, fix=None, detail=None, label=None):
    return {"id": cid, "label": label or cid.capitalize(), "status": status, "reason": f"{cid} is {status}",
            "fix": fix, "detail": detail}


class FakeOllama:
    def __init__(self):
        self.models = {"llama3.1:8b": "d1"}
        self.next_digest = "d2"
        self.broken = set()
        self.online = True

    def __call__(self, method, url, body, timeout):
        if not self.online:
            return None
        path = url.split("11434")[-1]
        if path == "/api/tags":
            return {"models": [{"name": n, "digest": d} for n, d in self.models.items()]}
        if path == "/api/copy":
            self.models[body["destination"]] = self.models[body["source"]]
            if self.models[body["destination"]] in self.broken and body["destination"] in self.models:
                pass
            return {}
        if path == "/api/pull":
            self.models[body["model"]] = self.next_digest
            return {"status": "success"}
        if path == "/api/generate":
            return {"response": "" if self.models.get(body["model"]) in self.broken else "ok"}
        return None


class FakeHost(self_host.Host):
    def __init__(self):
        self.calls, self.terminals = [], []
        self.rules = []                 # (argv prefix, rc, out)
        self.tools = {"pkexec", "fwupdmgr", "orion-slot", "orion-selftest", "orion-update"}
        self.helper_rc = 0
        self.t = 1_800_000_000.0
        self.ollama = FakeOllama()

    def on(self, prefix, rc=0, out=""):
        self.rules.insert(0, (prefix, rc, out))

    def run(self, argv, timeout=120):
        self.calls.append(list(argv))
        for prefix, rc, out in self.rules:
            if list(argv[:len(prefix)]) == list(prefix):
                return rc, out(argv) if callable(out) else out
        return 0, ""

    def which(self, name):
        return name if name in self.tools else None

    def exists(self, path):
        return False

    def helper(self, *args, timeout=120):
        self.calls.append(["HELPER", *args])
        return self.helper_rc, "ok" if not self.helper_rc else "refused"

    def open_terminal(self, command):
        self.terminals.append(command)
        return True

    def http_json(self, method, url, body=None, timeout=60):
        return self.ollama(method, url, body, timeout)

    def now(self):
        return self.t

    async def sleep(self, s):
        return None


class FakeDaemon:
    def __init__(self, tmp, checks=()):
        self.board = br.Boardroom(Path(tmp) / "board")
        self.approvals = approval_queue.ApprovalQueue(Path(tmp) / "approvals.json")
        self.bus = FakeBus()
        self.health = FakeMonitor(list(checks))
        self._tasks = []


@pytest.fixture()
def env(tmp_path, monkeypatch):
    changes.install(changes.ChangeLog(tmp_path / "changes.db"))
    freeze.set_dir(tmp_path / "data")
    monkeypatch.setattr(freeze, "HELPER", str(tmp_path / "no-helper"))
    monkeypatch.setenv("ORION_APPS_HISTORY", str(tmp_path / "apps.jsonl"))
    monkeypatch.setenv("ORION_SYSTEM_CHANGES", str(tmp_path / "system.jsonl"))
    monkeypatch.setenv("ORION_CHANNEL_FILE", str(tmp_path / "channel"))
    monkeypatch.setenv("ORION_CMDLINE", str(tmp_path / "cmdline"))
    (tmp_path / "cmdline").write_text("BOOT_IMAGE=/vmlinuz orion.slot=a quiet\n")
    self_levels.install(self_levels.SelfLevels(tmp_path / "data", admin=tmp_path / "admin-levels.json"))
    cfg = SimpleNamespace(data_dir=tmp_path / "data", ollama_url="http://127.0.0.1:11434", ollama_model="llama3.1:8b")
    monkeypatch.setattr(self_updates, "_settings", lambda: cfg)
    host = FakeHost()
    self_host.set_host(host)
    import alerts
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append((a, k)))
    import core_signals
    signals = []
    monkeypatch.setattr(core_signals, "set_external_signal", lambda n, v, now=None: signals.append((n, v)) or True)
    self_running._parts.clear()
    yield SimpleNamespace(tmp=tmp_path, host=host, alerts=sent, signals=signals, cfg=cfg)
    self_running._parts.clear()
    self_host.set_host(None)
    self_levels.install(None)
    freeze.set_dir(None)
    changes.install(None)


def freeze_now(d):
    run(freeze.freeze(d, "test", "testing"))
    assert freeze.is_frozen()


def approve(d, decision_id):
    dec = d.board.get(decision_id)
    return run(d.board.verdict(decision_id, "approve", br.packet_hash(dec["packet"])))


def make_repair(d, env, **kw):
    rep = self_repair.SelfRepair(d, env.tmp / "data", host=env.host, settle_s=0, home=env.tmp / "home", **kw)
    self_running._parts.update({"daemon": d, "repair": rep})
    return rep


# =====================================================================================  permission levels
def test_defaults_match_the_plan_table(env):
    want = {"security_update": "do", "app_update": "tell", "routine_fix": "do", "system_version": "ask",
            "ai_model": "tell", "ui_code": "ask", "daemon_code": "ask", "protected_core": "ask", "firmware": "ask"}
    assert {k: self_levels.decide(k) for k in want} == want
    assert self_levels.decide("something-new") == "ask"
    core = next(r for r in self_levels.current().table() if r["kind"] == "protected_core")
    assert not core["raisable"] and core["fingerprint"]


def test_freeze_makes_every_kind_ask(env):
    d = FakeDaemon(env.tmp)
    freeze_now(d)
    assert {self_levels.decide(k) for k in self_levels.KINDS} == {"ask"}


def test_lowering_is_immediate_raising_asks_the_phone(env):
    d = FakeDaemon(env.tmp)
    res = run(self_levels.request_change(d, "app_update", 1, by="Settings"))
    assert res["ok"] and self_levels.decide("app_update") == "ask"
    res = run(self_levels.request_change(d, "app_update", 3, by="Settings"))
    assert res["pending"] and self_levels.decide("app_update") == "ask"     # nothing changes before the phone
    again = run(self_levels.request_change(d, "app_update", 3))
    assert again["decision"] == res["decision"]                              # one question, not two
    self_levels.install_executor(d)
    approve(d, res["decision"])
    assert self_levels.decide("app_update") == "do"
    whats = [c["what"] for c in changes.current().list()]
    assert any("Raised" in w for w in whats) and any("Lowered" in w for w in whats)


def test_fixed_rows_cant_be_raised(env):
    d = FakeDaemon(env.tmp)
    for kind in ("protected_core", "daemon_code", "system_version", "firmware"):
        res = run(self_levels.request_change(d, kind, 2))
        assert not res["ok"] and self_levels.current().level(kind) == 1
    with pytest.raises(ValueError):
        run(self_levels.request_change(d, "app_update", 7))


def test_admin_file_newest_wins_and_is_clamped(env):
    lv = self_levels.current()
    (env.tmp / "admin-levels.json").write_text(json.dumps({"levels": {
        "app_update": {"level": 3, "at": time.time() + 5, "by": "admin"},
        "ui_code": {"level": 3, "at": time.time() + 5}}}))
    assert lv.decide("app_update") == "do" and lv.entry("app_update")["where"] == "admin"
    assert lv.level("ui_code") == 2            # clamped to what the kind allows
    lv.set_level("app_update", 1, "you")       # newer than nothing? the admin entry is in the future, so it wins
    assert lv.level("app_update") == 3


def test_learner_suggests_after_a_streak_and_never_raises(env, monkeypatch):
    d = FakeDaemon(env.tmp)
    monkeypatch.setattr(self_levels, "STREAK_TO_SUGGEST", 3)
    lv = self_levels.current()
    lv.set_level("app_update", 1, "you")
    for _ in range(2):
        assert lv.record_verdict("app_update", True) is None
    assert lv.record_verdict("app_update", True) == 2
    res = run(self_levels.suggest_raise(d, "app_update", 2))
    assert res["pending"] and lv.level("app_update") == 1                   # only a suggestion
    assert lv.record_verdict("app_update", True) is None                     # not asked twice
    assert lv.record_verdict("app_update", False) is None and lv._state()["streaks"]["app_update"] == 0


# =====================================================================================  cards
async def _noop_action(daemon, args):
    _noop_action.ran.append(args)
    return {"ok": True, "message": "did it"}
_noop_action.ran = []


def test_card_approve_runs_reject_counts_and_frozen_blocks(env):
    d = FakeDaemon(env.tmp)
    self_cards.register("test_action", _noop_action)
    _noop_action.ran.clear()
    card = run(self_cards.propose(d, "app_update", "Update X?", "body", "test_action", {"x": 1}, key="k1"))
    assert run(self_cards.propose(d, "app_update", "Update X?", "body", "test_action", {"x": 1}, key="k1"))["id"] == card["id"]
    run(self_running.decide_card(d, d.approvals.get(card["id"]), "approve"))
    assert _noop_action.ran == [{"x": 1}] and d.approvals.get(card["id"])["status"] == "approved"
    assert self_levels.current()._state()["streaks"]["app_update"] == 1
    card2 = run(self_cards.propose(d, "app_update", "Update Y?", "b", "test_action", {"x": 2}, key="k2"))
    freeze_now(d)
    run(self_running.decide_card(d, d.approvals.get(card2["id"]), "approve"))
    assert len(_noop_action.ran) == 1 and d.approvals.get(card2["id"])["status"] == "pending"
    run(self_running.decide_card(d, d.approvals.get(card2["id"]), "reject"))
    assert d.approvals.get(card2["id"])["status"] == "rejected"
    assert self_levels.current()._state()["streaks"]["app_update"] == 0


def test_fingerprint_kinds_go_to_the_phone(env):
    d = FakeDaemon(env.tmp)
    self_cards.register("test_action", _noop_action)
    res = run(self_cards.propose(d, "daemon_code", "Change my brain?", "diff", "test_action", {}, key="code1"))
    assert res["kind"] == self_cards.CHANGE_TASK and not d.approvals.pending()
    assert d.board.get(res["id"])["status"] == "pending"                      # never automatic


# =====================================================================================  self-repair
def test_routine_fix_runs_checks_again_and_records(env):
    failed = chk("services", "fail", "restart_unit", ["NetworkManager.service", "orion-hud-watch.service (yours)",
                                                      "evil.service"])
    d = FakeDaemon(env.tmp, [failed])
    d.health.next = [[chk("services", "ok")]]
    rep = make_repair(d, env)
    out = run(rep.run_once())
    assert out[0]["ran"] and out[0]["fixed"]
    assert ["HELPER", "restart-unit", "NetworkManager.service"] in env.host.calls
    assert ["systemctl", "--user", "restart", "orion-hud-watch.service"] in env.host.calls
    assert not any("evil.service" in " ".join(c) for c in env.host.calls)    # not on the list: left alone
    c = changes.current().list()[0]
    assert c["kind"] == "repair" and c["result"] == "fixed" and c["tests"][0]["after"] == "ok"
    assert rep.state()["seen"][0]["check"] == "services"


def test_fix_that_makes_things_worse_is_blocked_and_carded(env):
    d = FakeDaemon(env.tmp, [chk("network", "warn", "check_network")])
    d.health.next = [[chk("network", "fail", "check_network")]]
    rep = make_repair(d, env)
    res = run(rep.run_once())[0]
    assert res["worse"] and not res["ok"]
    assert "network" in json.dumps(d.approvals.pending())
    assert not rep._auto_allowed("check_network", env.host.now())
    assert changes.current().list()[0]["result"] == "made worse"


def test_ask_level_and_tell_level(env):
    d = FakeDaemon(env.tmp, [chk("hud", "fail", "restart_hud")])
    rep = make_repair(d, env)
    self_levels.current().set_level("routine_fix", 1, "you")
    res = run(rep.run_once())[0]
    assert res["card"] and not any(c[0] in ("pgrep", "swaymsg") for c in env.host.calls)
    card = d.approvals.pending()[0]
    assert card["action"] == "repair_fix"
    d.health.next = [[chk("hud", "ok")]]
    run(self_running.decide_card(d, card, "approve"))
    assert any(c[0] in ("pgrep", "swaymsg") for c in env.host.calls)
    self_levels.current().set_level("routine_fix", 2, "you")
    d.health.last = {"checks": [chk("hud", "fail", "restart_hud")]}
    d.health.next = [[chk("hud", "ok")]]
    rep._last_status.clear()
    env.host.t += 7200
    run(rep.run_once())
    assert any("Orion fixed" in a[0][1] for a in env.alerts)


def test_retry_limits_then_card(env):
    d = FakeDaemon(env.tmp, [chk("ai", "fail", "setup_ai")])
    env.host.on(["systemctl", "cat", "ollama.service"], 0)
    rep = make_repair(d, env, ask_model=lambda *a: asyncio.sleep(0, result=None))
    run(rep.run_once())                                   # runs, still failing
    res = run(rep.run_once())[0]                          # too soon: a card instead
    assert res["card"] and [c for c in env.host.calls if c[:2] == ["HELPER", "restart-unit"]].__len__() == 1


def test_hints_are_cards_or_notes(env):
    d = FakeDaemon(env.tmp, [chk("phone", "warn", "pair_phone"), chk("battery", "warn", "plug_in")])
    rep = make_repair(d, env)
    out = run(rep.run_once())
    assert len(out) == 1 and out[0]["hint"]
    card = d.approvals.pending()[0]
    assert "Pair your phone" in card["original"]
    assert env.alerts[-1][0][0] == "system"                                   # a hint doesn't buzz like an approval
    run(self_running.decide_card(d, card, "reject"))
    run(rep.run_once())
    assert not d.approvals.pending()                                          # answered: not back for a day
    env.host.t += 25 * 3600
    run(rep.run_once())
    assert len(d.approvals.pending()) == 1


def test_free_space_only_touches_caches(env, monkeypatch):
    home = env.tmp / "home"
    (home / ".cache/orion/x").mkdir(parents=True)
    (home / ".cache/orion/x/big.bin").write_bytes(b"0" * 5000)
    (home / ".cache/thumbnails/large").mkdir(parents=True)
    (home / ".cache/thumbnails/large/a.png").write_bytes(b"0" * 100)
    (home / "Documents").mkdir()
    (home / "Documents/keep.txt").write_text("mine")
    data = env.tmp / "data"
    data.mkdir(exist_ok=True)
    old, new = data / "orion.log.1", data / "today.log"
    old.write_text("old"), new.write_text("new")
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    env.host.t = time.time()
    d = FakeDaemon(env.tmp, [chk("disk", "warn", "free_space")])
    d.health.next = [[chk("disk", "ok")]]
    rep = make_repair(d, env)
    res = run(rep.run_once())[0]
    assert res["fixed"] and not (home / ".cache/orion/x").exists() and (home / ".cache/orion").is_dir()
    assert (home / "Documents/keep.txt").exists() and new.exists() and not old.exists()
    assert ["HELPER", "vacuum-journal"] in env.host.calls


def test_unknown_problem_is_diagnosed_tried_in_a_box_and_only_proposed(env):
    secret = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    env.host.on(["journalctl", "--user"], 0, f"oct 16 sway[12]: crashed, token={secret} user me@example.com\n")
    asked = []

    async def model(daemon, system, user):
        asked.append(user)
        return json.dumps({"diagnosis": "The desktop config has a typo.", "fix_kind": "config",
                           "script": "sed -i 's/oops/ok/' ~/.config/sway/config", "explain": "Fixes the typo."})

    class Box:
        def available(self):
            return True

        def run(self, cmd, mounts=(), timeout=600):
            Box.ran = cmd
            return SimpleNamespace(ok=True, rc=0, output="fine")

    d = FakeDaemon(env.tmp, [chk("desktop", "fail", None)])
    rep = make_repair(d, env, ask_model=model, box_factory=Box)
    res = run(rep.run_once())[0]
    assert res["diagnosed"] and res["script"] and res["box"]["ok"] and "sed -i" in Box.ran
    assert secret not in asked[0] and "me@example.com" not in asked[0]
    card = d.approvals.pending()[0]
    assert card["change_kind"] == "unknown_fix" and card["action"] == "repair_review_script"
    assert not any(c[:2] == ["sed", "-i"] for c in env.host.calls)          # never applied by itself
    assert self_levels.decide("unknown_fix") == "ask"
    assert run(rep.run_once()) == []                                          # not asked again right away
    run(self_running.decide_card(d, card, "approve"))
    assert env.host.terminals and "Run this fix now?" in env.host.terminals[0]
    kinds = [c["kind"] for c in changes.current().list()]
    assert "diagnosis" in kinds and "repair_proposal" in kinds


def test_unknown_problem_without_a_model(env):
    d = FakeDaemon(env.tmp, [chk("desktop", "fail", None)])
    rep = make_repair(d, env, ask_model=lambda *a: asyncio.sleep(0, result=None))
    res = run(rep.run_once())[0]
    assert res["diagnosed"] and not res["script"]
    assert "No AI model" in d.approvals.pending()[0]["original"]


# =====================================================================================  keeping current
def test_model_update_same_new_and_broken(env):
    d = FakeDaemon(env.tmp)
    o = env.host.ollama
    o.next_digest = "d1"
    res = run(self_updates.update_model(d))
    assert res["ok"] and not res["changed"]
    o.next_digest = "d2"
    res = run(self_updates.update_model(d))
    assert res["changed"] and res["ok"] and o.models["llama3.1:orion-prev"] == "d1"
    c = changes.current().list()[0]
    assert c["undo"]["kind"] == "model_restore" and c["tests"][0]["passed"]
    run(changes.undo(d, c["id"]))
    assert o.models["llama3.1:8b"] == "d1"
    o.next_digest = "d3"
    o.broken.add("d3")
    res = run(self_updates.update_model(d))
    assert res["rolled_back"] and o.models["llama3.1:8b"] == "d1"
    assert changes.current().list()[0]["result"] == "rolled back"


def test_models_job_follows_the_level(env):
    d = FakeDaemon(env.tmp)
    self_levels.current().set_level("ai_model", 1, "you")
    res = run(self_updates.models_job(d))
    assert res["card"] and env.host.ollama.models["llama3.1:8b"] == "d1"
    self_levels.current().set_level("ai_model", 2, "you")
    run(self_updates.models_job(d))
    assert env.host.ollama.models["llama3.1:8b"] == "d2"
    assert any("AI model updated" in a[0][1] for a in env.alerts)


def test_firmware_is_listed_never_flashed(env):
    d = FakeDaemon(env.tmp)
    env.host.on(["fwupdmgr", "get-updates"], 0, json.dumps({"Devices": [
        {"Name": "System Firmware", "Version": "1.0", "Releases": [{"Version": "1.2"}]},
        {"Name": "Mouse", "Version": "3", "Releases": []}]}))
    res = run(self_updates.firmware_job(d))
    assert res["firmware"] == [{"device": "System Firmware", "from": "1.0", "to": "1.2"}]
    assert not any(c[:2] == ["fwupdmgr", "update"] or c[:2] == ["fwupdmgr", "install"] for c in env.host.calls)
    card = d.approvals.pending()[0]
    assert card["change_kind"] == "firmware"
    run(self_running.decide_card(d, card, "approve"))
    assert env.host.terminals == ["fwupdmgr update"]                          # you run it, in a terminal


def test_security_waiting_card_at_always_ask(env):
    d = FakeDaemon(env.tmp)
    assert run(self_updates.security_job(d)) == {"automatic": True}
    self_levels.current().set_level("security_update", 1, "you")
    env.host.on(["apt", "list"], 0, "Listing...\nopenssl/noble-security 3.0.13-0ubuntu3.5 amd64 [upgradable]\n"
                                    "vim/noble-updates 2:9.1 amd64 [upgradable]\n")
    res = run(self_updates.security_job(d))
    assert res["security_waiting"] == 1 and res["card"]


def test_after_update_checks_health_and_offers_the_snapshot(env, monkeypatch):
    import snapshots
    monkeypatch.setattr(snapshots.Snapshots, "status", lambda self: {"undo_to": "42"})
    d = FakeDaemon(env.tmp, [chk("network", "ok")])
    d.health.next = [[chk("network", "ok")]]
    assert run(self_updates.after_update(d))["ok"]
    assert changes.current().list()[0]["undo"]["snapshot"] == "42"
    d.health.next = [[chk("network", "fail")]]
    res = run(self_updates.after_update(d))
    assert not res["ok"] and res["new_problems"] == ["network"]
    assert d.approvals.pending()[0]["action"] == "snapshot_undo"
    assert any("broken something" in a[0][1] for a in env.alerts)


# =====================================================================================  update channels
def _slot_rules(host, latest="1.1.0", current="1.0.0", can=True, passed=True, image=True):
    host.on(["orion-slot", "latest"], 0, json.dumps({"channel": "beta", "current": current, "latest": latest}))
    host.on(["orion-selftest", "can-vm"], 0 if can else 1,
            json.dumps({"ok": can, "why": "" if can else "no KVM access"}))
    host.on(["orion-slot", "fetch-image"], 0 if image else 1,
            json.dumps({"ok": True, "image": "/x/orionos-1.1.0-standard.img"}) if image else "Signature check FAILED")

    def selftest(argv):
        vm = "--vm-image" in argv
        parts = [{"name": "Health checks", "ok": True}]
        if vm:
            parts.append({"name": "VM boot", "ok": passed})
        else:
            parts.append({"name": "Test run in a VM", "ok": True, "skipped": True})
        return json.dumps({"ok": passed, "parts": parts, "vm": {"ran": vm}})
    host.on(["orion-selftest", "run"], 0, selftest)


def test_channel_file(env):
    assert update_channels.read_channel() == "stable"
    (env.tmp / "channel").write_text("beta\n")
    assert update_channels.read_channel() == "beta"
    (env.tmp / "channel").write_text("nonsense")
    assert update_channels.read_channel() == "stable"
    assert "orion-self-helper channel mine" in update_channels.channel_command("mine")
    with pytest.raises(ValueError):
        update_channels.channel_command("rm -rf")


def test_new_version_tested_in_a_vm_then_offered(env):
    d = FakeDaemon(env.tmp)
    _slot_rules(env.host)
    res = run(update_channels.check_and_offer(d))
    assert res["offered"] and res["selftest"]["vm_ran"]
    run_call = next(c for c in env.host.calls if c[1:2] == ["run"])
    assert "--vm-image" in run_call and "/x/orionos-1.1.0-standard.img" in run_call
    assert ("update_ready", 1.0) in env.signals
    card = d.approvals.pending()[0]
    assert card["change_kind"] == "system_version" and "passed" in card["original"]
    run(self_running.decide_card(d, card, "approve"))
    assert "orion-slot update --expect 1.1.0" in env.host.terminals[0] and "orion-admin" in env.host.terminals[0]
    # the same version isn't tested twice
    n = len([c for c in env.host.calls if c[1:2] == ["run"]])
    run(update_channels.check_and_offer(d))
    assert len([c for c in env.host.calls if c[1:2] == ["run"]]) == n


def test_no_vm_says_so_plainly(env):
    d = FakeDaemon(env.tmp)
    _slot_rules(env.host, can=False)
    res = run(update_channels.check_and_offer(d))
    assert res["offered"] and not res["selftest"]["vm_ran"]
    run_call = next(c for c in env.host.calls if c[1:2] == ["run"])
    assert "--no-vm" in run_call and "no KVM access" in run_call
    assert "VM was skipped: no KVM access" in d.approvals.pending()[0]["original"]


def test_failing_version_is_held_back(env):
    d = FakeDaemon(env.tmp)
    _slot_rules(env.host, passed=False)
    res = run(update_channels.check_and_offer(d))
    assert not res["offered"] and not d.approvals.pending()
    assert ("update_ready", 0.0) in env.signals
    assert any("held back" in a[0][1] for a in env.alerts)


def test_up_to_date_clears_the_ring(env):
    d = FakeDaemon(env.tmp)
    _slot_rules(env.host, latest="1.0.0")
    res = run(update_channels.check_and_offer(d))
    assert not res["newer"] and ("update_ready", 0.0) in env.signals


def test_vm_switch_off(env):
    d = FakeDaemon(env.tmp)
    _slot_rules(env.host)
    self_updates.update_state(lambda s: s.__setitem__("vm_test", False))
    run(update_channels.check_and_offer(d))
    assert not any(c[1:2] == ["can-vm"] for c in env.host.calls)
    assert "you turned off" in next(c for c in env.host.calls if c[1:2] == ["run"])[-1]


# =====================================================================================  freeze stops everything
def test_freeze_blocks_every_automatic_path(env):
    d = FakeDaemon(env.tmp, [chk("services", "fail", "restart_unit", ["NetworkManager.service"]),
                             chk("desktop", "fail", None)])
    _slot_rules(env.host)
    rep = make_repair(d, env)
    freeze_now(d)
    before = list(env.host.calls)
    assert run(rep.run_once()) == [{"skipped": "frozen"}]
    assert run(rep.apply(self_repair.FIXES["restart_unit"], d.health.last["checks"][0], "x"))["ok"] is False
    assert run(rep.diagnose(chk("desktop", "fail"))) is None
    upd = self_updates.SelfUpdates(d)
    assert run(upd.tick()) == []
    for job in self_updates.JOBS:
        assert run(upd.run_job(job))["skipped"] == "frozen"
    assert run(update_channels.check_and_offer(d)) == {"skipped": "frozen"}
    assert not run(self_updates.update_model(d))["ok"]
    assert env.host.calls == before and not d.approvals.pending()
    assert env.host.ollama.models["llama3.1:8b"] == "d1"
    # a phone-approved change can't run while frozen either
    self_cards.install_executor(d)
    self_cards.register("test_action", _noop_action)
    with pytest.raises(ValueError):
        d.board.executors[self_cards.CHANGE_TASK]({"packet": {"details": {"action": "test_action", "args": {}}}})


# =====================================================================================  weekly review
def test_weekly_review_card_items_and_chat(env, monkeypatch):
    d = FakeDaemon(env.tmp)
    rep = make_repair(d, env)
    now = time.time()
    rep._update(lambda s: s["seen"].extend([{"ts": now - 100 * i, "check": "network", "label": "Network",
                                             "status": "fail", "reason": "down", "fix": "check_network"}
                                            for i in range(3)]))
    changes.record_change("Self-repair: Restart the network service (Network)", kind="repair", result="fixed")
    changes.record_change("Updated the AI model llama3.1:8b", kind="model_update", result="ok")
    lv = self_levels.current()
    lv.set_level("app_update", 1, "you")
    st = lv._state()
    st["streaks"]["app_update"] = 15
    lv._save(st)
    d.orchestrator = SimpleNamespace(meter=__import__("compute_meter").ComputeMeter())
    d.orchestrator.meter.record("claude-sonnet-4-5", "anthropic", 1_000_000, 0)
    wr = weekly_review.WeeklyReview(d, env.tmp / "data", rep, clock=lambda: now)
    r = run(weekly_review.make_review(d, wr.reviews, rep, now))
    assert r["broke"] == ["Network (3×)"] and "Restart the network service (Network)" in r["fixed"]
    assert any("llama3.1" in u for u in r["updated"]) and r["cost"]["cost"] == pytest.approx(3.0)
    types = sorted(i["type"] for i in r["items"])
    assert types == ["diagnose", "raise_level"]
    card = d.approvals.pending()[0]
    assert card["kind"] == weekly_review.CARD_KIND and "What broke: Network (3×)" in card["original"]
    assert env.alerts[-1][1].get("level") == "digest"
    raise_item = next(i for i in r["items"] if i["type"] == "raise_level")
    res = run(weekly_review.decide_item(d, wr.reviews, r["id"], raise_item["id"], True, rep))
    assert "phone" in res["message"] and lv.level("app_update") == 1         # still asks the phone
    diag = next(i for i in r["items"] if i["type"] == "diagnose")
    run(weekly_review.decide_item(d, wr.reviews, r["id"], diag["id"], False, rep))
    assert all(i["state"] != "open" for i in wr.reviews.get(r["id"])["items"])
    reply = wr.chat("How did this week go?")
    assert reply and "What Orion fixed" in reply
    assert wr.chat("what's the weather") is None


def test_review_is_due_on_sunday_once(env):
    d = FakeDaemon(env.tmp)
    sunday = datetime(2026, 10, 18, 11, 0).timestamp()
    saturday = datetime(2026, 10, 17, 11, 0).timestamp()
    early = datetime(2026, 10, 18, 7, 0).timestamp()
    wr = weekly_review.WeeklyReview(d, env.tmp / "data", None, clock=lambda: sunday)
    assert not weekly_review.due(wr.reviews, saturday) and not weekly_review.due(wr.reviews, early)
    assert run(wr.tick()) is not None
    assert run(wr.tick()) is None                                             # one card per week


# =====================================================================================  routes and MCP
def test_routes(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    d = FakeDaemon(env.tmp)
    self_running._parts.clear()
    import config
    monkey_dir = env.tmp / "data"
    app = FastAPI()
    orig = self_running._data_dir
    self_running._data_dir = lambda: monkey_dir
    try:
        app.include_router(self_running.make_router(lambda: d))
        c = TestClient(app)
        lv = c.get("/self/levels").json()
        assert any(r["kind"] == "app_update" and r["level"] == 2 for r in lv["levels"])
        assert c.get("/self/decide/app_update").json()["decision"] == "tell"
        assert c.post("/self/levels", json={"kind": "app_update", "level": 1}).json()["ok"]
        assert c.post("/self/levels", json={"kind": "nope", "level": 1}).status_code == 404
        assert c.post("/self/levels", json={"kind": "app_update", "level": 3}).json()["pending"]
        st = c.get("/self/status").json()
        assert "Permission levels" in st["words"] and st["channel"]["channel"] == "stable"
        assert c.post("/self/updates/settings", json={"vm_test": False}).json()["vm_test"] is False
        assert c.post("/self/updates/run", json={"job": "nope"}).status_code == 400
        assert "fixes" in c.get("/self/repair").json()
        assert "words" in c.get("/self/review").json()
    finally:
        self_running._data_dir = orig


def test_mcp_self_status_is_read_only(env):
    import mcp_tools
    t = mcp_tools.OrionTools()
    tool = t.tools["self_status"]
    assert tool.risk == "read"

    class Backend:
        async def call(self, method, path, body=None, timeout=None):
            assert (method, path) == ("GET", "/self/status")
            return 200, {"words": "Orion is running normally (not frozen)."}
    t.backend = Backend()
    text, err = run(t.call("self_status", {}))
    assert not err and "not frozen" in text


def test_gate_defaults_match_the_daemon(env):
    import importlib.util
    from importlib.machinery import SourceFileLoader
    path = Path(__file__).resolve().parents[3] / "os/safety/orion-self-gate"
    loader = SourceFileLoader("orion_self_gate", str(path))
    spec = importlib.util.spec_from_loader("orion_self_gate", loader)
    gate = importlib.util.module_from_spec(spec)
    loader.exec_module(gate)
    for k, kind in self_levels.KINDS.items():
        if k == "unknown_fix":
            continue
        assert gate.DEFAULTS[k] == kind.default, k
        assert gate.MAX.get(k, 1) == kind.max, k
