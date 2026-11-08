"""Orion Studio on the MCP bus: Craft tools proxied with Orion's rules, tested against a fake Craft MCP server."""
import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

import craft_bridge as cb
from mcp_tools import DaemonUnavailable, McpConfig, OrionTools

FAKE_SERVER = textwrap.dedent(r'''
    import json, os, sys
    TOOLS = [
        {"name": "info", "description": "Image size", "annotations": {"readOnlyHint": True},
         "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "resize", "description": "Resize an image",
         "inputSchema": {"type": "object", "properties": {"input": {"type": "string"}, "output": {"type": "string"},
                                                          "width": {"type": "integer"}}, "required": ["input"]}},
        {"name": "sharpen", "description": "Sharpen in place",
         "inputSchema": {"type": "object", "properties": {"path": {"type": "string"},
                                                          "amount": {"type": "number"}}, "required": ["path"]}},
        {"name": "delete_layer", "description": "Remove a layer", "annotations": {"destructiveHint": True},
         "inputSchema": {"type": "object", "properties": {"output_file": {"type": "string"},
                                                          "file": {"type": "string"}, "layer": {"type": "integer"}}}},
        {"name": "fail", "description": "Always fails", "annotations": {"readOnlyHint": True},
         "inputSchema": {"type": "object", "properties": {}}},
    ]
    print("photocraft mcp starting (not json)", flush=True)        # stray output must be ignored
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        mid, m, p = msg["id"], msg.get("method"), msg.get("params") or {}
        if m == "initialize":
            res = {"protocolVersion": p.get("protocolVersion"), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "fake-photocraft", "version": "0"}}
        elif m == "tools/list":           # two pages, to exercise the cursor
            res = {"tools": TOOLS[:2], "nextCursor": "p2"} if not p.get("cursor") else {"tools": TOOLS[2:]}
        elif m == "tools/call":
            a, name = p.get("arguments") or {}, p["name"]
            if name == "info":
                res = {"content": [{"type": "text", "text": f"size {os.path.getsize(a['path'])}"}]}
            elif name == "resize":
                data = open(a["input"], "rb").read()
                open(a["output"], "wb").write(data + b"|resized")
                res = {"content": [{"type": "text", "text": "resized to " + a["output"]}]}
            elif name == "sharpen":
                open(a["path"], "ab").write(b"|sharp")
                res = {"content": [{"type": "text", "text": "sharpened " + a["path"]}]}
            elif name == "delete_layer":
                open(a.get("output_file") or a["file"], "ab").write(b"|nolayer")
                res = {"content": [{"type": "text", "text": "layer removed"}]}
            else:
                res = {"content": [{"type": "text", "text": "boom"}], "isError": True}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}), flush=True)
''')


class FakeBackend:
    def __init__(self, up=True):
        self.up, self.calls = up, []

    async def call(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        if not self.up:
            raise DaemonUnavailable("down")
        return 200, {"decision": {"id": "dec-1", "summary": "PhotoCraft: resize", "status": "pending"}}


@pytest.fixture()
def craft(tmp_path, monkeypatch):
    server = tmp_path / "fake_photocraft.py"
    server.write_text(FAKE_SERVER)
    conf = tmp_path / "craft.json"
    conf.write_text(json.dumps({"photocraft": {"command": [sys.executable, str(server)]}}))
    monkeypatch.setenv("ORION_CRAFT_CONFIG", str(conf))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))           # no real pdfcraft-cli / flatpak
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pic.png").write_bytes(b"PNGDATA")
    be = FakeBackend()
    tools = OrionTools(cfg=McpConfig(roots=[proj]), backend=be, default_project=str(proj))
    return {"tools": tools, "proj": proj, "backend": be, "conf": conf, "tmp": tmp_path}


def call(tools, name, args):
    return asyncio.run(tools.call(name, args))


def test_not_installed_says_get_apps(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_CRAFT_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    tools = OrionTools(cfg=McpConfig(roots=[tmp_path]), backend=FakeBackend(), default_project=str(tmp_path))
    names = set(tools.tools)
    assert {"photocraft.about", "pdfcraft.about"} <= names
    assert not any(n.startswith("photocraft.") and n != "photocraft.about" for n in names)
    text, err = call(tools, "pdfcraft.about", {})
    assert not err and "PdfCraft isn't installed. Get it in Get apps" in text


def test_catalog_names_match_getapps():
    apps = {a["bundle_prefix"]: a["flatpak_id"] for a in cb.catalog_apps()}
    assert apps == {"photocraft": "ai.storyteller.photocraft", "pdfcraft": "ai.storyteller.pdfcraft"}


def test_tools_listed_with_risks(craft):
    listed = {t["name"]: t for t in craft["tools"].list_tools()}
    assert {"photocraft.info", "photocraft.resize", "photocraft.sharpen", "photocraft.delete_layer"} <= set(listed)
    assert listed["photocraft.info"]["_meta"]["orion/risk"] == "read"
    assert listed["photocraft.resize"]["_meta"]["orion/risk"] == "run"
    assert "overwrite_original" in listed["photocraft.resize"]["inputSchema"]["properties"]
    assert "overwrite_original" not in listed["photocraft.info"]["inputSchema"]["properties"]
    text, err = call(craft["tools"], "photocraft.about", {})
    assert not err and "PhotoCraft is installed" in text and "photocraft.resize" in text


def test_read_only_runs(craft):
    text, err = call(craft["tools"], "photocraft.info", {"path": "pic.png"})
    assert not err and "size 7" in text
    text, err = call(craft["tools"], "photocraft.fail", {})
    assert err and "boom" in text
    text, err = call(craft["tools"], "photocraft.info", {})
    assert err and "Missing argument: path" in text


def test_write_goes_to_a_new_file(craft):
    proj = craft["proj"]
    text, err = call(craft["tools"], "photocraft.resize", {"input": "pic.png", "width": 1200})
    assert not err, text
    assert (proj / "pic.png").read_bytes() == b"PNGDATA"                      # original untouched
    assert (proj / "pic-orion.png").read_bytes() == b"PNGDATA|resized"
    assert "new file" in text
    text, err = call(craft["tools"], "photocraft.resize", {"input": "pic.png"})
    assert (proj / "pic-orion-2.png").exists()                                # never overwrites the earlier one


def test_in_place_tool_works_on_a_copy(craft):
    proj = craft["proj"]
    text, err = call(craft["tools"], "photocraft.sharpen", {"path": str(proj / "pic.png"), "amount": 0.5})
    assert not err, text
    assert (proj / "pic.png").read_bytes() == b"PNGDATA"
    assert (proj / "pic-orion.png").read_bytes() == b"PNGDATA|sharp"
    assert "copy" in text


def test_overwrite_outside_and_destructive_need_approval(craft, tmp_path):
    proj, be = craft["proj"], craft["backend"]
    (proj / "taken.png").write_bytes(b"X")
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"Y")
    cases = [("photocraft.resize", {"input": "pic.png", "output": "taken.png"}),
             ("photocraft.sharpen", {"path": "pic.png", "overwrite_original": True}),
             ("photocraft.info", {"path": str(outside)}),
             ("photocraft.delete_layer", {"file": "pic.png", "output_file": "new.png"})]
    for name, args in cases:
        text, err = call(craft["tools"], name, args)
        assert not err and "Waiting for the user's approval" in text and "dec-1" in text, (name, text)
        assert be.calls[-1][:2] == ("POST", "/craft/propose")
        assert be.calls[-1][2]["tool"] == name.split(".", 1)[1]
    assert (proj / "pic.png").read_bytes() == b"PNGDATA" and (proj / "taken.png").read_bytes() == b"X"
    assert outside.read_bytes() == b"Y" and not (proj / "new.png").exists()
    craft["tools"].backend = FakeBackend(up=False)
    text, err = call(craft["tools"], "photocraft.sharpen", {"path": "pic.png", "overwrite_original": True})
    assert err and "Nothing was changed" in text


def test_plan_rules_pure(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF")
    (root / "b.pdf").write_bytes(b"%PDF")
    merge = {"name": "merge", "inputSchema": {"properties": {"inputs": {"type": "array"}, "output": {"type": "string"}}}}
    p = cb.plan_call(merge, {"inputs": ["a.pdf", "b.pdf"]}, [root], root)
    assert not p.needs_approval and p.args["output"] == str(root / "a-orion.pdf")
    edit2 = {"name": "swap_pages", "inputSchema": {"properties": {"first": {"type": "string", "description": "a file"},
                                                                  "second_file": {"type": "string"}}}}
    p = cb.plan_call(edit2, {"first": "a.pdf", "second_file": "b.pdf"}, [root], root)
    assert p.needs_approval and "several" in p.reasons[0]
    escape = cb.plan_call({"name": "info", "annotations": {"readOnlyHint": True}}, {"path": "../x.pdf"}, [root], root)
    assert escape.needs_approval
    assert not cb.plan_call({"name": "get_text"}, {"path": "a.pdf"}, [root], root).needs_approval


def test_daemon_propose_and_approved_execution(craft, monkeypatch):
    proj = craft["proj"]
    monkeypatch.setenv("ORION_MCP_CONFIG", str(craft["tmp"] / "mcp.json"))
    (craft["tmp"] / "mcp.json").write_text(json.dumps({"roots": [str(proj)]}))

    class Board:
        def __init__(self):
            self.proposed = []

        async def propose(self, task, summary, details, **k):
            self.proposed.append((task, summary, details, k))
            return {"id": "dec-9", "summary": summary, "status": "pending"}

    class D:
        board = Board()
    d = D()
    with pytest.raises(cb.CraftError):        # a call that doesn't need approval isn't proposed
        asyncio.run(cb.propose(d, {"app": "photocraft", "tool": "resize", "arguments": {"input": "pic.png"},
                                   "cwd": str(proj)}))
    dec = asyncio.run(cb.propose(d, {"app": "photocraft", "tool": "sharpen", "cwd": str(proj),
                                     "arguments": {"path": "pic.png", "overwrite_original": True}}))
    task, summary, details, k = d.board.proposed[-1]
    assert dec["id"] == "dec-9" and task == "mcp.craft_write" and k["risk"] == "high"
    assert "write over the original" in details["why_it_asks"]
    res = asyncio.run(cb._exec_approved({"id": "dec-9", "packet": {"details": details}}))
    assert res["state"] == "done" and (proj / "pic.png").read_bytes() == b"PNGDATA|sharp"   # approved: in place
    import boardroom
    assert boardroom.TASK_TYPES.get("mcp.craft_write") is None or boardroom.TASK_TYPES["mcp.craft_write"].sensitive
    cb.install(type("NoBoard", (), {})())
    assert boardroom.TASK_TYPES["mcp.craft_write"].sensitive


def test_over_the_mcp_protocol(craft):
    from mcp_server import McpServer
    srv = McpServer(craft["tools"])
    res = asyncio.run(srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "photocraft.info", "arguments": {"path": "pic.png"}}}))
    assert res["result"]["isError"] is False and "size 7" in res["result"]["content"][0]["text"]


def test_server_that_never_answers_times_out(tmp_path):
    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(30)\n")
    with pytest.raises(cb.CraftError) as e:
        with cb.CraftSession([sys.executable, str(hang)], timeout=1):
            pass
    assert "too long" in str(e.value)
    with pytest.raises(cb.CraftError):
        with cb.CraftSession([str(tmp_path / "missing-binary")]):
            pass


def test_routes_status_and_try_with_craft(craft, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    home = craft["tmp"] / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(craft["tmp"] / "sys"))
    app = FastAPI()
    app.include_router(cb.make_router(lambda: None))
    c = TestClient(app)
    st = c.get("/craft/status").json()
    assert {a["id"]: a["installed"] for a in st["apps"]} == {"photocraft": True, "pdfcraft": False}
    assert st["try_with_craft"]["pdf"]["on"] is False                   # off by default
    r = c.post("/craft/try-with-craft", json={"kind": "pdf", "on": True})
    assert r.status_code == 400 and "Get apps" in r.json()["error"]
    apps = home / ".local/share/flatpak/exports/share/applications"
    apps.mkdir(parents=True)
    (apps / "ai.storyteller.pdfcraft.desktop").write_text("[Desktop Entry]\n")
    r = c.post("/craft/try-with-craft", json={"kind": "pdf", "on": True})
    assert r.status_code == 200 and r.json()["try_with_craft"]["pdf"]["on"]
    assert "ai.storyteller.pdfcraft.desktop" in (home / ".config/mimeapps.list").read_text()
    assert c.post("/craft/propose", json={"app": "pdfcraft", "tool": "x"}).status_code == 400


def test_main_wires_craft():
    src = (Path(cb.__file__).parent / "main.py").read_text()
    assert "craft_bridge.make_router" in src and "craft_bridge.install(self)" in src
