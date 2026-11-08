"""Updates don't wipe your changes (review H10): orion-owned (greetd config), orion-sway-config (your Sway file stays
yours) and orion-ufw-forget (only Orion's firewall rules are refreshed). Fakes and temporary folders only."""
import getpass
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OWNED = ROOT / "os/lab/orion-owned"
SWAY = ROOT / "os/lab/orion-sway-config"
FORGET = ROOT / "os/common/orion-ufw-forget"


def run(argv, **env):
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env={**os.environ, **env}, timeout=30)


def test_greetd_config_is_only_rewritten_while_its_orions(tmp_path):
    f, tpl = tmp_path / "config.toml", tmp_path / "greetd-auto.toml"
    tpl.write_text("[default_session]\ncommand = \"orion\"\n")
    e = {"ORION_OWNED_DIR": str(tmp_path / "owned")}
    assert run(["sh", OWNED, "may-write", f], **e).returncode == 0               # not there yet
    f.write_text("ubuntu default\n")
    assert run(["sh", OWNED, "may-write", f], **e).returncode == 0               # never recorded: first run
    f.write_text("written by orion\n")
    run(["sh", OWNED, "record", f], **e)
    assert run(["sh", OWNED, "may-write", f, tpl], **e).returncode == 0          # unchanged since
    f.write_text(tpl.read_text())
    assert run(["sh", OWNED, "may-write", f, tpl], **e).returncode == 0          # one of Orion's templates
    f.write_text("[default_session]\ncommand = \"my-own-greeter\"\n")
    assert run(["sh", OWNED, "may-write", f, tpl], **e).returncode == 1          # yours: left alone


def test_sway_config_is_yours_after_the_first_run(tmp_path):
    home, sysconf = tmp_path / "home", tmp_path / "etc/orion/sway/orion.conf"
    me = getpass.getuser()
    e = {"ORION_SWAY_SYS": str(sysconf)}
    r = run(["bash", SWAY, home, me, "baremetal"], **e)
    assert r.returncode == 0, r.stderr
    cfg = home / ".config/sway/config"
    assert f"include {sysconf}" in cfg.read_text().splitlines()
    assert sysconf.read_text().startswith((ROOT / "os/lab/sway-config").read_text().splitlines()[0])
    # first-run comes from orion-firstrun.service (sway-session.target), never a second time from the Sway config
    assert "foot --title=orion-tool orion-firstrun" not in sysconf.read_text()
    with cfg.open("a") as fh:
        fh.write("input * xkb_layout de\nbindsym Mod4+x exec mine\n")
    mine = cfg.read_text()
    run(["bash", SWAY, home, me, "baremetal"], **e)
    run(["bash", SWAY, home, me, "baremetal"], **e)
    assert cfg.read_text() == mine                                               # never overwritten again
    r = run(["bash", SWAY, home, me, "vm"], **e)
    assert "foot --title=orion-tool orion-firstrun" not in sysconf.read_text()


def test_old_full_copy_is_moved_aside_once_and_foreign_files_are_left(tmp_path):
    home, sysconf = tmp_path / "home", tmp_path / "orion.conf"
    me = getpass.getuser()
    cfg = home / ".config/sway/config"
    cfg.parent.mkdir(parents=True)
    old = (ROOT / "os/lab/sway-config").read_text() + "\n# an old edit\n"
    cfg.write_text(old)
    r = run(["bash", SWAY, home, me, "vm"], ORION_SWAY_SYS=str(sysconf))
    assert r.returncode == 0 and "moved" in r.stdout
    backups = list(cfg.parent.glob("config.orion-before-*"))
    assert len(backups) == 1 and backups[0].read_text() == old
    assert f"include {sysconf}" in cfg.read_text()
    cfg.write_text("# a config I wrote from scratch\nset $mod Mod1\n")
    r = run(["bash", SWAY, home, me, "vm"], ORION_SWAY_SYS=str(sysconf))
    assert "left exactly as it is" in r.stdout and cfg.read_text().startswith("# a config I wrote")


def test_firewall_refresh_touches_only_orions_rules(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    log = tmp_path / "ufw.log"
    (b / "ufw").write_text(f"""#!{sys.executable}
import sys
args = sys.argv[1:]
open({str(log)!r}, 'a').write(' '.join(args) + '\\n')
if args[:2] == ['show', 'added']:
    print("Added user rules (see 'ufw status' for running firewall):")
    print("ufw allow from 10.77.0.11 to any port 9121 proto tcp comment 'boardroom verdicts'")
    print("ufw allow in on tailscale0 to any port 22 proto tcp comment 'SSH over Tailscale only'")
    print("ufw allow from 192.168.0.0/16 to any port 9121 proto tcp comment 'Orion Mobile on your home network (signed requests only)'")
    print("ufw allow 25565/tcp comment 'my minecraft server'")
    print("ufw allow 8080/tcp")
""")
    (b / "ufw").chmod(0o755)
    r = run(["bash", FORGET], PATH=f"{b}:{os.environ['PATH']}")
    assert r.returncode == 0 and "refreshed 3" in r.stdout
    deletes = [l for l in log.read_text().splitlines() if l.startswith("--force delete")]
    assert deletes == ["--force delete allow from 10.77.0.11 to any port 9121 proto tcp",
                       "--force delete allow in on tailscale0 to any port 22 proto tcp",
                       "--force delete allow from 192.168.0.0/16 to any port 9121 proto tcp"]
    assert "reset" not in log.read_text()


def test_provision_no_longer_resets_or_overwrites():
    text = (ROOT / "os/lab/provision.sh").read_text()
    lines = [l for l in text.splitlines() if "ufw --force reset" in l]
    assert lines and all("ufw-managed" in l for l in lines)                       # only the very first time
    assert '"$OS/lab/sway-config" "$H/.config/sway/config"' not in text
