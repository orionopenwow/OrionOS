"""Clean Box: throwaway Ubuntu containers, tested with a stand-in podman that records what it was asked."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

import clean_box as cbx
from mcp_tools import McpConfig, OrionTools

FAKE_PODMAN = r'''
import json, os, subprocess, sys, time
log = os.environ["FAKE_PODMAN_LOG"]
state = os.environ["FAKE_PODMAN_STATE"]
open(log, "a").write(json.dumps(sys.argv[1:]) + "\n")
boxes = json.load(open(state)) if os.path.exists(state) else {}
a = sys.argv[1:]
def save():
    json.dump(boxes, open(state, "w"))
if a[0] == "run":
    name = a[a.index("--name") + 1]
    labels = dict(a[i + 1].split("=", 1) for i, x in enumerate(a) if x == "--label")
    if "-d" in a:
        boxes[name] = {"Names": [name], "State": "running", "Labels": labels}
        save(); print("cid"); sys.exit(0)
    cmd = a[-1]
    if "sleep 99" in cmd:
        time.sleep(30)
    if "sha256sum" in cmd:     # emulate: hash the mounted folder for real
        vol = next(a[i + 1] for i, x in enumerate(a) if x == "-v")
        host = vol.split(":")[0]
        out = subprocess.run("cd %s && find . -type f -print0 | xargs -0 -r sha256sum" % host, shell=True,
                             capture_output=True, text=True).stdout
        if os.environ.get("FAKE_PODMAN_CORRUPT"):
            out = out.replace(out[:4], "0000", 1)
        sys.stdout.write(out); sys.exit(0)
    print("ran: " + cmd); sys.exit(3 if "exit 3" in cmd else 0)
if a[0] == "ps":
    print(json.dumps(list(boxes.values()))); sys.exit(0)
if a[0] == "rm":
    name = a[-1]; boxes.pop(name, None); save(); sys.exit(0)
if a[0] == "inspect":
    sys.exit(0 if a[-1] in boxes else 1)
if a[0] == "exec":
    print("exec ok"); sys.exit(0)
sys.exit(0)
'''


@pytest.fixture()
def podman(tmp_path, monkeypatch):
    exe = tmp_path / "podman"
    exe.write_text(f"#!{sys.executable}\n" + FAKE_PODMAN)
    exe.chmod(0o755)
    log = tmp_path / "podman.log"
    monkeypatch.setenv("ORION_PODMAN", str(exe))
    monkeypatch.setenv("FAKE_PODMAN_LOG", str(log))
    monkeypatch.setenv("FAKE_PODMAN_STATE", str(tmp_path / "boxes.json"))
    return {"exe": exe, "log": log, "tmp": tmp_path}


def calls(podman):
    return [json.loads(x) for x in podman["log"].read_text().splitlines()] if podman["log"].exists() else []


def test_run_is_isolated_and_removed(podman):
    folder = podman["tmp"] / "proj"
    folder.mkdir()
    r = cbx.CleanBox().run("echo hi", mounts=[cbx.Mount(str(folder), "/work")], timeout=30)
    assert r.ok and "ran: echo hi" in r.output
    argv = calls(podman)[-1]
    assert argv[:2] == ["run", "--rm"] and "--network=none" in argv and "--userns=keep-id" in argv
    assert f"{folder.resolve()}:/work:ro" in argv                     # shared read-only by default
    assert argv[-3:] == ["bash", "-lc", "echo hi"] and argv[-4] == cbx.IMAGE
    assert sum(1 for x in argv if x == "-v") == 1                      # nothing else of yours inside
    r = cbx.CleanBox().run("exit 3", network=True)
    assert r.rc == 3 and not r.ok and "--network=none" not in calls(podman)[-1]


def test_timeout_kills_the_box(podman):
    r = cbx.CleanBox().run("sleep 99", timeout=1)
    assert r.timed_out and r.rc == 124
    assert calls(podman)[-1][:2] == ["rm", "-f"]


def test_new_list_enter_rm_and_sweep(podman):
    box = cbx.CleanBox()
    info = box.new("bench", folder=str(podman["tmp"]))
    assert info["container"] == "orion-bench" and any("/work" in n for n in info["notes"])
    run = next(c for c in calls(podman) if c[0] == "run")
    assert "-d" in run and run[-2:] == ["sleep", "infinity"] and "--network=none" in run
    assert any(x.endswith(":/work:rw") for x in run)
    assert [b["name"] for b in box.list()] == ["bench"]
    assert box.enter_argv("bench")[-4:] == ["-it", "orion-bench", "bash", "-l"]
    with pytest.raises(cbx.BoxError):
        box.enter_argv("nope")
    with pytest.raises(cbx.BoxError):
        box.new("Bad Name!")
    assert box.sweep(now=time.time()) == []                           # not expired yet
    assert box.sweep(now=time.time() + 25 * 3600) == ["bench"]
    assert box.list() == []
    box.new("two")
    assert box.rm("two") and box.list() == []


def test_no_podman_says_how_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setenv("ORION_PODMAN", str(tmp_path / "missing"))
    box = cbx.CleanBox()
    assert not box.available() and box.sweep() == []
    with pytest.raises(cbx.BoxError) as e:
        box.run("true")
    assert "apt install podman" in str(e.value)


def test_mcp_tool(podman):
    proj = podman["tmp"] / "proj"
    proj.mkdir()
    tools = OrionTools(cfg=McpConfig(roots=[proj]), backend=_NoBackend(), default_project=str(proj))
    desc = next(t for t in tools.list_tools() if t["name"] == "clean_box_run")
    assert desc["_meta"]["orion/risk"] == "run"
    text, err = asyncio.run(tools.call("clean_box_run", {"command": "make test", "folder": str(proj)}))
    assert not err and "deleted" in text and "ran: make test" in text
    assert f"{proj.resolve()}:/work:ro" in calls(podman)[-1]
    text, err = asyncio.run(tools.call("clean_box_run", {"command": "ls", "folder": "/etc"}))
    assert err and "isn't inside a folder" in text                    # only folders agents may use


class _NoBackend:
    async def call(self, *a, **k):
        from mcp_tools import DaemonUnavailable
        raise DaemonUnavailable("down")


def test_cli(podman, capsys):
    assert cbx.main(["run", "--", "echo", "x"]) == 0
    assert "ran: echo x" in capsys.readouterr().out
    assert cbx.main(["new", "--name", "cli", "--no-enter"]) == 0
    assert "orion-ctl box enter cli" in capsys.readouterr().out
    assert cbx.main(["list"]) == 0 and "cli" in capsys.readouterr().out
    assert cbx.main(["rm", "cli"]) == 0
