"""Privacy basics: the phone port (H11), phone approvals checked by root and the install gate (H9), polkit files,
first password and the idle lock (B7).   python -m pytest -q os/privacy/tests"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import xml.dom.minidom
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PRIV = HERE.parent
OS = PRIV.parent
REPO = OS.parent


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


port = load(PRIV / "orion-phone-port", "orion_phone_port")

# ------------------------------------------------------------------ phone port (H11)
STATUS = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 9121/tcp                   ALLOW IN    10.77.0.11                 # boardroom verdicts
[ 2] 9121/tcp                   ALLOW IN    192.168.0.0/16             # orion-phone-pairing
[ 3] 9121/tcp on wlp2s0         ALLOW IN    Anywhere                   # orion-phone-home
[ 4] 9121/tcp                   ALLOW IN    10.0.0.0/8                 # orion-phone-pairing
[ 5] 9121/tcp (v6) on wlp2s0    ALLOW IN    Anywhere (v6)              # orion-phone-home
"""


def test_rule_numbers_highest_first():
    assert port.numbered_with(STATUS, port.PAIR) == [4, 2]
    assert port.numbered_with(STATUS, port.HOME) == [5, 3]
    assert port.numbered_with(STATUS, "boardroom") == []


def test_home_devices():
    assert port.home_devices("wlp2s0:home\neth0:\nlo:home\ndocker0:public\nbad dev;rm:home\n") == ["wlp2s0"]
    assert port.home_devices("") == []


FAKE_UFW = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_UFW_STATE"]
rules = json.load(open(path)) if os.path.exists(path) else ["9121/tcp ALLOW IN 10.77.0.11 # boardroom verdicts"]
a = sys.argv[1:]
if a[:2] == ["status", "numbered"]:
    print("Status: active\n")
    for i, r in enumerate(rules, 1):
        print(f"[{i:2}] {r}")
elif a[:2] == ["--force", "delete"]:
    del rules[int(a[2]) - 1]
elif a[0] == "allow":
    c = a[a.index("comment") + 1]
    where = f"on {a[a.index('on') + 1]}" if "on" in a else f"from {a[a.index('from') + 1]}"
    rules.append(f"9121/tcp {where} ALLOW IN # {c}")
json.dump(rules, open(path, "w"))
'''
FAKE_NMCLI = r'''#!/bin/sh
case "$*" in
  "-t -f UUID,DEVICE connection show --active") printf '1111-2222-3333:wlp2s0\n4444-5555-6666:eth0\n' ;;
  *"uuid 1111-2222-3333") echo "${FAKE_ZONE-home}" ;;
  *) echo "" ;;
esac
'''


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "ufw").write_text(FAKE_UFW)
    (b / "nmcli").write_text(FAKE_NMCLI)
    (b / "systemd-run").write_text(f"#!/bin/sh\necho \"$@\" > {tmp_path}/timer\n")
    for f in b.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, ORION_UFW=str(b / "ufw"), ORION_NMCLI=str(b / "nmcli"),
               ORION_SYSTEMD_RUN=str(b / "systemd-run"), FAKE_UFW_STATE=str(tmp_path / "rules.json"),
               ORION_PHONE_PORT_TEST="1")

    def run(*args, **extra):
        r = subprocess.run([sys.executable, str(PRIV / "orion-phone-port"), *args], env={**env, **extra},
                           capture_output=True, text=True)
        return r

    def rules():
        p = tmp_path / "rules.json"
        return json.loads(p.read_text()) if p.exists() else []
    return run, rules, tmp_path


def test_pairing_opens_for_ten_minutes_then_closes(fakes):
    run, rules, tmp = fakes
    r = run("open", "600")
    assert r.returncode == 0 and "10 minutes" in r.stdout
    pair = [x for x in rules() if "orion-phone-pairing" in x]
    assert len(pair) == 3 and all("from" in x for x in pair)
    assert "--on-active=600s" in (tmp / "timer").read_text() and "close" in (tmp / "timer").read_text()
    run("open", "5000")                       # clamped, and never duplicated
    assert len([x for x in rules() if "orion-phone-pairing" in x]) == 3
    assert "--on-active=900s" in (tmp / "timer").read_text()
    assert run("close").returncode == 0
    assert not [x for x in rules() if "orion-phone-pairing" in x]
    assert any("boardroom" in x for x in rules())   # other rules untouched


def test_home_network_sync(fakes):
    run, rules, _ = fakes
    r = run("sync")
    assert r.returncode == 0 and "wlp2s0" in r.stdout
    home = [x for x in rules() if "orion-phone-home" in x]
    assert home == ["9121/tcp on wlp2s0 ALLOW IN # orion-phone-home"]
    r = run("sync", FAKE_ZONE="")              # the network is no longer marked Home: closed
    assert "isn't marked as Home" in r.stdout
    assert not [x for x in rules() if "orion-phone-home" in x]


def test_needs_root_without_test_flag():
    r = subprocess.run([sys.executable, str(PRIV / "orion-phone-port"), "open"], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != "ORION_PHONE_PORT_TEST"})
    if os.geteuid() != 0:
        assert r.returncode == 1


def test_provision_no_longer_opens_private_networks():
    prov = (OS / "lab/provision.sh").read_text()
    assert "192.168.0.0/16 10.0.0.0/8 172.16.0.0/12" not in prov
    assert "orion-phone-port sync" in prov


def test_dispatcher_hook_parses():
    assert subprocess.run(["sh", "-n", str(PRIV / "60-orion-phone-port")]).returncode == 0


# ------------------------------------------------------------------ phone approvals checked by root (H9)
check_mod = load(OS / "safety/orion-approval-check", "orion_approval_check")


def _keypair():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    der = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return k, der, (lambda msg: base64.b64encode(k.sign(msg, ec.ECDSA(hashes.SHA256()))).decode())


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    keys = tmp_path / "keys"
    keys.mkdir()
    monkeypatch.setattr(check_mod, "KEYS", keys)
    monkeypatch.setattr(check_mod, "USED", tmp_path / "used.json")
    monkeypatch.setenv("ORION_APPROVAL_TEST", "1")
    _k, der, sign = _keypair()
    (keys / "phone-abc123").with_suffix(".pub").write_text(base64.b64encode(der).decode())

    def token(path="/phone/unfreeze", body=b"", ts=None, device="phone-abc123", signer=sign):
        ts = str(int((ts or time.time()) * 1000))
        sig = signer(check_mod.signing_message("POST", path, ts, body))
        return {"device": device, "method": "POST", "path": path, "ts": ts, "sig": sig,
                "body_b64": base64.b64encode(body).decode()}
    return token


def test_valid_phone_approval_works_once(pinned):
    t = pinned()
    assert check_mod.check("unfreeze", t) == ""
    assert check_mod.check("unfreeze", t) == "already used"


def test_approval_refusals(pinned):
    assert "too old" in check_mod.check("unfreeze", pinned(ts=time.time() - 600))
    assert check_mod.check("unfreeze", pinned(path="/phone/chat")) == "not an approval of the right kind"
    assert check_mod.check("unfreeze", pinned(device="phone-unknown")).startswith("this phone isn't pinned")
    _k, _d, other = _keypair()
    assert check_mod.check("unfreeze", pinned(signer=other)) == "bad signature"
    t = pinned(path="/phone/verdict", body=json.dumps({"id": "d1", "verdict": "reject"}).encode())
    assert check_mod.check("unfreeze", t) == "the phone didn't approve"
    t = pinned(path="/phone/verdict", body=json.dumps({"id": "d1", "verdict": "approve"}).encode())
    assert check_mod.check("unfreeze", t) == ""
    assert check_mod.check("install", pinned(path="/phone/unfreeze")) == "not an approval of the right kind"
    assert check_mod.check("nonsense", pinned()) == "unknown purpose"


def test_signing_message_matches_the_daemon():
    sys.path.insert(0, str(REPO / "apps/daemon"))
    try:
        import boardroom
    except Exception:
        pytest.skip("daemon modules not importable here")
    assert boardroom.SIG_DOMAIN == check_mod.SIG_DOMAIN
    assert boardroom.signing_message("post", "/p", "1", b"x") == check_mod.signing_message("post", "/p", "1", b"x")


def test_freeze_helper_off_with_approval(tmp_path):
    env = dict(os.environ, ORION_FREEZE_TEST="1", ORION_FREEZE_DROPIN=str(tmp_path / "dropin"),
               ORION_FREEZE_MARK=str(tmp_path / "frozen"), ORION_PHONE_KEYS=str(tmp_path / "keys"))
    h = str(OS / "safety/orion-freeze-helper")
    assert subprocess.run(["sh", h, "on"], env=env, capture_output=True).returncode == 0
    ok = tmp_path / "ok"
    ok.write_text("#!/bin/sh\nexit 0\n")
    no = tmp_path / "no"
    no.write_text("#!/bin/sh\nexit 1\n")
    ok.chmod(0o755)
    no.chmod(0o755)
    r = subprocess.run(["sh", h, "off", "--approval", "/tmp/x.json"], env={**env, "ORION_APPROVAL_CHECK": str(no)},
                       capture_output=True, text=True)
    assert r.returncode == 4 and (tmp_path / "frozen").exists()
    r = subprocess.run(["sh", h, "off", "--approval", "/tmp/x.json"], env={**env, "ORION_APPROVAL_CHECK": str(ok)},
                       capture_output=True, text=True)
    assert r.returncode == 0 and not (tmp_path / "frozen").exists()
    key = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE" + "B" * 88
    assert subprocess.run(["sh", h, "pin-phone", "phone-abc123", key], env=env).returncode == 0
    assert (tmp_path / "keys/phone-abc123.pub").read_text().strip() == key
    assert subprocess.run(["sh", h, "pin-phone", "../../etc", key], env=env, capture_output=True).returncode == 2
    assert subprocess.run(["sh", h, "pin-phone", "phone-abc123", "bad key!"], env=env,
                          capture_output=True).returncode == 2


def test_install_gate(tmp_path):
    gate = tmp_path / "gate"
    env = dict(os.environ, ORION_INSTALL_GATE=str(gate))
    g = str(OS / "getapps/orion-install-gate")
    assert subprocess.run(["sh", g, "check"], env=env, capture_output=True).returncode == 1
    no = tmp_path / "no"
    no.write_text("#!/bin/sh\nexit 1\n")
    no.chmod(0o755)
    r = subprocess.run(["sh", g, "open", "--approval", "/x"], env={**env, "ORION_APPROVAL_CHECK": str(no)},
                       capture_output=True)
    assert r.returncode == 4 and not gate.exists()
    assert subprocess.run(["sh", g, "open"], env=env, capture_output=True).returncode == 0
    r = subprocess.run(["sh", g, "check"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "open"
    gate.write_text(str(int(time.time()) - 5))     # expired
    assert subprocess.run(["sh", g, "check"], env=env, capture_output=True).returncode == 1
    assert subprocess.run(["sh", g, "close"], env=env, capture_output=True).returncode == 0


# ------------------------------------------------------------------ polkit files
def test_polkit_rules_with_node():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", str(HERE / "test_polkit_rules.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run(["node", str(OS / "hardware/tests/test_polkit_rule.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("policy", ["privacy/ai.orion.privacy.policy", "getapps/ai.orion.getapps.policy",
                                    "safety/ai.orion.safety.policy"])
def test_policy_files_are_valid(policy):
    doc = xml.dom.minidom.parse(str(OS / policy))
    for a in doc.getElementsByTagName("action"):
        path = [n for n in a.getElementsByTagName("annotate")
                if n.getAttribute("key") == "org.freedesktop.policykit.exec.path"]
        assert path and path[0].firstChild.data.startswith("/")
        assert a.getElementsByTagName("allow_active")[0].firstChild.data in (
            "no", "yes", "auth_self", "auth_admin", "auth_self_keep", "auth_admin_keep")


def test_hardware_rule_comment_corrected():
    text = (OS / "hardware/50-orion-hardware.rules").read_text()
    assert "never qualify" not in text and "does NOT keep Orion's daemon" in text


# ------------------------------------------------------------------ first password, lock, idle (B7)
@pytest.mark.parametrize("script", ["privacy/orion-first-password", "lab/bin/orion-lock", "lab/bin/orion-idle",
                                    "lab/bin/orion-firstrun", "lab/bin/orion-statusbar",
                                    "lab/bin/orion-session-start", "image/os-rescue.sh"])
def test_scripts_parse(script):
    shell = "bash" if script.endswith((".sh", "orion-firstrun")) else "sh"
    assert subprocess.run([shell, "-n", str(OS / script)]).returncode == 0


def test_first_password_refuses_when_one_exists():
    t = (PRIV / "orion-first-password").read_text()
    assert 'PKEXEC_UID' in t and '= P ]' in t and "exit 3" in t
    fr = (OS / "lab/bin/orion-firstrun").read_text()
    assert "orion-first-password" in fr and '"$PROFILE" = vm' in fr     # skippable only on the VM lab
    assert "ORION_CAPTURE_ENABLED=0" in fr and "ORION_CAPTURE_ENABLED=1" in fr


def _fake_bin(tmp_path, names, body="#!/bin/sh\nexit 0\n"):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for n in names:
        (b / n).write_text(body)
        (b / n).chmod(0o755)
    return b


def test_idle_builds_lock_and_screen_off(tmp_path):
    b = _fake_bin(tmp_path, [])
    (b / "swayidle").write_text(f"#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done > {tmp_path}/args\n")
    (b / "swayidle").chmod(0o755)
    env = {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    subprocess.run(["sh", str(OS / "lab/bin/orion-idle")], env=env, check=True)
    args = (tmp_path / "args").read_text().splitlines()
    assert "before-sleep" in args and args[args.index("before-sleep") + 1] == "orion-lock --now"
    assert "300" in args and "600" in args and "orion-lock --idle" in args
    (tmp_path / "cfg/orion").mkdir(parents=True)
    (tmp_path / "cfg/orion/idle.conf").write_text("LOCK_AFTER=0\nSCREEN_OFF_AFTER=15\n")
    subprocess.run(["sh", str(OS / "lab/bin/orion-idle")], env=env, check=True)
    args = (tmp_path / "args").read_text().splitlines()
    assert "orion-lock --idle" not in args and "900" in args and "before-sleep" in args


def test_lock_needs_a_password_and_marks_the_screen(tmp_path):
    b = _fake_bin(tmp_path, ["notify-send", "orion-ctl"])
    (b / "passwd").write_text("#!/bin/sh\necho \"x ${FAKE_PW:-L} 2024\"\n")
    (b / "passwd").chmod(0o755)
    (b / "pgrep").write_text("#!/bin/sh\nexit 1\n")
    (b / "pgrep").chmod(0o755)
    (b / "swaylock").write_text(f"#!/bin/sh\nls {tmp_path}/run/orion-locked > {tmp_path}/during 2>&1\n")
    (b / "swaylock").chmod(0o755)
    (tmp_path / "run").mkdir()
    env = {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(tmp_path / "run")}
    r = subprocess.run(["sh", str(OS / "lab/bin/orion-lock")], env=env)
    assert r.returncode == 1 and not (tmp_path / "during").exists()          # no password: no lock
    r = subprocess.run(["sh", str(OS / "lab/bin/orion-lock")], env={**env, "FAKE_PW": "P"})
    assert r.returncode == 0 and "orion-locked" in (tmp_path / "during").read_text()
    assert not (tmp_path / "run/orion-locked").exists()                       # gone after the unlock


def test_sway_config_starts_only_the_target():
    conf = (OS / "lab/sway-config").read_text()
    execs = [ln for ln in conf.splitlines() if ln.startswith("exec ")]
    assert execs == ["exec orion-session-start"]
    for gone in ("mako", "orion-hud", "orion-clipboard watch", "orion-power watch", "swayidle", "orion-boot-sound",
                 "orion-access session", "orion-nightlight start", "kanshi"):
        assert not [ln for ln in conf.splitlines() if ln.startswith("exec") and gone in ln], gone
    assert "bindsym Ctrl+Alt+Delete exec orion-rescue" in conf


def test_user_units():
    units = OS / "lab/systemd"
    target = (units / "sway-session.target").read_text()
    for name in ("orion-mako", "orion-hud", "orion-idle", "orion-clipboard", "orion-nightlight", "orion-boot-sound",
                 "orion-access", "orion-power", "orion-wallpaper", "orion-firstrun"):
        assert f"{name}.service" in target
        u = (units / f"{name}.service").read_text()
        assert "PartOf=sway-session.target" in u
        if "Restart=on-failure" in u:
            assert "StartLimitBurst=" in u and "StartLimitIntervalSec=" in u
    daemon = (OS / "lab/orion-daemon.service").read_text()
    assert "StartLimitBurst=5" in daemon and "OnFailure=orion-daemon-gave-up.service" in daemon
    assert "KillMode=process" in (units / "orion-idle.service").read_text()


def test_user_units_verify(tmp_path):
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze not available")
    for f in list((OS / "lab/systemd").iterdir()) + [OS / "lab/orion-daemon.service"]:
        (tmp_path / f.name).write_text(f.read_text())
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    files = sorted(str(p) for p in tmp_path.iterdir() if p.suffix in (".service", ".target"))
    r = subprocess.run(["systemd-analyze", "--user", "verify", "--man=no", *files], capture_output=True, text=True,
                       env=dict(os.environ, XDG_RUNTIME_DIR=str(run)))
    noise = ("is not executable", "system bus", "foot-server", "sockets.target", "ordering cycle",
             "Found dependency", "RuntimeDirectory", "Failed to initialize manager")
    bad = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip() and not any(n in ln for n in noise)]
    if any("Failed to initialize manager" in ln for ln in (r.stdout + r.stderr).splitlines()):
        pytest.skip("no user manager possible here")
    assert not bad, bad
