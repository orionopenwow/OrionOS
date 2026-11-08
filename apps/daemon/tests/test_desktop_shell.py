"""OrionOS desktop routes (desktop_shell.py): real app windows for the HUD taskbar, the hardware report card and
sending it to the phone. No Sway session needed: the tree and the report are samples."""
from __future__ import annotations

import alerts
import desktop_shell as ds
import linux_desktop as ld
from fastapi import FastAPI
from fastapi.testclient import TestClient

TREE = {"id": 1, "type": "root", "nodes": [
    {"id": 2, "type": "output", "name": "__i3", "nodes": [
        {"id": 3, "type": "workspace", "name": "__i3_scratch", "nodes": [], "floating_nodes": [
            {"id": 40, "type": "floating_con", "name": "Calculator", "pid": 9, "app_id": "org.gnome.Calculator"}]}]},
    {"id": 4, "type": "output", "name": "eDP-1", "nodes": [
        {"id": 5, "type": "workspace", "name": "1", "nodes": [
            {"id": 10, "type": "con", "name": "Orion", "pid": 5, "app_id": "orion"}]},
        {"id": 6, "type": "workspace", "name": "2", "nodes": [
            {"id": 7, "type": "con", "name": None, "pid": None, "nodes": [
                {"id": 20, "type": "con", "name": "Docs — Mozilla Firefox", "pid": 7, "app_id": "firefox",
                 "focused": True},
                {"id": 30, "type": "con", "name": "main.py - Code", "pid": 8, "app_id": None,
                 "window_properties": {"class": "Code"}}]}],
         "floating_nodes": [{"id": 22, "type": "floating_con", "name": "Settings", "pid": 6,
                             "app_id": "ai.orion.Settings"}]}]}]}

REPORT = {"safe": False, "headline": "Not ready yet: 1 thing to fix before installing", "rows": [
    {"id": "gpu", "label": "Graphics", "status": "ok", "text": "Intel (i915): full-speed graphics.", "fix": ""},
    {"id": "speakers", "label": "Speakers", "status": "fail", "text": "No sound comes out.",
     "fix": "sudo apt install firmware-sof-signed alsa-ucm-conf"},
    {"id": "battery", "label": "Battery", "status": "warn", "text": "Battery health 70%.", "fix": ""}]}


class Phones:
    def __init__(self, n):
        self.n = n

    def list(self):
        return [{"device_id": str(i)} for i in range(self.n)]


class Daemon:
    def __init__(self, phones=1):
        self.board = type("B", (), {"phones": Phones(phones)})()


def client(report=REPORT, phones=1):
    calls = []

    def run():
        calls.append(1)
        return report
    hw = ds.HardwareReport(run=run, clock=lambda: 1000.0)
    app = FastAPI()
    app.include_router(ds.make_router(lambda: Daemon(phones), hw))
    return TestClient(app), calls


def test_windows_from_tree_skips_the_hud_and_marks_minimised():
    wins = ld.windows_from_tree(TREE)
    assert [w["id"] for w in wins] == [40, 20, 30, 22]
    assert wins[0]["minimised"] and wins[0]["app"] == "Calculator" and wins[0]["workspace"] is None
    assert wins[1]["focused"] and wins[1]["title"] == "Docs — Mozilla Firefox" and wins[1]["workspace"] == "2"
    assert wins[2]["app"] == "Code" and wins[3]["app_id"] == "ai.orion.Settings"
    assert ld.windows_from_tree(None) == []


def test_focus_window_commands(monkeypatch):
    sent = []
    monkeypatch.setattr(ld, "list_windows", lambda: ld.windows_from_tree(TREE))
    monkeypatch.setattr(ld, "swaymsg", lambda *a, **k: sent.append(a) or "")
    assert ld.focus_window(20) and sent[-1] == ("[con_id=20] focus",)
    assert ld.focus_window(40) and sent[-1] == ("[con_id=40] scratchpad show, floating disable, focus",)
    assert ld.focus_window(99) is False


def test_window_routes(monkeypatch):
    c, _ = client()
    monkeypatch.setattr(ld, "is_sway", lambda: False)
    assert c.get("/linux/windows").json() == {"available": False, "windows": []}
    assert c.post("/linux/windows/20/focus").status_code == 409
    monkeypatch.setattr(ld, "is_sway", lambda: True)
    monkeypatch.setattr(ld, "list_windows", lambda: ld.windows_from_tree(TREE))
    monkeypatch.setattr(ld, "swaymsg", lambda *a, **k: "")
    body = c.get("/linux/windows").json()
    assert body["available"] and len(body["windows"]) == 4
    assert c.post("/linux/windows/20/focus").json() == {"ok": True}
    assert c.post("/linux/windows/99/focus").status_code == 404


def test_hardware_report_is_cached_and_sent_to_the_phone(monkeypatch):
    c, calls = client()
    r = c.get("/hardware/report").json()
    assert r["available"] and r["headline"].startswith("Not ready yet")
    c.get("/hardware/report")
    assert len(calls) == 1                                  # cached for a minute
    c.get("/hardware/report?fresh=true")
    assert len(calls) == 2
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append((a, k)))
    out = c.post("/hardware/report/send").json()
    assert out["sent"] and out["title"].startswith("Not ready yet")
    (kind, title, body), kw = sent[0]
    assert kind == "system" and "Speakers: No sound comes out. Fix: sudo apt install" in body
    assert "Battery" not in body                             # the red list only
    assert kw == {"ref": "hardware-report"}


def test_send_needs_a_phone_and_the_check():
    c, _ = client(phones=0)
    assert c.post("/hardware/report/send").json()["sent"] is False
    c, _ = client(report=None)
    assert c.get("/hardware/report").json() == {"available": False}
    assert "isn't on this computer" in c.post("/hardware/report/send").json()["message"]


def test_card_text_green():
    title, body = ds.card_text({"safe": True, "headline": "Safe to install on this PC", "rows": REPORT["rows"][2:]})
    assert title == "Safe to install on this PC"
    assert body == "Everything important works.\nWorth knowing:\n- Battery: Battery health 70%."
