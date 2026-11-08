"""orion-update (os/lab/bin/orion-update) with fake commands: only signed, newer bundles are installed; a bundle that
doesn't come up healthy is rolled back; boot counting is armed with the snapshot from before (review B3, feature 7).
Nothing real is touched: the "release" is a folder, curl/minisign/provision are fakes."""
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
UPDATE = ROOT / "os/lab/bin/orion-update"


def fake(bindir: Path, name: str, body: str) -> str:
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    log = bindir.parent / "calls.txt"
    p.write_text(f"#!{sys.executable}\nimport json, os, shutil, sys\nargs = sys.argv[1:]\n"
                 f"open({str(log)!r}, 'a').write({name!r} + ' ' + ' '.join(args) + '\\n')\n" + body)
    p.chmod(0o755)
    return str(p)


def bundle(path: Path, version: str, inside: str = None, extra: dict = None):
    files = {"BUNDLE_VERSION": (inside or version) + "\n", "os/lab/provision.sh": "echo provision\n",
             "apps/daemon/main.py": "# daemon\n", **(extra or {})}
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))


@pytest.fixture()
def pc(tmp_path):
    b = tmp_path / "bin"
    rel = tmp_path / "release"
    rel.mkdir()
    src = tmp_path / "opt/orion-src"
    (src / "os/lab").mkdir(parents=True)
    (src / "BUNDLE_VERSION").write_text("1.2.0\n")
    (tmp_path / "release.pub").write_text("untrusted comment: test key\nRWQTEST\n")
    (tmp_path / "channel").write_text("stable\n")
    (tmp_path / "cmdline").write_text("BOOT_IMAGE=/vmlinuz root=UUID=x ro quiet\n")
    env = {**os.environ,
           "ORION_UPD_TEST_ROOT": "1", "ORION_UPD_BASE": "https://example.invalid/rel",
           "ORION_UPD_SRC": str(src), "ORION_UPD_PUB": str(tmp_path / "release.pub"),
           "ORION_CHANNEL_FILE": str(tmp_path / "channel"), "ORION_UPD_CMDLINE": str(tmp_path / "cmdline"),
           "ORION_UPD_STATE": str(tmp_path / "state"), "ORION_UPD_LOG": str(tmp_path / "update.log"),
           "ORION_UPD_TMP": str(tmp_path), "ORION_UPD_PROFILE_FILE": str(tmp_path / "profile"),
           "ORION_UPD_CURL": fake(b, "curl", f"""
url = args[-1]; name = url.rsplit('/', 1)[-1]
f = os.path.join({str(rel)!r}, name)
if not os.path.exists(f):
    sys.exit(22)
if '-o' in args:
    shutil.copy(f, args[args.index('-o') + 1])
else:
    sys.stdout.write(open(f).read())
"""),
           "ORION_UPD_MINISIGN": fake(b, "minisign", """
if os.environ.get('FAKE_SIG', 'ok') != 'ok':
    print('Signature verification failed'); sys.exit(1)
print('Signature and comment signature verified')
print('Trusted comment: orion-bundle ' + os.environ.get('FAKE_TRUSTED', '1.3.0'))
"""),
           "ORION_UPD_PROVISION": fake(b, "provision", f"""
print('provisioning', open(os.path.join({str(src)!r}, 'BUNDLE_VERSION')).read().strip())
open(os.path.join({str(tmp_path)!r}, 'provisioned.txt'), 'a').write(
    open(os.path.join({str(src)!r}, 'BUNDLE_VERSION')).read())
sys.exit(int(os.environ.get('FAKE_PROV_RC', '0')))
"""),
           "ORION_UPD_RESTART": fake(b, "restart", ""),
           "ORION_UPD_HEALTHY": fake(b, "orion-healthy", "sys.exit(0 if os.environ.get('FAKE_HEALTHY', '1') == '1' else 1)\n"),
           "ORION_UPD_SNAPSHOT": fake(b, "orion-snapshot", "print(json.dumps({'ok': True, 'snapshot': {'id': '12'}}))\n"),
           "ORION_UPD_BOOTCOUNT": fake(b, "orion-bootcount", "print('Boot counting is on.') if args[:1] == ['arm'] else None\n"),
           "ORION_UPD_FROZEN": fake(b, "orion-frozen", "sys.exit(0 if os.environ.get('FAKE_FROZEN') == '1' else 1)\n"),
           "ORION_UPD_BLKID": fake(b, "blkid", "sys.exit(2)\n")}

    class P:
        tmp, release, src_dir = tmp_path, rel, src

        @staticmethod
        def run(*args, **extra):
            return subprocess.run([sys.executable, str(UPDATE), *args], capture_output=True, text=True,
                                  env={**env, **extra}, timeout=60)

        @staticmethod
        def publish(version, inside=None, extra=None):
            bundle(rel / "orion-bundle.tar.gz", version, inside, extra)
            (rel / "orion-bundle.tar.gz.minisig").write_text("sig\n")
            (rel / "orion-bundle.version").write_text(version + "\n")

        @staticmethod
        def calls():
            f = tmp_path / "calls.txt"
            return f.read_text().splitlines() if f.exists() else []

        @staticmethod
        def version():
            return (src / "BUNDLE_VERSION").read_text().strip()
    return P


def test_signed_newer_bundle_is_installed_and_boot_counting_armed(pc):
    pc.publish("1.3.0")
    r = pc.run()
    assert r.returncode == 0, r.stderr
    assert pc.version() == "1.3.0"
    assert (pc.tmp / "opt/orion-src.prev/BUNDLE_VERSION").read_text().strip() == "1.2.0"
    c = pc.calls()
    assert any(l.startswith("minisign -V -p") for l in c)
    snap = next(i for i, l in enumerate(c) if l.startswith("orion-snapshot take --kind pre-update"))
    prov = next(i for i, l in enumerate(c) if l.startswith("provision"))
    assert snap < prov                                         # the snapshot comes first
    assert "orion-bootcount arm --snapshot 12 --version 1.3.0" in c
    assert "Updated Orion to 1.3.0" in r.stdout and "Boot counting is on." in r.stdout
    assert json.loads((pc.tmp / "state/last.json").read_text())["from"] == "1.2.0"


def test_up_to_date_downloads_nothing(pc):
    pc.publish("1.2.0", )
    r = pc.run()
    assert r.returncode == 0 and "Up to date (1.2.0)" in r.stdout
    assert not any(l.startswith("provision") for l in pc.calls())


def test_bad_signature_changes_nothing(pc):
    pc.publish("1.3.0")
    r = pc.run(FAKE_SIG="bad")
    assert r.returncode == 1 and "Signature check FAILED" in r.stderr
    assert pc.version() == "1.2.0" and not any(l.startswith("provision") for l in pc.calls())


def test_no_release_key_no_update(pc):
    pc.publish("1.3.0")
    (pc.tmp / "release.pub").unlink()
    r = pc.run()
    assert r.returncode == 1 and "no release key" in r.stderr and pc.version() == "1.2.0"


def test_old_signed_bundle_cant_pose_as_new(pc):
    pc.publish("1.3.0")                                       # the .version file lies; the signature says 1.1.0
    r = pc.run(FAKE_TRUSTED="1.1.0")
    assert r.returncode == 1 and "isn't newer than the installed 1.2.0" in r.stderr
    assert pc.version() == "1.2.0"
    pc.publish("1.1.0")
    r = pc.run("--allow-downgrade", FAKE_TRUSTED="1.1.0")
    assert r.returncode == 0, r.stderr
    assert pc.version() == "1.1.0"


def test_signature_and_contents_must_agree(pc):
    pc.publish("1.3.0", inside="1.9.9")
    r = pc.run()
    assert r.returncode == 1 and "says 1.9.9 inside" in r.stderr and pc.version() == "1.2.0"


def test_paths_outside_the_tree_are_refused(pc):
    pc.publish("1.3.0", extra={"../../etc/evil": "x\n"})
    r = pc.run()
    assert r.returncode == 1 and "shouldn't" in r.stderr and pc.version() == "1.2.0"
    assert not (pc.tmp / "etc/evil").exists()


def test_unhealthy_update_is_rolled_back(pc):
    pc.publish("1.3.0")
    r = pc.run(FAKE_HEALTHY="0")
    assert r.returncode == 1 and "Update not kept" in r.stderr and "orion-snapshot restore 12" in r.stderr
    assert pc.version() == "1.2.0"
    assert (pc.tmp / "provisioned.txt").read_text().split() == ["1.3.0", "1.2.0"]
    assert "orion-bootcount disarm" in pc.calls()
    assert not any(l.startswith("orion-bootcount arm") for l in pc.calls())


def test_failed_setup_is_rolled_back(pc):
    pc.publish("1.3.0")
    r = pc.run(FAKE_PROV_RC="1")
    assert r.returncode == 1 and "the setup step failed" in r.stderr


def test_expect_and_frozen(pc):
    pc.publish("1.3.0")
    r = pc.run("--expect", "1.4.0")
    assert r.returncode == 1 and "not the tested 1.4.0" in r.stderr
    r = pc.run(FAKE_FROZEN="1")
    assert r.returncode == 1 and "frozen" in r.stdout
    assert pc.version() == "1.2.0"


def test_check_json_and_stick(pc):
    pc.publish("1.3.0")
    r = pc.run("check", "--json")
    d = json.loads(r.stdout)
    assert d == {"channel": "stable", "current": "1.2.0", "latest": "1.3.0", "slot": False, "signed": True}
    (pc.tmp / "cmdline").write_text("boot=casper orion.slot=a\n")
    r = pc.run()
    assert r.returncode == 0 and "orion-slot update" in r.stdout


def test_git_only_behind_a_loud_developer_flag(pc):
    r = pc.run("--dev-git-unsigned")
    assert "WARNING: developer path" in r.stderr and "WITHOUT any signature check" in r.stderr
    assert r.returncode == 1 and "isn't a git checkout" in r.stderr
    text = UPDATE.read_text() + (ROOT / "os/baremetal/install-orion.sh").read_text()
    assert "reset --hard" not in text and '"reset"' not in text
